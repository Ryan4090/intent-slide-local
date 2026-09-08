"""Creation retries preserve the user's selected style and legacy receipts."""
import json
import tempfile
import unittest
from pathlib import Path

from presentation_agents.v2.contracts import Conflict, canonical, digest
from presentation_agents.v2.engine import Engine


class DesignCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def create(self, preference=None):
        return self.engine.create("같은 제목", "같은 요청", "external", "same-operation", design_preference=preference)

    def test_same_selected_style_replays_but_different_style_conflicts(self):
        original = self.create({"preset_id": "signal"})
        self.assertEqual(self.create({"preset_id": "signal"})["id"], original["id"])
        with self.assertRaises(Conflict):
            self.create({"preset_id": "pitch"})
        with self.assertRaises(Conflict):
            self.create(None)
        self.assertEqual(len(self.engine.list()), 1)

    def test_legacy_missing_preference_replays_without_rewriting_receipt(self):
        original = self.create()
        with self.engine.store.connect() as db:
            receipt = json.loads(db.execute("SELECT result FROM operations WHERE id='same-operation'").fetchone()[0])
            receipt.pop("design_preference")
            db.execute("UPDATE operations SET result=? WHERE id='same-operation'", (canonical(receipt),))
            db.commit()
        self.assertEqual(self.create(None)["id"], original["id"])
        with self.assertRaises(Conflict):
            self.create({"preset_id": "signal"})
        with self.engine.store.connect() as db:
            saved = json.loads(db.execute("SELECT result FROM operations WHERE id='same-operation'").fetchone()[0])
        self.assertNotIn("design_preference", saved)

    def test_old_receipt_without_execution_or_design_still_replays(self):
        original = self.create()
        with self.engine.store.connect() as db:
            receipt = json.loads(db.execute("SELECT result FROM operations WHERE id='same-operation'").fetchone()[0])
            receipt.pop("design_preference")
            receipt.pop("execution")
            legacy_hash = digest({key: receipt[key] for key in ("title", "request", "source_mode")})
            db.execute("UPDATE operations SET payload_sha=?,result=? WHERE id='same-operation'", (legacy_hash, canonical(receipt)))
            db.commit()
        self.assertEqual(self.create()["id"], original["id"])
        with self.assertRaises(Conflict):
            self.create({"preset_id": "signal"})


if __name__ == "__main__":
    unittest.main()
