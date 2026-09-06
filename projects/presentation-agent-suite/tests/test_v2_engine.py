"""Behavioral regression tests for the shared, provider-independent engine."""
import copy
import tempfile
import unittest
from pathlib import Path

from presentation_agents.v2.engine import Engine, Conflict, ContractError


def intent(count=5):
    return {"fields": {k: {"value": v, "state": "proposed", "source": "test brief"} for k, v in {
        "topic": "공급망 운영 개선", "audience": "경영진", "objective": "우선순위 결정",
        "success_criteria": "실행 항목 확인", "slide_count": count, "speaker_notes": False,
    }.items()}, "slides": [{"title": f"검토 {i}", "purpose": "운영 개선 판단", "content": ["제공된 정보 검토"], "evidence_needed": []} for i in range(count)]}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name))
        self.run = self.engine.create("공급망 검토", "내부 자료로만 작성", "provided_only", "create-1")

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, command, payload=None, operation_id=None):
        s = self.engine.snapshot(self.run["id"])
        return self.engine.command(s["id"], command, payload or {}, operation_id or f"op-{s['revision']}", s["revision"])

    def approve(self, gate):
        s = self.engine.snapshot(self.run["id"])
        review = next(r for r in s["reviews"] if r["gate"] == gate and r["status"] == "PENDING")
        return self.command("approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]})

    def test_replay_conflict_and_cross_run_approval(self):
        s = self.engine.publish(self.run["id"], "intent", intent())
        review = s["reviews"][-1]
        payload = {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]}
        approved = self.engine.command(s["id"], "approve", payload, "same", s["revision"])
        replay = self.engine.command(s["id"], "approve", payload, "same", s["revision"])
        self.assertEqual(approved["revision"], replay["revision"])
        with self.assertRaises(Conflict):
            self.engine.command(s["id"], "approve", {**payload, "x": 1}, "same", s["revision"])
        other = self.engine.create("별도", "별도", "hybrid", "create-2")
        with self.assertRaises(ContractError):
            self.engine.command(other["id"], "approve", payload, "alien", other["revision"])

    def test_variable_page_count_empty_evidence_and_progress(self):
        for count in (5, 10, 12):
            e = Engine(Path(self.tmp.name) / str(count))
            r = e.create("주제 독립", "검토", "provided_only", "create")
            s = e.publish(r["id"], "intent", intent(count))
            self.assertEqual(len(s["intent"]["slides"]), count)
            self.assertEqual(s["progress"]["percent"], 15)
            self.assertFalse(s["intent"]["requirements"])
        self.engine.publish(self.run["id"], "intent", intent())
        self.assertEqual(self.approve("G1")["progress"]["percent"], 20)

    def test_stale_screen_and_tampered_approval_bundle(self):
        s = self.engine.publish(self.run["id"], "intent", intent())
        self.command("message", {"content": "추가 메모"})
        with self.assertRaises(Conflict):
            self.engine.command(s["id"], "message", {"content": "stale"}, "stale", s["revision"])
        artifact = s["artifacts"][0]
        self.engine.artifact_path(s["id"], artifact["id"]).write_text("tampered")
        with self.assertRaises(ContractError):
            self.approve("G1")
        self.assertLess(self.engine.snapshot(s["id"])["progress"]["percent"], 15)

    def test_reordering_preserves_uids_and_reopens_intent(self):
        s = self.engine.publish(self.run["id"], "intent", intent())
        self.approve("G1")
        self.command("request_changes", {"stage": "intent", "reason": "순서 변경"})
        changed = copy.deepcopy(s["intent"])
        changed["slides"].reverse()
        revised = self.engine.publish(s["id"], "intent", changed)
        self.assertEqual(revised["intent"]["slides"][0]["uid"], s["intent"]["slides"][-1]["uid"])
        self.assertEqual(revised["intent"]["slides"][0]["display_id"], "P01")
        self.assertEqual(revised["status"], "INTENT_REVIEW")
        self.assertFalse(any(a["valid"] for a in revised["approvals"]))

    def test_provider_cannot_submit_via_user_commands(self):
        with self.assertRaises(ContractError):
            self.command("publish", {"stage": "intent", "data": intent()})

    def test_restart_preserves_events_and_interrupted_job(self):
        self.command("run")
        lease = self.engine.claim_job(self.run["id"])
        self.assertTrue(lease["id"])
        other = Engine(Path(self.tmp.name))
        other.recover()
        s = other.snapshot(self.run["id"])
        self.assertEqual(s["jobs"][-1]["status"], "INTERRUPTED")
        self.assertEqual(other.events(self.run["id"])[-1]["kind"], "job.interrupted")

    def test_zero_provider_request_cannot_be_answered_as_interview_and_cancels(self):
        self.command("run")
        self.engine.claim_job(self.run["id"])
        self.engine.add_question(self.run["id"], "provider question", "tool", provider_request_id=0)
        question = self.engine.snapshot(self.run["id"])["questions"][-1]
        with self.assertRaises(ContractError):
            self.command("answer", {"question_id": question["id"], "answer": "yes"})
        cancelled = self.command("cancel")
        self.assertEqual(cancelled["questions"][-1]["status"], "CANCELLED")
        self.assertTrue(cancelled["questions"][-1]["resolved_at"])

    def test_attachment_is_idempotent_and_legacy_read_only(self):
        rid = self.run["id"]
        first = self.engine.add_attachment(rid, "input.txt", b"provided", "upload-1", 1)
        retry = self.engine.add_attachment(rid, "input.txt", b"provided", "upload-1", 1)
        self.assertEqual(first["revision"], retry["revision"])
        with self.assertRaises(ContractError):
            self.engine.add_attachment(rid, "input.txt", b"provided", "", first["revision"])
        self.engine.store.mutate(rid, "fixture.legacy", {}, lambda b: b.update(legacy={"fixture": True}))
        with self.assertRaises(ContractError):
            self.engine.add_attachment(rid, "input.txt", b"provided", "upload-2", first["revision"] + 1)

    def test_research_checkpoint_advances_only_processed_work_and_keeps_job(self):
        proposal = intent(2)
        for page in proposal["slides"]:
            page["evidence_needed"] = [{"question": "자료로 검증할 내용"}]
        s = self.engine.publish(self.run["id"], "intent", proposal)
        self.approve("G1")
        self.command("run")
        job = self.engine.claim_job(s["id"])
        checkpoint = {"sources": [], "claims": [], "messages": [], "packets": [{"requirement_uid": s["intent"]["requirements"][0]["uid"], "work_status": "UNAVAILABLE", "claim_ids": [], "attempts": ["제공 자료에 해당 수치 없음"], "limitations": ["사실 단정에서 제외"]}]}
        partial = self.engine.publish(s["id"], "research_checkpoint", checkpoint, job_id=job["id"], input_hash=job["input_hash"])
        self.assertEqual(partial["progress"]["percent"], 30)
        self.assertEqual(partial["jobs"][-1]["status"], "RUNNING")
        self.assertEqual(self.engine.input_hash(partial), job["input_hash"])
        self.assertFalse(any(r["gate"] == "G2" for r in partial["reviews"]))


if __name__ == "__main__":
    unittest.main()
