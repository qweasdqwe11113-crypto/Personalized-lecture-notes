"""Question workflows in disposable PostgreSQL schemas, with explicit model fixtures."""
import copy
from io import BytesIO
import json
from threading import Lock
from unittest.mock import patch
from zipfile import ZipFile

from test_profiles import ProfileDatabaseFixture, module
from quizzes import QuizQueue, parse_questions, question_plan, validate_options


def fixture_result(context):
    questions = []
    for p in context['question_plan']:
        q = {**p, 'stem': f"第 {p['number']} 个隔离测试题：解释等式的性质。", 'options': [],
             'answer': 'FIXTURE-ANSWER-SECRET', 'analysis': '两边进行相同运算，核查原式成立。',
             'common_mistake': '只对等式一边进行运算。',
             'source_ids': [f"M{context['materials'][0]['id']}"] if context['materials'] else []}
        if p['type'] == 'single_choice':
            q.update(options=['两边同加', '只变左边', '只变右边', '随意改变'], answer='A')
        questions.append(q)
    return {'questions': questions}


class QuizTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        self.queue = QuizQueue(lambda: module.db(), lambda prepared, attempted: module.call_qwen(prepared, attempted), Lock())
        self.queue_patch = patch.object(module, 'QUIZZES', self.queue)
        self.queue_patch.start()

    def tearDown(self):
        self.queue.wait_idle(5)
        self.queue.close()
        self.queue_patch.stop()

    def payload(self, **changes):
        return dict(topic='linear', student_id=2, student_level='profile', count=10, difficulty='mixed',
                    types=['single_choice', 'short_answer'], knowledge_ids=[], requirements='检验等式性质。', **changes)

    def submit(self, data=None, failure=False):
        def model(prepared, attempted):
            attempted()
            if failure:
                raise module.ModelError('timeout', '隔离测试超时。')
            body = json.loads(prepared.body)
            context = json.loads(body['messages'][1]['content'])['context']
            return module.ModelResult(json.dumps(fixture_result(context), ensure_ascii=False), {'total_tokens': 10})
        with patch.object(module, 'call_qwen', model):
            response = self.client.post('/api/quizzes', json=data or self.payload())
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.queue.wait_idle(5))
        return self.client.get('/api/quizzes/'+response.json['id']).json

    def test_exact_count_difficulty_and_focus_use_frozen_context_without_profile_writes(self):
        before = self.profile()
        result = self.submit({**self.payload(), 'knowledge_ids': [2, 3]})
        self.assertEqual(result['status'], 'succeeded', result)
        questions = result['result']['questions']
        self.assertEqual(len(questions), 10)
        self.assertEqual([q['difficulty'] for q in questions].count('basic'), 5)
        self.assertEqual([q['difficulty'] for q in questions].count('medium'), 3)
        self.assertEqual([q['difficulty'] for q in questions].count('advanced'), 2)
        self.assertEqual({q['knowledge_id'] for q in questions}, {2, 3})
        self.assertEqual(self.profile(), before)
        self.assertTrue(result['integrity_valid'])
        self.assertNotIn('request_body', result)
        with self.fixture_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM lectures').fetchone()['n'], 1)
        self.assertEqual(self.client.get('/api/quizzes').json['quizzes'][0]['id'], result['id'])

    def test_general_level_excludes_profile_and_student_exports_exclude_answers_in_all_formats(self):
        result = self.submit({**self.payload(), 'student_id': None, 'student_level': 'standard', 'types': ['short_answer'], 'count': 2})
        self.assertEqual(result['context']['states'], [])
        self.assertNotIn('小周', json.dumps(result['context'], ensure_ascii=False))
        for fmt in ('md', 'txt', 'html', 'docx'):
            for version in ('student', 'teacher'):
                response = self.client.get(f"/api/quizzes/{result['id']}/download?format={fmt}&version={version}")
                self.assertEqual(response.status_code, 200)
                body = ZipFile(BytesIO(response.data)).read('word/document.xml').decode() if fmt == 'docx' else response.data.decode()
                self.assertEqual('FIXTURE-ANSWER-SECRET' in body, version == 'teacher')
                self.assertNotIn('小周', body)
                self.assertNotIn('profile_revision', body)
        self.assertEqual(self.client.get(f"/api/quizzes/{result['id']}/download?version=invalid").status_code, 400)
        self.assertEqual(self.client.post(f"/api/quizzes/{result['id']}/retry").status_code, 409)

    def test_invalid_model_count_difficulty_duplicate_stems_and_sources_are_rejected(self):
        ctx = module.get_context(2, 'linear')
        settings = validate_options(self.payload(), ctx['topic']['knowledge_ids'])
        ctx['question_plan'] = question_plan(settings, ctx['states'])
        good = fixture_result(ctx)
        mutations = [lambda d: d['questions'].pop(),
                     lambda d: d['questions'][0].update(difficulty='advanced'),
                     lambda d: d['questions'][1].update(stem=d['questions'][0]['stem']),
                     lambda d: d['questions'][0].update(source_ids=['M999']),
                     lambda d: d['questions'][0].update(options=['相同']*4),
                     lambda d: d['questions'][0].update(answer='E')]
        for mutate in mutations:
            changed = copy.deepcopy(good)
            mutate(changed)
            with self.assertRaises(module.ModelError):
                parse_questions(json.dumps(changed), ctx)
        self.assertEqual(parse_questions(json.dumps(good), ctx), good)

    def test_invalid_inputs_do_not_create_jobs(self):
        for changes in ({'count': True}, {'count': 31}, {'types': []}, {'types': [['single_choice']]},
                        {'difficulty': []}, {'student_level': 'unknown'}, {'student_id': 11},
                        {'student_id': None, 'student_level': 'profile'}, {'knowledge_ids': [11]},
                        {'requirements': '字'*2001}, {'topic': 'missing'}):
            response = self.client.post('/api/quizzes', json={**self.payload(), **changes})
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.get('/api/quizzes').json['quizzes'], [])

    def test_generic_questions_work_without_a_course_student_cohort(self):
        topic = {**module.TOPICS['linear'], 'student_ids': []}
        with patch.object(module, 'load_catalog', return_value={'empty_cohort': topic}):
            row = self.submit({**self.payload(), 'topic': 'empty_cohort', 'student_id': None, 'student_level': 'foundation', 'count': 1})
        self.assertEqual(row['status'], 'succeeded', row)
        self.assertEqual(row['context']['states'], [])
        self.assertEqual(row['context']['student']['name'], '通用练习')

    def test_failed_retry_preserves_exact_request_after_material_and_profile_edits(self):
        failed = self.submit(failure=True)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.client.get(f"/api/quizzes/{failed['id']}/download").status_code, 409)
        before = self.queue.detail(failed['id'])
        with self.fixture_db() as conn:
            conn.execute("UPDATE materials SET content='编辑后的资料' WHERE id=1")
            conn.execute("UPDATE students SET profile='新画像' WHERE id=2")
        def model(prepared, attempted):
            self.assertEqual(prepared.body, before['request_body'])
            return module.ModelResult(json.dumps(fixture_result(before['context']), ensure_ascii=False))
        with patch.object(module, 'call_qwen', model):
            response = self.client.post(f"/api/quizzes/{failed['id']}/retry")
            self.assertEqual(response.status_code, 202)
            self.assertTrue(self.queue.wait_idle(5))
        saved = self.queue.detail(response.json['id'])
        self.assertEqual(saved['status'], 'succeeded')
        self.assertEqual(saved['parent_id'], failed['id'])
        self.assertEqual(saved['request_sha256'], before['request_sha256'])

    def test_capacity_and_recovery(self):
        self.queue.lock.acquire()
        try:
            for _ in range(4):
                self.assertEqual(self.client.post('/api/quizzes', json=self.payload()).status_code, 202)
            self.assertEqual(self.client.post('/api/quizzes', json=self.payload()).status_code, 429)
        finally:
            with patch.object(module, 'call_qwen', side_effect=module.ModelError('test', '隔离测试。')):
                self.queue.lock.release()
                self.assertTrue(self.queue.wait_idle(5))
        with self.fixture_db() as conn:
            conn.execute("UPDATE quiz_jobs SET status='queued',finished_at=NULL")
        self.queue.recover()
        self.assertTrue(all(q['status'] == 'interrupted' for q in self.client.get('/api/quizzes').json['quizzes']))

    def test_plan_small_counts_and_all_types_stay_exact(self):
        for n in range(1, 31):
            settings = validate_options({**self.payload(), 'count': n}, [1, 2, 3])
            plan = question_plan(settings, [])
            self.assertEqual(len(plan), n)
            self.assertEqual([p['number'] for p in plan], list(range(1, n+1)))

