import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from presentation_agents.v2.engine import Engine
from presentation_agents.v2.server import create_app


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name))
        self.app = create_app(self.engine, Path(self.tmp.name), bootstrap_token="one-time")
        self.client = self.app.test_client()
        session = self.client.post('/api/v2/session', json={"bootstrap_token": "one-time"})
        self.headers = {"X-CSRF-Token": session.json["csrf_token"]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_auth_origin_csrf_and_bootstrap_replay(self):
        stranger = self.app.test_client()
        self.assertEqual(stranger.get('/api/v2/session').status_code, 401)
        self.assertEqual(stranger.get('/api/v2/runs').status_code, 401)
        self.assertEqual(stranger.post('/api/v2/session', json={"bootstrap_token": "one-time"}).status_code, 401)
        self.assertEqual(self.client.get('/api/v2/runs', headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.client.get('/api/v2/runs', headers={"Host": "evil.test"}).status_code, 403)
        self.assertEqual(self.client.post('/api/v2/runs', json={}).status_code, 403)

    def test_upload_actual_bytes_and_reject_other_run_artifact(self):
        run = self.engine.create('운영 검토', '내부 검토', 'provided_only', 'c1')
        uploaded = self.client.post(f"/api/v2/runs/{run['id']}/attachments", headers=self.headers,
            data={"file": (io.BytesIO(b'actual bytes'), 'source.txt'), "operation_id": "upload", "expected_revision": str(run['revision'])})
        self.assertEqual(uploaded.status_code, 200)
        artifact = uploaded.json['artifacts'][0]
        response = self.client.get(artifact['download_url'])
        self.assertEqual(response.data, b'actual bytes')
        response.close()
        other = self.engine.create('다른 작업', '다른', 'hybrid', 'c2')
        self.assertEqual(self.client.get(f"/api/v2/runs/{other['id']}/artifacts/{artifact['id']}").status_code, 422)

    def test_stale_command_is_409_and_no_raw_publish_endpoint(self):
        r = self.engine.create('제목', '요청', 'hybrid', 'c')
        base = {"expected_revision": r['revision'], "operation_id": 'op', "command": 'message', "payload": {"content": '수정'}}
        endpoint = f"/api/v2/runs/{r['id']}/commands"
        self.assertEqual(self.client.post(endpoint, json=base, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post(endpoint, json={**base, 'operation_id': 'other'}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post(endpoint, json={**base, 'expected_revision': 2, 'operation_id': 'forge', 'command': 'publish'}, headers=self.headers).status_code, 422)

    def test_malformed_json_object_has_contract_response(self):
        for body in ([], 'invalid', None):
            response = self.client.post('/api/v2/runs', json=body, headers=self.headers)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json['code'], 'CONTRACT_ERROR')

    def test_one_time_session_is_atomic_across_clients(self):
        app = create_app(self.engine, Path(self.tmp.name), bootstrap_token='concurrent')
        def authenticate(_):
            with app.test_client() as client:
                return client.post('/api/v2/session', json={'bootstrap_token': 'concurrent'}).status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(authenticate, range(8)))
        self.assertEqual(responses.count(200), 1)
        self.assertEqual(responses.count(401), 7)

    def test_sse_reconnect_replays_only_newer_events(self):
        r = self.engine.create('이벤트', '재연결', 'provided_only', 'events')
        first = self.engine.events(r['id'])[-1]['seq']
        self.engine.command(r['id'], 'message', {'content': '한 번만 수신'}, 'm', r['revision'])
        response = self.client.get(f"/api/v2/runs/{r['id']}/events", headers={'Last-Event-ID': str(first)}, buffered=False)
        chunk = next(response.response).decode()
        response.close()
        self.assertIn('command.message', chunk)
        self.assertNotIn('run.created', chunk)


if __name__ == '__main__':
    unittest.main()
