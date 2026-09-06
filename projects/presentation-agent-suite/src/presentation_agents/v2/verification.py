"""Trusted G4 verification: worker declarations never constitute a PASS.

The service supplies the approved contracts and a private attempt workspace.
Only the existing route owner verifies and renders; this adapter validates the
selected candidate, references and input hashes before adopting its receipt.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import posixpath
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable
from xml.etree import ElementTree as ET

from presentation_agents import pipeline as legacy
from presentation_agents import utils as legacy_utils

from . import contracts
from .contracts import ROUTES, ContractError, digest, file_hash, now, safe_path

_MAX_XML_BYTES = 16 * 1024 * 1024
_MAX_PPTX_BYTES = 256 * 1024 * 1024
_EVIDENCE = re.compile(r"\bEVID-[A-Za-z0-9_-]+\b")
_CLAIM = re.compile(r"\[CLAIM:\s*([A-Za-z0-9_.:-]+)\s*\]", re.IGNORECASE)
_CLAIM_TOKEN = re.compile(r"\b(?:claim|CLAIM)-[A-Za-z0-9_-]+\b")
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class VerificationBlocked(ContractError):
    """The selected owner or real renderer is unavailable; never a PASS."""


class VerificationCancelled(ContractError):
    """The service cancelled G4; no receipt may be adopted."""


def renderer_contract(repo_root: Path) -> dict:
    """Declare service prerequisites without pretending a candidate was rendered."""
    renderer = None
    if legacy._officecli_bin():
        renderer = "officecli"
    elif (sys.platform == "darwin" and shutil.which("qlmanage") and shutil.which("swift")
          and (repo_root / "projects/presentation-agent-suite/scripts/render_quicklook_contact_sheet.swift").is_file()):
        renderer = "macos-quicklook-webkit"
    return {
        "schema_version": "service-verification.v1", "owner": "service", "gate": "G4",
        "status": "PREREQUISITES_PRESENT" if renderer else "UNAVAILABLE",
        "renderer": renderer, "verification_status": "UNVERIFIED", "actual_render_required": True,
        "worker_responsibility": "owner preflight, exact specs, sequential SVG/notes, source checks and ordered PPTX export",
        "service_responsibility": "shared owner verifier, package/reference checks and a fresh render of the submitted PPTX",
        "boundary": "Binary presence is not a render PASS. G4 requires actual candidate verification; G5 requires independent visual review.",
    }


def _require_path(workspace: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise ContractError("candidate file paths must be workspace-relative strings")
    return safe_path(workspace, relative)


def _read_text(path: Path) -> str:
    if path.stat().st_size > _MAX_XML_BYTES:
        raise ContractError(f"text artifact exceeds verification limit: {path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ContractError(f"text artifact is not UTF-8: {path.name}") from exc


def _check_references(text: str, evidence: set[str], claims: set[str], label: str) -> None:
    unknown_evidence = set(_EVIDENCE.findall(text)) - evidence
    unknown_claims = (set(_CLAIM.findall(text)) | set(_CLAIM_TOKEN.findall(text))) - claims
    if unknown_evidence or unknown_claims:
        raise ContractError(
            f"undefined evidence/claim references in {label}: "
            + ", ".join(sorted(unknown_evidence | unknown_claims))
        )


def _reference_sets(text: str) -> dict:
    return {"evidence": sorted(set(_EVIDENCE.findall(text))),
            "claims": sorted(set(_CLAIM.findall(text)) | set(_CLAIM_TOKEN.findall(text)))}


def _document_text(document: ET.Element) -> str:
    paragraphs = [element for element in document.iter() if element.tag.rsplit("}", 1)[-1] == "p"]
    return "\n".join("".join(paragraph.itertext()) for paragraph in paragraphs) if paragraphs else "\n".join(document.itertext())


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    member = archive.getinfo(name)
    if member.file_size > _MAX_XML_BYTES:
        raise ContractError(f"PPTX XML part exceeds verification limit: {name}")
    data = archive.read(name)
    declaration = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declaration or b"<!ENTITY" in declaration:
        raise ContractError("PPTX XML declarations/entities are forbidden")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ContractError(f"malformed PPTX XML: {name}") from exc


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    location = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if location not in archive.namelist():
        return {}
    result = {}
    for relation in _xml(archive, location):
        rel_id, target, kind = (relation.get(key, "") for key in ("Id", "Target", "Type"))
        if rel_id in result:
            raise ContractError("duplicate PPTX relationship identifier")
        if relation.get("TargetMode") == "External":
            if not kind.endswith("/hyperlink"):
                raise ContractError("external PPTX media and data relationships are not accepted")
            continue
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
        if target.startswith("/"):
            resolved = target.lstrip("/")
        if resolved.startswith("../") or "\\" in target:
            raise ContractError("PPTX relationship escapes its package")
        result[rel_id] = (resolved, kind)
    return result


def _inspect_package(pptx: Path, count: int, needs_notes: bool, evidence: set[str], claims: set[str]) -> dict:
    if pptx.stat().st_size > _MAX_PPTX_BYTES:
        raise ContractError("PPTX exceeds the 256 MB candidate limit")
    try:
        with zipfile.ZipFile(pptx) as archive:
            names = archive.namelist()
            if len(set(names)) != len(names):
                raise ContractError("duplicate PPTX ZIP members")
            for item in archive.infolist():
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                    raise ContractError("PPTX ZIP member traverses outside its package")
                if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise ContractError("symlink or encrypted PPTX ZIP member")
        legacy.AgentPipeline._validate_native_pptx(pptx)
        with zipfile.ZipFile(pptx) as archive:
            names = set(archive.namelist())
            for name in sorted(names):
                if not name.endswith((".xml", ".rels")):
                    continue
                document = _xml(archive, name)
                # Concatenate text runs so split OOXML runs cannot hide an ID.
                _check_references(_document_text(document), evidence, claims, name)
                for element in document.iter():
                    if (element.tag.rsplit("}", 1)[-1] == "Relationship"
                            and element.get("TargetMode") == "External"
                            and not element.get("Type", "").endswith("/hyperlink")):
                        raise ContractError("external PPTX media and data relationships are not accepted")
                    for key, value in element.attrib.items():
                        if key.rsplit("}", 1)[-1] in {"claim-id", "claim_id", "data-claim-id"}:
                            if value not in claims:
                                raise ContractError(f"undefined claim reference in {name}: {value}")
            presentation = _xml(archive, "ppt/presentation.xml")
            relationships = _relationships(archive, "ppt/presentation.xml")
            ordered = []
            for element in presentation.iter():
                if element.tag.rsplit("}", 1)[-1] != "sldId":
                    continue
                linked = relationships.get(element.get(_REL_NS + "id", ""))
                if not linked or not linked[1].endswith("/slide") or linked[0] not in names:
                    raise ContractError("PPTX presentation has an invalid slide relationship")
                ordered.append(linked[0])
            slide_parts = {name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)}
            if len(ordered) != count or len(set(ordered)) != count or set(ordered) != slide_parts:
                raise ContractError("PPTX slide count or presentation order differs from the approved plan")
            notes, notes_by_slide = [], []
            for part in ordered:
                linked_notes = [target for target, kind in _relationships(archive, part).values() if kind.endswith("/notesSlide")]
                if len(linked_notes) > 1 or any(name not in names for name in linked_notes):
                    raise ContractError("PPTX slide has invalid notes relationships")
                if needs_notes and len(linked_notes) != 1:
                    raise ContractError("speaker notes are missing from a PPTX slide")
                notes.extend(linked_notes)
                notes_by_slide.append(_reference_sets(_document_text(_xml(archive, linked_notes[0]))) if linked_notes else None)
            note_parts = {name for name in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name)}
            if len(notes) != len(set(notes)) or set(notes) != note_parts or len(notes) not in {0, count}:
                raise ContractError("PPTX notes count or mapping differs from the page plan")
            return {"slide_count": len(ordered), "notes_count": len(notes), "slide_parts": ordered,
                    "note_references_by_slide": notes_by_slide}
    except (legacy.ContractError, zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ContractError(f"PPTX package verification failed: {exc}") from exc


def _selected_export(project: Path, candidate: Path) -> None:
    exports = [p for folder in (project, project / "exports") for p in folder.glob("*.pptx") if not p.stem.endswith("_svg")]
    if not exports:
        raise ContractError("no canonical native PPTX export")
    newest = max(path.stat().st_mtime for path in exports)
    selected = [path for path in exports if path.stat().st_mtime == newest]
    if len(selected) != 1 or selected[0].resolve() != candidate.resolve() or candidate.parent != project / "exports":
        raise ContractError("explicit PPTX candidate differs from the shared verifier's selected export")
    if any(legacy._path_has_symlink(path, project) for path in exports):
        raise ContractError("PPTX export candidates contain a symlink")


def _gate_helper(repo_root: Path):
    helper = repo_root / ".claude/skills/ppt-master/scripts/gate_receipts.py"
    spec = importlib.util.spec_from_file_location("_v2_content_gate_receipts", helper)
    if spec is None or spec.loader is None:
        raise VerificationBlocked("content-bound shared verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gate_capture(repo_root: Path, project: Path) -> dict:
    try:
        return _gate_helper(repo_root).capture_gate(project, "svg-quality")
    except (ValueError, OSError) as exc:
        raise ContractError(f"cannot freeze candidate inputs: {exc}") from exc


def _environment_snapshot(repo_root: Path, route: str) -> dict:
    owner = safe_path(repo_root, ROUTES[route])
    paths = [Path(__file__), Path(legacy.__file__), Path(legacy_utils.__file__), Path(contracts.__file__),
             repo_root / "projects/presentation-agent-suite/scripts/render_quicklook_contact_sheet.swift"]
    return {
        "shared_gate_bundle_sha256": _gate_helper(repo_root)._validator_bundle("svg-quality"),
        "adapter_sha256": {str(path.resolve().relative_to(repo_root)): file_hash(path) for path in paths},
        "owner_sha256": file_hash(owner),
    }


def receipt_environment_matches(repo_root: Path, receipt: dict) -> bool:
    """Invalidate G4/G5 if current owner, rule data or adapter code differs."""
    try:
        repo_root = repo_root.resolve()
        route = receipt.get("route")
        if (receipt.get("schema_version") != "g4-receipt.v1" or receipt.get("verdict") != "PASS"
                or route not in {"main-svg-generation", "beautify"} or receipt.get("owner") != ROUTES[route]):
            return False
        current = _environment_snapshot(repo_root, route)
        bundle_hash = digest({"shared": current["shared_gate_bundle_sha256"], "adapter": current["adapter_sha256"]})
        return (receipt.get("verifier_environment") == current
                and receipt.get("owner_sha256") == current["owner_sha256"]
                and receipt.get("validator_bundle_sha256") == bundle_hash)
    except (ContractError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def verify_candidate(
    repo_root: Path, workspace: Path, data: dict, intent: dict, research: dict, direction: dict,
    *, cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Keep failed native render outputs out of the attempt's reusable files."""
    outputs: list[Path] = []
    try:
        return _verify_candidate(repo_root, workspace, data, intent, research, direction,
                                 cancelled=cancelled, render_outputs=outputs)
    except Exception:
        for contact in outputs:
            if legacy._path_has_symlink(contact.parent, workspace.resolve()):
                continue
            try:
                contact.unlink(missing_ok=True)
                contact.with_suffix('.render.json').unlink(missing_ok=True)
                directory = contact.parent / f'{contact.stem}-pages'
                if directory.is_symlink():
                    directory.unlink()
                elif directory.exists():
                    shutil.rmtree(directory)
            except OSError:
                pass  # Preserve the validation/cancellation error; no receipt was returned.
        raise


def _verify_candidate(
    repo_root: Path, workspace: Path, data: dict, intent: dict, research: dict, direction: dict,
    *, cancelled: Callable[[], bool] | None = None, render_outputs: list[Path],
) -> dict:
    """Return a trusted PASS receipt, or fail/raise VerificationBlocked."""
    contact: Path | None = None
    owns_render_pages = False

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            if contact is not None:
                contact.unlink(missing_ok=True)
                if owns_render_pages:
                    contact.with_suffix('.render.json').unlink(missing_ok=True)
                    shutil.rmtree(contact.parent / f'{contact.stem}-pages', ignore_errors=True)
            raise VerificationCancelled("G4 verification was cancelled")

    check_cancelled()
    repo_root, workspace = repo_root.resolve(), workspace.resolve()
    route = direction.get("route")
    if route not in ROUTES:
        raise ContractError("approved route is missing or unknown")
    if route not in {"main-svg-generation", "beautify"}:
        raise VerificationBlocked(f"{route}: no trusted owner verification adapter is installed; SVG checks cannot replace it")
    project_relative = data.get("project_path")
    if not isinstance(project_relative, str) or not project_relative:
        raise ContractError("project_path must identify a workspace-relative project folder")
    project = workspace if project_relative == "." else safe_path(workspace, project_relative, exists=False)
    if not project.is_dir() or legacy._path_has_symlink(project, workspace):
        raise ContractError("project folder is missing or contains a symlink")
    owner = safe_path(repo_root, ROUTES[route])
    script = safe_path(repo_root, ".claude/skills/ppt-master/scripts/verify_deck.py")
    candidate = _require_path(workspace, data.get("pptx_path"))
    if not candidate.resolve().is_relative_to(project.resolve()):
        raise ContractError("candidate is outside its selected project")
    _selected_export(project, candidate)
    slides = intent.get("slides", [])
    pages = data.get("pages", [])
    if (not slides or not isinstance(pages, list) or any(not isinstance(page, dict) for page in pages)
            or [p.get("slide_uid") for p in pages] != [s["uid"] for s in slides]):
        raise ContractError("candidate pages must exactly match the approved slide identifiers and order")
    claims = {claim["id"] for claim in research.get("claims", [])}
    evidence = {item["legacy_id"] for item in intent.get("requirements", []) if item.get("legacy_id")}
    evidence |= {identifier for identifier in claims if identifier.startswith("EVID-")}
    needs_notes = bool(intent.get("fields", {}).get("speaker_notes", {}).get("value", False))
    declared_paths, notes_paths = [], []
    for page_number, page in enumerate(pages, 1):
        check_cancelled()
        path = _require_path(workspace, page.get("path"))
        if path.parent != project / "svg_output" or path.suffix != ".svg":
            raise ContractError("main SVG pages must be canonical svg_output files")
        claim_ids = page.get("claim_ids")
        if not isinstance(claim_ids, list) or any(not isinstance(identifier, str) for identifier in claim_ids) or set(claim_ids) - claims:
            raise ContractError("page contains undefined claim references")
        declared_paths.append(path)
        _check_references(_read_text(path), evidence, claims, path.name)
        if page.get("notes_path"):
            notes_path = _require_path(workspace, page["notes_path"])
            if notes_path.parent != project / "notes" or notes_path.suffix != ".md" or notes_path.name == "total.md":
                raise ContractError("page notes must be canonical per-page Markdown files")
            indexed_note = re.search(r"slide[_]?(\d+)", notes_path.stem)
            if notes_path.stem != path.stem and (not indexed_note or int(indexed_note.group(1)) != page_number):
                raise ContractError("notes filename does not map to its declared SVG page in the exporter")
            notes_paths.append(notes_path)
            _check_references(_read_text(notes_path), evidence, claims, notes_path.name)
        elif needs_notes:
            raise ContractError("approved speaker notes are missing from the page candidate")
    if declared_paths != sorted((project / "svg_output").glob("*.svg")):
        raise ContractError("declared pages differ from SVG export order or page count")
    actual_notes = sorted(path for path in (project / "notes").glob("*.md") if path.name != "total.md")
    if actual_notes and (len(actual_notes) != len(pages) or sorted(notes_paths) != actual_notes):
        raise ContractError("notes source count or mapping differs from declared pages")
    for name, key in (("design_spec.md", "design_spec"), ("spec_lock.md", "spec_lock")):
        actual = _read_text(_require_path(workspace, str((project / name).relative_to(workspace))))
        if not isinstance(direction.get(key), str) or actual != direction[key]:
            raise ContractError(f"{name} differs from the G3-approved design direction")
        _check_references(actual, evidence, claims, name)
    package = _inspect_package(candidate, len(slides), needs_notes, evidence, claims)
    for page, exported_refs in zip(pages, package["note_references_by_slide"]):
        if page.get("notes_path"):
            expected_refs = _reference_sets(_read_text(_require_path(workspace, page["notes_path"])))
            if exported_refs != expected_refs:
                raise ContractError("exported notes references differ from the declared source page notes")
    check_cancelled()
    before = _gate_capture(repo_root, project)
    input_hashes = {str(project.relative_to(workspace) / name): value for name, value in before["input_sha256"].items()}
    for path in [candidate, *notes_paths]:
        input_hashes[str(path.relative_to(workspace))] = file_hash(path)
    owner_hash = file_hash(owner)
    environment = _environment_snapshot(repo_root, route)
    adapter_hashes = environment["adapter_sha256"]
    candidate_hash = file_hash(candidate)
    contact = project / "_pptx_render" / f"{candidate.stem}-grid.png"
    log = project / "_v2_verification" / "verify_deck.log"
    for path in (contact, log):
        if legacy._path_has_symlink(path, workspace):
            raise ContractError("verification outputs contain a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
    contact.unlink(missing_ok=True)
    started = time.time_ns()
    command = [sys.executable, str(script), str(project), "--require-render-success"]
    try:
        result = legacy._run_bounded_subprocess(command, cwd=repo_root, timeout=600, cancelled=cancelled)
    except legacy.SubprocessCancelled as exc:
        contact.unlink(missing_ok=True)
        raise VerificationCancelled(str(exc)) from exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise VerificationBlocked(f"shared verifier could not finish: {exc}") from exc
    check_cancelled()
    log.write_text("[stdout]\n" + result.stdout + "\n[stderr]\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise ContractError(f"shared owner verifier failed with exit {result.returncode}; see {log.relative_to(workspace)}")
    officecli = legacy._officecli_bin()
    renderer = "officecli"
    diagnostics: list[str] = []
    render_provenance = None
    verified_outputs = {}
    render_pages = []
    if officecli is None:
        if contact.exists():
            raise ContractError("untrusted render: OfficeCLI was unavailable but a contact sheet appeared")
        try:
            rendered = legacy._render_macos_quicklook_contact_sheet(candidate, contact, diagnostics=diagnostics, cancelled=cancelled)
        except legacy.SubprocessCancelled as exc:
            contact.unlink(missing_ok=True)
            raise VerificationCancelled(str(exc)) from exc
        owns_render_pages = rendered
        if rendered:
            render_outputs.append(contact)
        check_cancelled()
        if not rendered:
            raise VerificationBlocked("a real exported-PPTX renderer is unavailable: " + "; ".join(diagnostics))
        renderer = "macos-quicklook-webkit"
        render_provenance = contact.with_suffix(".render.json")
        if (not render_provenance.is_file() or legacy._path_has_symlink(render_provenance, workspace)
                or render_provenance.stat().st_ctime_ns < started or render_provenance.stat().st_size > 1024 * 1024):
            raise ContractError("isolated renderer did not create fresh bounded provenance")
        with render_provenance.open("rb") as source:
            proof_bytes = source.read(1024 * 1024 + 1)
        if len(proof_bytes) > 1024 * 1024:
            raise ContractError("isolated renderer provenance exceeds the bounded size")
        verified_outputs[render_provenance] = (hashlib.sha256(proof_bytes).hexdigest(), len(proof_bytes))
    if not contact.is_file() or legacy._path_has_symlink(contact, workspace) or contact.stat().st_ctime_ns < started:
        raise ContractError("shared verifier did not create a fresh candidate contact sheet")
    try:
        with contact.open("rb") as source:
            contact_bytes = source.read(legacy._MAX_PNG_FILE_BYTES + 1)
        if len(contact_bytes) > legacy._MAX_PNG_FILE_BYTES:
            raise ContractError("renderer contact-sheet PNG exceeds the bounded size")
        legacy._validate_png_bytes(contact_bytes)
    except legacy.ContractError as exc:
        raise ContractError(str(exc)) from exc
    contact_hash = hashlib.sha256(contact_bytes).hexdigest()
    verified_outputs[contact] = (contact_hash, len(contact_bytes))
    del contact_bytes
    if render_provenance is not None:
        try:
            proof = json.loads(proof_bytes.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ContractError("isolated renderer provenance is invalid") from exc
        if (not isinstance(proof, dict) or proof.get("schema_version") != "quicklook-render.v1"
                or proof.get("method") != "isolated-slide-selection"
                or proof.get("source_sha256") != candidate_hash
                or proof.get("contact_sheet_sha256") != contact_hash
                or type(proof.get("slide_count")) is not int
                or proof.get("slide_count") != len(pages)
                or not isinstance(proof.get("pages"), list)
                or [item.get("page") if isinstance(item, dict) and type(item.get("page")) is int else None
                    for item in proof["pages"]] != list(range(1, len(pages) + 1))):
            raise ContractError("isolated renderer provenance does not match the candidate and page order")
        directory = contact.parent / f"{contact.stem}-pages"
        expected_names = [f"P{number:02d}.png" for number in range(1, len(pages) + 1)]
        if (legacy._path_has_symlink(directory, workspace) or not directory.is_dir()
                or sorted(path.name for path in directory.iterdir()) != sorted(expected_names)):
            raise ContractError("renderer page image directory or count is invalid")
        for number, (entry, slide) in enumerate(zip(proof["pages"], slides), 1):
            check_cancelled()
            relative = f"{contact.stem}-pages/P{number:02d}.png"
            if entry.get("image_path") != relative:
                raise ContractError("renderer page image path or order is invalid")
            path = safe_path(contact.parent, relative)
            if path.stat().st_ctime_ns < started:
                raise ContractError("renderer page image is not fresh")
            with path.open("rb") as source:
                image_bytes = source.read(legacy._MAX_PNG_FILE_BYTES + 1)
            if len(image_bytes) > legacy._MAX_PNG_FILE_BYTES:
                raise ContractError("renderer page image exceeds file bounds")
            try:
                legacy._validate_png_bytes(image_bytes)
            except legacy.ContractError as exc:
                raise ContractError("renderer page image is not a valid PNG") from exc
            width, height = struct.unpack(">II", image_bytes[16:24])
            image_hash = hashlib.sha256(image_bytes).hexdigest()
            if (type(entry.get("image_width")) is not int or type(entry.get("image_height")) is not int
                    or (width, height) != (entry["image_width"], entry["image_height"])
                    or width < 1920 or width * height > 16_000_000
                    or image_hash != entry.get("image_sha256")):
                raise ContractError("renderer page image hash, dimensions or resolution is invalid")
            verified_outputs[path] = (image_hash, len(image_bytes))
            render_pages.append({"slide_uid": slide["uid"], "page": number,
                                 "path": str(path.relative_to(workspace)), "sha256": image_hash,
                                 "width": width, "height": height})
    _selected_export(project, candidate)
    if _gate_capture(repo_root, project) != before or file_hash(owner) != owner_hash:
        raise ContractError("candidate inputs or owner rules changed during verification")
    for relative, expected in input_hashes.items():
        check_cancelled()
        path = safe_path(workspace, relative, exists=expected is not None)
        actual = file_hash(path) if path.is_file() else None
        if actual != expected:
            raise ContractError("candidate inputs changed during verification")
    if _environment_snapshot(repo_root, route) != environment:
        raise ContractError("trusted verifier adapter changed during verification")
    artifacts = []
    for kind, path in [("pptx", candidate), ("contact_sheet", contact), ("verification_log", log),
                       *([("render_provenance", render_provenance)] if render_provenance else []),
                       *[("render_page", workspace / page["path"]) for page in render_pages],
                       *[("page", path) for path in declared_paths], *[("notes", path) for path in notes_paths]]:
        artifact_hash, artifact_bytes = verified_outputs[path] if path in verified_outputs else (file_hash(path), path.stat().st_size)
        artifacts.append({"kind": kind, "path": str(path.relative_to(workspace)), "sha256": artifact_hash, "bytes": artifact_bytes})
    retained = {artifact["path"] for artifact in artifacts}
    for relative, expected in input_hashes.items():
        if expected is None or relative in retained:
            continue
        path = safe_path(workspace, relative)
        kind = "spec" if path.name in {"design_spec.md", "spec_lock.md"} else "input"
        artifacts.append({"kind": kind, "path": relative, "sha256": expected, "bytes": path.stat().st_size})
        retained.add(relative)
    for path, (expected, _) in verified_outputs.items():
        if legacy._path_has_symlink(path, workspace) or file_hash(path) != expected:
            raise ContractError("renderer outputs changed after verification")
    check_cancelled()
    return {
        "schema_version": "g4-receipt.v1", "verdict": "PASS", "route": route, "owner": ROUTES[route],
        "owner_sha256": owner_hash, "verified_at": now(), "renderer": renderer,
        "pptx_path": str(candidate.relative_to(workspace)), "pptx_sha256": candidate_hash,
        "contact_sheet_path": str(contact.relative_to(workspace)), "contact_sheet_sha256": contact_hash,
        "input_sha256": input_hashes, "contracts_sha256": {"intent": digest(intent), "research": digest(research), "direction": digest(direction)},
        "validator_bundle_sha256": digest({"shared": before["validator_bundle_sha256"], "adapter": adapter_hashes}),
        "verifier_environment": environment,
        "artifacts": artifacts, "pages": pages, "render_pages": render_pages, "package": package, "findings": [], "render_diagnostics": diagnostics,
    }
