"""Deleting an upload, a student, a lecture and a grading record; real PostgreSQL in a disposable schema.

Uploads and grading records live in SQLite files under a temporary folder, never the real data folder.
"""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from flask import Flask
from psycopg.types.json import Jsonb

import test_courses_jobs as base  # importing the class itself would make unittest run its tests again here
from deletion import register_deletion
from uploads import register_uploads

module = base.module


class DeletionTests(base.CourseJobTests):
    def scratch(self):
        """A small app over a temporary folder: uploads and grading files of its own."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = Flask(__name__)
        app.testing = True
        register_uploads(app, folder.name)
        register_deletion(app, self.fixture_db, folder.name)
        return app.test_client(), Path(folder.name)

    def lecture_for(self, student_id):
        with patch.object(module, 'call_qwen', self.model):
            response = self.post({**self.payload(), 'student_id': student_id})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(self.tasks.wait_idle(10))
        job = self.tasks.detail(response.json['id'])
        self.assertEqual(job['status'], 'succeeded', job['error_message'])
        return job['lecture_id'], job['run_id']

    def rows(self, sql, *args):
        with self.fixture_db() as conn:
            return conn.execute(sql, args).fetchall()

    def test_an_upload_goes_with_its_materials_images_and_draft_only_when_asked(self):
        client, folder = self.scratch()
        made = client.post('/api/uploads', data={'file': (BytesIO('# 讲义资料\n条件概率的定义。'.encode()), '资料.md')})
        self.assertEqual(made.status_code, 201, made.json)
        upload = made.json['upload']['id']
        _, run_id = self.lecture_for(12)
        with self.fixture_db() as conn:
            for mid, url in ((901, f'upload:{upload}'), (902, f'upload:{upload}#p1-2'), (903, f'upload:{upload}x')):
                conn.execute("INSERT INTO materials VALUES (%s,11,'资料','条件概率','资料.md',%s,'')", (mid, url))
            asset = conn.execute('''INSERT INTO material_assets (material_id,upload_id,kind,label,source_sha256,normalized_sha256,
                mime,width,height,byte_size,normalize_version,bytes) VALUES (902,%s,'figure','图','s','n','image/png',1,1,1,'v',%s)
                RETURNING id''', (upload, b'x')).fetchone()['id']
            conn.execute('''INSERT INTO generation_run_assets (run_id,asset_id,ordinal,normalized_sha256,byte_size)
                VALUES (%s,%s,1,'n',1)''', (run_id, asset))
            course = conn.execute("INSERT INTO courses(name) VALUES ('删除测试') RETURNING id").fetchone()['id']
            conn.execute("INSERT INTO knowledge_drafts (upload_id,course_id,status) VALUES (%s,%s,'running')", (upload, course))
        preview = client.get(f'/api/uploads/{upload}/delete-preview').json
        self.assertEqual((preview['materials'], preview['knowledge_draft'], preview['busy']), (2, True, True))
        self.assertEqual(client.post(f'/api/uploads/{upload}/delete', json={'with_materials': True}).status_code, 409)
        with self.fixture_db() as conn:
            conn.execute("UPDATE knowledge_drafts SET status='ready' WHERE upload_id=%s", (upload,))
        refused = client.post(f'/api/uploads/{upload}/delete')
        self.assertEqual((refused.status_code, refused.json['materials']), (409, 2))
        self.assertEqual(len(self.rows('SELECT id FROM materials WHERE id IN (901,902)')), 2)

        response = client.post(f'/api/uploads/{upload}/delete', json={'with_materials': True})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([r['id'] for r in self.rows('SELECT id FROM materials WHERE id IN (901,902,903)')], [903])
        self.assertEqual(self.rows('SELECT id FROM material_assets WHERE id=%s', asset), [])
        self.assertEqual(self.rows('SELECT id FROM generation_run_assets WHERE asset_id=%s', asset), [])
        self.assertEqual(len(self.rows('SELECT id FROM generation_runs WHERE id=%s', run_id)), 1)   # the run keeps its saved input
        self.assertEqual(self.rows('SELECT upload_id FROM knowledge_drafts WHERE upload_id=%s', upload), [])
        with sqlite3.connect(folder / '.runtime' / 'uploads.sqlite3') as store:
            self.assertEqual(store.execute('SELECT count(*) FROM uploads WHERE id=?', (upload,)).fetchone()[0], 0)
        gone = client.get(f'/api/uploads/{upload}/download')
        self.assertEqual(gone.status_code, 404)
        self.assertIn('原资料已删除', gone.json['error'])
        self.assertEqual(client.get(f'/api/uploads/{upload}/delete-preview').status_code, 404)

    def test_a_student_goes_everywhere_and_nobody_else_does(self):
        lecture, run = self.lecture_for(11)
        other, _ = self.lecture_for(12)
        with self.fixture_db() as conn:
            for sid in (11, 12):
                conn.execute('''INSERT INTO quiz_jobs (id,status,topic_key,student_id,context,options,request_body,request_sha256)
                    VALUES (%s,'failed','probability_advanced',%s,'{}','{}','{}','x')''', (f'quiz-{sid}', sid))
            conn.execute('''INSERT INTO course_tasks(key,payload) VALUES ('deletion_lesson',
                '{"student_ids": [11, 12], "title": "删除测试"}')''')
            before = {table: conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']
                      for table in ('lectures', 'generation_runs', 'quiz_jobs', 'learning_evidence', 'students')}
        preview = self.client.get('/api/students/11/delete-preview').json
        self.assertEqual((preview['name'], preview['lectures'], preview['runs'], preview['quizzes']), ('小许', 1, 1, 1))
        self.assertGreater(preview['evidence'], 0)
        self.assertNotIn('_runs', preview)
        self.assertEqual(self.client.post('/api/students/11/delete', json={'confirm_name': '小唐'}).status_code, 400)
        with self.fixture_db() as conn:
            conn.execute("UPDATE generation_jobs SET status='running' WHERE run_id=%s", (run,))
        self.assertEqual(self.client.post('/api/students/11/delete', json={'confirm_name': '小许'}).status_code, 409)
        with self.fixture_db() as conn:
            conn.execute("UPDATE generation_jobs SET status='succeeded' WHERE run_id=%s", (run,))

        response = self.client.post('/api/students/11/delete', json={'confirm_name': ' 小许 '})
        self.assertEqual(response.status_code, 200, response.json)
        with self.fixture_db() as conn:
            tables = [r['table_name'] for r in conn.execute('''SELECT table_name FROM information_schema.columns
                WHERE column_name='student_id' AND table_schema=current_schema()''').fetchall()]
            self.assertGreaterEqual(len(tables), 7)   # student_knowledge, evidence, history, lectures, runs, quizzes, classes
            for table in tables:
                self.assertEqual(conn.execute(f'SELECT count(*) AS n FROM {table} WHERE student_id=11').fetchone()['n'], 0, table)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM students WHERE id=11').fetchone()['n'], 0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM lectures WHERE id=%s', (lecture,)).fetchone()['n'], 0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM lectures WHERE id=%s', (other,)).fetchone()['n'], 1)
            after = {table: conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] for table in before}
            self.assertEqual(after, {**before, 'lectures': before['lectures'] - 1, 'generation_runs': before['generation_runs'] - 1,
                                     'quiz_jobs': before['quiz_jobs'] - 1, 'students': before['students'] - 1,
                                     'learning_evidence': before['learning_evidence'] - preview['evidence']})
            lesson = conn.execute("SELECT payload FROM course_tasks WHERE key='deletion_lesson'").fetchone()['payload']
            self.assertEqual(lesson['student_ids'], [12])
        self.assertEqual(self.client.get('/api/students/11/delete-preview').status_code, 404)

    def test_a_lecture_goes_with_its_records_and_its_quiz_says_so(self):
        lecture, run = self.lecture_for(12)
        other, other_run = self.lecture_for(12)
        body = '{}'
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO lecture_issues (lecture_id,target_revision,category,excerpt,start_line,end_line,
                content_sha256,detail,support,source,recorder) VALUES (%s,0,'other','片段',1,1,'x','说明','not_applicable','teacher','教师')''',
                         (lecture,))
            failed = conn.execute('''INSERT INTO generation_runs (student_id,topic_key,topic,goal,context,prompt_version,input_version,
                request_body,request_sha256,status,finished_at,error_stage,error_code,error_message)
                VALUES (12,'ppt_export','课件','',%s,'p','i',%s,%s,'failed',now(),'model','timeout','超时') RETURNING id''',
                                  (Jsonb({'ppt_export': {'lecture_id': lecture}}), body, hashlib.sha256(body.encode()).hexdigest())).fetchone()['id']
            conn.execute("INSERT INTO generation_jobs (id,run_id,status) VALUES ('ppt-job',%s,'queued')", (failed,))
            conn.execute('''INSERT INTO quiz_jobs (id,status,topic_key,student_id,context,options,request_body,request_sha256)
                VALUES ('quiz-linked','failed','probability_advanced',12,%s,'{"count": 3, "difficulty": "basic"}','{}','x')''',
                         (Jsonb({'lecture': {'id': lecture, 'revision': 0}, 'topic': {'title': '概率'}}),))
        preview = self.client.get(f'/api/lectures/{lecture}/delete-preview').json
        self.assertEqual((preview['runs'], preview['issues'], preview['quizzes_kept']), (2, 1, 1))
        self.assertEqual(self.client.post(f'/api/lectures/{lecture}/delete').status_code, 409)   # the slides job is queued
        with self.fixture_db() as conn:
            conn.execute("UPDATE generation_jobs SET status='failed' WHERE id='ppt-job'")

        self.assertEqual(self.client.post(f'/api/lectures/{lecture}/delete').status_code, 200)
        self.assertEqual(self.rows('SELECT id FROM lectures WHERE id=%s', lecture), [])
        self.assertEqual(self.rows('SELECT id FROM lecture_issues WHERE lecture_id=%s', lecture), [])
        self.assertEqual(self.rows('SELECT id FROM generation_runs WHERE id IN (%s,%s)', run, failed), [])
        self.assertEqual(self.rows('SELECT id FROM generation_jobs WHERE run_id IN (%s,%s)', run, failed), [])
        self.assertEqual(len(self.rows('SELECT id FROM generation_runs WHERE id=%s', other_run)), 1)
        self.assertEqual(len(self.rows('SELECT id FROM lectures WHERE id=%s', other)), 1)
        quiz = next(q for q in self.client.get('/api/quizzes').json['quizzes'] if q['id'] == 'quiz-linked')
        self.assertEqual((quiz['lecture_id'], quiz['lecture_deleted']), (lecture, True))
        self.assertEqual(self.client.get(f'/api/lectures/{lecture}/delete-preview').status_code, 404)

    def test_a_grading_record_goes_once_it_is_finished(self):
        client, folder = self.scratch()
        (folder / '.runtime').mkdir()
        with sqlite3.connect(folder / '.runtime' / 'grading.sqlite3') as store:
            store.execute('CREATE TABLE grading_jobs (id TEXT PRIMARY KEY, filename TEXT, status TEXT, extracted TEXT)')
            store.executemany('INSERT INTO grading_jobs VALUES (?,?,?,?)',
                              [('done', '答卷.pdf', 'done', '学生的答案'), ('busy', '答卷2.pdf', 'grading', '')])
        self.assertEqual(client.post('/api/grading/busy/delete').status_code, 409)
        self.assertEqual(client.get('/api/grading/done/delete-preview').json['title'], '答卷.pdf')
        self.assertEqual(client.post('/api/grading/done/delete').status_code, 200)
        with sqlite3.connect(folder / '.runtime' / 'grading.sqlite3') as store:
            self.assertEqual([r[0] for r in store.execute('SELECT id FROM grading_jobs')], ['busy'])
        self.assertEqual(client.post('/api/grading/done/delete').status_code, 404)
        self.assertEqual(json.loads(client.get('/api/grading/missing/delete-preview').data)['error'], '阅卷记录不存在。')


# The inherited course contracts already run in their own module.
for _name in [n for n in dir(base.CourseJobTests) if n.startswith('test_')]:
    setattr(DeletionTests, _name, None)
