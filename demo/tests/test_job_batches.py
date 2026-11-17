"""Batch help: a usage estimate before queuing, and one request that sends every interrupted run again.

Real PostgreSQL in a disposable schema; the model is a fixture.
"""
from unittest.mock import patch

import test_courses_jobs as base  # importing the class itself would make unittest run its tests again here

module = base.module


class BatchHelpTests(base.CourseJobTests):
    def single(self, **overrides):
        return self.payload(generation_mode='single', **overrides)

    def finish(self, model, count):
        with patch.object(module, 'call_qwen', model):
            for _ in range(count):
                self.assertEqual(self.post(self.single()).status_code, 202)
                self.assertTrue(self.tasks.wait_idle(10))

    def test_the_estimate_is_the_median_of_recent_complete_lessons_written_the_same_way(self):
        empty = self.client.get('/api/jobs/estimate?count=5&mode=single')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual((empty.json['basis'], empty.json['tokens'], empty.json['minutes']), (0, None, None))
        self.finish(self.model, 3)

        def failing(prepared, on_attempt):
            on_attempt()
            raise module.ModelError('timeout', '模型请求超时，请稍后重试。')
        self.finish(failing, 1)
        with self.fixture_db() as conn:
            runs = conn.execute("SELECT id FROM generation_runs WHERE status='succeeded' ORDER BY id").fetchall()
            for run, (tokens, seconds) in zip(runs, [(1000, 60), (3000, 600), (2000, 120)]):
                conn.execute("UPDATE generation_runs SET usage=jsonb_build_object('total_tokens',%s::int),elapsed_seconds=%s WHERE id=%s",
                             (tokens, seconds, run['id']))
            # A section rewrite is not a lesson; it does not count.
            conn.execute('''UPDATE generation_runs SET usage='{"total_tokens": 900000}',
                            context=context||'{"section_edit": {}}' WHERE id=%s''', (runs[0]['id'],))
        data = self.client.get('/api/jobs/estimate?count=6&mode=single').json
        self.assertEqual((data['basis'], data['tokens']), (2, 6 * 2500))
        self.assertEqual(data['concurrency'], self.tasks.workers)
        self.assertEqual(data['minutes'], -(-6 * 360 // self.tasks.workers // 60))
        self.assertEqual(self.client.get('/api/jobs/estimate?count=6&mode=designed-v1').json['basis'], 0)
        for query in ('count=0', 'count=61', 'count=x', 'count=3&mode=other'):
            self.assertEqual(self.client.get('/api/jobs/estimate?' + query).status_code, 400, query)

    def test_interrupted_runs_are_sent_again_once_each_with_their_saved_input(self):
        def failing(prepared, on_attempt):
            on_attempt()
            raise module.ModelError('timeout', '模型请求超时，请稍后重试。')
        self.finish(failing, 3)
        with self.fixture_db() as conn:
            failed = [r['id'] for r in conn.execute("SELECT id FROM generation_runs ORDER BY id").fetchall()]
            # What JobQueue.recover leaves after a restart; the third failure stays an ordinary timeout.
            conn.execute("UPDATE generation_runs SET error_code='process_interrupted' WHERE id=ANY(%s)", (failed[:2],))
            bodies = {r['id']: r['request_body'] for r in conn.execute('SELECT id,request_body FROM generation_runs').fetchall()}
        self.assertEqual(self.client.get('/api/generation-runs').json['interrupted'], 2)
        with patch.object(module, 'call_qwen', self.model):
            response = self.client.post('/api/jobs/retry-interrupted')
            self.assertEqual(response.status_code, 202)
            self.assertTrue(self.tasks.wait_idle(10))
        self.assertEqual((response.json['queued'], response.json['skipped']), (2, []))
        self.assertEqual([job['run_id'] for job in response.json['jobs']], failed[:2])
        with self.fixture_db() as conn:
            for job in response.json['jobs']:
                run = conn.execute('SELECT status,request_body FROM generation_runs WHERE id=%s', (job['new_run_id'],)).fetchone()
                self.assertEqual((run['status'], run['request_body']), ('succeeded', bodies[job['run_id']]))
                parent = conn.execute('SELECT parent_run_id FROM generation_jobs WHERE id=%s', (job['job_id'],)).fetchone()
                self.assertEqual(parent['parent_run_id'], job['run_id'])
        self.assertEqual(self.client.get('/api/generation-runs').json['interrupted'], 0)
        again = self.client.post('/api/jobs/retry-interrupted').json
        self.assertEqual((again['queued'], again['jobs'], again['skipped']), (0, [], []))


# The inherited course contracts already run in their own module.
for _name in [n for n in dir(base.CourseJobTests) if n.startswith('test_')]:
    setattr(BatchHelpTests, _name, None)
