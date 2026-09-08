"""The product experience uses the same authenticated, revision-bound surface."""
import tempfile
import unittest
from pathlib import Path

from presentation_agents.v2.engine import Engine
from presentation_agents.v2.server import create_app


class ExperienceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name))
        console = Path(__file__).resolve().parents[1] / 'console'
        self.app = create_app(self.engine, console, bootstrap_token='experience-fixture')
        self.client = self.app.test_client()
        response = self.client.post('/api/v2/session', json={'bootstrap_token':'experience-fixture'})
        self.headers = {'X-CSRF-Token':response.json['csrf_token']}

    def tearDown(self):
        self.temp.cleanup()

    def create(self):
        response = self.client.post('/api/v2/runs', headers=self.headers, json={
            'title':'의도 경험', 'request':'임원에게 운영 자동화 시범 사업 승인을 받고 싶어요',
            'source_mode':'provided_only', 'operation_id':'create', 'design_preference':{'preset_id':'signal'}})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_catalog_requires_session_and_serves_all_ten_preview_assets(self):
        self.assertEqual(self.app.test_client().get('/api/v2/design-presets').status_code,401)
        catalog = self.client.get('/api/v2/design-presets').json['presets']
        self.assertEqual(len(catalog),10)
        for preset in catalog:
            response = self.client.get(preset['preview_url'])
            self.assertEqual(response.status_code,200,preset['id'])
            self.assertIn(b'<svg',response.data)
            response.close()

    def test_create_list_snapshot_and_commands_share_activity_without_overwriting_ledger(self):
        run = self.create()
        self.assertEqual(run['design_preference'],{'preset_id':'signal'})
        activity = run['research_activity']
        self.assertEqual(activity['summary']['source_files'],0)
        self.assertIsNone(activity['storage']['bundle'])
        self.assertTrue(Path(activity['storage']['database']).is_file())
        for snapshot in [self.client.get('/api/v2/runs').json['runs'][0],self.client.get(f"/api/v2/runs/{run['id']}").json]:
            self.assertEqual(snapshot['research_activity']['schema_version'],'research-activity.v1')
        response = self.client.post(f"/api/v2/runs/{run['id']}/commands", headers=self.headers, json={
            'command':'select_design','payload':{'preset_id':'evidence'},'operation_id':'design','expected_revision':run['revision']})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['design_preference'],{'preset_id':'evidence'})
        self.assertIn('research_activity',response.json)
        stored = self.engine.store.read(run['id']).get('research_activity',{})
        self.assertNotEqual(stored.get('schema_version'),'research-activity.v1')

    def test_invalid_selection_and_stale_revision_do_not_change_user_design(self):
        run = self.create()
        endpoint=f"/api/v2/runs/{run['id']}/commands"
        request={'command':'select_design','operation_id':'invalid','expected_revision':run['revision'],'payload':{'preset_id':'unknown'}}
        self.assertEqual(self.client.post(endpoint,headers=self.headers,json=request).status_code,422)
        request.update(operation_id='stale',expected_revision=0,payload={'preset_id':'pitch'})
        self.assertEqual(self.client.post(endpoint,headers=self.headers,json=request).status_code,409)
        self.assertEqual(self.engine.snapshot(run['id'])['design_preference'],{'preset_id':'signal'})
