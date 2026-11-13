"""Course → class → student contracts on real PostgreSQL; model calls are fixtures."""
import io
import json
from threading import Lock
from unittest.mock import patch

from psycopg.types.json import Jsonb

from test_profiles import ProfileDatabaseFixture, module
from classes import CLASS_RULE, class_state, ensure_course_structure
from jobs import JobQueue
from quizzes import QuizQueue
from prompts import prepare_request


class ClassTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        with self.fixture_db() as conn:
            ensure_course_structure(conn)
            self.course = conn.execute("SELECT id FROM courses WHERE preset_key='junior-math'").fetchone()['id']
            self.demo = conn.execute('SELECT id FROM classes WHERE course_id=%s', (self.course,)).fetchone()['id']
        # Nothing here may reach the model configured in .env: unless a test supplies its own
        # fixture model, every call fails at once, and quizzes use their own queue and lock.
        self.offline = patch.object(module, 'call_qwen', side_effect=module.ModelError('test', '隔离测试，不调用模型。'))
        self.offline.start()
        self.tasks = JobQueue(lambda: module.db(), module.execute_job)
        self.task_patch = patch.object(module, 'TASKS', self.tasks)
        self.task_patch.start()
        self.quizzes = QuizQueue(lambda: module.db(), lambda prepared, attempted: module.call_qwen(prepared, attempted), Lock())
        self.quiz_patch = patch.object(module, 'QUIZZES', self.quizzes)
        self.quiz_patch.start()

    def tearDown(self):
        self.tasks.wait_idle(5)
        self.tasks.close()
        self.task_patch.stop()
        self.quizzes.wait_idle(5)
        self.quizzes.close()
        self.quiz_patch.stop()
        self.offline.stop()

    def json(self, method, url, body=None, status=200):
        response = getattr(self.client, method)(url, json=body)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def new_class(self, name='二班', course=None):
        return self.json('post', f'/api/courses/{course or self.course}/classes', {'name': name}, 201)['class']['id']

    def model(self, prepared, on_attempt):
        on_attempt()
        return module.ModelResult('# 全班讲义\n等式两边同减 [M1][M2][M3] [K1][K2][K3]', {'total_tokens': 5})

    def test_migration_seeds_demo_class_once_and_files_legacy_copies(self):
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO course_tasks(key,payload) VALUES ('custom_legacy',%s)''',
                         (Jsonb({'title': '旧副本', 'course': '高中物理', 'stage': '高中', 'knowledge_ids': [],
                                 'student_ids': [1, 2, 3], 'preparation_copy': True, 'custom': True, 'goal': ''}),))
            conn.execute('DELETE FROM schema_migrations')
            conn.execute('DELETE FROM class_students WHERE student_id=1')
            ensure_course_structure(conn)  # demo class exists: not recreated, and nobody is re-enrolled
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM classes').fetchone()['n'], 2)
            self.assertFalse(conn.execute('SELECT 1 FROM class_students WHERE student_id=1').fetchone())
            physics = conn.execute("SELECT id FROM courses WHERE name='高中物理' AND stage='高中'").fetchone()
            self.assertEqual(conn.execute("SELECT payload->>'course_id' AS c FROM course_tasks").fetchone()['c'], str(physics['id']))
            ensure_course_structure(conn)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM courses').fetchone()['n'], 3)
        courses = self.json('get', '/api/courses')
        demo = next(c for c in courses['courses'] if c['id'] == self.course)
        self.assertEqual(demo['classes'][0]['student_ids'], [2, 3])
        self.assertEqual(set(demo['lesson_keys']), {'basic', 'linear'})
        self.assertEqual(courses['unassigned_students'], 1)

    def test_one_class_per_course_with_explicit_transfer_and_leaving_keeps_the_record(self):
        second = self.new_class()
        conflict = self.client.post(f'/api/classes/{second}/students', json={'student_id': 1})
        self.assertEqual(conflict.status_code, 409)
        self.assertIn('演示班级', conflict.get_json()['error'])
        self.json('post', f'/api/classes/{second}/students', {'student_id': 1, 'move': True}, 201)
        topics = module.topic_catalog()
        self.assertIn(1, topics['linear']['student_ids'])  # presets keep their demo learners
        with self.fixture_db() as conn:
            self.assertEqual(conn.execute('SELECT class_id FROM class_students WHERE student_id=1').fetchone()['class_id'], second)
        self.json('post', f'/api/classes/{second}/students/1/remove', {})
        self.assertEqual(self.json('get', '/api/students/1/profile')['student']['name'], '小林')
        created = self.json('post', f'/api/classes/{second}/students',
                            {'name': '小新', 'subtitle': '初二', 'profile': '新同学'}, 201)['student']
        self.assertEqual(self.json('get', f'/api/classes/{second}/profile')['students'][0]['id'], created['id'])

    def test_new_lesson_in_a_course_serves_only_that_courses_students(self):
        other = self.json('post', '/api/courses', {'name': '高中物理', 'stage': '高中'}, 201)['course']['id']
        lesson = self.json('post', '/api/course-tasks', {'preparation_copy': True, 'course_id': other, 'title': '速度',
                                                         'knowledge_names': ['速度']}, 201)
        self.assertEqual(lesson['task']['course'], '高中物理')
        self.assertEqual(module.topic_catalog()[lesson['key']]['student_ids'], [])
        physics = self.new_class('物理一班', other)
        self.json('post', f'/api/classes/{physics}/students', {'student_id': 2}, 201)
        self.assertEqual(module.topic_catalog()[lesson['key']]['student_ids'], [2])
        renamed = self.json('put', f'/api/courses/{other}', {'name': '高中物理（必修）', 'stage': '高中'})
        self.assertEqual(renamed['course']['name'], '高中物理（必修）')
        self.assertEqual(module.topic_catalog()[lesson['key']]['course'], '高中物理（必修）')
        legacy = self.json('post', '/api/course-tasks', {'preparation_copy': True, 'title': '无课程旧接口'}, 201)
        self.assertEqual(module.topic_catalog()[legacy['key']]['student_ids'], [1, 2, 3])

    def test_class_profile_counts_states_and_states_its_rule(self):
        self.assertEqual(class_state(dict(needs_support=1, partial=2, mastered=0, unknown=5)), 'needs_support')
        self.assertEqual(class_state(dict(needs_support=0, partial=1, mastered=3, unknown=0)), 'mastered')
        self.assertEqual(class_state(dict(needs_support=0, partial=2, mastered=2, unknown=0)), 'partial')
        self.assertEqual(class_state(dict(needs_support=0, partial=0, mastered=0, unknown=4)), 'unknown')
        profile = self.json('get', f'/api/classes/{self.demo}/profile')
        self.assertEqual([s['name'] for s in profile['students']], ['小林', '小周', '小陈'])
        self.assertEqual(profile['rule'], CLASS_RULE)
        for point in profile['points']:
            self.assertEqual(sum(point['counts'].values()), 3)
            self.assertEqual(class_state(point['counts']), point['state'])
        self.assertTrue(profile['focus_students'])
        lesson = self.json('get', f'/api/classes/{self.demo}/profile?topic=basic')
        self.assertEqual([p['id'] for p in lesson['points']], [1, 2])
        self.assertEqual(self.client.get(f'/api/classes/{self.demo}/profile?topic=probability_basic').status_code, 400)

    def test_class_notes_are_versioned_with_history(self):
        body = {'name': '初二（3）班', 'profile': '课堂讨论积极，计算粗心。', 'recorder': '王老师', 'reason': '开学观察',
                'expected_revision': 0}
        saved = self.json('put', f'/api/classes/{self.demo}', body)
        self.assertEqual(saved['class']['profile_revision'], 1)
        self.assertEqual(saved['history'][0]['reason'], '开学观察')
        self.assertEqual(self.client.put(f'/api/classes/{self.demo}', json=body).status_code, 409)

    def test_student_profile_can_be_limited_to_one_course(self):
        with self.fixture_db() as conn:
            conn.execute("INSERT INTO knowledge_points VALUES (90,'别的课程知识点')")
            conn.execute("INSERT INTO student_knowledge(student_id,knowledge_id,state,evidence) VALUES (1,90,'partial','x')")
        everything = self.json('get', '/api/students/1/profile')
        course = self.json('get', f'/api/students/1/profile?course_id={self.course}')
        self.assertIn(90, [s['id'] for s in everything['states']])
        self.assertEqual([s['id'] for s in course['states']], [1, 2, 3])
        self.assertEqual(course['classes'][0]['class_name'], '演示班级')

    def test_class_context_aggregates_without_names_and_scopes_class_material(self):
        second = self.new_class()
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO materials(id,knowledge_id,title,content,source_title,source_url,section,class_id)
                VALUES (50,1,'二班讲义','二班专用','二班','upload:x','',%s)''', (second,))
        ctx = module.get_context(None, 'linear', 'profile', class_id=self.demo)
        self.assertEqual(ctx['personalization_mode'], 'class')
        self.assertEqual(ctx['student']['name'], '演示班级（全班）')
        self.assertEqual(ctx['class_target'], {'id': self.demo})
        self.assertNotIn(50, [m['id'] for m in ctx['materials']])
        self.assertTrue(all('class_id' not in m for m in ctx['materials']))
        self.assertEqual([s['class_size'] for s in ctx['states']], [3, 3, 3])
        self.assertTrue(all(p['action'].startswith(('面向全班', '分层', '压缩', '班级')) for p in ctx['personalization_plan']))
        body = prepare_request(ctx, ctx['topic']['goal'], module.MODEL)
        self.assertTrue(body.prompt_version.endswith('+class-v1'))
        for name in ('小林', '小周', '小陈'):
            self.assertNotIn(name, json.dumps(ctx['student'], ensure_ascii=False))
            self.assertNotIn(name, json.dumps(ctx['states'], ensure_ascii=False))
        self.json('post', f'/api/classes/{second}/students', {'student_id': 2, 'move': True}, 201)
        self.assertIn(50, [m['id'] for m in module.get_context(2, 'linear')['materials']])
        self.assertNotIn(50, [m['id'] for m in module.get_context(1, 'linear')['materials']])
        self.assertIn(50, [m['id'] for m in module.get_context(None, 'linear', 'none', class_id=second)['materials']])
        self.assertIsNone(module.get_context(None, 'probability_basic', class_id=self.demo))
        self.assertIsNone(module.get_context(None, 'linear', class_id=self.new_class('空班')))

    def test_class_lecture_is_saved_for_the_class_and_listed(self):
        with patch.object(module, 'call_qwen', self.model):
            job = self.json('post', '/api/jobs', {'class_id': self.demo, 'topic': 'linear', 'personalization_mode': 'class'}, 202)
            self.assertTrue(self.tasks.wait_idle(5))
        detail = self.tasks.detail(job['id'])
        self.assertEqual(detail['status'], 'succeeded', detail)
        with self.fixture_db() as conn:
            lecture = conn.execute('SELECT * FROM lectures WHERE id=%s', (detail['lecture_id'],)).fetchone()
        self.assertIsNone(lecture['student_id'])
        self.assertEqual(lecture['class_id'], self.demo)
        history = self.json('get', '/api/bootstrap')['history']
        self.assertEqual(history[0]['name'], '演示班级（全班）')
        self.assertEqual(self.tasks.recent()[0]['name'], '演示班级（全班）')
        self.assertEqual(self.client.get(f"/api/lectures/{lecture['id']}/profile-comparison").status_code, 400)
        both = self.client.post('/api/jobs', json={'class_id': self.demo, 'student_id': 1, 'topic': 'linear'})
        self.assertEqual(both.status_code, 400)
        quiz = self.client.post('/api/quizzes', json={'lecture_id': lecture['id'], 'student_level': 'profile', 'count': 2,
                                                      'difficulty': 'basic', 'types': ['calculation']})
        self.assertEqual(quiz.status_code, 202, quiz.get_json())
        with self.fixture_db() as conn:
            row = conn.execute('SELECT class_id,context FROM quiz_jobs').fetchone()
        self.assertEqual(row['class_id'], self.demo)
        self.assertEqual(row['context']['personalization_mode'], 'class')

    def test_class_quiz_uses_the_class_profile(self):
        response = self.client.post('/api/quizzes', json={'topic': 'linear', 'class_id': self.demo, 'student_level': 'profile',
                                                          'count': 3, 'difficulty': 'mixed', 'types': ['calculation']})
        self.assertEqual(response.status_code, 202, response.get_json())
        with self.fixture_db() as conn:
            row = conn.execute('SELECT * FROM quiz_jobs').fetchone()
        self.assertIsNone(row['student_id'])
        self.assertEqual(row['class_id'], self.demo)
        self.assertEqual(row['context']['personalization_mode'], 'class')
        self.assertIn('本次题组面向整个班级', json.loads(row['request_body'])['messages'][0]['content'])
        self.assertNotIn('小林', json.dumps(row['context']['states'], ensure_ascii=False))
        self.assertEqual(self.client.post('/api/quizzes', json={'topic': 'probability_basic', 'class_id': self.demo,
                                                                'student_level': 'standard', 'count': 1, 'difficulty': 'basic',
                                                                'types': ['calculation']}).status_code, 400)

    def test_class_upload_becomes_class_only_material(self):
        second = self.new_class()
        record = {'id': 'u1', 'title': '二班练习', 'filename': 'a.md', 'source_url': '', 'content': '练习正文',
                  'course_id': self.course, 'class_id': second}
        with patch('catalog.read_upload', return_value=record):
            saved = self.json('post', '/api/course-tasks/linear/materials', {'upload_id': 'u1', 'knowledge_id': 1}, 201)
        self.assertEqual(saved['class_id'], second)
        listed = self.json('get', '/api/course-tasks/linear/materials')['materials']
        self.assertEqual(next(m for m in listed if m['id'] == saved['material_id'])['class_name'], '二班')
        other = self.json('post', '/api/courses', {'name': '高中物理'}, 201)['course']['id']
        with patch('catalog.read_upload', return_value={**record, 'course_id': other, 'class_id': None}):
            self.assertEqual(self.client.post('/api/course-tasks/linear/materials',
                                              json={'upload_id': 'u1', 'knowledge_id': 2}).status_code, 400)

    def test_student_list_import_goes_into_the_class(self):
        second = self.new_class()
        csv = '姓名,年级或说明,学习特点\n小林,初二,同名但不同班\n小新,初二,新同学\n'
        form = lambda **extra: {'kind': 'students', 'class_id': str(second),
                                'file': (io.BytesIO(csv.encode()), 'a.csv'), **extra}
        preview = self.client.post('/api/students/import/preview', data=form()).get_json()
        self.assertEqual([r['action'] for r in preview['rows']], ['create', 'create'])
        self.assertIn('其他班级有同名学生', preview['rows'][0]['note'])
        done = self.client.post('/api/students/import', data=form(token=preview['token'], confirmed='true'))
        self.assertEqual(done.status_code, 201, done.get_json())
        members = self.json('get', f'/api/classes/{second}/profile')['students']
        self.assertEqual([m['name'] for m in members], ['小林', '小新'])
        again = self.client.post('/api/students/import/preview', data=form()).get_json()
        self.assertEqual([r['action'] for r in again['rows']], ['skip', 'skip'])
