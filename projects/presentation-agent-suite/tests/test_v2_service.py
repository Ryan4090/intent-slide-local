"""Exercise process leases and CLI-originated queue wakeups without a model."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from presentation_agents.v2.contracts import ContractError
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.service import QueueWatch, ServiceLease

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parents[1]


class RecordingRunner(Runner):
    """Use the real queue/claim loop, replacing only costly model execution."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, REPO)
        self.executed: list[str] = []
        self.completed = threading.Event()

    def _execute(self, run_id: str, job: dict) -> None:
        self.executed.append(job["id"])
        self.engine.update_job(run_id, job["id"], {"status":"COMPLETED"}, "Test-only execution completed")
        self.completed.set()


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="service-test-", dir=runtime)
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_same_database_rejects_a_second_service_and_releases_on_exit(self) -> None:
        with ServiceLease(self.root):
            with self.assertRaisesRegex(ContractError, "이미 실행"):
                with ServiceLease(self.root):
                    self.fail("a second service acquired the lease")
            self.assertTrue((self.root / "service.lock").is_file())
        with ServiceLease(self.root):
            self.assertTrue((self.root / "service.lock").is_file())

    def test_distinct_databases_have_independent_leases(self) -> None:
        with ServiceLease(self.root / "one"), ServiceLease(self.root / "two"):
            self.assertTrue((self.root / "one/service.lock").is_file())
            self.assertTrue((self.root / "two/service.lock").is_file())

    def test_exception_releases_lease_without_deleting_the_lock_file(self) -> None:
        with self.assertRaises(RuntimeError):
            with ServiceLease(self.root):
                raise RuntimeError("simulated startup failure")
        with ServiceLease(self.root):
            self.assertTrue((self.root / "service.lock").is_file())

    def test_an_independent_process_holds_the_lease_until_it_exits(self) -> None:
        script = """
import sys
from pathlib import Path
from presentation_agents.v2.service import ServiceLease
with ServiceLease(Path(sys.argv[1])):
    print('ACQUIRED', flush=True)
    sys.stdin.read()
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SUITE / "src")
        process = subprocess.Popen([sys.executable, "-u", "-c", script, str(self.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=environment)
        try:
            # communicate cannot be used before checking the held lease because
            # it closes stdin. A bounded reader avoids hanging if startup fails.
            ready = threading.Event()
            output = []
            def read_ready() -> None:
                output.append(process.stdout.readline())
                ready.set()
            worker = threading.Thread(target=read_ready, daemon=True)
            worker.start()
            self.assertTrue(ready.wait(3), "lease process did not become ready")
            self.assertEqual(output, ["ACQUIRED\n"])
            with self.assertRaises(ContractError):
                with ServiceLease(self.root):
                    self.fail("cross-process duplicate service was accepted")
            process.terminate()
            process.wait(timeout=3)
            with ServiceLease(self.root):
                self.assertTrue((self.root / "service.lock").is_file())
            worker.join(timeout=1)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_cli_enqueue_wakes_existing_service_without_an_http_request(self) -> None:
        engine = Engine(self.root)
        run = engine.create("CLI wake test", "Bounded queue fixture", "provided_only", "create")
        runner = RecordingRunner(engine)
        with ServiceLease(self.root), QueueWatch(runner, interval=0.02):
            completed = subprocess.run([
                sys.executable, str(SUITE / "scripts/presentation_v2.py"), "run", run["id"],
                "--data-dir", str(self.root), "--operation-id", "cli-run-once",
                "--expected-revision", str(run["revision"]),
            ], capture_output=True, text=True, timeout=5, cwd=REPO)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            acknowledgement = json.loads(completed.stdout)
            self.assertEqual(acknowledgement["jobs"][-1]["status"], "QUEUED")
            self.assertTrue(runner.completed.wait(3), "CLI-enqueued job was never claimed")
            time.sleep(0.08)
        self.assertEqual(len(runner.executed), 1, "periodic wakeup must not execute the same job twice")
        job = engine.store.read(run["id"])["jobs"][-1]
        self.assertEqual(job["status"], "COMPLETED")
        self.assertIn("job.started", [event["kind"] for event in engine.events(run["id"])])

    def test_queue_watch_stops_waking_after_context_exit(self) -> None:
        class IdleRunner:
            def __init__(self) -> None:
                self.calls = 0
                self.called = threading.Event()
            def kick(self) -> None:
                self.calls += 1
                self.called.set()
        runner = IdleRunner()
        with QueueWatch(runner, interval=0.02) as watch:
            self.assertTrue(runner.called.wait(1))
        calls = runner.calls
        time.sleep(0.06)
        self.assertEqual(runner.calls, calls)
        self.assertFalse(watch.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
