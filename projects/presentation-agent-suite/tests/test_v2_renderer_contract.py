"""Renderer availability is a prerequisite declaration, never a rendering PASS."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from presentation_agents.v2 import verification


class RendererContractTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parents[1] / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="renderer-contract-", dir=runtime)
        self.root = Path(self.temp.name)
        script = self.root / "projects/presentation-agent-suite/scripts/render_quicklook_contact_sheet.swift"
        script.parent.mkdir(parents=True)
        script.write_text("// Fixture presence only; not a renderer execution")

    def tearDown(self):
        self.temp.cleanup()

    def test_macos_fallback_is_service_owned_without_claiming_render_success(self):
        with patch.object(verification.sys, "platform", "darwin"), patch.object(verification.legacy, "_officecli_bin", return_value=None), patch("shutil.which", side_effect=lambda name: f"/fixture/{name}"):
            result = verification.renderer_contract(self.root)
        self.assertEqual(result["status"], "PREREQUISITES_PRESENT")
        self.assertEqual(result["renderer"], "macos-quicklook-webkit")
        self.assertEqual(result["owner"], "service")
        self.assertEqual(result["gate"], "G4")
        self.assertEqual(result["verification_status"], "UNVERIFIED")
        self.assertTrue(result["actual_render_required"])

    def test_officecli_takes_precedence(self):
        with patch.object(verification.legacy, "_officecli_bin", return_value="/fixture/officecli"):
            result = verification.renderer_contract(self.root)
        self.assertEqual(result["renderer"], "officecli")
        self.assertEqual(result["verification_status"], "UNVERIFIED")

    def test_missing_runtime_or_unsupported_platform_cannot_advertise_fallback(self):
        for platform, binary in (("linux", "/fixture/bin"), ("darwin", None)):
            with self.subTest(platform=platform, binary=binary), patch.object(verification.sys, "platform", platform), patch.object(verification.legacy, "_officecli_bin", return_value=None), patch("shutil.which", return_value=binary):
                result = verification.renderer_contract(self.root)
                self.assertEqual(result["status"], "UNAVAILABLE")
                self.assertIsNone(result["renderer"])


if __name__ == "__main__":
    unittest.main()
