#!/usr/bin/env python3
"""
Presentation Agent Suite - PDF Report Renderer

Renders a readable A4 PDF from the research agent's Markdown analysis.

Usage:
    Import render_markdown_pdf from the pipeline.

Dependencies:
    PyMuPDF.
"""

from __future__ import annotations

import re
from pathlib import Path


_PAGE_WIDTH = 595
_PAGE_HEIGHT = 842
_MARGIN_X = 54
_MARGIN_TOP = 58
_MARGIN_BOTTOM = 54
_TEXT_WIDTH = _PAGE_WIDTH - (2 * _MARGIN_X)


def _font_paths(repo_root: Path) -> tuple[Path, Path]:
    base = repo_root / ".claude/skills/ppt-master/assets/fonts/Pretendard"
    regular = base / "Pretendard-Regular.otf"
    semibold = base / "Pretendard-SemiBold.otf"
    if not regular.is_file() or not semibold.is_file():
        raise RuntimeError(f"Pretendard font assets are missing: {base}")
    return regular, semibold


def _font_category(character: str) -> str:
    codepoint = ord(character)
    if (
        0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xD7B0 <= codepoint <= 0xD7FF
    ):
        return "primary"
    if codepoint < 128:
        return "ascii"
    return "symbol"


def _text_length(text: str, primary_font, ascii_font, symbol_font, size: float) -> float:
    fonts = {
        "primary": primary_font,
        "ascii": ascii_font,
        "symbol": symbol_font,
    }
    return sum(
        fonts[_font_category(character)].text_length(character, fontsize=size)
        for character in text
    )


def _font_runs(text: str) -> list[tuple[str, str]]:
    """Return Hangul, ASCII, and symbol runs for reliable PDF glyph mapping."""
    if not text:
        return []
    runs: list[tuple[str, str]] = []
    current_category = _font_category(text[0])
    current = [text[0]]
    for character in text[1:]:
        category = _font_category(character)
        if category == current_category:
            current.append(character)
            continue
        runs.append((current_category, "".join(current)))
        current_category = category
        current = [character]
    runs.append((current_category, "".join(current)))
    return runs


def _insert_mixed_text(
    page,
    point: tuple[float, float],
    text: str,
    *,
    primary_name: str,
    primary_font,
    ascii_name: str,
    ascii_font,
    symbol_name: str,
    symbol_font,
    size: float,
    color: tuple[float, float, float],
) -> None:
    x, y = point
    names = {
        "primary": primary_name,
        "ascii": ascii_name,
        "symbol": symbol_name,
    }
    fonts = {
        "primary": primary_font,
        "ascii": ascii_font,
        "symbol": symbol_font,
    }
    for category, run in _font_runs(text):
        font_name = names[category]
        measure_font = fonts[category]
        page.insert_text(
            (x, y),
            run,
            fontname=font_name,
            fontsize=size,
            color=color,
        )
        x += measure_font.text_length(run, fontsize=size)


def _split_long_token(
    token: str,
    font,
    ascii_font,
    symbol_font,
    size: float,
    width: float,
) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_width = 0.0
    for character in token:
        character_width = _text_length(character, font, ascii_font, symbol_font, size)
        if current and current_width + character_width > width:
            chunks.append("".join(current))
            current = [character]
            current_width = character_width
        else:
            current.append(character)
            current_width += character_width
    if current:
        chunks.append("".join(current))
    return chunks


def _wrap(text: str, font, ascii_font, symbol_font, size: float, width: float) -> list[str]:
    words = re.findall(r"\S+", text)
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        pieces = _split_long_token(word, font, ascii_font, symbol_font, size, width)
        for piece in pieces:
            candidate = piece if not current else f"{current} {piece}"
            if current and _text_length(candidate, font, ascii_font, symbol_font, size) > width:
                lines.append(current)
                current = piece
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines


def _blocks(markdown: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("body", " ".join(paragraph)))
            paragraph.clear()

    for raw_line in markdown.replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            flush()
            blocks.append((f"h{len(heading.group(1))}", heading.group(2).strip()))
        elif re.match(r"^[-*+]\s+", line):
            flush()
            blocks.append(("bullet", re.sub(r"^[-*+]\s+", "", line)))
        elif re.match(r"^\d+[.)]\s+", line):
            flush()
            blocks.append(("bullet", re.sub(r"^\d+[.)]\s+", "", line)))
        elif line.startswith(">"):
            flush()
            blocks.append(("quote", line.lstrip("> ")))
        elif line.startswith("|") and line.endswith("|"):
            flush()
            if not re.fullmatch(r"[|:\- ]+", line):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                blocks.append(("table", " · ".join(cells)))
        else:
            paragraph.append(line)
    flush()
    return blocks


def render_markdown_pdf(markdown_path: Path, output_path: Path, repo_root: Path) -> None:
    """Render Markdown to a Korean-capable A4 PDF with embedded Pretendard."""
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required. Run with the SlideMaster repository-local .venv/bin/python."
        ) from exc

    if not markdown_path.is_file():
        raise FileNotFoundError(markdown_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    if not markdown.strip():
        raise ValueError("analysis Markdown is empty")

    regular_path, semibold_path = _font_paths(repo_root)
    regular_measure = fitz.Font(fontfile=str(regular_path))
    semibold_measure = fitz.Font(fontfile=str(semibold_path))
    ascii_measure = fitz.Font(fontname="helv")
    ascii_bold_measure = fitz.Font(fontname="hebo")
    symbol_measure = fitz.Font(fontname="korea")
    document = fitz.open()
    page = None
    cursor_y = _MARGIN_TOP
    page_number = 0

    def add_page():
        nonlocal page, cursor_y, page_number
        page = document.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        page.insert_font(fontname="Pretendard", fontfile=str(regular_path))
        page.insert_font(fontname="PretendardSemiBold", fontfile=str(semibold_path))
        page.insert_font(fontname="helv")
        page.insert_font(fontname="hebo")
        page.insert_font(fontname="korea")
        page_number += 1
        cursor_y = _MARGIN_TOP
        return page

    def ensure_space(height: float) -> None:
        nonlocal page
        if page is None or cursor_y + height > _PAGE_HEIGHT - _MARGIN_BOTTOM:
            add_page()

    style = {
        "h1": (22.0, 32.0, "PretendardSemiBold", semibold_measure, "hebo", ascii_bold_measure, 12.0),
        "h2": (16.0, 24.0, "PretendardSemiBold", semibold_measure, "hebo", ascii_bold_measure, 8.0),
        "h3": (13.0, 20.0, "PretendardSemiBold", semibold_measure, "hebo", ascii_bold_measure, 6.0),
        "h4": (11.5, 18.0, "PretendardSemiBold", semibold_measure, "hebo", ascii_bold_measure, 5.0),
        "body": (10.5, 16.0, "Pretendard", regular_measure, "helv", ascii_measure, 6.0),
        "bullet": (10.5, 16.0, "Pretendard", regular_measure, "helv", ascii_measure, 4.0),
        "quote": (10.0, 15.0, "Pretendard", regular_measure, "helv", ascii_measure, 6.0),
        "table": (9.2, 14.0, "Pretendard", regular_measure, "helv", ascii_measure, 3.0),
    }

    for kind, text in _blocks(markdown):
        (
            size,
            line_height,
            font_name,
            measure_font,
            ascii_name,
            ascii_font,
            after,
        ) = style[kind]
        prefix = "• " if kind == "bullet" else ""
        indent = 16 if kind in {"bullet", "quote"} else 0
        lines = _wrap(
            prefix + text,
            measure_font,
            ascii_font,
            symbol_measure,
            size,
            _TEXT_WIDTH - indent,
        )
        block_height = max(line_height, len(lines) * line_height) + after
        ensure_space(min(block_height, line_height + after))
        for line in lines:
            ensure_space(line_height)
            if page is None:
                raise RuntimeError("PDF page allocation failed")
            _insert_mixed_text(
                page,
                (_MARGIN_X + indent, cursor_y),
                line,
                primary_name=font_name,
                primary_font=measure_font,
                ascii_name=ascii_name,
                ascii_font=ascii_font,
                symbol_name="korea",
                symbol_font=symbol_measure,
                size=size,
                color=(0.09, 0.12, 0.16),
            )
            cursor_y += line_height
        if cursor_y + after <= _PAGE_HEIGHT - _MARGIN_BOTTOM:
            cursor_y += after

    for index, pdf_page in enumerate(document, start=1):
        footer = f"Presentation Agent Suite · Research Analysis · {index}/{document.page_count}"
        _insert_mixed_text(
            pdf_page,
            (_MARGIN_X, _PAGE_HEIGHT - 28),
            footer,
            primary_name="Pretendard",
            primary_font=regular_measure,
            ascii_name="helv",
            ascii_font=ascii_measure,
            symbol_name="korea",
            symbol_font=symbol_measure,
            size=8,
            color=(0.42, 0.46, 0.52),
        )

    document.set_metadata({
        "title": markdown_path.stem,
        "author": "Research & Analyst Agent",
        "subject": "Evidence-bound presentation research analysis",
    })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path, garbage=4, deflate=True)
    document.close()


def validate_pdf_report(pdf_path: Path) -> int:
    """Return page count after validating PDF magic and extractable text."""
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required. Run with the SlideMaster repository-local .venv/bin/python."
        ) from exc
    if not pdf_path.is_file() or pdf_path.stat().st_size < 1000:
        raise RuntimeError("analysis PDF was not generated")
    if not pdf_path.read_bytes().startswith(b"%PDF-"):
        raise RuntimeError("analysis PDF has an invalid signature")
    try:
        with fitz.open(pdf_path) as document:
            page_count = document.page_count
            extracted = "\n".join(page.get_text() for page in document)
    except Exception as exc:
        raise RuntimeError("analysis PDF cannot be opened") from exc
    if page_count < 1 or not extracted.strip():
        raise RuntimeError("analysis PDF lacks pages or extractable text")
    return page_count
