#!/usr/bin/env python3
"""
PPT Master - Content-Bound Gate Receipts

Bind successful planning and SVG checks to their exact inputs and validator
bundle. Older timestamp-only stamps are deliberately treated as cache misses.

Usage:
    Import capture_gate, write_gate_pass, and gate_pass_current from validators.

Examples:
    before = capture_gate(project, "spec", options={"strict": False})
    write_gate_pass(project, "spec", before)

Dependencies:
    None (only uses standard library).
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SCHEMA = "content-gate-receipt.v1"
_STAMPS = {"spec": ".spec_pass.json", "svg-quality": ".qc_pass.json"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validator_bundle(gate: str) -> str:
    # The SVG checker shares geometry, chart and import contracts throughout
    # scripts/. Hash the complete Python bundle rather than miss a transitive
    # rule dependency. Planning has a smaller, explicit dependency closure.
    if gate == "spec":
        paths = [_SCRIPTS_DIR / name for name in (
            "validate_spec.py", "gate_receipts.py", "console_encoding.py",
        )]
    else:
        paths = sorted(
            path for path in _SCRIPTS_DIR.rglob("*")
            if path.is_file() and path.suffix in {".py", ".json", ".xml", ".txt"}
        )
    paths.append(_SCRIPTS_DIR.parent / "templates/charts/charts_index.json")
    manifest = {
        path.relative_to(_SCRIPTS_DIR.parent).as_posix(): _sha256(path)
        for path in paths
    }
    versions = {}
    for package in ("Pillow", "lxml", "PyMuPDF"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    payload = {"files": manifest, "python": sys.version, "platform": platform.platform(), "packages": versions}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def capture_gate(project: Path, gate: str, *, options: dict | None = None) -> dict:
    """Capture present and absent inputs before a gate starts."""
    if gate not in _STAMPS:
        raise ValueError(f"unknown content gate: {gate}")
    project = project.resolve()
    paths = [project / "design_spec.md", project / "spec_lock.md"]
    if gate == "svg-quality":
        paths.extend(project / name for name in (
            "project_meta.json", "animations.json", "native_structure.json", "source_template.pptx",
        ))
        for folder in ("svg_output", "images", "icons"):
            paths.extend(path for path in (project / folder).rglob("*") if path.is_file() or path.is_symlink())
        for page in (project / "svg_output").glob("*.svg"):
            if page.is_symlink() or not page.resolve().is_relative_to(project):
                raise ValueError(f"gate page escapes the project or is a symlink: {page.name}")
            try:
                document = ET.parse(page)
            except ET.ParseError:
                continue  # The owning checker reports malformed page XML.
            for element in document.iter():
                for key in ("href", "{http://www.w3.org/1999/xlink}href"):
                    reference = urlsplit(element.get(key, ""))
                    if reference.path and not reference.scheme and not reference.netloc:
                        paths.append(page.parent / unquote(reference.path))
    inputs = {}
    for path in sorted(set(paths)):
        if not path.resolve().is_relative_to(project) or path.is_symlink():
            raise ValueError(f"gate input escapes the project or is a symlink: {path.name}")
        if path.exists() and not path.is_file():
            raise ValueError(f"gate input is not a file: {path.name}")
        # SVG hrefs routinely use ../images; the receipt records one canonical
        # in-project path rather than an alias containing traversal segments.
        inputs[path.resolve().relative_to(project).as_posix()] = _sha256(path) if path.is_file() else None
    return {
        "schema_version": _SCHEMA,
        "gate": gate,
        "input_sha256": inputs,
        "validator_bundle_sha256": _validator_bundle(gate),
        "options": options or ({"strict": False} if gate == "spec" else {"format": None, "scope": "full"}),
    }


def write_gate_pass(project: Path, gate: str, before: dict) -> None:
    """Write PASS only if both checked inputs and validator rules stayed fixed."""
    current = capture_gate(project, gate, options=before.get("options"))
    if before != current:
        raise ValueError("gate inputs or validator bundle changed during validation; re-run the gate")
    payload = {
        **before,
        "verdict": "PASS",
        "passed_at": datetime.now(timezone.utc).isoformat(),
    }
    destination = project / _STAMPS[gate]
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=project)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def gate_pass_current(project: Path, gate: str, *, options: dict | None = None) -> bool:
    """Reject missing, malformed, legacy, nonpassing, or stale receipts."""
    try:
        path = project / _STAMPS[gate]
        if path.is_symlink():
            return False
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("verdict") != "PASS":
            return False
        expected = capture_gate(project, gate, options=options)
        if any(receipt.get(key) != value for key, value in expected.items()):
            return False
        return isinstance(receipt.get("passed_at"), str) and bool(receipt["passed_at"])
    except (OSError, ValueError, KeyError):
        return False
