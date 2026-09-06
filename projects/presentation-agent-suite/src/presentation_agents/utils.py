#!/usr/bin/env python3
"""
Presentation Agent Suite - Shared Utilities

Provides atomic JSON writes, hashes, timestamps, and safe project paths.

Usage:
    Import from presentation_agents modules.

Dependencies:
    None (only uses standard library).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_SOURCE_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")


def now_iso() -> str:
    """Return a timezone-aware local timestamp."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    """Hash one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_files(paths: list[Path]) -> str:
    """Hash an ordered artifact bundle including each filename."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    """Atomically write formatted UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def require_slug(value: str) -> str:
    """Validate a stable lowercase workspace slug."""
    slug = value.strip().lower()
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError("slug must be 3-64 lowercase letters, digits, or hyphens")
    return slug


def require_source_id(value: str) -> str:
    """Validate a source identifier."""
    source_id = value.strip().upper()
    if not _SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError("source_id must be 3-64 uppercase letters, digits, or hyphens")
    return source_id


def relative_to_workspace(path: Path, workspace: Path) -> str:
    """Return a POSIX path contained by the workspace."""
    resolved = path.resolve()
    root = workspace.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {path}") from exc
