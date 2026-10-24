"""Preparation copies, independent student records and immutable per-run notes."""
import json
from unittest.mock import patch

from test_profiles import ProfileDatabaseFixture, module
from jobs import JobQueue


class PreparationWorkflowTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        self.queue = JobQueue(lambda: module.db(), module.execute_job)
        self.queue_patch = patch.object(module, 'TASKS', self.queue)
        self.queue_patch.start()

    def tearDown(self):
        self.queue.wait_idle(5)
        self.queue.close()
        self.queue_patch.stop()

    def payload(self, **changes):
        return {**dict(preparation_copy=True, stage='高中', course='物理', chapter='运动学',
                       title='运动学第一课', goal='区分速度与加速度。', task_level='概念入门',
                       knowledge_names=['速度', '加速度'], lesson_plan='先观察运动，再讨论速度和加速度的区别。'), **changes}

    def create_copy(self, **changes):
        result = self.client.post('/api/course-tasks', json=self.payload(**changes))
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def model(self, prepared, on_attempt):
        on_attempt()
        return module.ModelResult('# 测试讲义\n\n依据备课目标组织，参考来源不足，需教师审核。')

    def test_student_can_be_created_and_viewed_without_course(self):
        response = self.client.post('/api/students', json=dict(name='新同学', subtitle='高一', profile='概念理解较好，运算需要分步解释。'))
        self.assertEqual(response.status_code, 201)
        sid = response.json['student']['id']
        self.assertIn(sid, [s['id'] for s in self.client.get('/api/students').json['students']])
        profile = self.client.get(f'/api/students/{sid}/profile')
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json['student']['name'], '新同学')
        self.assertTrue(all(s['state'] == 'unknown' for s in profile.json['states']))

    def test_student_name_and_information_edits_keep_history(self):
        response = self.client.post('/api/students/1/profile', json=dict(
            name='学生甲', subtitle='高一 · 概念巩固', profile='长期需要加强单位换算。',
            confirmed=True, expected_revision=0, recorder='教师', reason='补充学生档案'))
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(data['student']['name'], '学生甲')
        self.assertEqual(data['profile_history'][0]['previous_snapshot']['name'], '小林')
        self.assertEqual(data['profile_history'][0]['new_snapshot']['name'], '学生甲')

    def test_new_students_can_use_existing_preparation_copy(self):
        saved = self.create_copy()
        response = self.client.post('/api/students', json=dict(name='后来加入', subtitle='高二', profile='基础扎实。'))
        sid = response.json['student']['id']
        context = self.client.get('/api/context', query_string=dict(student_id=sid, topic=saved['key']))
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.json['student']['id'], sid)
        for sid in (1, 2, 3):
            self.assertEqual(self.client.get('/api/context', query_string=dict(student_id=sid, topic=saved['key'])).status_code, 200)

    def test_copy_clones_goals_plan_and_materials_without_sharing_knowledge_ids(self):
        source = self.client.post('/api/course-tasks/linear/copy')
        self.assertEqual(source.status_code, 201)
        source_key = source.json['key']
        payload = self.payload(knowledge_names=source.json['task']['knowledge_names'], revision=0)
        self.assertEqual(self.client.put('/api/course-tasks/' + source_key, json=payload).status_code, 200)
        duplicate = self.client.post('/api/course-tasks/' + source_key + '/copy')
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.json['task']['lesson_plan'], payload['lesson_plan'])
        original = module.get_context(1, source_key)
        copied = module.get_context(1, duplicate.json['key'])
        self.assertEqual(len(original['materials']), len(copied['materials']))
        self.assertFalse(set(original['topic']['knowledge_ids']) & set(copied['topic']['knowledge_ids']))
        self.assertFalse({m['id'] for m in original['materials']} & {m['id'] for m in copied['materials']})
        with self.fixture_db() as conn:
            conn.execute('UPDATE materials SET content=%s WHERE id=%s', ('仅修改副本。', copied['materials'][0]['id']))
        self.assertEqual(module.get_context(1, source_key)['materials'], original['materials'])

    def test_plan_only_copy_can_generate_and_freezes_student_note_without_profile_write(self):
        saved = self.create_copy()
        before = self.client.get('/api/students/1/profile').json
        with patch.object(module, 'call_qwen', self.model):
            response = self.client.post('/api/generate', json=dict(student_id=1, topic=saved['key'], learner_note='单位换算薄弱，请分步骤。'))
        self.assertEqual(response.status_code, 200, response.json)
        run_id = response.json['generation_run_id']
        with self.fixture_db() as conn:
            run = conn.execute('SELECT * FROM generation_runs WHERE id=%s', (run_id,)).fetchone()
        user = json.loads(json.loads(run['request_body'])['messages'][1]['content'])
        self.assertEqual(user['context']['learner_note'], '单位换算薄弱，请分步骤。')
        self.assertEqual(user['context']['topic']['lesson_plan'], saved['task']['lesson_plan'])
        self.assertEqual(self.client.get('/api/students/1/profile').json, before)
        updated = self.payload(lesson_plan='换一份教案。', revision=0)
        self.assertEqual(self.client.put('/api/course-tasks/' + saved['key'], json=updated).status_code, 200)
        lecture = self.client.get('/api/lectures/' + str(response.json['id'])).json
        self.assertEqual(lecture['context']['topic']['lesson_plan'], saved['task']['lesson_plan'])

    def test_no_profile_mode_excludes_student_note_from_actual_model_input(self):
        saved = self.create_copy()
        with patch.object(module, 'call_qwen', self.model):
            response = self.client.post('/api/generate', json=dict(student_id=1, topic=saved['key'], personalization_mode='none', learner_note='不应发送的个人薄弱点'))
        self.assertEqual(response.status_code, 200)
        with self.fixture_db() as conn:
            body = conn.execute('SELECT request_body FROM generation_runs WHERE id=%s',
                                (response.json['generation_run_id'],)).fetchone()['request_body']
        self.assertNotIn('不应发送的个人薄弱点', body)
        self.assertNotIn('learner_note', json.loads(json.loads(body)['messages'][1]['content'])['context'])

    def test_queue_retry_reuses_original_plan_and_note_after_edits(self):
        saved = self.create_copy()
        def fail(prepared, on_attempt):
            on_attempt()
            raise module.ModelError('fixture_timeout', '测试超时。')
        with patch.object(module, 'call_qwen', fail):
            response = self.client.post('/api/jobs', json=dict(student_id=1, topic=saved['key'], learner_note='本次多讲单位。'))
            self.assertEqual(response.status_code, 202)
            self.assertTrue(self.queue.wait_idle(5))
        run_id = response.json['run_id']
        with self.fixture_db() as conn:
            frozen = conn.execute('SELECT * FROM generation_runs WHERE id=%s', (run_id,)).fetchone()
        self.client.put('/api/course-tasks/' + saved['key'], json=self.payload(lesson_plan='后续新版教案', revision=0))
        with patch.object(module, 'call_qwen', self.model):
            retry = self.client.post(f'/api/generation-runs/{run_id}/retry')
            self.assertEqual(retry.status_code, 202)
            self.assertTrue(self.queue.wait_idle(5))
        with self.fixture_db() as conn:
            rerun = conn.execute('SELECT * FROM generation_runs WHERE id=%s', (retry.json['run_id'],)).fetchone()
        self.assertEqual(frozen['request_body'], rerun['request_body'])
        self.assertEqual(rerun['context']['learner_note'], '本次多讲单位。')

    def test_invalid_student_notes_and_plan_are_rejected_without_generation(self):
        saved = self.create_copy()
        for note in ([], None, '字' * 2001):
            for endpoint in ('/api/generate', '/api/jobs'):
                self.assertEqual(self.client.post(endpoint, json=dict(student_id=1, topic=saved['key'], learner_note=note)).status_code, 400)
        self.assertEqual(self.client.get('/api/context', query_string=dict(student_id=1, topic=saved['key'], learner_note='字' * 2001)).status_code, 400)
        self.assertEqual(self.client.post('/api/course-tasks', json=self.payload(lesson_plan='字' * 20001)).status_code, 400)
        for data in ({}, {'name':'x'}, []):
            self.assertEqual(self.client.post('/api/students', json=data).status_code, 400)
