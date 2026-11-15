"""Question workflows in disposable PostgreSQL schemas, with explicit model fixtures."""
import copy
from io import BytesIO
import json
from threading import Lock
from unittest.mock import patch
from zipfile import ZipFile

from psycopg.types.json import Jsonb

import unittest

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


class QuizFixture(ProfileDatabaseFixture):
    """Queue and submit helpers without test methods, so subclasses do not rerun quiz tests."""
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


class QuizTests(QuizFixture):
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
        # Contexts frozen before JSON mode keep their original request shape and labels.
        from quizzes import prepare_quiz, quiz_versions
        self.assertNotIn('response_format', json.loads(prepare_quiz(ctx, settings, 'm').body))
        self.assertEqual(quiz_versions(ctx), ('quiz-v1', 'quiz-context-v1'))
        self.assertEqual(parse_questions('```json\n' + json.dumps(good) + '\n```', ctx), good)
        for wrapped in ('说明：\n' + json.dumps(good), '```\n' + json.dumps(good) + '\n```'):
            with self.assertRaises(module.ModelError):
                parse_questions(wrapped, ctx)

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



class LectureQuizTests(QuizFixture):
    def lecture_payload(self, **changes):
        return {**dict(lecture_id=self.lecture_id, lecture_revision=0, student_level='profile', count=3,
                       difficulty='basic', types=['short_answer'], knowledge_ids=[], requirements=''), **changes}

    def stored(self, job_id):
        with self.fixture_db() as conn:
            return conn.execute('SELECT * FROM quiz_jobs WHERE id=%s', (job_id,)).fetchone()

    def test_lecture_quiz_freezes_the_lecture_version_materials_and_states(self):
        # A later teacher-confirmed change must not leak into a quiz built for the taught lecture.
        self.assertEqual(self.save_evidence(ProfileDatabaseFixture.payload(self)).status_code, 201)
        result = self.submit(self.lecture_payload())
        self.assertEqual(result['status'], 'succeeded', result)
        ctx = result['context']
        self.assertEqual(ctx['lecture']['id'], self.lecture_id)
        self.assertEqual(ctx['lecture']['content'], '# 隔离测试讲义\n历史原稿 [M3]')
        self.assertEqual(ctx['lecture']['status'], '原稿未审核')
        self.assertEqual([m['id'] for m in ctx['materials']], [m['id'] for m in self.before_context['materials']])
        self.assertEqual([s['state'] for s in ctx['states']], [s['state'] for s in self.before_context['states']])
        self.assertEqual(ctx['student']['name'], self.before_context['student']['name'])
        row = self.stored(result['id'])
        self.assertEqual(row['student_id'], 2)
        self.assertEqual(row['topic_key'], 'linear')
        system = json.loads(row['request_body'])['messages'][0]['content']
        self.assertIn('quiz-lecture-v1', system)
        self.assertEqual(json.loads(row['request_body'])['response_format'], {'type': 'json_object'})
        listed = self.client.get(f'/api/quizzes?lecture_id={self.lecture_id}').json['quizzes']
        self.assertEqual([(q['id'], q['lecture_id'], q['lecture_revision']) for q in listed],
                         [(result['id'], self.lecture_id, 0)])
        self.assertEqual(self.client.get('/api/quizzes?lecture_id=999999').json['quizzes'], [])
        self.assertEqual(self.client.get('/api/quizzes?lecture_id=x').status_code, 400)
        teacher = self.client.get(f"/api/quizzes/{result['id']}/download?format=md&version=teacher").data.decode()
        student = self.client.get(f"/api/quizzes/{result['id']}/download?format=md&version=student").data.decode()
        self.assertIn('教师版', teacher)
        self.assertNotIn('配套讲义', teacher + student)   # the record lists the lecture; the sheet does not

    def test_general_level_keeps_lecture_but_drops_individual_diagnostics(self):
        result = self.submit(self.lecture_payload(student_level='standard'))
        self.assertEqual(result['status'], 'succeeded', result)
        ctx = result['context']
        self.assertEqual((ctx['states'], ctx['student']['name'], ctx['lecture']['personalization_plan']), ([], '通用练习', []))
        self.assertNotIn('小周', json.dumps(ctx, ensure_ascii=False))
        self.assertIsNone(self.stored(result['id'])['student_id'])

    def test_invalid_lecture_links_do_not_create_jobs(self):
        with self.fixture_db() as conn:
            generic = conn.execute('''INSERT INTO lectures (student_id,topic,goal,context,content,model,elapsed_seconds,citation_check)
                VALUES (2,'t','g',%s,'# 无画像讲义','fixture-only',0,'{}') RETURNING id''',
                (Jsonb(module.get_context(2, 'linear', 'none')),)).fetchone()['id']
        cases = [(self.lecture_payload(lecture_id=999999), 404), (self.lecture_payload(lecture_revision=5), 404),
                 (self.lecture_payload(lecture_revision=-1), 400), (self.lecture_payload(lecture_id='1'), 400),
                 (self.lecture_payload(topic='basic'), 400), (self.lecture_payload(knowledge_ids=[11]), 400),
                 (self.lecture_payload(lecture_id=generic), 400)]
        for data, status in cases:
            response = self.client.post('/api/quizzes', json=data)
            self.assertEqual(response.status_code, status, (data, response.json))
        self.assertEqual(self.client.get('/api/quizzes').json['quizzes'], [])
        allowed = self.submit(self.lecture_payload(lecture_id=generic, student_level='foundation'))
        self.assertEqual(allowed['status'], 'succeeded', allowed)

    def test_designed_lecture_quiz_follows_the_lesson_design(self):
        # The quiz is the after-class check of a designed lesson: the handout's notation, every objective,
        # nothing the students already did in class. A copied example goes back to the model once.
        from test_lesson_design import design_reply
        with self.fixture_db() as conn:
            conn.execute('UPDATE lectures SET context=context||%s WHERE id=%s',
                         (Jsonb({'lesson_design': design_reply()}), self.lecture_id))
        seen = []
        def model(prepared, attempted):
            payload = json.loads(prepared.body)
            seen.append((prepared.prompt_version, prepared.input_version, payload['messages']))
            ctx = json.loads(payload['messages'][1]['content'])['context']
            result = fixture_result(ctx)
            first = result['questions'][0]
            if len(seen) == 1:
                first['stem'] = ctx['lecture']['design']['used'][0]['stem'].replace('$A_1$', 'A1')
            else:
                first['stem'] = '两条产线的产量占比为 0.7、0.3，次品率为 0.01、0.04。抽到一件次品，求 P(A1|D)，并说明 A2 的后验为何更大。'
                first['analysis'] = '对应学习目标 1。$P(A_1\\mid D) = \\frac{0.007}{0.019}$，与例1同类。'
            # One backslash, as models write it: JSON reads "\frac" as a form feed followed by "rac".
            return module.ModelResult(json.dumps(result, ensure_ascii=False).replace('\\\\frac', '\\frac'), {'total_tokens': 10})
        with patch.object(module, 'call_qwen', model):
            response = self.client.post('/api/quizzes', json=self.lecture_payload())
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.queue.wait_idle(5))
        result = self.client.get('/api/quizzes/' + response.json['id']).json
        self.assertEqual(result['status'], 'succeeded', result)
        design = result['context']['lecture']['design']
        self.assertEqual([(o['number'], o['text'], o['level'], o['checked_by']) for o in design['objectives']],
                         [(1, '能用贝叶斯公式求后验', '基础', ['练1']), (2, '能说明先验变化的影响', '提高', ['练3', '作业1'])])
        self.assertEqual([q['objective'] for q in result['result']['questions']], [1, 1, 2])   # each objective, 提高 last
        self.assertEqual([p['name'] for p in design['used']], ['例1', '例2', '练1', '练2', '练3', '作业1'])
        self.assertEqual(design['notation'], [{'symbol': '$D$', 'meaning': '抽到次品'}])
        self.assertEqual([version[:2] for version in seen], [('quiz-v1+lecture-v2+json-v1', 'quiz-context-lecture-v2'),
                                                              ('quiz-v1+lecture-v2+json-v1+repair-v1', 'quiz-context-lecture-v2')])
        system = seen[0][2][0]['content']
        self.assertIn('quiz-lecture-v2', system)
        self.assertNotIn('quiz-lecture-v1', system)
        self.assertIn('几乎照抄了学生已经做过的「例1」', seen[1][2][-1]['content'])
        first = result['result']['questions'][0]
        self.assertEqual(first['stem'], '两条产线的产量占比为 0.7、0.3，次品率为 0.01、0.04。抽到一件次品，求 $P(A_1\\mid D)$，并说明 $A_2$ 的后验为何更大。')
        # The objective is printed on its own line; the analysis keeps only the solution.
        self.assertEqual(first['analysis'], '$P(A_1\\mid D) = \\frac{0.007}{0.019}$。')
        self.assertFalse(any('对应学习目标' in q['analysis'] for q in result['result']['questions']))
        docx = self.client.get(f"/api/quizzes/{result['id']}/download?format=docx&version=teacher").data
        xml = ZipFile(BytesIO(docx)).read('word/document.xml').decode()
        self.assertIn('<m:oMath', xml)   # the formulas are Word equations, not dollar signs
        self.assertNotIn('$A_2$', xml)
        teacher = self.client.get(f"/api/quizzes/{result['id']}/download?format=txt&version=teacher").data.decode()
        self.assertIn('学习目标：1. 能用贝叶斯公式求后验', teacher)
        self.assertIn('P(A₁ | D)', teacher)   # plain files get Unicode, not LaTeX
        student = self.client.get(f"/api/quizzes/{result['id']}/download?format=md&version=student").data.decode()
        self.assertNotIn('学习目标', student)
        self.assertEqual(student.count('____________________________'), 3 * 3)   # short answers get three lines

    def designed(self):
        from test_lesson_design import design_reply
        with self.fixture_db() as conn:
            conn.execute('UPDATE lectures SET context=context||%s WHERE id=%s',
                         (Jsonb({'lesson_design': design_reply()}), self.lecture_id))

    def finished_quiz(self, edit=None):
        def model(prepared, attempted):
            ctx = json.loads(json.loads(prepared.body)['messages'][1]['content'])['context']
            result = fixture_result(ctx)
            for q in result['questions']:
                q['stem'] = f"第 {q['number']} 题：先验 0.{q['number']} 时后验怎样变化？"
                q['analysis'] = '$0.3 \\times 0.2 = 0.08$' if q['number'] == 2 else q['analysis']
            return module.ModelResult(json.dumps(result, ensure_ascii=False))
        with patch.object(module, 'call_qwen', model):
            response = self.client.post('/api/quizzes', json=self.lecture_payload(count=4, types=['short_answer', 'single_choice']))
            self.assertTrue(self.queue.wait_idle(5))
        return self.client.get('/api/quizzes/' + response.json['id']).json

    def test_the_plan_is_shown_before_generating_and_arithmetic_is_rechecked(self):
        self.designed()
        plan = self.client.post('/api/quizzes/plan', json=self.lecture_payload(count=4)).json
        self.assertEqual([(p['number'], p['objective']) for p in plan['plan']], [(1, 1), (2, 1), (3, 2), (4, 2)])
        self.assertEqual([o['text'] for o in plan['objectives']], ['能用贝叶斯公式求后验', '能说明先验变化的影响'])
        self.assertEqual(self.client.post('/api/quizzes/plan', json={'topic': 'linear'}).status_code, 400)
        self.assertEqual(self.client.get('/api/quizzes').json['quizzes'], [])   # a preview creates nothing
        quiz = self.finished_quiz()
        self.assertEqual(quiz['status'], 'succeeded', quiz)
        self.assertEqual(quiz['checks'], [{'number': 2, 'details': ['0.3 * 0.2 计算得 0.06，与所写的 0.08 不一致。']}])
        teacher = self.client.get(f"/api/quizzes/{quiz['id']}/download?format=md&version=teacher").data.decode()
        for note in ('算式复算', '可能误报', '需教师复核', '请复核', '出题时状态'):   # the web view shows the reminder
            self.assertNotIn(note, teacher)

    def test_one_question_is_replaced_or_edited_as_a_new_record(self):
        self.designed()
        quiz = self.finished_quiz()
        calls = []
        def model(prepared, attempted):
            payload = json.loads(prepared.body)
            ctx = json.loads(payload['messages'][1]['content'])['context']
            calls.append((prepared.prompt_version, ctx, payload['messages'][0]['content']))
            q = {**fixture_result(ctx)['questions'][0]}
            # The first try repeats a question the set keeps; the second is new.
            q['stem'] = ctx['replace']['keep'][0] if len(calls) == 1 else '某检测灵敏度 0.9，患病率 0.01，阳性时患病的概率？'
            return module.ModelResult(json.dumps({'questions': [q]}, ensure_ascii=False))
        with patch.object(module, 'call_qwen', model):
            response = self.client.post(f"/api/quizzes/{quiz['id']}/questions/3/replace", json={'note': '换成医学检测情境'})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.queue.wait_idle(5))
        replaced = self.client.get('/api/quizzes/' + response.json['id']).json
        self.assertEqual(replaced['status'], 'succeeded', replaced)
        self.assertEqual(replaced['parent_id'], quiz['id'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], 'quiz-v1+lecture-v2+json-v1+replace-v1')
        self.assertEqual([p['number'] for p in calls[0][1]['question_plan']], [3])
        self.assertNotIn('replace_base', calls[0][1])   # the model sees only the kept stems
        self.assertEqual(calls[0][1]['replace']['note'], '换成医学检测情境')
        self.assertIn('换题规则 replace-v1', calls[0][2])
        before, after = quiz['result']['questions'], replaced['result']['questions']
        self.assertEqual(after[2]['stem'], '某检测灵敏度 0.9，患病率 0.01，阳性时患病的概率？')
        self.assertEqual([after[i] for i in (0, 1, 3)], [before[i] for i in (0, 1, 3)])
        self.assertEqual(self.client.post(f"/api/quizzes/{quiz['id']}/questions/9/replace", json={}).status_code, 404)

        bad = self.client.post(f"/api/quizzes/{replaced['id']}/questions/2/edit", json={'options': ['甲', '乙', '甲', '丁']})
        self.assertEqual(bad.status_code, 400)
        edited = self.client.post(f"/api/quizzes/{replaced['id']}/questions/2/edit",
                                  json={'stem': '教师改写的题干', 'answer': ' B ', 'options': ['甲', '乙', '丙', '丁']})
        self.assertEqual(edited.status_code, 201, edited.json)
        record = edited.json
        self.assertEqual((record['status'], record['parent_id'], record['context']['edits']),
                         ('succeeded', replaced['id'], [{'quiz_id': replaced['id'], 'number': 2}]))
        self.assertEqual((record['result']['questions'][1]['stem'], record['result']['questions'][1]['answer']), ('教师改写的题干', 'B'))
        self.assertTrue(record['integrity_valid'])
        self.assertEqual(self.client.post(f"/api/quizzes/{quiz['id']}/questions/1/edit", json={'stem': ''}).status_code, 400)

    def test_retry_of_lecture_quiz_keeps_its_prompt_versions(self):
        failed = self.submit(self.lecture_payload(), failure=True)
        self.assertEqual(failed['status'], 'failed')
        seen = []
        def model(prepared, attempted):
            seen.append((prepared.prompt_version, prepared.input_version))
            ctx = json.loads(json.loads(prepared.body)['messages'][1]['content'])['context']
            return module.ModelResult(json.dumps(fixture_result(ctx), ensure_ascii=False))
        with patch.object(module, 'call_qwen', model):
            response = self.client.post(f"/api/quizzes/{failed['id']}/retry")
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.queue.wait_idle(5))
        self.assertEqual(seen, [('quiz-v1+lecture-v1+json-v1', 'quiz-context-lecture-v1')])
        self.assertEqual(self.stored(response.json['id'])['request_sha256'], self.stored(failed['id'])['request_sha256'])


class DesignedQuizChecks(unittest.TestCase):
    """What the program checks in a quiz written for a designed lesson; correctness stays with the teacher."""
    def setUp(self):
        from test_lesson_design import design_reply
        import design as D
        self.view = D.quiz_view(design_reply())
        self.plan = [{'number': 1, 'type': 'short_answer', 'difficulty': 'basic', 'knowledge_id': 13},
                     {'number': 2, 'type': 'single_choice', 'difficulty': 'basic', 'knowledge_id': 13}]

    def context(self, designed=True):
        lecture = {'id': 1, 'content': '讲义'} | ({'design': self.view} if designed else {})
        return {'question_plan': self.plan, 'materials': [{'id': 13}], 'lecture': lecture}

    def reply(self, first_stem, option='A1 与 A2 的产量之比'):
        questions = [{**self.plan[0], 'stem': first_stem, 'options': [], 'answer': '变大', 'analysis': '对应学习目标 2。',
                      'common_mistake': '把 P(D|A1) 当成 P(A1|D)', 'source_ids': ['M13']},
                     {**self.plan[1], 'stem': '下列哪一项是全概率公式的分母？', 'options': [option, '0.032', '1', '0'],
                      'answer': 'A', 'analysis': '对应学习目标 1。', 'common_mistake': '只写一项', 'source_ids': []}]
        return json.dumps({'questions': questions}, ensure_ascii=False)

    def rejected(self, content, designed=True):
        with self.assertRaises(module.ModelError) as caught:
            parse_questions(content, self.context(designed))
        return str(caught.exception)

    def test_a_problem_the_students_already_did_is_sent_back(self):
        homework = self.view['used'][-1]['stem']
        for stem, name in [(self.view['used'][0]['stem'], '例1'),
                           ('两条生产线甲、乙的产量占比为 0.6、0.4，次品率为 0.02、0.05。抽到一件次品，求它来自甲的概率。', '例1'),
                           ('先验从 0.6 降到 0.2，后验怎样变化？说明理由。', '练3'),
                           (homework.replace('，', ', '), '作业1')]:
            self.assertIn(f'「{name}」', self.rejected(self.reply(stem)))
        # New numbers, a new question or a new situation is a variant, not a copy.
        for stem in ['两条产线 $A_1$、$A_2$ 的产量占比为 0.7、0.3，次品率为 0.01、0.04。抽到一件次品，求它来自 $A_1$ 的概率。',
                     '先验从 0.5 降到 0.1，后验怎样变化？说明理由。',
                     '某检测的灵敏度为 0.9，人群患病率为 0.01，检测阳性时真正患病的概率是多少？']:
            parse_questions(self.reply(stem), self.context())
        # A lecture without a design keeps the earlier rules: no copy check, no rewriting.
        plain = parse_questions(self.reply(self.view['used'][0]['stem']), self.context(designed=False))
        self.assertEqual(plain['questions'][1]['options'][0], 'A1 与 A2 的产量之比')

    def test_formulas_follow_the_handout_notation(self):
        result = parse_questions(self.reply('若先验 P(A1) 变大，P(A1|D) 怎样变化？'), self.context())
        first, second = result['questions']
        self.assertEqual(first['stem'], '若先验 $P(A_1)$ 变大，$P(A_1\\mid D)$ 怎样变化？')
        self.assertEqual(first['common_mistake'], '把 $P(D\\mid A_1)$ 当成 $P(A_1\\mid D)$')
        self.assertEqual((second['options'][0], second['answer']), ('$A_1$ 与 $A_2$ 的产量之比', 'A'))
        # A backslash the model did not double comes back as the command it meant; a broken one is sent back.
        restored = parse_questions(self.reply('若 $\\frac{a}{b}$ 变大呢？').replace('\\\\frac', '\\frac'), self.context())
        self.assertEqual(restored['questions'][0]['stem'], '若 $\\frac{a}{b}$ 变大呢？')
        self.assertIn('损坏的公式', self.rejected(self.reply('若 $x \x08yz$ 呢？')))   # "\\byz" is no command

    def test_questions_are_shared_out_over_the_learning_objectives(self):
        objectives = [dict(number=1, text='一', level='基础', knowledge_ids=[11], checked_by=[]),
                      dict(number=2, text='二', level='提高', knowledge_ids=[12], checked_by=[]),
                      dict(number=3, text='三', level='基础', knowledge_ids=[13], checked_by=[])]
        options = dict(count=5, difficulty='mixed', student_level='profile', types=['single_choice', 'calculation'],
                       knowledge_ids=[11, 12, 13])
        states = [{'id': 13, 'state': 'needs_support'}, {'id': 11, 'state': 'mastered'}]
        plan = question_plan(options, states, {'objectives': objectives})
        # Every objective once; the extra questions go to the weakest knowledge first (13, then 12);
        # the 提高 objective takes the hardest slots.
        self.assertEqual([(p['objective'], p['knowledge_id'], p['difficulty']) for p in plan],
                         [(1, 11, 'basic'), (3, 13, 'basic'), (3, 13, 'basic'), (2, 12, 'medium'), (2, 12, 'advanced')])
        self.assertEqual([p['type'] for p in plan], ['single_choice', 'calculation'] * 2 + ['single_choice'])
        # Fewer questions than objectives: the earlier objectives; a knowledge-point filter keeps only matching ones.
        self.assertEqual([p['objective'] for p in question_plan({**options, 'count': 2}, [], {'objectives': objectives})], [1, 2])
        self.assertEqual({p['objective'] for p in question_plan({**options, 'knowledge_ids': [12]}, [], {'objectives': objectives})}, {2})
        # Without a design the plan is the knowledge-point plan it always was.
        self.assertNotIn('objective', question_plan(options, states)[0])

    def test_the_planned_objective_and_distinct_options_are_kept(self):
        self.plan = [{**entry, 'objective': number} for number, entry in enumerate(self.plan, 1)]
        def reply(analysis='对应学习目标 1。先算全概率。', options=('A1 与 A2 的产量之比', '0.032', '1', '0')):
            data = json.loads(self.reply('全新的题目：先验变大时后验怎样变化？', option=options[0]))
            for q, entry in zip(data['questions'], self.plan):
                q['objective'] = entry['objective']
            data['questions'][0]['analysis'] = analysis
            data['questions'][1]['analysis'] = '只写一项会错。'
            data['questions'][1]['options'] = list(options)
            return json.dumps(data, ensure_ascii=False)
        result = parse_questions(reply(), self.context())
        self.assertEqual([q['objective'] for q in result['questions']], [1, 2])
        self.assertEqual([q['analysis'] for q in result['questions']], ['先算全概率。', '只写一项会错。'])
        self.assertIn('解析却写成目标 2', self.rejected(reply('对应学习目标 2。先算全概率。')))
        self.assertIn('学习目标分配', self.rejected(reply().replace('"objective": 2', '"objective": 1')))
        self.assertIn('数值相同', self.rejected(reply(options=('0.5', '1/2', '$0.25$', '1'))))

    def test_the_analysis_says_how_to_solve_and_nothing_about_the_quiz(self):
        from quizzes import solution_only
        self.assertEqual(solution_only('本题考查全概率公式。$P(D) = 0.7 \\times 0.01 + 0.3 \\times 0.04 = 0.019$，与例2类似，需教师复核。'),
                         '$P(D) = 0.7 \\times 0.01 + 0.3 \\times 0.04 = 0.019$。')
        self.assertEqual(solution_only('本题与练1同属一类，均为贝叶斯。先算 $P(D)$。'), '先算 $P(D)$。')
        self.assertEqual(solution_only('题中次品率与讲义冲突，以讲义 0.02 为准。'), '题中次品率与讲义冲突，以讲义 0.02 为准。')

    def test_the_same_numbers_in_a_new_situation_are_a_copy(self):
        from quizzes import copied_problem
        used = [dict(name='作业1', stem='某网站平均每分钟收到 $4$ 次访问请求，访问次数服从泊松分布。求某分钟内恰好收到 $3$ 次访问请求的概率。',
                     answer='$P(X=3)=\\frac{32}{3}e^{-4} \\approx 0.1954$')]
        stem = '某客服中心平均每小时接到 $4$ 次投诉电话，次数服从泊松分布。求某小时内恰好接到 $3$ 次投诉的概率 $P(X=3)$。（保留四位小数）'
        self.assertEqual(copied_problem(stem, used, '$\\frac{32}{3}e^{-4}\\approx 0.1954$'), '作业1')
        self.assertIsNone(copied_problem(stem.replace('$4$', '$5$'), used, '$\\frac{125}{6}e^{-5}\\approx 0.1404$'))

    def test_an_analysis_that_corrects_itself_goes_back(self):
        data = json.loads(self.reply('全新的题目：先验变大时后验怎样变化？'))
        data['questions'][0]['analysis'] = '$P(A_2\\mid D)=\\frac{3}{8}$。\n\n等等，重新核验计算：选项中无 $\\frac{3}{8}$，调整数值。'
        self.assertIn('改算的过程（「等等，」）', self.rejected(json.dumps(data, ensure_ascii=False)))
        data['questions'][0]['analysis'] = '先验变化后重新计算全概率：$P(D)=0.066$。选项 A、B 等等，都不对。'
        self.assertEqual(len(parse_questions(json.dumps(data, ensure_ascii=False), self.context())['questions']), 2)

    def test_more_ways_of_writing_symbols_follow_the_handout(self):
        result = parse_questions(self.reply('已知 P(A₁)=0.6，求 A₂ 的后验；P(X≥2)=1-3e^{-2}'), self.context())
        self.assertEqual(result['questions'][0]['stem'], '已知 $P(A_1)$=0.6，求 $A_2$ 的后验；P(X≥2)=1-$3e^{-2}$')

    def test_problems_a_revision_adds_count_as_done(self):
        from quizzes import revised_problems
        content = ('### 例1 双产线\n\n**题目**　' + self.view['used'][0]['stem'] + '\n\n'
                   '### 练4（提高） 新加的题\n\n**题目**　三条产线占比 0.5、0.3、0.2，求来自第三条的后验。\n')
        self.assertEqual(revised_problems(content, self.view['used']),
                         [dict(name='练4（修订稿）', role='讲义修订稿中的题', level='', answer='',
                               stem='三条产线占比 0.5、0.3、0.2，求来自第三条的后验。')])


class ObjectiveGrading(unittest.TestCase):
    def test_suggested_scores_are_summed_per_learning_objective(self):
        from grading import objective_lines, objective_summary
        reference = {'questions': [dict(number='1', objective=1, objective_text='1. 求后验'),
                                   dict(number='2', objective=2, objective_text='2. 先验的影响'),
                                   dict(number='3', objective=1, objective_text='1. 求后验'), dict(number='4')]}
        result = {'questions': [dict(number='1', score=8, max_score=10), dict(number='2', score=None, max_score=10),
                                dict(number='3', score=5.5, max_score=10), dict(number='4', score=3, max_score=5)]}
        summary = objective_summary(result, reference)
        self.assertEqual([(g['objective'], g['numbers'], g['score'], g['max_score'], g['pending']) for g in summary],
                         [(1, ['1', '3'], 13.5, 20, 0), (2, ['2'], 0, 10, 1)])
        self.assertEqual(objective_lines(summary)[1], '学习目标 2. 先验的影响：第 2 题，建议得分 0 / 10（1 题待核对未计分）')
        self.assertEqual(objective_summary(result, None), [])
