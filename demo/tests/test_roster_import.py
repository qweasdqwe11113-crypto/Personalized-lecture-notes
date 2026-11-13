"""Batch roster and evidence imports in a disposable schema: preview, confirm, reject on any doubt."""
from datetime import datetime, timedelta
from io import BytesIO

from test_profiles import ProfileDatabaseFixture
from classes import ensure_course_structure

HEADER_E = '学生姓名,知识点,证据来源,任务或观察内容,学生表现,观察日期,判断,新掌握状态,确认理由'


class RosterImportTests(ProfileDatabaseFixture):
    def send(self, path, kind, text, encoding='utf-8', **fields):
        data = {'kind': kind, 'file': (BytesIO(text.encode(encoding)), 'import.csv'), **fields}
        return self.client.post(path, data=data, content_type='multipart/form-data')

    def preview(self, kind, text, **fields):
        return self.send('/api/students/import/preview', kind, text, **fields)

    def confirm(self, kind, text, token, **fields):
        return self.send('/api/students/import', kind, text, token=token, confirmed='true', **fields)

    def students(self):
        return [s['name'] for s in self.client.get('/api/students').json['students']]

    def evidence_count(self):
        with self.fixture_db() as conn:
            return conn.execute('SELECT count(*) AS n FROM learning_evidence').fetchone()['n']

    def test_templates_open_in_excel_and_reject_unknown_kinds(self):
        for kind, first in (('students', '姓名'), ('evidence', '学生姓名')):
            response = self.client.get(f'/api/students/import/template?kind={kind}')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data.startswith('﻿'.encode()))
            self.assertTrue(response.data.decode('utf-8-sig').startswith(first))
        self.assertEqual(self.client.get('/api/students/import/template?kind=x').status_code, 400)

    def test_student_roster_preview_then_confirm_creates_only_new_names(self):
        text = '姓名,年级或说明,学习特点\n小王,初二 · 方程,喜欢先用数字检验。\n小林,初一,已存在的同名学生\n小赵,初二 · 巩固,计算稳定但解释跳步。\n'
        # Excel in Chinese locales often saves GBK; both encodings decode to the same rows.
        preview = self.preview('students', text, encoding='gb18030').json
        self.assertEqual((preview['summary']['ready'], preview['summary']['skipped'], preview['summary']['errors']), (2, 1, 0))
        self.assertEqual([r['action'] for r in preview['rows']], ['create', 'skip', 'create'])
        response = self.confirm('students', text, preview['token'])
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(len(response.json['created_ids']), 2)
        self.assertEqual(self.students(), ['小林', '小周', '小陈', '小王', '小赵'])

    def test_student_rows_with_errors_block_the_whole_batch(self):
        text = '姓名,年级或说明,学习特点\n小王,初二,特点\n小王,初二,重复\n,初二,缺姓名\n'
        preview = self.preview('students', text).json
        self.assertEqual(preview['summary']['errors'], 2)
        self.assertIn('文件内姓名重复', preview['rows'][1]['errors'])
        self.assertEqual(self.confirm('students', text, preview['token']).status_code, 400)
        self.assertEqual(self.students(), ['小林', '小周', '小陈'])
        for text, message in (('姓名,年级或说明\n小王,初二\n', '缺少列'), ('姓名,年级或说明,学习特点\n', '只有表头'), ('', '请选择 CSV 文件'), ('\n\n', '没有内容')):
            response = self.preview('students', text)
            self.assertEqual(response.status_code, 400)
            self.assertIn(message, response.json['error'])
        self.assertEqual(self.client.post('/api/students/import/preview', data={'kind': 'students'}).status_code, 400)

    def test_evidence_rows_apply_in_order_through_teacher_confirmed_records(self):
        before = self.knowledge()
        text = (HEADER_E + '\n'
                '小周,两步方程与移项,作业,作业第2题,能列式但移项漏变号,2026-10-20,更新状态,需要补充,两次作业均出现同类错误\n'
                '小周,两步方程与移项,课堂观察,课堂追问,能说出移项依据,2026/10/22,保留原判断,,单次表现，暂不改判断\n'
                '小林,等式的乘除性质,测验,测验第5题,能说明两边同除的理由,2026-10-21 15:30,更新,部分掌握,解释完整\n')
        preview = self.preview('evidence', text, topic='linear').json
        self.assertEqual(preview['summary']['ready'], 3, preview['rows'])
        first, second = preview['rows'][0], preview['rows'][1]
        self.assertEqual((first['expected_revision'], second['expected_revision']), (before['revision'], before['revision'] + 1))
        self.assertEqual((second['previous_label'], second['confirmed_label']), ('需要补充', '需要补充'))
        self.assertEqual(self.confirm('evidence', text, preview['token'], topic='linear').status_code, 400)  # recorder required
        response = self.confirm('evidence', text, preview['token'], topic='linear', recorder='测试教师')
        self.assertEqual(response.status_code, 201, response.json)
        after = self.knowledge()
        self.assertEqual((after['state'], after['revision']), ('needs_support', before['revision'] + 2))
        history = self.profile()['evidence_history']
        # Newest first: the retained classroom observation follows the homework update.
        self.assertEqual([(h['decision'], h['recorder'], h['observed_at'][:10]) for h in history[:2]],
                         [('retain', '测试教师', '2026-10-22'), ('update', '测试教师', '2026-10-20')])
        lin = next(s for s in self.profile(1)['states'] if s['id'] == 2)
        self.assertEqual(lin['state'], 'partial')

    def test_evidence_names_any_point_of_the_course_library_without_a_lesson(self):
        with self.fixture_db() as conn:
            ensure_course_structure(conn)
            course = conn.execute("SELECT id FROM courses WHERE preset_key='junior-math'").fetchone()['id']
            klass = conn.execute('SELECT id FROM classes WHERE course_id=%s', (course,)).fetchone()['id']
            # A confirmed library point that no lesson ticks, and an archived one.
            conn.execute('''INSERT INTO knowledge_points(id,name,course_id,chapter,position,archived)
                VALUES (50,'泊松分布（Poisson）',%s,'第二章',50,false),(51,'旧的知识点',%s,'',51,true)''', (course, course))
        rows = ['小周,泊松分布 (poisson),作业,作业第1题,会用公式但参数取错,2026-10-20,更新状态,部分掌握,参数含义不清',
                '小周,等式的加减性质,测验,测验第2题,依据完整,2026-10-21,保留原判断,,单次表现',
                '小周,旧的知识点,作业,任务,表现,2026-10-20,更新状态,已掌握,理由',
                '小周,泊松分步,作业,任务,表现,2026-10-20,更新状态,已掌握,理由',
                '小周,完全无关,作业,任务,表现,2026-10-20,更新状态,已掌握,理由']
        preview = self.preview('evidence', HEADER_E + '\n' + '\n'.join(rows) + '\n', class_id=str(klass)).json
        found = preview['rows']
        self.assertEqual([r['action'] for r in found], ['record', 'record', 'error', 'error', 'error'], found)
        self.assertEqual(found[0]['knowledge_id'], 50)  # full- / half-width brackets, spaces and case do not matter
        self.assertIn('已归档', found[2]['errors'][0])
        self.assertIn('是否指「泊松分布（Poisson）」', found[3]['errors'][0])
        self.assertIn('知识点库', found[4]['errors'][0])
        # Without a class, the course's students and library are used.
        good = HEADER_E + '\n' + '\n'.join(rows[:2]) + '\n'
        self.assertEqual(self.preview('evidence', good, course_id=str(course)).json['summary']['ready'], 2)
        preview = self.preview('evidence', good, class_id=str(klass)).json
        # A diagnosis this file does not touch, recorded after the preview, does not void it.
        self.assertEqual(self.save_evidence(sid=1).status_code, 201)
        response = self.confirm('evidence', good, preview['token'], class_id=str(klass), recorder='测试教师')
        self.assertEqual(response.status_code, 201, response.json)
        with self.fixture_db() as conn:
            state = conn.execute('SELECT state FROM student_knowledge WHERE student_id=2 AND knowledge_id=50').fetchone()
        self.assertEqual(state['state'], 'partial')

    def test_invalid_evidence_rows_are_reported_and_nothing_is_written(self):
        with self.fixture_db() as conn:
            conn.execute("INSERT INTO students VALUES (4,'小赵','外校','不在示例课程名单中')")
        future = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
        rows = ['不存在,等式的加减性质,作业,任务,表现,2026-10-20,更新状态,已掌握,理由',
                '小赵,等式的加减性质,作业,任务,表现,2026-10-20,更新状态,已掌握,理由',
                '小林,不存在的知识点,作业,任务,表现,2026-10-20,更新状态,已掌握,理由',
                '小林,等式的加减性质,考试,任务,表现,2026-10-20,更新状态,已掌握,理由',
                f'小林,等式的加减性质,作业,任务,表现,{future},更新状态,已掌握,理由',
                '小林,等式的加减性质,作业,任务,表现,2026-02-30,更新状态,已掌握,理由',
                '小林,等式的加减性质,作业,任务,表现,2026-10-20,改成掌握,已掌握,理由',
                '小林,等式的加减性质,作业,任务,表现,2026-10-20,更新状态,,理由',
                '小林,等式的加减性质,作业,,表现,2026-10-20,更新状态,已掌握,理由']
        text = HEADER_E + '\n' + '\n'.join(rows) + '\n'
        count = self.evidence_count()
        preview = self.preview('evidence', text, topic='linear').json
        self.assertEqual(preview['summary']['errors'], len(rows))
        self.assertTrue(all(r['errors'] for r in preview['rows']))
        self.assertEqual(self.confirm('evidence', text, preview['token'], topic='linear', recorder='测试').status_code, 400)
        self.assertEqual(self.preview('evidence', text).status_code, 400)  # needs a lesson, course or class
        self.assertEqual(self.evidence_count(), count)

    def test_changes_after_preview_reject_the_batch(self):
        text = HEADER_E + '\n小周,两步方程与移项,作业,作业第2题,漏变号,2026-10-20,更新状态,需要补充,同类错误\n'
        preview = self.preview('evidence', text, topic='linear').json
        self.assertEqual(self.save_evidence().status_code, 201)
        count = self.evidence_count()
        response = self.confirm('evidence', text, preview['token'], topic='linear', recorder='测试教师')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.evidence_count(), count)
        self.assertEqual(self.confirm('evidence', text, 'forged', topic='linear', recorder='测试教师').status_code, 409)
        unconfirmed = self.send('/api/students/import', 'evidence', text, token=preview['token'], topic='linear', recorder='测试教师')
        self.assertEqual(unconfirmed.status_code, 400)
