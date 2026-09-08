"""Research visibility counts accepted evidence, never web-tool self-reports."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from presentation_agents.v2.engine import Engine
from presentation_agents.v2.research_activity import (
    append_research_activity, project_research_activity, research_event, safe_source_url,
)
from test_v2_engine import intent


class ResearchActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name))
        self.run = self.engine.create("조사 확인", "보고 자료 조사", "external", "create")
        self.job = {"id": "test-job", "phase": "research", "status": "RUNNING"}

    def tearDown(self):
        self.temp.cleanup()

    def event(self, method="item/started", **fields):
        return {"method": method, "params": {"item": {"id": "web-one", "type": "webSearch", **fields}}}

    def accept_research(self):
        run_id = self.run["id"]
        state = self.engine.publish(run_id, "intent", intent(1))
        review = state["reviews"][-1]
        state = self.engine.command(run_id, "approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]}, "g1", state["revision"])
        state = self.engine.add_attachment(run_id, "source.txt", b"Revenue is 42.\nGrowth is 10%.\n", "source", state["revision"])
        artifact = state["artifacts"][-1]
        self.source_artifact = artifact
        source = {"id": "source-one", "origin": "external", "title": "매출 현황", "url": "https://Example.COM/report?token=private#part", "artifact_id": artifact["id"], "sha256": artifact["sha256"], "accessed_at": "2026-09-08"}
        support = {"source_id": "source-one", "locator": "lines:1", "excerpt": "Revenue is 42."}
        data = {"sources": [source], "claims": [{"id": "claim-one", "kind": "fact", "text": "Revenue 42", "evidence_status": "SUPPORTED", "supports": [support, copy.deepcopy(support)]}], "packets": [], "messages": []}
        return self.engine.publish(run_id, "research_checkpoint", data)

    def test_no_saved_bundle_has_no_evidence_counts_or_invented_location(self):
        result = project_research_activity(self.run, self.engine.root)
        self.assertEqual(result["summary"]["source_files"], 0)
        self.assertIsNone(result["storage"]["bundle"])
        self.assertTrue(Path(result["storage"]["database"]).is_file())
        self.assertEqual(result["sources"], [])

    def test_accepted_source_count_and_unique_verified_passages_with_saved_paths(self):
        state = self.accept_research()
        result = project_research_activity(state, self.engine.root)
        self.assertEqual(result["summary"], {"source_files": 1, "sites": 1, "claims": 1, "verified_excerpts": 1, "saved_bytes": 30})
        source = result["sources"][0]
        self.assertEqual(source["url"], "https://example.com/report")
        self.assertEqual(source["site"], "example.com")
        self.assertEqual(source["excerpt_count"], 1)
        self.assertEqual(source["claim_count"], 1)
        self.assertTrue(Path(source["saved_path"]).is_file())
        self.assertTrue(Path(result["storage"]["bundle"]).is_file())
        self.assertNotIn("token=", json.dumps(result))

    def test_source_tampering_and_deleted_bundle_are_not_current_evidence(self):
        state = self.accept_research()
        self.engine.artifact_path(state["id"], self.source_artifact["id"]).write_text("changed")
        result = project_research_activity(self.engine.snapshot(state["id"]), self.engine.root)
        self.assertEqual(result["summary"]["source_files"], 0)
        self.assertEqual(result["summary"]["verified_excerpts"], 0)
        self.assertEqual(result["summary"]["claims"], 0)
        self.assertEqual(result["sources"], [])
        bundle = next(a for a in state["artifacts"] if a["kind"] == "research")
        (self.engine.root / bundle["path"]).unlink()
        result = project_research_activity(self.engine.snapshot(state["id"]), self.engine.root)
        self.assertIsNone(result["storage"]["bundle"])

    def test_invalidated_research_cannot_show_old_counts(self):
        state = self.accept_research()
        state = self.engine.command(state["id"], "request_changes", {"stage": "research", "reason": "조사 범위 수정"}, "change", state["revision"])
        result = project_research_activity(state, self.engine.root)
        self.assertEqual(result["summary"]["verified_excerpts"], 0)
        self.assertEqual(result["sources"], [])

    def test_web_activity_is_sanitized_and_never_an_extraction_count(self):
        event = research_event(self.event(query="secret query", action={"type": "openPage", "url": "https://user:password@EXAMPLE.com/report?token=secret#private"}, result={"extracted": 999}), self.job)
        self.assertEqual(event["site"], "example.com")
        self.assertEqual(event["action"], "open")
        self.assertEqual(event["status"], "RUNNING")
        serialized = json.dumps(event)
        for private in ("password", "secret", "private", "999", "query"):
            self.assertNotIn(private, serialized)
        body = copy.deepcopy(self.run)
        append_research_activity(body, event)
        result = project_research_activity(body, self.engine.root)
        self.assertEqual(result["summary"]["source_files"], 0)

    def test_search_query_and_non_research_commands_are_not_saved(self):
        event = research_event(self.event(query="password", action={"type": "search", "queries": ["secret"]}), self.job)
        self.assertEqual(event["action"], "search")
        self.assertIsNone(event["site"])
        self.assertNotIn("secret", json.dumps(event))
        self.assertIsNone(research_event(self.event(type="commandExecution", command="TOKEN=secret"), self.job))
        self.assertIsNone(research_event(self.event(), {**self.job, "phase": "intent"}))

    def test_unknown_or_malformed_action_types_are_ignored(self):
        for action_type in ([], {}, 123, "unknownAction"):
            with self.subTest(action_type=action_type):
                self.assertIsNone(research_event(self.event(action={"type": action_type}), self.job))

    def test_url_sanitization_rejects_unsafe_schemes_controls_and_malformed_ports(self):
        for value in ("javascript:alert(1)", "file:///private/file", "https://host.invalid:bad/x", "https://bad\n.example/x", {}, None):
            with self.subTest(value=value):
                self.assertIsNone(safe_source_url(value))
        self.assertEqual(safe_source_url("https://u:p@Example.com:443/a?q=secret#hidden"), "https://example.com/a")

    def test_lifecycle_is_deduplicated_and_restart_retains_activity(self):
        started = research_event(self.event(action={"type": "openPage", "url": "https://example.com/a"}), self.job)
        finished = research_event(self.event("item/completed", action={"type": "openPage", "url": "https://example.com/a"}), self.job)
        def record(body):
            append_research_activity(body, started)
            append_research_activity(body, finished)
            append_research_activity(body, finished)
            return "정제 조사 동작을 저장했습니다"
        self.engine.store.mutate(self.run["id"], "research.activity", {}, record)
        restarted = Engine(self.engine.root).snapshot(self.run["id"])
        result = project_research_activity(restarted, self.engine.root)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["status"], "COMPLETED")
        self.assertEqual(result["events"][0]["evidence_status"], "OBSERVED")

    def test_late_start_cannot_replace_finished_state_and_history_is_bounded(self):
        body = copy.deepcopy(self.run)
        finished = research_event(self.event("item/completed"), self.job)
        append_research_activity(body, finished)
        append_research_activity(body, research_event(self.event(), self.job))
        self.assertEqual(body["research_activity"]["events"][0]["status"], "COMPLETED")
        for index in range(140):
            append_research_activity(body, research_event(self.event(id=f"web-{index}"), self.job))
        result = project_research_activity(body, self.engine.root)
        self.assertEqual(len(result["events"]), 128)
        self.assertGreater(result["omitted_events"], 0)

    def test_old_attempt_running_event_is_historical_after_cancel_or_retry(self):
        body = copy.deepcopy(self.run)
        body["active_stage"] = "research"
        body["jobs"] = [{**self.job, "status": "CANCELLED"}, {**self.job, "id": "next-job"}]
        append_research_activity(body, research_event(self.event(), self.job))
        result = project_research_activity(body, self.engine.root)
        self.assertTrue(result["events"][0]["historical"])
        self.assertEqual(result["events"][0]["status"], "INTERRUPTED")
        self.assertNotEqual(result["current_action"], result["events"][0]["message"])

    def test_stopped_research_without_a_bundle_explains_its_real_state(self):
        for status in ("FAILED", "BLOCKED", "CANCELLED", "INTERRUPTED"):
            with self.subTest(status=status):
                body = copy.deepcopy(self.run)
                body["active_stage"] = "research"
                body["jobs"] = [{**self.job, "status": status}]
                append_research_activity(body, research_event(self.event(), self.job))
                result = project_research_activity(body, self.engine.root)
                self.assertNotIn("의도를 확정하면", result["current_action"])
                self.assertIn("중단", result["current_action"])
                self.assertTrue(result["events"][0]["historical"])

    def test_runner_persists_observed_activity_and_ignores_events_after_cancel(self):
        from presentation_agents.v2.runner import Runner
        from test_v2_runner import ProviderFactory, REPO
        state = self.accept_research()
        factory = ProviderFactory()
        runner = Runner(self.engine, REPO, provider_factory=factory)
        def start(provider):
            provider.emit("item/started", self.event(action={"type": "openPage", "url": "https://example.com/report?secret=hidden"})["params"])
            provider.emit("item/completed", self.event(action={"type": "openPage", "url": "https://example.com/report?secret=hidden"})["params"])
            current = self.engine.snapshot(state["id"])
            self.assertEqual(current["research_activity"]["events"][0]["status"], "COMPLETED")
            self.engine.command(state["id"], "cancel", {}, "cancel", current["revision"])
            provider.emit("item/started", self.event(id="late-web")["params"])
        factory.plans.append(start)
        self.engine.command(state["id"], "run", {}, "run", state["revision"])
        job = self.engine.claim_job(state["id"])
        runner._active = {"run_id": state["id"], "job_id": job["id"]}
        try:
            # Synchronous fixture cancels during startup, before runner connects.
            from presentation_agents.v2.contracts import Conflict
            with self.assertRaises(Conflict):
                runner._execute(state["id"], job)
        finally:
            runner.close()
        restored = Engine(self.engine.root).snapshot(state["id"])
        self.assertEqual(len(restored["research_activity"]["events"]), 1)
        event_messages = [event["message"] for event in restored["events"] if event["kind"] == "research.activity"]
        self.assertEqual(len(event_messages), 2)
        self.assertNotIn("hidden", json.dumps(event_messages))


if __name__ == "__main__":
    unittest.main()
