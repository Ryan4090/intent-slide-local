"""Native file-render regression; no browser or desktop interaction is used."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from presentation_agents.pipeline import _run_bounded_subprocess, _validate_png_path


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "requires macOS Swift file renderer")
class QuickLookBitmapTests(unittest.TestCase):
    def test_native_file_render_has_display_independent_pixels_and_preserves_aspect(self):
        suite = Path(__file__).resolve().parents[1]
        runtime = suite / ".runtime"
        runtime.mkdir(exist_ok=True)
        renderer = suite / "scripts/render_quicklook_contact_sheet.swift"
        with tempfile.TemporaryDirectory(dir=runtime, prefix="bitmap-test-") as temporary:
            root = Path(temporary)
            environment = dict(os.environ, SWIFT_MODULECACHE_PATH=str(root / "module-cache"),
                               CLANG_MODULE_CACHE_PATH=str(root / "module-cache"))
            for width, height in ((960, 540), (800, 600), (997, 563), (540, 1080)):
                with self.subTest(width=width, height=height):
                    html = root / f"{width}-{height}.html"
                    output = html.with_suffix(".png")
                    html.write_text(
                        '<!doctype html><html><body><div class="slide" '
                        f'style="width:{width}px;height:{height}px;background:#1f5b9d;'
                        'color:white;font:24px sans-serif">Pixel contract</div></body></html>',
                        encoding="utf-8",
                    )
                    result = _run_bounded_subprocess(
                        [shutil.which("swift"), str(renderer), str(html), str(output), "--slide-images-dir", str(root / f"pages-{width}")],
                        timeout=120, env=environment,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("rendered 1 slides", result.stdout)
                    page = root / f"pages-{width}" / 'P01.png'
                    _validate_png_path(page)
                    with Image.open(page) as full:
                        full_width = max(1920, width * 2)
                        self.assertEqual(full.size, (full_width, round(full_width * height / width)))
                        self.assertIn(f"snapshot pixels {full_width}x", result.stdout)
                        red, green, blue = full.convert("RGB").getpixel((full_width // 2, 200))
                        self.assertTrue(red < green < blue and red < 70 and blue > 130)
                    _validate_png_path(output)
                    with Image.open(output) as image:
                        self.assertEqual(image.size, (504, round(480 * height / width + 50)))
                        rgb = image.convert("RGB")
                        # Permit color-profile conversion while checking that
                        # the slide and surrounding light border keep their positions.
                        red, green, blue = rgb.getpixel((252, 100))
                        self.assertTrue(red < green < blue and red < 70 and blue > 130)
                        red, green, blue = rgb.getpixel((1, 1))
                        self.assertTrue(200 < red <= green <= blue)


if __name__ == "__main__":
    unittest.main()
