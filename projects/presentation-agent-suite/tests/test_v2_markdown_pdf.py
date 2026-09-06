"""Validate safe Markdown against real, in-memory PDF output."""

from __future__ import annotations

import re
import unittest

import pymupdf

from presentation_agents.v2.reporting import markdown_pdf


def compact(text: str) -> str:
    return re.sub(r"[\s\u200b]+", "", text)


def document_text(document: pymupdf.Document) -> str:
    # Footer separators and page breaks are layout, not the user's Markdown.
    return "\n".join(re.sub(r"SlideMaster \| Research review \| \d+ / \d+", "", page.get_text())
                     for page in document)


class MarkdownPdfTests(unittest.TestCase):
    def assert_inside_pages(self, document: pymupdf.Document) -> None:
        for page in document:
            words = page.get_text("words")
            self.assertTrue(words, f"page {page.number + 1} must have readable text")
            for word in words:
                with self.subTest(page=page.number + 1, word=word[4]):
                    self.assertGreaterEqual(word[0], -1)
                    self.assertGreaterEqual(word[1], -1)
                    self.assertLessEqual(word[2], page.rect.width + 1)
                    self.assertLessEqual(word[3], page.rect.height + 1)

    def test_pipe_table_preserves_cells_without_raw_delimiters(self) -> None:
        markdown = """# 비교 결과

| 구분 | 월 비용 | 판단 |
| :--- | ---: | :---: |
| 생활비 | 300만원 | 지출 확인 |
| 연금 | 180만원 | 수입 확인 |

표 이후 결론은 차액 120만원입니다.
"""
        with pymupdf.open(stream=markdown_pdf(markdown), filetype="pdf") as document:
            text = compact(document_text(document))
            for cell in ("구분", "월 비용", "판단", "생활비", "300만원", "지출 확인",
                         "연금", "180만원", "수입 확인", "표 이후 결론은 차액 120만원입니다."):
                self.assertIn(compact(cell), text)
            self.assertNotIn("|", text)
            self.assertNotIn("---", text)
            self.assertLess(text.index("생활비"), text.index("연금"))
            self.assertLess(text.index("연금"), text.index("표이후결론"))
            self.assert_inside_pages(document)

    def test_wide_long_table_uses_repeated_labels_and_retains_final_row(self) -> None:
        headers = ("Case", "Evidence", "Method", "Caveat", "Action")
        rows = [
            [f"CASE-{index:03d}", f"EVIDENCE-{index:03d} observed source details " * 3,
             f"METHOD-{index:03d} repeatable calculation " * 3,
             f"CAVEAT-{index:03d} scope and date limits " * 3, f"ACTION-{index:03d}"]
            for index in range(24)
        ]
        markdown = "\n".join([
            "# Wide evidence ledger", "", "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
            *("| " + " | ".join(row) + " |" for row in rows),
            "", "FINAL-AFTER-TABLE",
        ])
        with pymupdf.open(stream=markdown_pdf(markdown), filetype="pdf") as document:
            text = compact(document_text(document))
            self.assertGreater(document.page_count, 1)
            # The wide-table fallback must keep each field's label with each record.
            for header in headers:
                self.assertGreaterEqual(text.count(header), len(rows))
            for index in range(len(rows)):
                for prefix in ("CASE", "EVIDENCE", "METHOD", "CAVEAT", "ACTION"):
                    self.assertIn(f"{prefix}-{index:03d}", text)
            self.assertIn("FINAL-AFTER-TABLE", text)
            self.assertLess(text.index("CASE-000"), text.index("CASE-023"))
            self.assertLess(text.index("ACTION-023"), text.index("FINAL-AFTER-TABLE"))
            self.assertNotIn("|", text)
            self.assertNotIn("---", text)
            self.assert_inside_pages(document)

    def test_block_and_inline_markdown_render_in_order_with_visible_styles(self) -> None:
        markdown = """### Format example

Intro **BoldSignal** and `InlineCodeSignal`.

- BulletFirst
- BulletSecond

1. OrderedFirst
2. OrderedSecond

> QuoteSignal

```text
LiteralCode <tag> **literal-stars**
```

AfterCodeSignal
"""
        with pymupdf.open(stream=markdown_pdf(markdown), filetype="pdf") as document:
            text = compact(document_text(document))
            signals = ("Format example", "Intro", "BoldSignal", "InlineCodeSignal", "BulletFirst",
                       "BulletSecond", "OrderedFirst", "OrderedSecond", "QuoteSignal",
                       "LiteralCode<tag>**literal-stars**", "AfterCodeSignal")
            positions = [text.index(compact(signal)) for signal in signals]
            self.assertEqual(positions, sorted(positions))
            for raw_markup in ("###", "**BoldSignal**", "`InlineCodeSignal`", "```", ">QuoteSignal",
                               "-BulletFirst", "-BulletSecond"):
                self.assertNotIn(raw_markup, text)
            self.assertIn("1.OrderedFirst", text)
            self.assertIn("2.OrderedSecond", text)
            spans = [span for page in document for block in page.get_text("dict")["blocks"]
                     for line in block.get("lines", []) for span in line["spans"]]
            bold = next(span for span in spans if "BoldSignal" in span["text"])
            code = next(span for span in spans if "InlineCodeSignal" in span["text"])
            self.assertTrue(bold["flags"] & 16, "Markdown emphasis must use a bold PDF font")
            self.assertTrue(code["flags"] & 8, "Inline code must use a monospaced PDF font")
            self.assert_inside_pages(document)

    def test_only_explicit_https_links_are_active_and_assets_stay_inert(self) -> None:
        safe_url = "https://example.invalid/report?view=full&lang=ko#source"
        markdown = f"""# Link policy

[SafeSource]({safe_url})

[ScriptSource](javascript:alert%281%29)

[HttpSource](http://example.invalid/insecure)

[FileSource](file:///tmp/example.txt)

[RelativeSource](//example.invalid/relative)

![RemoteImage](https://example.invalid/image.png)

<a href="https://example.invalid/html">RawHtmlSource</a>

<img src="https://example.invalid/tracker.png">

<script>console.log("VisibleLiteral")</script>
"""
        with pymupdf.open(stream=markdown_pdf(markdown), filetype="pdf") as document:
            links = [link for page in document for link in page.get_links()]
            self.assertEqual([link.get("uri") for link in links], [safe_url])
            self.assertTrue(all(link["kind"] == pymupdf.LINK_URI for link in links))
            self.assertEqual(sum(len(page.get_images()) for page in document), 0)
            text = compact(document_text(document))
            for label in ("SafeSource", "ScriptSource", "HttpSource", "FileSource", "RelativeSource",
                          "RemoteImage", "RawHtmlSource", "VisibleLiteral"):
                self.assertIn(label, text)
            self.assertIn("<script>", text)
            self.assertIn("tracker.png", text)

    def test_escaped_and_code_pipes_remain_inside_their_table_cells(self) -> None:
        markdown = "\n".join([
            "| Kind | Value | Result |", "| --- | --- | --- |",
            r"| escaped | left\|right | ESCAPED-END |",
            "| inline | `alpha|beta` | CODE-END |",
            r"| both | `one|two` and three\|four | BOTH-END |",
        ])
        with pymupdf.open(stream=markdown_pdf(markdown), filetype="pdf") as document:
            text = compact(document_text(document))
            for expected in ("left|right", "alpha|beta", "one|twoandthree|four",
                             "ESCAPED-END", "CODE-END", "BOTH-END"):
                self.assertIn(expected, text)
            self.assertEqual(text.count("|"), 4)
            self.assertNotIn("\\|", text)
            self.assertNotIn("`", text)
            self.assertNotIn("---", text)
            self.assert_inside_pages(document)

    def test_very_long_code_and_url_wrap_without_losing_tail_or_page_bounds(self) -> None:
        code = "CodeStart_" + "A1B2C3D4" * 140 + "_CodeTail"
        url = "https://example.invalid/" + "longsegment0123456789" * 90 + "/UrlTail"
        markdown = f"# Long tokens\n\n```text\n{code}\n```\n\n{url}\n\nAFTER-LONG-TOKENS\n"
        with pymupdf.open(stream=markdown_pdf(markdown), filetype="pdf") as document:
            text = compact(document_text(document))
            self.assertIn(code, text)
            self.assertIn(url, text)
            self.assertIn("AFTER-LONG-TOKENS", text)
            self.assertFalse(any(page.get_links() for page in document), "Plain URLs remain inert")
            self.assert_inside_pages(document)


if __name__ == "__main__":
    unittest.main()
