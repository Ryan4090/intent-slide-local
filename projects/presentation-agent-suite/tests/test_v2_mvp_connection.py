"""Codex-only MVP policy; native login and model execution stay explicit."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from presentation_agents.v2.contracts import ContractError
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.server import create_app

ROOT = Path(__file__).resolve().parents[3]


class MvpConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name))
        self.runner = Runner(self.engine, ROOT, default_provider='auto')
        self.app = create_app(self.engine, ROOT / 'projects/presentation-agent-suite/console', self.runner,
                              bootstrap_token='fixture-only')
        self.client = self.app.test_client()
        session = self.client.post('/api/v2/session', json={'bootstrap_token': 'fixture-only'}).json
        self.headers = {'X-CSRF-Token': session['csrf_token']}

    def tearDown(self):
        self.runner.close()
        if self.runner._discovery_thread:
            self.runner._discovery_thread.join(3)
        self.temp.cleanup()

    def test_discovery_checks_only_codex_without_login_or_jobs(self):
        calls = []
        class Probe:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def close(self): pass
            def preflight(self): return {'ready': True, 'auto_connect': True, 'models': []}
        def factory(name):
            calls.append(name)
            return Probe()
        with patch.object(self.runner, '_make_provider', side_effect=factory), patch.object(self.runner._login, 'start', side_effect=AssertionError('login must be explicit')):
            self.runner.discover()
            self.runner._discovery_thread.join(3)
        capabilities = self.runner.capabilities()
        self.assertEqual(calls, ['codex'])
        self.assertEqual([p['id'] for p in capabilities['providers']], ['codex'])
        self.assertEqual(capabilities['recommended_provider'], 'codex')
        self.assertEqual(capabilities['default_provider'], 'codex')
        self.assertEqual(self.engine.list(), [])

    def test_http_rejects_other_providers_without_rewriting_history(self):
        legacy = self.engine.create('기존 프로젝트', 'fixture', 'provided_only', 'legacy', execution='claude')
        for provider in ('claude', 'gemini', 'opencode'):
            with self.subTest(provider=provider):
                created = self.client.post('/api/v2/runs', headers=self.headers,
                    json={'request': 'fixture', 'operation_id': 'create-' + provider, 'execution': {'provider': provider}})
                self.assertEqual(created.status_code, 422)
                configured = self.client.post(f"/api/v2/runs/{legacy['id']}/commands", headers=self.headers,
                    json={'command': 'configure_provider', 'payload': {'provider': provider},
                          'expected_revision': legacy['revision'], 'operation_id': 'configure-' + provider})
                self.assertEqual(configured.status_code, 422)
                with patch.object(self.runner, '_make_provider', side_effect=AssertionError('must not probe')):
                    refreshed = self.client.post('/api/v2/capabilities/refresh', headers=self.headers, json={'provider': provider})
                self.assertEqual(refreshed.status_code, 422)
        self.assertEqual(self.engine.snapshot(legacy['id'])['execution']['provider'], 'claude')
        self.assertEqual(len(self.engine.list()), 1)

    def test_new_http_project_defaults_to_codex(self):
        response = self.client.post('/api/v2/runs', headers=self.headers,
            json={'request': 'fixture', 'operation_id': 'new-codex'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['execution']['provider'], 'codex')

    def test_legacy_followups_require_explicit_switch_and_preserve_job_selection(self):
        legacy = self.engine.create('기존 프로젝트', 'fixture', 'provided_only', 'legacy', execution='claude')
        endpoint = f"/api/v2/runs/{legacy['id']}/commands"
        for command in ('run', 'resume', 'message', 'answer', 'approve'):
            response = self.client.post(endpoint, headers=self.headers, json={'command': command,
                'payload': {'content': 'fixture'}, 'operation_id': command, 'expected_revision': legacy['revision']})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.engine.snapshot(legacy['id'])['revision'], legacy['revision'])
        queued = self.engine.command(legacy['id'], 'run', {}, 'legacy-queue', legacy['revision'])
        with patch.object(self.runner, '_make_provider', side_effect=AssertionError('disabled CLI must not start')):
            self.runner.kick()
            self.runner._worker.join(3)
        blocked = self.engine.snapshot(legacy['id'])
        self.assertEqual(blocked['jobs'][-1]['status'], 'BLOCKED')
        switched = self.client.post(endpoint, headers=self.headers, json={'command': 'configure_provider',
            'payload': {'provider': 'codex'}, 'operation_id': 'switch', 'expected_revision': blocked['revision']})
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json['execution'], {'provider': 'codex', 'model': None, 'effort': None})
        self.assertEqual(switched.json['jobs'][-1]['provider_selection'], queued['jobs'][-1]['provider_selection'])

    def test_disabled_default_is_rejected(self):
        for provider in ('claude', 'gemini', 'opencode'):
            with self.subTest(provider=provider), self.assertRaises(ContractError):
                Runner(self.engine, ROOT, default_provider=provider)
