"""Common-goal comparisons preserve versions, input records and teaching boundaries."""
import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from psycopg.types.json import Jsonb
from test_profiles import ProfileDatabaseFixture, module
from comparisons import align_sections, split_sections
from prompts import prepare_request
from quality import REVIEW_ITEMS

TEXT = '# 同一课程\n\n## 学习目标\n理解移项的依据。\n\n## 本次学习安排\n先解释两边同减，再核查错误步骤。\n\n## 核心讲解\n两边同减 5 [M3]。\n\n## 例题与检验\n2x + 5 = 17，解得 x=6，并代入检验。\n\n## 易错点与小结\n保持等式两边平衡。\n'


class SectionComparisonTests(unittest.TestCase):
    def test_all_lines_duplicate_headings_and_third_level_headings_are_preserved(self):
        content='# 标题\r\n## 核心讲解\r\n第一段\r\n### 小节\r\n内容\r\n## 核心讲解\r\n第二段\r\n## C#\r\n语言名称\r\n'
        sections=split_sections(content)
        self.assertEqual(''.join(s['text'] for s in sections),content)
        self.assertEqual([(s['title'],s['occurrence']) for s in sections],
                         [('标题与开场',1),('核心讲解',1),('核心讲解',2),('C#',1)])

    def test_code_fence_heading_is_content_instead_of_a_real_section(self):
        content='## 核心讲解\n```text\n## 引号内文字\n```\n## 例题与检验\n例题\n'
        sections=split_sections(content)
        self.assertEqual([s['title'] for s in sections],['核心讲解','例题与检验'])
        self.assertEqual(''.join(s['text'] for s in sections),content)

    def test_repeated_sections_added_sections_and_source_line_numbers_are_unambiguous(self):
        left='开场\n## 核心讲解\n原第一段\n## 核心讲解\n原第二段\n'
        right='开场\n## 核心讲解\n原第一段\n## 核心讲解\n新第二段\n## 例题与检验\n新例题\n'
        sections,order_changed=align_sections(left,right)
        self.assertEqual([s['status'] for s in sections],['same','same','changed','right_only'])
        block=sections[2]['changes']['changed_blocks'][0]
        self.assertEqual((block['before_start'],block['after_start']),(5,5))
        self.assertTrue(order_changed)

    def test_unstructured_content_and_removed_sections_remain_visible(self):
        sections,_=align_sections('无分节旧稿\n','## 学习目标\n目标\n')
        self.assertEqual([s['status'] for s in sections],['left_only','right_only'])
        self.assertEqual(sections[0]['left']['text'],'无分节旧稿\n')

    def test_editor_trailing_newline_and_line_ending_conversion_are_not_content_changes(self):
        sections,_=align_sections('## 小结\r\n相同内容\r\n','## 小结\n相同内容')
        self.assertEqual(sections[0]['status'],'same')
        self.assertEqual(sections[0]['changes']['changed_blocks'],[])


class LectureComparisonTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        with self.fixture_db() as conn:
            conn.execute('UPDATE lectures SET content=%s WHERE id=%s',(TEXT,self.lecture_id))

    def make_lecture(self, context=None, sid=1, goal=None, topic=None, content=None):
        ctx=context if context is not None else module.get_context(sid,'linear')
        with self.fixture_db() as conn:
            row=conn.execute('''INSERT INTO lectures
                (student_id,topic,goal,context,content,model,elapsed_seconds,citation_check)
                VALUES (%s,%s,%s,%s,%s,'fixture-only',0,%s) RETURNING id''',
                (sid,topic or module.TOPICS['linear']['title'],goal if goal is not None else module.TOPICS['linear']['goal'],
                 Jsonb(ctx),content or TEXT.replace('先解释两边同减，再核查错误步骤。','先补充基础解释和简单示例。'),
                 Jsonb({'has_citations':True}))).fetchone()
        return row['id']

    def compare(self, other_id, **params):
        return self.client.get(f'/api/lectures/{self.lecture_id}/comparison',query_string={'other_id':other_id,**params})

    def options(self):
        return self.client.get(f'/api/lectures/{self.lecture_id}/comparison-options').get_json()

    def revision(self):
        response=self.client.post(f'/api/lectures/{self.lecture_id}/revisions',json={
            'expected_revision':0,'base_revision':0,'content':TEXT.replace('两边同减 5 [M3]。','两边同时减去 5，等式仍成立 [M3]。'),
            'status':'draft','recorder':'隔离测试教师','reason':'补充共同目标内的变形依据。','review_seconds':0,
            'checklist':{key:False for key in REVIEW_ITEMS},'resolutions':[],'confirmed':True})
        self.assertEqual(response.status_code,201)
        return response.get_json()['saved_revision']

    def trace(self, lecture_id, temperature=None):
        with self.fixture_db() as conn:
            lecture=conn.execute('SELECT * FROM lectures WHERE id=%s',(lecture_id,)).fetchone()
            prepared=prepare_request(lecture['context'],lecture['goal'],'fixture-only')
            body=json.loads(prepared.body)
            if temperature is not None:body['temperature']=temperature
            raw=json.dumps(body,ensure_ascii=False)
            fixture=SimpleNamespace(body=raw,sha256=hashlib.sha256(raw.encode()).hexdigest(),
                prompt_version=prepared.prompt_version,input_version=prepared.input_version)
            run_id=module.create_run(conn,lecture['student_id'],'linear',lecture['goal'],lecture['context'],fixture)
            conn.execute("UPDATE generation_runs SET status='succeeded',finished_at=now(),lecture_id=%s WHERE id=%s",(lecture_id,run_id))

    def test_different_students_same_actual_goal_can_compare_learning_arrangements(self):
        other=self.make_lecture()
        response=self.compare(other)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        data=response.get_json()
        self.assertEqual(data['kind'],'between_students')
        self.assertEqual(data['summary']['different_sections'],1)
        section=next(s for s in data['sections'] if s['title']=='本次学习安排')
        self.assertEqual(section['status'],'changed')
        self.assertIn('错误步骤',section['left']['text'])
        self.assertIn('基础解释',section['right']['text'])

    def test_original_and_saved_revision_are_separate_and_actual_status_is_retained(self):
        number=self.revision()
        options=self.options()
        base=next(c for c in options['candidates'] if c['id']==self.lecture_id)
        self.assertEqual([v['revision_number'] for v in base['versions']],[0,number])
        data=self.compare(self.lecture_id,right_revision=number).get_json()
        self.assertEqual(data['kind'],'revision')
        self.assertEqual(data['left']['status'],'unreviewed')
        self.assertEqual(data['right']['status'],'draft')
        self.assertEqual(data['right']['recorder'],'隔离测试教师')
        self.assertEqual(data['summary']['different_sections'],1)
        self.assertEqual(next(s['status'] for s in data['sections'] if s['title']=='核心讲解'),'changed')

    def test_candidates_and_endpoint_use_actual_goal_not_topic_default(self):
        custom='本次仅要求解释移项的依据。'
        with self.fixture_db() as conn:
            conn.execute('UPDATE lectures SET goal=%s WHERE id=%s',(custom,self.lecture_id))
        matching=self.make_lecture(goal=custom)
        different=self.make_lecture()
        self.assertEqual({c['id'] for c in self.options()['candidates']},{self.lecture_id,matching})
        self.assertEqual(self.compare(matching).get_json()['goal'],custom)
        response=self.compare(different)
        self.assertEqual(response.status_code,400)
        self.assertIn('实际教学目标',response.get_json()['error'])

    def test_same_goal_different_task_or_scope_is_excluded_and_rejected(self):
        task=self.make_lecture(topic='另一门课程任务')
        context=copy.deepcopy(self.before_context);context['topic']['knowledge_ids']=[1]
        scope=self.make_lecture(context=context)
        candidates={c['id'] for c in self.options()['candidates']}
        self.assertNotIn(task,candidates);self.assertNotIn(scope,candidates)
        self.assertEqual(self.compare(task).status_code,400)
        self.assertEqual(self.compare(scope).status_code,400)

    def test_changed_materials_and_model_are_visible_as_different_controls(self):
        context=copy.deepcopy(self.before_context)
        context['materials'][0]['content']='不同资料内容，不能将输出差异全归因于画像。'
        other=self.make_lecture(context=context)
        with self.fixture_db() as conn:
            conn.execute("UPDATE lectures SET model='another-fixture' WHERE id=%s",(other,))
        controls={c['code']:c['status'] for c in self.compare(other).get_json()['controls']}
        self.assertEqual(controls['materials'],'different')
        self.assertEqual(controls['model'],'different')

    def test_missing_legacy_scope_and_input_details_are_unavailable_without_backfill(self):
        context=copy.deepcopy(self.before_context);context['topic'].pop('knowledge_ids')
        other=self.make_lecture(context=context)
        controls={c['code']:c['status'] for c in self.compare(other).get_json()['controls']}
        self.assertEqual(controls['scope'],'unavailable')
        self.assertEqual(controls['prompt'],'unavailable')
        self.assertEqual(controls['parameters'],'unavailable')

    def test_actual_saved_prompt_and_parameters_are_checked_without_exposing_request_body(self):
        other=self.make_lecture()
        self.trace(self.lecture_id);self.trace(other,temperature=0.6)
        data=self.compare(other).get_json()
        controls={c['code']:c['status'] for c in data['controls']}
        self.assertEqual(controls['prompt'],'same')
        self.assertEqual(controls['parameters'],'different')
        self.assertNotIn('request_body',json.dumps(data))
        self.assertNotIn('messages',json.dumps(data))

    def test_tampered_input_is_not_presented_as_verified_or_leaked(self):
        other=self.make_lecture();self.trace(self.lecture_id);self.trace(other)
        with self.fixture_db() as conn:
            conn.execute("UPDATE generation_runs SET request_body=%s WHERE lecture_id=%s",('fixture-sensitive-value',other))
        response=self.compare(other)
        controls={c['code']:c['status'] for c in response.get_json()['controls']}
        self.assertEqual(controls['prompt'],'unavailable')
        self.assertEqual(controls['parameters'],'unavailable')
        self.assertNotIn('fixture-sensitive-value',response.get_data(as_text=True))

    def test_no_profile_version_keeps_generic_name_and_no_diagnostic_snapshot(self):
        other=self.make_lecture(context=module.get_context(1,'linear','none'))
        data=self.compare(other).get_json()
        self.assertEqual(data['right']['mode'],'none')
        self.assertEqual(data['right']['name'],'通用讲义')
        self.assertEqual(data['right']['states'],[])

    def test_live_profile_changes_do_not_replace_generation_snapshots(self):
        other=self.make_lecture();self.save_evidence()
        data=self.compare(other).get_json()
        state=next(s for s in data['left']['states'] if s['id']==3)
        self.assertEqual(state['state'],'needs_support')
        self.assertEqual(self.knowledge()['state'],'partial')

    def test_reads_do_not_change_originals_revisions_inputs_or_call_qwen(self):
        number=self.revision();self.trace(self.lecture_id)
        def snapshot():
            with self.fixture_db() as conn:
                return {table:conn.execute(f'SELECT to_jsonb(t) AS row FROM {table} t ORDER BY to_jsonb(t)::text').fetchall()
                        for table in ('lectures','lecture_revisions','generation_runs','student_knowledge','learning_evidence')}
        before=snapshot()
        with patch.object(module,'call_qwen',side_effect=AssertionError('Comparison cannot generate')):
            self.options();self.compare(self.lecture_id,right_revision=number)
        self.assertEqual(snapshot(),before)

    def test_invalid_parameters_missing_lecture_or_revision_return_readable_errors(self):
        for params in ({},{'other_id':'oops'},{'other_id':'1','left_revision':'-1'},
                       {'other_id':'1','right_revision':'true'},{'other_id':'9'*20},
                       {'other_id':'1','left_revision':str(2**31)}):
            response=self.client.get(f'/api/lectures/{self.lecture_id}/comparison',query_string=params)
            self.assertEqual(response.status_code,400,params)
            self.assertIn('error',response.get_json())
        self.assertEqual(self.compare(99999).status_code,404)
        self.assertEqual(self.compare(self.lecture_id,right_revision=999).status_code,404)
        self.assertEqual(self.client.get('/api/lectures/99999/comparison-options').status_code,404)

    def test_older_base_is_always_selectable_when_candidate_window_is_truncated(self):
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO lectures (student_id,topic,goal,context,content,model,elapsed_seconds)
                SELECT 2,%s,%s,%s,%s,'fixture-only',0 FROM generate_series(1,101)''',
                (module.TOPICS['linear']['title'],module.TOPICS['linear']['goal'],Jsonb(self.before_context),TEXT))
        options=self.options()
        self.assertTrue(options['truncated'])
        self.assertIn(self.lecture_id,{c['id'] for c in options['candidates']})
