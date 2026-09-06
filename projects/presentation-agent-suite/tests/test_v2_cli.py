"""Integration checks at the legacy launcher's new run.json dispatch boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from presentation_agents.v2.engine import Engine

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parents[1]
LAUNCHER = SUITE / "scripts/presentation_agents.py"


class V2CliTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="cli-adapter-test-", dir=runtime)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.engine = Engine(self.root / "state")
        self.run = self.engine.create("CLI 계약 경계", "가역적 통합 검증", "provided_only", "fixture-create")
        self.metadata = {"schema_version":"2.0.0", "run_id":self.run["id"],
                         "project_id":self.run["project_id"], "state_store":str(self.engine.root)}
        self.write_metadata(self.metadata)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_metadata(self, value: dict) -> None:
        (self.project / "run.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def cli(self, command: str, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONPATH":str(SUITE / "src")}
        return subprocess.run([sys.executable, str(LAUNCHER), command, str(self.project), *args],
                              cwd=REPO, env=env, text=True, capture_output=True, timeout=5)

    def success(self, command: str, *args: str) -> dict | list:
        result = self.cli(command, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def mutate(self, command: str, operation_id: str, revision: int, *args: str) -> dict:
        return self.success(command, "--operation-id", operation_id,
                            "--expected-revision", str(revision), *args)

    def test_status_progress_and_events_return_current_v2_contract(self) -> None:
        status = self.success("status")
        self.assertEqual(status["schema_version"], "2.0.0")
        self.assertEqual(status["id"], self.run["id"])
        self.assertEqual(status["revision"], self.run["revision"])
        self.assertEqual(self.success("progress"), status["progress"])
        events = self.success("events", "--after", "0")
        self.assertEqual(events[-1]["kind"], "run.created")
        self.assertEqual(events[-1]["run_id"], self.run["id"])
        self.assertEqual(self.success("events", "--after", str(events[-1]["seq"])), [])
        self.assertEqual(self.engine.snapshot(self.run["id"])["revision"], self.run["revision"], "read adapters must not mutate the aggregate")

    def test_real_cli_mutation_replay_and_cas_use_the_shared_store(self) -> None:
        queued = self.mutate("run", "queue-once", self.run["revision"])
        self.assertEqual(queued["revision"], self.run["revision"] + 1)
        self.assertEqual(queued["jobs"][-1]["status"], "QUEUED")
        event_count = len(self.engine.events(self.run["id"]))
        replay = self.mutate("run", "queue-once", self.run["revision"])
        self.assertEqual(replay["revision"], queued["revision"])
        self.assertEqual(replay["jobs"][-1]["id"], queued["jobs"][-1]["id"])
        self.assertEqual(len(self.engine.events(self.run["id"])), event_count)
        stale = self.cli("cancel", "--operation-id", "stale-cancel", "--expected-revision", str(self.run["revision"]))
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("갱신", stale.stderr)
        mismatched = self.cli("cancel", "--operation-id", "queue-once", "--expected-revision", str(queued["revision"]))
        self.assertNotEqual(mismatched.returncode, 0)
        self.assertIn("reused", mismatched.stderr)
        self.assertEqual(self.engine.snapshot(self.run["id"])["revision"], queued["revision"])

    def test_cancel_resume_and_request_changes_dispatch_to_v2(self) -> None:
        state = self.mutate("run", "run-1", self.run["revision"])
        state = self.mutate("cancel", "cancel-1", state["revision"])
        self.assertEqual(state["status"], "CANCELLED")
        state = self.mutate("resume", "resume-1", state["revision"])
        self.assertEqual(state["status"], "QUEUED")
        self.assertEqual(len(state["jobs"]), 2)
        state = self.mutate("cancel", "cancel-2", state["revision"])
        state = self.mutate("request-changes", "change-1", state["revision"],
                            "--stage", "intent", "--reason", "청중과 메시지를 다시 확인합니다")
        self.assertEqual(state["active_stage"], "intent")
        self.assertEqual(state["changes"][-1]["reason"], "청중과 메시지를 다시 확인합니다")
        self.assertEqual(state["revision"], self.run["revision"] + 5)

    def test_v2_reference_cannot_fall_through_to_legacy_mutator(self) -> None:
        original = (self.project / "run.json").read_bytes()
        result = self.cli("start-research")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("v2", result.stderr)
        self.assertEqual((self.project / "run.json").read_bytes(), original)
        self.assertEqual(self.engine.snapshot(self.run["id"])["revision"], self.run["revision"])

    def test_external_state_store_and_symlink_escape_are_rejected_before_creation(self) -> None:
        external = REPO.parent / f"slidemaster-cli-denied-{uuid.uuid4().hex}"
        self.assertFalse(external.exists())
        alias = self.root / "external-store-link"
        alias.symlink_to(external, target_is_directory=True)
        for target in (external, alias):
            with self.subTest(target=str(target)):
                self.write_metadata({**self.metadata, "state_store":str(target)})
                result = self.cli("status")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("inside SlideMaster", result.stderr)
                self.assertFalse(external.exists(), "untrusted metadata must not create an external SQLite store")
        linked_store = self.root / "linked-database-store"
        linked_store.mkdir()
        (linked_store / "control.sqlite").symlink_to(self.engine.store.path)
        self.write_metadata({**self.metadata, "state_store":str(linked_store)})
        linked = self.cli("status")
        self.assertNotEqual(linked.returncode, 0, "the SQLite file itself must not redirect access through a symlink")
        self.assertIn("control.sqlite", linked.stderr)
        self.assertEqual(self.engine.snapshot(self.run["id"])["revision"], self.run["revision"])

    def test_schema_and_required_mutation_arguments_are_checked_without_state_changes(self) -> None:
        missing = self.cli("run")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("--operation-id", missing.stderr)
        self.assertIn("--expected-revision", missing.stderr)
        for overrides, expected in (
            ({"schema_version":"1.0.0"}, "schema_version"),
            ({"state_store":{}}, "state_store"),
            ({"state_store":[]}, "state_store"),
            ({"state_store":"relative-store-must-not-be-created"}, "absolute path"),
            ({"state_store":str(self.root / "missing-store")}, "existing control.sqlite"),
            ({"run_id":[]}, "run_id"),
        ):
            with self.subTest(overrides=overrides):
                self.write_metadata({**self.metadata, **overrides})
                invalid = self.cli("status")
                self.assertNotEqual(invalid.returncode, 0)
                self.assertIn(expected, invalid.stderr)
        self.assertFalse((self.root / "missing-store").exists())
        self.assertEqual(self.engine.snapshot(self.run["id"])["revision"], self.run["revision"])


if __name__ == "__main__":
    unittest.main()
