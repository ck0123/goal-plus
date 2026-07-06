from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    log_path: Path


class OpenCodeRunner:
    """Drive `opencode run --command goal-plus "<prompt>"` in a project root."""

    def __init__(
        self,
        project_root: Path,
        log_dir: Path,
        default_timeout: int = 1800,
        model: str | None = None,
    ):
        self.project_root = project_root
        self.log_dir = log_dir
        self.default_timeout = default_timeout
        # Only pass -m if the user explicitly set ST_OPENCODE_MODEL; otherwise
        # let opencode pick its own default model.
        self.model = model or os.environ.get("ST_OPENCODE_MODEL") or None
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _build_cmd(self, prompt: str) -> list[str]:
        cmd = ["opencode", "run", "--command", "goal-plus"]
        if self.model:
            cmd += ["-m", self.model]
        cmd.append(
            "This is a non-interactive /goal-plus system test. If the task is "
            "Initial Search-Ready, this prompt explicitly confirms the frozen "
            "verifier, metric, edit surface, and promotion rule.\n\n"
            + prompt
        )
        return cmd

    def run(self, prompt: str, *, scenario: str, timeout: int | None = None) -> RunResult:
        timeout = timeout or self.default_timeout
        log_path = self.log_dir / f"{scenario}.log"
        cmd = self._build_cmd(prompt)

        env = dict(os.environ)
        env.setdefault("RUST_LOG", "info")

        proc = subprocess.run(
            cmd,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        log_path.write_text(
            f"$ {' '.join(cmd[:3])} ... (prompt {len(prompt)} chars)\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n"
            f"--- exit {proc.returncode} ---\n",
            encoding="utf-8",
        )

        return RunResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
            log_path=log_path,
        )

    def run_streaming(self, prompt: str, *, scenario: str, timeout: int | None = None) -> RunResult:
        """Run opencode with line-by-line streaming to a log file so we can tail it."""
        timeout = timeout or self.default_timeout
        log_path = self.log_dir / f"{scenario}.log"
        cmd = self._build_cmd(prompt)

        env = dict(os.environ)
        env.setdefault("RUST_LOG", "info")

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"$ {' '.join(cmd[:3])} ... (prompt {len(prompt)} chars)\n")
                f.flush()
                proc = subprocess.run(
                    cmd,
                    cwd=self.project_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
                f.write("--- output ---\n")
                f.write(proc.stdout)
                f.write(f"\n--- exit {proc.returncode} ---\n")
        except subprocess.TimeoutExpired as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- TIMEOUT after {timeout}s ---\n")
                if e.output:
                    try:
                        f.write(e.output.decode("utf-8", errors="replace"))
                    except AttributeError:
                        f.write(str(e.output))
            return RunResult(
                returncode=124,
                stdout=(e.output.decode("utf-8", errors="replace") if isinstance(e.output, bytes) else (e.output or "")),
                stderr="",
                timed_out=True,
                log_path=log_path,
            )

        return RunResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr="",
            timed_out=False,
            log_path=log_path,
        )


def find_opencode() -> str | None:
    return shutil.which("opencode")
