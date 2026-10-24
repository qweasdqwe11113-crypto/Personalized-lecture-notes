"""Generation trace tests: real PostgreSQL, stubbed HTTP, no real model traffic."""
from io import BytesIO
import hashlib
import json
import os
from unittest.mock import patch
import urllib.error

from test_profiles import ProfileDatabaseFixture, module
import qwen

SECRET = 'fixture-secret-never-store'


class GenerationTraceTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        self.environment = patch.dict(os.environ, {'QWEN_API_KEY': SECRET,
            'QWEN_BASE_URL': 'https://qwen.example.invalid/v1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def model_reply(self, content='# HTTP 测试夹具\n内容 [M3]', finish='stop'):
        return {'model': 'provider-fixture', 'choices': [{'message': {'content': content},
                'finish_reason': finish}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}

    def generate(self, reply=None, effect=None, goal=''):
        if effect is None:
            effect = lambda *_args, **_kwargs: BytesIO(json.dumps(reply or self.model_reply()).encode())
        with patch.object(qwen.urllib.request, 'urlopen', side_effect=effect):
            return self.client.post('/api/generate', json={'student_id': 2, 'topic': 'linear', 'goal': goal})

    def trace(self, response):
        run_id = response.get_json()['generation_run_id']
        return self.client.get(f'/api/generation-runs/{run_id}').get_json()

    def counts(self):
        with self.fixture_db() as conn:
            return {table: conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']
                    for table in ('lectures', 'generation_runs')}

    def test_saved_bytes_are_exactly_sent_and_saved_before_http(self):
        sent = []
        def transport(req, timeout):
            with self.fixture_db() as conn:
                saved = conn.execute('SELECT * FROM generation_runs ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(saved['status'], 'running')
            self.assertIsNotNone(saved['attempted_at'])
            self.assertEqual(req.data, saved['request_body'].encode('utf-8'))
            self.assertEqual(hashlib.sha256(req.data).hexdigest(), saved['request_sha256'])
            self.assertEqual(req.get_header('Authorization'), 'Bearer ' + SECRET)
            self.assertEqual(timeout, 600)
            sent.append(req.data)
            return BytesIO(json.dumps(self.model_reply()).encode())
        response = self.generate(effect=transport, goal='核查自定义目标与默认目标的分别留存。')
        self.assertEqual(response.status_code, 200)
        run = self.trace(response)
        self.assertEqual(run['status'], 'succeeded')
        self.assertTrue(run['integrity_valid'])
        self.assertEqual(run['lecture_id'], response.get_json()['id'])
        self.assertEqual(run['response_metadata']['response_model'], 'provider-fixture')
        self.assertEqual(run['prompt_version'], 'lecture-math-v3+expanded-v1')
        self.assertEqual(run['input_version'], 'lecture-context-v4')
        payload = run['request_payload']
        self.assertEqual(payload['temperature'], .3)
        self.assertEqual(payload['max_tokens'], 16000)
        self.assertEqual(payload['chat_template_kwargs'], {'enable_thinking': False})
        user = json.loads(payload['messages'][1]['content'])
        self.assertEqual(user['goal'], run['goal'])
        self.assertNotEqual(user['goal'], user['context']['topic']['goal'])
        self.assertEqual(user['context']['scope_check']['goal'], run['goal'])
        self.assertTrue(user['context']['scope_check']['custom_goal'])
        self.assertEqual(run['citation_check']['quality_report']['version'], 'math-text-check-v1')
        self.assertEqual([m['citation_id'] for m in user['context']['materials']], ['M1', 'M2', 'M3'])
        self.assertNotIn(SECRET, json.dumps(run))
        download = self.client.get(f'/api/generation-runs/{run["id"]}/input')
        self.assertEqual(download.data, sent[0])
        self.assertEqual(download.headers['Cache-Control'], 'no-store')
        lecture = self.client.get(f'/api/lectures/{run["lecture_id"]}').get_json()
        self.assertEqual(lecture['generation_run_id'], run['id'])

    def test_missing_key_preserves_prepared_input_without_marking_attempt(self):
        with patch.dict(os.environ, {'QWEN_API_KEY': ''}):
            response = self.generate()
        run = self.trace(response)
        self.assertEqual((response.status_code, run['status'], run['error_code']), (502, 'failed', 'missing_key'))
        self.assertIsNone(run['attempted_at'])
        self.assertIsNotNone(run['finished_at'])
        self.assertIsNone(run['lecture_id'])
        self.assertEqual(self.counts()['lectures'], 1)

    def test_invalid_endpoint_does_not_persist_credential_strings(self):
        with patch.dict(os.environ, {'QWEN_BASE_URL': f'https://user:{SECRET}@example.invalid/v1'}):
            response = self.generate()
        run = self.trace(response)
        self.assertEqual(run['error_code'], 'invalid_endpoint')
        self.assertIsNone(run['attempted_at'])
        self.assertNotIn(SECRET, json.dumps(run))

    def test_http_service_error_is_sanitized_and_recorded(self):
        def error(*_args, **_kwargs):
            raise urllib.error.HTTPError(f'https://example.invalid/?key={SECRET}', 503, SECRET,
                                         None, BytesIO(SECRET.encode()))
        response = self.generate(effect=error)
        run = self.trace(response)
        self.assertEqual((run['status'], run['error_code']), ('failed', 'http_error'))
        self.assertEqual(run['response_metadata']['http_status'], 503)
        self.assertNotIn(SECRET, json.dumps(response.get_json()) + json.dumps(run))
        self.assertEqual(self.counts()['lectures'], 1)

    def test_timeouts_and_connection_errors_keep_safe_failure_records(self):
        for exception, code in [(TimeoutError(SECRET), 'timeout'),
                                (urllib.error.URLError(SECRET), 'unreachable')]:
            with self.subTest(code=code):
                response = self.generate(effect=exception)
                run = self.trace(response)
                self.assertEqual(run['error_code'], code)
                self.assertEqual(run['status'], 'failed')
                self.assertIsNotNone(run['attempted_at'])
                self.assertNotIn(SECRET, json.dumps(run))

    def test_malformed_json_and_protocol_are_recorded(self):
        malformed = self.generate(effect=lambda *_a, **_k: BytesIO(b'not JSON'))
        self.assertEqual(self.trace(malformed)['error_code'], 'invalid_json')
        for reply in [[], {}, {'choices': []}, {'choices': [None]}, {'choices': [{'message': None}]}]:
            with self.subTest(reply=reply):
                response = self.generate(effect=lambda *_a, **_k: BytesIO(json.dumps(reply).encode()))
                self.assertEqual(self.trace(response)['error_code'], 'invalid_protocol')
        self.assertEqual(self.counts()['lectures'], 1)

    def test_truncated_output_retains_partial_text_and_usage_as_failure(self):
        response = self.generate(reply=self.model_reply('<think>hidden reasoning</think>\n# 尚未完成\n片段 [M3]', 'length'))
        run = self.trace(response)
        self.assertEqual(run['error_code'], 'truncated')
        self.assertEqual(run['content'], '# 尚未完成\n片段 [M3]')
        self.assertEqual(run['usage']['completion_tokens'], 20)
        self.assertNotIn('hidden reasoning', json.dumps(run))
        self.assertEqual(run['citation_check']['quality_report']['automatic'][1]['status'], 'warning')
        self.assertEqual(self.counts()['lectures'], 1)

    def test_empty_or_unfinished_reasoning_is_never_saved_as_lecture(self):
        for content, code in [('', 'empty_output'), ('<think>unfinished private reasoning', 'unfinished_output')]:
            with self.subTest(code=code):
                response = self.generate(reply=self.model_reply(content))
                run = self.trace(response)
                self.assertEqual(run['error_code'], code)
                self.assertEqual(run['content'], '')
                self.assertNotIn('private reasoning', json.dumps(run))

    def test_invalid_citation_keeps_rejected_draft_separate_from_lectures(self):
        response = self.generate(reply=self.model_reply('# 被拒绝的夹具\n引用 [M999]'))
        run = self.trace(response)
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['error_code'], 'invalid_citation')
        self.assertEqual(run['citation_check']['invalid_ids'], ['999'])
        self.assertIn('[M999]', run['content'])
        self.assertIsNone(run['lecture_id'])
        self.assertEqual(self.counts()['lectures'], 1)

    def test_success_and_lecture_rollback_together_on_storage_failure(self):
        with self.fixture_db() as conn:
            conn.execute("ALTER TABLE generation_runs ADD CONSTRAINT fixture_storage_failure CHECK (status <> 'succeeded')")
        response = self.generate()
        run = self.trace(response)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(run['error_stage'], 'storage')
        self.assertEqual(run['status'], 'failed')
        self.assertIsNone(run['lecture_id'])
        self.assertIn('HTTP 测试夹具', run['content'])
        self.assertEqual(self.counts()['lectures'], 1)

    def test_failure_finalization_outage_is_reported_without_false_completed_status(self):
        with patch.object(module, 'fail_run', side_effect=module.psycopg.OperationalError(SECRET)):
            with self.assertLogs(module.app.logger, level='ERROR') as logs:
                response = self.generate(effect=TimeoutError(SECRET))
        run = self.trace(response)
        self.assertFalse(response.get_json()['failure_recorded'])
        self.assertEqual(run['status'], 'running')
        self.assertIsNone(run['finished_at'])
        self.assertIn('结束状态未能更新', response.get_json()['error'])
        self.assertNotIn(SECRET, ''.join(logs.output))

    def test_unexpected_exception_never_exposes_raw_exception_text(self):
        with self.assertLogs(module.app.logger, level='ERROR') as logs:
            response = self.generate(effect=RuntimeError(SECRET))
        run = self.trace(response)
        self.assertEqual(run['error_code'], 'unexpected_error')
        self.assertNotIn(SECRET, json.dumps(run) + json.dumps(response.get_json()) + ''.join(logs.output))

    def test_failure_finalization_cannot_claim_an_already_closed_run(self):
        response = self.generate(effect=TimeoutError('fixture timeout'))
        original = self.trace(response)
        with self.fixture_db() as conn:
            with self.assertRaises(RuntimeError):
                module.fail_run(conn, original['id'], module.ModelError('other', 'new failure'), 1)
        self.assertEqual(original, self.trace(response))

    def test_history_remains_missing_instead_of_fabricating_old_prompts(self):
        old = self.client.get(f'/api/lectures/{self.lecture_id}').get_json()
        self.assertIsNone(old['generation_run_id'])
        self.generate()
        self.assertEqual(old, self.client.get(f'/api/lectures/{self.lecture_id}').get_json())
        self.assertEqual(self.counts()['generation_runs'], 1)
        for path in ['/api/generation-runs/999', '/api/generation-runs/999/input']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_input_tampering_is_visible_and_download_is_blocked(self):
        response = self.generate()
        run = self.trace(response)
        with self.fixture_db() as conn:
            conn.execute("UPDATE generation_runs SET request_body='{}' WHERE id=%s", (run['id'],))
        changed = self.client.get(f'/api/generation-runs/{run["id"]}').get_json()
        self.assertFalse(changed['integrity_valid'])
        self.assertEqual(self.client.get(f'/api/generation-runs/{run["id"]}/input').status_code, 409)

    def test_changes_during_model_call_do_not_rebuild_or_mutate_saved_input(self):
        before = self.knowledge()
        def transport(req, **_kwargs):
            self.assertEqual(self.save_evidence().status_code, 201)
            return BytesIO(json.dumps(self.model_reply()).encode())
        response = self.generate(effect=transport)
        run = self.trace(response)
        context = json.loads(run['request_payload']['messages'][1]['content'])['context']
        self.assertEqual(next(s for s in context['states'] if s['id'] == 3)['state'], before['state'])
        self.assertEqual(next(s for s in run['context']['states'] if s['id'] == 3)['state'], before['state'])
        self.assertEqual(self.knowledge()['state'], 'partial')

    def test_repeated_inputs_get_separate_runs_with_same_input_digest(self):
        first, second = self.generate(), self.generate()
        a, b = self.trace(first), self.trace(second)
        self.assertNotEqual(a['id'], b['id'])
        self.assertNotEqual(a['lecture_id'], b['lecture_id'])
        self.assertEqual(a['request_sha256'], b['request_sha256'])

    def test_no_materials_is_a_preflight_failure_with_unsent_input(self):
        with self.fixture_db() as conn:
            conn.execute('DELETE FROM materials')
        response = self.generate()
        run = self.trace(response)
        self.assertEqual(response.status_code, 400)
        self.assertEqual((run['status'], run['error_code']), ('failed', 'no_materials'))
        self.assertIsNone(run['attempted_at'])
        self.assertEqual(self.counts()['lectures'], 1)

    def test_invalid_requests_and_busy_rejections_do_not_create_fake_runs(self):
        self.assertEqual(self.client.post('/api/generate', json={'student_id': True, 'topic': 'linear'}).status_code, 400)
        self.assertEqual(self.client.post('/api/generate', json={'student_id': 999, 'topic': 'linear'}).status_code, 404)
        module.GENERATION_LOCK.acquire()
        try:
            self.assertEqual(self.client.post('/api/generate', json={'student_id': 2, 'topic': 'linear'}).status_code, 429)
        finally:
            module.GENERATION_LOCK.release()
        self.assertEqual(self.counts()['generation_runs'], 0)
