"""Regression checks for the teacher's lecture workflow; models are always mocked."""
import copy
from io import BytesIO
import json
import unittest
from html.parser import HTMLParser
from pathlib import Path
from threading import Event
from unittest.mock import patch

from test_profiles import ProfileDatabaseFixture, module
from embed import QwenEmbedder
from model_settings import probe
from prompts import prepare_request
from quality import check_output
from teaching import DEFAULTS, length_range
from jobs import JobQueue
from lecture_policy import budget_context, finalize_context, LectureInputError
from reviews import REVIEW_ITEMS


class LectureOptimizationTests(ProfileDatabaseFixture):
    def test_copied_materials_are_independent_but_diagnoses_and_template_follow_origin(self):
        original = module.get_context(2, 'linear')
        copied = self.client.post('/api/course-tasks/linear/copy').json
        current = module.get_context(2, copied['key'])
        self.assertEqual([s['state'] for s in current['states']], [s['state'] for s in original['states']])
        self.assertTrue(prepare_request(current, current['topic']['goal'], 'fixture').prompt_version.startswith('lecture-math-v3'))
        self.assertNotEqual([m['id'] for m in current['materials']], [m['id'] for m in original['materials']])
        with patch.object(module,'call_qwen',return_value=module.ModelResult('# 标题\n[M4]')):
            generated=self.client.post('/api/generate',json=dict(student_id=2,topic=copied['key']))
        self.assertEqual(generated.status_code,200,generated.json)
        self.save_evidence()
        updated = module.get_context(2, copied['key'])
        self.assertEqual(updated['states'][-1]['state'], 'partial')
        self.assertTrue(updated['states'][-1]['recent_evidence'])
        compared=self.client.get(f'/api/lectures/{generated.json["id"]}/profile-comparison').json
        self.assertEqual(compared['points'][-1]['current']['state'],'partial')
        self.assertEqual(compared['points'][-1]['new_evidence_count'],1)
        profile=self.client.get('/api/students/2/profile').json
        self.assertEqual(len(profile['states']),3)
        again = self.client.post('/api/course-tasks/'+copied['key']+'/copy').json
        self.assertEqual(module.get_context(2, again['key'])['states'][-1]['state'], 'partial')

    def test_invalid_visual_ids_fail_generation_even_without_visual_input(self):
        for evidence in (None, [], [{'citation_id':'V1', 'content':'测试图片'}]):
            ctx = copy.deepcopy(self.before_context)
            if evidence is not None:ctx['visual_evidence'] = evidence
            module.GENERATION_LOCK.acquire()
            with patch.object(module, 'call_qwen', return_value=module.ModelResult('# 测试\n不存在的图片 [V999]')):
                result, status = module.generate_locked(2, 'linear', ctx['topic']['goal'], frozen=ctx)
            self.assertEqual(status, 502, result)
            self.assertTrue(result['failure_recorded'])
            self.assertEqual(self.client.get('/api/generation-runs/'+str(result['generation_run_id'])).json['error_code'],'invalid_citation')

    def test_visual_warning_distinguishes_absent_use_from_invalid_ids(self):
        ctx = copy.deepcopy(self.before_context)
        ctx['visual_evidence'] = [{'citation_id':'V1'}]
        report = check_output('# 标题\n可不使用不相关图片。', ctx, ctx['topic']['goal'])
        self.assertEqual(report.get('invalid_source_ids'), [])
        report = check_output('# 标题\n引用 [V999]', ctx, ctx['topic']['goal'])
        self.assertEqual(report['invalid_source_ids'], ['V999'])

    def test_stable_topic_key_is_returned_by_catalog_and_saved_lecture(self):
        topics = self.client.get('/api/bootstrap').json['topics']
        self.assertEqual(topics['linear'].get('key'), 'linear')
        with patch.object(module, 'call_qwen', return_value=module.ModelResult('# 标题\n[M3]')):
            row = self.client.post('/api/generate', json=dict(student_id=2, topic='linear')).json
        self.assertEqual(self.client.get('/api/lectures/'+str(row['id'])).json.get('topic_key'), 'linear')

    def test_teacher_review_rejects_invalid_visual_and_can_record_valid_visual_support(self):
        with self.fixture_db() as conn:
            ctx = copy.deepcopy(self.before_context)
            ctx['visual_evidence'] = [{'citation_id':'V1','content':'图片描述','image_id':'fixture'}]
            from psycopg.types.json import Jsonb
            conn.execute('UPDATE lectures SET context=%s,content=%s WHERE id=%s',
                         (Jsonb(ctx),'# 核对图片\n独有的判断 [V1]',self.lecture_id))
        payload=dict(expected_revision=0,base_revision=0,content='# 标题\n[V999]',recorder='测试教师',reason='核对来源',
                     status='reviewed',review_seconds=0,checklist={k:True for k in REVIEW_ITEMS},resolutions=[],confirmed=True)
        self.assertEqual(self.client.post(f'/api/lectures/{self.lecture_id}/revisions',json=payload).status_code,400)
        issue=dict(expected_revision=0,target_revision=0,category='citation_support',excerpt='独有的判断 [V1]',detail='图中支持此判断，仍需人工核对。',
                   material_id=None,visual_citation_id='V1',support='supported',recorder='测试教师',confirmed=True)
        result=self.client.post(f'/api/lectures/{self.lecture_id}/issues',json=issue)
        self.assertEqual(result.status_code,201,result.json)
        self.assertEqual(result.json['issues'][0]['visual_citation_id'],'V1')

    def test_plan_edit_is_frozen_and_none_mode_excludes_individual_plan(self):
        entered=Event();release=Event()
        def model(prepared,attempted):
            attempted();entered.set();release.wait(5)
            return module.ModelResult('# 标题\n教师安排位置 [K1] [K2] [K3] [M3]')
        queue=JobQueue(lambda:module.db(),module.execute_job)
        with patch.object(module,'TASKS',queue),patch.object(module,'call_qwen',model):
            try:
                result=self.client.post('/api/jobs',json=dict(student_id=2,topic='linear',plan_overrides={'3':'先分析错例，再逐步解释移项依据。'},teaching_options={**DEFAULTS,'length_policy':'duration-v2','duration':15}))
                self.assertEqual(result.status_code,202,result.json)
                self.assertTrue(entered.wait(5))
                self.save_evidence()
                run=self.client.get('/api/generation-runs/'+str(result.json['run_id'])).json
                plan=run['context']['personalization_plan'][-1]
                self.assertEqual(plan['state'],'needs_support')
                self.assertEqual(plan['action'],'先分析错例，再逐步解释移项依据。')
                self.assertIn(plan['action'],run['request_body'])
            finally:release.set();queue.wait_idle(5);queue.close()
        none=module.get_context(2,'linear','none')
        self.assertEqual(none['personalization_plan'],[])
        self.assertNotIn('先分析错例',prepare_request(none,none['topic']['goal'],'fixture').body)
        with self.assertRaises(LectureInputError):finalize_context(none,none['topic']['goal'],{'3':'个人观察'})

    def test_cancel_pending_job_is_idempotent_and_never_calls_model(self):
        entered=Event();release=Event();calls=[]
        def model(prepared,attempted):
            calls.append(prepared.sha256);attempted();entered.set();release.wait(5)
            return module.ModelResult('# 标题\n[M3]')
        queue=JobQueue(lambda:module.db(),module.execute_job)
        with patch.object(module,'TASKS',queue),patch.object(module,'call_qwen',model):
            try:
                first=self.client.post('/api/jobs',json=dict(student_id=2,topic='linear')).json
                self.assertTrue(entered.wait(5))
                second=self.client.post('/api/jobs',json=dict(student_id=2,topic='linear')).json
                cancel=self.client.post('/api/jobs/'+second['id']+'/cancel')
                self.assertEqual(cancel.status_code,200,cancel.json)
                self.assertEqual(cancel.json['status'],'cancelled')
                self.assertEqual(self.client.post('/api/jobs/'+second['id']+'/cancel').status_code,200)
                self.assertEqual(self.client.post('/api/jobs/'+first['id']+'/cancel').status_code,409)
                run=self.client.get('/api/generation-runs/'+str(second['run_id'])).json
                self.assertIsNone(run['attempted_at'])
            finally:release.set();queue.wait_idle(5);queue.close()
        self.assertEqual(len(calls),1)


class LecturePolicyTests(unittest.TestCase):
    def test_workspace_has_unique_static_ids_for_navigation_and_dialogs(self):
        class Ids(HTMLParser):
            def __init__(self):super().__init__();self.ids=[]
            def handle_starttag(self,tag,attrs):
                for name,value in attrs:
                    if name=='id' and '{{' not in value:self.ids.append(value)
        parser=Ids();parser.feed((Path(__file__).parents[1]/'templates'/'index.html').read_text())
        self.assertEqual(len(parser.ids),len(set(parser.ids)))

    def test_long_materials_are_bounded_and_source_ranges_are_exact(self):
        ctx={'student':{},'topic':{'knowledge_ids':[1,2]},'states':[], 'materials':[
            dict(id=1,knowledge_id=1,content='条件概率的定义与正分母。'*14000),dict(id=2,knowledge_id=2,content='贝叶斯。'*5000)]}
        frozen=copy.deepcopy(ctx)
        result=budget_context(ctx,'条件概率与贝叶斯')
        self.assertEqual(ctx,frozen)
        self.assertLessEqual(result['input_budget']['material_characters'],16000)
        self.assertEqual({m['knowledge_id'] for m in result['materials']},{1,2})
        for old,new in zip(ctx['materials'],result['materials']):
            self.assertEqual(new['content'],'\n…\n'.join(old['content'][a:b] for a,b in new['selection']['ranges']))
        self.assertEqual(result,budget_context(ctx,'条件概率与贝叶斯'))
        with self.assertRaises(LectureInputError):budget_context({**ctx,'topic':{'lesson_plan':'字'*8001}},'目标')

    def test_v2_length_depends_on_duration_and_teacher_can_override(self):
        short = {**DEFAULTS, 'duration':5, 'length_policy':'duration-v2'}
        long = {**short, 'duration':180}
        self.assertLess(length_range(short, True)[1], length_range(long, True)[0])
        self.assertEqual(length_range({**short,'target_characters':1200}, True), (960,1440))

    def test_keyless_probe_and_real_embedding_transport_agree(self):
        channel = dict(base_url='http://127.0.0.1:11434/v1', model='fixture', api_key='')
        response = json.dumps({'data':[{'embedding':[1.,0.]}]}).encode()
        with patch('urllib.request.urlopen', side_effect=lambda *a, **kw: BytesIO(response)) as send:
            self.assertEqual(probe('embedding',channel)['dimensions'], 2)
            client = QwenEmbedder(model='fixture',base=channel['base_url'],key='')
            self.assertTrue(client.configured())
            self.assertEqual(client.embed(['文本']), [[1.,0.]])
            self.assertIsNone(send.call_args.args[0].get_header('Authorization'))
