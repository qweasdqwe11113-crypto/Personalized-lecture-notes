"""Sectioned generation, automatic repair, parallel writers, batches and live progress.

Real PostgreSQL in a disposable schema; every model reply is an explicit fixture.
"""
import io
import json
import unittest
from threading import Event, Lock
from unittest.mock import patch

import test_courses_jobs as base  # importing the class itself would make unittest run its tests again here
import qwen
import sectioned
from gate import ModelGate
from jobs import JobQueue
from progress import ProgressHub

OUTLINE = {'title': '隔离分章节讲义', 'notation': 'A1、A2 为产线，D 为次品',
           'sections': {name: [f'{name}要点一', f'{name}要点二'] for name, _, _ in sectioned.SECTIONS}}


def request_of(prepared):
    payload = json.loads(prepared.body)
    return payload, json.loads(payload['messages'][1]['content'])


def section_text(user, extra=''):
    title = user['target_section']
    marks = ' [K11][K12][K13]' if title == '核心讲解' else ''
    return f"## {title}\n{'讲' * user['section_quota']['low']} [M11]{marks}{extra}\n"


module = base.module


class PipelineTests(base.CourseJobTests):
    def new_payload(self, **overrides):
        return self.payload(generation_mode='sectioned-v1', lecture_format='plain',
                            teaching_options={'duration': 20, 'detail': 'brief', 'length_policy': 'duration-v2'}, **overrides)

    def staged(self, prepared, on_attempt):
        on_attempt()
        _, user = request_of(prepared)
        if 'target_section' not in user:
            return module.ModelResult(json.dumps(OUTLINE, ensure_ascii=False), {'total_tokens': 7})
        return module.ModelResult(section_text(user), {'total_tokens': 11})

    def run_job(self, model, payload=None):
        with patch.object(module, 'call_qwen', model):
            response = self.post(payload or self.new_payload())
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.tasks.wait_idle(10))
        job = self.tasks.detail(response.json['id'])
        with self.fixture_db() as conn:
            run = conn.execute('SELECT * FROM generation_runs WHERE id=%s', (job['run_id'],)).fetchone()
        return job, run

    def test_sections_are_written_separately_assembled_in_order_and_recorded(self):
        job, run = self.run_job(self.staged)
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertTrue(run['prompt_version'].endswith('+sectioned-v1'))
        headings = [line[3:] for line in run['content'].splitlines() if line.startswith('## ')]
        self.assertEqual(headings, [name for name, _, _ in sectioned.SECTIONS])
        self.assertTrue(run['content'].startswith('# 隔离分章节讲义\n'))
        stages = run['response_metadata']['stages']
        self.assertEqual([s['stage'] for s in stages if s['attempt'] == 1][0], 'outline')
        self.assertEqual(len(stages), 6)
        self.assertEqual(len({s['request_sha256'] for s in stages}), 6)
        self.assertEqual(run['usage'], {'total_tokens': 7 + 5 * 11, 'model_calls': 6})
        report = run['citation_check']['quality_report']
        self.assertEqual(next(c for c in report['automatic'] if c['code'] == 'length')['status'], 'pass')
        self.assertEqual(next(c for c in report['automatic'] if c['code'] == 'plan_locations')['status'], 'pass')
        self.assertTrue(report['version'].endswith('+pipeline-v3'))
        # The frozen request is the outline call, and server-only stage data never reaches the model.
        payload, user = request_of(type('P', (), {'body': run['request_body']}))
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertNotIn('sectioned', user['context'])
        self.assertIn('sectioned', run['context'])
        low, high = sectioned.target_range(run['context'])
        quotas = run['context']['sectioned']['quotas']
        self.assertGreaterEqual(sum(q['low'] for q in quotas), low)
        self.assertLessEqual(sum(q['high'] for q in quotas), high + 80)

    def test_short_section_is_rewritten_once_with_the_concrete_problem(self):
        seen = []

        def model(prepared, on_attempt):
            payload, user = request_of(prepared)
            if user.get('target_section') == '核心讲解':
                seen.append(payload['messages'])
                if len(payload['messages']) == 2:
                    on_attempt()
                    return module.ModelResult('## 核心讲解\n太短 [M11] [K11][K12][K13]\n')
            return self.staged(prepared, on_attempt)
        job, run = self.run_job(model)
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertEqual(len(seen), 2)
        self.assertEqual([m['role'] for m in seen[1]], ['system', 'user', 'assistant', 'user'])
        self.assertIn('低于本章节下限', seen[1][3]['content'])
        core = [s for s in run['response_metadata']['stages'] if s['stage'] == '核心讲解']
        self.assertEqual([(s['attempt'], bool(s['problems'])) for s in core], [(1, True), (2, False)])

    def test_overlong_section_and_missing_plan_markers_get_one_rewrite_and_the_closer_draft_wins(self):
        seen = {}

        def model(prepared, on_attempt):
            payload, user = request_of(prepared)
            title = user.get('target_section')
            if title in ('易错点与小结', '核心讲解'):
                seen.setdefault(title, []).append(payload['messages'])
                on_attempt()
                quota = user['section_quota']
                if title == '易错点与小结':  # first far too long, then within quota
                    size = quota['high'] * 3 if len(payload['messages']) == 2 else quota['high']
                    return module.ModelResult(f"## {title}\n{'讲' * size} [M11]\n")
                # the core section never marks K13: the rewrite is tried, the lecture is still saved
                self.assertEqual(user['required_plan_markers'], ['K11', 'K12', 'K13'])
                return module.ModelResult(f"## {title}\n{'讲' * quota['low']} [M11] [K11][K12]\n")
            return self.staged(prepared, on_attempt)
        job, run = self.run_job(model)
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertIn('超过本章节上限', seen['易错点与小结'][1][3]['content'])
        self.assertIn('缺少讲解安排定位 K13', seen['核心讲解'][1][3]['content'])
        self.assertEqual(len(seen['核心讲解']), 2)
        summary = next(q for q in run['context']['sectioned']['quotas'] if q['title'] == '易错点与小结')
        section = run['content'].split('## 易错点与小结')[1]
        self.assertLessEqual(sectioned.chinese(section), summary['high'])
        report = {c['code']: c['status'] for c in run['citation_check']['quality_report']['automatic']}
        self.assertEqual((report['length'], report['plan_locations']), ('pass', 'warning'))

    def test_section_that_keeps_citing_a_missing_source_fails_the_run_without_a_fake_lecture(self):
        def model(prepared, on_attempt):
            _, user = request_of(prepared)
            if user.get('target_section') == '例题与检验':
                on_attempt()
                return module.ModelResult(section_text(user, ' [M999]'))
            return self.staged(prepared, on_attempt)
        before = self.count('lectures')
        job, run = self.run_job(model)
        self.assertEqual((job['status'], run['error_code']), ('failed', 'invalid_section'))
        self.assertEqual(self.count('lectures'), before)
        self.assertIn('M999', run['error_message'])
        self.assertIn('## 核心讲解', run['content'])  # readable partial text is kept for the teacher

    def test_unusable_outline_falls_back_to_fixed_section_briefs(self):
        def model(prepared, on_attempt):
            _, user = request_of(prepared)
            if 'target_section' not in user:
                on_attempt()
                return module.ModelResult('我先说明一下思路……')
            self.assertNotIn('outline', user)
            return self.staged(prepared, on_attempt)
        job, run = self.run_job(model)
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertIsNone(run['response_metadata']['outline'])
        self.assertEqual([s['attempt'] for s in run['response_metadata']['stages'] if s['stage'] == 'outline'], [1, 2])
        self.assertTrue(run['content'].startswith('# ' + run['context']['topic']['title']))

    def test_cut_off_outline_is_recorded_and_sections_are_still_written(self):
        def model(prepared, on_attempt):
            _, user = request_of(prepared)
            if 'target_section' not in user:
                on_attempt()
                raise module.ModelError('truncated', '模型输出达到长度上限', 'output', module.ModelResult('{"title":"半截', {'total_tokens': 3}))
            return self.staged(prepared, on_attempt)
        job, run = self.run_job(model)
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        outline = [s for s in run['response_metadata']['stages'] if s['stage'] == 'outline']
        self.assertEqual(len(outline), 1)
        self.assertIn('长度上限', outline[0]['problems'][0])
        self.assertIsNone(run['response_metadata']['outline'])
        self.assertEqual(json.loads(run['request_body'])['max_tokens'], 4000)

    def test_spare_permits_let_sections_run_in_parallel_and_all_are_returned(self):
        module.GENERATION_LOCK.set_limit(3)
        guard, counts, both = Lock(), {'active': 0, 'peak': 0}, Event()

        def model(prepared, on_attempt):
            _, user = request_of(prepared)
            if 'target_section' not in user:
                return self.staged(prepared, on_attempt)
            with guard:
                counts['active'] += 1
                counts['peak'] = max(counts['peak'], counts['active'])
                if counts['active'] >= 2:
                    both.set()
            both.wait(2)
            result = self.staged(prepared, on_attempt)
            with guard:
                counts['active'] -= 1
            return result
        try:
            job, run = self.run_job(model)
        finally:
            module.GENERATION_LOCK.set_limit(1)
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertGreaterEqual(counts['peak'], 2)
        self.assertLessEqual(counts['peak'], 3)
        self.assertFalse(module.GENERATION_LOCK.locked())

    def test_retry_of_failed_sectioned_run_reuses_frozen_request_and_stage_prompt(self):
        def fail(prepared, on_attempt):
            on_attempt()
            raise module.ModelError('timeout', '测试超时')
        job, run = self.run_job(fail)
        self.assertEqual(run['status'], 'failed')
        with patch.object(module, 'call_qwen', self.staged):
            retry = self.client.post(f'/api/generation-runs/{run["id"]}/retry')
            self.assertEqual(retry.status_code, 202)
            self.assertTrue(self.tasks.wait_idle(10))
        with self.fixture_db() as conn:
            second = conn.execute('SELECT * FROM generation_runs WHERE id=%s', (retry.json['run_id'],)).fetchone()
        self.assertEqual(second['status'], 'succeeded')
        self.assertEqual(second['request_body'], run['request_body'])
        self.assertEqual(second['context']['sectioned'], run['context']['sectioned'])

    def test_single_call_mode_freezes_a_streaming_request_and_old_clients_are_unchanged(self):
        job, run = self.run_job(self.model, self.payload(generation_mode='single', lecture_format='rich-v1'))
        payload = json.loads(run['request_body'])
        self.assertTrue(payload['stream'])
        self.assertIn('+rich-v1', run['prompt_version'])
        self.assertIn('```figure', payload['messages'][0]['content'])
        _, legacy = self.run_job(self.model, self.payload())
        self.assertNotIn('stream', json.loads(legacy['request_body']))
        self.assertNotIn('rich-v1', legacy['prompt_version'])
        self.assertNotIn('lecture_pipeline', legacy['context'])
        self.assertEqual(self.post(self.payload(generation_mode='magic')).status_code, 400)

    def test_batch_queues_one_frozen_run_per_student_and_reports_bad_ids(self):
        queue = JobQueue(lambda: module.db(), module.execute_job, capacity=4, workers=2, bulk_capacity=20)
        with patch.object(module, 'TASKS', queue), patch.object(module, 'call_qwen', self.staged):
            payload = {k: v for k, v in self.new_payload().items() if k != 'student_id'}
            response = self.client.post('/api/jobs/batch', json={**payload, 'student_ids': [11, 12, 13, 999]})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(queue.wait_idle(15))
            jobs = response.json['jobs']
            self.assertEqual(response.json['queued'], 3)
            self.assertIn('error', jobs[3])
            status = self.client.get('/api/job-status?ids=' + ','.join(j['job_id'] for j in jobs[:3])).json['jobs']
            queue.close()
        self.assertEqual(sorted(s['student_id'] for s in status), [11, 12, 13])
        self.assertTrue(all(s['status'] == 'succeeded' and s['lecture_id'] for s in status))
        self.assertEqual(self.client.post('/api/jobs/batch', json={**payload, 'student_ids': []}).status_code, 400)
        self.assertEqual(self.client.get('/api/job-status?ids=nope').status_code, 400)

    def test_live_stream_delivers_section_text_then_ends(self):
        job, run = self.run_job(self.staged)
        body = self.client.get(f'/api/jobs/{job["id"]}/stream').get_data(as_text=True)
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith('data: {"')]
        self.assertTrue(body.rstrip().endswith('event: done\ndata: {}'))
        parts = {part['key']: part for event in events for part in event['parts']}
        self.assertEqual(set(parts), {name for name, _, _ in sectioned.SECTIONS})
        self.assertTrue(all(part['state'] == 'done' and part['text'].startswith('## ') for part in parts.values()))
        self.assertEqual(self.client.get('/api/jobs/' + '0' * 32 + '/stream').status_code, 404)

    def test_new_pipeline_recomputes_arithmetic_and_checks_figures(self):
        from quality import check_output
        ctx = module.get_context(11, 'probability_advanced')
        ctx.update(lecture_pipeline='v3', lecture_format='rich-v1')
        figure = json.dumps({'type': 'probability_tree', 'branches': [{'label': 'A1', 'p': 0.7}, {'label': 'A2', 'p': 0.4}]})
        content = ('# 概率\n## 例题与检验\n$P(D) = 0.6 \\times 0.02 + 0.4 \\times 0.05 = 0.034$ [M13]\n'
                   f'```figure\n{figure}\n```\n价格 $5 未闭合\n')
        report = check_output(content, ctx, ctx['topic']['goal'])
        status = {c['code']: c for c in report['automatic']}
        self.assertEqual(status['arithmetic']['status'], 'warning')
        self.assertIn('0.032', status['arithmetic']['detail'])
        self.assertEqual(status['figures']['status'], 'warning')
        self.assertEqual(status['math_markup']['status'], 'warning')
        self.assertEqual(report['figures']['count'], 1)
        legacy = check_output(content, module.get_context(11, 'probability_advanced'), ctx['topic']['goal'])
        self.assertNotIn('arithmetic', {c['code'] for c in legacy['automatic']})
        rendered = self.client.post('/api/figures/render', json={'figures': [figure, '{"type":"pie"}']}).json['figures']
        self.assertTrue(rendered[0]['ok'] and rendered[0]['svg'].startswith('<svg') and rendered[0]['warnings'])
        self.assertFalse(rendered[1]['ok'])


# The inherited course contracts already run in their own module.
for _name in [n for n in dir(base.CourseJobTests) if n.startswith('test_')]:
    setattr(PipelineTests, _name, None)


class TransportAndGateTests(unittest.TestCase):
    def test_sse_chunks_are_collected_and_forwarded_to_the_listener(self):
        lines = [b'data: {"model":"m","choices":[{"delta":{"content":"<think>x</think>\xe8\xae\xb2"}}]}\n', b'\n',
                 b'data: {"choices":[{"delta":{"content":"\xe4\xb9\x89"},"finish_reason":"stop"}]}\n',
                 b'data: {"choices":[],"usage":{"total_tokens":9}}\n', b'data: [DONE]\n']
        heard = []
        with qwen.stream_to(heard.append):
            result = qwen._read_stream(io.BytesIO(b''.join(lines)))
        self.assertEqual(''.join(heard), '<think>x</think>讲义')
        self.assertEqual(result['choices'][0], {'message': {'content': '<think>x</think>讲义'}, 'finish_reason': 'stop'})
        self.assertEqual(result['usage'], {'total_tokens': 9})
        self.assertEqual(qwen.visible_text('<think>草稿</think>正文<think>未完'), '正文')
        with self.assertRaises(qwen.ModelError) as caught:
            qwen._read_stream(io.BytesIO(b'data: {"choices":[{"delta":{"content":"\xe5\x8d\x8a"}}]}\ndata: {"error":{"message":"secret"}}\n'))
        self.assertEqual((caught.exception.code, caught.exception.partial.content), ('stream_error', '半'))
        self.assertNotIn('secret', str(caught.exception))

    def test_gate_bounds_concurrency_and_limit_one_is_the_old_lock(self):
        gate = ModelGate(2)
        self.assertTrue(gate.acquire(blocking=False) and gate.acquire(blocking=False))
        self.assertFalse(gate.acquire(blocking=False))
        self.assertFalse(gate.acquire(timeout=0.05))
        self.assertTrue(gate.locked())
        gate.release(); gate.release()
        self.assertFalse(gate.locked())
        with self.assertRaises(RuntimeError):
            gate.release()
        single = ModelGate(1)
        with single:
            self.assertFalse(single.acquire(blocking=False))
        self.assertEqual(ModelGate(99).limit, 8)

    def test_progress_hub_reports_only_changed_parts(self):
        hub = ProgressHub()
        self.assertIsNone(hub.changes(1, 0))
        hub.start(1, [('a', '甲'), ('b', '乙')])
        first = hub.changes(1, 0)
        self.assertEqual((first['order'], first['parts']), (['a', 'b'], []))
        hub.write(1, 'b', '乙的正文')
        second = hub.changes(1, first['version'])
        self.assertEqual([(p['key'], p['text']) for p in second['parts']], [('b', '乙的正文')])
        self.assertIsNone(hub.changes(1, second['version'], timeout=0.01))
        hub.finish(1)
        self.assertTrue(hub.changes(1, second['version'])['done'])

    def test_embedding_requests_are_split_into_service_sized_batches(self):
        from embed import QwenEmbedder
        sizes = []

        class Reply(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): return False

        def fake(request, timeout):
            texts = json.loads(request.data)['input']
            sizes.append(len(texts))
            return Reply(json.dumps({'data': [{'embedding': [float(len(t))]} for t in texts]}).encode())
        with patch('embed.urllib.request.urlopen', fake), patch.dict('os.environ', {'QWEN_EMBEDDING_BATCH_SIZE': '10'}):
            vectors = QwenEmbedder(model='m', base='https://embedding.invalid/v1', key='').embed(['字' * n for n in range(1, 24)])
        self.assertEqual(sizes, [10, 10, 3])
        self.assertEqual([v[0] for v in vectors], [float(n) for n in range(1, 24)])
