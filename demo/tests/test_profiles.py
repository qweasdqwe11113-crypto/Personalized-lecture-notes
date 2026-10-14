"""Real PostgreSQL integration checks in a disposable schema of the dedicated demo DB.

Run: .venv/bin/python -m unittest discover -s demo/tests -v
No Qwen requests are made; model responses in one transport test are explicit fixtures.
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as module
from profiles import retain_initial_evidence

DEMO = Path(__file__).resolve().parents[1]


class ProfileDatabaseFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = 'profile_test_' + uuid4().hex
        cls.original_db = module.db
        with cls.original_db() as conn:
            if conn.execute('SELECT current_database() AS name').fetchone()['name'] != 'lecture_demo_prototype':
                raise RuntimeError('Tests only accept the dedicated demo database.')
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
        cls.db_patch = patch.object(module, 'db', cls.fixture_db)
        cls.db_patch.start()
        cls.old_testing = module.app.testing
        module.app.testing = True

    @classmethod
    def fixture_db(cls):
        return psycopg.connect(module.DSN, row_factory=dict_row, connect_timeout=5,
                               options=f'-c search_path={cls.schema}')

    @classmethod
    def tearDownClass(cls):
        cls.db_patch.stop()
        module.app.testing = cls.old_testing
        with cls.original_db() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def setUp(self):
        self.client = module.app.test_client()
        # Build a legacy database first, then migrate it without changing its records.
        schema = (DEMO / 'schema.sql').read_text()
        seed = json.loads((DEMO / 'seed.json').read_text())
        with self.fixture_db() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
            conn.execute(schema.split('-- Additive migration:')[0])
            for s in seed['students']:
                conn.execute('INSERT INTO students VALUES (%s,%s,%s,%s)',
                             (s['id'], s['name'], s['subtitle'], s['profile']))
            for k in seed['knowledge_points']:
                conn.execute('INSERT INTO knowledge_points VALUES (%s,%s)', (k['id'], k['name']))
            for s in seed['states']:
                conn.execute('INSERT INTO student_knowledge VALUES (%s,%s,%s,%s)', s)
            for m in seed['materials']:
                conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s)',
                             tuple(m[k] for k in ('id','knowledge_id','title','content','source_title','source_url','section')))
            conn.execute(schema)
            retain_initial_evidence(conn)
        self.before_context = module.get_context(2, 'linear')
        with self.fixture_db() as conn:
            row = conn.execute('''INSERT INTO lectures
                (student_id,topic,goal,context,content,model,elapsed_seconds,citation_check)
                VALUES (2,%s,%s,%s,%s,'fixture-only',0,%s) RETURNING id''',
                (module.TOPICS['linear']['title'], module.TOPICS['linear']['goal'],
                 Jsonb(self.before_context), '# 隔离测试讲义\n历史原稿 [M3]',
                 Jsonb({'has_citations': True}))).fetchone()
            self.lecture_id = row['id']

    def profile(self, sid=2):
        return self.client.get(f'/api/students/{sid}/profile').get_json()

    def payload(self, **overrides):
        return {**{'knowledge_id': 3, 'expected_revision': 0, 'source': 'observation',
            'task_content': '虚拟课堂任务：求解 2x + 5 = 17 并解释等式变形。',
            'performance': '能得到 x=6，但仍不能解释两边同减 5 的依据。',
            'observed_at': datetime.now(timezone.utc).isoformat(), 'recorder': '测试教师',
            'decision': 'update', 'new_state': 'partial', 'reason': '计算正确但解释不足，确认部分掌握。',
            'confirmed': True}, **overrides}

    def save_evidence(self, payload=None, sid=2):
        return self.client.post(f'/api/students/{sid}/evidence', json=payload or self.payload())

    def knowledge(self, profile=None):
        return next(s for s in (profile or self.profile())['states'] if s['id'] == 3)


class ProfileIntegrationTests(ProfileDatabaseFixture):
    def test_migration_is_idempotent_and_preserves_legacy_dates(self):
        before = self.profile()
        with self.fixture_db() as conn:
            conn.execute((DEMO / 'schema.sql').read_text())
            retain_initial_evidence(conn)
            retain_initial_evidence(conn)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM learning_evidence').fetchone()['n'], 9)
        self.assertEqual(before, self.profile())
        self.assertTrue(all(e['observed_at'] is None and e['decision'] == 'baseline' for e in before['evidence_history']))
        self.assertEqual(self.knowledge()['revision'], 0)
        unknown = next(s for s in self.profile(1)['states'] if s['id'] == 3)
        self.assertEqual(unknown['state'], 'unknown')
        self.assertIn('不判断为薄弱', unknown['strategy'])

    def test_teacher_update_retains_previous_evidence_and_changes_current_strategy(self):
        old = self.knowledge()
        response = self.save_evidence()
        self.assertEqual(response.status_code, 201)
        result = response.get_json()
        current = self.knowledge(result)
        self.assertEqual((current['state'], current['revision']), ('partial', 1))
        self.assertEqual(current['evidence'], self.payload()['performance'])
        event = result['evidence_history'][0]
        self.assertEqual((event['previous_state'], event['confirmed_state']), ('needs_support', 'partial'))
        self.assertEqual(event['previous_evidence'], old['evidence'])
        self.assertEqual(current['evidence_id'], event['id'])
        context = module.get_context(2, 'linear')
        json.dumps(context, ensure_ascii=False)  # All nested dates must serialize for Qwen.
        state = next(s for s in context['states'] if s['id'] == 3)
        self.assertIn('正确步骤', state['strategy'])
        self.assertEqual(state['recent_evidence'][0]['reason'], self.payload()['reason'])

    def test_conflicting_evidence_can_be_retained_without_overwriting_judgment(self):
        old = self.knowledge()
        result = self.save_evidence(self.payload(decision='retain', new_state=None,
            performance='本次答案正确，但与先前符号错误记录冲突。', reason='需再核查解释，保留原判断。')).get_json()
        current = self.knowledge(result)
        self.assertEqual(current['state'], old['state'])
        self.assertEqual(current['evidence'], old['evidence'])
        self.assertEqual(current['evidence_id'], old['evidence_id'])
        self.assertEqual(current['revision'], 1)
        self.assertEqual(result['evidence_history'][0]['decision'], 'retain')
        context = module.get_context(2, 'linear')
        self.assertEqual(next(s for s in context['states'] if s['id'] == 3)['recent_evidence'][0]['decision'], 'retain')

    def test_stale_request_returns_conflict_without_inserting_evidence(self):
        self.assertEqual(self.save_evidence().status_code, 201)
        before = self.profile()
        response = self.save_evidence(self.payload(new_state='mastered'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(before, self.profile())

    def test_frequent_updates_do_not_crowd_out_other_knowledge_evidence(self):
        for revision in range(51):
            self.assertEqual(self.save_evidence(self.payload(knowledge_id=1,
                expected_revision=revision, new_state='mastered')).status_code, 201)
        context = module.get_context(2, 'linear')
        self.assertEqual(len(next(s for s in context['states'] if s['id'] == 1)['recent_evidence']), 3)
        self.assertEqual(len(next(s for s in context['states'] if s['id'] == 3)['recent_evidence']), 1)

    def test_concurrent_requests_cannot_overwrite_each_other(self):
        def save(state):
            with module.app.test_client() as client:
                return client.post('/api/students/2/evidence', json=self.payload(new_state=state)).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(save, ['partial', 'mastered']))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(self.profile()['evidence_count'], 4)
        self.assertEqual(self.knowledge()['revision'], 1)

    def test_profile_edit_saves_both_snapshots_and_protects_version(self):
        original = self.profile()['student']
        data = {'expected_revision': 0, 'subtitle': '解释依据需巩固', 'profile': '虚拟学生，已能计算，需练习说明变形依据。',
                'recorder': '测试教师', 'reason': '补充本次课堂观察。', 'confirmed': True}
        response = self.client.post('/api/students/2/profile', json=data)
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result['student']['profile_revision'], 1)
        self.assertEqual(result['profile_history'][0]['previous_snapshot'], original)
        self.assertEqual(result['profile_history'][0]['new_snapshot'], result['student'])
        self.assertEqual(self.client.post('/api/students/2/profile', json=data).status_code, 409)
        self.assertEqual(result, self.profile())

    def test_no_change_and_unconfirmed_profile_are_not_saved(self):
        original = self.profile()
        student = original['student']
        data = {'expected_revision': 0, 'subtitle': student['subtitle'], 'profile': student['profile'],
                'recorder': '测试', 'reason': '无变化', 'confirmed': True}
        self.assertEqual(self.client.post('/api/students/2/profile', json=data).status_code, 400)
        data.update(profile='changed', confirmed=False)
        self.assertEqual(self.client.post('/api/students/2/profile', json=data).status_code, 400)
        self.assertEqual(original, self.profile())

    def test_invalid_evidence_is_rejected_without_mutating_data(self):
        original = self.profile()
        cases = [{'confirmed': False}, {'expected_revision': True}, {'knowledge_id': True},
                 {'source': 'initial_snapshot'}, {'source': []}, {'new_state': 'excellent'},
                 {'new_state': []}, {'performance': '  '}, {'task_content': 'x'*1201},
                 {'recorder': 1}, {'reason': ''}, {'observed_at': '2026-01-01T12:00:00'},
                 {'observed_at': '9999-01-01T00:00:00+00:00'}, {'observed_at': None}, {'decision': 'auto'}]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.save_evidence(self.payload(**case)).status_code, 400)
        self.assertEqual(self.client.post('/api/students/2/evidence', json=[]).status_code, 400)
        self.assertEqual(original, self.profile())

    def test_missing_student_or_knowledge_does_not_create_records(self):
        original = self.profile()
        self.assertEqual(self.client.get('/api/students/99/profile').status_code, 404)
        self.assertEqual(self.save_evidence(sid=99).status_code, 404)
        self.assertEqual(self.save_evidence(self.payload(knowledge_id=99)).status_code, 404)
        self.assertEqual(original, self.profile())

    def test_missing_state_starts_unknown_and_stale_insertion_rolls_back(self):
        with self.fixture_db() as conn:
            conn.execute("INSERT INTO knowledge_points VALUES (4,'测试知识点')")
        state = next(s for s in self.profile()['states'] if s['id'] == 4)
        self.assertEqual((state['state'], state['revision']), ('unknown', 0))
        self.assertEqual(self.save_evidence(self.payload(knowledge_id=4, expected_revision=1)).status_code, 409)
        with self.fixture_db() as conn:
            self.assertIsNone(conn.execute('SELECT * FROM student_knowledge WHERE student_id=2 AND knowledge_id=4').fetchone())
        self.assertEqual(self.save_evidence(self.payload(knowledge_id=4)).status_code, 201)

    def test_transaction_failure_leaves_no_orphan_evidence(self):
        original = self.profile()
        with self.fixture_db() as conn:
            conn.execute("ALTER TABLE knowledge_state_history ADD CONSTRAINT fixture_failure CHECK (reason <> 'force failure')")
        response = self.save_evidence(self.payload(reason='force failure'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(original, self.profile())

    def test_old_lecture_is_immutable_and_generation_reads_latest_profile(self):
        original = self.client.get(f'/api/lectures/{self.lecture_id}').get_json()
        download = self.client.get(f'/api/lectures/{self.lecture_id}/download').data
        self.save_evidence()
        model_contexts = []
        def fixture_model(prepared, on_attempt):
            context = json.loads(json.loads(prepared.body)['messages'][1]['content'])['context']
            model_contexts.append(copy.deepcopy(context))
            on_attempt()
            return module.ModelResult('# 测试接口夹具\n非真实模型输出 [M3]')
        with patch.object(module, 'call_qwen', fixture_model):
            response = self.client.post('/api/generate', json={'student_id': 2, 'topic': 'linear', 'goal': ''})
        self.assertEqual(response.status_code, 200)
        generated = response.get_json()
        self.assertEqual(next(s for s in model_contexts[0]['states'] if s['id'] == 3)['state'], 'partial')
        self.assertEqual(self.knowledge()['revision'], 1)  # Generating never updates a state.
        self.save_evidence(self.payload(expected_revision=1, new_state='mastered'))
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}').get_json(), original)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}/download').data, download)
        saved = self.client.get(f'/api/lectures/{generated["id"]}').get_json()
        self.assertEqual(saved['context'], generated['context'])
        self.assertEqual(next(s for s in saved['context']['states'] if s['id'] == 3)['state'], 'partial')

    def test_cross_origin_write_is_rejected(self):
        original = self.profile()
        for endpoint in ['evidence', 'profile']:
            response = self.client.post(f'/api/students/2/{endpoint}', json=self.payload(),
                                        headers={'Origin': 'https://example.invalid'})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(original, self.profile())

    def test_maximum_chinese_payload_and_request_size_error(self):
        self.assertEqual(self.save_evidence(self.payload(task_content='测'*1200,
            performance='记'*2000, reason='因'*600, recorder='师'*80)).status_code, 201)
        response = self.client.post('/api/students/2/evidence', data='x'*131073, content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertIn('提交内容过长', response.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
