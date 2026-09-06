"""SVG root style cascade through the real DrawingML converter.

Only the XML file read is replaced with an in-memory tree. No live project,
PPTX export, renderer or font installation is involved in these tests.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / ".claude/skills/ppt-master/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from svg_to_pptx.drawingml import converter

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


class SvgRootStylesTests(unittest.TestCase):
    def convert(self, attributes: str, children: str, *, merge: bool = True) -> dict:
        tree = ET.ElementTree(ET.fromstring(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
            + attributes + ">" + children + "</svg>"
        ))
        with patch.object(converter.ET, "parse", return_value=tree):
            xml = converter.convert_svg_to_slide_shapes(
                REPO / "projects/presentation-agent-suite/tests/root-style-fixture.svg",
                merge_paragraphs=merge,
            )[0]
        result = {}
        for run in ET.fromstring(xml).findall(".//a:r", NS):
            properties = run.find("a:rPr", NS)
            result[run.findtext("a:t", namespaces=NS)] = {
                "latin": properties.find("a:latin", NS).get("typeface"),
                "ea": properties.find("a:ea", NS).get("typeface"),
                "size": properties.get("sz"),
                "bold": properties.get("b"),
                "color": properties.find("a:solidFill/a:srgbClr", NS).get("val"),
            }
        self.assertTrue(result, "the converter must produce actual text runs")
        return result

    def test_root_attributes_reach_direct_and_group_text(self):
        runs = self.convert(
            'font-family="Pretendard" font-size="24" font-weight="700" fill="#17324D"',
            '<text x="60" y="100">Direct 한글</text>'
            '<g><g><text x="60" y="180">Nested 한글</text></g></g>',
        )
        self.assertEqual(set(runs), {"Direct 한글", "Nested 한글"})
        for run in runs.values():
            self.assertEqual(run, {"latin": "Pretendard", "ea": "Pretendard",
                                   "size": "1800", "bold": "1", "color": "17324D"})

    def test_root_inline_style_overrides_presentation_attributes(self):
        runs = self.convert(
            'font-family="Arial" font-size="12" fill="#000000" '
            'style="font-family: Pretendard; font-size: 28px; fill: #234567"',
            '<g><text x="60" y="100">Style 한글</text></g>',
        )
        self.assertEqual(runs["Style 한글"]["latin"], "Pretendard")
        self.assertEqual(runs["Style 한글"]["ea"], "Pretendard")
        self.assertEqual(runs["Style 한글"]["size"], "2100")
        self.assertEqual(runs["Style 한글"]["color"], "234567")

    def test_group_and_text_overrides_remain_more_specific(self):
        runs = self.convert(
            'style="font-family: Pretendard; font-size: 24px; fill: #123456"',
            '<g font-family="Arial" style="font-family: Pretendard Medium; fill: #345678">'
            '<text x="60" y="100">Group 한글</text>'
            '<text x="60" y="180" font-family="Pretendard SemiBold" fill="#456789">Text 한글</text>'
            '<text x="60" y="260" font-family="Arial" '
            'style="font-family: Pretendard Black; fill: #567890">Inline 한글</text></g>',
        )
        for label, family, color in [
            ("Group 한글", "Pretendard Medium", "345678"),
            ("Text 한글", "Pretendard SemiBold", "456789"),
            ("Inline 한글", "Pretendard Black", "567890"),
        ]:
            self.assertEqual(runs[label]["latin"], family)
            self.assertEqual(runs[label]["ea"], family)
            self.assertEqual(runs[label]["color"], color)

    def test_existing_explicit_text_family_keeps_its_output(self):
        text = ('<text x="60" y="100" font-family="Pretendard" '
                'font-size="24" fill="#17324D">Explicit 한글</text>')
        without_root = self.convert("", text)
        with_root = self.convert('font-family="Arial" fill="#FFFFFF"', text)
        self.assertEqual(with_root, without_root)
        self.assertEqual(with_root["Explicit 한글"]["latin"], "Pretendard")
        self.assertEqual(with_root["Explicit 한글"]["ea"], "Pretendard")

    def test_root_family_survives_both_tspan_export_modes(self):
        for merge in (True, False):
            with self.subTest(merge_paragraphs=merge):
                runs = self.convert(
                    'font-family="Pretendard" fill="#17324D"',
                    '<text x="60" y="100" font-size="24">'
                    '<tspan x="60" dy="0">첫 줄</tspan>'
                    '<tspan x="60" dy="36">둘째 줄</tspan></text>',
                    merge=merge,
                )
                for run in runs.values():
                    self.assertEqual(run["latin"], "Pretendard")
                    self.assertEqual(run["ea"], "Pretendard")
                    self.assertEqual(run["color"], "17324D")


if __name__ == "__main__":
    unittest.main()
