"""Provider selection across real store/queue/HTTP boundaries; CLI is a fixture."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from presentation_agents.v2.contracts import Conflict, ContractError, canonical, digest
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.server import create_app
from test_v2_runner import ProviderFactory, REPO


class ExecutionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name))
        self.run_id = self.engine.create("연결 검사", "합성 테스트", "provided_only", "create")["id"]
        self.factory = ProviderFactory()
        self.runner = Runner(self.engine, REPO, provider_factory=self.factory)

    def tearDown(self):
        self.runner.close()
        worker = self.runner._worker
        if worker:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.temp.cleanup()

    def command(self, name, payload=None):
        body = self.engine.snapshot(self.run_id)
        return self.engine.command(self.run_id, name, payload or {}, f"op-{body['revision']}", body["revision"])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("worker did not reach expected state")

    def ask(self):
        self.factory.plans.append(lambda p: p.finish({"kind": "question", "question": "누가 보나요?"}))
        self.command("run")
        self.runner.kick()
        self.wait_for(lambda: self.engine.snapshot(self.run_id)["jobs"][-1]["status"] == "COMPLETED")
        self.wait_for(lambda: self.runner._worker is None)
        return self.factory.instances[-1]

    def answer(self):
        question = self.engine.snapshot(self.run_id)["questions"][-1]
        return self.command("answer", {"question_id": question["id"], "answer": "합성 경영회의"})

    def test_queue_freezes_provider_and_rejects_switch_until_cancel(self):
        configured = self.command("configure_provider", {"provider": "claude", "model": "sonnet", "effort": "high"})
        queued = self.command("run")
        self.assertEqual(queued["jobs"][-1]["provider_selection"], configured["execution"])
        with self.assertRaises(Conflict):
            self.command("configure_provider", {"provider": "codex"})
        self.command("cancel")
        changed = self.command("configure_provider", {"provider": "codex"})
        self.assertEqual(changed["jobs"][-1]["provider_selection"]["provider"], "claude")
        self.assertEqual(changed["execution"]["provider"], "codex")

    def test_same_selection_resumes_but_provider_or_model_change_does_not(self):
        first = self.ask()
        self.assertIsNone(first.calls[0]["thread_id"])
        self.answer()
        second = self.ask()
        self.assertEqual(second.calls[0]["thread_id"], "test-thread-1")
        self.answer()
        self.command("configure_provider", {"provider": "codex", "model": "fixture-model-a", "effort": "high"})
        third = self.ask()
        self.assertIsNone(third.calls[0]["thread_id"])
        self.assertEqual((third.calls[0]["model"], third.calls[0]["effort"]), ("fixture-model-a", "high"))
        self.answer()
        self.command("configure_provider", {"provider": "codex", "model": "fixture-model-b", "effort": "high"})
        fourth = self.ask()
        self.assertIsNone(fourth.calls[0]["thread_id"])

    def test_registry_receives_queued_provider(self):
        self.command("configure_provider", {"provider": "codex"})
        self.runner.provider_factory = None
        with patch("presentation_agents.v2.runner.make_provider", side_effect=lambda selected: self.factory()) as factory:
            self.ask()
        factory.assert_called_once_with("codex")

    def test_late_response_cannot_cross_job_or_provider(self):
        self.factory.plans.append(lambda p: p.emit("item/tool/requestUserInput", {"questions": []}, request_id=0))
        self.command("run")
        self.runner.kick()
        self.wait_for(lambda: self.engine.snapshot(self.run_id)["questions"])
        body = self.engine.snapshot(self.run_id)
        question = body["questions"][-1]
        good = {"question_id": question["id"], "job_id": question["job_id"], "provider_id": "codex",
                "request_id": 0, "response": {"answers": {}}}
        for key, value in (("job_id", "previous-job"), ("provider_id", "claude"), ("question_id", "old-question")):
            with self.assertRaises(Conflict):
                self.runner.respond(self.run_id, {**good, key: value}, "bad-" + key, body["revision"])
        self.assertEqual(self.factory.instances[-1].responses, [])
        self.command("cancel")
        self.runner.cancel(self.run_id)

    def test_http_default_selection_refresh_and_identity_boundary(self):
        app = create_app(self.engine, Path(self.temp.name), self.runner, bootstrap_token="test-only")
        client = app.test_client()
        session = client.post("/api/v2/session", json={"bootstrap_token": "test-only"})
        headers = {"X-CSRF-Token": session.json["csrf_token"]}
        created = client.post("/api/v2/runs", headers=headers, json={"request": "테스트", "operation_id": "http-create"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["execution"]["provider"], "codex")
        self.assertEqual(client.post("/api/v2/capabilities/refresh", json={"provider": "codex"}).status_code, 403)
        self.factory.plans.append(lambda p: None)
        refreshed = client.post("/api/v2/capabilities/refresh", headers=headers, json={"provider": "codex"})
        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(next(p for p in refreshed.json["providers"] if p["id"] == "codex")["ready"])
        unbound = client.post(f"/api/v2/runs/{self.run_id}/commands", headers=headers,
                             json={"command": "provider_answer", "payload": {"request_id": 0, "response": {}}})
        self.assertEqual(unbound.status_code, 422)

    def test_invalid_provider_does_not_create_run(self):
        count = len(self.engine.list())
        with self.assertRaises(ContractError):
            self.engine.create("wrong", "wrong", "external", "bad-create", execution={"provider": "unknown"})
        self.assertEqual(len(self.engine.list()), count)

    def test_create_replay_binds_provider_model_and_effort(self):
        selected = {"provider": "claude", "model": "sonnet", "effort": "high"}
        first = self.engine.create("same", "same", "provided_only", "creation-op", execution=selected)
        replay = self.engine.create("same", "same", "provided_only", "creation-op", execution=selected)
        self.assertEqual(first["id"], replay["id"])
        for changed in ({"provider": "codex"}, {**selected, "model": "opus"}, {**selected, "effort": "medium"}):
            with self.assertRaises(Conflict):
                self.engine.create("same", "same", "provided_only", "creation-op", execution=changed)
        self.assertEqual(len(self.engine.list()), 2)

    def test_claude_design_references_are_local_regular_files(self):
        repo = Path(self.temp.name) / "code"
        for relative in (".claude/skills", ".codex/skills", "docs/rules"):
            (repo / relative).mkdir(parents=True)
            (repo / relative / "reference.md").write_text("owned reference")
        secret = Path(self.temp.name) / "unrelated.txt"
        secret.write_text("must not be copied")
        (repo / ".claude/skills/link.md").symlink_to(secret)
        self.runner.repo_root = repo
        self.command("configure_provider", {"provider": "claude"})
        self.command("run")
        job = self.engine.claim_job(self.run_id)
        job["phase"] = "design_direction"
        workspace = self.runner._prepare(self.run_id, job)
        self.assertEqual((workspace / "reference/.claude/skills/reference.md").read_text(), "owned reference")
        self.assertFalse((workspace / "reference/.claude/skills/link.md").exists())

    def test_legacy_creation_receipt_replays_default_only_without_rewrite(self):
        body = self.engine.store.read(self.run_id)
        body.pop("execution")
        legacy_hash = digest({k: body[k] for k in ("title", "request", "source_mode")})
        with self.engine.store.connect() as db:
            db.execute("UPDATE operations SET payload_sha=?,result=? WHERE run_id='' AND id='create'", (legacy_hash, canonical(body)))
            db.commit()
        same = self.engine.create(body["title"], body["request"], body["source_mode"], "create")
        self.assertEqual(same["id"], self.run_id)
        with self.assertRaises(Conflict):
            self.engine.create(body["title"], body["request"], body["source_mode"], "create", execution={"provider": "claude"})
        with self.engine.store.connect() as db:
            stored = db.execute("SELECT payload_sha,result FROM operations WHERE run_id='' AND id='create'").fetchone()
        self.assertEqual(stored, (legacy_hash, canonical(body)))

    def test_missing_or_symlinked_reference_directory_stops_claude_design(self):
        for case in ("missing", "symlink"):
            with self.subTest(case=case):
                repo = Path(self.temp.name) / case
                repo.mkdir()
                if case == "symlink":
                    (repo / ".claude").mkdir()
                    (repo / ".claude/skills").symlink_to(Path(self.temp.name), target_is_directory=True)
                self.runner.repo_root = repo
                self.command("configure_provider", {"provider": "claude"})
                self.command("run")
                job = self.engine.claim_job(self.run_id)
                job["phase"] = "design_direction"
                with self.assertRaises(ContractError):
                    self.runner._prepare(self.run_id, job)
                self.command("cancel")


if __name__ == "__main__":
    unittest.main()
