"""Exercise the real subprocess/JSON-lines boundary without calling a model."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE / "src"))

from presentation_agents.v2.provider import CodexProvider, ProviderError


# This independent peer checks the installed 0.153.4 schema's enum spelling,
# emits requests before turn/start's response, and permits cancellation while
# waiting for user input. No fixture borrows implementation helpers.
PEER = r'''
import json, sys, time, subprocess
mode = sys.argv[1]
cwd = None
def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)
def reply(request, result):
    send({"id":request["id"], "result": result})
def done(status="completed"):
    send({"method":"item/completed", "params":{"threadId":"thread-1","turnId":"turn-1","item":{"type":"agentMessage","id":"answer-1","text":"검증 완료"}}})
    send({"method":"turn/completed", "params":{"threadId":"thread-1","turn":{"id":"turn-1","status":status}}})
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    params = request.get("params", {})
    if method == "initialize":
        if mode == "descendant_pipe":
            subprocess.Popen([sys.executable,"-c","import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"])
            sys.exit(0)
        if mode == "bad_json":
            print("not json", flush=True); continue
        if mode == "bad_utf8":
            sys.stdout.buffer.write(b'\xff\n');sys.stdout.buffer.flush();continue
        if mode == "large_frame":
            print("x" * 5000, flush=True);continue
        if mode == "hang_init":
            time.sleep(5);continue
        if mode == "exit":
            sys.exit(4)
        assert params["clientInfo"]["name"] == "slidemaster_v2"
        reply(request, {"userAgent":"fake-codex/0.153.4","platformFamily":"unix"})
    elif method == "initialized":
        pass
    elif method == "account/read":
        assert params == {"refreshToken":False}
        reply(request, {"requiresOpenaiAuth":True,"account":None if mode == "no_auth" else {"type":("apiKey" if mode == "api_auth" else "unknownVendor" if mode == "unknown_auth" else "chatgpt"),"email":"private@example.test","accountId":"private-id"}})
    elif method == "model/list":
        model = {"id":"fixture-model","model":"fixture-model","displayName":"Fixture","isDefault":True,"defaultReasoningEffort":"medium","supportedReasoningEfforts":[{"reasoningEffort":"low"},{"reasoningEffort":"medium"},{"reasoningEffort":"high"},{"reasoningEffort":"ultra"}]}
        if mode == "missing_effort_default": model.pop("defaultReasoningEffort")
        if mode == "unsupported_effort_default": model["defaultReasoningEffort"] = "max"
        if mode == "missing_effort_options": model.pop("supportedReasoningEfforts")
        reply(request, {"data":[model],"nextCursor":None})
    elif method == "account/rateLimits/read":
        reply(request, {"accountId":"private-id","rateLimits":{"primary":{"usedPercent":12,"windowDurationMins":300,"resetsAt":1234},"credits":{"balance":"private balance"}}})
    elif method in ("thread/start", "thread/resume"):
        assert params["approvalPolicy"] == "on-request"
        assert params["approvalsReviewer"] == "user"
        assert params["sandbox"] == "workspace-write"
        assert params["config"]["sandbox_workspace_write"]["writable_roots"] == []
        if method == "thread/resume": assert params["threadId"] == "thread-1"
        cwd = params["cwd"]
        reply(request, {"thread":{"id":"thread-1","status":{"type":"idle"}},"model":"fixture-model","cwd":cwd,"approvalPolicy":"on-request","approvalsReviewer":"user","sandbox":{"type":"dangerFullAccess" if mode == "bad_policy" else "workspaceWrite","writableRoots":[cwd],"networkAccess":False}})
    elif method == "turn/start":
        assert params["sandboxPolicy"] == {"type":"workspaceWrite","writableRoots":[cwd],"networkAccess":False,"excludeSlashTmp":True,"excludeTmpdirEnvVar":True}
        if mode in ("missing_effort_default", "unsupported_effort_default", "missing_effort_options"):
            assert "effort" not in params
        else:
            assert params["effort"] in ("low","medium","ultra")
        if mode == "turn_error":
            send({"id":request["id"],"error":{"code":-32000,"message":"private sensitive upstream details"}});continue
        if mode == "early_complete":
            done();reply(request,{"turn":{"id":"turn-1","status":"inProgress"}});continue
        reply(request, {"turn":{"id":"turn-1","status":"inProgress"}})
        if mode == "stderr":
            sys.stderr.buffer.write(("한" * 2000).encode());sys.stderr.buffer.flush()
        send({"method":"item/agentMessage/delta","params":{"threadId":"thread-1","turnId":"turn-1","delta":"안녕하세요"}})
        if mode in ("normal", "cancel", "auto_respond"):
            send({"id":71,"method":"item/commandExecution/requestApproval","params":{"threadId":"thread-1","turnId":"turn-1","itemId":"cmd-1","command":"pwd"}})
        elif mode == "input":
            send({"id":"ask-1","method":"item/tool/requestUserInput","params":{"threadId":"thread-1","turnId":"turn-1","questions":[{"id":"audience","question":"대상은?"}]}})
        elif mode == "unsupported":
            send({"id":"tool-1","method":"item/tool/call","params":{"threadId":"thread-1","turnId":"turn-1","tool":"unsafe"}})
        elif mode != "turn_hang":
            done()
    elif method == "turn/interrupt":
        assert params == {"threadId":"thread-1","turnId":"turn-1"}
        reply(request,{})
        done("interrupted")
    elif method is None:
        if request["id"] == 71:
            assert request["result"] == {"decision":"accept"}
        elif request["id"] == "ask-1":
            assert request["result"] == {"answers":{"audience":{"answers":["임원"]}}}
        elif request["id"] == "tool-1":
            assert request["error"]["code"] == -32601
        done()
    else:
        raise RuntimeError("unexpected method:"+str(method))
'''


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="provider-test-", dir=runtime)
        self.cwd = Path(self.temp.name)
        self.providers: list[CodexProvider] = []

    def tearDown(self) -> None:
        for provider in self.providers:
            provider.close()
            if provider._process is not None:
                self.assertIsNotNone(provider._process.poll(), "owned process must be reaped")
        self.temp.cleanup()

    def provider(self, mode: str = "normal", **kwargs: object) -> CodexProvider:
        options = {"request_timeout": 2.0, "turn_timeout": 5.0, **kwargs}
        result = CodexProvider(command=[sys.executable, "-u", "-c", PEER, mode], **options)
        self.providers.append(result)
        return result

    @staticmethod
    def wait_for(events: list[dict], method: str, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in list(events):
                if event.get("method") == method:
                    return event
            time.sleep(0.01)
        raise AssertionError(f"No {method}; observed {[e.get('method') for e in events]}")

    def test_preflight_removes_private_account_metadata(self) -> None:
        status = self.provider().preflight()
        self.assertTrue(status["ready"])
        self.assertEqual(status["auth_type"], "chatgpt")
        self.assertNotIn("private", json.dumps(status))
        self.assertEqual(status["rate_limits"]["codex"]["primary"]["usedPercent"], 12)

    def test_missing_login_blocks_generation(self) -> None:
        provider = self.provider("no_auth")
        self.assertFalse(provider.preflight()["ready"])
        with self.assertRaises(ProviderError) as raised:
            provider.start_turn(self.cwd, "hello", lambda event: None)
        self.assertEqual(raised.exception.code, "AUTH_REQUIRED")

    def test_stream_approval_and_complete(self) -> None:
        events: list[dict] = []
        provider = self.provider()
        ack = provider.start_turn(self.cwd, "hello", events.append)
        self.assertEqual(ack, {"thread_id":"thread-1", "turn_id":"turn-1", "model":"fixture-model", "effort":"medium"})
        request = self.wait_for(events, "item/commandExecution/requestApproval")
        self.assertEqual(request["id"], 71)
        with self.assertRaises(ValueError):
            provider.respond(71, {"decision":"acceptForSession"})
        provider.respond(71, {"decision":"accept"})
        final = self.wait_for(events, "turn/completed")
        self.assertEqual(final["params"]["turn"]["status"], "completed")
        with self.assertRaises(ProviderError):
            provider.respond(71, {"decision":"accept"})
        self.assertEqual(self.wait_for(events, "item/agentMessage/delta")["params"]["delta"], "안녕하세요")

    def test_only_native_subscription_auth_is_ready(self) -> None:
        for mode, auth_mode in [("api_auth", "api_key"), ("unknown_auth", "unknown"), ("no_auth", "none")]:
            with self.subTest(mode=mode):
                provider = self.provider(mode)
                caps = provider.preflight()
                self.assertFalse(caps["ready"])
                self.assertEqual(caps["auth_mode"], auth_mode)
                self.assertEqual(caps["provider"], "codex")
                with self.assertRaises(ProviderError):
                    provider.start_turn(self.cwd, "do not run", lambda event: None)

    def test_callback_may_respond_without_deadlocking_reader(self) -> None:
        events: list[dict] = []
        provider = self.provider("auto_respond")
        def handle(event: dict) -> None:
            events.append(event)
            if event["method"] == "item/commandExecution/requestApproval":
                provider.respond(event["id"], {"decision":"accept"})
        provider.start_turn(self.cwd, "hello", handle)
        self.wait_for(events, "turn/completed")

    def test_user_input_roundtrip_on_resumed_thread(self) -> None:
        provider = self.provider("input")
        events: list[dict] = []
        ack = provider.start_turn(self.cwd, "hello", events.append, thread_id="thread-1", effort="low")
        self.assertEqual(ack["effort"], "low")
        self.wait_for(events, "item/tool/requestUserInput")
        provider.respond("ask-1", {"answers":{"audience":{"answers":["임원"]}}})
        self.wait_for(events, "turn/completed")

    def test_missing_or_unsupported_catalog_default_leaves_effort_to_native_server(self) -> None:
        for mode in ("missing_effort_default", "unsupported_effort_default", "missing_effort_options"):
            with self.subTest(mode=mode):
                provider = self.provider(mode)
                events = []
                ack = provider.start_turn(self.cwd, "hello", events.append)
                self.assertIsNone(ack["effort"])
                self.wait_for(events, "turn/completed")

    def test_explicit_effort_is_preserved_and_unsupported_choice_rejected(self) -> None:
        provider = self.provider()
        provider.preflight()
        self.assertEqual(provider._choose_effort("fixture-model", "ultra"), "ultra")
        self.assertEqual(provider._choose_effort("fixture-model", "low"), "low")
        self.assertEqual(provider._choose_effort("fixture-model", None), "medium")
        with self.assertRaises(ProviderError) as raised:
            provider._choose_effort("fixture-model", "max")
        self.assertEqual(raised.exception.code, "EFFORT_UNAVAILABLE")

    def test_cancel_requires_final_interrupted_event(self) -> None:
        provider = self.provider("cancel")
        events: list[dict] = []
        ack = provider.start_turn(self.cwd, "hello", events.append)
        self.wait_for(events, "item/commandExecution/requestApproval")
        provider.cancel(ack["thread_id"], ack["turn_id"])
        final = self.wait_for(events, "turn/completed")
        self.assertEqual(final["params"]["turn"]["status"], "interrupted")
        with self.assertRaises(ProviderError):
            provider.respond(71, {"decision":"accept"})

    def test_unsupported_dynamic_tool_is_rejected(self) -> None:
        events: list[dict] = []
        self.provider("unsupported").start_turn(self.cwd, "hello", events.append)
        self.wait_for(events, "turn/completed")
        self.assertNotIn("item/tool/call", [event["method"] for event in events])

    def test_early_completion_does_not_leave_phantom_active_turn(self) -> None:
        events: list[dict] = []
        provider = self.provider("early_complete")
        provider.start_turn(self.cwd, "hello", events.append)
        self.wait_for(events, "turn/completed")
        self.assertEqual(provider._active, {})
        provider.start_turn(self.cwd, "hello again", events.append, thread_id="thread-1")

    def test_policy_mismatch_stops_before_model_turn(self) -> None:
        with self.assertRaises(ProviderError) as raised:
            self.provider("bad_policy").start_turn(self.cwd, "hello", lambda event: None)
        self.assertEqual(raised.exception.code, "POLICY_MISMATCH")

    def test_protocol_failures_and_ack_timeout_are_bounded(self) -> None:
        for mode, code in [("bad_json", "PROTOCOL_ERROR"), ("bad_utf8", "PROTOCOL_ERROR"),
                           ("large_frame", "OUTPUT_LIMIT"), ("hang_init", "REQUEST_TIMEOUT"), ("exit", "PROCESS_EXIT")]:
            with self.subTest(mode=mode):
                started = time.monotonic()
                with self.assertRaises(ProviderError) as raised:
                    self.provider(mode, request_timeout=0.2, max_frame_bytes=1024).preflight()
                self.assertEqual(raised.exception.code, code)
                self.assertLess(time.monotonic() - started, 3)

    def test_stderr_buffer_is_byte_bounded_and_not_forwarded(self) -> None:
        provider = self.provider("stderr", max_stderr_bytes=32)
        events: list[dict] = []
        provider.start_turn(self.cwd, "hello", events.append)
        self.wait_for(events, "turn/completed")
        self.assertLessEqual(len(provider._stderr_tail), 32)
        self.assertNotIn("한", json.dumps(events, ensure_ascii=False))

    def test_active_execution_timeout_reports_failure(self) -> None:
        provider = self.provider("turn_hang", turn_timeout=0.15)
        events: list[dict] = []
        provider.start_turn(self.cwd, "hello", events.append)
        error = self.wait_for(events, "provider/error")
        self.assertEqual(error["params"]["code"], "TURN_TIMEOUT")

    def test_user_wait_does_not_consume_execution_deadline(self) -> None:
        provider = self.provider("normal", turn_timeout=0.2)
        events: list[dict] = []
        provider.start_turn(self.cwd, "hello", events.append)
        self.wait_for(events, "item/commandExecution/requestApproval")
        time.sleep(0.4)
        provider.respond(71, {"decision":"accept"})
        self.wait_for(events, "turn/completed")
        self.assertNotIn("provider/error", [event["method"] for event in events])

    def test_callback_failure_is_visible_on_provider(self) -> None:
        provider = self.provider("stderr")
        called = threading.Event()
        def fail(event: dict) -> None:
            called.set()
            raise RuntimeError("consumer unavailable")
        provider.start_turn(self.cwd, "hello", fail)
        self.assertTrue(called.wait(2))
        deadline = time.monotonic() + 2
        while provider._failure is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(provider._failure.code, "CALLBACK_ERROR")

    def test_persistent_and_profile_permission_expansion_rejected(self) -> None:
        for method, payload in [
            ("item/commandExecution/requestApproval", {"decision":{"acceptWithExecpolicyAmendment":{"execpolicy_amendment":["sh"]}}}),
            ("item/permissions/requestApproval", {"permissions":{"fileSystem":{"write":["/"]}},"scope":"session"}),
        ]:
            with self.assertRaises(ValueError):
                CodexProvider._validate_decision(method, payload)

    def test_rpc_rejection_clears_pending_turn_without_echoing_private_error(self) -> None:
        provider = self.provider("turn_error")
        with self.assertRaises(ProviderError) as raised:
            provider.start_turn(self.cwd, "hello", lambda event: None)
        self.assertEqual(raised.exception.code, "RPC_ERROR")
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(provider._active, {})

    def test_provider_failure_does_not_notify_completed_project(self) -> None:
        provider = self.provider("early_complete")
        old_events: list[dict] = []
        provider.start_turn(self.cwd, "hello", old_events.append)
        self.wait_for(old_events, "turn/completed")
        provider._fail(ProviderError("PROCESS_EXIT", "peer stopped after completion"))
        time.sleep(0.2)
        self.assertNotIn("provider/error", [event["method"] for event in old_events])

    def test_unknown_thread_event_is_not_sent_to_starting_project(self) -> None:
        provider = self.provider()
        events: list[dict] = []
        provider._starting_callback = events.append
        provider._receive({"method":"item/agentMessage/delta","params":{"threadId":"unrelated-thread","delta":"private"}})
        self.assertTrue(provider._events.empty())

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup")
    def test_exited_parent_does_not_leave_descendant_pipe_running(self) -> None:
        provider = self.provider("descendant_pipe", request_timeout=0.2)
        started = time.monotonic()
        with self.assertRaises(ProviderError) as raised:
            provider.preflight()
        self.assertEqual(raised.exception.code, "REQUEST_TIMEOUT")
        provider.close()
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(all(not worker.is_alive() for worker in provider._threads))

    def test_blocked_event_consumer_has_a_bounded_queue(self) -> None:
        provider = self.provider(event_queue_size=1)
        events: list[dict] = []
        provider._enqueue(events.append, {"method":"fixture/one"})
        provider._enqueue(events.append, {"method":"fixture/two"})
        self.assertEqual(provider._failure.code, "CONSUMER_BACKPRESSURE")
        self.assertEqual(provider._events.qsize(), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX desktop symlink fixture; Windows has an executable-layout fixture")
    def test_bundled_code_host_is_added_only_to_child_path(self) -> None:
        bundle = self.cwd / "Bundle Resources"
        bundle.mkdir()
        cli = bundle / "codex"
        host = bundle / "codex-code-mode-host"
        for executable in (cli, host):
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf8")
            executable.chmod(0o755)
        link = self.cwd / "codex"
        link.symlink_to(cli)
        provider = CodexProvider(command=[str(link), "app-server"])
        self.providers.append(provider)
        original_path = os.environ.get("PATH")
        environment = provider._runtime_environment()
        self.assertEqual(os.environ.get("PATH"), original_path)
        self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(bundle))
        self.assertEqual(provider._runtime["code_mode_host"], str(host))
        self.assertEqual(provider._runtime["status"], "PARTIALLY_VERIFIED")

    def npm_fixture(self, arch="arm64", *, nested=False, embedded=False, version="0.153.4"):
        if os.name != "posix":
            self.skipTest("POSIX npm symlink fixture; test_v2_stdio_transport covers Windows npm layout")
        root = self.cwd / ("npm-" + arch + ("-nested" if nested else "") + ("-embedded" if embedded else ""))
        main = root / "lib/node_modules/@openai/codex"
        (main / "bin").mkdir(parents=True)
        main_version = "0.153.4"
        platform_name = "@openai/codex-darwin-" + arch
        manifest = {"name":"@openai/codex", "version":main_version, "bin":{"codex":"bin/codex.js"},
                    "optionalDependencies":{platform_name:"npm:@openai/codex@"+main_version+"-darwin-"+arch}}
        (main / "package.json").write_text(json.dumps(manifest))
        launcher = main / "bin/codex.js"
        launcher.write_text("#!/usr/bin/env node\n// fixture launcher; never executed\n")
        launcher.chmod(0o755)
        link = root / "codex"
        link.symlink_to(launcher)
        platform_root = main if embedded else (main / "node_modules/@openai" if nested else main.parent) / ("codex-darwin-"+arch)
        if not embedded:
            platform_root.mkdir(parents=True)
            (platform_root / "package.json").write_text(json.dumps({"name":"@openai/codex", "version":version+"-darwin-"+arch,
                                                                    "os":["darwin"], "cpu":[arch]}))
        triple = ("aarch64" if arch == "arm64" else "x86_64") + "-apple-darwin"
        binary_dir = platform_root / "vendor" / triple / "bin"
        binary_dir.mkdir(parents=True)
        for name in ("codex", "codex-code-mode-host"):
            executable = binary_dir / name
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
        return link, binary_dir, main

    def npm_environment(self, link, arch="arm64"):
        provider = CodexProvider(command=[str(link), "app-server"])
        self.providers.append(provider)
        with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="arm64" if arch == "arm64" else "x86_64"), patch(
            "presentation_agents.v2.provider.shutil.which", side_effect=lambda name: str(link) if Path(name).name == "codex" else None
        ):
            environment = provider._runtime_environment()
        return provider, environment

    def test_npm_platform_helpers_are_paired_and_child_path_only(self) -> None:
        for arch, nested in (("arm64", False), ("x64", True)):
            with self.subTest(arch=arch, nested=nested):
                link, binary_dir, main = self.npm_fixture(arch, nested=nested)
                before = {p: p.read_bytes() for p in main.rglob("*") if p.is_file()}
                old_path = os.environ.get("PATH")
                provider, environment = self.npm_environment(link, arch)
                self.assertIsNotNone(environment)
                self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(binary_dir))
                self.assertEqual(provider._runtime["host_discovery"], "npm_platform_package")
                self.assertEqual(provider._runtime["code_mode_host"], str(binary_dir / "codex-code-mode-host"))
                self.assertEqual(os.environ.get("PATH"), old_path)
                self.assertEqual({p:p.read_bytes() for p in before}, before)
                self.assertEqual(provider.command[0], str(link))

    def test_npm_launcher_is_not_shadowed_by_native_payload_path(self) -> None:
        link, binary_dir, _ = self.npm_fixture()
        provider = CodexProvider(request_timeout=1)
        self.providers.append(provider)
        actual_popen = subprocess.Popen
        launches = []
        def launch(command, **kwargs):
            launches.append((command, kwargs["env"]["PATH"]))
            return actual_popen([sys.executable, "-u", "-c", PEER, "normal"], **kwargs)
        with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="arm64"), patch(
            "presentation_agents.v2.provider.shutil.which", side_effect=lambda name: str(link) if name == "codex" else None
        ), patch("presentation_agents.v2.provider.subprocess.Popen", side_effect=launch):
            self.assertTrue(provider.preflight()["ready"])
        self.assertEqual(launches[0][0], [str(link), "app-server", "--listen", "stdio://"])
        self.assertEqual(launches[0][1].split(os.pathsep)[0], str(binary_dir))
        self.assertEqual(provider.command[0], "codex")

    def test_npm_embedded_vendor_fallback_is_supported(self) -> None:
        link, binary_dir, _ = self.npm_fixture(embedded=True)
        provider, environment = self.npm_environment(link)
        self.assertIsNotNone(environment)
        self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(binary_dir))
        self.assertEqual(provider._runtime["host_discovery"], "npm_embedded_vendor")

    def test_npm_version_mismatch_and_missing_helper_fail_closed(self) -> None:
        link, _, _ = self.npm_fixture(version="0.153.3")
        provider, environment = self.npm_environment(link)
        self.assertIsNone(environment)
        self.assertEqual(provider._runtime["status"], "BLOCKED")
        link, binary_dir, _ = self.npm_fixture("x64")
        (binary_dir / "codex-code-mode-host").unlink()
        provider, environment = self.npm_environment(link, "x64")
        self.assertIsNone(environment)
        self.assertEqual(provider._runtime["status"], "BLOCKED")

    def test_npm_untrusted_or_oversize_manifest_is_not_used(self) -> None:
        link, _, main = self.npm_fixture()
        for contents in ('{"name":"unrelated","version":"0.153.4"}', 'x' * 65537):
            (main / "package.json").write_text(contents)
            provider, environment = self.npm_environment(link)
            self.assertIsNone(environment)
            self.assertEqual(provider._runtime["status"], "BLOCKED")

    def test_missing_code_host_is_reported_without_gui_fallback(self) -> None:
        provider = CodexProvider()
        self.providers.append(provider)
        with patch("presentation_agents.v2.provider.shutil.which", side_effect=lambda name: str(self.cwd / "codex") if name == "codex" else None):
            self.assertIsNone(provider._runtime_environment())
        self.assertEqual(provider._runtime["status"], "BLOCKED")
        self.assertIn("codex-code-mode-host", provider._runtime["reason"])


if __name__ == "__main__":
    unittest.main()
