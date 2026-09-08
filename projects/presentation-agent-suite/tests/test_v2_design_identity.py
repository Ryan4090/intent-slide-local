"""A selected design must be identified by the candidate before opening G3."""
import json
import unittest
from pathlib import Path

from presentation_agents.v2.contracts import ContractError
from test_v2_engine import intent
import test_v2_runner as fixtures


class DesignIdentityTests(unittest.TestCase):
    setUp = fixtures.RunnerTests.setUp
    tearDown = fixtures.RunnerTests.tearDown
    command = fixtures.RunnerTests.command
    approve = fixtures.RunnerTests.approve

    def candidate(self, *, selected=True):
        state = self.engine.publish(self.run_id, "intent", intent(1))
        self.approve("G1")
        self.engine.publish(self.run_id, "research", fixtures.RunnerTests.research(state["intent"]))
        self.approve("G2")
        if selected:
            self.command("select_design", {"preset_id": "signal"})
        workspace = Path(self.temp.name) / "direction-proposal"
        workspace.mkdir()
        (workspace / "preview.md").write_text("합성 디자인 방향 검증 예시", encoding="utf-8")
        mapping = self.engine.import_worker_artifacts(self.run_id, workspace,
            [{"key": "preview", "path": "preview.md", "kind": "preview"}], "design")
        return {"route": "main-svg-generation", "summary": "합성 디자인 방향",
                "design_spec": "검증 예시 명세", "spec_lock": "검증 예시 잠금",
                "preview_artifact_ids": [mapping["preview"]]}

    def test_missing_or_different_selection_cannot_publish_artifacts_or_open_g3(self):
        candidate = self.candidate()
        before = self.engine.snapshot(self.run_id)
        files_before = set((self.engine.root / "runs" / self.run_id / "artifacts").rglob("*"))
        for update in ({}, {"preset_id": "pitch"}):
            with self.subTest(update=update), self.assertRaises(ContractError):
                self.engine.publish(self.run_id, "design_direction", {**candidate, **update})
            after = self.engine.snapshot(self.run_id)
            self.assertEqual(after["revision"], before["revision"])
            self.assertEqual(after["artifacts"], before["artifacts"])
            self.assertEqual(after["reviews"], before["reviews"])
            self.assertIsNone(after["direction"])
            self.assertEqual(set((self.engine.root / "runs" / self.run_id / "artifacts").rglob("*")), files_before)

    def test_matching_selection_is_in_the_exact_artifact_awaiting_g3_approval(self):
        state = self.engine.publish(self.run_id, "design_direction", {**self.candidate(), "preset_id": "signal"})
        self.assertEqual(state["direction"]["preset_id"], "signal")
        self.assertEqual(state["status"], "DESIGN_DIRECTION_REVIEW")
        direction = next(a for a in state["artifacts"] if a["kind"] == "direction" and a["valid"])
        saved = json.loads(self.engine.artifact_path(self.run_id, direction["id"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["preset_id"], "signal")
        review = next(r for r in state["reviews"] if r["gate"] == "G3")
        self.assertEqual(review["status"], "PENDING")
        self.assertEqual(review["manifest"][direction["id"]], direction["sha256"])
        self.assertFalse(any(a["gate"] == "G3" for a in state["approvals"]))

    def test_existing_direction_shape_remains_valid_without_a_selection(self):
        state = self.engine.publish(self.run_id, "design_direction", self.candidate(selected=False))
        self.assertNotIn("preset_id", state["direction"])
        self.assertEqual(state["status"], "DESIGN_DIRECTION_REVIEW")


if __name__ == "__main__":
    unittest.main()
