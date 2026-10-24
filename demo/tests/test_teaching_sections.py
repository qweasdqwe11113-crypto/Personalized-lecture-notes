"""Real transactions and queued, stubbed model replies; never contacts a model."""
import hashlib
import json
import unittest
from unittest.mock import patch

from test_profiles import ProfileDatabaseFixture, module
from jobs import JobQueue
from sections import replacement, split_sections

ORIGINAL = '# 授课讲义\n\n## 学习目标\n理解等式。\n\n## 本次学习安排\n先讲再练。\n\n## 核心讲解\n两边同减 3。[M1]\n\n## 例题与检验\nx + 3 = 5，x = 2。[M3]\n\n## 易错点与小结\n两边保持平衡。\n'
REPLY = '## 核心讲解\n两边同减 3，保持相等。[M1]\n### 课堂提问\n为什么两边都要减？预期回答：保持平衡。'


class SectionSpliceTests(unittest.TestCase):
    def test_duplicate_titles_use_exact_span_and_keep_crlf_outside(self):
        text = '# 标题\r\n\r\n## 同名\r\n第一节。\r\n\r\n## 同名\r\n第二节。\r\n'
        section = split_sections(text)[1]
        result = replacement({**section, 'base_content': text}, '## 同名\n替换第二节。')
        self.assertEqual(result, text[:section['start']] + '## 同名\n替换第二节。\r\n')

    def test_model_cannot_return_other_sections_or_change_title(self):
        edit = {**split_sections(ORIGINAL)[2], 'base_content': ORIGINAL}
        for text in ('说明\n'+REPLY, '## 其他章节\n内容', REPLY+'\n## 偷改别节\n内容',
                     REPLY+'\n  ## 缩进别节\n内容', REPLY+'\n # 偷改标题', '## 核心讲解'):
            with self.subTest(text=text), self.assertRaises(module.ModelError):
                replacement(edit, text)


class TeachingSectionTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        self.queue = JobQueue(lambda: module.db(), module.execute_job)
        self.queue_patch = patch.object(module, 'TASKS', self.queue)
        self.queue_patch.start()
        def original_model(prepared, attempted):
            attempted()
            return module.ModelResult(ORIGINAL)
        with patch.object(module, 'call_qwen', original_model):
            result = self.client.post('/api/generate', json=dict(student_id=2, topic='linear'))
        self.assertEqual(result.status_code, 200, result.json)
        self.lecture_id = result.json['id']
        self.original_run_id = result.json['generation_run_id']

    def tearDown(self):
        self.queue.wait_idle(5)
        self.queue.close()
        self.queue_patch.stop()

    def submit(self, reply=REPLY, **changes):
        sections = self.client.get(f'/api/lectures/{self.lecture_id}/sections',
                                   query_string={'base_revision': changes.get('base_revision', 0)}).json
        target = sections['sections'][2]
        data = dict(base_revision=0, expected_revision=sections['expected_revision'],
                    section_index=2, section_sha256=target['sha256'], action='expand',
                    instruction='补充课堂追问。', recorder='测试教师', confirmed=True)
        data.update(changes)
        def model(prepared, attempted):
            attempted()
            with self.fixture_db() as conn:
                row = conn.execute('SELECT * FROM generation_runs ORDER BY id DESC LIMIT 1').fetchone()
                self.assertEqual(row['request_body'], prepared.body)
                self.assertEqual(row['request_sha256'], hashlib.sha256(prepared.body.encode()).hexdigest())
            return module.ModelResult(reply, {'total_tokens': 12})
        with patch.object(module, 'call_qwen', model):
            result = self.client.post(f'/api/lectures/{self.lecture_id}/section-rewrites', json=data)
            self.assertEqual(result.status_code, 202, result.json)
            self.assertTrue(self.queue.wait_idle(5))
        return result.json

    def test_proposal_keeps_original_then_accepts_one_draft_idempotently(self):
        job = self.submit()
        detail = self.client.get('/api/jobs/'+job['id']).json
        self.assertTrue(detail['section_rewrite'])
        self.assertEqual(detail['status'], 'succeeded')
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}').json['content'], ORIGINAL)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}').json['generation_run_id'], self.original_run_id)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}/review').json['revisions'], [])
        url = f'/api/section-rewrites/{job["run_id"]}/accept'
        accepted = self.client.post(url, json={'confirmed': True})
        self.assertEqual(accepted.status_code, 201, accepted.json)
        rev = accepted.json['revisions'][0]
        target = split_sections(ORIGINAL)[2]
        self.assertEqual(rev['content'][:target['start']], ORIGINAL[:target['start']])
        self.assertTrue(rev['content'].endswith(ORIGINAL[target['end']:]))
        self.assertEqual(rev['status'], 'draft')
        self.assertFalse(any(rev['checklist'].values()))
        self.assertEqual(rev['resolutions'], [])
        again = self.client.post(url, json={'confirmed': True})
        self.assertEqual(again.json['saved_revision'], accepted.json['saved_revision'])
        self.assertEqual(len(again.json['revisions']), 1)

    def test_concurrent_review_change_prevents_adopting_old_candidate(self):
        job = self.submit()
        with self.fixture_db() as conn:
            conn.execute('UPDATE lectures SET review_revision=review_revision+1 WHERE id=%s', (self.lecture_id,))
        result = self.client.post(f'/api/section-rewrites/{job["run_id"]}/accept', json={'confirmed': True})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.get(f'/api/generation-runs/{job["run_id"]}').json['content'], REPLY)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}/review').json['revisions'], [])

    def test_second_proposal_can_use_a_saved_revision_and_preserves_original_trace(self):
        first = self.submit()
        accepted = self.client.post(f'/api/section-rewrites/{first["run_id"]}/accept', json={'confirmed':True}).json
        second = self.submit(REPLY+'\n再增加一次追问。', base_revision=accepted['saved_revision'])
        self.assertEqual(self.client.get('/api/jobs/'+second['id']).json['status'], 'succeeded')
        result = self.client.post(f'/api/section-rewrites/{second["run_id"]}/accept', json={'confirmed':True})
        self.assertEqual(result.status_code, 201, result.json)
        self.assertEqual(result.json['revisions'][0]['base_revision'], 1)
        self.assertEqual(len(result.json['revisions']), 2)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}').json['generation_run_id'], self.original_run_id)

    def test_maximum_instruction_can_be_accepted_and_full_input_is_kept(self):
        instruction = '讲' * 1500
        job = self.submit(instruction=instruction)
        result = self.client.post(f'/api/section-rewrites/{job["run_id"]}/accept', json={'confirmed':True})
        self.assertEqual(result.status_code, 201, result.json)
        run = self.client.get(f'/api/generation-runs/{job["run_id"]}').json
        self.assertEqual(run['context']['section_edit']['instruction'], instruction)

    def test_invalid_model_sections_and_sources_never_save_revisions(self):
        for reply in (REPLY+'\n## 额外章节\n越界', REPLY+' [M9999]', REPLY+' [V9]'):
            with self.subTest(reply=reply):
                job = self.submit(reply)
                self.assertEqual(self.client.get('/api/jobs/'+job['id']).json['status'], 'failed')
                self.assertEqual(self.client.post(f'/api/section-rewrites/{job["run_id"]}/accept', json={'confirmed':True}).status_code, 409)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}/review').json['revisions'], [])

    def test_failed_section_retries_exact_input_and_keeps_operation(self):
        job = self.submit('## 错误标题\n重试前失败。')
        original = self.client.get(f'/api/generation-runs/{job["run_id"]}').json
        def model(prepared, attempted):
            attempted()
            return module.ModelResult(REPLY)
        with patch.object(module, 'call_qwen', model):
            retry = self.client.post(f'/api/generation-runs/{job["run_id"]}/retry')
            self.assertEqual(retry.status_code, 202)
            self.assertTrue(self.queue.wait_idle(5))
        rerun = self.client.get(f'/api/generation-runs/{retry.json["run_id"]}').json
        self.assertEqual(rerun['request_body'], original['request_body'])
        self.assertEqual(rerun['prompt_version'], 'lecture-section-v1')
        self.assertEqual(rerun['status'], 'succeeded')

    def test_modified_section_sha_and_missing_custom_instruction_are_rejected(self):
        sections = self.client.get(f'/api/lectures/{self.lecture_id}/sections').json
        data = dict(base_revision=0, expected_revision=0, section_index=2, section_sha256='bad',
                    action='custom', recorder='教师', confirmed=True)
        url = f'/api/lectures/{self.lecture_id}/section-rewrites'
        self.assertEqual(self.client.post(url, json=data).status_code, 409)
        data['section_sha256'] = sections['sections'][2]['sha256']
        self.assertEqual(self.client.post(url, json=data).status_code, 400)

    def test_controls_and_requirements_are_frozen_and_no_profile_excludes_observation(self):
        def model(prepared, attempted):
            attempted()
            return module.ModelResult(ORIGINAL)
        options = dict(duration=30, lesson_type='correction', detail='detailed', examples=1,
                       exercises=2, requirements='只讲负号与错误解法。')
        with patch.object(module, 'call_qwen', model):
            result = self.client.post('/api/generate', json=dict(student_id=2, topic='linear',
                personalization_mode='none', learner_note='不发送的个人观察', teaching_options=options))
        self.assertEqual(result.status_code, 200, result.json)
        run = self.client.get(f'/api/generation-runs/{result.json["generation_run_id"]}').json
        self.assertEqual(run['context']['teaching_options'], options)
        self.assertNotIn('不发送的个人观察', run['request_body'])
        self.assertIn('课堂提问', run['request_payload']['messages'][0]['content'])
        self.assertIn('teaching-v1', run['prompt_version'])

    def test_invalid_controls_are_rejected_on_both_generation_paths(self):
        for options in (None, [], {'duration':True}, {'duration':181}, {'examples':-1},
                        {'exercises':11}, {'detail':'bad'}, {'lesson_type':[]}, {'requirements':'字'*2001}):
            for endpoint in ('/api/generate', '/api/jobs'):
                with self.subTest(options=options, endpoint=endpoint):
                    result=self.client.post(endpoint,json=dict(student_id=2,topic='linear',teaching_options=options))
                    self.assertEqual(result.status_code,400,result.json)
