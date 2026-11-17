"""Custom course workflows in an isolated database schema; no model services."""
import copy
import json
from unittest.mock import patch

from test_profiles import ProfileDatabaseFixture, module
from prompts import prepare_request
from quality import check_output


class CatalogTests(ProfileDatabaseFixture):
    def test_material_list_belongs_to_requested_copy(self):
        saved = self.create()
        response = self.client.get('/api/course-tasks/' + saved['key'] + '/materials')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['materials'], [])
        sample = self.client.get('/api/course-tasks/linear/materials')
        self.assertEqual(sample.status_code, 200)
        self.assertTrue(sample.json['materials'])
        expected = module.get_context(1, 'linear')['materials']
        self.assertEqual([m['id'] for m in sample.json['materials']], [m['id'] for m in expected])
        self.assertEqual(self.client.get('/api/course-tasks/missing/materials').status_code, 404)

    def payload(self, **changes):
        return {**dict(stage='高中', course='物理', chapter='运动的描述', title='速度与加速度',
                       goal='区分速度与加速度，解释运动图像。', task_level='概念入门',
                       knowledge_names=['速度', '加速度'], student_ids=[1, 2]), **changes}

    def create(self, **changes):
        response = self.client.post('/api/course-tasks', json=self.payload(**changes))
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_all_stages_persist_and_enter_context_without_math_assumptions(self):
        for stage, subject, points in [('小学', '语文', ['比喻']), ('初中', '历史', ['史料']),
                                       ('高中', '物理', ['加速度']), ('大学', '微积分', ['导数'])]:
            with self.subTest(stage=stage):
                saved = self.create(stage=stage, course=subject, knowledge_names=points)
                key = saved['key']
                self.assertEqual(self.client.get('/api/bootstrap').json['topics'][key]['stage'], stage)
                response = self.client.get('/api/context', query_string={'student_id': 1, 'topic': key})
                self.assertEqual(response.status_code, 200)
                context = response.json
                self.assertEqual(context['states'][0]['state'], 'unknown')
                self.assertEqual(context['scope_check']['coverage'][0]['scope'], points[0])
                prepared = prepare_request(context, saved['task']['goal'], 'fixture')
                self.assertEqual(prepared.prompt_version, 'lecture-general-v1+expanded-v1+plan-v1')
                system = json.loads(prepared.body)['messages'][0]['content']
                self.assertNotIn('初中数学教师', system)
                report = check_output('导数与积分，去括号。', context, saved['task']['goal'])
                self.assertFalse(any('可能超出' in w for w in report['scope']['warnings']))
                self.assertNotIn('bracket_evidence', [c['code'] for c in report['automatic']])
                self.assertEqual(report['version'], 'general-text-check-v1+lecture-workflow-v2')

    def test_a_course_subject_adds_subject_rules_and_checks_to_its_own_lessons(self):
        from classes import SUBJECT_MIGRATION, ensure_course_structure
        with self.fixture_db() as conn:
            ensure_course_structure(conn)
            # A demonstration course made before subjects existed gets its subject once; a later choice stays.
            conn.execute("UPDATE courses SET subject='general' WHERE preset_key='junior-math'")
            conn.execute('DELETE FROM schema_migrations WHERE name=%s', (SUBJECT_MIGRATION,))
            ensure_course_structure(conn)
            conn.execute("UPDATE courses SET subject='general' WHERE preset_key='university-probability'")
            ensure_course_structure(conn)
        presets = {c['preset_key']: c['subject'] for c in self.client.get('/api/courses').json['courses'] if c['preset_key']}
        self.assertEqual(presets, {'junior-math': 'math', 'university-probability': 'general'})
        made = self.client.post('/api/courses', json={'name': '概率统计初步', 'stage': '高中', 'subject': 'probability'})
        self.assertEqual((made.status_code, made.json['course']['subject']), (201, 'probability'))
        self.assertEqual(self.client.post('/api/courses', json={'name': '力学', 'stage': '高中', 'subject': 'physics'}).status_code, 400)
        course = made.json['course']
        saved = self.create(course_id=course['id'], course='', stage='', knowledge_names=['二项分布', '期望'], title='二项分布的期望',
                            goal='会求二项分布的期望。')
        goal = saved['task']['goal']

        def context():
            response = self.client.get('/api/context', query_string={'student_id': 1, 'topic': saved['key']})
            self.assertEqual(response.status_code, 200, response.json)
            return response.json
        ctx = context()
        prepared = prepare_request(ctx, goal, 'fixture')
        self.assertEqual(prepared.prompt_version, 'lecture-general-v1+probability-general-v1+expanded-v1+plan-v1')
        system = json.loads(prepared.body)['messages'][0]['content']
        self.assertIn('【学科要求：概率统计】', system)
        self.assertNotIn('【学科要求：概率论】', system)   # the Bayes lesson's own rules stay with the demonstration lessons
        design = prepare_request({**ctx, 'generation_mode': 'designed-v1'}, goal, 'fixture')
        self.assertEqual(design.prompt_version, 'lesson-design-v1+probability-general-v1+tutor-v1')
        self.assertIn('【学科要求：概率统计】', json.loads(design.body)['messages'][0]['content'])
        report = check_output('## 例题\n设 $P(A)=1.2$，$P(B) = 0.6 \\times 0.5 = 0.3$。', ctx, goal)
        self.assertIn('+probability-subject-v1', report['version'])
        checks = {c['code']: c for c in report['automatic']}
        self.assertEqual(checks['probability_range']['status'], 'warning')
        self.assertIn('1.2', checks['probability_range']['detail'])
        self.assertNotIn('0.3', checks['probability_range']['detail'])
        # A P(…) inside a product counts people, not a probability: 10000 × 0.01 × 0.95 = 95 is fine.
        count = check_output('真阳性数 = $N \\times P(A_1) \\times P(B\\mid A_1) = 10000 \\times 0.01 \\times 0.95 = 95$，'
                             '$2P(A) = 1.2$，$P(A) + P(B) = 1.3$', ctx, goal)
        self.assertEqual({c['code']: c for c in count['automatic']}['probability_range']['status'], 'pass')
        self.assertNotIn('probability_conditions', checks)   # 互斥、完备 belong to the Bayes lesson, not every probability lesson

        # The course decides: changed, the next generation of the same lesson follows; left out of an edit, it stays.
        edit = dict(name=course['name'], stage=course['stage'])
        self.assertEqual(self.client.put(f'/api/courses/{course["id"]}', json={**edit, 'subject': 'math'}).json['course']['subject'], 'math')
        self.assertEqual(self.client.put(f'/api/courses/{course["id"]}', json=edit).json['course']['subject'], 'math')
        ctx = context()
        self.assertEqual(prepare_request(ctx, goal, 'fixture').prompt_version, 'lecture-general-v1+math-general-v1+expanded-v1+plan-v1')
        self.assertNotIn('probability_range', [c['code'] for c in check_output('$P(A)=1.2$', ctx, goal)['automatic']])
        self.client.put(f'/api/courses/{course["id"]}', json={**edit, 'subject': 'general'})
        self.assertEqual(prepare_request(context(), goal, 'fixture').prompt_version, 'lecture-general-v1+expanded-v1+plan-v1')
        # A demonstration lesson keeps its own family whatever its course says.
        preset = prepare_request(module.get_context(1, 'linear'), '', 'fixture').prompt_version
        self.assertTrue(preset.startswith('lecture-math-v3+'), preset)
        self.assertNotIn('general-v1', preset)

    def test_edit_preserves_point_ids_and_history_but_rejects_stale_revision(self):
        saved = self.create()
        key = saved['key']
        before = module.get_context(1, key)
        frozen = copy.deepcopy(before)
        response = self.client.put('/api/course-tasks/' + key,
                                   json=self.payload(goal='新的教学目标', revision=0))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['task']['knowledge_ids'], saved['task']['knowledge_ids'])
        self.assertEqual(response.json['task']['revision'], 1)
        self.assertEqual(before, frozen)
        self.assertEqual(self.client.put('/api/course-tasks/' + key, json=self.payload(revision=0)).status_code, 409)
        self.assertEqual(self.client.put('/api/course-tasks/linear', json=self.payload(revision=0)).status_code, 404)

    def test_invalid_input_never_leaves_partial_tasks_or_knowledge_points(self):
        with self.fixture_db() as conn:
            initial = conn.execute('SELECT count(*) AS n FROM knowledge_points').fetchone()['n']
        for data in [None, [], self.payload(stage=''), self.payload(knowledge_names=['重复', '重复']),
                     self.payload(student_ids=[True]), self.payload(student_ids=[999]),
                     self.payload(knowledge_names=['x'] * 21), self.payload(goal='字' * 401)]:
            self.assertEqual(self.client.post('/api/course-tasks', json=data).status_code, 400)
        with self.fixture_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM course_tasks').fetchone()['n'], 0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM knowledge_points').fetchone()['n'], initial)

    def test_materials_are_scoped_to_point_idempotent_and_need_no_embedding(self):
        saved = self.create()
        other = self.create(title='另一任务')
        key = saved['key']
        record = dict(title='运动讲义', content='速度描述位置随时间的变化。', filename='motion.txt')
        url = '/api/course-tasks/' + key + '/materials'
        ids = saved['task']['knowledge_ids']
        with patch('catalog.read_upload', return_value=record), patch.object(module.EMBEDDER, 'embed', side_effect=AssertionError('No embedding during adoption')):
            first = self.client.post(url, json={'upload_id':'fixture', 'knowledge_id':ids[0]})
            again = self.client.post(url, json={'upload_id':'fixture', 'knowledge_id':ids[0]})
            second = self.client.post(url, json={'upload_id':'fixture', 'knowledge_id':ids[1]})
            self.assertEqual(first.status_code, 201)
            self.assertEqual(first.json['material_id'], again.json['material_id'])
            self.assertNotEqual(first.json['material_id'], second.json['material_id'])
            self.assertEqual(self.client.post(url, json={'upload_id':'fixture', 'knowledge_id':other['task']['knowledge_ids'][0]}).status_code, 400)
        self.assertEqual(len(module.get_context(1, key)['materials']), 2)
        self.assertEqual(module.get_context(1, other['key'])['materials'], [])
        self.assertEqual(module.get_context(1, 'linear')['materials'], self.before_context['materials'])

    def test_missing_or_image_only_upload_cannot_create_empty_material(self):
        saved = self.create()
        url = '/api/course-tasks/' + saved['key'] + '/materials'
        body = {'upload_id':'absent', 'knowledge_id':saved['task']['knowledge_ids'][0]}
        with patch('catalog.read_upload', return_value=None):
            self.assertEqual(self.client.post(url, json=body).status_code, 404)
        with patch('catalog.read_upload', return_value={'content':''}):
            self.assertEqual(self.client.post(url, json=body).status_code, 400)

    def test_cohort_restriction_no_profile_and_custom_probability_are_generic(self):
        saved = self.create(course='大学概率论', student_ids=[1])
        key = saved['key']
        self.assertEqual(self.client.get('/api/context', query_string={'student_id':2, 'topic':key}).status_code, 404)
        ctx = module.get_context(1, key, 'none')
        self.assertEqual(ctx['states'], [])
        self.assertNotIn('reference_examples', ctx)
        self.assertNotIn('小林', prepare_request(ctx, saved['task']['goal'], 'fixture').body)

    def test_custom_task_generates_with_saved_snapshot_and_prompt(self):
        saved = self.create()
        key = saved['key']
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO materials(id,knowledge_id,title,content,source_title,source_url,section)
                VALUES (100,%s,'物理资料','速度描述运动变化。','测试资料','https://example.com','第一章')''',
                (saved['task']['knowledge_ids'][0],))
        def generate(prepared, on_attempt):
            on_attempt()
            self.assertEqual(prepared.prompt_version, 'lecture-general-v1+expanded-v1+plan-v1')
            return module.ModelResult('# 速度与加速度\n速度描述运动变化。[M100]')
        with patch.object(module, 'call_qwen', generate):
            response = self.client.post('/api/generate', json={'student_id':1, 'topic':key})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['context']['topic']['stage'], '高中')
        self.assertEqual(response.json['context']['topic']['chapter'], '运动的描述')
        run = self.client.get('/api/generation-runs/' + str(response.json['generation_run_id']))
        self.assertEqual(run.status_code, 200)

    def test_only_local_host_names_reach_the_workspace(self):
        # DNS rebinding: a page on rebind.example re-points its name at 127.0.0.1. The browser then
        # sends its own name as Host and as Origin, so the Origin comparison alone would pass.
        foreign = {'Host': 'rebind.example:38660', 'Origin': 'http://rebind.example:38660'}
        for method, path in (('get', '/api/students'), ('get', '/api/courses'), ('get', '/'),
                             ('get', '/static/app.js'), ('post', '/api/onboarding/tour')):
            response = getattr(self.client, method)(path, headers=foreign, json={'outcome': 'finished', 'step': 1} if method == 'post' else None)
            self.assertEqual(response.status_code, 403, path)
        self.assertNotIn('students', self.client.get('/api/students', headers={'Host': 'rebind.example'}).get_json())
        for host in ('127.0.0.1:38660', 'localhost:38661', '[::1]:38660', 'localhost'):
            self.assertEqual(self.client.get('/api/students', headers={'Host': host}).status_code, 200, host)
        same = self.client.post('/api/onboarding/tour', json={'outcome': 'finished', 'step': 1},
                                headers={'Host': '127.0.0.1:38660', 'Origin': 'http://127.0.0.1:38660'})
        self.assertEqual(same.status_code, 200, same.get_json())

    def test_cross_origin_put_is_rejected_before_edit(self):
        saved = self.create()
        response = self.client.put('/api/course-tasks/' + saved['key'], json=self.payload(revision=0),
                                   headers={'Origin':'https://external.invalid'})
        self.assertEqual(response.status_code, 403)
