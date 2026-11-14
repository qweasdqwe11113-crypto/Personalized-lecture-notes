"""The desktop release starts empty and keeps its data outside the program folder."""
import os
from pathlib import Path
from unittest.mock import patch

from psycopg import sql

from test_profiles import ProfileDatabaseFixture, module
import edition
from catalog import load_catalog
from init_db import COUNTED, initialize


class EditionTests(ProfileDatabaseFixture):
    def fresh_schema(self):
        with self.fixture_db() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))

    def test_release_database_has_tables_but_no_demonstration_rows(self):
        self.fresh_schema()
        with patch.dict(os.environ, {'ZHIYE_EDITION': 'release'}), self.fixture_db() as conn:
            counts = initialize(conn, demo=False)
            self.assertEqual(counts, dict.fromkeys(COUNTED, 0))
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM knowledge_points').fetchone()['n'], 0)
            self.assertEqual(load_catalog(conn), {})
            # A second start (an upgrade) applies the schema again and still adds nothing.
            self.assertEqual(initialize(conn, demo=False), dict.fromkeys(COUNTED, 0))
        with patch.dict(os.environ, {'ZHIYE_EDITION': 'release'}):
            self.assertEqual(self.client.get('/api/courses').get_json()['courses'], [])
            self.assertEqual(self.client.get('/api/course-tasks').get_json()['topics'], {})
            self.assertEqual(self.client.get('/api/students').get_json()['students'], [])

    def test_release_database_builds_a_course_from_scratch(self):
        self.fresh_schema()
        with self.fixture_db() as conn:
            initialize(conn, demo=False)
        with patch.dict(os.environ, {'ZHIYE_EDITION': 'release'}):
            course = self.client.post('/api/courses', json={'name': '高等数学', 'stage': '大学'})
            self.assertEqual(course.status_code, 201, course.get_json())
            course_id = course.get_json()['course']['id']
            point = self.client.post(f'/api/courses/{course_id}/knowledge', json={'name': '极限的定义', 'chapter': '第一章'})
            self.assertEqual(point.status_code, 201, point.get_json())
            klass = self.client.post(f'/api/courses/{course_id}/classes', json={'name': '一班'})
            self.assertEqual(klass.status_code, 201, klass.get_json())
            class_id = klass.get_json()['class']['id']
            student = self.client.post(f'/api/classes/{class_id}/students', json={'name': '林晓', 'subtitle': '大一', 'profile': '课堂表现稳定。'})
            self.assertEqual(student.status_code, 201, student.get_json())
            self.assertEqual(student.get_json()['student']['id'], 1)
            self.assertEqual(point.get_json()['point']['id'], 1)

    def test_demo_database_keeps_the_demonstration_content(self):
        self.fresh_schema()
        with self.fixture_db() as conn:
            counts = initialize(conn, demo=True)
            self.assertEqual((counts['courses'], counts['classes'], counts['students'], counts['materials']), (2, 2, 6, 6))
            self.assertIn('linear', load_catalog(conn))

    def test_release_page_hides_the_fixed_experiment(self):
        with patch.object(module, 'release', return_value=True):
            page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('record-tab-experiments', page)
        self.assertNotIn('evaluation.js', page)
        self.assertIn('本机工作空间', page)
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('record-tab-experiments', page)
        self.assertIn('本地演示环境', page)

    def test_release_opens_the_tour_once_per_workspace(self):
        with patch.object(module, 'release', return_value=True):
            self.assertEqual(self.client.get('/api/onboarding').get_json()['tour'], {'seen': False, 'auto': True})
            self.assertIn('id="tour-open"', self.client.get('/').get_data(as_text=True))
            done = self.client.post('/api/onboarding/tour', json={'outcome': 'skipped', 'step': 3})
            self.assertEqual(done.status_code, 200, done.get_json())
            self.assertEqual(self.client.get('/api/onboarding').get_json()['tour'], {'seen': True, 'auto': False})
            # Watching it again from the sidebar records the new outcome and keeps it closed.
            self.assertEqual(self.client.post('/api/onboarding/tour', json={'outcome': 'finished', 'step': 8}).status_code, 200)
        with self.fixture_db() as conn:
            row = conn.execute("SELECT value FROM workspace_settings WHERE key='workspace_tour'").fetchone()
        self.assertEqual(row['value'], {'outcome': 'finished', 'step': 8})

    def test_demo_edition_never_opens_the_tour_by_itself(self):
        self.assertEqual(self.client.get('/api/onboarding').get_json()['tour'], {'seen': False, 'auto': False})
        self.assertIn('id="tour-open"', self.client.get('/').get_data(as_text=True))

    def test_tour_outcome_is_checked(self):
        for body in ({}, {'outcome': 'closed', 'step': 1}, {'outcome': 'finished'}, {'outcome': 'finished', 'step': 0},
                     {'outcome': 'finished', 'step': True}, {'outcome': 'finished', 'step': '2'}):
            self.assertEqual(self.client.post('/api/onboarding/tour', json=body).status_code, 400, body)
        with patch.object(module, 'release', return_value=True):
            self.assertTrue(self.client.get('/api/onboarding').get_json()['tour']['auto'])

    def test_course_and_class_choice_is_kept_by_the_server(self):
        self.assertEqual(self.client.get('/api/workspace-scope').get_json(), {'scope': None})
        choice = {'course': 2, 'classes': {'1': 11, '2': None}}
        saved = self.client.put('/api/workspace-scope', json=choice)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(self.client.get('/api/workspace-scope').get_json(), {'scope': choice})
        # The latest choice replaces the earlier one; a cleared course is allowed.
        self.client.put('/api/workspace-scope', json={'course': None, 'classes': {}})
        self.assertEqual(self.client.get('/api/workspace-scope').get_json(), {'scope': {'course': None, 'classes': {}}})
        for body in ([], {'course': '2', 'classes': {}}, {'course': 0, 'classes': {}}, {'course': True, 'classes': {}},
                     {'course': 1, 'classes': []}, {'course': 1, 'classes': {'x': 1}}, {'course': 1, 'classes': {'01': 1}},
                     {'course': 1, 'classes': {'1': -3}}, {'course': 1, 'classes': {'1': 1.5}},
                     {'course': 1, 'classes': {str(i): i for i in range(1, 502)}}):
            self.assertEqual(self.client.put('/api/workspace-scope', json=body).status_code, 400, body)
        refused = self.client.put('/api/workspace-scope', json=choice, headers={'Origin': 'https://external.invalid'})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.client.get('/api/workspace-scope').get_json()['scope']['course'], None)

    def test_data_folder_follows_the_environment(self):
        with patch.dict(os.environ, {'ZHIYE_DATA_DIR': '/tmp/知页 数据'}):
            self.assertEqual(edition.data_dir(), Path('/tmp/知页 数据'))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ZHIYE_DATA_DIR', None)
            self.assertEqual(edition.data_dir(), Path(module.__file__).resolve().parents[1])
        for value, expected in (('release', True), (' Release ', True), ('demo', False), ('', False)):
            with patch.dict(os.environ, {'ZHIYE_EDITION': value}):
                self.assertIs(edition.release(), expected)
