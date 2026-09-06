"""Runner integration with real SQLite, attempt files and canonical adoption.

Codex transport and G4 rendering are explicit fixtures, never live AI evidence.
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from presentation_agents.v2.contracts import Conflict, ContractError, file_hash
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.provider import ProviderError
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.verification import VerificationCancelled
from test_v2_engine import intent

REPO = Path(__file__).resolve().parents[3]


class FakeProvider:
    def __init__(self, factory, start):
        self.factory, self.start = factory, start
        self.callback = None
        self.calls, self.responses, self.cancellations = [], [], []
        self.respond_action = None
        self.started = threading.Event()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def preflight(self):
        return {"ready": True, "reason": "explicit test provider"}

    def start_turn(self, **kwargs):
        self.calls.append(kwargs)
        self.callback = kwargs["on_event"]
        self.workspace = kwargs["cwd"]
        self.packet = json.loads((self.workspace / "input.json").read_text())
        self.start(self)
        self.started.set()
        return {"thread_id": f"test-thread-{len(self.factory.instances)}", "turn_id": "test-turn"}

    def emit(self, method, params=None, request_id=None):
        event = {"method": method, "params": params or {}}
        if request_id is not None:
            event["id"] = request_id
        self.callback(event)

    def finish(self, envelope=None, status="completed"):
        if envelope is not None:
            (self.workspace / "stage_result.json").write_text(json.dumps(envelope), encoding="utf-8")
        self.emit("turn/completed", {"turn": {"status": status}})

    def respond(self, request_id, response):
        self.responses.append((request_id, response))
        if self.respond_action:
            self.respond_action(request_id, response)

    def cancel(self, *ids):
        self.cancellations.append(ids)
        self.finish(status="interrupted")

    def close(self):
        self.closed = True
        if self.callback:
            self.emit("provider/error", {"message": "test provider closed"})


class ProviderFactory:
    def __init__(self):
        self.plans, self.instances = [], []

    def __call__(self):
        provider = FakeProvider(self, self.plans.pop(0))
        self.instances.append(provider)
        return provider


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / "control")
        self.run_id = self.engine.create("[TEST] 운영 개선", "제공 자료만으로 검토", "provided_only", "create")["id"]
        self.factory = ProviderFactory()
        self.runner = Runner(self.engine, REPO, provider_factory=self.factory)

    def tearDown(self):
        self.runner.close()
        if self.runner._worker:
            self.runner._worker.join(3)
            self.assertFalse(self.runner._worker and self.runner._worker.is_alive())
        self.temp.cleanup()

    def command(self, name, payload=None):
        state = self.engine.snapshot(self.run_id)
        return self.engine.command(self.run_id, name, payload or {}, f"op-{state['revision']}", state["revision"])

    def approve(self, gate):
        review = next(r for r in self.engine.snapshot(self.run_id)["reviews"] if r["gate"] == gate and r["status"] == "PENDING")
        return self.command("approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]})

    def launch(self, start):
        expected_count = len(self.factory.instances) + 1
        self.factory.plans.append(start)
        self.command("run")
        self.runner.kick()
        self.wait_for(lambda: len(self.factory.instances) == expected_count and self.factory.instances[-1].started.is_set())
        self.wait_for(lambda: self.engine.snapshot(self.run_id)["jobs"][-1].get("thread_id") or self.engine.snapshot(self.run_id)["jobs"][-1]["status"] not in {"RUNNING", "WAITING_USER", "QUEUED"})
        return self.factory.instances[-1]

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("runner did not reach the expected state within 3 seconds")

    def join(self):
        worker = self.runner._worker
        if worker:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        return self.engine.snapshot(self.run_id)

    def prepare_design(self):
        state = self.engine.publish(self.run_id, "intent", intent(1))
        self.approve("G1")
        self.engine.publish(self.run_id, "research", self.research(state["intent"]))
        self.approve("G2")
        workspace = Path(self.temp.name) / "direction-fixture"
        workspace.mkdir()
        (workspace / "preview.md").write_text("explicit test preview")
        mapping = self.engine.import_worker_artifacts(self.run_id, workspace, [{"key": "preview", "path": "preview.md", "kind": "preview"}], "design")
        self.engine.publish(self.run_id, "design_direction", {"route": "main-svg-generation", "summary": "test direction", "design_spec": "test spec", "spec_lock": "test lock", "preview_artifact_ids": [mapping["preview"]]})
        self.approve("G3")

    @staticmethod
    def research(contract):
        return {"sources": [], "claims": [], "packets": [], "messages": [{"slide_uid": p["uid"], "message": "제공된 검토 안건", "claim_ids": []} for p in contract["slides"]], "analysis": "# 검토\n합성 자료의 개선 제안입니다.", "limitations": ["test fixture; no empirical business claims"]}

    def test_real_stage_files_are_adopted_through_three_shared_stages(self):
        def produce(provider):
            phase = provider.packet["phase"]
            result = {"kind": "result", "data": intent(2) if phase == "intent" else self.research(provider.packet["intent"])}
            if phase == "design_direction":
                (provider.workspace / "preview.md").write_text("canonical preview from private attempt")
                result = {"kind": "result", "data": {"route": "main-svg-generation", "summary": "two-page design", "design_spec": "approved spec", "spec_lock": "approved lock", "preview_artifact_ids": ["artifact:preview"]}, "artifacts": [{"key": "preview", "path": "preview.md", "kind": "preview"}]}
            provider.emit("item/completed", {"item": {"type": "agentMessage", "text": "stage fixture completed"}})
            provider.finish(result)
        for gate in ("G1", "G2", "G3"):
            self.launch(produce)
            state = self.join()
            self.assertEqual(state["jobs"][-1]["status"], "COMPLETED")
            self.approve(gate)
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(state["active_phase"], "design_build")
        self.assertEqual(state["progress"]["percent"], 65)
        self.assertEqual(len({p.workspace for p in self.factory.instances}), 3)
        self.assertTrue(all(p.calls[0]["thread_id"] is None for p in self.factory.instances))
        artifact_id = state["direction"]["preview_artifact_ids"][0]
        self.assertEqual(self.engine.artifact_path(self.run_id, artifact_id).read_text(), "canonical preview from private attempt")
        self.assertTrue(all((p.workspace / "stage_result.json").is_file() for p in self.factory.instances))

    def test_changed_input_rejects_worker_result_and_retains_attempt(self):
        def produce(provider):
            self.command("message", {"content": "장수를 새로 검토해 주세요"})
            provider.finish({"kind": "result", "data": intent(2)})
        provider = self.launch(produce)
        state = self.join()
        self.assertIsNone(state["intent"])
        self.assertFalse(state["reviews"])
        self.assertNotEqual(state["jobs"][-1]["status"], "COMPLETED")
        self.assertTrue((provider.workspace / "stage_result.json").is_file())

    def test_retry_receives_exact_failure_and_unaccepted_draft_files(self):
        def invalid(provider):
            (provider.workspace / 'source.txt').write_text('preserved research bytes')
            provider.finish({'kind': 'result', 'data': {}, 'artifacts': [{'key': 'source', 'path': 'source.txt', 'kind': 'source'}]})
        self.launch(invalid)
        failed = self.join()
        self.assertEqual(failed['jobs'][-1]['status'], 'FAILED')
        def repaired(provider):
            prior = provider.packet['previous_attempt']
            self.assertFalse(prior['accepted'])
            self.assertEqual(prior['error'], failed['jobs'][-1]['error'])
            self.assertTrue((provider.workspace / prior['candidate_path']).is_file())
            self.assertEqual((provider.workspace / prior['artifacts'][0]['local_path']).read_text(), 'preserved research bytes')
            provider.finish({'kind': 'result', 'data': intent(2)})
        self.launch(repaired)
        accepted = self.join()
        self.assertEqual(accepted['status'], 'INTENT_REVIEW')
        self.assertEqual(accepted['jobs'][0]['status'], 'FAILED')
        self.assertEqual(accepted['jobs'][1]['status'], 'COMPLETED')
        self.assertFalse(accepted['findings'])

    def test_cancelled_turn_and_late_requests_cannot_become_failed_or_waiting(self):
        def produce(provider):
            self.command("cancel")
            provider.emit("item/tool/requestUserInput", {"questions": []}, request_id=0)
            provider.emit("serverRequest/resolved", {"requestId": 0})
            provider.finish({"kind": "result", "data": intent(2)}, status="interrupted")
        self.launch(produce)
        state = self.join()
        self.assertEqual(state["status"], "CANCELLED")
        self.assertEqual(state["jobs"][-1]["status"], "CANCELLED")
        self.assertFalse(state["questions"])
        self.assertIsNone(state["intent"])
        self.assertFalse(any(f.get("code") == "JOB_ERROR" for f in state["findings"]))

    def test_g4_cancellation_preserves_cancelled_job(self):
        self.prepare_design()
        def verification(*args, **kwargs):
            self.assertFalse(kwargs["cancelled"]())
            self.command("cancel")
            self.assertTrue(kwargs["cancelled"]())
            raise VerificationCancelled("test G4 cancelled")
        with patch("presentation_agents.v2.verification.verify_candidate", side_effect=verification) as verify:
            self.launch(lambda p: p.finish({"kind": "result", "data": {"pages": []}}))
            state = self.join()
        verify.assert_called_once()
        self.assertEqual(state["status"], "CANCELLED")
        self.assertEqual(state["jobs"][-1]["status"], "CANCELLED")
        self.assertIsNone(state["candidate"])

    def test_zero_request_response_is_replayable_after_provider_closes(self):
        provider = self.launch(lambda p: p.emit("item/tool/requestUserInput", {"questions": []}, request_id=0))
        def respond(request_id, response):
            provider.emit("serverRequest/resolved", {"requestId": request_id})
            provider.finish({"kind": "result", "data": intent(1)})
        provider.respond_action = respond
        state = self.engine.snapshot(self.run_id)
        payload = {"request_id": 0, "response": {"answers": {}}}
        self.runner.respond(self.run_id, payload, "answer-zero", state["revision"])
        state = self.join()
        question = state["questions"][-1]
        self.assertEqual(question["status"], "RESOLVED")
        self.assertTrue(question.get("resolved_at"))
        replay = self.runner.respond(self.run_id, payload, "answer-zero", state["revision"] - 100)
        self.assertEqual(len(provider.responses), 1)
        self.assertEqual(replay["jobs"][-1]["status"], "COMPLETED")
        with self.assertRaises(Conflict):
            self.runner.respond(self.run_id, {"request_id": 0, "response": {"answers": {"different": {"answers": ["yes"]}}}}, "answer-zero", state["revision"])
        original_resolved_at = question["resolved_at"]
        self.approve("G1")
        later = self.launch(lambda p: p.emit("item/tool/requestUserInput", {"questions": []}, request_id=0))
        self.runner.respond(self.run_id, payload, "answer-zero", state["revision"])
        self.assertFalse(later.responses, "an old operation must not answer the reused JSON-RPC id")
        later.emit("serverRequest/resolved", {"requestId": 0})
        latest = self.engine.snapshot(self.run_id)
        self.assertEqual(latest["questions"][0]["resolved_at"], original_resolved_at)
        self.assertEqual(latest["questions"][-1]["job_id"], latest["jobs"][-1]["id"])
        later.finish({"kind": "result", "data": self.research(later.packet["intent"])})
        self.assertEqual(self.join()["jobs"][-1]["status"], "COMPLETED")

    def test_duplicate_and_multiple_requests_preserve_wait_until_all_resolve(self):
        def start(provider):
            for request_id in (0, 0, 1):
                provider.emit("item/tool/requestUserInput", {"questions": []}, request_id=request_id)
        provider = self.launch(start)
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(len(state["questions"]), 2)
        provider.emit("serverRequest/resolved", {"requestId": 0})
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(state["jobs"][-1]["status"], "WAITING_USER")
        self.assertTrue(state["jobs"][-1].get("wait_started_at"))
        provider.emit("serverRequest/resolved", {"requestId": 1})
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(state["jobs"][-1]["status"], "RUNNING")
        self.assertNotIn("wait_started_at", state["jobs"][-1])
        self.assertTrue(all(q.get("resolved_at") and q.get("job_id") == state["jobs"][-1]["id"] for q in state["questions"]))
        provider.finish({"kind": "result", "data": intent(1)})
        self.assertEqual(self.join()["jobs"][-1]["status"], "COMPLETED")

    def test_ambiguous_response_delivery_is_not_resent_by_operation_retry(self):
        provider = self.launch(lambda p: p.emit("item/tool/requestUserInput", {"questions": []}, request_id=0))
        def fail(*args):
            provider.finish(status="failed")
            raise ProviderError("CONNECTION_LOST", "test response delivery is uncertain")
        provider.respond_action = fail
        state = self.engine.snapshot(self.run_id)
        payload = {"request_id": 0, "response": {"answers": {}}}
        with self.assertRaises(ContractError):
            self.runner.respond(self.run_id, payload, "uncertain", state["revision"])
        state = self.join()
        self.assertEqual(state["jobs"][-1]["status"], "INTERRUPTED")
        self.assertEqual(state["questions"][-1]["status"], "INTERRUPTED")
        self.assertTrue(state["questions"][-1].get("resolved_at"))
        self.runner.respond(self.run_id, payload, "uncertain", state["revision"] - 100)
        self.assertEqual(len(provider.responses), 1)

    def test_live_checkpoint_advances_only_valid_packets_and_imports_source_once(self):
        self.run_id = self.engine.create("[TEST] 출처 체크포인트", "외부 근거 확인", "external", "checkpoint-run")["id"]
        contract = intent(1)
        contract["slides"][0]["evidence_needed"] = ["fixture amount"]
        self.engine.publish(self.run_id, "intent", contract)
        baseline = self.approve("G1")
        def start(provider):
            (provider.workspace / "checkpoint.json").write_text("{")
        with patch.object(self.runner, "_poll_checkpoints", wraps=self.runner._poll_checkpoints) as poll:
            provider = self.launch(start)
            self.wait_for(lambda: poll.call_count >= 1)
            self.assertEqual(self.engine.snapshot(self.run_id)["progress"]["percent"], 20)
            source = provider.workspace / "source.txt"
            source.write_text("Fixture amount is 42.\n")
            requirement = provider.packet["intent"]["requirements"][0]["uid"]
            data = self.research(provider.packet["intent"])
            data.update(sources=[{"id": "source-1", "origin": "external", "url": "https://example.invalid/report", "artifact_id": "artifact:source", "sha256": file_hash(source), "title": "Synthetic test source", "accessed_at": "2026-09-06"}],
                claims=[{"id": "claim-1", "text": "Fixture amount is 42", "kind": "fact", "evidence_status": "SUPPORTED", "supports": [{"source_id": "source-1", "locator": "lines:1", "excerpt": "Fixture amount is 42."}]}],
                packets=[{"requirement_uid": requirement, "work_status": "DONE", "claim_ids": ["claim-1"]}])
            descriptors = [{"key": "source", "path": "source.txt", "kind": "source"}]
            envelope = {"kind": "checkpoint", "data": data, "artifacts": descriptors}
            folder = provider.workspace / "checkpoints"
            folder.mkdir()
            invalid = json.loads(json.dumps(envelope))
            invalid["data"]["packets"][0]["claim_ids"] = ["undefined-claim"]
            (folder / "01.json").write_text(json.dumps(invalid))
            self.wait_for(lambda: any(e["kind"] == "checkpoint.rejected" for e in self.engine.events(self.run_id)))
            self.assertEqual(self.engine.snapshot(self.run_id)["progress"]["percent"], 20)
            provider.emit("item/tool/requestUserInput", {"questions": []}, request_id=0)
            (folder / "01.json").write_text(json.dumps(envelope))
            before_poll = poll.call_count
            self.wait_for(lambda: poll.call_count > before_poll)
            self.assertEqual(self.engine.snapshot(self.run_id)["progress"]["percent"], 20)
            provider.emit("serverRequest/resolved", {"requestId": 0})
            self.wait_for(lambda: self.engine.snapshot(self.run_id)["progress"]["percent"] == 40)
            state = self.engine.snapshot(self.run_id)
            self.assertEqual(state["jobs"][-1]["status"], "RUNNING")
            self.assertEqual(state["content_revision"], baseline["content_revision"])
            self.assertFalse(any(r["gate"] == "G2" for r in state["reviews"]))
            before_poll = poll.call_count
            (provider.workspace / "checkpoint.json").write_text(json.dumps(envelope))
            self.wait_for(lambda: poll.call_count >= before_poll + 2)
            self.assertEqual(sum(e["kind"] == "candidate.research_checkpoint" for e in self.engine.events(self.run_id)), 1)
            provider.finish({**envelope, "kind": "result"})
            state = self.join()
        self.assertEqual(state["status"], "RESEARCH_REVIEW")
        self.assertEqual(state["progress"]["percent"], 50)
        self.assertEqual(state["jobs"][-1]["status"], "COMPLETED")
        self.assertEqual(sum(a["name"] == "source.txt" for a in state["artifacts"]), 1)
        self.assertEqual(state["research"]["sources"][0]["artifact_id"], next(a["id"] for a in state["artifacts"] if a["name"] == "source.txt"))


if __name__ == "__main__":
    unittest.main()
