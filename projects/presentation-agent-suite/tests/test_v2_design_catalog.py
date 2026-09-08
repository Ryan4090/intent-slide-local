"""Contract and shipped-resource checks for the product's design selection."""
from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from presentation_agents.v2.contracts import ContractError
from presentation_agents.v2.design_catalog import (
    get_design_preset,
    list_design_presets,
    normalize_preference,
)


SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parents[1]


class DesignCatalogTests(unittest.TestCase):
    def test_ten_distinct_product_directions_have_shipped_master_references(self):
        presets = list_design_presets()
        self.assertEqual(len(presets), 10)
        for key in ("id", "label", "description", "delivery_guidance", "preview_url"):
            self.assertEqual(len({preset[key] for preset in presets}), 10, key)
        for preset in presets:
            with self.subTest(preset=preset["id"]):
                self.assertRegex(preset["id"], r"^[a-z]+$")
                self.assertEqual(preset["reference_mode"], "structure_reference")
                self.assertEqual(preset["preview_kind"], "schematic")
                self.assertEqual(preset["canvas"], "16:9")
                self.assertTrue(preset["typography"])
                self.assertGreaterEqual(len(preset["best_for"]), 2)
                self.assertGreaterEqual(len(preset["visualization_patterns"]), 3)
                self.assertTrue(all(re.fullmatch(r"#[0-9A-F]{6}", color) for color in preset["palette"]))
                root = REPO / preset["template_root"]
                self.assertTrue(root.is_relative_to(REPO / ".claude/skills/ppt-master/templates/layouts"))
                self.assertFalse(root.is_symlink())
                self.assertTrue((root / "templates/design_spec.md").is_file())
                self.assertGreaterEqual(len(list((root / "templates").glob("*.svg"))), 1)

    def test_catalog_results_are_independent_of_callers(self):
        first = get_design_preset("signal")
        first["palette"].clear()
        first["label"] = "changed"
        listed = list_design_presets()
        listed[0]["best_for"].clear()
        listed.clear()
        current = get_design_preset("signal")
        self.assertEqual(current["label"], "시그널")
        self.assertEqual(len(current["palette"]), 3)
        self.assertGreater(len(current["best_for"]), 0)
        self.assertEqual(len(list_design_presets()), 10)

    def test_preference_preserves_unselected_legacy_runs_and_valid_choices(self):
        self.assertIsNone(normalize_preference(None))
        for preset in list_design_presets():
            value = {"preset_id": preset["id"]}
            result = normalize_preference(value)
            self.assertEqual(result, value)
            self.assertIsNot(result, value)

    def test_preference_rejects_unknown_ids_paths_and_untrusted_overrides(self):
        for value in ("signal", [], {}, 0, False,
                      {"preset_id": None}, {"preset_id": True}, {"preset_id": ["signal"]},
                      {"preset_id": "SIGNAL"}, {"preset_id": "unknown"},
                      {"preset_id": "../signal"}, {"preset_id": "/tmp/signal"},
                      {"preset_id": "signal", "template_root": "/tmp/untrusted"},
                      {"preset_id": "signal", "approved": True}):
            with self.subTest(value=value), self.assertRaises(ContractError):
                normalize_preference(value)
        for invalid in (None, False, [], {}, "", "../../x"):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                get_design_preset(invalid)

    def test_previews_are_local_accessible_nonactive_svg_assets(self):
        allowed_tags = {"svg", "title", "desc", "g", "rect", "path", "circle", "line", "text"}
        for preset in list_design_presets():
            with self.subTest(preset=preset["id"]):
                self.assertEqual(preset["preview_url"], f"/design-previews/{preset['id']}.svg")
                path = SUITE / "console" / preset["preview_url"].lstrip("/")
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("<!", content)
                self.assertIn("스타일 미리보기 · 예시 구조", content)
                root = ET.fromstring(content)
                self.assertEqual(root.attrib["viewBox"], "0 0 640 360")
                self.assertEqual(root.attrib["role"], "img")
                ids = {element.get("id") for element in root.iter() if element.get("id")}
                self.assertTrue(set(root.attrib["aria-labelledby"].split()).issubset(ids))
                for element in root.iter():
                    self.assertIn(element.tag.rsplit("}", 1)[-1], allowed_tags)
                    for attribute, value in element.attrib.items():
                        local_name = attribute.rsplit("}", 1)[-1].lower()
                        self.assertFalse(local_name.startswith("on"))
                        self.assertNotIn(local_name, {"href", "src", "style"})
                        self.assertNotIn("url(", value.lower())


if __name__ == "__main__":
    unittest.main()
