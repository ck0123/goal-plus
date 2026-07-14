#!/usr/bin/env python3
"""Export sanitized AscendC knowledge from pinned Git revisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit


EXAMPLE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_SELECTION = EXAMPLE_ROOT / "knowledge.sources.json"

FORBIDDEN_OUTPUT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Agent instruction", re.compile(r"\bagents?\b", re.IGNORECASE)),
    ("Plugin instruction", re.compile(r"\bplugins?\b", re.IGNORECASE)),
    (
        "external skill invocation",
        re.compile(
            r"(?:skill:|(?<![A-Za-z0-9_.-])/ascendc-[a-z0-9_-]+)",
            re.IGNORECASE,
        ),
    ),
    (
        "external knowledge command",
        re.compile(r"\bascendc-docs-search\b", re.IGNORECASE),
    ),
    ("Chinese skill invocation", re.compile(r"技能")),
    (
        "global install",
        re.compile(
            r"\b(?:pip3?|conda|apt(?:-get)?|yum|dnf)\s+install\b",
            re.IGNORECASE,
        ),
    ),
    ("privileged command", re.compile(r"\bsudo\b", re.IGNORECASE)),
    (
        "destructive shell command",
        re.compile(r"\brm\s+-[^\n]*[rf]", re.IGNORECASE),
    ),
    (
        "destructive find command",
        re.compile(r"\bfind\b[^\n]*\s-delete\b", re.IGNORECASE),
    ),
    (
        "Git workflow",
        re.compile(
            r"\bgit\s+(?:checkout|switch|branch|merge|rebase|reset\s+--hard)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "user checkpoint",
        re.compile(
            r"(?:用户确认|询问用户|等待用户|ask\s+the\s+user|user\s+confirmation)",
            re.IGNORECASE,
        ),
    ),
    ("excluded script dependency", re.compile(r"\bscripts?/", re.IGNORECASE)),
    (
        "orchestration counter",
        re.compile(r"(?:调试计数器|尝试[^\n]{0,12}7\s*次|7\s*次尝试)"),
    ),
    (
        "home-directory dependency",
        re.compile(r"(?:\$\{?HOME\}?|/home/[A-Za-z0-9_.-]+/)"),
    ),
)

DANGEROUS_SOURCE_LINE = re.compile(
    r"(?:"
    r"\brm\s+-[^\n]*[rf]|"
    r"\bfind\b[^\n]*\s-delete\b|"
    r"\b(?:pip3?|conda|apt(?:-get)?|yum|dnf)\s+install\b|"
    r"\bsudo\b|"
    r"\bgit\s+(?:checkout|switch|branch|merge|rebase|reset\s+--hard)\b|"
    r"curl[^\n]*\|\s*(?:ba)?sh|wget[^\n]*\|\s*(?:ba)?sh|"
    r"\bscripts?/|(?:^|\s)(?:\./)?[A-Za-z0-9_.-]+\.sh\b|"
    r"\$\{?HOME\}?|/home/[A-Za-z0-9_.-]+/|"
    r"调试计数器|尝试[^\n]{0,12}7\s*次|7\s*次尝试|"
    r"\bagent\b[^\n]*(?:must|required|should|必须|需要执行|应当)|"
    r"(?:must|required|should|必须|需要执行|应当)[^\n]*\bagent\b|"
    r"用户确认|询问用户|等待用户|ask\s+the\s+user|user\s+confirmation"
    r")",
    re.IGNORECASE,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_text(path: Path, text: str) -> None:
    _write_bytes(path, text.encode("utf-8"))


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _safe_relative_path(value: str, *, label: str) -> PurePosixPath:
    if not value or "\\" in value or ":" in value:
        raise ValueError(f"invalid {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a normalized relative path: {value!r}")
    return path


def _run_git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = (
                exc.stderr.decode("utf-8", errors="replace")
                if binary
                else exc.stderr
            )
            detail = f": {str(stderr).strip()}"
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}{detail}"
        ) from exc
    return completed.stdout if binary else completed.stdout.strip()


def _resolve_commit(repo: Path, ref: str, *, source_name: str) -> str:
    try:
        inside_work_tree = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    except RuntimeError as exc:
        raise FileNotFoundError(
            f"{source_name} knowledge checkout is not a Git repository: {repo}"
        ) from exc
    if inside_work_tree != "true":
        raise FileNotFoundError(
            f"{source_name} knowledge checkout is not a Git work tree: {repo}"
        )
    commit = str(_run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}"))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError(f"unexpected Git commit id for {ref!r}: {commit!r}")
    return commit


def _sanitize_repository_locator(value: str) -> str:
    value = value.strip()
    if "://" in value:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    value = value.split("?", 1)[0].split("#", 1)[0]
    if re.match(r"^[^/@\s]+@[^/\s]+:.+", value):
        value = value.split("@", 1)[1]
    return value


def _observed_git_origin(repo: Path) -> str | None:
    try:
        value = str(_run_git(repo, "remote", "get-url", "origin"))
    except RuntimeError:
        return None
    return _sanitize_repository_locator(value) if value else None


def _read_git_blob(repo: Path, commit: str, path: str) -> tuple[bytes, str]:
    _safe_relative_path(path, label="Git object path")
    object_spec = f"{commit}:{path}"
    object_type = str(_run_git(repo, "cat-file", "-t", object_spec))
    if object_type != "blob":
        raise ValueError(f"selection entry is not a Git blob: {path} ({object_type})")
    blob = str(_run_git(repo, "rev-parse", object_spec))
    data = _run_git(repo, "cat-file", "blob", object_spec, binary=True)
    assert isinstance(data, bytes)
    return data, blob


def _find_git_root(candidates: list[Path]) -> str:
    for candidate in candidates:
        if (candidate / ".git").exists():
            return str(candidate)
    return str(candidates[0])


def _default_akg_root() -> str:
    configured = os.environ.get("AKG_ASCENDC_SKILLS_ROOT") or os.environ.get(
        "AKG_ROOT"
    )
    if configured:
        return configured
    return _find_git_root(
        [
            REPO_ROOT.parent / "akg",
            Path.cwd() / "akg",
            REPO_ROOT / "akg",
        ]
    )


def _default_cannbot_root() -> str:
    configured = os.environ.get("CANNBOT_SKILLS_ROOT")
    if configured:
        return configured
    return _find_git_root(
        [
            Path.cwd() / "cannbot-skills",
            REPO_ROOT.parent / "cannbot-skills",
            REPO_ROOT / "cannbot-skills",
        ]
    )


def _normalize_git_root(path: Path, *, source_name: str) -> Path:
    path = path.expanduser().resolve()
    try:
        root = str(_run_git(path, "rev-parse", "--show-toplevel"))
    except RuntimeError as exc:
        raise FileNotFoundError(
            f"{source_name} knowledge checkout is not a Git work tree: {path}"
        ) from exc
    return Path(root).resolve()


def _validate_file_entry(entry: dict[str, Any], *, source_name: str) -> None:
    source = str(entry.get("source", ""))
    output = str(entry.get("output", ""))
    source_path = _safe_relative_path(source, label="selection source")
    output_path = _safe_relative_path(output, label="selection output")
    forbidden_parts = {"agents", "plugins", "hooks", "scripts", ".github"}
    if forbidden_parts.intersection(part.lower() for part in source_path.parts):
        raise ValueError(
            f"selection entry chooses executable/workflow content: {source_name}:{source}"
        )
    if source_path.suffix.lower() != ".md":
        raise ValueError(f"selection source must be Markdown: {source_name}:{source}")
    if output_path.suffix.lower() != ".md":
        raise ValueError(f"selection output must be Markdown: {output}")
    if entry.get("transform") != "sanitize_markdown":
        raise ValueError(f"unsupported knowledge transformation: {source_name}:{source}")
    category = entry.get("category")
    if not isinstance(category, str) or not re.fullmatch(
        r"[a-z][a-z0-9_]*", category
    ):
        raise ValueError(
            f"invalid knowledge category for {source_name}:{source}: {category!r}"
        )


def _load_selection(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    selection = json.loads(raw)
    if selection.get("schema_version") != 2:
        raise ValueError("knowledge selection schema_version must be 2")
    if not isinstance(selection.get("name"), str) or not selection["name"].strip():
        raise ValueError("knowledge selection name must be non-empty")
    sources = selection.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("knowledge selection must contain non-empty sources")

    names: set[str] = set()
    output_roots: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("every knowledge source must be an object")
        name = str(source.get("name", ""))
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError(f"invalid knowledge source name: {name!r}")
        if name in names:
            raise ValueError(f"duplicate knowledge source name: {name}")
        names.add(name)
        repository = source.get("repository")
        if not isinstance(repository, str) or not repository.strip():
            raise ValueError(f"knowledge source repository must be non-empty: {name}")
        if source.get("role") not in {"primary", "supplement"}:
            raise ValueError(
                f"knowledge source role must be primary or supplement: {name}"
            )
        default_ref = source.get("commit")
        if not isinstance(default_ref, str) or not re.fullmatch(
            r"[0-9a-f]{40}", default_ref
        ):
            raise ValueError(f"knowledge source commit must be full: {name}")
        license_spec = source.get("license")
        if not isinstance(license_spec, dict):
            raise ValueError(f"knowledge source license must be an object: {name}")
        _safe_relative_path(
            str(license_spec.get("path", "")),
            label=f"{name} license path",
        )
        if not str(license_spec.get("name", "")).strip():
            raise ValueError(f"knowledge source license name must be non-empty: {name}")

        kind = source.get("kind")
        output_root = str(source.get("output_root", name))
        _safe_relative_path(output_root, label=f"{name} output_root")
        if output_root in output_roots:
            raise ValueError(f"duplicate knowledge output_root: {output_root}")
        output_roots.add(output_root)
        if kind == "skill_tree":
            _safe_relative_path(
                str(source.get("include_root", "")),
                label=f"{name} include_root",
            )
        elif kind == "files":
            entries = source.get("files")
            if not isinstance(entries, list) or not entries:
                raise ValueError(f"file knowledge source must contain files: {name}")
            seen_sources: set[str] = set()
            seen_outputs: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("every knowledge file entry must be an object")
                _validate_file_entry(entry, source_name=name)
                entry_source = str(entry["source"])
                entry_output = str(entry["output"])
                if entry_source in seen_sources:
                    raise ValueError(f"duplicate selection source: {name}:{entry_source}")
                if entry_output in seen_outputs:
                    raise ValueError(f"duplicate selection output: {name}:{entry_output}")
                seen_sources.add(entry_source)
                seen_outputs.add(entry_output)
        else:
            raise ValueError(f"unsupported knowledge source kind: {name}:{kind!r}")
    return selection, raw


def _parse_named_values(values: list[str], *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        name, separator, item = value.partition("=")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or not item:
            raise ValueError(f"{option} values must use NAME=VALUE: {value!r}")
        if name in parsed:
            raise ValueError(f"duplicate {option} value for {name}")
        parsed[name] = item
    return parsed


def _skill_tree_entries(
    *,
    repo: Path,
    commit: str,
    source_name: str,
    include_root: str,
    output_root: str,
) -> list[dict[str, str]]:
    listing = str(
        _run_git(
            repo,
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            include_root,
        )
    )
    root_path = PurePosixPath(include_root)
    entries: list[dict[str, str]] = []
    for value in listing.splitlines():
        path = PurePosixPath(value)
        if path.suffix.lower() != ".md":
            continue
        relative = path.relative_to(root_path)
        is_skill = relative.name == "SKILL.md"
        lower_parts = tuple(part.lower() for part in relative.parts)
        is_reference = any(part in {"reference", "references"} for part in lower_parts)
        if not (is_skill or is_reference):
            continue
        if is_skill:
            family = relative.parent.name
        else:
            reference_index = next(
                index
                for index, part in enumerate(lower_parts)
                if part in {"reference", "references"}
            )
            family = relative.parts[max(0, reference_index - 1)]
        family = family.lower().replace("-", "_")
        entry = {
            "category": f"{source_name}_{family}",
            "source": path.as_posix(),
            "output": (PurePosixPath(output_root) / relative).as_posix(),
            "transform": "sanitize_markdown",
        }
        _validate_file_entry(entry, source_name=source_name)
        entries.append(entry)
    if not entries:
        raise ValueError(
            f"skill tree contains no SKILL.md/reference Markdown at "
            f"{source_name}:{include_root}"
        )
    return entries


def _expand_selection(
    selection: dict[str, Any],
    *,
    root_values: dict[str, str],
    ref_values: dict[str, str],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    workspace_outputs: set[str] = set()
    selected_names = {str(source["name"]) for source in selection["sources"]}
    unknown_refs = sorted(set(ref_values) - selected_names)
    if unknown_refs:
        raise ValueError(
            "knowledge refs target unknown sources: " + ", ".join(unknown_refs)
        )
    for source in selection["sources"]:
        name = str(source["name"])
        if name not in root_values:
            raise ValueError(f"no local checkout configured for knowledge source: {name}")
        repo = _normalize_git_root(Path(root_values[name]), source_name=name)
        requested_ref = ref_values.get(name, str(source["commit"]))
        if not re.fullmatch(r"[0-9a-f]{40}", requested_ref):
            raise ValueError(f"knowledge ref must be a full commit id: {name}")
        commit = _resolve_commit(repo, requested_ref, source_name=name)
        output_root = str(source.get("output_root", name))
        if source["kind"] == "skill_tree":
            entries = _skill_tree_entries(
                repo=repo,
                commit=commit,
                source_name=name,
                include_root=str(source["include_root"]),
                output_root=output_root,
            )
        else:
            entries = []
            for raw_entry in source["files"]:
                entry = dict(raw_entry)
                entry["output"] = (
                    PurePosixPath(output_root) / str(raw_entry["output"])
                ).as_posix()
                entries.append(entry)

        for entry in entries:
            output = str(entry["output"])
            if output in workspace_outputs:
                raise ValueError(f"duplicate workspace knowledge output: {output}")
            workspace_outputs.add(output)
        contexts.append(
            {
                "name": name,
                "spec": source,
                "repo": repo,
                "requested_ref": requested_ref,
                "commit": commit,
                "entries": entries,
            }
        )
    return contexts


def _source_lines_without_frontmatter(text: str) -> list[tuple[int, str]]:
    records = list(enumerate(text.splitlines(), start=1))
    if not records or records[0][1] != "---":
        return records
    for index, (_line_number, line) in enumerate(records[1:], start=1):
        if line == "---":
            return records[index + 1 :]
    return records


def _strip_orchestration_sections(
    records: list[tuple[int, str]],
) -> tuple[list[tuple[int, str]], list[int]]:
    markers = re.compile(
        r"(?:agent[^\n]*(?:使用|执行|流程|指南|编排|workflow)|"
        r"plugin[^\n]*(?:使用|执行|流程|指南|编排|workflow)|"
        r"skill\s*改进|给\s*agent|调试总结要求|调试计数规则|工作流编排|用户确认)",
        re.IGNORECASE,
    )
    kept: list[tuple[int, str]] = []
    removed: list[int] = []
    drop_level: int | None = None
    for line_number, line in records:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if drop_level is not None:
            if heading and len(heading.group(1)) <= drop_level:
                drop_level = None
            else:
                removed.append(line_number)
                continue
        if heading and markers.search(heading.group(2)):
            drop_level = len(heading.group(1))
            removed.append(line_number)
            continue
        kept.append((line_number, line))
    return kept, removed


def _normalize_source_link(source_path: str, target: str) -> str | None:
    target_path = target.split("#", 1)[0].split("?", 1)[0]
    if not target_path or target_path.startswith(("#", "/")):
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*:", target_path, re.IGNORECASE):
        return None
    combined = PurePosixPath(source_path).parent / target_path
    normalized = os.path.normpath(combined.as_posix()).replace("\\", "/")
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def _sanitize_markdown(
    text: str,
    *,
    source_path: str,
    output_path: str,
    source_to_output: dict[str, str],
) -> tuple[str, list[str], dict[str, Any]]:
    records = _source_lines_without_frontmatter(text.replace("\r\n", "\n"))
    records, removed_sections = _strip_orchestration_sections(records)
    transformations = [
        "strip_skill_frontmatter",
        "remove_unsafe_workflow_instructions",
    ]
    if removed_sections:
        transformations.append("remove_orchestration_sections")

    kept_lines: list[str] = []
    removed_instruction_lines: list[int] = []
    for line_number, line in records:
        if DANGEROUS_SOURCE_LINE.search(line):
            removed_instruction_lines.append(line_number)
            continue
        kept_lines.append(line)
    text = "\n".join(kept_lines) + "\n"

    link_pattern = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
    selected_dependencies: set[str] = set()
    unresolved_dependencies: set[str] = set()

    def sanitize_prose_terms(value: str) -> str:
        value = re.sub(
            r"(?<![A-Za-z0-9_.-])/ascendc-[a-z0-9_-]+",
            "installed CANN documentation",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"skill:[a-z0-9_-]+",
            "installed CANN documentation",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"\bascendc-docs-search\b",
            "installed CANN documentation",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(r"\bplugins?\b", "extension", value, flags=re.IGNORECASE)
        value = re.sub(
            r"\bagents?\b", "candidate worker", value, flags=re.IGNORECASE
        )
        value = re.sub(
            r"\bskills?\b", "knowledge guide", value, flags=re.IGNORECASE
        )
        value = value.replace("技能", "知识指南")
        value = value.replace("$HOME/atc_data/kernel_cache/", ".tmp/build/")
        return value.replace("${HOME}/atc_data/kernel_cache/", ".tmp/build/")

    def selected_link(target: str) -> str | None:
        normalized = _normalize_source_link(source_path, target)
        target_without_fragment = target.split("#", 1)[0].split("?", 1)[0]
        candidates = [normalized] if normalized is not None else []
        target_path = PurePosixPath(target_without_fragment)
        if target_path.name == target_without_fragment:
            source_parent = PurePosixPath(source_path).parent
            candidates.extend(
                [
                    (source_parent.parent / target_path).as_posix(),
                    (source_parent / "references" / target_path).as_posix(),
                    (source_parent.parent / "references" / target_path).as_posix(),
                ]
            )
        selected_output = next(
            (
                source_to_output[candidate]
                for candidate in candidates
                if candidate in source_to_output
            ),
            None,
        )
        if selected_output is None and target_path.name == target_without_fragment:
            basename_matches = [
                output
                for source, output in source_to_output.items()
                if PurePosixPath(source).name == target_path.name
            ]
            if len(basename_matches) == 1:
                selected_output = basename_matches[0]
        if selected_output is None:
            unresolved_dependencies.add(target)
            return None
        selected_dependencies.add(target)
        suffix = f"#{target.split('#', 1)[1]}" if "#" in target else ""
        relative = os.path.relpath(
            PurePosixPath(selected_output),
            PurePosixPath(output_path).parent,
        ).replace("\\", "/")
        return relative + suffix

    def rewrite_link(match: re.Match[str]) -> str:
        label = sanitize_prose_terms(match.group(1))
        target = match.group(2).strip()
        normalized = _normalize_source_link(source_path, target)
        if normalized is None:
            if target.lower().startswith("skill:"):
                return (
                    f"{label} (not bundled; consult installed CANN documentation)"
                )
            return match.group(0)
        relative = selected_link(target)
        if relative is None:
            display = "unbundled document" if ".md" in label.lower() else label
            return (
                f"{display} (not bundled; consult installed CANN documentation)"
            )
        return f"[{label}]({relative})"

    protected_links: list[str] = []

    def protect_rendered_link(rendered: str) -> str:
        placeholder = f"@@GOAL_PLUS_KNOWLEDGE_LINK_{len(protected_links)}@@"
        protected_links.append(rendered)
        return placeholder

    def protect_link(match: re.Match[str]) -> str:
        return protect_rendered_link(rewrite_link(match))

    text = link_pattern.sub(protect_link, text)
    bare_document = re.compile(
        r"`?((?:(?:\.\.?/)?[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.md"
        r"(?:#[A-Za-z0-9_.\-\u4e00-\u9fff]+)?)`?"
    )

    def rewrite_bare_document(match: re.Match[str]) -> str:
        target = match.group(1)
        relative = selected_link(target)
        if relative is None:
            return "unbundled document (not bundled; consult installed CANN documentation)"
        label = sanitize_prose_terms(target)
        return protect_rendered_link(f"[{label}]({relative})")

    text = bare_document.sub(rewrite_bare_document, text)
    text = re.sub(
        r"(?:scripts?/)?parse_plog\.py",
        "workspace-local log parser",
        text,
        flags=re.IGNORECASE,
    )
    text = sanitize_prose_terms(text)
    for index, rendered_link in enumerate(protected_links):
        text = text.replace(
            f"@@GOAL_PLUS_KNOWLEDGE_LINK_{index}@@",
            rendered_link,
        )

    for description, pattern in FORBIDDEN_OUTPUT_PATTERNS:
        match = pattern.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            raise ValueError(
                "sanitized knowledge still contains "
                f"{description} at {source_path}:{line}"
            )

    without_links = link_pattern.sub("", text)
    bare_match = bare_document.search(without_links)
    if bare_match:
        line = text.count("\n", 0, bare_match.start()) + 1
        raise ValueError(
            "sanitized knowledge still contains undeclared bare document "
            f"dependency at {source_path}:{line}: {bare_match.group(1)}"
        )

    transformations.extend(
        [
            "rewrite_local_links",
            "rewrite_bare_document_dependencies",
            "replace_external_skill_references",
        ]
    )
    audit = {
        "removed_orchestration_lines": removed_sections,
        "removed_unsafe_instruction_lines": removed_instruction_lines,
        "rewritten_selected_dependencies": sorted(selected_dependencies),
        "unresolved_dependencies_removed": sorted(unresolved_dependencies),
    }
    return text, transformations, audit


def _audit_rendered_links(files_root: Path) -> None:
    pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for document in sorted(files_root.rglob("*.md")):
        text = document.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            target = match.group(1).strip().split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith("#") or re.match(
                r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE
            ):
                continue
            resolved = (document.parent / target).resolve()
            try:
                resolved.relative_to(files_root.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"knowledge link escapes bundle: {document}: {target}"
                ) from exc
            if not resolved.is_file():
                raise ValueError(
                    f"undeclared knowledge dependency: {document}: {target}"
                )


def _materialize(
    *,
    output_dir: Path,
    source_contexts: list[dict[str, Any]],
    selection: dict[str, Any],
    selection_raw: bytes,
) -> dict[str, Any]:
    manifest_files: list[dict[str, Any]] = []
    files_root = output_dir / "files"
    manifest_sources: list[dict[str, Any]] = []
    manifest_licenses: list[dict[str, Any]] = []

    for source_context in source_contexts:
        source_name = str(source_context["name"])
        entries = source_context["entries"]
        source_to_output = {
            str(entry["source"]): str(entry["output"]) for entry in entries
        }
        for entry in entries:
            source = str(entry["source"])
            output = str(entry["output"])
            source_data, blob = _read_git_blob(
                source_context["repo"], source_context["commit"], source
            )
            try:
                source_text = source_data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"knowledge file is not UTF-8 text: {source_name}:{source}"
                ) from exc
            rendered, transformations, derivation_audit = _sanitize_markdown(
                source_text,
                source_path=source,
                output_path=output,
                source_to_output=source_to_output,
            )
            rendered_data = rendered.encode("utf-8")
            _write_bytes(files_root / output, rendered_data)
            manifest_files.append(
                {
                    "category": entry["category"],
                    "source_name": source_name,
                    "source_path": source,
                    "source_git_blob_sha1": blob,
                    "source_sha256": _sha256(source_data),
                    "workspace_path": f"_skills/files/{output}",
                    "rendered_sha256": _sha256(rendered_data),
                    "transformations": transformations,
                    "derivation_audit": derivation_audit,
                }
            )

        source_spec = source_context["spec"]
        declared_repository = _sanitize_repository_locator(
            str(source_spec["repository"])
        )
        manifest_sources.append(
            {
                "name": source_name,
                "repository": declared_repository,
                "observed_origin": _observed_git_origin(source_context["repo"]),
                "requested_ref": source_context["requested_ref"],
                "commit": source_context["commit"],
                "export_method": "git_object_database",
                "kind": source_spec["kind"],
                "role": source_spec["role"],
                "files": len(entries),
            }
        )

        license_spec = source_spec["license"]
        license_source = str(license_spec["path"])
        license_data, license_blob = _read_git_blob(
            source_context["repo"], source_context["commit"], license_source
        )
        license_workspace = f"_skills/licenses/{source_name}.txt"
        _write_bytes(output_dir / "licenses" / f"{source_name}.txt", license_data)
        if not manifest_licenses:
            _write_bytes(output_dir / "LICENSE", license_data)
        manifest_licenses.append(
            {
                "source_name": source_name,
                "name": str(license_spec["name"]),
                "source_path": license_source,
                "source_git_blob_sha1": license_blob,
                "source_sha256": _sha256(license_data),
                "workspace_path": license_workspace,
            }
        )

    _audit_rendered_links(files_root)

    grouped: dict[str, list[str]] = {}
    for item in manifest_files:
        grouped.setdefault(str(item["category"]), []).append(
            str(item["workspace_path"])
        )
    readme_lines = [
        "# AscendC Direct Invoke Knowledge",
        "",
        "This read-only bundle was generated from pinned Git commits.",
        "Primary sources contain curated implementation guidance; supplements fill only declared gaps.",
        "It contains implementation facts and debugging methods, not orchestration instructions.",
        "",
        "Sources:",
        "",
    ]
    for source in manifest_sources:
        readme_lines.append(
            f"- `{source['name']}` ({source['role']}): `{source['commit']}` "
            f"({source['files']} files)"
        )
    readme_lines.extend(
        [
            "",
            "Fact precedence:",
            "",
            "1. The frozen task, reference, verifier, and scaffold contracts.",
            "2. `_task/target_platform.json` runtime facts.",
            "3. Headers and API facts in the active CANN installation.",
            "4. These derived knowledge documents.",
            "5. Prior model knowledge.",
            "",
        ]
    )
    for category in sorted(grouped):
        readme_lines.extend([f"## {category}", ""])
        readme_lines.extend(f"- `{path}`" for path in grouped[category])
        readme_lines.append("")
    _write_text(output_dir / "README.md", "\n".join(readme_lines))

    manifest = {
        "schema_version": 2,
        "materializer": {
            "name": Path(__file__).name,
            "sha256": _sha256(Path(__file__).read_bytes()),
        },
        "selection": {
            "name": selection["name"],
            "sha256": _sha256(selection_raw),
        },
        "sources": manifest_sources,
        "licenses": manifest_licenses,
        "files": manifest_files,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export selected AscendC knowledge from exact Git revisions "
            "into a generated Goal Plus task workspace"
        )
    )
    parser.add_argument(
        "--akg-root",
        default=_default_akg_root(),
        help="local AKG Git checkout containing curated AscendC skills",
    )
    parser.add_argument(
        "--cannbot-skills-root",
        default=_default_cannbot_root(),
        help="local CANNBot Skills Git checkout for declared supplements",
    )
    parser.add_argument(
        "--source-root",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="override a named local knowledge checkout",
    )
    parser.add_argument(
        "--source-ref",
        action="append",
        default=[],
        metavar="NAME=COMMIT",
        help="override a named source with a full Git commit id",
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=DEFAULT_SELECTION,
        help="pinned knowledge-source selection and sanitization contract",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="fresh generated task path ending in _skills",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    selection_path = args.selection.resolve()
    selection, selection_raw = _load_selection(selection_path)
    root_values = {
        "akg": str(args.akg_root),
        "cannbot": str(args.cannbot_skills_root),
    }
    root_values.update(_parse_named_values(args.source_root, option="--source-root"))
    ref_values = _parse_named_values(args.source_ref, option="--source-ref")
    source_contexts = _expand_selection(
        selection,
        root_values=root_values,
        ref_values=ref_values,
    )

    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.name != "_skills":
        raise ValueError("knowledge output directory must be named _skills")
    if output_dir.exists():
        raise FileExistsError(f"knowledge output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=".goal-plus-ascendc-skills-",
            dir=output_dir.parent,
        )
    )
    try:
        manifest = _materialize(
            output_dir=stage,
            source_contexts=source_contexts,
            selection=selection,
            selection_raw=selection_raw,
        )
        stage.replace(output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "manifest": str(output_dir / "manifest.json"),
                "source_commits": {
                    item["name"]: item["commit"] for item in manifest["sources"]
                },
                "files": len(manifest["files"]),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
