"""Small, shared helpers for JSON artifacts and readable Markdown."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any


@contextmanager
def run_directory_lock(directory: Path | str, *, filename: str = "run.lock"):
    """Allow one run per output directory, while retaining in-run concurrency.

    The OS releases the lock when the process exits, including after a crash.
    Keep the lock file in place: deleting it could let two processes lock
    different files at the same path. Its presence alone does not mean a run
    is active. The optional filename preserves existing auction lock paths.
    """
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / filename).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                f"Another run is already using {directory}. "
                "Wait for it to finish or choose a different output directory."
            ) from error
        try:
            yield directory
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _sha256_bytes(value: bytes) -> str:
    """Return the standard hexadecimal SHA-256 digest."""
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode())


def sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file and report the precise location of invalid JSON."""
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON on {path}:{line_number}: {error}") from error
    return rows


def append_jsonl(path: Path, row: dict[str, Any], *, sync: bool = False) -> None:
    """Append one JSON object, optionally syncing it before returning."""
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        if sync:
            os.fsync(handle.fileno())


def safe_name(value: str) -> str:
    """Make one identifier safe for use as a path component."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return safe or "unknown"


def fenced(value: Any, language: str = "text") -> str:
    """Put arbitrary text or JSON in a Markdown fence without truncation."""
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    )
    longest = max(
        (len(match.group()) for match in re.finditer(r"`+", text)),
        default=0,
    )
    marker = "`" * max(3, longest + 1)
    return f"{marker}{language}\n{text}\n{marker}"
