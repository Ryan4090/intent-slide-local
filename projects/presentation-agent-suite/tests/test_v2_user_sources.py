"""Service-owned text provenance; fixtures do not substitute for real AI execution."""
from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from presentation_agents.v2.contracts import ContractError, validate_research
from presentation_agents.v2.engine import Engine
from test_v2_engine import intent

SUITE = Path(__file__).resolve().parents[1]
REQUEST = "합성 예시입니다.\n기준은 100분입니다.\n목표는 80분입니다."


class UserSourceTests(unittest.TestCase):
    def setUp(self):
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="user-source-test-", dir=runtime)
        self.root = Path(self.temporary.name)
        self.engine = Engine(self.root)
        self.state = self.engine.create("합성 업무 개선", REQUEST, "provided_only", "create")
        self.run_id = self.state["id"]
        self.state = self.engine.publish(self.run_id, "intent", intent(2))
        review = self.state["reviews"][-1]
        self.command("approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]})

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, command, payload=None, operation=None):
        self.state = self.engine.snapshot(self.run_id)
        self.state = self.engine.command(self.run_id, command, payload or {}, operation or f"op-{self.state['revision']}", self.state["revision"])
        return self.state

    def queued_source(self):
        self.command("run")
        sources = [a for a in self.state["artifacts"] if a["kind"] == "user_request"]
        self.assertEqual(len(sources), 1, "text-only requests need a service-owned source before research starts")
        return sources[0]

    def data(self, source):
        claims = []
        for key, value, line in (("baseline", 100, 2), ("target", 80, 3)):
            claims.append({"id": key, "text": f"사용자가 제시한 합성 {key} {value}분", "kind": "numeric",
                           "evidence_status": "PROVIDED", "value": value, "value_text": str(value),
                           "unit": "분", "population": "합성 사례", "as_of": "2026-09-06",
                           "limitations": ["실측 또는 외부 검증 수치가 아닙니다."],
                           "supports": [{"source_id": "request", "locator": f"lines:{line}", "excerpt": REQUEST.splitlines()[line - 1]}]})
        return {"sources": [{"id": "request", "origin": "provided", "title": "서비스에 입력한 사용자 원문",
                             "artifact_id": source["id"], "sha256": source["sha256"], "accessed_at": "2026-09-06"}],
                "claims": claims, "packets": [], "messages": [{"slide_uid": p["uid"], "message": "합성 예시를 검토합니다",
                    "claim_ids": ["baseline", "target"]} for p in self.state["intent"]["slides"]],
                "analysis": "제공한 합성 기준과 목표를 비교합니다.", "limitations": ["합성 예시이며 사실 검증 결과가 아닙니다."]}

    def validate(self, data, *, messages=None):
        body = self.engine.store.read(self.run_id)
        return validate_research(data, body["intent"], body["artifacts"], artifact_root=self.root,
                                 user_messages=body["messages"] if messages is None else messages)

    def test_existing_text_only_run_gets_exact_source_and_can_open_g2_without_an_attachment(self):
        approval = copy.deepcopy(self.state["approvals"][-1])
        source = self.queued_source()
        self.assertEqual(self.engine.artifact_path(self.run_id, source["id"]).read_bytes(), REQUEST.encode())
        self.assertEqual(source["sha256"], hashlib.sha256(REQUEST.encode()).hexdigest())
        self.assertFalse(any(a["kind"] == "attachment" for a in self.state["artifacts"]))
        self.assertEqual(self.state["approvals"][-1], approval)
        research_stage = next(s for s in self.state["stages"] if s["id"] == "research")
        self.assertIn(source["id"], [a["id"] for a in research_stage["inputs"]])
        self.assertNotIn(source["id"], [a["id"] for s in self.state["stages"] for a in s["outputs"]])
        job = self.engine.claim_job(self.run_id)
        adopted = self.engine.publish(self.run_id, "research", self.data(source), job_id=job["id"], input_hash=job["input_hash"])
        self.assertEqual(adopted["status"], "RESEARCH_REVIEW")
        self.assertEqual(adopted["progress"]["percent"], 50)
        self.assertTrue(all(c["evidence_status"] == "PROVIDED" for c in adopted["research"]["claims"]))
        self.assertEqual(adopted["research"]["claims"][0]["verification"]["semantic_entailment"], "UNVERIFIED")

    def test_resume_is_idempotent_and_records_only_user_answers_not_assistant_text(self):
        first = self.queued_source()
        self.command("cancel")
        self.engine.assistant_message(self.run_id, "모델이 작성한 임의 기준 999분")
        self.engine.add_question(self.run_id, "제약은 무엇입니까?", "합성 계획의 제약")
        question = self.engine.snapshot(self.run_id)["questions"][-1]
        self.command("answer", {"question_id": question["id"], "answer": "추가 인력 없이 진행합니다."})
        revision = self.state["revision"]
        resumed = self.engine.command(self.run_id, "resume", {}, "resume-once", revision)
        replay = self.engine.command(self.run_id, "resume", {}, "resume-once", revision)
        self.assertEqual(resumed, replay)
        sources = [a for a in resumed["artifacts"] if a["kind"] == "user_request"]
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["id"], first["id"])
        self.assertEqual({self.engine.artifact_path(self.run_id, a["id"]).read_text() for a in sources},
                         {REQUEST, "추가 인력 없이 진행합니다."})

    def test_worker_cannot_mint_user_request_or_relabel_even_an_exact_copy(self):
        source = self.queued_source()
        workspace = self.root / "worker"
        workspace.mkdir()
        (workspace / "copy.txt").write_text(REQUEST)
        descriptor = {"key": "copy", "path": "copy.txt", "kind": "user_request",
                      "provenance": source["provenance"]}
        with self.assertRaises(ContractError):
            self.engine.import_worker_artifacts(self.run_id, workspace, [descriptor], "research")
        descriptor["kind"] = "source"
        imported = self.engine.import_worker_artifacts(self.run_id, workspace, [descriptor], "research")
        data = self.data(source)
        data["sources"][0]["artifact_id"] = imported["copy"]
        with self.assertRaisesRegex(ContractError, "service-imported"):
            self.validate(data)

    def test_original_message_identity_bytes_and_source_hash_are_required(self):
        source = self.queued_source()
        data = self.data(source)
        messages = copy.deepcopy(self.engine.store.read(self.run_id)["messages"])
        for field, value in (("id", "unrelated-message"), ("role", "assistant"), ("content", "모델이 바꾼 원문 999분")):
            changed = copy.deepcopy(messages)
            changed[0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.validate(data, messages=changed)
        with self.assertRaises(ContractError):
            body = self.engine.store.read(self.run_id)
            validate_research(data, body["intent"], body["artifacts"], artifact_root=self.root)
        self.engine.artifact_path(self.run_id, source["id"]).write_text("changed bytes")
        with self.assertRaisesRegex(ContractError, "bytes changed"):
            self.validate(data)

    def test_user_statement_cannot_be_promoted_to_supported_fact(self):
        source = self.queued_source()
        data = self.data(source)
        data["claims"][0]["evidence_status"] = "SUPPORTED"
        with self.assertRaisesRegex(ContractError, "user statement"):
            self.validate(data)

    def test_user_statement_cannot_be_relabelled_as_an_external_fact(self):
        source = self.queued_source()
        data = self.data(source)
        data["sources"][0].update(origin="external", url="https://example.com/invented-source")
        data["claims"][0]["evidence_status"] = "SUPPORTED"
        data["claims"][1]["evidence_status"] = "SUPPORTED"
        body = self.engine.store.read(self.run_id)
        hybrid_intent = {**body["intent"], "source_mode": "hybrid"}
        with self.assertRaisesRegex(ContractError, "user statement"):
            validate_research(data, hybrid_intent, body["artifacts"], artifact_root=self.root, user_messages=body["messages"])

    def test_calculation_keeps_user_inputs_conditional_without_upgrading_them_to_fact(self):
        source = self.queued_source()
        data = self.data(source)
        data["claims"].append({"id": "gap", "text": "합성 기준 대비 목표 차이", "kind": "derived",
            "evidence_status": "SUPPORTED", "value": 20, "unit": "분", "population": "합성 사례", "as_of": "2026-09-06",
            "formula": "기준 - 목표", "input_claim_ids": ["baseline", "target"],
            "calculation": {"expression": "B-T", "bindings": {"B": "baseline", "T": "target"}}})
        with self.assertRaisesRegex(ContractError, "user statement"):
            self.validate(data)
        data["claims"][-1]["evidence_status"] = "PROVIDED"
        result = self.validate(data)
        self.assertEqual(result["claims"][-1]["verification"]["calculation"]["computed_value"], 20)


if __name__ == "__main__":
    unittest.main()
