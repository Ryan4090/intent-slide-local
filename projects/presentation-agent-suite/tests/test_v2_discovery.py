"""Automatic discovery must not submit jobs or alter a queued selection."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.server import create_app

ROOT = Path(__file__).resolve().parents[3]


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.engine=Engine(Path(self.temp.name))
        self.runner=Runner(self.engine,ROOT,default_provider='auto')
        self.calls=[]
        owner=self
        class Probe:
            def __init__(self,name): self.name=name
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
            def close(self): pass
            def preflight(self):
                owner.calls.append(self.name)
                return {'ready':self.name in {'claude','opencode'}, 'auto_connect':self.name=='claude', 'models':[]}
        self.runner._make_provider=Probe

    def tearDown(self):
        if self.runner._discovery_thread:
            self.runner._discovery_thread.join(timeout=4)
        self.runner.close()
        self.temp.cleanup()

    def finish(self):
        self.runner._discovery_thread.join(timeout=4)
        self.assertFalse(self.runner._discovery_thread.is_alive())
        return self.runner.capabilities()

    def test_auto_selects_ready_subscription_without_running_a_model(self):
        self.runner.discover()
        result=self.finish()
        self.assertEqual(result['discovery']['status'],'COMPLETE')
        self.assertEqual(result['default_provider'],'claude')
        self.assertEqual(result['recommended_provider'],'claude')
        self.assertEqual(self.engine.list(),[])
        self.runner.discover()
        self.assertEqual(len(self.calls),4)

    def test_discovery_defers_during_active_job(self):
        self.runner._active={'id':'fixture-active'}
        result=self.runner.discover(refresh=True)
        self.assertEqual(result['discovery']['status'],'DEFERRED')
        self.assertEqual(self.calls,[])
        self.runner._active=None

    def test_discovery_endpoint_requires_session_and_csrf(self):
        app=create_app(self.engine, ROOT/'projects/presentation-agent-suite/console',self.runner,bootstrap_token='fixture-token')
        client=app.test_client()
        self.assertEqual(client.post('/api/v2/capabilities/discover',json={}).status_code,401)
        session=client.post('/api/v2/session',json={'bootstrap_token':'fixture-token'}).get_json()
        self.assertEqual(client.post('/api/v2/capabilities/discover',json={}).status_code,403)
        headers={'X-CSRF-Token':session['csrf_token']}
        self.assertEqual(client.post('/api/v2/capabilities/discover',json={'refresh':'yes'},headers=headers).status_code,422)
        self.assertEqual(client.post('/api/v2/capabilities/discover',json={},headers=headers).status_code,200)
        self.finish()

    def test_existing_run_selection_stays_unchanged(self):
        run=self.engine.create('Existing','Fixture request','provided_only','create-fixture',execution={'provider':'codex','model':None,'effort':None})
        before=self.engine.snapshot(run['id'])['execution']
        self.runner.discover()
        self.finish()
        self.assertEqual(self.engine.snapshot(run['id'])['execution'],before)


if __name__=='__main__': unittest.main()
