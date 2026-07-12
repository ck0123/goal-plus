from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.st.hosts import ST_ACTIVE_ENV, st_model_for_host


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    log_path: Path


class CodexRunner:
    """Drive `codex exec` for host-level ST scenarios."""

    def __init__(
        self,
        project_root: Path,
        log_dir: Path,
        default_timeout: int = 1800,
        model: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.log_dir = log_dir
        self.default_timeout = default_timeout
        self.model = model or st_model_for_host("codex")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _build_cmd(self, prompt: str, *, final_path: Path | None = None) -> list[str]:
        cmd = [
            "codex",
            "exec",
            "-C",
            str(self.project_root),
            "--skip-git-repo-check",
            "--dangerously-bypass-hook-trust",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
        ]
        if self.model:
            cmd += ["-m", self.model]
        if final_path is not None:
            cmd += ["-o", str(final_path)]
        cmd.append(
            "This is a non-interactive Codex /goal-plus system test. "
            "Use the project-local Codex goal-plus and search skills. If the "
            "task is Initial Search-Ready, this prompt explicitly confirms the "
            "frozen verifier, metric, edit surface, and promotion rule. "
            "You are already inside the ST harness: do not run pytest, codex, "
            "opencode, claude, or any tests/st command. Drive the goal-plus "
            "MCP tools directly, and only launch foreground workers from runtime "
            "launch payloads.\n\n"
            + prompt
        )
        return cmd

    def run_streaming(
        self,
        prompt: str,
        *,
        scenario: str,
        timeout: int | None = None,
    ) -> RunResult:
        timeout = timeout or self.default_timeout
        log_path = self.log_dir / f"{scenario}.log"
        final_path = self.log_dir / f"{scenario}.final.md"
        log_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        cmd = self._build_cmd(prompt, final_path=final_path)

        env = dict(os.environ)
        env.setdefault("RUST_LOG", "info")
        env[ST_ACTIVE_ENV] = scenario

        log_path.write_text(
            f"$ {' '.join(cmd[:4])} ... (prompt {len(prompt)} chars)\n"
            "--- running ---\n",
            encoding="utf-8",
        )

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            output = (
                exc.output.decode("utf-8", errors="replace")
                if isinstance(exc.output, bytes)
                else (exc.output or "")
            )
            log_path.write_text(
                f"$ {' '.join(cmd[:4])} ... (prompt {len(prompt)} chars)\n"
                f"--- TIMEOUT after {timeout}s ---\n{output}",
                encoding="utf-8",
            )
            return RunResult(
                returncode=124,
                stdout=output,
                stderr="",
                timed_out=True,
                log_path=log_path,
            )

        final_text = final_path.read_text(encoding="utf-8") if final_path.exists() else ""
        combined_stdout = proc.stdout
        if final_text and final_text not in combined_stdout:
            combined_stdout = f"{combined_stdout}\n--- final message ---\n{final_text}\n"
        log_path.write_text(
            f"$ {' '.join(cmd[:4])} ... (prompt {len(prompt)} chars)\n"
            f"--- output ---\n{proc.stdout}\n"
            f"--- final message ---\n{final_text}\n"
            f"--- exit {proc.returncode} ---\n",
            encoding="utf-8",
        )
        return RunResult(
            returncode=proc.returncode,
            stdout=combined_stdout,
            stderr="",
            timed_out=False,
            log_path=log_path,
        )


def find_codex() -> str | None:
    return shutil.which("codex")
