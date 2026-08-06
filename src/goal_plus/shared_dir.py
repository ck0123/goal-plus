"""Run-scoped, verifier-settled shared tool snapshots.

Candidates stage optional tools under ``.tmp/share-out``.  Only an attributed,
passing process verifier may consume that directory.  Consumption first renames
the whole staging directory into runtime-owned pending storage, so a successful
settlement cannot publish the same staging contents again on the next iteration.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from goal_plus.models import SharedToolRecord


SHARE_OUT_RELATIVE_PATH = ".tmp/share-out"
SHARED_INDEX_SCHEMA_VERSION = 1
TOOL_VIEW_MAX_CONTENT_BYTES = 256 * 1024
TOOL_VIEW_MAX_FILE_BYTES = 64 * 1024


def _utc_timestamp() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return normalized[:80] or "tool"


def _manifest_metadata(tool: Path) -> tuple[str, str | None, str | None]:
    manifest_path = tool / "manifest.json" if tool.is_dir() else None
    payload: dict[str, Any] = {}
    if manifest_path is not None and manifest_path.is_file():
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                payload = value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass

    def text_field(name: str, limit: int) -> str | None:
        value = payload.get(name)
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        return normalized[:limit] or None

    return (
        text_field("name", 120) or tool.name,
        text_field("summary", 500),
        text_field("entrypoint", 300),
    )


@dataclass
class SharedDirSettlement:
    tools: list[SharedToolRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    staged_entries: list[str] = field(default_factory=list)
    staged_file_count: int = 0
    staged_bytes: int = 0
    consumed_entries: list[str] = field(default_factory=list)
    deduplicated_entries: list[str] = field(default_factory=list)


class SharedDirManager:
    """Snapshot candidate exports into a runtime-owned immutable view.

    File modes are best-effort protection against accidental edits.  They are
    not a security boundary when a worker runs as the same OS user as the
    runtime; host sandboxing or ACL separation must provide that boundary.
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.shared_dir = run_dir / "shared"
        self.tools_dir = self.shared_dir / "tools"
        self.index_path = self.shared_dir / "index.json"
        # Pending candidate input is deliberately outside the advertised
        # shared directory, so peers cannot mistake unverified input for a
        # published snapshot.
        self.pending_dir = self.run_dir / ".shared-tool-consume"
        self.publish_temp_dir = self.run_dir / ".shared-tool-publish"
        self.index_temp_dir = self.run_dir / ".shared-tool-index"

    def ensure_layout(self) -> Path:
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index([])
        return self.shared_dir

    def inspect_staging(
        self,
        share_out_dir: Path,
        *,
        max_tools: int = 16,
        max_files: int = 64,
        max_bytes: int = 2 * 1024 * 1024,
        max_path_entries: int = 512,
        max_depth: int = 8,
        deep: bool = True,
    ) -> dict[str, Any]:
        """Inspect staging with hard traversal bounds and no publication.

        ``deep=False`` performs only a capped top-level inventory.  Runtime
        settlement uses that cheap form until verifier validity and worker
        attribution are known.
        """
        entries, paths, errors = self._top_level_entries(
            share_out_dir,
            max_tools=max_tools,
        )
        result = {
            "entries": entries,
            "file_count": 0,
            "size_bytes": 0,
            "errors": errors,
        }
        if errors or not deep:
            return result

        file_count = 0
        size_bytes = 0
        path_count = 0
        for entry in paths:
            try:
                files, entry_size, entry_paths = self._tool_files(
                    entry,
                    max_files=max_files,
                    max_bytes=max_bytes,
                    max_path_entries=max_path_entries,
                    max_depth=max_depth,
                    files_already=file_count,
                    bytes_already=size_bytes,
                    paths_already=path_count,
                )
            except (OSError, ValueError) as exc:
                result["errors"].append(f"{entry.name}: {exc}")
                continue
            file_count += len(files)
            size_bytes += entry_size
            path_count += entry_paths
        result["file_count"] = file_count
        result["size_bytes"] = size_bytes
        return result

    def settle_iteration(
        self,
        *,
        candidate_id: str,
        iteration: int,
        source_commit: str | None,
        share_out_dir: Path,
        max_tools: int,
        max_files: int,
        max_bytes: int,
        max_path_entries: int,
        max_depth: int,
    ) -> SharedDirSettlement:
        """Atomically claim staging, publish deltas, and consume accepted input."""
        self.ensure_layout()
        inventory = self.inspect_staging(
            share_out_dir,
            max_tools=max_tools,
            max_files=max_files,
            max_bytes=max_bytes,
            max_path_entries=max_path_entries,
            max_depth=max_depth,
            deep=False,
        )
        result = SharedDirSettlement(
            staged_entries=list(inventory["entries"]),
            errors=list(inventory["errors"]),
        )
        if result.errors or not result.staged_entries:
            return result

        claim_dir = self._claim_staging(
            share_out_dir,
            candidate_id=candidate_id,
            iteration=iteration,
        )
        existing = self._load_index()
        latest_by_source = self._latest_by_source(existing, candidate_id)
        physical_by_hash = self._physical_by_hash(existing)
        new_records: list[SharedToolRecord] = []
        created_snapshots: list[Path] = []
        processed: list[Path] = []
        files_used = 0
        bytes_used = 0
        paths_used = 0

        try:
            for entry in sorted(claim_dir.iterdir(), key=lambda item: item.name):
                try:
                    files, size_bytes, path_entries = self._tool_files(
                        entry,
                        max_files=max_files,
                        max_bytes=max_bytes,
                        max_path_entries=max_path_entries,
                        max_depth=max_depth,
                        files_already=files_used,
                        bytes_already=bytes_used,
                        paths_already=paths_used,
                    )
                    snapshot_hash, relative_files = self._tool_digest(
                        entry,
                        files,
                        expected_size=size_bytes,
                    )
                    source_relative_path = entry.relative_to(claim_dir).as_posix()
                    previous = latest_by_source.get(source_relative_path)
                    if previous and previous.get("snapshot_hash") == snapshot_hash:
                        result.deduplicated_entries.append(entry.name)
                    else:
                        record, created_snapshot = self._publish_tool(
                            candidate_id=candidate_id,
                            iteration=iteration,
                            source_commit=source_commit,
                            tool=entry,
                            source_relative_path=source_relative_path,
                            files=files,
                            relative_files=relative_files,
                            size_bytes=size_bytes,
                            snapshot_hash=snapshot_hash,
                            physical_by_hash=physical_by_hash,
                        )
                        new_records.append(record)
                        if created_snapshot:
                            created_snapshots.append(record.read_only_path)
                        latest_by_source[source_relative_path] = record.model_dump(
                            mode="json"
                        )
                        physical_by_hash[snapshot_hash] = record.read_only_path
                    files_used += len(files)
                    bytes_used += size_bytes
                    paths_used += path_entries
                    processed.append(entry)
                    result.consumed_entries.append(entry.name)
                except (OSError, ValueError) as exc:
                    result.errors.append(f"{entry.name}: {exc}")
                    self._restore_entry(entry, share_out_dir, result.errors)

            if new_records:
                self._append_index(new_records)
        except Exception:
            # No index publication means none of this batch is durably settled.
            for entry in processed:
                if entry.exists():
                    self._restore_entry(entry, share_out_dir, result.errors)
            for snapshot in reversed(created_snapshots):
                self._remove_unindexed_snapshot(snapshot)
            raise
        else:
            result.tools = new_records
            result.staged_file_count = files_used
            result.staged_bytes = bytes_used
        finally:
            if claim_dir.exists():
                try:
                    # Delete only entries known to have been consumed.  A
                    # rejected entry that could not be restored remains in the
                    # claim directory for recovery instead of being lost.
                    for entry in processed:
                        if entry.is_dir() and not entry.is_symlink():
                            shutil.rmtree(entry)
                        elif entry.exists() or entry.is_symlink():
                            entry.unlink()
                    claim_dir.rmdir()
                except OSError as exc:
                    result.errors.append(
                        f"consumed staging cleanup failed at {claim_dir}: {exc}"
                    )
        return result

    def tool_view_input(
        self,
        tool: SharedToolRecord,
        *,
        max_content_bytes: int,
    ) -> tuple[dict[str, Any], int]:
        """Build bounded, hash-checked, untrusted input for the View Agent."""
        root = tool.read_only_path
        if not self._safe_snapshot_path(root):
            raise ValueError(f"unsafe shared tool snapshot path for {tool.tool_id}")

        root = root.resolve()
        files: list[tuple[str, Path]] = []
        for value in tool.files:
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe shared tool file path {value!r}")
            path = (root / relative).resolve(strict=True)
            if not path.is_file() or not path.is_relative_to(root):
                raise ValueError(f"shared tool file escaped its snapshot: {value!r}")
            if path.relative_to(root).as_posix() != value:
                raise ValueError(f"non-canonical shared tool file path {value!r}")
            files.append((value, path))

        entrypoint_file = (tool.entrypoint or "").partition(":")[0]
        ordered = sorted(
            files,
            key=lambda item: (
                item[0] != "manifest.json",
                item[0] != entrypoint_file,
                item[0],
            ),
        )
        remaining = max(0, max_content_bytes)
        capture_limits: dict[str, int] = {}
        try:
            for relative, path in ordered:
                limit = min(
                    path.stat().st_size,
                    remaining,
                    TOOL_VIEW_MAX_FILE_BYTES,
                )
                capture_limits[relative] = limit
                remaining -= limit

            digest = hashlib.sha256()
            bytes_read = 0
            file_sizes: dict[str, int] = {}
            captured: dict[str, bytearray] = {
                relative: bytearray() for relative, _path in files
            }
            for relative, path in files:
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                file_size = 0
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        file_size += len(chunk)
                        bytes_read += len(chunk)
                        if bytes_read > tool.size_bytes:
                            raise ValueError("tool changed while it was being read")
                        digest.update(chunk)
                        excerpt = captured[relative]
                        excerpt.extend(
                            chunk[: max(0, capture_limits[relative] - len(excerpt))]
                        )
                file_sizes[relative] = file_size
            if bytes_read != tool.size_bytes or digest.hexdigest() != tool.snapshot_hash:
                raise ValueError("tool changed while it was being read")
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"shared tool snapshot integrity mismatch for {tool.tool_id}: {exc}"
            ) from exc

        used = sum(len(value) for value in captured.values())
        excerpts: list[dict[str, Any]] = []
        manifest: dict[str, Any] | None = None
        for relative, _path in ordered:
            size = file_sizes[relative]
            raw = bytes(captured[relative])
            if not raw:
                excerpts.append(
                    {"path": relative, "size_bytes": size, "content_omitted": True}
                )
                continue
            truncated = len(raw) < size
            try:
                decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
                text = decoder.decode(raw, final=not truncated)
            except UnicodeDecodeError:
                excerpts.append(
                    {"path": relative, "size_bytes": size, "binary": True}
                )
                continue
            if relative == "manifest.json" and not truncated:
                try:
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        manifest = payload
                        continue
                except json.JSONDecodeError:
                    pass
            excerpts.append(
                {
                    "path": relative,
                    "size_bytes": size,
                    "text": text,
                    "truncated": truncated,
                }
            )

        return (
            {
                "tool_id": tool.tool_id,
                "snapshot_hash": tool.snapshot_hash,
                "source_commit": tool.source_commit,
                "name": tool.name,
                "summary": tool.summary,
                "entrypoint": tool.entrypoint,
                "files": list(tool.files),
                "size_bytes": tool.size_bytes,
                "manifest": manifest,
                "snapshot_excerpts": excerpts,
                "evidence_warning": (
                    "The candidate iteration passed its process verifier; the tool "
                    "was not independently verified."
                ),
            },
            used,
        )

    @staticmethod
    def _top_level_entries(
        share_out_dir: Path,
        *,
        max_tools: int,
    ) -> tuple[list[str], list[Path], list[str]]:
        try:
            if share_out_dir.is_symlink():
                return [], [], [
                    f"{SHARE_OUT_RELATIVE_PATH} must be a real directory"
                ]
            if not share_out_dir.exists():
                return [], [], []
            if not share_out_dir.is_dir():
                return [], [], [
                    f"{SHARE_OUT_RELATIVE_PATH} must be a real directory"
                ]
            paths: list[Path] = []
            with os.scandir(share_out_dir) as entries:
                for entry in entries:
                    if len(paths) >= max_tools:
                        names = sorted(path.name for path in paths)
                        return names, paths, [
                            f"iteration share-out exceeds {max_tools} top-level tools"
                        ]
                    paths.append(Path(entry.path))
        except OSError as exc:
            return [], [], [f"{SHARE_OUT_RELATIVE_PATH} inspection failed: {exc}"]
        paths.sort(key=lambda item: item.name)
        return [path.name for path in paths], paths, []

    @staticmethod
    def _tool_files(
        tool: Path,
        *,
        max_files: int,
        max_bytes: int,
        max_path_entries: int,
        max_depth: int,
        files_already: int = 0,
        bytes_already: int = 0,
        paths_already: int = 0,
    ) -> tuple[list[Path], int, int]:
        if tool.is_symlink():
            raise ValueError("symbolic links are not supported")

        files: list[Path] = []
        total_bytes = 0
        path_entries = 0

        def account_file(path: Path) -> None:
            nonlocal total_bytes
            size = path.stat().st_size
            if files_already + len(files) + 1 > max_files:
                raise ValueError(f"iteration share-out exceeds {max_files} files")
            if bytes_already + total_bytes + size > max_bytes:
                raise ValueError(f"iteration share-out exceeds {max_bytes} bytes")
            files.append(path)
            total_bytes += size

        def visit(path: Path, depth: int) -> None:
            nonlocal path_entries
            path_entries += 1
            if paths_already + path_entries > max_path_entries:
                raise ValueError(
                    "iteration share-out exceeds "
                    f"{max_path_entries} filesystem entries"
                )
            if depth > max_depth:
                raise ValueError(
                    f"tool nesting exceeds maximum depth {max_depth}"
                )
            if path.is_symlink():
                raise ValueError("symbolic links are not supported")
            if path.is_file():
                account_file(path)
                return
            if not path.is_dir():
                raise ValueError("tool entries must be regular files or directories")
            with os.scandir(path) as children:
                for child in children:
                    visit(Path(child.path), depth + 1)

        visit(tool, 0)
        if not files:
            raise ValueError("tool is empty")
        files.sort(key=lambda path: path.relative_to(tool).as_posix())
        return files, total_bytes, path_entries

    @staticmethod
    def _tool_digest(
        tool: Path,
        files: list[Path],
        *,
        expected_size: int,
    ) -> tuple[str, list[str]]:
        digest = hashlib.sha256()
        tool_is_file = tool.is_file()
        relative_files: list[str] = []
        bytes_read = 0
        for path in files:
            relative = Path(tool.name) if tool_is_file else path.relative_to(tool)
            value = relative.as_posix()
            relative_files.append(value)
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    bytes_read += len(chunk)
                    if bytes_read > expected_size:
                        raise ValueError("tool changed while it was being hashed")
                    digest.update(chunk)
        if bytes_read != expected_size:
            raise ValueError("tool changed while it was being hashed")
        return digest.hexdigest(), relative_files

    def _publish_tool(
        self,
        *,
        candidate_id: str,
        iteration: int,
        source_commit: str | None,
        tool: Path,
        source_relative_path: str,
        files: list[Path],
        relative_files: list[str],
        size_bytes: int,
        snapshot_hash: str,
        physical_by_hash: dict[str, Path],
    ) -> tuple[SharedToolRecord, bool]:
        destination = physical_by_hash.get(snapshot_hash)
        created_snapshot = False
        if destination is None or not self._safe_snapshot_path(destination):
            destination = (
                self.tools_dir / "sha256" / snapshot_hash[:2] / snapshot_hash
            )
            if destination.exists():
                destination = destination.with_name(
                    f"{snapshot_hash}-{uuid.uuid4().hex[:8]}"
                )
            self.publish_temp_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.publish_temp_dir / f"snapshot-{uuid.uuid4().hex}"
            temporary.mkdir(parents=True, exist_ok=False)
            try:
                copied_bytes = 0
                copied_files: list[Path] = []
                for source in files:
                    relative = (
                        Path(tool.name)
                        if tool.is_file()
                        else source.relative_to(tool)
                    )
                    target = temporary / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    copied_bytes += self._copy_file_bounded(
                        source,
                        target,
                        max_bytes=size_bytes - copied_bytes,
                    )
                    copied_files.append(target)
                if copied_bytes != size_bytes:
                    raise ValueError("tool changed while it was being copied")
                copied_hash, _ = self._tool_digest(
                    temporary,
                    copied_files,
                    expected_size=size_bytes,
                )
                if copied_hash != snapshot_hash:
                    raise ValueError("tool changed while it was being copied")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary, destination)
                self._make_read_only(destination)
                created_snapshot = True
            except Exception:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
                raise

        name, summary, entrypoint = _manifest_metadata(tool)
        identity = hashlib.sha256(
            f"{candidate_id}\0{iteration}\0{source_relative_path}\0{snapshot_hash}".encode(
                "utf-8"
            )
        ).hexdigest()
        return SharedToolRecord(
            tool_id=f"{candidate_id}-i{iteration:04d}-{identity[:16]}",
            candidate_id=candidate_id,
            iteration=iteration,
            source_commit=source_commit,
            snapshot_hash=snapshot_hash,
            name=name,
            summary=summary,
            entrypoint=entrypoint,
            source_relative_path=source_relative_path,
            read_only_path=destination.resolve(),
            files=relative_files,
            size_bytes=size_bytes,
            created_at=_utc_timestamp(),
        ), created_snapshot

    @staticmethod
    def _copy_file_bounded(source: Path, target: Path, *, max_bytes: int) -> int:
        """Copy one source file without letting a concurrent append exceed bounds."""
        copied = 0
        with source.open("rb") as reader, target.open("xb") as writer:
            while True:
                chunk = reader.read(min(1024 * 1024, max_bytes - copied + 1))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise ValueError("tool changed while it was being copied")
                writer.write(chunk)
        return copied

    def _claim_staging(
        self,
        share_out_dir: Path,
        *,
        candidate_id: str,
        iteration: int,
    ) -> Path:
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        claim = self.pending_dir / (
            f"{_safe_name(candidate_id)}-i{iteration:04d}-{uuid.uuid4().hex}"
        )
        os.replace(share_out_dir, claim)
        try:
            share_out_dir.mkdir(parents=True, exist_ok=False)
        except Exception:
            os.replace(claim, share_out_dir)
            raise
        return claim

    @staticmethod
    def _restore_entry(entry: Path, share_out_dir: Path, errors: list[str]) -> None:
        target = share_out_dir / entry.name
        if target.exists():
            errors.append(
                f"{entry.name}: could not restore rejected staging because "
                "a new entry already exists"
            )
            return
        try:
            os.replace(entry, target)
        except OSError as exc:
            errors.append(f"{entry.name}: could not restore rejected staging: {exc}")

    def _safe_snapshot_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=True)
            return resolved.is_dir() and resolved.is_relative_to(
                self.tools_dir.resolve()
            )
        except (OSError, RuntimeError):
            return False

    def _remove_unindexed_snapshot(self, path: Path) -> None:
        """Best-effort rollback for a snapshot not committed to ``index.json``."""
        if not self._safe_snapshot_path(path):
            return
        try:
            for item in sorted(path.rglob("*"), reverse=True):
                try:
                    item.chmod(0o666 if item.is_file() else 0o777)
                except OSError:
                    pass
            path.chmod(0o777)
            shutil.rmtree(path)
        except OSError:
            # A failed rollback leaves an unreachable runtime-owned orphan;
            # it is never referenced by Global Evidence or the shared index.
            pass

    @staticmethod
    def _latest_by_source(
        tools: list[dict[str, Any]],
        candidate_id: str,
    ) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for tool in tools:
            if tool.get("candidate_id") != candidate_id:
                continue
            source = tool.get("source_relative_path")
            if not isinstance(source, str):
                continue
            previous = latest.get(source)
            iteration = tool.get("iteration")
            if previous is None or (
                isinstance(iteration, int)
                and iteration > int(previous.get("iteration", 0))
            ):
                latest[source] = tool
        return latest

    def _physical_by_hash(self, tools: list[dict[str, Any]]) -> dict[str, Path]:
        result: dict[str, Path] = {}
        for tool in tools:
            snapshot_hash = tool.get("snapshot_hash")
            raw_path = tool.get("read_only_path")
            if not isinstance(snapshot_hash, str) or not isinstance(raw_path, str):
                continue
            path = Path(raw_path)
            if self._safe_snapshot_path(path):
                result.setdefault(snapshot_hash, path)
        return result

    @staticmethod
    def _make_read_only(root: Path) -> None:
        """Apply advisory read-only modes on both POSIX and Windows."""
        try:
            for path in sorted(root.rglob("*"), reverse=True):
                try:
                    path.chmod(0o444 if path.is_file() else 0o555)
                except OSError:
                    pass
            root.chmod(0o555)
        except OSError:
            pass

    def _append_index(self, tools: list[SharedToolRecord]) -> None:
        existing = self._load_index()
        by_id = {item.get("tool_id"): item for item in existing}
        for tool in tools:
            payload = tool.model_dump(mode="json")
            by_id[payload["tool_id"]] = payload
        self._write_index(list(by_id.values()))

    def _load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        tools = payload.get("tools") if isinstance(payload, dict) else None
        if not isinstance(tools, list):
            raise ValueError("shared tool index has an invalid shape")
        return [item for item in tools if isinstance(item, dict)]

    def _write_index(self, tools: list[dict[str, Any]]) -> None:
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SHARED_INDEX_SCHEMA_VERSION,
            "tools": tools,
        }
        self.index_temp_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.index_temp_dir / (
            f"index-{os.getpid()}-{uuid.uuid4().hex}.json"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.index_path)
