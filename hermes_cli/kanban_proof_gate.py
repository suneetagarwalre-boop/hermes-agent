"""Opt-in deterministic file proof gates for bounded Kanban canaries.

A card opts in by embedding exactly one JSON object in an HTML comment::

    <!-- hermes-proof-gate
    {"type":"file_equals","path":"proof.txt","expected":"PASS\n",
     "max_bytes":1024}
    -->

Only local read-back checks are supported. The completion path never executes a
command from card text. Cards without this marker are unchanged.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Optional


_MARKER_START_RE = re.compile(r"<!--\s*hermes-proof-gate\b", re.IGNORECASE)
_MARKER_RE = re.compile(
    r"<!--\s*hermes-proof-gate\b(.*?)-->",
    re.DOTALL | re.IGNORECASE,
)
_MAX_BYTES = 1024 * 1024
_VALID_TYPES = frozenset({"file_equals", "file_sha256"})


class ProofGateSpecError(ValueError):
    """Raised when a card contains an invalid or ambiguous proof-gate marker."""


@dataclass(frozen=True)
class ProofGateSpec:
    gate_type: str
    path: str
    expected: str
    max_bytes: int = 64 * 1024


@dataclass(frozen=True)
class ProofGateResult:
    passed: bool
    detail: str
    evidence: dict[str, Any]


def parse_proof_gate(body: Optional[str]) -> Optional[ProofGateSpec]:
    """Parse one opt-in file proof gate from a task body, failing closed."""
    text = body or ""
    starts = _MARKER_START_RE.findall(text)
    matches = _MARKER_RE.findall(text)
    if not starts:
        return None
    if len(starts) != len(matches):
        raise ProofGateSpecError("proof gate marker is malformed or missing '-->'")
    if len(matches) != 1:
        raise ProofGateSpecError("task must contain exactly one hermes-proof-gate marker")
    try:
        raw = json.loads(matches[0].strip())
    except json.JSONDecodeError as exc:
        raise ProofGateSpecError(f"proof gate JSON is invalid: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ProofGateSpecError("proof gate must be a JSON object")

    gate_type = raw.get("type")
    if gate_type not in _VALID_TYPES:
        raise ProofGateSpecError(
            f"proof gate type must be one of {sorted(_VALID_TYPES)}"
        )
    path = raw.get("path")
    raw_parts = re.split(r"[\\/]", path) if isinstance(path, str) else []
    if (
        not isinstance(path, str)
        or not path.strip()
        or path != path.strip()
        or "\x00" in path
        or PurePosixPath(path).is_absolute()
        or PureWindowsPath(path).is_absolute()
    ):
        raise ProofGateSpecError(
            "proof gate path must be a non-empty path relative to the task workspace"
        )
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise ProofGateSpecError(
            "proof gate path cannot contain empty, '.' or '..' components"
        )
    parts = PurePath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ProofGateSpecError(
            "proof gate path cannot contain empty, '.' or '..' components"
        )

    expected = raw.get("expected")
    if not isinstance(expected, str):
        raise ProofGateSpecError("proof gate expected must be a string")
    try:
        expected_bytes = expected.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProofGateSpecError("proof gate expected must be valid UTF-8") from exc
    if gate_type == "file_sha256" and not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ProofGateSpecError("file_sha256 expected must be a 64-character hex digest")

    max_bytes = raw.get("max_bytes", 64 * 1024)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ProofGateSpecError("proof gate max_bytes must be an integer")
    if max_bytes < 1 or max_bytes > _MAX_BYTES:
        raise ProofGateSpecError(
            f"proof gate max_bytes must be between 1 and {_MAX_BYTES}"
        )
    if gate_type == "file_equals" and len(expected_bytes) > max_bytes:
        raise ProofGateSpecError("file_equals expected value exceeds max_bytes")
    return ProofGateSpec(gate_type, path, expected, max_bytes)


def _spec_sha256(spec: ProofGateSpec) -> str:
    payload = json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_workspace_file(
    workspace: Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> tuple[bytes, os.stat_result, os.stat_result]:
    """Read a regular file using descriptor-relative no-follow traversal.

    Every component is opened relative to an already-open directory descriptor.
    The final descriptor is checked before reading, closing symlink/rename, FIFO,
    and post-stat replacement races in path-based implementations.
    """
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise OSError("secure descriptor-relative proof reads are unsupported")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC

    workspace_fd = _open_workspace_directory(workspace, directory_flags)
    workspace_stat = os.fstat(workspace_fd)
    if not stat.S_ISDIR(workspace_stat.st_mode):
        os.close(workspace_fd)
        raise OSError("task workspace is not a directory")
    current_fd = workspace_fd
    file_fd: Optional[int] = None
    try:
        parts = PurePath(relative_path).parts
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            if current_fd != workspace_fd:
                os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("proof artifact is not a regular file")
        if file_stat.st_size > max_bytes:
            raise OSError(
                f"proof artifact is {file_stat.st_size} bytes; max_bytes is {max_bytes}"
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        observed = b"".join(chunks)
        if len(observed) > max_bytes:
            raise OSError(f"proof artifact exceeds max_bytes {max_bytes}")
        return observed, workspace_stat, file_stat
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if current_fd != workspace_fd:
            os.close(current_fd)
        os.close(workspace_fd)


def _open_workspace_directory(workspace: Path, directory_flags: int) -> int:
    """Open an absolute directory path without following any component symlink."""
    if not workspace.is_absolute() or not workspace.anchor:
        raise OSError("task workspace must resolve to an absolute path")
    current_fd = os.open(workspace.anchor, directory_flags)
    try:
        for component in workspace.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def run_proof_gate(task: Any, spec: ProofGateSpec) -> ProofGateResult:
    """Read and verify one bounded file inside ``task.workspace_path``."""
    workspace_raw = getattr(task, "workspace_path", None)
    if not workspace_raw:
        return ProofGateResult(False, "task has no resolved workspace_path", {})
    try:
        workspace = Path(str(workspace_raw)).expanduser()
        if not workspace.is_absolute() or any(
            part in {".", ".."} for part in workspace.parts
        ):
            return ProofGateResult(
                False,
                "task workspace_path must be an absolute path without traversal",
                {},
            )
        observed, workspace_stat, file_stat = _read_workspace_file(
            workspace,
            spec.path,
            max_bytes=spec.max_bytes,
        )
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            detail = "proof artifact path must not contain symbolic links"
        else:
            detail = f"proof artifact read failed: {exc}"
        return ProofGateResult(False, detail, {})

    observed_sha256 = hashlib.sha256(observed).hexdigest()
    expected_sha256 = (
        hashlib.sha256(spec.expected.encode("utf-8")).hexdigest()
        if spec.gate_type == "file_equals"
        else spec.expected.lower()
    )
    passed = observed_sha256 == expected_sha256
    evidence = {
        "version": 1,
        "type": spec.gate_type,
        "passed": passed,
        "checked_at": int(time.time()),
        "task_id": str(getattr(task, "id", "")),
        "run_id": getattr(task, "current_run_id", None),
        "workspace_path": str(workspace),
        "workspace_device": workspace_stat.st_dev,
        "workspace_inode": workspace_stat.st_ino,
        "contract_sha256": _spec_sha256(spec),
        "path": spec.path,
        "file_device": file_stat.st_dev,
        "file_inode": file_stat.st_ino,
        "size": len(observed),
        "observed_sha256": observed_sha256,
        "expected_sha256": expected_sha256,
    }
    if not passed:
        return ProofGateResult(False, "proof artifact did not match expected evidence", evidence)
    return ProofGateResult(True, "proof gate passed", evidence)


def evaluate_task_proof_gate(task: Any) -> Optional[ProofGateResult]:
    """Parse and execute a task's proof gate, or return ``None`` when absent."""
    spec = parse_proof_gate(getattr(task, "body", None))
    if spec is None:
        return None
    return run_proof_gate(task, spec)


def has_proof_gate_marker(body: Optional[str]) -> bool:
    """Return whether body contains a proof-gate start marker, even malformed."""
    return _MARKER_START_RE.search(body or "") is not None
