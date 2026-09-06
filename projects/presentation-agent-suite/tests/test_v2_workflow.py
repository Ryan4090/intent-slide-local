"""End-to-end state tests; trusted G4 and reviewer observations are test fixtures.

Actual package/render checks are independently exercised in test_v2_verification.
These tests must never be reported as live AI or real user approval evidence.
"""
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from presentation_agents.v2.contracts import file_hash
from presentation_agents.v2.engine import Engine, ContractError
from test_v2_engine import intent


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name) / 'control')
        # G4 environment is a trusted-adapter fixture only in this state test.
        self.environment = patch('presentation_agents.v2.verification.receipt_environment_matches', return_value=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    def command(self, rid, name, payload=None):
        s = self.engine.snapshot(rid)
        return self.engine.command(rid, name, payload or {}, f"op-{s['revision']}", s['revision'])

    def approve(self, rid, gate):
        review = next(r for r in self.engine.snapshot(rid)['reviews'] if r['gate'] == gate and r['status'] == 'PENDING')
        return self.command(rid, 'approve', {'review_id': review['id'], 'bundle_sha256': review['bundle_sha256']})

    def prepare(self, count):
        r = self.engine.create(f'[검증용] {count}장 운영 개선', '제공 자료만', 'provided_only', f'c-{count}')
        rid = r['id']
        s = self.engine.publish(rid, 'intent', intent(count))
        self.approve(rid, 'G1')
        research = {'sources': [], 'claims': [], 'packets': [], 'messages': [{'slide_uid': p['uid'], 'message': '검토할 운영 개선 제안', 'claim_ids': []} for p in s['intent']['slides']], 'analysis': '# 운영 개선 제안\n외부 수치를 단정하지 않는 검토 안건입니다.', 'limitations': ['합성 검증 자료; 실제 사업 성과가 아님']}
        self.engine.publish(rid, 'research', research)
        self.assertEqual(self.approve(rid, 'G2')['progress']['percent'], 55)
        workspace = Path(self.tmp.name) / f'worker-{count}'
        workspace.mkdir()
        (workspace / 'proposal.md').write_text('검토용 디자인 제안')
        mapping = self.engine.import_worker_artifacts(rid, workspace, [{'key': 'p', 'path': 'proposal.md', 'kind': 'preview'}], 'design')
        direction = {'route': 'main-svg-generation', 'summary': '명확한 위계', 'design_spec': 'approved spec', 'spec_lock': 'approved lock', 'preview_artifact_ids': [mapping['p']]}
        self.engine.publish(rid, 'design_direction', direction)
        self.assertEqual(self.approve(rid, 'G3')['progress']['percent'], 65)
        return rid, workspace

    def candidate(self, rid, workspace):
        self.command(rid, 'run')
        job = self.engine.claim_job(rid)
        pages = []
        artifacts = []
        for index, page in enumerate(self.engine.snapshot(rid)['intent']['slides']):
            relative = f'fixture-page-{index}.svg'
            (workspace / relative).write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>fixture only</text></svg>')
            pages.append({'slide_uid': page['uid'], 'path': relative, 'claim_ids': []})
            artifacts.append({'path': relative, 'kind': 'page', 'sha256': file_hash(workspace / relative)})
        for relative, kind in [('fixture.pptx', 'pptx'), ('fixture-grid.png', 'contact_sheet')]:
            (workspace / relative).write_bytes(b'TRUSTED-ADAPTER-TEST-FIXTURE-NOT-A-DECK')
            artifacts.append({'path': relative, 'kind': kind, 'sha256': file_hash(workspace / relative)})
        receipt = {'verdict': 'PASS', 'route': 'main-svg-generation', 'pptx_path': 'fixture.pptx', 'contact_sheet_path': 'fixture-grid.png', 'renderer': 'TEST_FIXTURE', 'pptx_sha256': file_hash(workspace / 'fixture.pptx'), 'contact_sheet_sha256': file_hash(workspace / 'fixture-grid.png'), 'input_sha256': {}, 'artifacts': artifacts}
        return self.engine.accept_candidate(rid, workspace, {'pages': pages}, receipt, job_id=job['id'], input_hash=job['input_hash'])

    def review(self, rid, verdict, opened=True):
        self.command(rid, 'run')
        job = self.engine.claim_job(rid)
        s = self.engine.snapshot(rid)
        candidate = s['candidate']
        data = {'verdict': verdict, 'candidate_sha256': candidate['pptx_sha256'], 'contact_sheet_sha256': candidate['contact_sheet_sha256'], 'reviewed_slide_uids': [p['uid'] for p in s['intent']['slides']], 'findings': [] if verdict == 'PASS' else ['fixture overlap'], 'summary': 'test review'}
        render = next(a for a in s['artifacts'] if a['id'] == candidate['contact_sheet_artifact_id'])
        observations = {'images_viewed': [f"{render['id']}--{render['name']}"] if opened else []}
        return self.engine.accept_review(rid, data, observations, job_id=job['id'], input_hash=job['input_hash'])

    def test_five_ten_twelve_pages_reject_rework_release_and_tamper(self):
        for count in (5, 10, 12):
            rid, workspace = self.prepare(count)
            self.assertEqual(self.candidate(rid, workspace)['progress']['percent'], 95)
            rejected = self.review(rid, 'FAIL')
            self.assertEqual(rejected['status'], 'REWORK')
            self.assertLess(rejected['progress']['percent'], 95)
            self.candidate(rid, workspace)
            released = self.review(rid, 'PASS')
            self.assertEqual(released['progress']['percent'], 100)
            self.assertEqual(released['release']['pptx_artifact_id'], released['candidate']['pptx_artifact_id'])
            self.engine.artifact_path(rid, released['candidate']['pptx_artifact_id']).write_text('changed')
            stale = self.engine.snapshot(rid)
            self.assertLess(stale['progress']['percent'], 100)
            self.assertEqual(stale['status'], 'STALE')
            self.command(rid, 'request_changes', {'stage': 'design', 'reason': 'restore candidate'})

    def test_cannot_release_without_observed_visual_review(self):
        rid, workspace = self.prepare(1)
        self.candidate(rid, workspace)
        with self.assertRaises(ContractError):
            self.review(rid, 'PASS', opened=False)

    def test_messages_after_approval_are_assessed_before_downstream(self):
        rid, _ = self.prepare(2)
        s = self.command(rid, 'message', {'content': '청중을 초등학생으로, 장수를 12장으로 바꿔 줘'})
        self.assertEqual(s['active_phase'], 'clarification')
        queued = self.command(rid, 'run')
        self.assertEqual(queued['jobs'][-1]['phase'], 'clarification')

    def test_page_only_rework_keeps_other_pages_and_design_approval(self):
        rid, workspace = self.prepare(5)
        s = self.candidate(rid, workspace)
        page = next(a for a in s['artifacts'] if a['kind'] == 'page')
        revised = self.command(rid, 'request_changes', {'stage': 'design', 'reason': '이 페이지만 잘림 수정', 'artifact_ids': [page['id']]})
        self.assertTrue(any(a['gate'] == 'G3' and a['valid'] for a in revised['approvals']))
        self.assertEqual(sum(u['id'].startswith('page.') and u['status'] == 'VALID' for u in revised['units']), 4)
        self.assertEqual(revised['active_phase'], 'design_build')
        self.assertEqual(revised['progress']['percent'], 81)

    def test_replaced_export_does_not_contaminate_current_release(self):
        rid, workspace = self.prepare(2)
        old = self.candidate(rid, workspace)['candidate']
        self.review(rid, 'FAIL')
        current = self.candidate(rid, workspace)
        self.assertFalse(next(a for a in current['artifacts'] if a['id'] == old['pptx_artifact_id'])['valid'])
        self.engine.artifact_path(rid, old['pptx_artifact_id']).write_text('historical output altered')
        release = self.review(rid, 'PASS')
        self.assertEqual(release['progress']['percent'], 100)


if __name__ == '__main__':
    unittest.main()
