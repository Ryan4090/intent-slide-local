"""G5 image-observation boundaries with real state and explicit G4 fixtures.

The fixture bytes are not a rendered presentation. Rendering and image provenance
are verified separately; these tests check which immutable images G5 must observe.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from presentation_agents.v2.contracts import ContractError, file_hash
import test_v2_design_checkpoints as checkpoints


class ReviewPageTests(unittest.TestCase):
    command = checkpoints.DesignCheckpointTests.command
    approve = checkpoints.DesignCheckpointTests.approve
    start_attempt = checkpoints.DesignCheckpointTests.start_attempt
    write_deck = checkpoints.DesignCheckpointTests.write_deck
    tearDown = checkpoints.DesignCheckpointTests.tearDown

    def setUp(self):
        checkpoints.DesignCheckpointTests.setUp(self)
        environment = patch("presentation_agents.v2.verification.receipt_environment_matches", return_value=True)
        environment.start()
        self.addCleanup(environment.stop)

    def candidate(self, *, per_page=True):
        artifacts = []
        outputs = [("candidate.pptx", "pptx"), ("candidate-grid.png", "contact_sheet")]
        if per_page:
            outputs += [(f"P{index:02d}.png", "render_page") for index in (1, 2)]
        for filename, kind in outputs:
            path = Path("deck/exports") / filename
            (self.workspace / path).write_bytes(f"STATE-FIXTURE:{filename}".encode())
            artifacts.append({"path": str(path), "kind": kind, "sha256": file_hash(self.workspace / path)})
        receipt = {
            "verdict": "PASS", "route": self.direction["route"], "renderer": "TEST_FIXTURE",
            "pptx_path": artifacts[0]["path"], "pptx_sha256": artifacts[0]["sha256"],
            "contact_sheet_path": artifacts[1]["path"], "contact_sheet_sha256": artifacts[1]["sha256"],
            "input_sha256": {}, "artifacts": artifacts,
        }
        state = self.engine.accept_candidate(self.run_id, self.workspace, self.data, receipt,
                                             job_id=self.job["id"], input_hash=self.job["input_hash"])
        self.assertEqual(state["progress"]["percent"], 95)
        self.job, workspace = self.start_attempt()
        packet = json.loads((workspace / "input.json").read_text())
        ids = set(state["candidate"]["artifact_ids"])
        current = [a for a in packet["artifacts"] if a["id"] in ids]
        self.contact = next(a["local_path"] for a in current if a["kind"] == "contact_sheet")
        self.pages = [a["local_path"] for a in current if a["kind"] == "render_page"]
        self.review = {"verdict": "PASS", "candidate_sha256": receipt["pptx_sha256"],
                       "contact_sheet_sha256": receipt["contact_sheet_sha256"],
                       "reviewed_slide_uids": [p["slide_uid"] for p in self.data["pages"]],
                       "findings": [], "summary": "State fixture review",
                       "limitations": ["Microsoft PowerPoint was not tested."]}

    def finish(self, images):
        return self.engine.accept_review(self.run_id, self.review, {"images_viewed": images},
                                         job_id=self.job["id"], input_hash=self.job["input_hash"])

    def assert_pending(self):
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(state["progress"]["percent"], 95)
        self.assertIsNone(state["release"])
        self.assertEqual(state["jobs"][-1]["status"], "RUNNING")

    def test_contact_sheet_alone_cannot_release_a_candidate_with_page_images(self):
        self.candidate()
        with self.assertRaisesRegex(ContractError, "page render"):
            self.finish([self.contact])
        self.assert_pending()

    def test_opening_only_a_subset_cannot_release(self):
        self.candidate()
        with self.assertRaisesRegex(ContractError, "page render"):
            self.finish([self.contact, self.pages[0]])
        self.assert_pending()

    def test_prior_candidate_images_with_same_names_do_not_count(self):
        self.candidate()
        prior_images = ["inputs/artifact-old--" + Path(path).name.split("--", 1)[1] for path in self.pages]
        with self.assertRaisesRegex(ContractError, "page render"):
            self.finish([self.contact, *prior_images])
        self.assert_pending()

    def test_all_current_images_release_and_preserve_verification_limits(self):
        self.candidate()
        state = self.finish([self.contact, *self.pages])
        self.assertEqual(state["status"], "COMPLETE")
        self.assertEqual(state["progress"]["percent"], 100)
        report = self.engine.artifact_path(self.run_id, state["release"]["review_artifact_id"])
        self.assertEqual(json.loads(report.read_text())["limitations"], self.review["limitations"])

    def test_pages_alone_do_not_replace_the_contact_sheet(self):
        self.candidate()
        with self.assertRaisesRegex(ContractError, "candidate render"):
            self.finish(self.pages)
        self.assert_pending()

    def test_legacy_without_page_images_retains_the_contact_sheet_boundary(self):
        self.candidate(per_page=False)
        state = self.finish([self.contact])
        self.assertEqual(state["status"], "COMPLETE")
        self.assertEqual(state["progress"]["percent"], 100)

    def test_observed_actionable_failure_does_not_need_a_complete_pass_scan(self):
        self.candidate()
        self.review.update(verdict="FAIL", findings=[{"slide_uid": self.data["pages"][0]["slide_uid"],
                           "issue": "Required page image could not be opened", "requested_fix": "Rerender page"}])
        state = self.finish([self.contact])
        self.assertEqual(state["status"], "REWORK")
        self.assertIsNone(state["release"])
        self.assertLess(state["progress"]["percent"], 95)


if __name__ == "__main__":
    unittest.main()
