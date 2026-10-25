"""Merged download formats must preserve the selected version and its provenance."""
from io import BytesIO

from docx import Document

from test_profiles import ProfileDatabaseFixture
from quality import REVIEW_ITEMS
from rendering import markdown_html


class ExportTests(ProfileDatabaseFixture):
    def download(self, fmt, revision=None):
        path = f'/api/lectures/{self.lecture_id}'
        if revision is not None:
            path += f'/revisions/{revision}'
        return self.client.get(path + '/download', query_string={'format': fmt})

    def text(self, response, fmt):
        if fmt == 'docx':
            return '\n'.join(p.text for p in Document(BytesIO(response.data)).paragraphs)
        return response.get_data(as_text=True)

    def save_draft(self, content):
        bundle = self.client.get(f'/api/lectures/{self.lecture_id}/review').get_json()
        response = self.client.post(f'/api/lectures/{self.lecture_id}/revisions', json={
            'expected_revision': bundle['expected_revision'], 'base_revision': 0,
            'content': content, 'status': 'draft', 'recorder': '测试教师',
            'reason': '核查下载版本', 'review_seconds': 60,
            'checklist': {k: False for k in REVIEW_ITEMS},
            'resolutions': [], 'confirmed': True})
        self.assertEqual(response.status_code, 201)

    def test_original_all_formats_keep_content_status_and_sources(self):
        types = {'md': 'text/markdown', 'txt': 'text/plain', 'html': 'text/html',
                 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
        for fmt, mime in types.items():
            with self.subTest(fmt=fmt):
                response = self.download(fmt)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, mime)
                self.assertIn('.' + fmt, response.headers['Content-Disposition'])
                text = self.text(response, fmt)
                for part in ('历史原稿', '原稿未审核', '[M3]',
                             self.before_context['materials'][0]['source_url']):
                    self.assertIn(part, text)

    def test_revision_all_formats_keep_selected_content_and_review_record(self):
        self.save_draft('# 修订专有正文\n明确等式变形 [M3]')
        for fmt in ('md', 'txt', 'html', 'docx'):
            with self.subTest(fmt=fmt):
                response = self.download(fmt, 1)
                self.assertEqual(response.status_code, 200)
                text = self.text(response, fmt)
                for part in ('R1', '修订专有正文', '保存时审核状态：草稿',
                             '测试教师', '核查下载版本', '60 秒',
                             self.before_context['materials'][0]['source_url']):
                    self.assertIn(part, text)
                self.assertNotIn('历史原稿', text)
                self.assertIn('-R1.' + fmt, response.headers['Content-Disposition'])

    def test_metadata_survives_when_body_repeats_a_status_word(self):
        self.save_draft('草稿')
        for fmt in ('txt', 'html', 'docx'):
            with self.subTest(fmt=fmt):
                text = self.text(self.download(fmt, 1), fmt)
                self.assertIn('记录者：测试教师', text)
                self.assertIn('60 秒', text)

    def test_html_escapes_model_markup_and_revision_metadata(self):
        self.save_draft('# 修订\n<script>alert(1)</script> [M3]')
        text = self.download('html', 1).get_data(as_text=True)
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)

    def test_invalid_format_missing_version_and_revision_zero(self):
        self.assertEqual(self.download('pdf').status_code, 400)
        self.assertEqual(self.download('docx', 99).status_code, 404)
        for fmt in ('md', 'txt', 'html', 'docx'):
            with self.subTest(fmt=fmt):
                response = self.download(fmt, 0)
                self.assertEqual(response.status_code, 200)
                self.assertIn('原稿未审核', self.text(response, fmt))

    def test_visual_evidence_text_and_fingerprint_survive_every_export_format(self):
        from psycopg.types.json import Jsonb
        ctx = {**self.before_context, 'visual_evidence': [dict(citation_id='V1', label='条件概率图',
               content='解析文字：P(A|B) 的分母为 P(B)>0；待核对。',model='fixture-vision',normalized_sha256='f'*64)]}
        with self.fixture_db() as conn:
            conn.execute('UPDATE lectures SET context=%s,content=%s WHERE id=%s',
                         (Jsonb(ctx),'# 图片讲义\n分析 [V1]',self.lecture_id))
        for fmt in ('md','txt','html','docx'):
            text=self.text(self.download(fmt),fmt)
            for part in ('[V1]','解析文字','fixture-vision','f'*64):self.assertIn(part,text)

    def test_html_keeps_formula_blocks_and_ordered_list_numbers_without_executing_markup(self):
        text=markdown_html('```\nP(A|B)=P(A∩B)/P(B)\n<script>not executed</script>\n```\n7. 第七步\n8. 第八步')
        self.assertIn('<pre class="formula-block">',text)
        self.assertIn('&lt;script&gt;',text)
        self.assertNotIn('<script>',text)
        self.assertIn('<li value="7">',text)
        self.assertIn('<li value="8">',text)
