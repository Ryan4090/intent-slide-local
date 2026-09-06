"""Approval context matching and limits, plus real Runner/SQLite integration.

Provider events and user decisions are explicit local fixtures. These checks
never contact Codex or grant an actual external tool permission.
"""

from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner, _ApprovalContextCache
from test_v2_engine import intent
import test_v2_runner as runner_fixtures

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parents[1]
FILE_APPROVAL = "item/fileChange/requestApproval"
COMMAND_APPROVAL = "item/commandExecution/requestApproval"


def encoded_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def event(item_id: str, changes: list[dict], **ids: str) -> dict:
    return {"threadId": "thread-fixture", "turnId": "turn-fixture", **ids,
            "item": {"id": item_id, "type": "fileChange", "changes": changes}}


def request(item_id: str, **ids: str) -> dict:
    return {"threadId": "thread-fixture", "turnId": "turn-fixture", "itemId": item_id, **ids}


class ApprovalContextCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="approval-context-", dir=runtime)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "attempt"
        self.workspace.mkdir()
        self.cache = _ApprovalContextCache("job-fixture", self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_context(self, context: dict, *, item_id: str, status: str) -> None:
        self.assertEqual(context["schema_version"], "approval-context.v1")
        self.assertEqual(context["job_id"], "job-fixture")
        self.assertEqual(context["item_id"], item_id)
        self.assertEqual(context["match_status"], status)
        self.assertEqual(context["workspace"], str(self.workspace))
        self.assertTrue(context["source"])
        self.assertIsInstance(context["notes"], list)
        self.assertLessEqual(encoded_size(context), 32 * 1024)

    def test_exact_file_event_keeps_diff_and_marks_move_outside_workspace(self) -> None:
        original = self.workspace / "source.py"
        destination = self.root / "moved.py"
        diff = "@@ -1 +1 @@\n-before\n+after\n"
        params = event("file-1", [{"path": str(original), "kind": {"type": "update", "move_path": str(destination)},
                                    "diff": diff}])
        pristine = copy.deepcopy(params)
        self.cache.record(params)
        context = self.cache.review(FILE_APPROVAL, request("file-1"))
        self.assert_context(context, item_id="file-1", status="MATCHED")
        self.assertEqual(context["item_type"], "fileChange")
        self.assertEqual(context["scope"], "outside")
        self.assertEqual(params, pristine, "Recording must not mutate the provider wire event")
        self.assertIsNone(context["command"])
        self.assertEqual(len(context["files"]), 1)
        file = context["files"][0]
        self.assertEqual(file["path"], str(original))
        self.assertEqual(file["resolved_path"], str(original.resolve()))
        self.assertEqual(file["scope"], "inside")
        self.assertEqual(file["kind"], "update")
        self.assertEqual(file["diff"], diff)
        self.assertTrue(file["diff_available"])
        self.assertFalse(file["diff_truncated"])
        self.assertFalse(file["path_truncated"])
        self.assertEqual(file["move_to"]["path"], str(destination))
        self.assertEqual(file["move_to"]["resolved_path"], str(destination.resolve()))
        self.assertEqual(file["move_to"]["scope"], "outside")

    def test_missing_diff_and_symlink_resolution_do_not_invent_evidence(self) -> None:
        inside = self.workspace / "inside.txt"
        outside = self.root / "outside.txt"
        inside.write_text("existing inside bytes", encoding="utf-8")
        outside.write_text("existing outside bytes", encoding="utf-8")
        link_inside = self.workspace / "inside-link.txt"
        link_outside = self.workspace / "outside-link.txt"
        link_inside.symlink_to(inside)
        link_outside.symlink_to(outside)
        changes = [{"path": str(path), "kind": {"type": "delete"}} for path in (link_inside, link_outside)]
        self.cache.record(event("no-diff", changes))
        context = self.cache.review(FILE_APPROVAL, request("no-diff"))
        self.assert_context(context, item_id="no-diff", status="MATCHED")
        self.assertEqual(context["scope"], "outside")
        self.assertEqual([file["scope"] for file in context["files"]], ["inside", "outside"])
        self.assertEqual([file["resolved_path"] for file in context["files"]], [str(inside), str(outside)])
        for file in context["files"]:
            self.assertFalse(file["diff_available"])
            self.assertIn(file["diff"], (None, ""))
            self.assertIsNone(file["move_to"])

    def test_relative_nul_and_truncated_paths_have_unknown_scope(self) -> None:
        paths = ["relative.txt", "bad\x00path", str(self.workspace) + "/" + "긴" * 1800]
        for index, path in enumerate(paths):
            item_id = f"uncertain-{index}"
            self.cache.record(event(item_id, [{"path": path, "kind": {"type": "add"}}]))
            context = self.cache.review(FILE_APPROVAL, request(item_id))
            with self.subTest(path_kind=index):
                self.assert_context(context, item_id=item_id, status="MATCHED")
                self.assertEqual(context["scope"], "unknown")
                file = context["files"][0]
                self.assertEqual(file["scope"], "unknown")
                self.assertLessEqual(len(file["path"].encode("utf-8")), 2048)
                if index == 2:
                    self.assertTrue(file["path_truncated"])
                    self.assertTrue(context["truncated"])

    def test_missing_mismatched_and_other_job_items_never_reuse_file_details(self) -> None:
        self.cache.record(event("known", [{"path": str(self.workspace / "known.py"),
                                            "kind": {"type": "update"}, "diff": "+known"}]))
        cases = [("absent", request("absent"), "MISSING"),
                 ("known", request("known", threadId="different-thread"), "MISMATCH"),
                 ("known", request("known", turnId="different-turn"), "MISMATCH")]
        for item_id, params, status in cases:
            with self.subTest(status=status, params=params):
                context = self.cache.review(FILE_APPROVAL, params)
                self.assert_context(context, item_id=item_id, status=status)
                self.assertEqual(context["scope"], "unknown")
                self.assertEqual(context["files"], [])
                self.assertIsNone(context["command"])
        other_job = _ApprovalContextCache("job-other", self.workspace)
        context = other_job.review(FILE_APPROVAL, request("known"))
        self.assertEqual(context["job_id"], "job-other")
        self.assertEqual(context["match_status"], "MISSING")
        self.assertEqual(context["files"], [])
        self.cache.record({**request("known"), "item": {"id": "known", "type": "commandExecution",
                                                       "command": "echo fixture", "cwd": str(self.workspace)}})
        context = self.cache.review(FILE_APPROVAL, request("known"))
        self.assert_context(context, item_id="known", status="MISMATCH")
        self.assertEqual(context["files"], [])
        self.assertIsNone(context["command"])

    def test_large_contexts_limit_utf8_diff_path_file_count_and_total_bytes(self) -> None:
        changes = [{"path": str(self.workspace / f"file-{index}.py"), "kind": {"type": "update"},
                    "diff": "추가한문장" * 3000} for index in range(25)]
        params = event("large", changes)
        self.cache.record(params)
        context = self.cache.review(FILE_APPROVAL, request("large"))
        self.assert_context(context, item_id="large", status="MATCHED")
        self.assertTrue(context["truncated"])
        self.assertLessEqual(len(context["files"]), 16)
        self.assertGreaterEqual(context["omitted_files"], 25 - len(context["files"]))
        self.assertTrue(context["files"])
        for file in context["files"]:
            self.assertTrue(file["diff_available"])
            self.assertTrue(file["diff_truncated"])
            self.assertLessEqual(len(file["diff"].encode("utf-8")), 4096)
        self.assertEqual(params["item"]["changes"][0]["diff"], "추가한문장" * 3000)

    def test_cache_evicts_old_entries_by_item_count_and_total_byte_budget(self) -> None:
        for index in range(33):
            self.cache.record(event(f"small-{index}", [{"path": str(self.workspace / f"file-{index}.py"),
                                                       "kind": {"type": "add"}, "diff": "+fixture"}]))
        self.assertEqual(self.cache.review(FILE_APPROVAL, request("small-0"))["match_status"], "MISSING")
        self.assertEqual(self.cache.review(FILE_APPROVAL, request("small-32"))["match_status"], "MATCHED")
        large_cache = _ApprovalContextCache("job-fixture", self.workspace)
        for index in range(16):
            changes = [{"path": str(self.workspace / f"large-{index}-{file}.py"), "kind": {"type": "update"},
                        "diff": "+" * 4096} for file in range(16)]
            large_cache.record(event(f"large-{index}", changes))
        self.assertEqual(large_cache.review(FILE_APPROVAL, request("large-0"))["match_status"], "MISSING")
        self.assertEqual(large_cache.review(FILE_APPROVAL, request("large-15"))["match_status"], "MATCHED")
        retained = [large_cache.review(FILE_APPROVAL, request(f"large-{index}")) for index in range(16)]
        self.assertLessEqual(sum(encoded_size(context) for context in retained if context["match_status"] == "MATCHED"),
                             256 * 1024)

    def test_command_cwd_is_descriptive_and_never_proves_the_commands_scope(self) -> None:
        command = "python -c 'print(\"fixture\")'"
        params = {"threadId": "thread-fixture", "turnId": "turn-fixture",
                  "item": {"id": "command-1", "type": "commandExecution", "command": command,
                           "cwd": str(self.workspace)}}
        self.cache.record(params)
        context = self.cache.review(COMMAND_APPROVAL, request("command-1"))
        self.assert_context(context, item_id="command-1", status="MATCHED")
        self.assertEqual(context["item_type"], "commandExecution")
        self.assertEqual(context["files"], [])
        self.assertEqual(context["scope"], "unknown")
        self.assertEqual(context["command"]["text"], command)
        self.assertEqual(context["command"]["cwd_scope"], "inside")
        self.assertEqual(context["command"]["resolved_cwd"], str(self.workspace))
        params["item"].update(id="command-2", command="명령" * 30000, cwd="relative-directory")
        self.cache.record(params)
        context = self.cache.review(COMMAND_APPROVAL, request("command-2"))
        self.assert_context(context, item_id="command-2", status="MATCHED")
        self.assertEqual(context["scope"], "unknown")
        self.assertEqual(context["command"]["cwd_scope"], "unknown")
        self.assertTrue(context["command"]["truncated"])
        self.assertTrue(context["truncated"])


class ApprovalContextRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="approval-runner-", dir=runtime)
        self.engine = Engine(Path(self.temp.name) / "control")
        self.run_id = self.engine.create("[TEST] 승인 맥락", "합성 이벤트 검증", "provided_only", "create")["id"]
        self.factory = runner_fixtures.ProviderFactory()
        self.runner = Runner(self.engine, REPO, provider_factory=self.factory)

    def tearDown(self) -> None:
        self.runner.close()
        worker = self.runner._worker
        if worker:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.temp.cleanup()

    def wait_for(self, condition) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Runner did not reach the expected state within 3 seconds")

    def test_request_zero_keeps_wire_params_and_waits_for_an_explicit_response(self) -> None:
        wire = {"threadId": "test-thread-1", "turnId": "test-turn", "itemId": "file-wire-0",
                "reason": "Fixture user decision required", "grantRoot": None,
                "futureField": {"keep": ["exactly", 0, False]}}
        original_wire = copy.deepcopy(wire)

        def start(provider):
            provider.emit("item/started", event(
                "file-wire-0", [{"path": str(provider.workspace / "candidate.txt"), "kind": {"type": "add"},
                                 "diff": "+explicit fixture\n"}], threadId="test-thread-1", turnId="test-turn"
            ))
            provider.emit(FILE_APPROVAL, wire, request_id=0)

        self.factory.plans.append(start)
        state = self.engine.snapshot(self.run_id)
        self.engine.command(self.run_id, "run", {}, "run-fixture", state["revision"])
        self.runner.kick()
        self.wait_for(lambda: bool(self.factory.instances) and self.factory.instances[0].started.is_set())
        self.wait_for(lambda: self.engine.snapshot(self.run_id)["jobs"][-1].get("thread_id"))
        provider = self.factory.instances[0]
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(state["jobs"][-1]["status"], "WAITING_USER")
        self.assertEqual(provider.responses, [], "Enrichment must never approve or respond automatically")
        question = state["questions"][-1]
        self.assertEqual(question["provider_request_id"], 0)
        self.assertEqual(set(question["provider_params"]), {"method", "params", "review_context"})
        self.assertEqual(question["provider_params"]["method"], FILE_APPROVAL)
        self.assertEqual(question["provider_params"]["params"], original_wire)
        self.assertEqual(wire, original_wire)
        context = question["provider_params"]["review_context"]
        self.assertEqual(context["match_status"], "MATCHED")
        self.assertEqual(context["job_id"], state["jobs"][-1]["id"])
        self.assertEqual(context["files"][0]["diff"], "+explicit fixture\n")
        self.assertEqual(context["scope"], "inside")

        def respond(request_id, response):
            provider.emit("serverRequest/resolved", {"requestId": request_id})
            provider.finish({"kind": "result", "data": intent(1)})

        provider.respond_action = respond
        response = {"decision": "accept"}
        self.runner.respond(self.run_id, {"request_id": 0, "response": response}, "explicit-fixture-answer", state["revision"])
        worker = self.runner._worker
        if worker:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        state = self.engine.snapshot(self.run_id)
        self.assertEqual(provider.responses, [(0, response)])
        self.assertEqual(state["jobs"][-1]["status"], "COMPLETED")
        self.assertEqual(state["questions"][-1]["provider_params"]["method"], FILE_APPROVAL)
        self.assertEqual(state["questions"][-1]["provider_params"]["params"], original_wire)


if __name__ == "__main__":
    unittest.main()
