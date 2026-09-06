"""Guard numeric DrawingML frame widths without changing letter/CJK estimates.

The fixed advance fixtures below were measured from the bundled Pretendard OTFs
with PyMuPDF at 64 SVG px. Core regression checks need no font-measurement tool;
an additional check compares every supported bundled weight when available.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / ".claude/skills/ppt-master/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from svg_to_pptx.drawingml.context import ConvertContext  # noqa: E402
from svg_to_pptx.drawingml.elements import convert_text  # noqa: E402
from svg_to_pptx.drawingml.utils import EMU_PER_PX, estimate_text_width  # noqa: E402

FONTS = REPO / ".claude/skills/ppt-master/assets/fonts/Pretendard"
ASCII_ADVANCES_AT_64PX = {
    "400": (38.125, 28.0625, 37.5625, 39.5, 39.9375, 38.1875, 39.25, 35.3125, 38.8125, 39.25),
    "700": (42.25, 30.0, 39.0625, 40.875, 42.0625, 40.125, 41.125, 36.8125, 41.1875, 41.125),
}


class SvgTextWidthTests(unittest.TestCase):
    def test_p10_step_number_frame_covers_the_actual_pretendard_bold_advance(self) -> None:
        # P10's original 03 frame was 82.355px, below its actual 83.125px advance.
        for label, measured in (("01", 72.25), ("02", 81.3125), ("03", 83.125)):
            with self.subTest(label=label):
                element = ET.fromstring(
                    f'<text x="64" y="232" font-family="Pretendard" font-size="64" '
                    f'font-weight="700">{label}</text>'
                )
                shape = convert_text(element, ConvertContext())
                self.assertIsNotNone(shape)
                left, _, right, _ = shape.bounds_emu
                self.assertGreaterEqual((right - left) / EMU_PER_PX, measured)
                self.assertIn(f"<a:t>{label}</a:t>", shape.xml)

    def test_ascii_digit_estimates_cover_recorded_regular_and_bold_advances(self) -> None:
        for weight, advances in ASCII_ADVANCES_AT_64PX.items():
            for digit, advance in zip("0123456789", advances):
                with self.subTest(weight=weight, digit=digit):
                    self.assertGreaterEqual(estimate_text_width(digit, 64, weight), advance)
        # ExtraBold is the widest of the currently bundled faces: 0 = 43.625px.
        self.assertGreaterEqual(estimate_text_width("0", 64, "800"), 43.625)

    def test_unicode_digits_do_not_inherit_an_ascii_digit_width(self) -> None:
        for text in ("③", "❸", "²", "١", "𝟡", "１２"):
            with self.subTest(text=text):
                self.assertTrue(all(char.isdigit() for char in text))
                self.assertGreaterEqual(estimate_text_width(text, 64), 64 * len(text))

    def test_non_digit_estimates_keep_existing_letter_punctuation_and_cjk_widths(self) -> None:
        for text, expected_em in (("abcXYZ", 3.3), ("mMwWOQ%", 5.25), ("iIlj!|", 1.8),
                                  (" ", 0.3), (".-+", 1.65), ("한글", 2.0), ("漢字", 2.0), ("１２", 2.0)):
            with self.subTest(text=text):
                self.assertAlmostEqual(estimate_text_width(text, 64), expected_em * 64)
                self.assertAlmostEqual(estimate_text_width(text, 64, "700"), expected_em * 64 * 1.05)

    def test_estimates_cover_all_ascii_digits_in_each_bundled_pretendard_weight(self) -> None:
        try:
            import pymupdf
        except ImportError:
            self.skipTest("PyMuPDF unavailable; fixed measured-advance regressions still run")
        for face, weight in (("Light", "300"), ("Regular", "400"), ("Medium", "500"),
                             ("SemiBold", "600"), ("Bold", "700"), ("ExtraBold", "800")):
            path = FONTS / f"Pretendard-{face}.otf"
            self.assertTrue(path.is_file(), f"Bundled measurement fixture missing: {path.name}")
            font = pymupdf.Font(fontfile=str(path))
            for digit in "0123456789":
                with self.subTest(face=face, digit=digit):
                    self.assertTrue(font.has_glyph(ord(digit)))
                    self.assertGreaterEqual(estimate_text_width(digit, 64, weight),
                                            font.text_length(digit, fontsize=64))


if __name__ == "__main__":
    unittest.main()
