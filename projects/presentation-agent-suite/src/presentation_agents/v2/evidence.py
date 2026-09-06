#!/usr/bin/env python3
"""SlideMaster v2 - verify excerpts against immutable local source snapshots.

Usage: EvidenceReader(root, artifacts).verify_support(source, support).
Example locators: lines:2-4 (UTF-8/DOCX/XLSX), page:3 (PDF).
Dependencies: stdlib; the repository-declared PyMuPDF for PDF extraction.

Extraction is verification, not OCR or semantic fact checking. Unsupported
formats, empty scan pages, and unresolvable locators remain blocking failures.
DOCX lines follow paragraph order. XLSX lines contain a sheet-name header followed
by populated worksheet rows in workbook order, with tab-separated cell values.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .contracts import ContractError, safe_path

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl"}
_MAX_FILE_BYTES = 100 * 1024 * 1024
_MAX_TEXT_BYTES = 20 * 1024 * 1024
_MAX_ZIP_BYTES = 40 * 1024 * 1024
_MAX_XML_BYTES = 12 * 1024 * 1024
_LINE_LOCATOR = re.compile(r"lines:([1-9]\d{0,6})(?:-([1-9]\d{0,6}))?\Z")
_PAGE_LOCATOR = re.compile(r"page:([1-9]\d{0,5})\Z")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def normalized_excerpt(text: str) -> str:
    """Normalize Unicode and whitespace while preserving punctuation and numbers."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def canonical_source_url(value: str) -> str:
    """Deduplicate exact URLs with scheme/host case and fragment normalization.

    A distinct URL is not evidence of publisher or study independence. Query
    strings and document paths are deliberately retained rather than guessed.
    """
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("unsupported source URL")
        host = parsed.hostname.lower()
        port = parsed.port
        if ":" in host:
            host = f"[{host}]"
        if port is not None and not ((port == 80 and parsed.scheme.lower() == "http") or (port == 443 and parsed.scheme.lower() == "https")):
            host += f":{port}"
        return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", parsed.query, ""))
    except (ValueError, TypeError) as exc:
        raise ContractError("source.url must be an HTTP(S) URL without credentials") from exc


class EvidenceReader:
    """Validate hashes, provenance, and exact source locations with bounded reads."""

    def __init__(self, root: Path | None, artifacts: dict[str, dict[str, Any]]) -> None:
        self.root = Path(root).resolve() if root is not None else None
        self.artifacts = artifacts
        self._paths: dict[str, Path] = {}
        self._texts: dict[str, list[str]] = {}
        self._pages: dict[tuple[str, int], str] = {}

    def verified_path(self, artifact_id: str) -> Path:
        if artifact_id in self._paths:
            return self._paths[artifact_id]
        if self.root is None:
            raise ContractError("artifact_root is required to verify cited source bytes")
        artifact = self.artifacts.get(artifact_id)
        if not artifact or artifact.get("valid") is not True:
            raise ContractError("source artifact is missing or stale")
        relative = artifact.get("path")
        expected = artifact.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            raise ContractError("source artifact has no valid path/hash metadata")
        try:
            path = safe_path(self.root, relative)
            if path.stat().st_size > _MAX_FILE_BYTES:
                raise ContractError("source exceeds the 100 MB verification limit")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise ContractError("source snapshot could not be read") from exc
        if actual != expected.lower():
            raise ContractError("source snapshot bytes changed after import")
        self._paths[artifact_id] = path
        return path

    def provided_origin(self, artifact_id: str) -> bool:
        """Only service-owned provenance can identify user-provided source data."""
        seen: set[str] = set()
        while artifact_id:
            if artifact_id in seen or len(seen) >= 64:
                raise ContractError("source provenance contains a cycle or exceeds 64 ancestors")
            seen.add(artifact_id)
            artifact = self.artifacts.get(artifact_id)
            if not artifact or artifact.get("valid") is not True:
                raise ContractError("source provenance references a missing or stale artifact")
            self.verified_path(artifact_id)
            provenance = artifact.get("provenance", {})
            if not isinstance(provenance, dict):
                raise ContractError("source provenance must be service-owned metadata")
            if artifact.get("kind") == "attachment" and provenance.get("origin") in {"user-upload", "import"}:
                return True
            parent = provenance.get("derived_from")
            if parent is None:
                return False
            if not isinstance(parent, str) or not parent:
                raise ContractError("source provenance derived_from must identify one artifact")
            artifact_id = parent
        return False

    def verify_support(self, source: dict[str, Any], support: dict[str, Any]) -> dict[str, Any]:
        artifact_id = source["artifact_id"]
        path = self.verified_path(artifact_id)
        locator, excerpt = support.get("locator"), support.get("excerpt")
        if not isinstance(locator, str) or not isinstance(excerpt, str) or not normalized_excerpt(excerpt):
            raise ContractError("support.locator and support.excerpt must be nonempty strings")
        if len(excerpt) > 20000:
            raise ContractError("support excerpt exceeds 20,000 characters; cite a smaller passage")
        page = _PAGE_LOCATOR.fullmatch(locator)
        lines = _LINE_LOCATOR.fullmatch(locator)
        if path.suffix.lower() == ".pdf":
            if not page:
                raise ContractError("PDF evidence requires a page:N locator")
            selected = self._pdf_page(artifact_id, path, int(page.group(1)))
            extractor = "pymupdf-page-text"
        else:
            if not lines:
                raise ContractError("text/DOCX/XLSX evidence requires a lines:N-M locator")
            text_lines = self.text_lines(artifact_id)
            start = int(lines.group(1))
            end = int(lines.group(2) or start)
            if start > end or end > len(text_lines) or end - start >= 200:
                raise ContractError("source line range is invalid, absent, or exceeds 200 lines")
            selected = "\n".join(text_lines[start - 1:end])
            extractor = "utf8-lines" if path.suffix.lower() in _TEXT_SUFFIXES else f"{path.suffix[1:].lower()}-lines-v1"
        if normalized_excerpt(excerpt) not in normalized_excerpt(selected):
            raise ContractError("support excerpt does not occur within the cited source location")
        return {"status": "VERIFIED", "artifact_id": artifact_id,
                "sha256": self.artifacts[artifact_id]["sha256"], "locator": locator,
                "extractor": extractor, "semantic_entailment": "UNVERIFIED"}

    def text_lines(self, artifact_id: str) -> list[str]:
        """Return deterministic verification lines, suitable for a source viewer."""
        if artifact_id in self._texts:
            return self._texts[artifact_id]
        path = self.verified_path(artifact_id)
        suffix = path.suffix.lower()
        try:
            if suffix in _TEXT_SUFFIXES:
                if path.stat().st_size > _MAX_TEXT_BYTES:
                    raise ContractError("text source exceeds the 20 MB extraction limit")
                text = path.read_text(encoding="utf-8-sig")
            elif suffix in {".docx", ".xlsx"}:
                text = self._office_text(path, suffix)
            else:
                raise ContractError("unsupported source format; provide a supported original or trusted extraction")
        except (UnicodeDecodeError, OSError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
            raise ContractError("source extraction failed; provide a readable UTF-8 or supported document snapshot") from exc
        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ContractError("extracted source exceeds the 20 MB text limit")
        result = text.splitlines()
        if not result:
            raise ContractError("source has no extractable text")
        self._texts[artifact_id] = result
        return result

    def _pdf_page(self, artifact_id: str, path: Path, page: int) -> str:
        if (artifact_id, page) in self._pages:
            return self._pages[artifact_id, page]
        try:
            import pymupdf
        except ImportError as exc:
            raise ContractError("PDF verification requires the repository-declared PyMuPDF dependency") from exc
        try:
            with pymupdf.open(path) as document:
                if document.needs_pass or page > document.page_count:
                    raise ContractError("PDF is encrypted or the cited page does not exist")
                text = document[page - 1].get_text("text", sort=True)
        except (RuntimeError, ValueError, OSError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError("PDF source could not be extracted") from exc
        if not normalized_excerpt(text):
            raise ContractError("PDF page has no extractable text; OCR evidence is not verified by this reader")
        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ContractError("PDF page text exceeds the extraction limit")
        self._pages[artifact_id, page] = text
        return text

    @staticmethod
    def _office_text(path: Path, suffix: str) -> str:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 10000 or sum(i.file_size for i in members) > _MAX_ZIP_BYTES:
                raise ContractError("Office document exceeds the bounded extraction budget")
            def read_xml(name: str) -> ET.Element:
                info = archive.getinfo(name)
                if info.file_size > _MAX_XML_BYTES or info.flag_bits & 1:
                    raise ContractError("Office XML is encrypted or exceeds the extraction limit")
                raw = archive.read(name)
                if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise ContractError("Office XML declarations are not accepted")
                return ET.fromstring(raw)
            if suffix == ".docx":
                document = read_xml("word/document.xml")
                paragraphs = []
                for paragraph in document.iter(_W + "p"):
                    parts = []
                    for node in paragraph.iter():
                        if node.tag == _W + "t":
                            parts.append(node.text or "")
                        elif node.tag == _W + "tab":
                            parts.append("\t")
                        elif node.tag in {_W + "br", _W + "cr"}:
                            parts.append(" ")
                    paragraphs.append("".join(parts))
                return "\n".join(paragraphs)
            workbook = read_xml("xl/workbook.xml")
            relationships = read_xml("xl/_rels/workbook.xml.rels")
            targets = {r.attrib.get("Id"): r.attrib.get("Target", "") for r in relationships if r.attrib.get("TargetMode") != "External"}
            strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ["".join(node.itertext()) for node in read_xml("xl/sharedStrings.xml")]
            output: list[str] = []
            for sheet in workbook.iter(_S + "sheet"):
                target = targets.get(sheet.attrib.get(_R + "id"))
                if not target:
                    raise ContractError("worksheet relationship is unavailable")
                name = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
                if not name.startswith("xl/worksheets/") or ".." in name.split("/"):
                    raise ContractError("worksheet relationship is outside the workbook")
                output.append("# " + sheet.attrib.get("name", "Worksheet"))
                for row in read_xml(name).iter(_S + "row"):
                    values = []
                    for cell in row.findall(_S + "c"):
                        value = cell.findtext(_S + "v", default="")
                        if cell.find(_S + "f") is not None:
                            # Cached formula values are not recomputed by this
                            # extractor; never present them as verified results.
                            value = "[formula requires recalculation]"
                        elif cell.attrib.get("t") == "s":
                            try:
                                index = int(value)
                                if index < 0:
                                    raise ValueError("negative string index")
                                value = strings[index]
                            except (ValueError, IndexError) as exc:
                                raise ContractError("worksheet shared-string reference is invalid") from exc
                        elif cell.attrib.get("t") == "inlineStr":
                            value = "".join(n.text or "" for n in cell.iter(_S + "t"))
                        elif cell.attrib.get("t") == "e":
                            value = "[cell error]"
                        values.append(f"{cell.attrib.get('r', '?')}={value}")
                    output.append("\t".join(values))
            return "\n".join(output)
