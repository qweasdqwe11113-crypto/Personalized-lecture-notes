"""Lecture → quiz → grading → teacher-confirmed evidence, across PostgreSQL and a temporary grading store."""
from io import BytesIO
import json
import tempfile
from threading import Lock
import time

from flask import Flask

from test_profiles import module
from test_quizzes import QuizFixture
import grading
from quizzes import grading_reference


class TeachingLoopTests(QuizFixture):
    def grading_client(self, scores):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = Flask(__name__)
        def model(prepared, attempt):
            attempt()
            return module.ModelResult(json.dumps({'questions': [dict(number=str(n), stem=f'第{n}题', student_answer='作答',
                reference_answer='参考', basis='provided', max_score=10, score=s, status='graded', feedback=f'第{n}题错因')
                for n, s in scores]}, ensure_ascii=False))
        grading.register_grading(app, directory.name, model, Lock(),
                                 quiz_reference=lambda identity: grading_reference(self.queue.detail(identity)))
        return app.test_client()

    def wait(self, client, identity):
        for _ in range(500):
            row = client.get('/api/grading/' + identity).json
            if row['status'] in ('completed', 'failed'):
                time.sleep(.02)
                return row
            time.sleep(.01)
        self.fail('grading did not finish')

    def states(self, sid, ids):
        return {s['id']: s for s in self.client.get(f'/api/students/{sid}/knowledge-states?ids={",".join(map(str, ids))}').json['states']}

    def test_grading_a_lecture_quiz_produces_point_drafts_that_teachers_confirm(self):
        quiz = self.submit(dict(lecture_id=self.lecture_id, lecture_revision=0, student_level='profile', count=3,
                                difficulty='basic', types=['short_answer'], knowledge_ids=[1, 3], requirements=''))
        self.assertEqual(quiz['status'], 'succeeded', quiz)
        planned = {str(q['number']): q['knowledge_id'] for q in quiz['result']['questions']}
        client = self.grading_client([(1, 10), (2, 4), (3, 6)])
        response = client.post('/api/grading', data={'paper': (BytesIO('学生作答'.encode()), '小周作答.txt'), 'quiz_id': quiz['id']})
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(self.wait(client, response.json['id'])['status'], 'completed')
        drafts = client.get(f"/api/grading/{response.json['id']}/evidence-drafts").json
        self.assertEqual(drafts['quiz']['lecture_id'], self.lecture_id)
        self.assertEqual({g['knowledge_id']: g['question_numbers'] for g in drafts['groups']},
                         {k: [n for n, v in planned.items() if v == k] for k in set(planned.values())})
        target = next(g for g in drafts['groups'] if g['knowledge_id'] == 3)
        before = self.states(2, [3])[3]
        saved = self.client.post('/api/students/2/evidence', json=dict(
            knowledge_id=3, expected_revision=before['revision'], source='quiz', task_content=target['task_content'],
            performance=target['performance'], observed_at=drafts['observed_on'] + 'T00:00:00+00:00', recorder='测试教师',
            decision='retain', new_state=None, reason='单次测验，保留原判断。', confirmed=True))
        self.assertEqual(saved.status_code, 201, saved.json)
        after = self.states(2, [3])[3]
        self.assertEqual((after['state'], after['revision']), (before['state'], before['revision'] + 1))
        latest = self.profile()['evidence_history'][0]
        self.assertEqual((latest['source'], latest['decision']), ('quiz', 'retain'))
        self.assertIn('小周作答.txt', latest['task_content'])

    def test_knowledge_states_resolve_copied_points_to_the_diagnosed_identity(self):
        copied = self.client.post('/api/course-tasks/linear/copy').json['task']
        original = self.states(2, [1, 2, 3])
        aliased = self.states(2, copied['knowledge_ids'])
        for new_id, old_id in zip(copied['knowledge_ids'], [1, 2, 3]):
            self.assertEqual(aliased[new_id]['canonical_id'], old_id)
            self.assertEqual((aliased[new_id]['state'], aliased[new_id]['revision']),
                             (original[old_id]['state'], original[old_id]['revision']))
        self.assertEqual(self.client.get('/api/students/2/knowledge-states?ids=1;drop').status_code, 400)
        self.assertEqual(self.client.get('/api/students/999/knowledge-states?ids=1').status_code, 404)
