"""Lesson packages read saved records only; checked in a disposable schema with fixture quizzes."""
from hashlib import sha256
from io import BytesIO
import json
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from psycopg.types.json import Jsonb

from test_quizzes import QuizFixture
from test_profiles import module
import packages
from reviews import REVIEW_ITEMS


def docx_text(data):
    document = Document(BytesIO(data))
    cells = [c.text for t in document.tables for r in t.rows for c in r.cells]
    return '\n'.join([p.text for p in document.paragraphs] + cells)


class PackageTests(QuizFixture):
    def lecture_quiz(self, **changes):
        return self.submit({**dict(lecture_id=self.lecture_id, lecture_revision=0, student_level='profile', count=3,
                                   difficulty='basic', types=['single_choice', 'short_answer'], knowledge_ids=[],
                                   requirements=''), **changes})

    def package(self, query='', status=200):
        response = self.client.get(f'/api/lectures/{self.lecture_id}/package{query}')
        if response.status_code != status:
            self.fail(f'{response.status_code} != {status}: {response.get_data()[:300]!r}')
        return response

    def test_zip_bundles_handbook_worksheet_and_manifest_with_matching_fingerprints(self):
        quiz = self.lecture_quiz()
        self.assertEqual(quiz['status'], 'succeeded', quiz)
        archive = ZipFile(BytesIO(self.package(f"?quiz={quiz['id']}").data))
        names = set(archive.namelist())
        self.assertEqual(names, {'教师授课手册.docx', '教师授课手册.md', '讲义正文-原稿.md', '学生练习.docx',
                                 '练习-教师版.docx', '授课包清单.json'})
        manifest = json.loads(archive.read('授课包清单.json'))
        for item in manifest['files']:
            self.assertEqual(sha256(archive.read(item['name'])).hexdigest(), item['sha256'])
        self.assertEqual(manifest['lecture']['content_sha256'], sha256('# 隔离测试讲义\n历史原稿 [M3]'.encode()).hexdigest())
        self.assertEqual((manifest['quiz']['id'], manifest['quiz']['lecture'], manifest['presentation']), (quiz['id'], self.lecture_id, None))
        handbook = archive.read('教师授课手册.md').decode()
        # Lecture headings nest below the handbook section; quiz answers and learner snapshot are present.
        self.assertIn('### 隔离测试讲义', handbook)
        self.assertIn('**参考答案**：', handbook)
        self.assertIn(self.before_context['student']['name'], handbook)
        self.assertIn('配套本讲义 原稿', handbook)
        for item in REVIEW_ITEMS.values():
            self.assertIn(item, handbook)
        self.assertIn(self.before_context['student']['name'], docx_text(archive.read('教师授课手册.docx')))
        worksheet = docx_text(archive.read('学生练习.docx'))
        self.assertIn('第 1 题', worksheet)
        self.assertNotIn('参考答案', worksheet)
        self.assertNotIn(self.before_context['student']['name'], worksheet)
        self.assertIn('参考答案', docx_text(archive.read('练习-教师版.docx')))

    def test_revision_export_uses_saved_revision_and_its_report(self):
        with self.fixture_db() as conn:
            conn.execute('''INSERT INTO lecture_revisions (lecture_id,revision_number,base_revision,content,status,recorder,
                reason,review_seconds,checklist,resolutions,changes,quality_report) VALUES (%s,1,0,%s,'draft','测试教师',
                '补充依据',0,%s,'[]','{}',%s)''', (self.lecture_id, '# 教师修订\n## 核心讲解\n区分 P(A|B) 与 P(B|A) [M3]',
                Jsonb({k: False for k in REVIEW_ITEMS}), Jsonb({'automatic': [{'label': '修订检查', 'status': 'pass', 'detail': '留存'}],
                                                                'teacher_review': []})))
        archive = ZipFile(BytesIO(self.package('?revision=1').data))
        self.assertIn('讲义正文-R1.md', archive.namelist())
        handbook = archive.read('教师授课手册.md').decode()
        self.assertIn('三、讲义正文（R1）', handbook)
        self.assertIn('修订保存时留存', handbook)
        self.assertIn('修订记录：测试教师', handbook)
        self.assertIn('区分 P(A|B) 与 P(B|A)', docx_text(archive.read('教师授课手册.docx')))
        self.package('?revision=2', 404)

    def test_single_handbook_formats_and_package_options(self):
        linked = self.lecture_quiz()
        general = self.submit({**self.payload(), 'count': 1, 'student_id': None, 'student_level': 'standard', 'types': ['short_answer']})
        other = self.submit({**self.payload(), 'topic': 'basic', 'count': 1, 'student_id': None, 'student_level': 'standard', 'types': ['short_answer']})
        options = self.client.get(f'/api/lectures/{self.lecture_id}/package-options').json
        self.assertEqual({q['id'] for q in options['quizzes']}, {linked['id'], general['id']})
        self.assertEqual(options['presentations'], [])
        self.assertTrue(self.package('?format=md').data.decode().startswith('# '))
        self.assertIn('教师授课手册', docx_text(self.package(f"?format=docx&quiz={general['id']}").data))
        handbook = self.package(f"?format=md&quiz={general['id']}").data.decode()
        self.assertIn('按同一备课副本生成，未关联本讲义', handbook)
        self.package(f"?quiz={other['id']}", 400)
        self.package('?format=pdf', 400)

    def test_invalid_selections_are_rejected(self):
        with self.fixture_db() as conn:
            other = conn.execute('''INSERT INTO lectures (student_id,topic,goal,context,content,model,elapsed_seconds,citation_check)
                VALUES (2,'t','g',%s,'# 另一份讲义','fixture-only',0,'{}') RETURNING id''', (Jsonb(self.before_context),)).fetchone()['id']
        foreign = self.submit({**dict(lecture_id=other, lecture_revision=0, student_level='profile', count=1,
                                      difficulty='basic', types=['short_answer'], knowledge_ids=[], requirements='')})
        self.package(f"?quiz={foreign['id']}", 400)
        broken = self.submit({**self.payload(), 'count': 1}, failure=True)
        self.package(f"?quiz={broken['id']}", 409)
        self.package('?quiz=../../etc', 400)
        self.package(f"?quiz={'0' * 32}", 404)
        self.package('?revision=-1', 400)
        self.package('?presentation=abc', 400)
        self.assertEqual(self.client.get('/api/lectures/999999/package').status_code, 404)

    def test_presentation_bytes_are_bundled_only_for_this_lecture(self):
        def deck(lecture_id):
            run = {'id': 77, 'request_sha256': 'f' * 64, 'context': {'ppt_export': {
                'lecture_id': lecture_id, 'base_revision': 0, 'template_id': 'teaching-blue', 'template_name': '蓝白教学'}}}
            return lambda db, run_id: (run, {'title': 'T', 'slides': [{}] * 6}, b'PPTX-FIXTURE')
        with patch.object(packages, 'presentation_pptx', deck(self.lecture_id)):
            archive = ZipFile(BytesIO(self.package('?presentation=77').data))
            self.assertEqual(archive.read('授课课件.pptx'), b'PPTX-FIXTURE')
            manifest = json.loads(archive.read('授课包清单.json'))
            self.assertEqual(manifest['presentation']['run_id'], 77)
            self.assertIn('#77 · 6 页 · 蓝白教学', archive.read('教师授课手册.md').decode())
        with patch.object(packages, 'presentation_pptx', deck(self.lecture_id + 1)):
            self.package('?presentation=77', 400)

    def test_markdown_docx_keeps_tables_fences_and_page_breaks(self):
        text = '# 标题\n\n| 项 | 值 |\n| --- | --- |\n| 公式 | ' + packages.cell('P(A|B)') + ' |\n\n' + packages.PAGE_BREAK + \
               '\n## 小节\n```\n# 不是标题\nx = 1\n```\n- 要点 **加粗**\n> 提示'
        document = Document(BytesIO(packages.markdown_docx(text)))
        self.assertEqual([c.text for c in document.tables[0].rows[1].cells], ['公式', 'P(A|B)'])
        paragraphs = [p.text for p in document.paragraphs]
        self.assertIn('# 不是标题', paragraphs)
        self.assertIn('要点 加粗', paragraphs)
        self.assertTrue(any('w:br' in p._p.xml and 'type="page"' in p._p.xml for p in document.paragraphs))
        self.assertEqual(packages.demote('# A\n```\n# B\n```\n## C'), '### A\n```\n# B\n```\n#### C')
