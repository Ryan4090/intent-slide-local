"""Installer/launcher contracts: fake subprocesses; no model or account access."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_mvp", ROOT / "scripts/local_mvp.py")
local_mvp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_mvp)


class LocalMvpTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=runtime, prefix="setup test ")
        self.root = Path(self.temporary.name)
        (self.root / "requirements-local.lock").write_text("Flask==3.1.3 --hash=sha256:" + "0" * 64 + "\n")

    def tearDown(self):
        self.temporary.cleanup()

    def fake_runner(self, command, **kwargs):
        self.commands.append(command)
        if "venv" in command:
            python = self.root / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("fixture")
            (self.root / ".venv/pyvenv.cfg").write_text("fixture")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def test_setup_creates_only_local_venv_and_installs_hash_locked_dependencies(self):
        self.commands = []
        result = local_mvp.setup_environment(self.root, run=self.fake_runner)
        self.assertEqual(result["status"], "READY")
        install = next(c for c in self.commands if "install" in c)
        self.assertEqual(install[0], str(self.root / ".venv/bin/python"))
        self.assertIn("--require-hashes", install)
        self.assertIn("--only-binary=:all:", install)
        self.assertEqual(install[-1], str(self.root / "requirements-local.lock"))
        self.assertTrue((self.root / ".runtime/setup-state.json").is_file())
        self.assertFalse(any("--user" in c or "--upgrade" in c for c in self.commands))

    def test_symlink_environment_is_rejected_before_any_process(self):
        (self.root / "outside").mkdir()
        (self.root / ".venv").symlink_to(self.root / "outside", target_is_directory=True)
        self.commands = []
        with self.assertRaises(local_mvp.SetupError):
            local_mvp.setup_environment(self.root, run=self.fake_runner)
        self.assertEqual(self.commands, [])

    def test_failed_install_never_records_ready_state(self):
        self.commands = []
        def fail(command, **kwargs):
            result = self.fake_runner(command, **kwargs)
            return subprocess.CompletedProcess(command, 1 if "install" in command else 0, stdout="", stderr="dependency unavailable")
        with self.assertRaises(local_mvp.SetupError):
            local_mvp.setup_environment(self.root, run=fail)
        self.assertFalse((self.root / ".runtime/setup-state.json").exists())

    def test_start_preserves_spaces_provider_and_port_without_a_shell(self):
        command = local_mvp.server_command(self.root, provider="claude", port=4318, no_runner=False)
        self.assertEqual(command[0], str(self.root / ".venv/bin/python"))
        self.assertEqual(command[command.index("--provider") + 1], "claude")
        self.assertEqual(command[command.index("--port") + 1], "4318")
        self.assertIn("--preflight", command)
        self.assertNotIn("shell", command)
        with self.assertRaises(local_mvp.SetupError):
            local_mvp.server_command(self.root, provider="untrusted", port=4318, no_runner=False)

    def test_read_only_doctor_distinguishes_cli_presence_from_authenticated_readiness(self):
        report = local_mvp.inspect_environment(self.root, provider="codex", which=lambda name: "/fixture/" + name,
                                              system="darwin", python_version=(3, 12), package_versions={})
        self.assertFalse(report["ready"])
        self.assertEqual(report["provider"]["auth_status"], "UNVERIFIED")
        self.assertEqual(report["renderer"]["verification_status"], "UNVERIFIED")
        self.assertIn("Flask", report["dependencies"]["missing"])

    def test_incomplete_existing_environment_cannot_install_into_another_python(self):
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("not a complete virtual environment")
        self.commands = []
        with self.assertRaises(local_mvp.SetupError):
            local_mvp.setup_environment(self.root, run=self.fake_runner)
        self.assertFalse(any("install" in c for c in self.commands))

    def test_doctor_blocks_unsupported_os_and_reports_missing_cli(self):
        report = local_mvp.inspect_environment(self.root, provider="claude", which=lambda name: None,
                                              system="win32", python_version=(3, 12), package_versions={})
        self.assertFalse(report["platform"]["supported"])
        self.assertFalse(report["provider"]["installed"])
        self.assertFalse(report["renderer"]["available"])

    def test_connected_doctor_uses_registry_provider_instance_and_omits_account_payload(self):
        class Adapter:
            def __enter__(self): return self
            def __exit__(self, *exc): pass
            def preflight(self): return {"provider": "codex", "ready": True, "auth_mode": "chatgpt",
                                         "account": {"private": "not diagnostic output"}}
        module = SimpleNamespace(provider_factory=lambda name: Adapter())
        with patch.dict(sys.modules, {"presentation_agents.v2.provider_registry": module}):
            report = local_mvp.connected_provider_check(self.root, "codex")
        self.assertTrue(report["ready"])
        self.assertNotIn("account", report)


if __name__ == "__main__":
    unittest.main()
