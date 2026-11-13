"""Blank preparation copies can be saved, reopened and completed later."""
import test_catalog
from test_profiles import ProfileDatabaseFixture


class OptionalPreparationCopyTests(ProfileDatabaseFixture):
    payload = test_catalog.CatalogTests.payload
    def test_blank_copy_can_be_created_reopened_and_completed(self):
        response = self.client.post('/api/course-tasks', json={'preparation_copy': True})
        self.assertEqual(response.status_code, 201, response.json)
        key = response.json['key']
        task = response.json['task']
        for name in ('stage', 'course', 'chapter', 'title', 'goal', 'task_level', 'lesson_plan'):
            self.assertEqual(task[name], '')
        self.assertEqual(task['knowledge_ids'], [])
        self.assertEqual(task['knowledge_names'], [])
        saved = self.client.get('/api/course-tasks').json['topics'][key]
        self.assertEqual(saved['title'], '')
        self.assertEqual(self.client.get(f'/api/course-tasks/{key}/materials').json['materials'], [])
        completed = self.client.put(f'/api/course-tasks/{key}', json=self.payload(
            preparation_copy=True, revision=task['revision']))
        self.assertEqual(completed.status_code, 200, completed.json)
        self.assertEqual(len(completed.json['task']['knowledge_ids']), 2)
        cleared = self.client.put(f'/api/course-tasks/{key}', json=dict(
            preparation_copy=True, revision=completed.json['task']['revision'],
            stage=' ', course='', chapter='', title='', goal='', task_level='',
            knowledge_names=[], lesson_plan=''))
        self.assertEqual(cleared.status_code, 200, cleared.json)
        self.assertEqual(cleared.json['task']['knowledge_ids'], [])

    def test_optional_fields_still_reject_invalid_types_and_limits(self):
        for change in ({'stage': None}, {'goal': '字'*401}, {'knowledge_names': None},
                       {'knowledge_names': ['']}, {'knowledge_names': ['重复', '重复']}):
            with self.subTest(change=change):
                response = self.client.post('/api/course-tasks', json=dict(preparation_copy=True, **change))
                self.assertEqual(response.status_code, 400)
