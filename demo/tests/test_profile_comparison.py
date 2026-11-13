"""Read-only historical/current comparisons in the dedicated DB's temporary schema."""
import copy
from unittest.mock import patch

from psycopg.types.json import Jsonb
from test_profiles import ProfileDatabaseFixture, module


class ProfileComparisonTests(ProfileDatabaseFixture):
    def compare(self, lecture_id=None):
        response = self.client.get(f'/api/lectures/{lecture_id or self.lecture_id}/profile-comparison')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.get_json()

    def point(self, data, knowledge_id=3):
        return next(p for p in data['points'] if p['id'] == knowledge_id)

    def replace_context(self, context):
        with self.fixture_db() as conn:
            conn.execute('UPDATE lectures SET context=%s WHERE id=%s', (Jsonb(context), self.lecture_id))

    def test_unchanged_profile_and_saved_snapshot_are_shown_without_changes(self):
        data = self.compare()
        self.assertEqual(data['historical_student'], self.before_context['student'])
        self.assertEqual(data['profile_changes'], [])
        self.assertEqual(data['summary'], {'changed_points': 0, 'state_changes': 0,
            'evidence_changes': 0, 'strategy_changes': 0, 'incomplete_points': 0})
        for point in data['points']:
            self.assertEqual(point['historical'], point['current'])
            self.assertEqual(point['new_evidence_count'], 0)
            self.assertEqual(point['missing_historical_fields'], [])

    def test_teacher_confirmed_change_compares_state_evidence_and_strategy(self):
        self.assertEqual(self.save_evidence().status_code, 201)
        data = self.compare()
        point = self.point(data)
        self.assertEqual(point['historical']['state'], 'needs_support')
        self.assertEqual(point['current']['state'], 'partial')
        self.assertEqual(point['changes'], ['state', 'evidence', 'strategy'])
        self.assertEqual(point['new_evidence_count'], 1)
        self.assertEqual(point['new_evidence'][0]['reason'], self.payload()['reason'])
        self.assertEqual(data['summary']['changed_points'], 1)
        self.assertNotEqual(point['historical']['strategy'], point['current']['strategy'])

    def test_retained_conflicting_evidence_does_not_invent_a_state_change(self):
        self.assertEqual(self.save_evidence(self.payload(decision='retain', new_state='mastered')).status_code, 201)
        point = self.point(self.compare())
        self.assertTrue(point['changed'])
        self.assertEqual(point['changes'], [])
        self.assertEqual(point['historical']['state'], point['current']['state'])
        self.assertEqual(point['historical']['evidence'], point['current']['evidence'])
        self.assertEqual(point['historical']['strategy'], point['current']['strategy'])
        self.assertEqual(point['new_evidence_count'], 1)
        self.assertEqual(point['new_evidence'][0]['decision'], 'retain')

    def test_new_evidence_count_is_complete_while_details_are_bounded(self):
        for revision in range(5):
            response = self.save_evidence(self.payload(expected_revision=revision, decision='retain'))
            self.assertEqual(response.status_code, 201)
        point = self.point(self.compare())
        self.assertEqual(point['new_evidence_count'], 5)
        self.assertEqual(len(point['new_evidence']), 3)
        self.assertEqual([e['new_revision'] for e in point['new_evidence']], [5, 4, 3])
        self.assertEqual(len(point['current']['recent_evidence']), 3)
        self.assertEqual(point['changes'], [])

    def test_student_description_edit_preserves_original_description(self):
        response = self.client.post('/api/students/2/profile', json={
            'subtitle': '虚拟情景：教师新确认', 'profile': '先检查错误原因，再做迁移。',
            'expected_revision': 0, 'recorder': '隔离测试教师', 'reason': '补充学习特点。', 'confirmed': True,
        })
        self.assertEqual(response.status_code, 200)
        data = self.compare()
        self.assertEqual(data['profile_changes'], ['subtitle', 'profile'])
        self.assertEqual(data['historical_student'], self.before_context['student'])
        self.assertEqual(data['current_student']['profile_revision'], 1)
        self.assertEqual(data['summary']['changed_points'], 0)

    def test_old_missing_versions_strategies_and_details_are_not_backfilled(self):
        context = copy.deepcopy(self.before_context)
        context['student'].pop('profile_revision')
        for state in context['states']:
            for field in ('revision', 'recent_evidence', 'strategy'):
                state.pop(field)
        self.replace_context(context)
        self.save_evidence()
        data = self.compare()
        point = self.point(data)
        self.assertIsNone(point['new_evidence_count'])
        self.assertEqual(point['new_evidence'], [])
        self.assertNotIn('strategy', point['historical'])
        self.assertNotIn('revision', point['historical'])
        self.assertNotIn('strategy', point['changes'])
        self.assertEqual(point['changes'], ['state', 'evidence'])
        self.assertEqual(data['summary']['incomplete_points'], 3)
        self.assertNotIn('profile_revision', data['historical_student'])

    def test_null_legacy_fields_are_unavailable_instead_of_changed(self):
        context = copy.deepcopy(self.before_context)
        state = next(s for s in context['states'] if s['id'] == 3)
        state.update(strategy=None, revision=None, recent_evidence=None)
        self.replace_context(context)
        point = self.point(self.compare())
        self.assertIsNone(point['new_evidence_count'])
        self.assertEqual(point['changes'], [])
        self.assertEqual(set(point['missing_historical_fields']), {'strategy', 'revision', 'recent_evidence'})

    def test_no_profile_mode_cannot_reconstruct_historical_learner_data(self):
        self.replace_context(module.get_context(2, 'linear', 'none'))
        self.save_evidence()
        data = self.compare()
        self.assertEqual(data['personalization_mode'], 'none')
        self.assertIsNone(data['historical_student'])
        self.assertEqual(data['profile_changes'], [])
        self.assertEqual(data['summary']['changed_points'], 0)
        self.assertEqual(data['summary']['incomplete_points'], 0)
        for point in data['points']:
            self.assertIsNone(point['historical'])
            self.assertIsNone(point['new_evidence_count'])
            self.assertFalse(point['changed'])

    def test_scope_uses_saved_topic_even_when_catalog_or_current_points_expand(self):
        context = module.get_context(2, 'basic')
        self.replace_context(context)
        with self.fixture_db() as conn:
            conn.execute("INSERT INTO knowledge_points VALUES (99,'另一门课程知识点')")
            conn.execute("INSERT INTO student_knowledge (student_id,knowledge_id,state,evidence) VALUES (2,99,'mastered','其他课程依据')")
        changed_topic = {**module.TOPICS['basic'], 'knowledge_ids': [1, 2, 3, 99]}
        with patch.dict(module.TOPICS, {'basic': changed_topic}):
            data = self.compare()
        self.assertEqual([p['id'] for p in data['points']], [1, 2])

    def test_comparison_never_writes_or_calls_qwen_and_refresh_reads_new_state(self):
        original = self.client.get(f'/api/lectures/{self.lecture_id}').get_json()
        download = self.client.get(f'/api/lectures/{self.lecture_id}/download').data
        profile = self.profile()
        with patch.object(module, 'call_qwen', side_effect=AssertionError('Comparison must not call Qwen')):
            self.compare()
            self.assertEqual(self.profile(), profile)
            self.save_evidence()
            updated = self.compare()
        self.assertEqual(self.point(updated)['current']['revision'], 1)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}').get_json(), original)
        self.assertEqual(self.client.get(f'/api/lectures/{self.lecture_id}/download').data, download)
        self.assertEqual(self.knowledge()['revision'], 1)

    def test_missing_lecture_returns_a_readable_not_found_response(self):
        response = self.client.get('/api/lectures/999999/profile-comparison')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], '讲义不存在。')
