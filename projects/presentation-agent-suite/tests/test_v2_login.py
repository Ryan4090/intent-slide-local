"""Login button never accepts arbitrary commands or handles credentials."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from presentation_agents.v2.login import BrowserLogin
from presentation_agents.v2.stdio_transport import StdioTransport
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.server import create_app

ROOT = Path(__file__).resolve().parents[3]


class LoginTests(unittest.TestCase):
    def run_login(self, script, timeout=3):
        calls, finished = [], []
        def launch(command, **kwargs):
            calls.append(command)
            return StdioTransport.launch([sys.executable, '-c', script], **kwargs)
        login = BrowserLogin(finished=lambda: finished.append(True), launch=launch, timeout=timeout)
        self.addCleanup(login.close)
        with patch.dict(os.environ, {'INTENT_SLIDE_CODEX': sys.executable}):
            login.start()
            login.start()
        deadline = time.monotonic() + 5
        while login.snapshot()['status'] in {'STARTING', 'RUNNING'} and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(calls, [[sys.executable, 'login']])
        self.assertEqual(finished, [True])
        return login.snapshot()

    def test_native_success_contains_no_cli_output(self):
        result = self.run_login("import time; print('fixture-private-url-and-token'); time.sleep(.1)")
        self.assertEqual(result, {'status':'COMPLETE', 'provider':'codex'})

    def test_timeout_terminates_owned_process(self):
        self.assertEqual(self.run_login('import time; time.sleep(30)', .1)['status'], 'TIMEOUT')

    def test_nonzero_exit_is_not_login_success(self):
        self.assertEqual(self.run_login('import sys,time; time.sleep(.1); sys.exit(1)')['status'], 'FAILED')

    def test_login_endpoint_guards_auth_csrf_provider_and_active_work(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Runner(Engine(Path(directory)), ROOT)
            self.addCleanup(runner.close)
            app = create_app(runner.engine, ROOT/'projects/presentation-agent-suite/console', runner, bootstrap_token='fixture')
            client = app.test_client()
            endpoint = '/api/v2/providers/login'
            self.assertEqual(client.post(endpoint,json={'provider':'codex'}).status_code,401)
            session = client.post('/api/v2/session',json={'bootstrap_token':'fixture'}).get_json()
            self.assertEqual(client.post(endpoint,json={'provider':'codex'}).status_code,403)
            headers = {'X-CSRF-Token':session['csrf_token']}
            for value in [{'provider':'claude'}, {'provider':'codex','command':'untrusted'}, {'provider':['codex']}]:
                self.assertEqual(client.post(endpoint,json=value,headers=headers).status_code,422)
            runner._active={'run_id':'fixture'}
            self.assertEqual(client.post(endpoint,json={'provider':'codex'},headers=headers).status_code,409)
            runner._active=None
            runner._provider_capabilities['codex']['ready']=True
            self.assertEqual(client.post(endpoint,json={'provider':'codex'},headers=headers).get_json()['status'],'ALREADY_CONNECTED')


if __name__ == '__main__':
    unittest.main()
