"""Text-check boundaries and real PostgreSQL review transactions, no Qwen."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest

from test_profiles import ProfileDatabaseFixture, module
from psycopg.types.json import Jsonb
from quality import REVIEW_ITEMS, assess_scope, check_output
from reviews import retain_feedback_issues


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.ctx = {'topic':module.TOPICS['linear'], 'materials':[
            {'id':10,'knowledge_id':1}, {'id':20,'knowledge_id':2}, {'id':30,'knowledge_id':3}]}
        self.goal = self.ctx['topic']['goal']

    def test_material_map_uses_snapshot_ids_and_checks_each_point(self):
        self.ctx['materials'].pop()
        scope = assess_scope(self.ctx, self.goal)
        self.assertEqual(scope['coverage'][0]['material_ids'], [10])
        self.assertFalse(scope['coverage'][2]['has_material'])
        self.assertIn('知识点 3', scope['warnings'][0])

    def test_custom_and_outside_goals_are_hints_not_semantic_guarantees(self):
        scope = assess_scope(self.ctx, '不要使用贝叶斯公式，解释去括号。')
        self.assertTrue(scope['custom_goal'])
        self.assertTrue(any('否定' in s for s in scope['warnings']))
        self.assertTrue(any('分配律' in s for s in scope['warnings']))
        self.assertFalse(assess_scope(self.ctx,self.goal)['warnings'])

    def test_basic_scope_does_not_advertise_transposition(self):
        self.ctx['topic'] = module.TOPICS['basic']
        scope = assess_scope(self.ctx,'理解移项，解两步方程。')
        self.assertEqual([r['knowledge_id'] for r in scope['coverage']],[1,2])
        self.assertTrue(any('未包含' in s for s in scope['warnings']))

    def test_structure_length_and_citations_report_objective_findings(self):
        report = check_output('# 标题\n## 学习目标\n证据 [M20] [M999]', self.ctx,self.goal)
        checks = {c['code']:c for c in report['automatic']}
        self.assertEqual(checks['title']['status'],'pass')
        self.assertEqual(checks['sections']['status'],'warning')
        self.assertEqual(checks['length']['status'],'warning')
        self.assertEqual(report['citation_locations'][0]['line'],3)
        self.assertEqual(report['citation_locations'][0]['invalid_ids'],['999'])
        self.assertTrue(all(c['status']=='needs_review' for c in report['teacher_review']))

    def test_full_format_still_needs_teacher_math_and_source_review(self):
        content = '# 标题\n' + '\n'.join('## '+s for s in ['学习目标','本次学习安排','核心讲解','例题与检验','易错点与小结']) + '\n' + '字'*650 + '[M10]'
        report = check_output(content,self.ctx,self.goal)
        self.assertTrue(all(c['status']=='pass' for c in report['automatic']))
        self.assertEqual(len(report['teacher_review']),5)
        self.assertNotIn('correct',report)

    def test_bracket_claim_warns_even_with_valid_material_number(self):
        report = check_output('# 讲义\n去括号使用分配律 [M20]',self.ctx,self.goal)
        c = {c['code']:c for c in report['automatic']}
        self.assertEqual(c['citation_ids']['status'],'pass')
        self.assertEqual(c['bracket_evidence']['status'],'warning')


class ReviewIntegrationTests(ProfileDatabaseFixture):
    def bundle(self):
        return self.client.get(f'/api/lectures/{self.lecture_id}/review').get_json()

    def revision(self, **overrides):
        return {**{'expected_revision':self.bundle()['expected_revision'], 'base_revision':0,
            'content':'# 教师修订\n明确两边同减的依据 [M3]', 'status':'draft', 'recorder':'测试教师',
            'reason':'解释等式变形，保存为草稿等待审核。', 'review_seconds':60,
            'checklist':{k:False for k in REVIEW_ITEMS}, 'resolutions':[], 'confirmed':True}, **overrides}

    def issue(self, **overrides):
        return {**{'expected_revision':self.bundle()['expected_revision'], 'target_revision':0,
            'category':'citation_support', 'excerpt':'历史原稿 [M3]', 'detail':'测试：需要核查移项依据是否被来源支持。',
            'material_id':3,'support':'unclear','recorder':'测试教师','confirmed':True},**overrides}

    def post_revision(self,data=None):
        return self.client.post(f'/api/lectures/{self.lecture_id}/revisions',json=data or self.revision())

    def post_issue(self,data=None):
        return self.client.post(f'/api/lectures/{self.lecture_id}/issues',json=data or self.issue())

    def test_read_checks_never_rewrite_old_content_or_citation_check(self):
        with self.fixture_db() as c:
            before = c.execute('SELECT * FROM lectures WHERE id=%s',(self.lecture_id,)).fetchone()
        data = self.client.get(f'/api/lectures/{self.lecture_id}').get_json()
        self.assertEqual(data['quality_report_origin'],'本次读取时检查，未改写原稿')
        self.assertEqual(data['quality_report']['version'],'math-text-check-v1+lecture-workflow-v2')
        with self.fixture_db() as c:
            self.assertEqual(before,c.execute('SELECT * FROM lectures WHERE id=%s',(self.lecture_id,)).fetchone())

    def test_scope_api_uses_custom_goal_and_topic_materials(self):
        response=self.client.get('/api/context',query_string={'student_id':2,'topic':'basic','goal':'解释移项和贝叶斯公式。'})
        self.assertEqual(response.status_code,200)
        scope=response.get_json()['scope_check']
        self.assertEqual([m['knowledge_id'] for m in scope['coverage']],[1,2])
        self.assertTrue(any('贝叶斯' in w for w in scope['warnings']))
        self.assertTrue(any('未包含' in w for w in scope['warnings']))
        self.assertEqual(self.client.get('/api/context',query_string={'student_id':2,'topic':'basic','goal':'字'*401}).status_code,400)

    def test_original_prompt_and_request_stay_unchanged_after_teacher_revision(self):
        def model(prepared,on_attempt):
            on_attempt()
            return module.ModelResult('# 生成测试原稿\n内容 [M3]')
        with patch.object(module,'call_qwen',side_effect=model):
            data=self.client.post('/api/generate',json={'student_id':2,'topic':'linear'}).get_json()
        self.lecture_id=data['id']
        trace=self.client.get(f'/api/generation-runs/{data["generation_run_id"]}').get_json()
        self.assertEqual(self.post_revision().status_code,201)
        self.assertEqual(trace,self.client.get(f'/api/generation-runs/{data["generation_run_id"]}').get_json())

    def test_issue_locates_actual_version_and_support_judgment(self):
        r=self.post_issue()
        self.assertEqual(r.status_code,201)
        issue=r.get_json()['issues'][0]
        self.assertEqual((issue['start_line'],issue['end_line']),(2,2))
        self.assertEqual(issue['support'],'unclear')
        self.assertEqual(issue['content_sha256'],hashlib.sha256(self.bundle()['original']['content'].encode()).hexdigest())
        self.assertEqual(self.bundle()['expected_revision'],1)

    def test_invalid_issue_anchor_source_and_payload_roll_back(self):
        for change in [{'excerpt':'does not exist'}, {'material_id':999}, {'material_id':True},
            {'support':'automatic'}, {'category':[]}, {'target_revision':99}, {'confirmed':False},
            {'expected_revision':False}, {'category':'math','support':'supported'}, {'detail':'x'*1501}]:
            with self.subTest(change=change):
                self.assertIn(self.post_issue(self.issue(**change)).status_code,[400,404])
        self.assertEqual(self.bundle()['expected_revision'],0)
        self.assertEqual(self.bundle()['issues'],[])

    def test_ambiguous_excerpt_requires_more_context(self):
        self.post_revision(self.revision(content='# 测试\n重复\n重复'))
        r=self.post_issue(self.issue(target_revision=1,excerpt='重复'))
        self.assertEqual(r.status_code,400)
        self.assertIn('多次',r.get_json()['error'])

    def test_revision_keeps_original_and_records_diff_reason_and_seconds(self):
        original=self.bundle()['original']['content']
        result=self.post_revision().get_json()
        revision=result['revisions'][0]
        self.assertEqual(revision['revision_number'],1)
        self.assertEqual(revision['base_revision'],0)
        self.assertEqual(revision['review_seconds'],60)
        self.assertTrue(revision['changes']['changed_blocks'])
        self.assertIn('before_start',revision['changes']['changed_blocks'][0])
        self.assertEqual(result['original']['content'],original)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}').get_json()['content'],original)

    def test_reviewed_requires_checklist_resolutions_and_valid_ids(self):
        all_checked={k:True for k in REVIEW_ITEMS}
        self.assertEqual(self.post_revision(self.revision(status='reviewed')).status_code,400)
        issue=self.post_issue().get_json()['issues'][0]
        self.assertEqual(self.post_revision(self.revision(status='reviewed',checklist=all_checked)).status_code,400)
        resolutions=[{'issue_id':issue['id'],'note':'已改写对应表述并核查材料。'}]
        self.assertEqual(self.post_revision(self.revision(status='reviewed',checklist=all_checked,resolutions=resolutions,content='# 测试 [M999]')).status_code,400)
        r=self.post_revision(self.revision(status='reviewed',checklist=all_checked,resolutions=resolutions))
        self.assertEqual(r.status_code,201)
        self.assertEqual(r.get_json()['pending_issue_ids'],[])
        self.assertEqual(r.get_json()['revisions'][0]['status'],'reviewed')

    def test_new_issue_after_review_keeps_approval_audit_and_becomes_pending(self):
        self.post_revision(self.revision(status='reviewed',checklist={k:True for k in REVIEW_ITEMS}))
        r=self.post_issue(self.issue(target_revision=1,excerpt='明确两边同减的依据 [M3]'))
        self.assertEqual(r.status_code,201)
        self.assertEqual(r.get_json()['revisions'][0]['status'],'reviewed')
        self.assertEqual(len(r.get_json()['pending_issue_ids']),1)

    def test_old_page_conflicts_after_either_issue_or_revision(self):
        old=self.revision()
        self.post_issue()
        self.assertEqual(self.post_revision(old).status_code,409)
        fresh=self.revision()
        self.post_revision(fresh)
        self.assertEqual(self.post_issue(self.issue(expected_revision=1)).status_code,409)
        self.assertEqual(len(self.bundle()['revisions']),1)

    def test_concurrent_saves_serialize_to_one_success_one_conflict(self):
        payload=self.revision()
        def save(_):
            with module.app.test_client() as client:
                return client.post(f'/api/lectures/{self.lecture_id}/revisions',json=payload).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(save,range(2)))
        self.assertEqual(sorted(statuses),[201,409])

    def test_revision_transaction_failure_keeps_event_and_content_unchanged(self):
        with self.fixture_db() as c:
            c.execute("ALTER TABLE lecture_revisions ADD CONSTRAINT fixture_fail CHECK (reason <> 'force failure')")
        self.assertEqual(self.post_revision(self.revision(reason='force failure')).status_code,503)
        self.assertEqual(self.bundle()['expected_revision'],0)
        self.assertEqual(self.bundle()['revisions'],[])

    def test_revision_validation_is_bounded_and_uses_own_issue_ids(self):
        for change in [{'content':' '},{'content':'字'*16001},{'review_seconds':True}, {'review_seconds':-1},
            {'review_seconds':86401},{'checklist':{}},{'status':[]}, {'base_revision':True},
            {'resolutions':[{'issue_id':999,'note':'bad'}]}, {'confirmed':False}]:
            with self.subTest(change=change):
                self.assertEqual(self.post_revision(self.revision(**change)).status_code,400)
        self.assertEqual(self.bundle()['revisions'],[])
        self.assertEqual(self.post_revision(self.revision(content='字'*16000)).status_code,201)

    def test_original_and_revision_downloads_are_explicit_and_keep_sources(self):
        self.post_revision()
        original=self.client.get(f'/api/lectures/{self.lecture_id}/download').get_data(as_text=True)
        modified=self.client.get(f'/api/lectures/{self.lecture_id}/revisions/1/download').get_data(as_text=True)
        self.assertIn('模型原稿',original)
        self.assertIn('历史原稿',original)
        self.assertIn('教师修订稿',modified)
        self.assertIn('保存时审核状态：草稿',modified)
        self.assertIn('明确两边同减',modified)
        self.assertIn(self.before_context['materials'][0]['source_url'],modified)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}/revisions/99/download').status_code,404)

    def test_missing_lecture_and_cross_origin_review_writes_are_rejected(self):
        self.assertEqual(self.client.get('/api/lectures/999/review').status_code,404)
        self.assertEqual(self.client.post('/api/lectures/999/revisions',json=self.revision()).status_code,404)
        self.assertEqual(self.client.post(f'/api/lectures/{self.lecture_id}/revisions',json=self.revision(),headers={'Origin':'https://invalid.example'}).status_code,403)

    def test_feedback_import_is_idempotent_and_requires_original_fingerprint(self):
        issue=self.issue(category='math',support='not_applicable',material_id=None)
        body=self.bundle()['original']['content']
        catalog=[{**issue,'key':'fixture-feedback','lecture_id':self.lecture_id,'content_sha256':hashlib.sha256(body.encode()).hexdigest()}]
        with patch('reviews.Path.read_text',return_value=json.dumps(catalog)):
            with self.fixture_db() as c:
                retain_feedback_issues(c)
                retain_feedback_issues(c)
        self.assertEqual(len(self.bundle()['issues']),1)
        self.assertEqual(self.bundle()['expected_revision'],1)
        catalog[0]['key']='different-feedback'
        catalog[0]['content_sha256']='0'*64
        with patch('reviews.Path.read_text',return_value=json.dumps(catalog)):
            with self.fixture_db() as c:
                retain_feedback_issues(c)
        self.assertEqual(len(self.bundle()['issues']),1)
