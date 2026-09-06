#!/usr/bin/env python3
"""
Presentation Agent Suite - Three-Agent Pipeline

Creates a SlideMaster-compatible workspace and enforces hash-bound handoffs
between intent, research, and presentation-design agents.

Usage:
    Import AgentPipeline or use presentation_agents.cli.

Dependencies:
    PyMuPDF only when completing the research stage.
"""

from __future__ import annotations

import json
import io
import hashlib
import mimetypes
import math
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from presentation_agents.pdf_report import render_markdown_pdf, validate_pdf_report
from presentation_agents.utils import (
    now_iso,
    read_json,
    relative_to_workspace,
    require_slug,
    require_source_id,
    sha256_file,
    sha256_files,
    write_json,
)
from presentation_agents.validation import validate_agent_manifests


_SUITE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_AGENT_PIPELINE = "agent_pipeline"
_REQUIRED_INTENT_FIELDS = (
    "topic",
    "audience",
    "objective",
    "desired_decision",
    "key_message",
    "call_to_action",
    "source_scope",
    "must_include",
    "must_exclude",
    "delivery_mode",
    "duration_minutes",
    "slide_count",
    "language",
    "content_divergence",
    "template_or_brand",
    "speaker_notes",
    "confidentiality",
    "success_criteria",
)
_ALLOWED_ROLES = {
    "context",
    "scope",
    "question",
    "process",
    "evidence",
    "comparison",
    "finding",
    "implication",
    "decision",
    "appendix",
}
_STATUS_AGENT = {
    "INTENT_INTERVIEW": "intent-architect",
    "INTENT_WAITING_USER": "intent-architect",
    "INTENT_REVIEW": "intent-architect",
    "RESEARCH_READY": "research-analyst",
    "RESEARCH_ACTIVE": "research-analyst",
    "RESEARCH_WAITING_USER": "research-analyst",
    "DESIGN_READY": "presentation-designer",
    "DESIGN_HANDOFF_WAITING_USER": "presentation-designer",
    "DESIGN_AUTHORIZED": "presentation-designer",
    "DESIGN_ACTIVE": "presentation-designer",
    "DESIGN_APPROVED": "presentation-designer",
    "DESIGN_REVIEW": "presentation-designer",
    "COMPLETE": "presentation-designer",
}
_ALLOWED_TRANSITIONS = {
    "INTENT_INTERVIEW": {"INTENT_WAITING_USER", "INTENT_REVIEW"},
    "INTENT_WAITING_USER": {"INTENT_INTERVIEW", "INTENT_REVIEW"},
    "INTENT_REVIEW": {"INTENT_WAITING_USER", "INTENT_REVIEW", "RESEARCH_READY"},
    "RESEARCH_READY": {"RESEARCH_ACTIVE"},
    "RESEARCH_ACTIVE": {"RESEARCH_WAITING_USER", "DESIGN_READY"},
    "RESEARCH_WAITING_USER": {"RESEARCH_ACTIVE"},
    "DESIGN_READY": {"RESEARCH_ACTIVE", "DESIGN_HANDOFF_WAITING_USER", "DESIGN_AUTHORIZED"},
    "DESIGN_HANDOFF_WAITING_USER": {"DESIGN_READY"},
    "DESIGN_AUTHORIZED": {"DESIGN_ACTIVE"},
    "DESIGN_ACTIVE": {"DESIGN_APPROVED"},
    "DESIGN_APPROVED": {"DESIGN_REVIEW"},
    "DESIGN_REVIEW": {"COMPLETE", "DESIGN_APPROVED", "DESIGN_ACTIVE"},
    "COMPLETE": {"DESIGN_APPROVED", "DESIGN_ACTIVE"},
}
# Every downstream validation uses the same required approval/receipt frontier.
_DESIGN_AUTHORIZED_STATES = {
    "DESIGN_AUTHORIZED", "DESIGN_ACTIVE", "DESIGN_APPROVED", "DESIGN_REVIEW", "COMPLETE",
}
_RESEARCH_COMPLETE_STATES = _DESIGN_AUTHORIZED_STATES | {
    "DESIGN_READY", "DESIGN_HANDOFF_WAITING_USER",
}
_INTENT_APPROVED_STATES = _RESEARCH_COMPLETE_STATES | {
    "RESEARCH_READY", "RESEARCH_ACTIVE", "RESEARCH_WAITING_USER",
}
_UPSTREAM_GATE_REGISTRY = (
    ("01_intent/approval.json", "_verify_intent_approval", _INTENT_APPROVED_STATES),
    ("02_research/research_receipt.json", "_verify_research_receipt", _RESEARCH_COMPLETE_STATES),
    ("approvals/research_handoff_approval.json", "_verify_research_handoff_approval", _DESIGN_AUTHORIZED_STATES),
    ("03_design/design_approval.json", "_verify_design_approval", {"DESIGN_APPROVED", "DESIGN_REVIEW", "COMPLETE"}),
)
_REQUIRED_ANALYSIS_HEADINGS = (
    "Executive conclusion",
    "조사 질문별 답변",
    "핵심 주장과 근거",
    "비교·계산·가정",
    "반대 근거와 대안 해석",
    "불확실성·누락·시간 경계",
    "슬라이드별 evidence packet",
    "출처 목록",
)
_REQUIRED_GUIDELINE_LABELS = (
    "Claim:",
    "Evidence:",
    "Visual:",
    "Axes/units:",
    "Media/license:",
    "Caveat/source note:",
    "Density/emphasis:",
    "Avoid:",
)
_MAX_PPTX_MEMBERS = 10_000
_MAX_PPTX_UNCOMPRESSED_BYTES = 1_000_000_000
_MAX_PPTX_COMPRESSION_RATIO = 500
_MAX_PNG_DECOMPRESSED_BYTES = 250_000_000
_MAX_PNG_FILE_BYTES = 64_000_000
_MAX_CHILD_OUTPUT_BYTES = 64_000
_VERIFY_DECK_TIMEOUT_SECONDS = 600
_CANONICAL_DESIGN_ROUTE = "main-svg-generation"
_CANONICAL_DESIGN_OWNER = ".claude/skills/ppt-master/SKILL.md"


class ContractError(RuntimeError):
    """Raised when a stage attempts to cross an unmet gate."""


class SubprocessCancelled(ContractError):
    """The supervisor cancelled an external verification or render process."""


def _terminate_subprocess_tree(process: subprocess.Popen) -> None:
    """Kill the owned process and descendants, including nested POSIX sessions."""
    if os.name != "posix":
        owner = getattr(process, '_intent_slide_owner', None)
        if owner is not None:
            owner.terminate()
            return
        if process.poll() is None:
            process.kill()
        return
    descendants = {process.pid}
    try:
        # Shared verifiers can create their own sessions; the original process
        # group alone therefore does not cover the renderer's complete tree.
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True,
            check=True, timeout=2,
        )
        pairs = [tuple(map(int, line.split())) for line in listing.stdout.splitlines() if line.strip()]
        while True:
            expanded = descendants | {pid for pid, parent in pairs if parent in descendants}
            if expanded == descendants:
                break
            descendants = expanded
    except (OSError, ValueError, subprocess.SubprocessError):
        # The isolated root group remains safe to terminate if process-table
        # inspection is unavailable; callers never target the service group.
        pass
    groups = {process.pid}
    for pid in descendants:
        try:
            groups.add(os.getpgid(pid))
        except ProcessLookupError:
            pass
    own_group = os.getpgrp()
    for group in groups - {own_group}:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for pid in descendants:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_bounded_subprocess(
    command: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a child while draining both streams and retaining bounded diagnostics."""
    if cancelled is not None and cancelled():
        raise SubprocessCancelled("verification was cancelled before starting a child process")
    owner = None
    if os.name == 'nt':
        from presentation_agents.v2.stdio_transport import StdioTransport
        owner = StdioTransport.launch(command, cwd=cwd, env=env, startup_timeout=min(timeout, 30))
        process = owner.process
        process._intent_slide_owner = owner
        process.stdin.close()
    else:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    assert process.stdout is not None
    assert process.stderr is not None
    captured: dict[str, bytearray] = {
        "stdout": bytearray(),
        "stderr": bytearray(),
    }
    truncated = {"stdout": False, "stderr": False}

    def drain(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(8_192)
            if not chunk:
                break
            remaining = _MAX_CHILD_OUTPUT_BYTES - len(captured[name])
            if remaining > 0:
                captured[name].extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[name] = True

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        deadline = time.monotonic() + timeout
        while True:
            if cancelled is not None and cancelled():
                raise SubprocessCancelled("verification or rendering was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                returncode = process.wait(timeout=min(0.2, remaining) if cancelled is not None else remaining)
                break
            except subprocess.TimeoutExpired:
                if cancelled is None or time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
        if cancelled is not None and cancelled():
            raise SubprocessCancelled("verification was cancelled before returning its result")
        # A child may exit while a descendant still holds stdout/stderr open.
        # Keep cancellation active during draining; closing a live buffered
        # reader can otherwise wait indefinitely for its lock.
        drain_deadline = time.monotonic() + 5
        for reader in readers:
            while reader.is_alive():
                if cancelled is not None and cancelled():
                    raise SubprocessCancelled("verification was cancelled while draining child output")
                remaining = drain_deadline - time.monotonic()
                if remaining <= 0:
                    raise OSError("child output reader did not terminate")
                reader.join(timeout=min(0.2, remaining) if cancelled is not None else remaining)
    except BaseException as exc:
        _terminate_subprocess_tree(process)
        process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=5)
        for reader, stream in zip(readers, (process.stdout, process.stderr)):
            if not reader.is_alive():
                stream.close()
        if owner:
            owner.close()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise subprocess.TimeoutExpired(
                command, timeout,
                output=_decode_bounded_output(captured["stdout"], truncated["stdout"]),
                stderr=_decode_bounded_output(captured["stderr"], truncated["stderr"]),
            ) from exc
        raise
    process.stdout.close()
    process.stderr.close()
    if owner:
        owner.close()
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=_decode_bounded_output(captured["stdout"], truncated["stdout"]),
        stderr=_decode_bounded_output(captured["stderr"], truncated["stderr"]),
    )


def _decode_bounded_output(data: bytearray, truncated: bool) -> str:
    text = bytes(data).decode("utf-8", errors="replace")
    if truncated:
        text += "\n...[output truncated at 64000 bytes]...\n"
    return text


def _is_nonempty(value: object) -> bool:
    if value is False or value == 0:
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        same = source.resolve() == destination.resolve()
    except FileNotFoundError:
        same = False
    if not same:
        shutil.copy2(source, destination)


def _path_has_symlink(path: Path, root: Path) -> bool:
    """Return whether an owned path or one of its descendants is a symlink."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _require_fresh_destination(destination: Path, owned_root: Path) -> None:
    """Reject stale files and symlinked parents before copying an owned artifact."""
    try:
        destination.parent.resolve().relative_to(owned_root.resolve())
    except ValueError as exc:
        raise ContractError(f"destination escapes its owned folder: {destination}") from exc
    if _path_has_symlink(destination.parent, owned_root):
        raise ContractError(f"destination uses a symlinked owned folder: {destination}")
    if destination.exists() or destination.is_symlink():
        raise ContractError(f"destination already exists outside the source manifest: {destination}")


def _evidence_items(outline: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return stable evidence IDs, slide IDs, and requested evidence text."""
    items: list[tuple[str, str, str]] = []
    for slide in outline.get("slides", []):
        slide_id = str(slide.get("slide_id") or "")
        for index, evidence in enumerate(slide.get("evidence_needed", []), start=1):
            items.append((f"EVID-{slide_id}-{index:02d}", slide_id, str(evidence)))
    return items


def _render_research_plan(outline: dict[str, Any], *, checked: bool) -> str:
    """Render the canonical ordered research plan for an approved outline."""
    checkbox = "x" if checked else " "
    lines = [
        "# Research Plan",
        "",
        "> Status: `ACTIVE` — 모든 조사 항목을 근거와 함께 닫는다.",
        "",
    ]
    for slide in outline["slides"]:
        lines.extend([
            f"## {slide['slide_id']} · {slide['title']}",
            "",
            *[
                f"- [{checkbox}] {evidence_id} | {evidence_text}"
                for evidence_id, evidence_slide_id, evidence_text in _evidence_items(outline)
                if evidence_slide_id == slide["slide_id"]
            ],
            "",
        ])
    return "\n".join(lines)


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _validate_png_bytes(data: bytes) -> None:
    """Validate a bounded, non-interlaced PNG through reconstructed scanlines."""
    invalid_message = "verify_deck.py produced an invalid contact-sheet PNG"
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ContractError(invalid_message)
    if len(data) > _MAX_PNG_DECOMPRESSED_BYTES:
        raise ContractError("verify_deck.py produced an oversized contact-sheet PNG")

    offset = len(signature)
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ContractError(invalid_message)
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise ContractError(invalid_message)
        chunk_type = data[offset + 4 : offset + 8]
        if (
            len(chunk_type) != 4
            or any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in chunk_type)
            or not 65 <= chunk_type[2] <= 90
        ):
            raise ContractError(invalid_message)
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ContractError(invalid_message)
        chunks.append((chunk_type, payload))
        offset = chunk_end
        if chunk_type == b"IEND":
            break

    if offset != len(data) or not chunks or chunks[0][0] != b"IHDR":
        raise ContractError(invalid_message)
    if chunks[-1] != (b"IEND", b""):
        raise ContractError(invalid_message)
    chunk_types = [chunk_type for chunk_type, _ in chunks]
    if chunk_types.count(b"IHDR") != 1 or chunk_types.count(b"IEND") != 1:
        raise ContractError(invalid_message)
    unknown_critical = [
        chunk_type
        for chunk_type in chunk_types
        if 65 <= chunk_type[0] <= 90
        and chunk_type not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
    ]
    if unknown_critical:
        raise ContractError(invalid_message)
    idat_indices = [index for index, chunk_type in enumerate(chunk_types) if chunk_type == b"IDAT"]
    if not idat_indices or idat_indices != list(range(idat_indices[0], idat_indices[-1] + 1)):
        raise ContractError(invalid_message)
    plte_indices = [index for index, chunk_type in enumerate(chunk_types) if chunk_type == b"PLTE"]
    if len(plte_indices) > 1 or (plte_indices and plte_indices[0] > idat_indices[0]):
        raise ContractError(invalid_message)

    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        raise ContractError(invalid_message)
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    legal_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    if (
        width <= 0
        or height <= 0
        or color_type not in legal_depths
        or bit_depth not in legal_depths[color_type]
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        raise ContractError(invalid_message)

    palette_entries = 0
    if plte_indices:
        palette = chunks[plte_indices[0]][1]
        if not palette or len(palette) % 3 != 0 or len(palette) > 256 * 3:
            raise ContractError(invalid_message)
        palette_entries = len(palette) // 3
    if color_type == 3:
        if not palette_entries or palette_entries > 2**bit_depth:
            raise ContractError(invalid_message)
    elif color_type in {0, 4} and plte_indices:
        raise ContractError(invalid_message)

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    expected_size = height * (row_bytes + 1)
    if expected_size > _MAX_PNG_DECOMPRESSED_BYTES:
        raise ContractError("verify_deck.py produced an oversized contact-sheet PNG")
    compressed = b"".join(chunks[index][1] for index in idat_indices)
    if not compressed:
        raise ContractError(invalid_message)
    try:
        decompressor = zlib.decompressobj()
        scanlines = decompressor.decompress(compressed, expected_size + 1)
    except zlib.error as exc:
        raise ContractError(invalid_message) from exc
    if (
        not decompressor.eof
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or len(scanlines) != expected_size
    ):
        raise ContractError(invalid_message)

    bytes_per_pixel = max(1, (channels * bit_depth + 7) // 8)
    previous = bytearray(row_bytes)
    reconstructed_rows: list[bytearray] = []
    stride = row_bytes + 1
    for row_index in range(height):
        row_offset = row_index * stride
        filter_type = scanlines[row_offset]
        if filter_type > 4:
            raise ContractError(invalid_message)
        raw = scanlines[row_offset + 1 : row_offset + stride]
        reconstructed = bytearray(row_bytes)
        for index, value in enumerate(raw):
            left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                predictor = _paeth_predictor(left, above, upper_left)
            reconstructed[index] = (value + predictor) & 0xFF
        reconstructed_rows.append(reconstructed)
        previous = reconstructed

    if color_type == 3:
        mask = (1 << bit_depth) - 1
        for row in reconstructed_rows:
            for column in range(width):
                bit_offset = column * bit_depth
                byte = row[bit_offset // 8]
                shift = 8 - bit_depth - (bit_offset % 8)
                if ((byte >> shift) & mask) >= palette_entries:
                    raise ContractError(invalid_message)


def _validate_png_path(path: Path) -> None:
    """Validate one bounded PNG without reading an unbounded file into memory."""
    try:
        if path.stat().st_size > _MAX_PNG_FILE_BYTES:
            raise ContractError("verify_deck.py produced an oversized contact-sheet PNG")
        with path.open("rb") as handle:
            data = handle.read(_MAX_PNG_FILE_BYTES + 1)
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError("verify_deck.py produced an unreadable contact-sheet PNG") from exc
    if len(data) > _MAX_PNG_FILE_BYTES:
        raise ContractError("verify_deck.py produced an oversized contact-sheet PNG")
    _validate_png_bytes(data)


def _officecli_bin() -> str | None:
    """Resolve OfficeCLI exactly as the canonical verify_deck.py does."""
    configured = os.environ.get("OFFICECLI_BIN")
    if configured is not None:
        return configured or None
    return shutil.which("officecli")


def _quicklook_presentation(archive: zipfile.ZipFile):
    """Read bounded slide selection; leave every slide and relationship untouched."""
    from lxml import etree

    entries = archive.infolist()
    if (len(entries) > 50_000 or len({entry.filename for entry in entries}) != len(entries)
            or sum(entry.file_size for entry in entries) > 512 * 1024 * 1024):
        raise ContractError("Quick Look input package exceeds bounds or has duplicate entries")
    entry = archive.getinfo("ppt/presentation.xml")
    if entry.file_size > 4 * 1024 * 1024:
        raise ContractError("Quick Look presentation XML exceeds 4 MB")
    data = archive.read(entry)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ContractError("Quick Look presentation XML cannot declare entities")
    try:
        tree = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as exc:
        raise ContractError("Quick Look presentation XML is invalid") from exc
    slides = tree.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldIdLst")
    if slides is None or not 1 <= len(slides) <= 500:
        raise ContractError("Quick Look requires 1..500 ordered slides")
    return tree, slides


def _write_quicklook_single_slide(pptx_path: Path, output_path: Path, index: int) -> dict:
    """Select one slide in a derived package, preserving all other ZIP entry bytes.

    macOS Quick Look can attach a previous slide's table PDF to a later slide
    when converting a multi-slide deck. A one-entry sldIdLst avoids that leak;
    it does not replace or edit any slide, notes, relationship or media part.
    """
    from lxml import etree

    with zipfile.ZipFile(pptx_path) as original:
        tree, slides = _quicklook_presentation(original)
        if not 0 <= index < len(slides):
            raise ContractError("Quick Look slide index is outside the presentation")
        selected = slides[index]
        for slide in list(slides):
            if slide is not selected:
                slides.remove(slide)
        xml = etree.tostring(tree, encoding="UTF-8", xml_declaration=True, standalone=True)
        with zipfile.ZipFile(output_path, "w") as derived:
            for entry in original.infolist():
                derived.writestr(entry, xml if entry.filename == "ppt/presentation.xml" else original.read(entry))
    return {"page": index + 1, "slide_id": selected.get("id"),
            "relationship_id": selected.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"),
            "derived_pptx_sha256": sha256_file(output_path)}


def _render_macos_quicklook_contact_sheet(
    pptx_path: Path,
    output_path: Path,
    *,
    diagnostics: list[str] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Render each selected slide independently, then compose the ordered grid."""

    def record(message: str) -> None:
        if diagnostics is not None:
            diagnostics.append(message.strip()[:1_000])

    if sys.platform != "darwin":
        record("macOS Quick Look fallback is unavailable on this platform")
        return False
    qlmanage = shutil.which("qlmanage")
    swift = shutil.which("swift")
    renderer = _SUITE_ROOT / "scripts/render_quicklook_contact_sheet.swift"
    if not qlmanage or not swift or not renderer.is_file():
        record("macOS Quick Look fallback dependency is missing")
        return False

    provenance_path = output_path.with_suffix(".render.json")
    page_images_path = output_path.parent / f"{output_path.stem}-pages"
    owns_page_images = False
    try:
        from PIL import Image, ImageDraw, ImageFont

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        if _path_has_symlink(page_images_path, output_path.parent):
            raise ContractError("Quick Look page image directory contains a symlink")
        if page_images_path.exists():
            shutil.rmtree(page_images_path)
        page_images_path.mkdir()
        owns_page_images = True
        with pptx_path.open("rb") as source:
            source_bytes = source.read(256 * 1024 * 1024 + 1)
        if len(source_bytes) > 256 * 1024 * 1024:
            raise ContractError("Quick Look source PPTX exceeds 256 MB")
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
            tree, slides = _quicklook_presentation(archive)
            count = len(slides)
            size = tree.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz")
            width, height = (float(size.get("cx")), float(size.get("cy"))) if size is not None else (1280.0, 720.0)
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise ContractError("Quick Look slide dimensions are invalid")
        aspect = width / height
        if not math.isfinite(aspect) or not 0.5 <= aspect <= 4:
            raise ContractError("Quick Look slide aspect is unsupported")
        columns = min(count, max(1, math.ceil(math.sqrt(count * aspect))))
        rows = math.ceil(count / columns)
        thumb_height = round(480 / aspect)
        canvas_width, canvas_height = 12 + columns * 492, 12 + rows * (thumb_height + 38)
        if max(canvas_width, canvas_height) > 32_768 or canvas_width * canvas_height > 40_000_000:
            raise ContractError("Quick Look contact sheet exceeds geometry bounds")
        canvas = Image.new("RGB", (canvas_width, canvas_height), (222, 230, 237))
        draw = ImageDraw.Draw(canvas)
        try:
            label_font = ImageFont.load_default(size=16)
        except TypeError:
            label_font = ImageFont.load_default()
        deadline = time.monotonic() + _VERIFY_DECK_TIMEOUT_SECONDS

        def remaining() -> float:
            if cancelled and cancelled():
                raise SubprocessCancelled("Quick Look rendering cancelled")
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise subprocess.TimeoutExpired("isolated Quick Look render", _VERIFY_DECK_TIMEOUT_SECONDS)
            return seconds

        with tempfile.TemporaryDirectory(prefix="presentation-quicklook-", dir=output_path.parent) as temp_root:
            temp = Path(temp_root)
            source_snapshot = temp / "source.pptx"
            source_snapshot.write_bytes(source_bytes)
            del source_bytes
            module_cache = temp / "module-cache"
            environment = os.environ.copy()
            environment["SWIFT_MODULECACHE_PATH"] = str(module_cache)
            environment["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
            page_proofs = []
            for index in range(count):
                remaining()
                page_root = temp / f"page-{index + 1:03d}"
                page_root.mkdir()
                selected = page_root / "selected.pptx"
                proof = _write_quicklook_single_slide(source_snapshot, selected, index)
                preview_root = page_root / "preview"
                preview_root.mkdir()
                quicklook_result = _run_bounded_subprocess(
                    [qlmanage, "-p", "-o", str(preview_root), str(selected)],
                    timeout=remaining(), env=environment, cancelled=cancelled,
                )
                if quicklook_result.returncode != 0:
                    detail = (quicklook_result.stderr or quicklook_result.stdout or "")[-800:]
                    raise ContractError(f"qlmanage page {index + 1} exited {quicklook_result.returncode}: {detail}")
                preview_files = sorted(preview_root.rglob("Preview.html"))
                if len(preview_files) != 1:
                    raise ContractError(f"qlmanage page {index + 1} produced {len(preview_files)} Preview.html files")
                page_png = page_root / "page.png"
                high_resolution = page_root / "slide-images"
                rendered = _run_bounded_subprocess(
                    [swift, str(renderer), str(preview_files[0]), str(page_png), "--slide-images-dir", str(high_resolution)],
                    timeout=remaining(), env=environment, cancelled=cancelled,
                )
                if rendered.returncode != 0 or not page_png.is_file():
                    detail = (rendered.stderr or rendered.stdout or "")[-800:]
                    raise ContractError(f"Swift renderer page {index + 1} exited {rendered.returncode}: {detail}")
                _validate_png_path(page_png)
                with Image.open(page_png) as rendered_page:
                    actual_thumb_height = rendered_page.height - 50
                    if rendered_page.width != 504 or abs(actual_thumb_height - thumb_height) > 1:
                        raise ContractError("isolated Quick Look render did not produce exactly one slide")
                    tile = rendered_page.convert("RGB").crop((12, 12, 492, 12 + actual_thumb_height))
                    if actual_thumb_height != thumb_height:
                        tile = tile.resize((480, thumb_height), Image.Resampling.LANCZOS)
                    x, y = 12 + index % columns * 492, 12 + index // columns * (thumb_height + 38)
                    canvas.paste(tile, (x, y))
                    draw.text((x + 4, y + thumb_height + 3), f"P{index + 1:02d}", fill=(23, 59, 94), font=label_font)
                proof["isolated_grid_sha256"] = sha256_file(page_png)
                if (high_resolution.is_symlink() or not high_resolution.is_dir()
                        or sorted(path.name for path in high_resolution.iterdir()) != ["P01.png"]):
                    raise ContractError("isolated Quick Look render did not produce exactly one page image")
                source_image = high_resolution / "P01.png"
                if source_image.is_symlink():
                    raise ContractError("Quick Look page image is a symlink")
                with source_image.open("rb") as stream:
                    page_bytes = stream.read(_MAX_PNG_FILE_BYTES + 1)
                if len(page_bytes) > _MAX_PNG_FILE_BYTES:
                    raise ContractError("Quick Look page image exceeds file bounds")
                _validate_png_bytes(page_bytes)
                with Image.open(io.BytesIO(page_bytes)) as page_image:
                    image_width, image_height = page_image.size
                if (image_width < 1920 or image_width * image_height > 16_000_000
                        or abs(image_height - image_width / aspect) > 1):
                    raise ContractError("Quick Look page image resolution or aspect is invalid")
                saved_image = page_images_path / f"P{index + 1:02d}.png"
                with saved_image.open("xb") as stream:
                    stream.write(page_bytes)
                proof.update(image_path=saved_image.relative_to(output_path.parent).as_posix(),
                             image_sha256=hashlib.sha256(page_bytes).hexdigest(),
                             image_width=image_width, image_height=image_height)
                page_proofs.append(proof)
                shutil.rmtree(page_root)
            remaining()
            if sha256_file(pptx_path) != source_sha:
                raise ContractError("PPTX changed during isolated rendering")
            canvas.save(output_path, format="PNG")
            _validate_png_path(output_path)
            write_json(provenance_path, {"schema_version": "quicklook-render.v1", "method": "isolated-slide-selection",
                "source_sha256": source_sha, "slide_count": count, "pages": page_proofs,
                "contact_sheet_sha256": sha256_file(output_path), "rendered_at": now_iso(),
                "boundary": "Only presentation.xml slide selection changes in temporary copies; no slide/notes/media edits. Quick Look/WebKit, not PowerPoint."})
            return True
    except SubprocessCancelled:
        output_path.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        if owns_page_images:
            shutil.rmtree(page_images_path)
        raise
    except subprocess.TimeoutExpired as exc:
        record(f"macOS Quick Look fallback timed out: {exc.cmd}")
    except (OSError, ContractError, ValueError, TypeError, KeyError, ZeroDivisionError, zipfile.BadZipFile) as exc:
        record(f"macOS Quick Look fallback failed: {exc}")
    output_path.unlink(missing_ok=True)
    provenance_path.unlink(missing_ok=True)
    if owns_page_images:
        shutil.rmtree(page_images_path)
    return False


class AgentPipeline:
    """Manage one hash-bound presentation-agent workspace."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.control_dir = self.workspace / _AGENT_PIPELINE
        if not (self.control_dir / "state.json").is_file():
            raise FileNotFoundError(f"not a presentation-agent workspace: {workspace}")

    @classmethod
    def create(
        cls,
        *,
        projects_root: Path,
        slug: str,
        request: str,
        canvas_format: str = "ppt169",
        date_prefix: str | None = None,
    ) -> "AgentPipeline":
        """Create a new isolated workspace under a projects root."""
        normalized_slug = require_slug(slug)
        if not request.strip():
            raise ValueError("request is required")
        if canvas_format not in {"ppt169", "ppt43"}:
            raise ValueError("canvas_format must be ppt169 or ppt43")
        if date_prefix is None:
            date_prefix = now_iso()[:10].replace("-", "")
        if not re.fullmatch(r"\d{8}", date_prefix):
            raise ValueError("date_prefix must be YYYYMMDD")

        projects_root = projects_root.resolve()
        workspace = projects_root / f"{date_prefix}_{normalized_slug}"
        try:
            workspace.resolve().relative_to(projects_root)
        except ValueError as exc:
            raise ValueError("workspace escapes projects root") from exc
        if workspace.exists():
            raise FileExistsError(workspace)

        directories = (
            "svg_output",
            "svg_final",
            "images",
            "icons",
            "live_preview",
            "sources",
            "analysis",
            "exports",
            f"{_AGENT_PIPELINE}/01_intent",
            f"{_AGENT_PIPELINE}/02_research/text",
            f"{_AGENT_PIPELINE}/02_research/media",
            f"{_AGENT_PIPELINE}/03_design",
            f"{_AGENT_PIPELINE}/logs",
        )
        for relative in directories:
            (workspace / relative).mkdir(parents=True, exist_ok=True)

        created = now_iso()
        write_json(
            workspace / "project_meta.json",
            {
                "title": normalized_slug,
                "canvas_format": canvas_format,
                "created": date_prefix,
                "presentation_agent_suite": "1.0.0",
            },
        )
        (workspace / "README.md").write_text(
            f"# {normalized_slug}\n\n"
            f"- Canvas format: `{canvas_format}`\n"
            f"- Created: `{date_prefix}`\n"
            "- Pipeline: `intent-architect → research-analyst → presentation-designer`\n\n"
            "Control artifacts live in `agent_pipeline/`; SlideMaster design artifacts use the project root.\n",
            encoding="utf-8",
        )
        (workspace / _AGENT_PIPELINE / "01_intent/request.md").write_text(
            f"# 최초 요청\n\n{request.strip()}\n",
            encoding="utf-8",
        )
        write_json(
            workspace / _AGENT_PIPELINE / "manifest.json",
            {
                "schema_version": "1.0.0",
                "suite_root": _SUITE_ROOT.as_posix(),
                "workspace": workspace.as_posix(),
                "agents": [
                    "intent-architect",
                    "research-analyst",
                    "presentation-designer",
                ],
                "created_at": created,
            },
        )
        write_json(
            workspace / _AGENT_PIPELINE / "state.json",
            {
                "schema_version": "1.0.0",
                "status": "INTENT_INTERVIEW",
                "revision": 1,
                "created_at": created,
                "updated_at": created,
                "history": [
                    {
                        "from": None,
                        "to": "INTENT_INTERVIEW",
                        "at": created,
                        "reason": "workspace created",
                    }
                ],
            },
        )
        write_json(workspace / _AGENT_PIPELINE / "01_intent/questions.json", {"questions": []})
        write_json(workspace / _AGENT_PIPELINE / "01_intent/user_answers.json", {"answers": []})
        write_json(
            workspace / _AGENT_PIPELINE / "01_intent/intent_contract.draft.json",
            {
                "schema_version": "1.0.0",
                "fields": {
                    field_id: {"value": None, "state": "unknown", "source": "unresolved"}
                    for field_id in _REQUIRED_INTENT_FIELDS
                },
            },
        )
        write_json(
            workspace / _AGENT_PIPELINE / "01_intent/story_outline.draft.json",
            {"schema_version": "1.0.0", "status": "draft", "slides": []},
        )
        write_json(workspace / _AGENT_PIPELINE / "02_research/questions.json", {"questions": []})
        write_json(workspace / _AGENT_PIPELINE / "02_research/user_answers.json", {"answers": []})
        write_json(workspace / _AGENT_PIPELINE / "02_research/source_manifest.json", {"sources": []})
        return cls(workspace)

    @property
    def state(self) -> dict[str, Any]:
        """Return the current state record."""
        return read_json(self.control_dir / "state.json")

    @property
    def current_agent_id(self) -> str:
        """Return the agent that owns the current state."""
        status = str(self.state["status"])
        try:
            return _STATUS_AGENT[status]
        except KeyError as exc:
            raise ContractError(f"unknown pipeline status: {status}") from exc

    def _transition(
        self,
        status: str,
        reason: str,
        *,
        state_updates: dict[str, object] | None = None,
    ) -> None:
        state = self.state
        previous = str(state["status"])
        allowed = _ALLOWED_TRANSITIONS.get(previous)
        if allowed is None or status not in allowed:
            raise ContractError(f"invalid state transition: {previous} -> {status}")
        timestamp = now_iso()
        history = list(state.get("history", []))
        history.append({"from": previous, "to": status, "at": timestamp, "reason": reason})
        if state_updates:
            protected = {"status", "revision", "updated_at", "history"} & set(state_updates)
            if protected:
                raise ValueError("state_updates cannot replace transition fields")
            state.update(state_updates)
        state.update({
            "status": status,
            "revision": int(state.get("revision", 0)) + 1,
            "updated_at": timestamp,
            "history": history,
        })
        write_json(self.control_dir / "state.json", state)

    def _require_status(self, *allowed: str) -> str:
        status = str(self.state["status"])
        if status not in allowed:
            raise ContractError(f"status {status} does not permit this action; expected {', '.join(allowed)}")
        return status

    def _require_supervisor_actor(self) -> None:
        if os.environ.get("PRESENTATION_SUITE_ACTOR") != "supervisor":
            raise ContractError(
                "this action requires the supervisor actor; use scripts/presentation_supervisor.py"
            )

    def _questions(self, stage: str) -> tuple[Path, dict[str, Any]]:
        if stage not in {"intent", "research"}:
            raise ValueError("stage must be intent or research")
        folder = "01_intent" if stage == "intent" else "02_research"
        path = self.control_dir / folder / "questions.json"
        return path, read_json(path)

    def _answer_receipts(self, stage: str) -> tuple[Path, dict[str, Any]]:
        if stage not in {"intent", "research"}:
            raise ValueError("stage must be intent or research")
        folder = "01_intent" if stage == "intent" else "02_research"
        path = self.control_dir / folder / "user_answers.json"
        return path, read_json(path)

    @staticmethod
    def _question_sha256(item: dict[str, Any]) -> str:
        immutable = {
            key: item.get(key)
            for key in (
                "question_id",
                "question",
                "impact",
                "asked_at",
                "resume_status",
                "question_type",
                "subject_sha256",
            )
        }
        payload = json.dumps(
            immutable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _answer_sha256(answer: str) -> str:
        return hashlib.sha256(answer.encode("utf-8")).hexdigest()

    def _verify_question_answers(self, stage: str) -> list[dict[str, Any]]:
        """Verify that every answered question has a supervisor-owned receipt."""
        _, record = self._questions(stage)
        _, receipt_record = self._answer_receipts(stage)
        questions = record.get("questions")
        receipts = receipt_record.get("answers")
        if not isinstance(questions, list) or not isinstance(receipts, list):
            raise ContractError(f"{stage} question or answer ledger is invalid")
        if any(not isinstance(item, dict) for item in questions + receipts):
            raise ContractError(f"{stage} question or answer ledger entries must be objects")
        question_by_id = {str(item.get("question_id") or ""): item for item in questions}
        receipt_by_id = {str(item.get("question_id") or ""): item for item in receipts}
        if len(question_by_id) != len(questions) or "" in question_by_id:
            raise ContractError(f"{stage} question IDs are missing or duplicated")
        if len(receipt_by_id) != len(receipts) or "" in receipt_by_id:
            raise ContractError(f"{stage} answer receipt IDs are missing or duplicated")
        raised_ids = {
            match.group(1)
            for entry in self.state.get("history", [])
            if isinstance(entry, dict)
            for match in [
                re.fullmatch(
                    rf"question raised: (Q-{stage.upper()}-\d{{3}})",
                    str(entry.get("reason") or ""),
                )
            ]
            if match is not None
        }
        if set(question_by_id) != raised_ids:
            raise ContractError(f"{stage} question ledger does not match state history")
        if not set(receipt_by_id).issubset(question_by_id):
            raise ContractError(f"{stage} answer receipt refers to an unknown question")
        answered: list[dict[str, Any]] = []
        for question_id, question in question_by_id.items():
            status = question.get("status")
            receipt = receipt_by_id.get(question_id)
            if status == "open":
                if receipt is not None:
                    raise ContractError(f"open question has an answer receipt: {question_id}")
                continue
            if status != "answered" or receipt is None:
                raise ContractError(f"question lacks a supervisor answer receipt: {question_id}")
            answer = str(question.get("answer") or "").strip()
            if not answer:
                raise ContractError(f"answered question has an empty answer: {question_id}")
            if (
                receipt.get("stage") != stage
                or receipt.get("actor") != "supervisor"
                or receipt.get("question_sha256") != self._question_sha256(question)
                or receipt.get("answer_sha256") != self._answer_sha256(answer)
            ):
                raise ContractError(f"supervisor answer receipt is stale: {question_id}")
            answered.append(question)
        return answered

    def _open_questions(self, stage: str) -> list[dict[str, Any]]:
        _, record = self._questions(stage)
        return [item for item in record.get("questions", []) if item.get("status") == "open"]

    def raise_question(self, *, stage: str, question: str, impact: str) -> dict[str, Any]:
        """Pause one stage for a material user decision."""
        if not question.strip() or not impact.strip():
            raise ValueError("question and impact are required")
        if stage == "intent":
            resume_status = self._require_status("INTENT_INTERVIEW", "INTENT_REVIEW")
            waiting_status = "INTENT_WAITING_USER"
        elif stage == "research":
            resume_status = self._require_status("RESEARCH_ACTIVE")
            waiting_status = "RESEARCH_WAITING_USER"
        else:
            raise ValueError("stage must be intent or research")
        self._verify_question_answers(stage)
        path, record = self._questions(stage)
        questions = list(record.get("questions", []))
        question_id = f"Q-{stage.upper()}-{len(questions) + 1:03d}"
        item = {
            "question_id": question_id,
            "question": question.strip(),
            "impact": impact.strip(),
            "status": "open",
            "answer": None,
            "asked_at": now_iso(),
            "answered_at": None,
            "resume_status": resume_status,
        }
        if stage == "intent" and resume_status == "INTENT_REVIEW":
            blueprint = self.control_dir / "01_intent/presentation_blueprint.md"
            if not blueprint.is_file():
                raise ContractError("intent review question requires a rendered blueprint")
            item["question_type"] = "blueprint-confirmation"
            item["subject_sha256"] = sha256_file(blueprint)
        questions.append(item)
        state_path = self.control_dir / "state.json"
        state_before = self.state
        try:
            write_json(path, {"questions": questions})
            self._transition(waiting_status, f"question raised: {question_id}")
        except Exception as exc:
            rollback_errors: list[str] = []
            for rollback_path, rollback_value in (
                (state_path, state_before),
                (path, record),
            ):
                try:
                    write_json(rollback_path, rollback_value)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{rollback_path.name}: {rollback_exc}")
            if rollback_errors:
                raise ContractError(
                    "raise-question transaction failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
        return item

    def answer_question(self, question_id: str, answer: str) -> dict[str, Any]:
        """Record a user answer and resume the paused stage."""
        self._require_supervisor_actor()
        if not answer.strip():
            raise ValueError("answer is required")
        for stage in ("intent", "research"):
            self._verify_question_answers(stage)
            path, record = self._questions(stage)
            questions = list(record.get("questions", []))
            for index, item in enumerate(questions):
                if item.get("question_id") != question_id:
                    continue
                if item.get("status") != "open":
                    raise ContractError(f"question is not open: {question_id}")
                expected_waiting = "INTENT_WAITING_USER" if stage == "intent" else "RESEARCH_WAITING_USER"
                self._require_status(expected_waiting)
                item = dict(item)
                item.update({
                    "status": "answered",
                    "answer": answer.strip(),
                    "answered_at": now_iso(),
                })
                questions[index] = item
                receipt_path, receipt_record = self._answer_receipts(stage)
                receipts = list(receipt_record.get("answers", []))
                receipts.append({
                    "schema_version": "1.0.0",
                    "stage": stage,
                    "actor": "supervisor",
                    "question_id": question_id,
                    "question_sha256": self._question_sha256(item),
                    "answer_sha256": self._answer_sha256(answer.strip()),
                    "answered_at": item["answered_at"],
                })
                resume_status = None
                if not any(question.get("status") == "open" for question in questions):
                    if stage == "research":
                        resume_status = "RESEARCH_ACTIVE"
                    else:
                        intent_paths = self._intent_paths()
                        resume_status = "INTENT_INTERVIEW"
                        if all(intent_path.is_file() for intent_path in intent_paths[:3]):
                            try:
                                self._validate_intent(
                                    read_json(intent_paths[0]),
                                    read_json(intent_paths[1]),
                                )
                            except ContractError:
                                pass
                            else:
                                resume_status = "INTENT_REVIEW"

                state_before = self.state
                questions_written = False
                receipts_written = False
                transition_started = False
                try:
                    write_json(path, {"questions": questions})
                    questions_written = True
                    write_json(receipt_path, {"answers": receipts})
                    receipts_written = True
                    if resume_status is not None:
                        transition_started = True
                        self._transition(resume_status, f"question answered: {question_id}")
                except Exception as exc:
                    rollback_errors: list[str] = []
                    if transition_started:
                        try:
                            write_json(self.control_dir / "state.json", state_before)
                        except Exception as rollback_exc:
                            rollback_errors.append(f"state.json: {rollback_exc}")
                    if receipts_written:
                        try:
                            write_json(receipt_path, receipt_record)
                        except Exception as rollback_exc:
                            rollback_errors.append(f"{receipt_path.name}: {rollback_exc}")
                    if questions_written:
                        try:
                            write_json(path, record)
                        except Exception as rollback_exc:
                            rollback_errors.append(f"{path.name}: {rollback_exc}")
                    if rollback_errors:
                        raise ContractError(
                            "answer transaction failed and rollback was incomplete: "
                            + "; ".join(rollback_errors)
                        ) from exc
                    raise
                return item
        raise KeyError(question_id)

    def record_intent(
        self,
        intent_contract: dict[str, object],
        story_outline: dict[str, object],
    ) -> Path:
        """Validate and write Agent 1's intent and Markdown blueprint."""
        self._require_status("INTENT_INTERVIEW", "INTENT_REVIEW")
        if self._open_questions("intent"):
            raise ContractError("open intent questions must be answered")
        self._validate_intent(intent_contract, story_outline)

        stage_dir = self.control_dir / "01_intent"
        intent_path = stage_dir / "intent_contract.json"
        outline_path = stage_dir / "story_outline.json"
        blueprint_path = stage_dir / "presentation_blueprint.md"
        write_json(intent_path, intent_contract)
        write_json(outline_path, story_outline)
        blueprint_path.write_text(
            self._render_blueprint(intent_contract, story_outline),
            encoding="utf-8",
        )
        (stage_dir / "approval.json").unlink(missing_ok=True)
        self._transition("INTENT_REVIEW", "validated intent blueprint written; user approval required")
        return blueprint_path

    def _validate_intent(
        self,
        intent_contract: dict[str, object],
        story_outline: dict[str, object],
    ) -> None:
        fields = intent_contract.get("fields")
        if not isinstance(fields, dict):
            raise ContractError("intent fields object is required")
        missing = sorted(set(_REQUIRED_INTENT_FIELDS) - set(fields))
        if missing:
            raise ContractError(f"missing intent fields: {', '.join(missing)}")
        invalid: list[str] = []
        for field_id in _REQUIRED_INTENT_FIELDS:
            entry = fields.get(field_id)
            if not isinstance(entry, dict):
                invalid.append(field_id)
                continue
            if entry.get("state") != "confirmed" or not _is_nonempty(entry.get("value")):
                invalid.append(field_id)
            if not str(entry.get("source") or "").strip():
                invalid.append(field_id)
        if invalid:
            raise ContractError(f"intent fields are not user-confirmed: {', '.join(sorted(set(invalid)))}")

        slides = story_outline.get("slides")
        if not isinstance(slides, list) or not slides:
            raise ContractError("story outline slides are required")
        expected_count = fields["slide_count"]["value"]
        if not isinstance(expected_count, int) or expected_count < 1 or expected_count > 200:
            raise ContractError("slide_count must be an integer from 1 to 200")
        if len(slides) != expected_count:
            raise ContractError(
                f"slide count mismatch: intent={expected_count}, outline={len(slides)}"
            )
        seen: set[str] = set()
        for index, slide in enumerate(slides, start=1):
            if not isinstance(slide, dict):
                raise ContractError(f"slide {index} must be an object")
            expected_id = f"P{index:02d}"
            if slide.get("slide_id") != expected_id:
                raise ContractError(f"slide id must be {expected_id}")
            if expected_id in seen:
                raise ContractError(f"duplicate slide id: {expected_id}")
            seen.add(expected_id)
            if slide.get("role") not in _ALLOWED_ROLES:
                raise ContractError(f"invalid slide role: {slide.get('role')}")
            for key in ("title", "purpose", "visual_intent"):
                if not _is_nonempty(slide.get(key)):
                    raise ContractError(f"{expected_id} missing {key}")
            for key in ("content", "evidence_needed"):
                value = slide.get(key)
                if not isinstance(value, list) or not value:
                    raise ContractError(f"{expected_id} missing {key}")

    def _render_blueprint(
        self,
        intent_contract: dict[str, object],
        story_outline: dict[str, object],
    ) -> str:
        fields = intent_contract["fields"]

        def value(field_id: str) -> object:
            return fields[field_id]["value"]

        def bullets(items: object) -> str:
            if isinstance(items, list):
                return "\n".join(f"- {item}" for item in items)
            return f"- {items}"

        slide_sections: list[str] = []
        research_needs: list[str] = []
        for slide in story_outline["slides"]:
            content = "\n".join(f"  - {item}" for item in slide["content"])
            evidence = "\n".join(f"  - {item}" for item in slide["evidence_needed"])
            research_needs.extend(f"{slide['slide_id']}: {item}" for item in slide["evidence_needed"])
            slide_sections.append(
                f"### {slide['slide_id']} · {slide['title']}\n\n"
                f"- **Role**: `{slide['role']}`\n"
                f"- **Purpose**: {slide['purpose']}\n"
                f"- **Content**:\n{content}\n"
                f"- **Evidence needed**:\n{evidence}\n"
                f"- **Structural visual intent**: {slide['visual_intent']}"
            )
        slides_text = "\n\n".join(slide_sections)

        return (
            "# Presentation Blueprint\n\n"
            "> Status: `READY FOR USER APPROVAL` — 승인 전에는 리서치로 넘기지 않는다.\n\n"
            "## 1. 승인 상태와 범위\n\n"
            f"- Topic: {value('topic')}\n"
            f"- Objective: {value('objective')}\n"
            f"- Confidentiality: {value('confidentiality')}\n\n"
            "## 2. 청중·결정·행동\n\n"
            f"- Audience: {value('audience')}\n"
            f"- Desired decision: {value('desired_decision')}\n"
            f"- Call to action: {value('call_to_action')}\n\n"
            "## 3. 핵심 메시지와 성공 기준\n\n"
            f"- Key message: {value('key_message')}\n\n"
            f"{bullets(value('success_criteria'))}\n\n"
            "## 4. 근거·포함·제외 경계\n\n"
            f"- Source scope: {value('source_scope')}\n"
            f"- Content divergence: {value('content_divergence')}\n\n"
            "### Must include\n\n"
            f"{bullets(value('must_include'))}\n\n"
            "### Must exclude\n\n"
            f"{bullets(value('must_exclude'))}\n\n"
            "## 5. 슬라이드별 구조\n\n"
            f"{slides_text}\n\n"
            "## 6. 전달 제약\n\n"
            f"- Delivery mode: `{value('delivery_mode')}`\n"
            f"- Duration: {value('duration_minutes')} minutes\n"
            f"- Slide count: {value('slide_count')}\n"
            f"- Language: `{value('language')}`\n"
            f"- Template / brand: {value('template_or_brand')}\n"
            f"- Speaker notes: {value('speaker_notes')}\n\n"
            "## 7. 리서치 요청 목록\n\n"
            f"{bullets(research_needs)}\n\n"
            "## 8. 디자인 에이전트용 구조적 힌트\n\n"
            "- 각 페이지의 role, purpose, evidence, structural visual intent를 유지한다.\n"
            "- 색·폰트·template·layout의 최종 lock은 SlideMaster 확인 단계에서 결정한다.\n\n"
            "## 9. 열린 질문\n\n"
            "- 없음\n\n"
            "## 10. 사용자 승인 기록\n\n"
            "- Status: `PENDING`\n"
            "- Approval receipt: `agent_pipeline/01_intent/approval.json`\n"
        )

    def _intent_paths(self) -> list[Path]:
        stage = self.control_dir / "01_intent"
        return [
            stage / "intent_contract.json",
            stage / "story_outline.json",
            stage / "presentation_blueprint.md",
            stage / "questions.json",
            stage / "user_answers.json",
            stage / "request.md",
            self.control_dir / "manifest.json",
        ]

    def approve_intent(self, note: str, decision: str) -> dict[str, Any]:
        """Create a hash-bound user approval for Agent 1 outputs."""
        self._require_supervisor_actor()
        self._require_status("INTENT_REVIEW")
        if decision != "APPROVE":
            raise ContractError("intent approval requires explicit decision APPROVE")
        if self._open_questions("intent"):
            raise ContractError("open intent questions must be answered")
        if not note.strip():
            raise ValueError("a non-empty user approval note is required")
        self._verify_question_answers("intent")
        _, question_record = self._questions("intent")
        blueprint_sha256 = sha256_file(self.control_dir / "01_intent/presentation_blueprint.md")
        answered_questions = [
            item
            for item in question_record.get("questions", [])
            if isinstance(item, dict)
            and item.get("status") == "answered"
            and str(item.get("answer") or "").strip()
            and item.get("question_type") == "blueprint-confirmation"
            and item.get("subject_sha256") == blueprint_sha256
        ]
        if not answered_questions:
            raise ContractError(
                "at least one answered confirmation question bound to the current blueprint is required"
            )
        paths = self._intent_paths()
        if not all(path.is_file() for path in paths):
            raise ContractError("intent artifacts are missing")
        intent_contract = read_json(paths[0])
        story_outline = read_json(paths[1])
        self._validate_intent(intent_contract, story_outline)
        expected_blueprint = self._render_blueprint(intent_contract, story_outline)
        if paths[2].read_text(encoding="utf-8") != expected_blueprint:
            raise ContractError("presentation blueprint does not match the validated intent contract")
        approval = {
            "schema_version": "1.0.0",
            "stage": "intent",
            "status": "APPROVED",
            "decision": decision,
            "approved_at": now_iso(),
            "approval_note": note.strip() or "explicit user approval",
            "confirmation_question_ids": [
                str(item["question_id"]) for item in answered_questions
            ],
            "artifact_sha256": sha256_files(paths),
            "files": {
                relative_to_workspace(path, self.workspace): sha256_file(path)
                for path in paths
            },
            "next_agent": "research-analyst",
        }
        write_json(self.control_dir / "01_intent/approval.json", approval)
        self._transition("RESEARCH_READY", "intent blueprint approved and hash-bound")
        return approval

    def _verify_intent_approval(self) -> dict[str, Any]:
        approval_path = self.control_dir / "01_intent/approval.json"
        if not approval_path.is_file():
            raise ContractError("intent approval is required")
        approval = read_json(approval_path)
        paths = self._intent_paths()
        if not all(path.is_file() for path in paths):
            raise ContractError("approved intent artifacts are missing")
        if (
            approval.get("status") != "APPROVED"
            or approval.get("decision") != "APPROVE"
            or approval.get("artifact_sha256") != sha256_files(paths)
        ):
            raise ContractError("intent approval is stale")
        self._verify_question_answers("intent")
        intent_contract = read_json(paths[0])
        story_outline = read_json(paths[1])
        self._validate_intent(intent_contract, story_outline)
        if paths[2].read_text(encoding="utf-8") != self._render_blueprint(
            intent_contract, story_outline
        ):
            raise ContractError("approved presentation blueprint is invalid")
        return approval

    def start_research(self) -> None:
        """Activate Agent 2 after verifying Agent 1 approval."""
        if self.state["status"] == "INTENT_REVIEW":
            raise ContractError("intent approval is required before research")
        self._require_status("RESEARCH_READY")
        self._verify_intent_approval()
        outline = read_json(self.control_dir / "01_intent/story_outline.json")
        (self.control_dir / "02_research/research_plan.md").write_text(
            _render_research_plan(outline, checked=False),
            encoding="utf-8",
        )
        self._transition("RESEARCH_ACTIVE", "research agent activated")

    def _source_manifest(self) -> tuple[Path, dict[str, Any]]:
        path = self.control_dir / "02_research/source_manifest.json"
        return path, read_json(path)

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source URL must be absolute http(s)")
        return url

    def _require_new_source_id(self, source_id: str) -> None:
        """Reject duplicate IDs before any destination file can be mutated."""
        _, manifest = self._source_manifest()
        sources = manifest.get("sources", [])
        if isinstance(sources, list) and any(
            isinstance(item, dict) and item.get("source_id") == source_id
            for item in sources
        ):
            raise ContractError(f"duplicate source_id: {source_id}")

    def _append_source(self, entry: dict[str, Any]) -> None:
        path, manifest = self._source_manifest()
        sources = list(manifest.get("sources", []))
        if any(item.get("source_id") == entry["source_id"] for item in sources):
            raise ContractError(f"duplicate source_id: {entry['source_id']}")
        sources.append(entry)
        write_json(path, {"schema_version": "1.0.0", "sources": sources})

    def _append_snapshot_source(
        self,
        *,
        source: Path,
        destination: Path,
        owned_root: Path,
        entry: dict[str, Any],
    ) -> None:
        """Commit a staged source and manifest entry as one rollback-safe update."""
        _require_fresh_destination(destination, owned_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, staged_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            dir=destination.parent,
        )
        os.close(descriptor)
        staged_path = Path(staged_name)
        manifest_path, manifest = self._source_manifest()
        sources = list(manifest.get("sources", []))
        if any(item.get("source_id") == entry["source_id"] for item in sources):
            staged_path.unlink(missing_ok=True)
            raise ContractError(f"duplicate source_id: {entry['source_id']}")
        manifest_written = False
        try:
            shutil.copy2(source, staged_path)
            entry["sha256"] = sha256_file(staged_path)
            sources.append(entry)
            write_json(
                manifest_path,
                {"schema_version": "1.0.0", "sources": sources},
            )
            manifest_written = True
            if destination.exists() or destination.is_symlink():
                raise ContractError(f"destination appeared during source commit: {destination}")
            staged_path.replace(destination)
        except Exception:
            if manifest_written:
                try:
                    write_json(manifest_path, manifest)
                except Exception as rollback_exc:
                    raise ContractError(
                        "source transaction failed and manifest rollback was incomplete"
                    ) from rollback_exc
            raise
        finally:
            staged_path.unlink(missing_ok=True)

    def add_text_source(
        self,
        *,
        source_id: str,
        title: str,
        url: str,
        markdown_file: Path,
        license_status: str,
        evidence_locator: str,
    ) -> dict[str, Any]:
        """Register one normalized Markdown text snapshot."""
        self._require_status("RESEARCH_ACTIVE")
        source_id = require_source_id(source_id)
        self._require_new_source_id(source_id)
        self._validate_url(url)
        markdown_file = markdown_file.resolve()
        if markdown_file.suffix.lower() not in {".md", ".markdown"}:
            raise ValueError("text sources must be Markdown")
        if not markdown_file.is_file() or not markdown_file.read_text(encoding="utf-8").strip():
            raise ValueError("Markdown source is missing or empty")
        title = title.strip()
        license_status = license_status.strip()
        evidence_locator = evidence_locator.strip()
        if not title or not license_status or not evidence_locator:
            raise ValueError("title, license_status, and evidence_locator are required")
        destination = self.control_dir / "02_research/text" / f"{source_id}.md"
        _require_fresh_destination(destination, self.control_dir / "02_research/text")
        entry = {
            "schema_version": "1.0.0",
            "source_id": source_id,
            "title": title,
            "source_url": url,
            "accessed_at": now_iso(),
            "resource_type": "text",
            "preservation_status": "SNAPSHOT",
            "local_path": relative_to_workspace(destination, self.workspace),
            "sha256": None,
            "mime_type": "text/markdown",
            "license_status": license_status,
            "evidence_locator": evidence_locator,
            "limitations": [],
        }
        self._append_snapshot_source(
            source=markdown_file,
            destination=destination,
            owned_root=self.control_dir / "02_research/text",
            entry=entry,
        )
        return entry

    def add_media_source(
        self,
        *,
        source_id: str,
        title: str,
        url: str,
        media_file: Path,
        license_status: str,
        evidence_locator: str,
    ) -> dict[str, Any]:
        """Register one original-format media snapshot."""
        self._require_status("RESEARCH_ACTIVE")
        source_id = require_source_id(source_id)
        self._require_new_source_id(source_id)
        self._validate_url(url)
        media_file = media_file.resolve()
        if not media_file.is_file() or media_file.stat().st_size == 0:
            raise ValueError("media source is missing or empty")
        if media_file.suffix.lower() in {".md", ".markdown", ".txt", ".html", ".htm"}:
            raise ValueError("text sources must be normalized through add_text_source")
        title = title.strip()
        license_status = license_status.strip()
        evidence_locator = evidence_locator.strip()
        if not title or not license_status or not evidence_locator:
            raise ValueError("title, license_status, and evidence_locator are required")
        destination = self.control_dir / "02_research/media" / source_id / media_file.name
        _require_fresh_destination(destination, self.control_dir / "02_research/media")
        mime_type = mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
        entry = {
            "schema_version": "1.0.0",
            "source_id": source_id,
            "title": title,
            "source_url": url,
            "accessed_at": now_iso(),
            "resource_type": "media",
            "preservation_status": "SNAPSHOT",
            "local_path": relative_to_workspace(destination, self.workspace),
            "original_filename": media_file.name,
            "sha256": None,
            "mime_type": mime_type,
            "license_status": license_status,
            "evidence_locator": evidence_locator,
            "limitations": [],
        }
        self._append_snapshot_source(
            source=media_file,
            destination=destination,
            owned_root=self.control_dir / "02_research/media",
            entry=entry,
        )
        return entry

    def add_blocked_source(
        self,
        *,
        source_id: str,
        title: str,
        url: str,
        resource_type: str,
        license_status: str,
        evidence_locator: str,
        failure_reason: str,
    ) -> dict[str, Any]:
        """Record an inaccessible source without pretending it was downloaded."""
        self._require_status("RESEARCH_ACTIVE")
        source_id = require_source_id(source_id)
        self._require_new_source_id(source_id)
        self._validate_url(url)
        if resource_type not in {"text", "media"}:
            raise ValueError("blocked source resource_type must be text or media")
        if not failure_reason.strip():
            raise ValueError("blocked source failure_reason is required")
        entry = {
            "schema_version": "1.0.0",
            "source_id": source_id,
            "title": title.strip(),
            "source_url": url,
            "accessed_at": now_iso(),
            "resource_type": resource_type,
            "preservation_status": "ACCESS_RESTRICTED",
            "local_path": None,
            "sha256": None,
            "mime_type": "unavailable",
            "license_status": license_status.strip() or "UNKNOWN",
            "evidence_locator": evidence_locator.strip(),
            "limitations": [failure_reason.strip()],
            "failure_reason": failure_reason.strip(),
        }
        if not entry["title"] or not entry["evidence_locator"]:
            raise ValueError("title and evidence_locator are required")
        self._append_source(entry)
        return entry

    def _verify_sources(self) -> dict[str, Any]:
        manifest_path, manifest = self._source_manifest()
        sources = manifest.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ContractError("at least one research source is required")
        seen: set[str] = set()
        snapshot_count = 0
        for source in sources:
            if not isinstance(source, dict):
                raise ContractError("source manifest entries must be objects")
            source_id = str(source.get("source_id") or "")
            try:
                require_source_id(source_id)
                self._validate_url(str(source.get("source_url") or ""))
            except ValueError as exc:
                raise ContractError(f"invalid source metadata: {source_id or '<missing>'}: {exc}") from exc
            if source_id in seen:
                raise ContractError(f"duplicate source_id: {source_id}")
            seen.add(source_id)
            resource_type = source.get("resource_type")
            if resource_type not in {"text", "media"}:
                raise ContractError(f"invalid resource_type: {source_id}")
            for key in ("title", "mime_type", "license_status", "evidence_locator"):
                if not str(source.get(key) or "").strip():
                    raise ContractError(f"source metadata is missing {key}: {source_id}")
            preservation_status = source.get("preservation_status")
            if preservation_status == "ACCESS_RESTRICTED":
                if not str(source.get("failure_reason") or "").strip():
                    raise ContractError(f"blocked source lacks failure reason: {source_id}")
                if source.get("local_path") is not None or source.get("sha256") is not None:
                    raise ContractError(f"blocked source must not claim a snapshot: {source_id}")
                continue
            if preservation_status != "SNAPSHOT":
                raise ContractError(f"source has invalid preservation status: {source_id}")
            snapshot_count += 1
            local_path = str(source.get("local_path") or "")
            if resource_type == "text":
                expected_path = self.control_dir / "02_research/text" / f"{source_id}.md"
            else:
                original_filename = str(source.get("original_filename") or "")
                if (
                    not original_filename
                    or original_filename in {".", ".."}
                    or "/" in original_filename
                    or "\\" in original_filename
                    or Path(original_filename).name != original_filename
                ):
                    raise ContractError(f"media source lacks a valid original filename: {source_id}")
                expected_path = (
                    self.control_dir
                    / "02_research/media"
                    / source_id
                    / original_filename
                )
            expected_local_path = expected_path.relative_to(self.workspace).as_posix()
            if local_path != expected_local_path:
                raise ContractError(f"source path is not canonical: {source_id}")
            if _path_has_symlink(expected_path, self.workspace):
                raise ContractError(f"source path contains a symlink: {source_id}")
            path = expected_path.resolve()
            try:
                path.relative_to(self.workspace)
            except ValueError as exc:
                raise ContractError(f"source escapes workspace: {source_id}") from exc
            if not path.is_file():
                raise ContractError(f"source file is missing: {source_id}")
            if source.get("sha256") != sha256_file(path):
                raise ContractError(f"source hash is stale: {source_id}")
            if resource_type == "text" and path.suffix.lower() not in {".md", ".markdown"}:
                raise ContractError(f"text source is not Markdown: {source_id}")
            if resource_type == "media" and path.suffix.lower() in {
                ".md",
                ".markdown",
                ".txt",
                ".html",
                ".htm",
            }:
                raise ContractError(f"media source is a text format: {source_id}")
        if snapshot_count == 0:
            raise ContractError("at least one successfully snapshotted source is required")
        if not manifest_path.is_file():
            raise ContractError("source manifest is missing")
        return manifest

    def complete_research(self, analysis_markdown: Path, design_guidelines: Path) -> dict[str, Any]:
        """Render and hash-lock Agent 2's complete research package."""
        self._require_status("RESEARCH_ACTIVE", "RESEARCH_WAITING_USER")
        self._verify_question_answers("research")
        if self._open_questions("research"):
            raise ContractError("open research questions must be answered")
        self._verify_intent_approval()
        source_manifest = self._verify_sources()
        for path, label in (
            (analysis_markdown, "analysis Markdown"),
            (design_guidelines, "design guidelines"),
        ):
            if not path.is_file() or len(path.read_text(encoding="utf-8").strip()) < 40:
                raise ContractError(f"{label} is missing or too short")
        plan_path = self.control_dir / "02_research/research_plan.md"
        if not plan_path.is_file():
            raise ContractError("research plan is missing")
        plan_text = plan_path.read_text(encoding="utf-8")
        if re.search(r"(?m)^\s*[-*+]\s+\[\s\]\s+", plan_text):
            raise ContractError("research plan has incomplete evidence items")
        outline = read_json(self.control_dir / "01_intent/story_outline.json")
        if plan_text != _render_research_plan(outline, checked=True):
            raise ContractError("research plan does not exactly match the approved outline")
        analysis_text = analysis_markdown.read_text(encoding="utf-8")
        guidelines_text = design_guidelines.read_text(encoding="utf-8")
        combined_text = analysis_text + "\n" + guidelines_text
        evidence_items = _evidence_items(outline)
        expected_evidence_ids = {item[0] for item in evidence_items}
        analysis_h2_headings = re.findall(r"(?m)^##[ \t]+(.+?)[ \t]*$", analysis_text)
        if analysis_h2_headings != list(_REQUIRED_ANALYSIS_HEADINGS):
            raise ContractError(
                "analysis H2 section sequence does not match the required contract"
            )
        evidence_packet_lines = {
            evidence_id: [
                line
                for line in analysis_text.splitlines()
                if f"[EVIDENCE: {evidence_id}]" in line
            ]
            for evidence_id in sorted(expected_evidence_ids)
        }
        missing_evidence_packets = [
            evidence_id for evidence_id, lines in evidence_packet_lines.items() if not lines
        ]
        if missing_evidence_packets:
            raise ContractError(
                "analysis is missing evidence packets: " + ", ".join(missing_evidence_packets)
            )

        source_citations = re.findall(
            r"\[SOURCE:\s*([A-Z][A-Z0-9-]{2,63})\s+@\s*([^\]]+?)\s*\]",
            combined_text,
        )
        restricted_markers = set(
            re.findall(
                r"\[ACCESS_RESTRICTED:\s*([A-Z][A-Z0-9-]{2,63})\s*\]",
                combined_text,
            )
        )
        source_by_id = {str(source["source_id"]): source for source in source_manifest["sources"]}
        unknown_citations = sorted(
            {source_id for source_id, _ in source_citations if source_id not in source_by_id}
            | {source_id for source_id in restricted_markers if source_id not in source_by_id}
        )
        if unknown_citations:
            raise ContractError("analysis cites unknown sources: " + ", ".join(unknown_citations))
        for evidence_id, lines in evidence_packet_lines.items():
            for line in lines:
                packet_citations = re.findall(
                    r"\[SOURCE:\s*([A-Z][A-Z0-9-]{2,63})\s+@\s*([^\]]+?)\s*\]",
                    line,
                )
                if not packet_citations:
                    raise ContractError(
                        f"evidence packet lacks a locator-bound source citation: {evidence_id}"
                    )
                for source_id, cited_locator in packet_citations:
                    source = source_by_id[source_id]
                    if source["preservation_status"] != "SNAPSHOT":
                        raise ContractError(
                            f"evidence packet cites a non-snapshot source: {evidence_id} @ {source_id}"
                        )
                    expected_locator = str(source["evidence_locator"]).strip()
                    if cited_locator.strip() != expected_locator:
                        raise ContractError(
                            "evidence packet has an invalid source locator: "
                            f"{evidence_id} @ {source_id}"
                        )
        for source_id, source in source_by_id.items():
            matching_citations = [
                locator.strip()
                for cited_id, locator in source_citations
                if cited_id == source_id
            ]
            if source["preservation_status"] == "ACCESS_RESTRICTED":
                if matching_citations:
                    raise ContractError(
                        f"access-restricted source cannot be cited as evidence: {source_id}"
                    )
                if source_id not in restricted_markers:
                    raise ContractError(
                        f"access-restricted source is not disclosed in analysis: {source_id}"
                    )
                continue
            locator = str(source["evidence_locator"]).strip()
            if locator not in matching_citations:
                raise ContractError(
                    f"snapshotted source lacks a locator-bound citation: {source_id} @ {locator}"
                )

        missing_guideline_pages = [
            str(slide["slide_id"])
            for slide in outline["slides"]
            if not re.search(
                rf"(?m)^##+\s+{re.escape(str(slide['slide_id']))}(?:\s|$)",
                guidelines_text,
            )
        ]
        if missing_guideline_pages:
            raise ContractError(
                "design guidelines are missing slide sections: "
                + ", ".join(missing_guideline_pages)
            )
        guideline_field_names = [label.removesuffix(":") for label in _REQUIRED_GUIDELINE_LABELS]
        guideline_field_pattern = re.compile(
            r"^\s*[-*+]\s+("
            + "|".join(re.escape(name) for name in guideline_field_names)
            + r"):\s*(.*?)\s*$"
        )
        for slide in outline["slides"]:
            slide_id = str(slide["slide_id"])
            section_match = re.search(
                rf"(?ms)^##+\s+{re.escape(slide_id)}(?:\s|$)(.*?)(?=^##+\s+|\Z)",
                guidelines_text,
            )
            if section_match is None:
                continue
            section = section_match.group(1)
            field_values = {name: [] for name in guideline_field_names}
            for line in section.splitlines():
                match = guideline_field_pattern.fullmatch(line)
                if match is not None:
                    field_values[match.group(1)].append(match.group(2).strip())
            missing_fields = [name for name, values in field_values.items() if not values]
            duplicate_fields = [name for name, values in field_values.items() if len(values) > 1]
            empty_fields = [
                name
                for name, values in field_values.items()
                if len(values) == 1 and not values[0]
            ]
            if missing_fields:
                raise ContractError(
                    f"design guideline section {slide_id} is missing fields: "
                    + ", ".join(missing_fields)
                )
            if duplicate_fields:
                raise ContractError(
                    f"design guideline section {slide_id} has duplicate fields: "
                    + ", ".join(duplicate_fields)
                )
            if empty_fields:
                raise ContractError(
                    f"design guideline section {slide_id} has empty fields: "
                    + ", ".join(empty_fields)
                )
            slide_evidence_ids = [
                evidence_id
                for evidence_id, evidence_slide_id, _ in evidence_items
                if evidence_slide_id == slide_id
            ]
            guideline_evidence_ids = re.findall(
                r"\[EVIDENCE:\s*(EVID-[A-Z0-9-]+)\s*\]",
                field_values["Evidence"][0],
            )
            if guideline_evidence_ids != slide_evidence_ids:
                raise ContractError(
                    f"design guideline section {slide_id} Evidence IDs do not match "
                    "the approved outline"
                )

        stage_dir = self.control_dir / "02_research"
        analysis_path = stage_dir / "analysis.md"
        guidelines_path = stage_dir / "design_guidelines.md"
        pdf_path = stage_dir / "analysis.pdf"
        _copy_file(analysis_markdown, analysis_path)
        _copy_file(design_guidelines, guidelines_path)
        render_markdown_pdf(analysis_path, pdf_path, _REPO_ROOT)
        try:
            analysis_pdf_pages = validate_pdf_report(pdf_path)
        except RuntimeError as exc:
            raise ContractError(str(exc)) from exc

        source_manifest_path = stage_dir / "source_manifest.json"
        questions_path = stage_dir / "questions.json"
        answer_receipts_path = stage_dir / "user_answers.json"
        intent_approval = self._verify_intent_approval()
        package_paths = [
            source_manifest_path,
            questions_path,
            answer_receipts_path,
            plan_path,
            analysis_path,
            pdf_path,
            guidelines_path,
        ]
        receipt = {
            "schema_version": "1.0.0",
            "stage": "research",
            "status": "COMPLETE",
            "completed_at": now_iso(),
            "upstream_intent_sha256": intent_approval["artifact_sha256"],
            "artifact_sha256": sha256_files(package_paths),
            "source_manifest_sha256": sha256_file(source_manifest_path),
            "questions_sha256": sha256_file(questions_path),
            "user_answers_sha256": sha256_file(answer_receipts_path),
            "research_plan_sha256": sha256_file(plan_path),
            "analysis_markdown_sha256": sha256_file(analysis_path),
            "analysis_pdf_sha256": sha256_file(pdf_path),
            "analysis_pdf_pages": analysis_pdf_pages,
            "design_guidelines_sha256": sha256_file(guidelines_path),
            "source_count": len(self._verify_sources()["sources"]),
            "next_agent": "presentation-designer",
        }
        write_json(stage_dir / "research_receipt.json", receipt)
        self._transition("DESIGN_READY", "research package completed and hash-bound")
        return receipt

    def _verify_research_receipt(self) -> dict[str, Any]:
        stage = self.control_dir / "02_research"
        receipt_path = stage / "research_receipt.json"
        if not receipt_path.is_file():
            raise ContractError("research receipt is required")
        receipt = read_json(receipt_path)
        intent = self._verify_intent_approval()
        if receipt.get("upstream_intent_sha256") != intent.get("artifact_sha256"):
            raise ContractError("research receipt is bound to stale intent")
        paths = [
            stage / "source_manifest.json",
            stage / "questions.json",
            stage / "user_answers.json",
            stage / "research_plan.md",
            stage / "analysis.md",
            stage / "analysis.pdf",
            stage / "design_guidelines.md",
        ]
        if not all(path.is_file() for path in paths):
            raise ContractError("research artifacts are missing")
        try:
            analysis_pdf_pages = validate_pdf_report(paths[5])
        except RuntimeError as exc:
            raise ContractError(str(exc)) from exc
        expected = {
            "artifact_sha256": sha256_files(paths),
            "source_manifest_sha256": sha256_file(paths[0]),
            "questions_sha256": sha256_file(paths[1]),
            "user_answers_sha256": sha256_file(paths[2]),
            "research_plan_sha256": sha256_file(paths[3]),
            "analysis_markdown_sha256": sha256_file(paths[4]),
            "analysis_pdf_sha256": sha256_file(paths[5]),
            "design_guidelines_sha256": sha256_file(paths[6]),
            "analysis_pdf_pages": analysis_pdf_pages,
        }
        for key, value in expected.items():
            if receipt.get(key) != value:
                raise ContractError(f"research receipt is stale: {key}")
        self._verify_question_answers("research")
        self._verify_sources()
        return receipt

    def reopen_research(self, note: str) -> dict[str, Any]:
        """Return a valid completed package to Agent 2 for review remediation."""
        self._require_supervisor_actor()
        self._require_status("DESIGN_READY")
        normalized_note = note.strip()
        if not normalized_note:
            raise ValueError("a non-empty research remediation note is required")
        if len(normalized_note) > 500:
            raise ValueError("research remediation note must be at most 500 characters")
        self._verify_research_receipt()
        self._transition(
            "RESEARCH_ACTIVE",
            f"research remediation reopened: {normalized_note}",
        )
        return self.state

    def _research_handoff_question_records(
        self,
    ) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
        approvals_dir = self.control_dir / "approvals"
        question_path = approvals_dir / "research_handoff_questions.json"
        answer_path = approvals_dir / "research_handoff_answers.json"
        questions = read_json(question_path) if question_path.is_file() else {"questions": []}
        answers = read_json(answer_path) if answer_path.is_file() else {"answers": []}
        return question_path, questions, answer_path, answers

    def _verify_research_handoff_answers(self) -> list[dict[str, Any]]:
        _, question_record, _, answer_record = self._research_handoff_question_records()
        questions = question_record.get("questions")
        answers = answer_record.get("answers")
        if not isinstance(questions, list) or not isinstance(answers, list):
            raise ContractError("research handoff question or answer ledger is invalid")
        if any(not isinstance(item, dict) for item in questions + answers):
            raise ContractError("research handoff ledger entries must be objects")
        question_by_id = {str(item.get("question_id") or ""): item for item in questions}
        answer_by_id = {str(item.get("question_id") or ""): item for item in answers}
        if len(question_by_id) != len(questions) or "" in question_by_id:
            raise ContractError("research handoff question IDs are missing or duplicated")
        if len(answer_by_id) != len(answers) or "" in answer_by_id:
            raise ContractError("research handoff answer IDs are missing or duplicated")
        if not set(answer_by_id).issubset(question_by_id):
            raise ContractError("research handoff answer refers to an unknown question")
        verified: list[dict[str, Any]] = []
        for question_id, question in question_by_id.items():
            receipt = answer_by_id.get(question_id)
            if question.get("status") == "open":
                if receipt is not None:
                    raise ContractError(
                        f"open research handoff question has an answer receipt: {question_id}"
                    )
                continue
            answer = question.get("answer")
            if question.get("status") != "answered" or not isinstance(answer, str) or receipt is None:
                raise ContractError(
                    f"research handoff question lacks a supervisor answer receipt: {question_id}"
                )
            if (
                receipt.get("actor") != "supervisor"
                or receipt.get("question_sha256") != self._question_sha256(question)
                or receipt.get("answer_sha256") != self._answer_sha256(answer)
            ):
                raise ContractError(
                    f"research handoff answer receipt is stale: {question_id}"
                )
            verified.append(question)
        return verified

    def request_research_handoff_approval(
        self,
        question: str,
        impact: str,
    ) -> dict[str, Any]:
        """Ask for user approval bound to the current completed research package."""
        self._require_supervisor_actor()
        self._require_status("DESIGN_READY")
        if not question.strip() or not impact.strip():
            raise ValueError("question and impact are required")
        research_receipt = self._verify_research_receipt()
        self._verify_research_handoff_answers()
        question_path, question_record, _, _ = self._research_handoff_question_records()
        questions = list(question_record.get("questions", []))
        if any(item.get("status") == "open" for item in questions):
            raise ContractError("open research handoff question must be answered")
        question_id = f"Q-RESEARCH-HANDOFF-{len(questions) + 1:03d}"
        item = {
            "question_id": question_id,
            "question": question.strip(),
            "impact": impact.strip(),
            "status": "open",
            "answer": None,
            "asked_at": now_iso(),
            "answered_at": None,
            "resume_status": "DESIGN_READY",
            "question_type": "research-handoff-confirmation",
            "subject_sha256": research_receipt["artifact_sha256"],
        }
        questions.append(item)
        state_path = self.control_dir / "state.json"
        state_before = self.state
        try:
            write_json(question_path, {"questions": questions})
            self._transition(
                "DESIGN_HANDOFF_WAITING_USER",
                f"research handoff question raised: {question_id}",
            )
        except Exception as exc:
            rollback_errors: list[str] = []
            for rollback_path, rollback_value in (
                (state_path, state_before),
                (question_path, question_record),
            ):
                try:
                    write_json(rollback_path, rollback_value)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{rollback_path.name}: {rollback_exc}")
            if rollback_errors:
                raise ContractError(
                    "research approval question transaction failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
        return item

    def answer_research_handoff_question(
        self,
        question_id: str,
        answer: str,
    ) -> dict[str, Any]:
        """Record the user's raw handoff decision in a supervisor-owned ledger."""
        self._require_supervisor_actor()
        self._require_status("DESIGN_HANDOFF_WAITING_USER")
        if not answer.strip():
            raise ValueError("answer is required")
        self._verify_research_handoff_answers()
        question_path, question_record, answer_path, answer_record = (
            self._research_handoff_question_records()
        )
        questions = list(question_record.get("questions", []))
        for index, item in enumerate(questions):
            if item.get("question_id") != question_id:
                continue
            if item.get("status") != "open":
                raise ContractError(f"research handoff question is not open: {question_id}")
            updated = dict(item)
            updated.update({
                "status": "answered",
                "answer": answer,
                "answered_at": now_iso(),
            })
            questions[index] = updated
            answers = list(answer_record.get("answers", []))
            answers.append({
                "schema_version": "1.0.0",
                "stage": "research-handoff",
                "actor": "supervisor",
                "question_id": question_id,
                "question_sha256": self._question_sha256(updated),
                "answer_sha256": self._answer_sha256(answer),
                "answered_at": updated["answered_at"],
            })
            state_path = self.control_dir / "state.json"
            state_before = self.state
            try:
                write_json(question_path, {"questions": questions})
                write_json(answer_path, {"answers": answers})
                self._transition("DESIGN_READY", f"research handoff question answered: {question_id}")
            except Exception as exc:
                rollback_errors: list[str] = []
                for rollback_path, rollback_value in (
                    (state_path, state_before),
                    (answer_path, answer_record),
                    (question_path, question_record),
                ):
                    try:
                        write_json(rollback_path, rollback_value)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"{rollback_path.name}: {rollback_exc}")
                if rollback_errors:
                    raise ContractError(
                        "research handoff answer transaction failed and rollback was incomplete: "
                        + "; ".join(rollback_errors)
                    ) from exc
                raise
            return updated
        raise KeyError(question_id)

    def approve_research_handoff(self, note: str, decision: str) -> dict[str, Any]:
        """Hash-bind an exact user APPROVE answer before Agent 3 activation."""
        self._require_supervisor_actor()
        self._require_status("DESIGN_READY")
        if decision != "APPROVE":
            raise ContractError("research handoff approval requires explicit decision APPROVE")
        if not note.strip():
            raise ValueError("a non-empty user approval note is required")
        research_receipt = self._verify_research_receipt()
        answered = self._verify_research_handoff_answers()
        _, question_record, _, _ = self._research_handoff_question_records()
        if any(
            item.get("status") == "open"
            for item in question_record.get("questions", [])
        ):
            raise ContractError("open research handoff questions must be answered")
        candidates = [
            item
            for item in answered
            if item.get("question_type") == "research-handoff-confirmation"
            and item.get("subject_sha256") == research_receipt["artifact_sha256"]
            and item.get("answer") == "APPROVE"
        ]
        if not candidates:
            raise ContractError(
                "research handoff approval requires an exact APPROVE answer bound to the current package"
            )
        confirmation = candidates[-1]
        receipt_path = self.control_dir / "02_research/research_receipt.json"
        bound_paths = self._research_handoff_bound_paths()
        bound_file_sha256 = {
            path.relative_to(self.workspace).as_posix(): sha256_file(path)
            for path in bound_paths
        }
        approval = {
            "schema_version": "1.0.0",
            "stage": "research-handoff",
            "status": "APPROVED",
            "decision": decision,
            "actor": "supervisor",
            "approved_at": now_iso(),
            "approval_note": note.strip(),
            "confirmation_question_id": confirmation["question_id"],
            "question_sha256": self._question_sha256(confirmation),
            "answer_sha256": self._answer_sha256("APPROVE"),
            "research_artifact_sha256": research_receipt["artifact_sha256"],
            "combined_artifact_sha256": research_receipt["artifact_sha256"],
            "research_receipt_sha256": sha256_file(receipt_path),
            "bound_file_sha256": bound_file_sha256,
            "next_agent": "presentation-designer",
        }
        approval_path = self.control_dir / "approvals/research_handoff_approval.json"
        write_json(approval_path, approval)
        self._transition(
            "DESIGN_AUTHORIZED",
            "user approved the hash-bound research package for design handoff",
            state_updates={
                "research_handoff_approval_sha256": sha256_file(approval_path),
            },
        )
        return approval

    def _research_handoff_bound_paths(self) -> list[Path]:
        stage = self.control_dir / "02_research"
        return [stage / name for name in (
            "research_plan.md", "source_manifest.json", "analysis.md", "analysis.pdf",
            "design_guidelines.md", "questions.json", "user_answers.json", "research_receipt.json",
        )]

    def _verify_research_handoff_approval(self) -> dict[str, Any]:
        approval_path = self.control_dir / "approvals/research_handoff_approval.json"
        if not approval_path.is_file():
            raise ContractError("research handoff approval is required before design")
        approval = read_json(approval_path)
        research_receipt = self._verify_research_receipt()
        receipt_path = self.control_dir / "02_research/research_receipt.json"
        bound_file_sha256 = approval.get("bound_file_sha256")
        expected_paths = {
            path.relative_to(self.workspace).as_posix()
            for path in self._research_handoff_bound_paths()
        }
        if not isinstance(bound_file_sha256, dict) or set(bound_file_sha256) != expected_paths:
            raise ContractError("research handoff approval has an invalid bound file set")
        bound_files_current: dict[str, str] = {}
        if isinstance(bound_file_sha256, dict):
            for relative_path in bound_file_sha256:
                path = self.workspace / str(relative_path)
                if (not path.is_file() or not path.resolve().is_relative_to(self.workspace)
                        or _path_has_symlink(path, self.workspace)):
                    raise ContractError("research handoff approval has an invalid bound file")
                bound_files_current[str(relative_path)] = sha256_file(path)
        answered = self._verify_research_handoff_answers()
        question_id = str(approval.get("confirmation_question_id") or "")
        confirmation = next(
            (
                item
                for item in answered
                if item.get("question_id") == question_id
                and item.get("question_type") == "research-handoff-confirmation"
            ),
            None,
        )
        if (
            approval.get("status") != "APPROVED"
            or approval.get("decision") != "APPROVE"
            or approval.get("actor") != "supervisor"
            or approval.get("research_artifact_sha256") != research_receipt["artifact_sha256"]
            or approval.get("combined_artifact_sha256") != research_receipt["artifact_sha256"]
            or approval.get("research_receipt_sha256") != sha256_file(receipt_path)
            or self.state.get("research_handoff_approval_sha256") != sha256_file(approval_path)
            or len(bound_files_current) != 8
            or bound_file_sha256 != bound_files_current
            or confirmation is None
            or confirmation.get("subject_sha256") != research_receipt["artifact_sha256"]
            or confirmation.get("answer") != "APPROVE"
            or approval.get("question_sha256") != self._question_sha256(confirmation)
            or approval.get("answer_sha256") != self._answer_sha256("APPROVE")
        ):
            raise ContractError("research handoff approval is stale")
        return approval

    def prepare_design(self) -> Path:
        """Materialize Agent 3's SlideMaster handoff and source mirrors."""
        if self.state["status"] == "DESIGN_READY":
            raise ContractError("research handoff approval is required before design")
        self._require_status("DESIGN_AUTHORIZED")
        intent_approval = self._verify_intent_approval()
        research_receipt = self._verify_research_receipt()
        research_handoff_approval = self._verify_research_handoff_approval()
        intent_dir = self.control_dir / "01_intent"
        research_dir = self.control_dir / "02_research"
        mirrors = {
            intent_dir / "presentation_blueprint.md": self.workspace / "sources/01_approved_presentation_blueprint.md",
            research_dir / "analysis.md": self.workspace / "sources/02_research_analysis.md",
            research_dir / "design_guidelines.md": self.workspace / "sources/03_design_guidelines.md",
        }
        for source, destination in mirrors.items():
            _copy_file(source, destination)

        manifest = read_json(research_dir / "source_manifest.json")
        manifest_lines = [
            "# Research Source Manifest",
            "",
            "| Source ID | Type | Title | Local path | URL | License |",
            "|---|---|---|---|---|---|",
        ]
        for source in manifest["sources"]:
            manifest_lines.append(
                f"| `{source['source_id']}` | {source['resource_type']} | {source['title']} | "
                f"`{source['local_path']}` | {source['source_url']} | {source['license_status']} |"
            )
        (self.workspace / "sources/04_research_source_manifest.md").write_text(
            "\n".join(manifest_lines) + "\n",
            encoding="utf-8",
        )

        handoff = self.control_dir / "03_design/SLIDEMASTER_HANDOFF.md"
        handoff.write_text(
            "# SlideMaster Design Handoff\n\n"
            "## Status\n\n"
            "- Route intent: new SVG-authored presentation from approved source materials\n"
            "- Current agent: `presentation-designer`\n"
            f"- Approved intent SHA-256: `{intent_approval['artifact_sha256']}`\n"
            f"- Research package SHA-256: `{research_receipt['artifact_sha256']}`\n"
            f"- User-approved research handoff SHA-256: `{research_handoff_approval['research_artifact_sha256']}`\n"
            f"- Analysis PDF SHA-256: `{research_receipt['analysis_pdf_sha256']}`\n\n"
            "## Mandatory reading order\n\n"
            "1. `.claude/skills/ppt-master/workflows/routing.md`\n"
            "2. `agent_pipeline/01_intent/presentation_blueprint.md`\n"
            "3. `agent_pipeline/02_research/analysis.pdf` and `analysis.md`\n"
            "4. `agent_pipeline/02_research/source_manifest.json`\n"
            "5. `agent_pipeline/02_research/design_guidelines.md`\n"
            "6. The execution owner selected by routing; for this source-rework handoff, main `.claude/skills/ppt-master/SKILL.md`\n\n"
            "## Ownership boundary\n\n"
            "- Preserve approved audience, decision, message, page order, evidence, and caveats.\n"
            "- Use SlideMaster confirmation for visual direction and template choices.\n"
            "- Keep design artifacts at this workspace root.\n"
            "- Do not declare completion until `validate_spec.py`, SVG gates, export, and `verify_deck.py` pass.\n\n"
            "## Expected outputs\n\n"
            "- `design_spec.md`\n"
            "- `spec_lock.md`\n"
            "- `svg_output/*.svg`\n"
            "- `exports/*.pptx`\n"
            "- `agent_pipeline/03_design/design_approval.json`\n"
            "- `agent_pipeline/03_design/verification_candidate.json`\n"
            "- `agent_pipeline/03_design/verification.json`\n",
            encoding="utf-8",
        )
        self._transition("DESIGN_ACTIVE", "SlideMaster design handoff prepared")
        return handoff

    def _design_handoff_paths(self) -> list[Path]:
        return [
            self.control_dir / "03_design/SLIDEMASTER_HANDOFF.md",
            self.workspace / "sources/01_approved_presentation_blueprint.md",
            self.workspace / "sources/02_research_analysis.md",
            self.workspace / "sources/03_design_guidelines.md",
            self.workspace / "sources/04_research_source_manifest.md",
        ]

    def approve_design(self, note: str, decision: str) -> dict[str, Any]:
        """Bind explicit user direction approval to the design specification."""
        self._require_supervisor_actor()
        self._verify_research_handoff_approval()
        self._require_status("DESIGN_ACTIVE")
        if decision != "APPROVE":
            raise ContractError("design approval requires explicit decision APPROVE")
        if not note.strip():
            raise ValueError("a non-empty user design approval note is required")
        design_spec = self.workspace / "design_spec.md"
        spec_lock = self.workspace / "spec_lock.md"
        if not design_spec.is_file():
            raise ContractError("design_spec.md is required before design approval")
        if not spec_lock.is_file():
            raise ContractError("spec_lock.md is required before design approval")
        handoff_paths = self._design_handoff_paths()
        if not all(path.is_file() for path in handoff_paths):
            raise ContractError("SlideMaster handoff or mirrored design sources are missing")
        research_receipt = self._verify_research_receipt()
        approval = {
            "schema_version": "1.0.0",
            "stage": "design-direction",
            "status": "APPROVED",
            "decision": decision,
            "approved_at": now_iso(),
            "approval_note": note.strip(),
            "upstream_research_sha256": research_receipt["artifact_sha256"],
            "design_spec_sha256": sha256_file(design_spec),
            "spec_lock_sha256": sha256_file(spec_lock),
            "design_handoff_sha256": sha256_files(handoff_paths),
        }
        write_json(self.control_dir / "03_design/design_approval.json", approval)
        self._transition("DESIGN_APPROVED", "user approved design direction and spec lock")
        return approval

    def _verify_design_approval(self) -> dict[str, Any]:
        """Verify that the approved design direction is current."""
        self._verify_research_handoff_approval()
        path = self.control_dir / "03_design/design_approval.json"
        if not path.is_file():
            raise ContractError("design direction approval is missing")
        approval = read_json(path)
        if (
            approval.get("status") != "APPROVED"
            or approval.get("decision") != "APPROVE"
            or not str(approval.get("approval_note") or "").strip()
        ):
            raise ContractError("design direction approval is invalid")
        research_receipt = self._verify_research_receipt()
        if approval.get("upstream_research_sha256") != research_receipt.get("artifact_sha256"):
            raise ContractError("design direction approval is bound to stale research")
        for key, path_obj in (
            ("design_spec_sha256", self.workspace / "design_spec.md"),
            ("spec_lock_sha256", self.workspace / "spec_lock.md"),
        ):
            if not path_obj.is_file() or approval.get(key) != sha256_file(path_obj):
                raise ContractError(f"design direction approval is stale: {key}")
        handoff_paths = self._design_handoff_paths()
        if not all(path.is_file() for path in handoff_paths):
            raise ContractError("SlideMaster handoff or mirrored design sources are missing")
        if approval.get("design_handoff_sha256") != sha256_files(handoff_paths):
            raise ContractError("design direction approval is stale: design_handoff_sha256")
        return approval

    def _select_final_pptx(self) -> Path:
        """Select the newest native export under the canonical exports folder."""
        pptx_files = [
            path
            for path in [
                *self.workspace.glob("*.pptx"),
                *(self.workspace / "exports").glob("*.pptx"),
            ]
            if not path.stem.endswith("_svg")
        ]
        if not pptx_files:
            raise ContractError("workspace must contain a native PPTX (not an *_svg intermediate)")
        newest_mtime = max(path.stat().st_mtime for path in pptx_files)
        newest = [path for path in pptx_files if path.stat().st_mtime == newest_mtime]
        if len(newest) != 1:
            names = ", ".join(sorted(path.name for path in newest))
            raise ContractError(f"ambiguous newest native exports: {names}")
        pptx_path = newest[0]
        if _path_has_symlink(pptx_path, self.workspace):
            raise ContractError("final PPTX path contains a symlink")
        if pptx_path.parent != self.workspace / "exports":
            raise ContractError(
                f"a root-level PPTX shadows the final export selected by verify_deck.py: {pptx_path.name}"
            )
        if pptx_path.suffix != ".pptx" or pptx_path.stem.endswith("_svg"):
            raise ContractError("final PPTX path is not a canonical native export")
        return pptx_path.resolve()

    @staticmethod
    def _validate_native_pptx(pptx_path: Path) -> None:
        """Validate the selected PPTX as a bounded native Open XML package."""
        try:
            with zipfile.ZipFile(pptx_path) as archive:
                members = archive.infolist()
                if len(members) > _MAX_PPTX_MEMBERS:
                    raise ContractError("final PPTX contains too many ZIP members")
                total_uncompressed = sum(member.file_size for member in members)
                if total_uncompressed > _MAX_PPTX_UNCOMPRESSED_BYTES:
                    raise ContractError("final PPTX uncompressed size exceeds the safety limit")
                suspicious = [
                    member.filename
                    for member in members
                    if member.file_size > 0
                    and member.file_size / max(member.compress_size, 1)
                    > _MAX_PPTX_COMPRESSION_RATIO
                ]
                if suspicious:
                    raise ContractError(
                        "final PPTX contains a suspicious compression ratio: " + suspicious[0]
                    )
                bad_member = archive.testzip()
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise ContractError("final PPTX is not a valid ZIP package") from exc
        if bad_member is not None:
            raise ContractError(f"final PPTX contains a corrupt member: {bad_member}")
        required_members = {"[Content_Types].xml", "ppt/presentation.xml"}
        if not required_members.issubset(names):
            raise ContractError("final PPTX is missing required Open XML parts")

    def verify_design(self) -> dict[str, Any]:
        """Run SlideMaster verification and produce a review candidate."""
        if self.state["status"] == "DESIGN_ACTIVE":
            raise ContractError("design direction approval is required before verification")
        self._require_status("DESIGN_APPROVED")
        self._verify_intent_approval()
        research_receipt = self._verify_research_receipt()
        self._verify_design_approval()
        design_spec = self.workspace / "design_spec.md"
        spec_lock = self.workspace / "spec_lock.md"
        if not design_spec.is_file():
            raise ContractError("design_spec.md is required before design completion")
        if not spec_lock.is_file():
            raise ContractError("spec_lock.md is required before design completion")
        svg_files = sorted((self.workspace / "svg_output").glob("*.svg"))
        if not svg_files:
            raise ContractError("svg_output must contain authored slide SVGs")
        pptx_path = self._select_final_pptx()
        self._validate_native_pptx(pptx_path)
        pptx_sha256_before = sha256_file(pptx_path)
        verify_script = _REPO_ROOT / ".claude/skills/ppt-master/scripts/verify_deck.py"
        if not verify_script.is_file():
            raise ContractError(f"SlideMaster verifier is missing: {verify_script}")
        contact_sheet = self.workspace / "_pptx_render" / f"{pptx_path.stem}-grid.png"
        if _path_has_symlink(contact_sheet.parent, self.workspace):
            raise ContractError("contact-sheet output folder contains a symlink")
        if contact_sheet.exists() or contact_sheet.is_symlink():
            contact_sheet.unlink()
        officecli_bin = _officecli_bin()
        verification_started_ns = time.time_ns()
        command = [
            sys.executable,
            str(verify_script),
            str(self.workspace),
            "--require-render-success",
        ]
        try:
            result = _run_bounded_subprocess(
                command,
                cwd=_REPO_ROOT,
                timeout=_VERIFY_DECK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ContractError(
                f"verify_deck.py timed out after {_VERIFY_DECK_TIMEOUT_SECONDS} seconds"
            ) from exc
        except OSError as exc:
            raise ContractError(f"verify_deck.py could not run: {exc}") from exc
        log_path = self.control_dir / "logs/verify_deck.log"
        if _path_has_symlink(log_path, self.workspace):
            raise ContractError("verification log path contains a symlink")
        log_path.write_text(
            "$ " + " ".join(command) + "\n\n[stdout]\n" + result.stdout
            + "\n[stderr]\n" + result.stderr,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise ContractError(
                f"verify_deck.py failed with exit {result.returncode}; see {log_path}"
            )
        try:
            selected_after = self._select_final_pptx()
            pptx_sha256_after = sha256_file(selected_after)
        except (ContractError, OSError):
            contact_sheet.unlink(missing_ok=True)
            raise
        if selected_after != pptx_path or pptx_sha256_after != pptx_sha256_before:
            contact_sheet.unlink(missing_ok=True)
            raise ContractError("native PPTX changed during verification")
        quicklook_fallback = False
        fallback_diagnostics: list[str] = []
        render_backend = "officecli"
        if contact_sheet.is_file() and officecli_bin is None:
            contact_sheet.unlink()
            raise ContractError(
                f"verify_deck.py produced a contact sheet without OfficeCLI: {pptx_path.name}"
            )
        if not contact_sheet.is_file():
            if officecli_bin is not None:
                raise ContractError(
                    f"OfficeCLI was available but did not render the selected PPTX: {pptx_path.name}"
                )
            quicklook_fallback = _render_macos_quicklook_contact_sheet(
                pptx_path,
                contact_sheet,
                diagnostics=fallback_diagnostics,
            )
            if quicklook_fallback:
                render_backend = "macos-quicklook-webkit"
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\nmacOS Quick Look/WebKit contact-sheet fallback: "
                    + ("PASS" if quicklook_fallback else "UNAVAILABLE_OR_FAILED")
                    + "\n"
                )
                for diagnostic in fallback_diagnostics:
                    handle.write(f"- {diagnostic}\n")
        if not contact_sheet.is_file():
            raise ContractError(
                f"verify_deck.py passed but did not render the selected PPTX: {pptx_path.name}"
            )
        if _path_has_symlink(contact_sheet, self.workspace):
            raise ContractError("contact-sheet path contains a symlink")
        if contact_sheet.stat().st_ctime_ns < verification_started_ns:
            raise ContractError("verify_deck.py did not recreate the contact sheet during this run")
        _validate_png_path(contact_sheet)
        try:
            selected_final = self._select_final_pptx()
            pptx_sha256_final = sha256_file(selected_final)
        except (ContractError, OSError):
            contact_sheet.unlink(missing_ok=True)
            raise
        if selected_final != pptx_path or pptx_sha256_final != pptx_sha256_before:
            contact_sheet.unlink(missing_ok=True)
            raise ContractError("native PPTX changed during verification")
        candidate = {
            "schema_version": "1.0.0",
            "stage": "design",
            "status": "AWAITING_CONTACT_SHEET_REVIEW",
            "verified_at": now_iso(),
            "route": _CANONICAL_DESIGN_ROUTE,
            "owner": _CANONICAL_DESIGN_OWNER,
            "upstream_research_sha256": research_receipt["artifact_sha256"],
            "design_approval_sha256": sha256_file(
                self.control_dir / "03_design/design_approval.json"
            ),
            "design_spec_sha256": sha256_file(design_spec),
            "spec_lock_sha256": sha256_file(spec_lock),
            "svg_count": len(svg_files),
            "svg_bundle_sha256": sha256_files(svg_files),
            "pptx_path": relative_to_workspace(pptx_path, self.workspace),
            "pptx_sha256": pptx_sha256_before,
            "verify_deck_exit_code": result.returncode,
            "render_backend": render_backend,
            "verification_log": relative_to_workspace(log_path, self.workspace),
            "verification_log_sha256": sha256_file(log_path),
            "contact_sheet": relative_to_workspace(contact_sheet, self.workspace),
            "contact_sheet_sha256": sha256_file(contact_sheet),
        }
        candidate_path = self.control_dir / "03_design/verification_candidate.json"
        write_json(candidate_path, candidate)
        candidate_sha256 = sha256_file(candidate_path)
        self._transition(
            "DESIGN_REVIEW",
            "SlideMaster verification passed; contact sheet review required",
            state_updates={"verification_candidate_sha256": candidate_sha256},
        )
        return candidate

    def _verify_design_candidate(
        self,
        *,
        allow_legacy_backend: bool = False,
    ) -> dict[str, Any]:
        candidate_path = self.control_dir / "03_design/verification_candidate.json"
        if not candidate_path.is_file():
            raise ContractError("design verification candidate is missing")
        if _path_has_symlink(candidate_path, self.workspace):
            raise ContractError("design verification candidate path contains a symlink")
        locked_candidate_sha256 = str(
            self.state.get("verification_candidate_sha256") or ""
        )
        if (
            not re.fullmatch(r"[0-9a-f]{64}", locked_candidate_sha256)
            or locked_candidate_sha256 != sha256_file(candidate_path)
        ):
            raise ContractError("design verification candidate hash is stale")
        candidate = read_json(candidate_path)
        fixed_values = {
            "schema_version": "1.0.0",
            "stage": "design",
            "status": "AWAITING_CONTACT_SHEET_REVIEW",
            "route": _CANONICAL_DESIGN_ROUTE,
            "owner": _CANONICAL_DESIGN_OWNER,
            "verify_deck_exit_code": 0,
        }
        for key, expected in fixed_values.items():
            if candidate.get(key) != expected:
                raise ContractError(f"design verification candidate has invalid {key}")
        backend = candidate.get("render_backend")
        if backend not in {"officecli", "macos-quicklook-webkit"} and not (
            allow_legacy_backend and backend is None
        ):
            raise ContractError("design verification candidate has invalid render_backend")

        research_receipt = self._verify_research_receipt()
        self._verify_design_approval()
        if candidate.get("upstream_research_sha256") != research_receipt.get("artifact_sha256"):
            raise ContractError("design verification candidate is bound to stale research")
        expected_design_approval_sha = sha256_file(
            self.control_dir / "03_design/design_approval.json"
        )
        if candidate.get("design_approval_sha256") != expected_design_approval_sha:
            raise ContractError("design verification candidate is bound to stale design approval")

        design_spec = self.workspace / "design_spec.md"
        spec_lock = self.workspace / "spec_lock.md"
        svg_files = sorted((self.workspace / "svg_output").glob("*.svg"))
        checks = {
            "design_spec_sha256": sha256_file(design_spec) if design_spec.is_file() else None,
            "spec_lock_sha256": sha256_file(spec_lock) if spec_lock.is_file() else None,
            "svg_count": len(svg_files),
            "svg_bundle_sha256": sha256_files(svg_files) if svg_files else None,
        }
        for key, actual in checks.items():
            if candidate.get(key) != actual:
                raise ContractError(f"design verification candidate is stale: {key}")

        pptx_path = self._select_final_pptx()
        self._validate_native_pptx(pptx_path)
        log_path = self.control_dir / "logs/verify_deck.log"
        contact_sheet = self.workspace / "_pptx_render" / f"{pptx_path.stem}-grid.png"
        artifacts = {
            "pptx_path": (pptx_path, "pptx_sha256"),
            "contact_sheet": (contact_sheet, "contact_sheet_sha256"),
            "verification_log": (log_path, "verification_log_sha256"),
        }
        for key, (artifact, hash_key) in artifacts.items():
            expected_path = artifact.relative_to(self.workspace).as_posix()
            if candidate.get(key) != expected_path:
                raise ContractError(f"design verification candidate has non-canonical {key}")
            if _path_has_symlink(artifact, self.workspace):
                raise ContractError(f"design artifact path contains a symlink: {key}")
            if not artifact.is_file():
                raise ContractError(f"design artifact is missing: {key}")
            if candidate.get(hash_key) != sha256_file(artifact):
                raise ContractError(f"design verification candidate is stale: {key}")
        _validate_png_path(contact_sheet)
        return candidate

    def complete_design(
        self,
        *,
        contact_sheet_review: str,
        review_note: str,
    ) -> dict[str, Any]:
        """Record the post-render visual review and lock the final receipt."""
        self._require_supervisor_actor()
        self._require_status("DESIGN_REVIEW")
        if contact_sheet_review == "FAIL":
            return self.request_design_changes(review_note)
        if contact_sheet_review != "PASS" or len(review_note.strip()) < 10:
            raise ContractError(
                "contact sheet review PASS and a review note of at least 10 characters are required"
            )
        candidate = self._verify_design_candidate()
        candidate_sha256 = str(self.state["verification_candidate_sha256"])
        receipt = {
            **candidate,
            "status": "COMPLETE",
            "completed_at": now_iso(),
            "verification_candidate_sha256": candidate_sha256,
            "contact_sheet_review": contact_sheet_review,
            "contact_sheet_review_note": review_note.strip(),
        }
        receipt_path = self.control_dir / "03_design/verification.json"
        state_path = self.control_dir / "state.json"
        state_before = self.state
        receipt_existed = receipt_path.is_file()
        receipt_before = receipt_path.read_bytes() if receipt_existed else None
        try:
            write_json(receipt_path, receipt)
            self._transition("COMPLETE", "contact sheet reviewed; final deck hash-bound")
        except Exception as exc:
            rollback_errors: list[str] = []
            try:
                write_json(state_path, state_before)
            except Exception as rollback_exc:
                rollback_errors.append(f"state.json: {rollback_exc}")
            try:
                if receipt_existed:
                    if receipt_before is None:
                        raise RuntimeError("missing prior verification receipt bytes")
                    receipt_path.write_bytes(receipt_before)
                else:
                    receipt_path.unlink(missing_ok=True)
            except Exception as rollback_exc:
                rollback_errors.append(f"verification.json: {rollback_exc}")
            if rollback_errors:
                raise ContractError(
                    "complete-design transaction failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
        return receipt

    def _verify_design_receipt(self, *, allow_legacy_backend: bool = False) -> None:
        path = self.control_dir / "03_design/verification.json"
        if not path.is_file():
            raise ContractError("design verification receipt is missing")
        receipt = read_json(path)
        if (
            receipt.get("verify_deck_exit_code") != 0
            or receipt.get("contact_sheet_review") != "PASS"
            or len(str(receipt.get("contact_sheet_review_note") or "").strip()) < 10
        ):
            raise ContractError("design verification receipt is not passing")
        candidate = self._verify_design_candidate(
            allow_legacy_backend=allow_legacy_backend
        )
        locked_candidate_sha256 = str(self.state.get("verification_candidate_sha256") or "")
        if receipt.get("verification_candidate_sha256") != locked_candidate_sha256:
            raise ContractError(
                "design verification receipt is stale: verification_candidate_sha256"
            )
        for key in (
            "route",
            "owner",
            "upstream_research_sha256",
            "design_approval_sha256",
            "design_spec_sha256",
            "spec_lock_sha256",
            "svg_count",
            "svg_bundle_sha256",
            "pptx_path",
            "pptx_sha256",
            "render_backend",
            "contact_sheet",
            "contact_sheet_sha256",
            "verification_log",
            "verification_log_sha256",
        ):
            if receipt.get(key) != candidate.get(key):
                raise ContractError(f"design verification receipt is stale: {key}")
        for key in ("pptx_path", "contact_sheet"):
            artifact = (self.workspace / str(receipt.get(key) or "")).resolve()
            try:
                artifact.relative_to(self.workspace)
            except ValueError as exc:
                raise ContractError(f"design artifact escapes workspace: {key}") from exc
            if not artifact.is_file():
                raise ContractError(f"design artifact is missing: {key}")
            expected = receipt.get("pptx_sha256" if key == "pptx_path" else "contact_sheet_sha256")
            if expected != sha256_file(artifact):
                raise ContractError(f"design artifact hash is stale: {key}")

    def reopen_design(self, note: str) -> dict[str, Any]:
        """Preserve a completed candidate, including drift, and reopen remediation."""
        self._require_supervisor_actor()
        self._require_status("COMPLETE")
        return self._archive_design_for_rework(note, "REOPEN")

    def request_design_changes(self, note: str) -> dict[str, Any]:
        """Reject the current review candidate without recording a passing review."""
        self._require_supervisor_actor()
        self._require_status("DESIGN_REVIEW")
        return self._archive_design_for_rework(note, "REQUEST_CHANGES")

    def _archive_design_for_rework(self, note: str, decision: str) -> dict[str, Any]:
        normalized_note = note.strip()
        if len(normalized_note) < 10:
            raise ValueError("design remediation note must be at least 10 characters")
        if len(normalized_note) > 500:
            raise ValueError("design remediation note must be at most 500 characters")
        prior_findings: list[str] = []
        try:
            self._verify_design_approval()
            resume_status = "DESIGN_APPROVED"
        except (ContractError, OSError, ValueError, KeyError) as exc:
            prior_findings.append(str(exc))
            resume_status = "DESIGN_ACTIVE"
        try:
            if self.state["status"] == "COMPLETE":
                self._verify_design_receipt(allow_legacy_backend=True)
            else:
                self._verify_design_candidate()
        except (ContractError, OSError, ValueError, KeyError) as exc:
            prior_findings.append(str(exc))

        stage = self.control_dir / "03_design"
        candidate_path = stage / "verification_candidate.json"
        receipt_path = stage / "verification.json"
        for path in (candidate_path, receipt_path):
            if _path_has_symlink(path, self.workspace):
                raise ContractError(f"design verification archive input contains a symlink: {path.name}")
            if path.exists() and not path.is_file():
                raise ContractError(f"design verification archive input is not a file: {path.name}")
        candidate_bytes = candidate_path.read_bytes() if candidate_path.is_file() else None
        receipt_bytes = receipt_path.read_bytes() if receipt_path.is_file() else None
        candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest() if candidate_bytes is not None else None
        receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest() if receipt_bytes is not None else None
        try:
            receipt = read_json(receipt_path) if receipt_bytes is not None else {}
        except (ValueError, UnicodeError):
            receipt = {}

        state_path = self.control_dir / "state.json"
        state_bytes = state_path.read_bytes()

        history_root = stage / "verification_history"
        history_root_existed = history_root.exists()
        archive_key = receipt_sha256 or candidate_sha256 or hashlib.sha256(state_bytes).hexdigest()
        archive_dir = history_root / archive_key
        if _path_has_symlink(archive_dir, self.workspace):
            raise ContractError("design verification history path contains a symlink")
        if archive_dir.exists():
            # Repeated reviews can reject byte-identical candidates. Preserve
            # each decision without replacing the earlier evidence archive.
            archive_dir = history_root / f"{archive_key}-r{self.state['revision']}"
            if _path_has_symlink(archive_dir, self.workspace):
                raise ContractError("design verification history path contains a symlink")
            if archive_dir.exists():
                raise ContractError("design verification decision was already archived")

        try:
            archive_dir.mkdir(parents=True)
            if candidate_bytes is not None:
                (archive_dir / "verification_candidate.json").write_bytes(candidate_bytes)
            if receipt_bytes is not None:
                (archive_dir / "verification.json").write_bytes(receipt_bytes)
            write_json(
                archive_dir / "reopen.json",
                {
                    "schema_version": "1.0.0",
                    "stage": "design-remediation",
                    "reopened_at": now_iso(),
                    "note": normalized_note,
                    "decision": decision,
                    "prior_status": self.state["status"],
                    "resume_status": resume_status,
                    "prior_findings": list(dict.fromkeys(prior_findings)),
                    "prior_candidate_sha256": candidate_sha256,
                    "prior_verification_sha256": receipt_sha256,
                    "pptx_sha256": receipt.get("pptx_sha256"),
                    "render_backend": receipt.get("render_backend"),
                },
            )
            candidate_path.unlink(missing_ok=True)
            receipt_path.unlink(missing_ok=True)
            self._transition(
                resume_status,
                f"design verification remediation reopened: {normalized_note}",
                state_updates={"verification_candidate_sha256": None},
            )
        except Exception as exc:
            rollback_errors: list[str] = []
            for path, payload in (
                (state_path, state_bytes),
                (candidate_path, candidate_bytes),
                (receipt_path, receipt_bytes),
            ):
                try:
                    if payload is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(payload)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{path.name}: {rollback_exc}")
            try:
                if archive_dir.exists():
                    shutil.rmtree(archive_dir)
                if not history_root_existed and history_root.exists():
                    history_root.rmdir()
            except Exception as rollback_exc:
                rollback_errors.append(f"verification_history: {rollback_exc}")
            if rollback_errors:
                raise ContractError(
                    "reopen-design transaction failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
        return {
            "status": self.state["status"],
            "archive": relative_to_workspace(archive_dir, self.workspace),
            "prior_verification_sha256": receipt_sha256,
            "decision": decision,
            "prior_findings": list(dict.fromkeys(prior_findings)),
        }

    def _state_findings(self) -> list[str]:
        """Check state revision and transition history consistency."""
        findings: list[str] = []
        state = self.state
        history = state.get("history")
        if not isinstance(history, list) or not history:
            return ["state history is missing"]
        if state.get("revision") != len(history):
            findings.append("state revision does not match history length")
        previous_to: object = None
        for index, raw_entry in enumerate(history):
            if not isinstance(raw_entry, dict):
                findings.append(f"state history entry {index + 1} is invalid")
                continue
            source = raw_entry.get("from")
            target = raw_entry.get("to")
            if source != previous_to:
                findings.append(f"state history chain breaks at entry {index + 1}")
            if index == 0:
                if source is not None or target != "INTENT_INTERVIEW":
                    findings.append("state history has an invalid initial transition")
            elif target not in _ALLOWED_TRANSITIONS.get(str(source), set()):
                findings.append(f"state history contains invalid transition: {source} -> {target}")
            previous_to = target
        if state.get("status") != previous_to:
            findings.append("state status does not match history tail")
        return findings

    def validate(self) -> list[str]:
        """Return current workspace contract findings."""
        findings = validate_agent_manifests(_SUITE_ROOT / "agents")
        findings.extend(self._state_findings())
        for stage in ("intent", "research"):
            try:
                self._verify_question_answers(stage)
            except (ContractError, OSError, ValueError, KeyError) as exc:
                findings.append(str(exc))
        state = self.state
        status = str(state.get("status") or "")
        if status not in _STATUS_AGENT:
            findings.append(f"unknown status: {status}")
            return findings
        for relative, verifier, required_states in _UPSTREAM_GATE_REGISTRY:
            if status in required_states or (self.control_dir / relative).exists():
                try:
                    getattr(self, verifier)()
                except (ContractError, OSError, ValueError, KeyError) as exc:
                    findings.append(f"{relative}: {exc}")
        handoff_questions = self.control_dir / "approvals/research_handoff_questions.json"
        if handoff_questions.exists() or status == "DESIGN_HANDOFF_WAITING_USER":
            try:
                self._verify_research_handoff_answers()
            except (ContractError, OSError, ValueError, KeyError) as exc:
                findings.append(f"research handoff questions: {exc}")
        if status in {"DESIGN_ACTIVE", "DESIGN_APPROVED", "DESIGN_REVIEW", "COMPLETE"}:
            if not (self.control_dir / "03_design/SLIDEMASTER_HANDOFF.md").is_file():
                findings.append("SlideMaster design handoff is missing")
        candidate_path = self.control_dir / "03_design/verification_candidate.json"
        if status in {"DESIGN_REVIEW", "COMPLETE"} or candidate_path.exists():
            try:
                self._verify_design_candidate()
            except (ContractError, OSError, ValueError, KeyError) as exc:
                findings.append(str(exc))
        final_receipt_path = self.control_dir / "03_design/verification.json"
        if final_receipt_path.exists() and status != "COMPLETE":
            findings.append("design verification receipt exists outside COMPLETE state")
        if status == "COMPLETE" or final_receipt_path.exists():
            try:
                self._verify_design_receipt()
            except (ContractError, OSError, ValueError, KeyError) as exc:
                findings.append(str(exc))
        return list(dict.fromkeys(findings))
