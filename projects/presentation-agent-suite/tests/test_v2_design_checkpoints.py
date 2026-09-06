"""Exercise design checkpoints with real SQLite and isolated attempt files.

One test runs the shared SVG checker for real. Other checker results and final
G4/reviewer receipts are explicit state-machine fixtures, not a rendered deck.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from presentation_agents.v2.contracts import Conflict, ContractError, file_hash
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from test_v2_engine import intent
import test_v2_runner as runner_fixtures

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parents[1]
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
       'viewBox="0 0 1280 720"><rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>'
       '<text x="80" y="140" font-family="Pretendard" font-size="36" fill="#183047">'
       'Checkpoint fixture</text></svg>')
QUALITY = {"passed": True, "errors": [], "warnings": []}


class DesignCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="design-checkpoints-", dir=runtime)
        self.root = Path(self.temp.name)
        self.engine = Engine(self.root / "control")
        self.runner = Runner(self.engine, REPO)
        self.run_id = self.engine.create("[TEST] 페이지 체크포인트", "합성 제안 검토", "provided_only", "create")["id"]
        proposal = intent(2)
        proposal["fields"]["speaker_notes"]["value"] = True
        state = self.engine.publish(self.run_id, "intent", proposal)
        self.approve("G1")
        self.engine.publish(self.run_id, "research", runner_fixtures.RunnerTests.research(state["intent"]))
        self.approve("G2")
        proposal_folder = self.root / "proposal"
        proposal_folder.mkdir()
        (proposal_folder / "preview.md").write_text("Synthetic design proposal", encoding="utf-8")
        mapping = self.engine.import_worker_artifacts(
            self.run_id, proposal_folder, [{"key": "preview", "path": "preview.md", "kind": "preview"}], "design"
        )
        self.direction = {"route": "main-svg-generation", "summary": "Fixture direction",
                          "design_spec": "Approved fixture spec\n", "spec_lock": "Approved fixture lock\n",
                          "preview_artifact_ids": [mapping["preview"]]}
        self.engine.publish(self.run_id, "design_direction", self.direction)
        self.assertEqual(self.approve("G3")["progress"]["percent"], 65)
        self.job, self.workspace = self.start_attempt()
        self.data = self.write_deck(self.workspace)

    def tearDown(self) -> None:
        self.runner.close()
        self.temp.cleanup()

    def command(self, name: str, payload: dict | None = None) -> dict:
        state = self.engine.snapshot(self.run_id)
        return self.engine.command(self.run_id, name, payload or {}, f"op-{state['revision']}", state["revision"])

    def approve(self, gate: str) -> dict:
        state = self.engine.snapshot(self.run_id)
        review = next(item for item in state["reviews"] if item["gate"] == gate and item["status"] == "PENDING")
        return self.command("approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]})

    def start_attempt(self) -> tuple[dict, Path]:
        self.command("run")
        job = self.engine.claim_job(self.run_id)
        workspace = self.runner._prepare(self.run_id, job)
        self.engine.update_job(self.run_id, job["id"], {"workspace": str(workspace.relative_to(self.engine.root))},
                               "Fixture attempt prepared")
        return job, workspace

    def write_deck(self, workspace: Path) -> dict:
        project = workspace / "deck"
        for folder in ("svg_output", "notes", "exports"):
            (project / folder).mkdir(parents=True, exist_ok=True)
        for filename, key in (("design_spec.md", "design_spec"), ("spec_lock.md", "spec_lock")):
            (project / filename).write_text(self.direction[key], encoding="utf-8")
        pages = []
        for index, slide in enumerate(self.engine.snapshot(self.run_id)["intent"]["slides"], 1):
            svg_path = f"deck/svg_output/{index:02d}.svg"
            notes_path = f"deck/notes/{index:02d}.md"
            (workspace / svg_path).write_text(SVG, encoding="utf-8")
            (workspace / notes_path).write_text(f"Speaker notes for fixture page {index}\n", encoding="utf-8")
            pages.append({"slide_uid": slide["uid"], "path": svg_path, "claim_ids": [], "notes_path": notes_path})
        return {"project_path": "deck", "pages": pages}

    def verify(self, data: dict, *, real_checker: bool = False) -> dict:
        if real_checker:
            return self.runner._verify_design_checkpoint(self.run_id, self.job, self.workspace, data)
        with patch.object(self.runner, "_check_single_svg", return_value=QUALITY):
            return self.runner._verify_design_checkpoint(self.run_id, self.job, self.workspace, data)

    def accept(self, data: dict, receipt: dict | None = None) -> dict:
        return self.engine.accept_page_checkpoint(
            self.run_id, self.workspace, data, self.verify(data) if receipt is None else receipt,
            job_id=self.job["id"], input_hash=self.job["input_hash"],
        )

    def assert_unweighted(self, state: dict) -> None:
        self.assertEqual(state["progress"]["percent"], 65)
        self.assertIsNone(state["candidate"])
        self.assertIsNone(state["release"])
        for unit in state["units"]:
            if unit["id"].startswith("page.") or unit["id"] in {"G4", "G5"}:
                self.assertNotEqual(unit["status"], "VALID", unit["id"])

    def test_cumulative_pages_advance_execution_only_and_freeze_the_inspected_bytes(self) -> None:
        packet = json.loads((self.workspace / "input.json").read_text())
        self.assertEqual(packet["service_verification"]["owner"], "service")
        self.assertEqual(packet["service_verification"]["gate"], "G4")
        self.assertEqual(packet["service_verification"]["verification_status"], "UNVERIFIED")
        first = {**self.data, "pages": self.data["pages"][:1]}
        state = self.accept(first)
        progress = state["jobs"][-1]["execution"]["pages"]
        self.assertEqual((progress["completed"], progress["total"]), (1, 2))
        self.assertFalse(progress["weighted_progress"])
        self.assertEqual(progress["status"], "CURRENT")
        self.assert_unweighted(state)
        first_artifact = progress["page_records"][0]["artifact_id"]
        self.assertEqual(self.engine.artifact_path(self.run_id, first_artifact).read_text(), SVG)

        state = self.accept(self.data)
        progress = state["jobs"][-1]["execution"]["pages"]
        self.assertEqual(progress["completed"], 2)
        self.assertEqual(progress["page_records"][0]["artifact_id"], first_artifact)
        self.assertEqual(progress["slide_uids"], [page["slide_uid"] for page in self.data["pages"]])
        self.assertEqual(state["jobs"][-1]["status"], "RUNNING")
        self.assert_unweighted(state)
        with self.assertRaisesRegex(ContractError, "cumulative"):
            self.accept(first)
        with self.assertRaisesRegex(ContractError, "prefix"):
            self.verify({**self.data, "pages": list(reversed(self.data["pages"]))})
        previews = [item for item in state["artifacts"] if item.get("checkpoint_job_id")]
        self.assertTrue(previews)
        self.assertTrue(all(item["kind"] == "preview" and item["accepted"] is False for item in previews))
        (self.workspace / self.data["pages"][0]["path"]).write_text(SVG.replace("fixture", "changed"))
        self.assertEqual(self.engine.artifact_path(self.run_id, first_artifact).read_text(), SVG)
        self.assertEqual(self.engine.snapshot(self.run_id)["jobs"][-1]["execution"]["pages"]["completed"], 2)

    def test_cancelled_attempt_rejects_late_verification_and_acceptance(self) -> None:
        receipt = self.verify(self.data)
        self.command("cancel")
        with self.assertRaises(Conflict):
            self.verify(self.data)
        with self.assertRaises(Conflict):
            self.accept(self.data, receipt)
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(state["jobs"][-1]["status"], "CANCELLED")
        self.assertFalse(any(item.get("checkpoint_job_id") for item in state["artifacts"]))
        self.assert_unweighted(state)

    def test_changed_inputs_reject_a_previously_inspected_checkpoint(self) -> None:
        receipt = self.verify(self.data)
        self.command("message", {"content": "청중 변경을 검토해 주세요"})
        with self.assertRaises(Conflict):
            self.verify(self.data)
        with self.assertRaises(Conflict):
            self.accept(self.data, receipt)
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(state["active_phase"], "clarification")
        self.assertFalse(any(item.get("checkpoint_job_id") for item in state["artifacts"]))
        self.assert_unweighted(state)

    def test_invalid_notes_references_svg_and_paths_cannot_produce_receipts(self) -> None:
        first = {**self.data, "pages": [copy.deepcopy(self.data["pages"][0])]}
        missing_notes = copy.deepcopy(first)
        del missing_notes["pages"][0]["notes_path"]
        unknown_claim = copy.deepcopy(first)
        unknown_claim["pages"][0]["claim_ids"] = ["CLAIM-UNKNOWN"]
        external = copy.deepcopy(first)
        outside = self.root / "outside.svg"
        outside.write_text(SVG, encoding="utf-8")
        external["pages"][0]["path"] = str(outside)
        for label, data in (("missing notes", missing_notes), ("unknown claim", unknown_claim),
                            ("external path", external)):
            with self.subTest(case=label), self.assertRaises(ContractError):
                self.verify(data)

        page_path = self.workspace / first["pages"][0]["path"]
        invalid_svgs = ("<svg", SVG.replace('viewBox="0 0 1280 720"', 'viewBox="0 0 -1 720"'),
                        SVG.replace("</svg>", '<image href="https://example.invalid/remote.png"/></svg>'))
        for invalid in invalid_svgs:
            page_path.write_text(invalid, encoding="utf-8")
            with self.subTest(svg=invalid[:60]), self.assertRaises(ContractError):
                self.verify(first)
        page_path.unlink()
        page_path.symlink_to(outside)
        with self.assertRaises(ContractError):
            self.verify(first)
        page_path.unlink()
        page_path.write_text(SVG, encoding="utf-8")
        notes_path = self.workspace / first["pages"][0]["notes_path"]
        notes_path.write_text(" \n", encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "notes are empty"):
            self.verify(first)
        self.assert_unweighted(self.engine.snapshot(self.run_id))

    def test_real_shared_svg_checker_accepts_a_page_and_rejects_script_content(self) -> None:
        first = {**self.data, "pages": self.data["pages"][:1]}
        receipt = self.verify(first, real_checker=True)
        self.assertTrue(receipt["pages"][0]["quality"]["passed"])
        self.assertEqual(receipt["pages"][0]["quality"]["errors"], [])
        self.assertEqual(receipt["checker_sha256"],
                         file_hash(REPO / ".claude/skills/ppt-master/scripts/svg_quality_checker.py"))
        page_path = self.workspace / first["pages"][0]["path"]
        page_path.write_text(SVG.replace("</svg>", '<script>console.log("fixture")</script></svg>'), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "SVG quality failed"):
            self.verify(first, real_checker=True)

    def test_poller_adopts_once_and_rejects_incomplete_or_changing_files(self) -> None:
        first = {**self.data, "pages": self.data["pages"][:1]}
        checkpoint = self.workspace / "checkpoint.json"
        accepted, rejected, cache = set(), set(), {}
        checkpoint.write_text("{", encoding="utf-8")
        self.runner._poll_checkpoints(self.run_id, self.job, self.workspace, accepted, rejected, cache)
        self.assertNotIn("execution", self.engine.snapshot(self.run_id)["jobs"][-1])
        checkpoint.write_text(json.dumps({"kind": "checkpoint", "data": first}), encoding="utf-8")
        with patch.object(self.runner, "_check_single_svg", return_value=QUALITY):
            self.runner._poll_checkpoints(self.run_id, self.job, self.workspace, accepted, rejected, cache)
            current = self.engine.snapshot(self.run_id)
            self.assertEqual(current["jobs"][-1]["execution"]["pages"]["completed"], 1)
            self.runner._poll_checkpoints(self.run_id, self.job, self.workspace, accepted, rejected, cache)
            self.assertEqual(self.engine.snapshot(self.run_id)["revision"], current["revision"])
        checkpoint.write_text(json.dumps({"kind": "checkpoint", "data": {**self.data, "checkpoint_revision": 2}}),
                              encoding="utf-8")
        notes = self.workspace / first["pages"][0]["notes_path"]
        def concurrent_edit(*args):
            notes.write_text("[CLAIM: CLAIM-not-approved]", encoding="utf-8")
            return QUALITY
        with patch.object(self.runner, "_check_single_svg", side_effect=concurrent_edit):
            self.runner._poll_checkpoints(self.run_id, self.job, self.workspace, accepted, rejected, cache)
        current = self.engine.snapshot(self.run_id)
        self.assertEqual(current["jobs"][-1]["execution"]["pages"]["completed"], 1)
        self.assertTrue(any(item["code"] == "CHECKPOINT_REJECTED" for item in current["findings"]))
        self.assert_unweighted(current)

    def test_retry_carries_immutable_lineage_but_starts_with_no_count(self) -> None:
        prior = self.accept(self.data)
        prior_job = self.job
        prior_pages = prior["jobs"][-1]["execution"]["pages"]
        self.engine.update_job(self.run_id, prior_job["id"], {"status": "FAILED", "error": "Fixture export failed"},
                               "Fixture failed after source checkpoints")
        self.job, self.workspace = self.start_attempt()
        packet = json.loads((self.workspace / "input.json").read_text(encoding="utf-8"))
        lineage = packet["prior_page_checkpoint"]
        self.assertEqual(lineage["job_id"], prior_job["id"])
        self.assertFalse(lineage["accepted_for_current_attempt"])
        self.assertEqual(len(lineage["pages"]), 2)
        for page in lineage["pages"]:
            self.assertEqual(file_hash(self.workspace / page["local_path"]), page["sha256"])
            self.assertEqual(file_hash(self.workspace / page["notes_local_path"]), page["notes_sha256"])
        current = self.engine.snapshot(self.run_id)
        self.assertEqual(current["jobs"][-1].get("execution", {}).get("pages", {}).get("completed", 0), 0)
        self.assertFalse((self.workspace / "deck").exists())
        self.assert_unweighted(current)
        self.data = self.write_deck(self.workspace)
        current = self.accept({**self.data, "pages": self.data["pages"][:1]})
        self.assertEqual(current["jobs"][-1]["execution"]["pages"]["completed"], 1)
        self.assertEqual(current["jobs"][-2]["execution"]["pages"]["completed"], prior_pages["completed"])
        self.assert_unweighted(current)

    def test_retry_omits_missing_optional_previews_but_keeps_mandatory_inputs(self) -> None:
        prior = self.accept(self.data)
        checkpoint = prior["jobs"][-1]["execution"]["pages"]
        missing = checkpoint["page_records"][0]["artifact_id"]
        corrupted = checkpoint["page_records"][1]["notes_artifact_id"]
        self.engine.artifact_path(self.run_id, missing).unlink()
        self.engine.artifact_path(self.run_id, corrupted).write_text("changed optional notes")
        self.engine.update_job(self.run_id, self.job["id"], {"status": "FAILED", "error": "Fixture export failure"},
                               "Fixture failed after checkpoint")
        self.job, self.workspace = self.start_attempt()
        packet = json.loads((self.workspace / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(packet["prior_page_checkpoint"]["pages"], [])
        self.assertFalse(packet["prior_page_checkpoint"]["snapshot_available"])
        copied = {artifact["id"] for artifact in packet["artifacts"]}
        self.assertNotIn(missing, copied)
        self.assertNotIn(corrupted, copied)
        current = self.engine.snapshot(self.run_id)
        self.assertEqual(current["status"], "RUNNING")
        self.assertEqual(current["jobs"][-1].get("execution", {}).get("pages", {}).get("completed", 0), 0)
        self.assertEqual(current["jobs"][-2]["execution"]["pages"]["status"], "STALE")
        self.assertTrue(all(approval["valid"] for approval in current["approvals"]))
        self.assert_unweighted(current)
        self.command("cancel")
        mandatory = next(artifact for artifact in current["artifacts"] if artifact["kind"] == "direction")
        self.engine.artifact_path(self.run_id, mandatory["id"]).write_text("changed approved G3 input")
        with self.assertRaises(ContractError):
            self.start_attempt()

    def test_candidate_adoption_hashes_the_same_bytes_it_freezes(self) -> None:
        artifacts = []
        original = b"ORIGINAL-TRUSTED-STATE-FIXTURE-NOT-A-RENDERED-DECK"
        for relative, kind in (("deck/exports/candidate.pptx", "pptx"),
                               ("deck/exports/candidate-grid.png", "contact_sheet")):
            (self.workspace / relative).write_bytes(original)
            artifacts.append({"path": relative, "kind": kind, "sha256": file_hash(self.workspace / relative)})
        receipt = {"verdict": "PASS", "route": self.direction["route"], "renderer": "TEST_FIXTURE",
                   "pptx_path": artifacts[0]["path"], "pptx_sha256": artifacts[0]["sha256"],
                   "contact_sheet_path": artifacts[1]["path"], "contact_sheet_sha256": artifacts[1]["sha256"],
                   "input_sha256": {}, "artifacts": artifacts}
        target = self.workspace / artifacts[1]["path"]
        read_bytes = Path.read_bytes
        def swap_before_read(path):
            if path == target:
                path.write_bytes(b"CHANGED-AFTER-PRIOR-HASH-CHECK")
            return read_bytes(path)
        with patch("presentation_agents.v2.verification.receipt_environment_matches", return_value=True):
            with patch.object(Path, "read_bytes", swap_before_read):
                with self.assertRaisesRegex(ContractError, "output changed after verification"):
                    self.engine.accept_candidate(self.run_id, self.workspace, self.data, receipt,
                                                 job_id=self.job["id"], input_hash=self.job["input_hash"])
            rejected = self.engine.snapshot(self.run_id)
            self.assertIsNone(rejected["candidate"])
            self.assertEqual(rejected["progress"]["percent"], 65)
            target.write_bytes(original)
            accepted = self.engine.accept_candidate(self.run_id, self.workspace, self.data, receipt,
                                                    job_id=self.job["id"], input_hash=self.job["input_hash"])
            frozen = self.engine.artifact_path(self.run_id, accepted["candidate"]["contact_sheet_artifact_id"])
            self.assertEqual(frozen.read_bytes(), original)
            self.assertEqual(file_hash(frozen), receipt["contact_sheet_sha256"])

    def test_final_candidate_supersedes_previews_and_g5_remains_required(self) -> None:
        checkpoint = self.accept(self.data)
        preview_ids = checkpoint["jobs"][-1]["execution"]["pages"]["artifact_ids"]
        artifacts = []
        for page in self.data["pages"]:
            for key, kind in (("path", "page"), ("notes_path", "notes")):
                artifacts.append({"path": page[key], "kind": kind, "sha256": file_hash(self.workspace / page[key])})
        for path, kind in (("deck/exports/candidate.pptx", "pptx"), ("deck/exports/candidate-grid.png", "contact_sheet")):
            (self.workspace / path).write_bytes(b"TRUSTED-STATE-FIXTURE-NOT-A-RENDERED-DECK")
            artifacts.append({"path": path, "kind": kind, "sha256": file_hash(self.workspace / path)})
        receipt = {"verdict": "PASS", "route": self.direction["route"], "renderer": "TEST_FIXTURE",
                   "pptx_path": artifacts[-2]["path"], "pptx_sha256": artifacts[-2]["sha256"],
                   "contact_sheet_path": artifacts[-1]["path"], "contact_sheet_sha256": artifacts[-1]["sha256"],
                   "input_sha256": {item["path"]: item["sha256"] for item in artifacts}, "artifacts": artifacts}
        with patch("presentation_agents.v2.verification.receipt_environment_matches", return_value=True):
            state = self.engine.accept_candidate(self.run_id, self.workspace, self.data, receipt,
                                                  job_id=self.job["id"], input_hash=self.job["input_hash"])
            self.assertEqual(state["jobs"][-1]["execution"]["pages"]["status"], "SUPERSEDED")
            self.assertTrue(all(not item["valid"] for item in state["artifacts"] if item["id"] in preview_ids))
            self.assertEqual(state["progress"]["percent"], 95)
            self.assertEqual(state["active_phase"], "design_review")
            self.assertIsNone(state["release"])
            units = {unit["id"]: unit for unit in state["units"]}
            self.assertEqual(units["G4"]["status"], "VALID")
            self.assertNotEqual(units["G5"]["status"], "VALID")
            self.assertTrue(all(units[f"page.{page['slide_uid']}"]["status"] == "VALID" for page in self.data["pages"]))
            # Superseded previews retain history but no longer invalidate the current candidate.
            self.engine.artifact_path(self.run_id, preview_ids[0]).write_text("Historical preview altered")
            self.assertEqual(self.engine.snapshot(self.run_id)["progress"]["percent"], 95)


if __name__ == "__main__":
    unittest.main()
