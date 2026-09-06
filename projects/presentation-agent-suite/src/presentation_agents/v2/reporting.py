"""Service-owned, escaped research report exports from validated contracts.

The model supplies analysis text; this module binds that text, all claims,
locators, limitations and slide messages to the same G2 review package.
Only repository-declared PyMuPDF is used. No remote assets or raw HTML execute.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from .contracts import ContractError

_UNRENDERABLE_SOURCE = re.compile("[\u2024\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")


def _display_text(text: str) -> str:
    # Source extracts can contain private font codes with no Unicode glyph.
    # Preserve their identity explicitly instead of emitting invisible NULs.
    return _UNRENDERABLE_SOURCE.sub(lambda match: f"[U+{ord(match[0]):04X}]", text)


def _literal(value: object) -> str:
    """Keep contract fields and verbatim excerpts distinct from analysis markup."""
    text = re.sub(r"([\\`*_\[\]#!|>])", r"\\\1", str(value))
    text = re.sub(r"(?m)^(\s*)([-+])(?=\s)", r"\1\\\2", text)
    return re.sub(r"(?m)^(\s*\d+)([.)])(?=\s)", r"\1\\\2", text)


def _code_literal(value: object) -> str:
    text = str(value)
    fence = "`" * (1 + max([0, *(len(run) for run in re.findall(r"`+", text))]))
    return fence + text + fence


def research_markdown(research: dict, intent: dict) -> str:
    title = intent["fields"]["topic"]["value"]
    def paragraphs(value):
        return [value] if isinstance(value, str) else value
    parts = [f"# {_literal(title)} - 리서치·분석 검토", *paragraphs(research["analysis"]), "## 페이지별 전달 메시지"]
    pages = {p["uid"]: p for p in intent["slides"]}
    for message in research["messages"]:
        page = pages[message["slide_uid"]]
        parts += [f"### {_literal(page['display_id'])} · {_literal(page['title'])}", _literal(message["message"]),
                  "근거: " + (_literal(", ".join(message.get("claim_ids", []))) or "사실 단정 없는 제안·안내")]
        if message.get("caveat"):
            parts.append("한계: " + _literal(message["caveat"]))
    parts.append("## 주장·원문 위치·계산 장부")
    for claim in research["claims"]:
        parts += [f"### {_literal(claim['id'])} · {_literal(claim['evidence_status'])}", _literal(claim["text"])]
        metadata = [f"**{key}:** {_literal(claim[key])}" for key in
                    ("value", "unit", "population", "as_of", "as_of_note", "formula", "calculation")
                    if key in claim]
        if metadata:
            parts.append(" · ".join(metadata))
        for support in claim.get("supports", []):
            quote = "\n".join("> " + _literal(line) for line in support["excerpt"].splitlines())
            parts += [f"**{_literal(support['source_id'])} · {_literal(support['locator'])}**", quote]
        parts.extend("- 한계: " + _literal(item) for item in paragraphs(claim.get("limitations", [])))
    parts.append("## 자료 목록")
    for source in research["sources"]:
        parts += [f"### {_literal(source['id'])} · {_literal(source['title'])}",
                  f"상태: {_literal(source.get('status', 'SNAPSHOT'))} · 접근일: {_literal(source.get('accessed_at', '미기록'))}"]
        metadata = [f"**{key}:** {_code_literal(source[key])}" for key in ("url", "sha256", "artifact_id") if source.get(key)]
        if metadata:
            parts.append(" · ".join(metadata))
    parts.append("## 해석과 검증의 한계")
    parts.extend("- " + _literal(item) for item in paragraphs(research["limitations"]))
    parts.append("원문 파일·발췌 위치·입력값·산술 계산은 자동 검증합니다. 의미적 뒷받침과 현재성은 분석·검토의 책임이며 파일 해시만으로 확인되지 않습니다.")
    return "\n\n".join(parts) + "\n"


def _escaped(text: str, wrap: int = 36) -> str:
    """Add layout-only wrap opportunities without dropping source characters."""
    text = _display_text(text)
    text = re.sub(r"\S{" + str(wrap + 1) + r",}",
                  lambda m: "\u200b".join(m[0][i:i + wrap] for i in range(0, len(m[0]), wrap)), text)
    return html.escape(text)


def _https_url(value: str) -> bool:
    if re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme.lower() == "https" and parsed.hostname
                    and parsed.username is None and parsed.password is None
                    and (parsed.port is None or 0 < parsed.port < 65536))
    except ValueError:
        return False


def _link_at(text: str, start: int) -> tuple[int, str, str] | None:
    """Recognize a bounded inline link; leave unsupported syntax as source text."""
    label_end = text.find("](", start + 1)
    if label_end < 0:
        return None
    cursor, depth = label_end + 2, 1
    while cursor < len(text):
        if text[cursor] == "\\" and cursor + 1 < len(text):
            cursor += 2
            continue
        if text[cursor] == "(":
            depth += 1
        elif text[cursor] == ")":
            depth -= 1
            if depth == 0:
                return cursor + 1, text[start + 1:label_end], text[label_end + 2:cursor]
        cursor += 1
    return None


def _inline(text: str, wrap: int = 36, depth: int = 0) -> str:
    if depth > 8:
        return _escaped(text, wrap)
    result, plain = [], []

    def flush():
        if plain:
            result.append(_escaped("".join(plain), wrap))
            plain.clear()

    index = 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text) and text[index + 1] in r"\`*_{}[]()#+-.!|>":
            plain.append(text[index + 1])
            index += 2
            continue
        if text[index] == "`":
            count = len(text[index:]) - len(text[index:].lstrip("`"))
            end = text.find("`" * count, index + count)
            if end >= 0:
                flush()
                result.append("<code>" + _escaped(text[index + count:end], wrap) + "</code>")
                index = end + count
                continue
        marker = text[index:index + 2]
        if marker in {"**", "__"}:
            end = text.find(marker, index + 2)
            if end > index + 2:
                flush()
                result.append("<strong>" + _inline(text[index + 2:end], wrap, depth + 1) + "</strong>")
                index = end + 2
                continue
        image = text[index:index + 2] == "!["
        if image or text[index] == "[":
            link = _link_at(text, index + 1 if image else index)
            if link:
                end, label, target = link
                flush()
                if not image and _https_url(target):
                    result.append('<a href="' + html.escape(target, quote=True) + '">'
                                  + _inline(label, wrap, depth + 1) + "</a>")
                else:
                    result.append(_escaped(text[index:end], wrap))
                index = end
                continue
        plain.append(text[index])
        index += 1
    flush()
    return "".join(result)


def _table_cells(line: str) -> list[str] | None:
    """Split only unescaped pipes outside inline code spans."""
    cells, current = [], []
    index, code = 0, 0
    while index < len(line):
        character = line[index]
        if character == "\\" and index + 1 < len(line):
            current.extend(line[index:index + 2])
            index += 2
            continue
        if character == "`":
            count = len(line[index:]) - len(line[index:].lstrip("`"))
            if not code:
                code = count
            elif code == count:
                code = 0
            current.append("`" * count)
            index += count
            continue
        if character == "|" and not code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    if not cells:
        return None
    cells.append("".join(current).strip())
    if cells[0] == "":
        cells.pop(0)
    if cells and cells[-1] == "":
        cells.pop()
    return cells or None


def _table_start(lines: list[str], index: int) -> tuple[list[str], list[str]] | None:
    if index + 1 >= len(lines):
        return None
    header, divider = _table_cells(lines[index]), _table_cells(lines[index + 1])
    if header and divider and len(header) == len(divider) and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in divider):
        return header, divider
    return None


def _table_html(header: list[str], rows: list[list[str]], divider: list[str]) -> str:
    columns = max([len(header), *(len(row) for row in rows)])
    header = [*header, *(f"추가 열 {i + 1}" for i in range(columns - len(header)))]
    wide = columns >= 5 or any(len(cell) > 90 for row in [header, *rows] for cell in row)
    wide = wide or any(sum(map(len, row)) > 250 for row in rows)
    if wide and rows:
        cards = []
        for row in rows:
            fields = []
            for index, label in enumerate(header):
                value = row[index] if index < len(row) else ""
                fields.append('<p class="table-field"><strong>' + _inline(label)
                              + ":</strong> " + _inline(value) + "</p>")
            cards.append('<div class="table-card">' + "".join(fields) + "</div>")
        return "".join(cards)
    parts = ['<table><thead><tr>']
    parts.extend("<th>" + _inline(cell, 12) + "</th>" for cell in header)
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for index in range(columns):
            rule = divider[index] if index < len(divider) else ""
            align = "center" if rule.startswith(":") and rule.endswith(":") else (
                "right" if rule.endswith(":") else "left")
            value = row[index] if index < len(row) else ""
            parts.append(f'<td style="text-align:{align}">' + _inline(value, 12) + "</td>")
        parts.append("</tr>")
    return "".join(parts) + "</tbody></table>"


def _markdown_html(markdown: str, depth: int = 0) -> str:
    """Render the supported Markdown subset; unknown markup remains escaped text."""
    if depth > 8:
        return "<p>" + _escaped(markdown) + "</p>"
    lines = markdown.splitlines()
    blocks, index = [], 0

    def list_item(line):
        return re.match(r"^\s*(?:([-+*])\s+|(\d+)[.)]\s+)(.*)$", line)

    def starts_block(position):
        line = lines[position]
        return bool(not line.strip() or re.match(r"^\s*(?:#{1,6}\s|>|`{3,}|~{3,})", line)
                    or list_item(line) or _table_start(lines, position))

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = re.match(r"^\s*(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading[1])
            blocks.append(f"<h{level}>" + _inline(heading[2]) + f"</h{level}>")
            index += 1
            continue
        fence = re.match(r"^\s*(`{3,}|~{3,})[^`~]*$", line)
        if fence:
            content = []
            index += 1
            while index < len(lines) and not re.fullmatch(r"\s*" + re.escape(fence[1][0])
                                                         + "{" + str(len(fence[1])) + r",}\s*", lines[index]):
                content.append(lines[index])
                index += 1
            blocks.append('<p class="code-block"><code>' + "<br/>".join(_escaped(value) for value in content)
                          + "</code></p>")
            index += int(index < len(lines))
            continue
        table = _table_start(lines, index)
        if table:
            header, divider = table
            rows = []
            index += 2
            while index < len(lines) and lines[index].strip():
                cells = _table_cells(lines[index])
                if cells is None:
                    break
                rows.append(cells)
                index += 1
            blocks.append(_table_html(header, rows, divider))
            continue
        if re.match(r"^\s*>", line):
            quote = []
            while index < len(lines) and re.match(r"^\s*>", lines[index]):
                quote.append(re.sub(r"^\s*> ?", "", lines[index]))
                index += 1
            blocks.append("<blockquote>" + _markdown_html("\n".join(quote), depth + 1) + "</blockquote>")
            continue
        item = list_item(line)
        if item:
            ordered = item[2] is not None
            tag = "ol" if ordered else "ul"
            next_number = int(item[2]) if ordered else 0
            blocks.append(f'<{tag} start="{next_number}">' if ordered else f"<{tag}>")
            while index < len(lines):
                current = list_item(lines[index])
                if not current or (current[2] is not None) != ordered:
                    break
                if ordered and int(current[2]) != next_number:
                    break
                content = [current[3]]
                index += 1
                while index < len(lines) and lines[index].startswith("  ") and not starts_block(index):
                    content.append(lines[index].strip())
                    index += 1
                blocks.append("<li>" + "<br/>".join(_inline(value) for value in content) + "</li>")
                next_number += 1
                while index < len(lines) and not lines[index].strip():
                    index += 1
            blocks.append(f"</{tag}>")
            continue
        content = [line]
        index += 1
        while index < len(lines) and not starts_block(index):
            content.append(lines[index])
            index += 1
        blocks.append("<p>" + "<br/>".join(_inline(value) for value in content) + "</p>")
    return "".join(blocks)


def markdown_pdf(markdown: str) -> bytes:
    """Paginate safe Markdown with Unicode fallback and full-width wide-table cards."""
    import pymupdf

    rendered = _markdown_html(markdown)
    if _UNRENDERABLE_SOURCE.search(markdown):
        rendered += ('<p class="render-note">일부 원문 추출 문자는 [U+XXXX] 코드포인트로 보존했습니다. '
                     '정확한 글자 모양은 해당 원문 파일에서 확인하세요.</p>')
    story = pymupdf.Story(html="<html><body>" + rendered + "</body></html>", user_css="""
        body { font-family: sans-serif; font-size: 10pt; line-height: 1.45; color: #183047; }
        h1 { font-size: 21pt; color: #123148; margin-top: 0; margin-bottom: 16pt; }
        h2 { font-size: 15pt; margin-top: 19pt; margin-bottom: 9pt; }
        h3 { font-size: 11.5pt; margin-top: 13pt; margin-bottom: 5pt; }
        h4, h5, h6 { font-size: 10.5pt; margin-top: 10pt; margin-bottom: 5pt; }
        p { margin-top: 0; margin-bottom: 6pt; }
        ul, ol { padding-left: 18pt; margin-top: 0; margin-bottom: 7pt; }
        li { margin-bottom: 4pt; }
        strong, th { font-weight: bold; }
        a { color: #126184; text-decoration: underline; }
        code { font-family: monospace; font-size: 9pt; color: #214d65; }
        .code-block { background-color: #f1f4f6; padding: 8pt; }
        blockquote { margin: 3pt 0 7pt 10pt; padding-left: 9pt;
                     border-left: 2pt solid #b6cbd7; color: #435969; }
        table { width: 100%; border-collapse: collapse; margin: 6pt 0 12pt 0; font-size: 9pt; }
        th { background-color: #e9f0f4; color: #163d57; text-align: left; }
        th, td { border: 0.5pt solid #cbd8e0; padding: 5pt; vertical-align: top; }
        .table-card { border-top: 1pt solid #bfd0da; margin: 5pt 0 9pt 0; padding-top: 6pt; }
        .table-field { margin: 0 0 3pt 0; }
        .render-note { margin-top: 12pt; color: #526675; font-size: 9pt; }
    """)
    def rectfn(index, filled):
        if index >= 250:
            raise ContractError("analysis PDF exceeds 250 pages")
        page = pymupdf.Rect(0, 0, 595, 842)
        return page, pymupdf.Rect(48, 54, 547, 786), None
    document = story.write_with_links(rectfn)
    try:
        if not document.page_count or not all(page.get_text().strip() for page in document):
            raise ContractError("analysis PDF has empty or unextractable pages")
        for page in document:
            page.insert_text((48, 816), f"SlideMaster | Research review | {page.number + 1} / {len(document)}", fontsize=8, color=(.35, .4, .45))
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
