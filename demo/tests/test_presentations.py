"""PPT validation, native editability, version isolation and queued recovery."""
import copy
from io import BytesIO
import json
from pathlib import Path
import posixpath
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from test_profiles import ProfileDatabaseFixture, module
from jobs import JobQueue
from presentations import MIME, fingerprint, parse_deck, prepare_slides
from pptx_export import A, P, build_pptx
from quality import REVIEW_ITEMS
from pptkit_export import build_pptx as build_kit_pptx, EXPORT_VERSION, check as check_layout
from presentations import build_pptx as build_versioned_pptx
from pptx import Presentation
from ppt_templates import PROFILES, template_record, MAPPING_VERSION
from ppt_story import DESIGN_VERSION, fallback_visual


def fixture_deck(count=6):
    """Explicit fixed test response, not an actual model-generated lesson."""
    slides = []
    titles = ['等式与方程', '等式的性质', '方程例题', '课堂练习', '答案讨论', '本课总结']
    for i in range(count):
        slides.append(dict(kind='cover' if i == 0 else 'summary' if i == count-1 else 'question' if i == 3 else 'example',
            title=titles[i % len(titles)], bullets=['等式两边同时减去相同的数，等式仍然成立。'],
            question='' if i == 0 else '为什么要在等式两边进行相同运算？',
            notes='这是明确的隔离测试讲稿，不是真实模型输出。请讲解等式的性质，说明两边同时进行相同运算的理由。提问后先让学生思考，再解释预期回答：保持相等关系。',
            sources=['M3']))
    for slide in slides:
        slide['visual'] = fallback_visual(slide)
    return dict(title='隔离测试：等式与方程', slides=slides)


def fixture_context():
    return dict(materials=[dict(id=3, title='等式性质', content='两边同减相同数。',
                source_title='测试来源', source_url='https://example.invalid/equality', section='1')],
                ppt_export=dict(lecture_id=1, base_revision=0, base_content='x + 3 = 5',
                    content_sha256=fingerprint('x + 3 = 5'), slide_count=6, goal='理解等式',
                    source_status='原稿未审核', instruction=''))


class PresentationUnitTests(unittest.TestCase):
    def test_kit_preserves_maximum_classroom_content_notes_and_sources(self):
        ctx = fixture_context()
        ctx['ppt_export']['slide_count'] = 20
        ctx['visual_evidence'] = [dict(citation_id='V1', label='公式图片', content='识读结果待核对',
                                      model='fixture-vlm', normalized_sha256='a'*64)]
        deck = fixture_deck(20)
        for i, slide in enumerate(deck['slides']):
            slide['kind'] = 'cover' if i == 0 else 'summary' if i == 19 else ['concept', 'example', 'question'][i % 3]
            slide['title'] = '长标题' * 11 + '题'
            slide['bullets'] = ['已知条件' * 16, '关键步骤' * 4] if i == 0 else ['条件' * 32, '步骤' * 32, '结论' * 25, '核对']
            slide['question'] = '' if i == 0 else '为什么' * 23 + '？'
            slide['notes'] = '完整解答仅供教师查看。' + '讲稿' * 780
            slide['sources'] = ['M3', 'V1']
        deck = parse_deck(json.dumps(deck, ensure_ascii=False), ctx)
        payload = build_kit_pptx(deck, ctx['ppt_export'], ctx)
        self.assertEqual([issue for issue in check_layout(BytesIO(payload)) if issue[0] != 'ok'], [])
        prs = Presentation(BytesIO(payload))
        self.assertEqual(len(prs.slides), 20)
        self.assertEqual(prs.slide_width, 1280 * 12700)
        for i, (slide, content) in enumerate(zip(prs.slides, deck['slides'])):
            visible = '\n'.join(sh.text for sh in slide.shapes if sh.has_text_frame)
            self.assertIn(content['title'], visible)
            for bullet in content['bullets']:
                self.assertIn(bullet, visible)
            self.assertIn(content['question'], visible)
            self.assertNotIn('完整解答仅供教师查看', visible)
            notes = slide.notes_slide.notes_text_frame.text
            for expected in (content['notes'], 'fixture-vlm', 'a'*64, '待核对',
                             ctx['ppt_export']['content_sha256'], 'https://example.invalid/equality'):
                self.assertIn(expected, notes)

    def test_export_version_routes_legacy_and_kit_and_rejects_unknown(self):
        ctx = fixture_context()
        legacy = Presentation(BytesIO(build_versioned_pptx(fixture_deck(), ctx['ppt_export'], ctx)))
        self.assertEqual(legacy.slide_width, 12192000)
        ctx['ppt_export']['export_version'] = EXPORT_VERSION
        kit = Presentation(BytesIO(build_versioned_pptx(fixture_deck(), ctx['ppt_export'], ctx)))
        self.assertEqual(kit.slide_width, 1280 * 12700)
        ctx['ppt_export']['export_version'] = 'unknown'
        with self.assertRaises(module.ReviewError):
            build_versioned_pptx(fixture_deck(), ctx['ppt_export'], ctx)

    def test_kit_geometry_errors_reject_export_before_success(self):
        ctx = fixture_context()
        with patch('pptkit_export.check', return_value=[('error', '测试排版越界')]):
            with self.assertRaises(module.ModelError) as caught:
                build_kit_pptx(fixture_deck(), ctx['ppt_export'], ctx)
        self.assertEqual(caught.exception.code, 'invalid_ppt')

    def test_all_templates_preserve_native_masters_and_sample_content_is_removed(self):
        ctx = fixture_context()
        deck = fixture_deck()
        for identity in PROFILES:
            with self.subTest(template=identity):
                record = template_record(identity)
                source = {**ctx['ppt_export'], 'template_id': identity, 'template_sha256': record['sha256'],
                          'template_mapping_version': MAPPING_VERSION}
                payload = build_kit_pptx(deck, source, ctx)
                prs = Presentation(BytesIO(payload))
                original = Presentation(record['path'])
                self.assertEqual(prs.slide_width, original.slide_width)
                self.assertEqual(prs.slide_height, original.slide_height)
                self.assertEqual(len(prs.slide_masters), len(original.slide_masters))
                self.assertEqual(len(prs.slides), 6)
                visible = '\n'.join(s.text for page in prs.slides for s in page.shapes if s.has_text_frame)
                self.assertNotIn('Lorem ipsum', visible)
                self.assertIn('等式的性质', visible)
                self.assertIn(record['sha256'], prs.slides[1].notes_slide.notes_text_frame.text)
                source['template_sha256'] = 'tampered'
                with self.assertRaises(module.ReviewError):
                    build_kit_pptx(deck, source, ctx)

    def test_bad_json_wrong_count_missing_notes_and_overflow_are_rejected(self):
        cases = ['不是 JSON', '[]']
        for key, value in [('notes', ''), ('bullets', ['很长'*80]), ('title', '标题'*40),
                           ('bullets', ['一\n'*30]), ('question', None)]:
            bad = fixture_deck(); bad['slides'][1][key] = value; cases.append(json.dumps(bad))
        bad = fixture_deck(); bad['slides'].pop(); cases.append(json.dumps(bad))
        bad = fixture_deck(); bad['slides'][0]['question'] = '封面不放提问'; cases.append(json.dumps(bad))
        bad = fixture_deck(); bad['slides'][-1]['kind'] = 'concept'; cases.append(json.dumps(bad))
        for content in cases:
            with self.subTest(content=content[:60]), self.assertRaises(module.ModelError):
                parse_deck(content, fixture_context())

    def test_unknown_citations_in_sources_or_notes_are_rejected(self):
        for key, value in [('sources', ['M999']), ('sources', [False]), ('notes', '完整测试说明。'*12+' [V999]')]:
            bad = fixture_deck(); bad['slides'][2][key] = value
            with self.subTest(key=key), self.assertRaises(module.ModelError):
                parse_deck(json.dumps(bad), fixture_context())

    def test_input_does_not_send_individual_profile_and_is_budgeted(self):
        ctx = fixture_context(); ctx['student'] = {'name':'不应发送的姓名'}; ctx['states'] = ['不应发送的诊断']
        prepared = prepare_slides(ctx, 'fixture-model')
        self.assertNotIn('不应发送', prepared.body)
        self.assertEqual(prepared.sha256, fingerprint(prepared.body))
        ctx['materials'][0]['content'] = '长'*60000
        with self.assertRaises(module.ReviewError):
            prepare_slides(ctx, 'fixture-model')

    def test_pptx_has_editable_text_notes_correct_relationships_and_no_macros(self):
        ctx = fixture_context(); deck = fixture_deck()
        deck['slides'][1]['bullets'] = ['P(A|B) = P(A∩B) / P(B)，P(B)>0', '<script>纯文本 & 不执行</script>']
        deck['slides'][2]['notes'] += '计算得到 x = 2。'
        payload = build_pptx(deck, ctx['ppt_export'], ctx)
        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            self.assertEqual(len([n for n in names if re_slide(n)]), 6)
            self.assertFalse(any(n.endswith(('.png', '.bin')) for n in names))
            for name in names:
                root = ET.fromstring(archive.read(name))
                if name.endswith('.rels'):
                    base = posixpath.dirname(posixpath.dirname(name))
                    for rel in root:
                        target = posixpath.normpath(posixpath.join(base, rel.attrib['Target']))
                        self.assertIn(target, names)
            slide = ET.fromstring(archive.read('ppt/slides/slide2.xml'))
            text = ''.join(slide.itertext())
            self.assertIn('P(A|B)', text); self.assertIn('<script>纯文本 & 不执行</script>', text)
            notes = ''.join(ET.fromstring(archive.read('ppt/notesSlides/notesSlide3.xml')).itertext())
            for expected in ('x = 2', '原稿未审核', ctx['ppt_export']['content_sha256'], 'https://example.invalid/equality'):
                self.assertIn(expected, notes)
            self.assertNotIn('x = 2', ''.join(ET.fromstring(archive.read('ppt/slides/slide3.xml')).itertext()))

    def test_visual_sources_keep_model_fingerprint_and_recognition_uncertainty(self):
        ctx = fixture_context()
        ctx['visual_evidence'] = [dict(citation_id='V1', label='概率树', content='图中文字待核对', model='fixture-vlm', normalized_sha256='f'*64)]
        deck = fixture_deck(); deck['slides'][1]['sources'] = ['V1']
        parsed = parse_deck(json.dumps(deck), ctx)
        with ZipFile(BytesIO(build_pptx(parsed, ctx['ppt_export'], ctx))) as archive:
            notes = ''.join(ET.fromstring(archive.read('ppt/notesSlides/notesSlide2.xml')).itertext())
            for text in ('图中文字待核对', 'fixture-vlm', 'f'*64): self.assertIn(text, notes)


def re_slide(name):
    return name.startswith('ppt/slides/slide') and name.endswith('.xml')


class PresentationIntegrationTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        self.queue = JobQueue(lambda: module.db(), module.execute_job)
        self.queue_patch = patch.object(module, 'TASKS', self.queue)
        self.queue_patch.start()

    def tearDown(self):
        self.queue.wait_idle(10); self.queue.close(); self.queue_patch.stop()

    def submit(self, reply=None, **changes):
        def model(prepared, attempted):
            self.assertTrue(module.GENERATION_LOCK.locked())
            attempted()
            return module.ModelResult(json.dumps(reply or fixture_deck(), ensure_ascii=False), {'total_tokens': 15})
        with patch.object(module, 'call_qwen', model):
            response = self.client.post(f'/api/lectures/{self.lecture_id}/presentations',
                json=dict(base_revision=0, slide_count=6, **changes))
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.queue.wait_idle(10))
        return self.client.get('/api/jobs/' + response.json['id']).json

    def test_generation_preview_download_preserve_original_without_creating_lecture(self):
        job = self.submit()
        self.assertEqual(job['status'], 'succeeded', job)
        self.assertTrue(job['ppt_generation'])
        detail = self.client.get('/api/presentations/' + str(job['run_id']))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(len(detail.json['deck']['slides']), 6)
        self.assertNotIn('base_content', detail.json['source'])
        self.assertEqual(detail.json['source']['export_version'], DESIGN_VERSION)
        original = self.client.get(f'/api/lectures/{self.lecture_id}').json
        self.assertEqual(original['content'], '# 隔离测试讲义\n历史原稿 [M3]')
        self.assertIsNone(original['generation_run_id'])
        with self.fixture_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM lectures').fetchone()['n'], 1)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM lecture_revisions').fetchone()['n'], 0)
        download = self.client.get(f'/api/presentations/{job["run_id"]}/download')
        self.assertEqual(download.mimetype, MIME)
        self.assertIn('.pptx', download.headers['Content-Disposition'])
        with ZipFile(BytesIO(download.data)) as archive:
            self.assertIn('ppt/notesSlides/notesSlide1.xml', archive.namelist())
        self.assertEqual(Presentation(BytesIO(download.data)).slide_width, template_record('teaching-blue')['width'])
        # Pre-integration records lack the frozen renderer field.
        with self.fixture_db() as conn:
            conn.execute("UPDATE generation_runs SET context=context #- '{ppt_export,export_version}' WHERE id=%s",
                         (job['run_id'],))
        legacy = self.client.get(f'/api/presentations/{job["run_id"]}/download')
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(Presentation(BytesIO(legacy.data)).slide_width, 12192000)

    def test_multiple_decks_and_schema_migration_are_idempotent(self):
        first, second = self.submit(), self.submit()
        self.assertEqual([first['status'], second['status']], ['succeeded', 'succeeded'])
        with self.fixture_db() as conn:
            for _ in range(2): conn.execute((Path(__file__).resolve().parents[1] / 'schema.sql').read_text(encoding='utf-8'))
        rows = self.client.get(f'/api/lectures/{self.lecture_id}/presentations').json['presentations']
        self.assertEqual(len(rows), 2); self.assertNotIn('base_content', rows[0]['source'])

    def test_notebook_selection_queues_semantic_output_and_freezes_new_mapping(self):
        job = self.submit(template_id='notebook')
        self.assertEqual(job['status'], 'succeeded', job)
        detail = self.client.get(f'/api/presentations/{job["run_id"]}').json
        self.assertEqual(detail['source']['export_version'], DESIGN_VERSION)
        self.assertEqual(detail['source']['template_mapping_version'], 'semantic-notebook-v1')
        self.assertIn('visual', detail['deck']['slides'][1])

    def test_remaining_styles_queue_v3_and_serve_new_previews(self):
        for identity in PROFILES:
            if identity == 'notebook':
                continue
            with self.subTest(identity=identity):
                job = self.submit(template_id=identity)
                self.assertEqual(job['status'], 'succeeded', job)
                detail = self.client.get(f'/api/presentations/{job["run_id"]}').json
                self.assertEqual(detail['source']['export_version'], DESIGN_VERSION)
                self.assertEqual(detail['source']['template_mapping_version'], f'semantic-{identity}-v1')
                self.assertIn('visual', detail['deck']['slides'][1])
                preview = self.client.get(f'/api/presentation-templates/{identity}/preview')
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.mimetype, 'image/png')
                preview.close()

    def test_selected_revision_is_frozen_and_does_not_inherit_ppt_review(self):
        bundle = self.client.get(f'/api/lectures/{self.lecture_id}/review').json
        data = dict(expected_revision=bundle['expected_revision'], base_revision=0,
            content='# 专有修订稿\n两边同减三。 [M3]', status='draft', recorder='测试教师', reason='课件版本测试',
            review_seconds=0, checklist={k:False for k in REVIEW_ITEMS}, resolutions=[], confirmed=True)
        self.assertEqual(self.client.post(f'/api/lectures/{self.lecture_id}/revisions', json=data).status_code, 201)
        # Submit explicitly rather than dict duplicate keyword arguments.
        with patch.object(module, 'call_qwen', lambda prepared, attempted: (attempted(), module.ModelResult(json.dumps(fixture_deck())))[1]):
            response = self.client.post(f'/api/lectures/{self.lecture_id}/presentations', json=dict(base_revision=1, slide_count=6))
            self.assertEqual(response.status_code, 202); self.assertTrue(self.queue.wait_idle(10))
        run = self.client.get('/api/generation-runs/' + str(response.json['run_id'])).json
        self.assertEqual(run['context']['ppt_export']['base_content'], data['content'])
        self.assertEqual(run['context']['ppt_export']['source_status'], '草稿')
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(run['context']['ppt_export']['content_sha256'], fingerprint(data['content']))

    def test_invalid_model_result_keeps_failed_input_and_can_retry_exact_bytes(self):
        bad = fixture_deck(); bad['slides'][1]['sources'] = ['M999']
        failed = self.submit(bad)
        self.assertEqual(failed['status'], 'failed')
        first = self.client.get('/api/generation-runs/' + str(failed['run_id'])).json
        self.assertEqual(first['error_code'], 'invalid_ppt')
        self.assertIn('M999', first['content'])
        self.assertEqual(self.client.get('/api/presentations/' + str(failed['run_id'])).status_code, 409)
        def model(prepared, attempted):
            self.assertEqual(prepared.body, first['request_body']); attempted()
            return module.ModelResult(json.dumps(fixture_deck()))
        with patch.object(module, 'call_qwen', model):
            retry = self.client.post(f'/api/generation-runs/{failed["run_id"]}/retry')
            self.assertEqual(retry.status_code, 202); self.assertTrue(self.queue.wait_idle(10))
        self.assertEqual(self.client.get('/api/jobs/' + retry.json['id']).json['status'], 'succeeded')

    def test_invalid_parameters_missing_version_and_record_are_rejected(self):
        for data in ([], {}, {'base_revision':True}, {'base_revision':0,'slide_count':21},
                     {'base_revision':0,'instruction':['bad']}, {'base_revision':0,'slide_count':False},
                     {'base_revision':0,'template_id':'../../other'}, {'base_revision':0,'template_id':False}):
            self.assertEqual(self.client.post(f'/api/lectures/{self.lecture_id}/presentations', json=data).status_code, 400)
        self.assertEqual(self.client.post(f'/api/lectures/{self.lecture_id}/presentations', json={'base_revision':99}).status_code, 404)
        self.assertEqual(self.client.get('/api/presentations/99999/download').status_code, 404)

    def test_template_catalog_preview_and_selection_are_persisted(self):
        catalog = self.client.get('/api/presentation-templates')
        self.assertEqual(len(catalog.json['templates']), 9)
        self.assertEqual(catalog.json['default'], 'teaching-blue')
        preview = self.client.get(catalog.json['templates'][0]['preview_url'])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, 'image/png')
        preview.close()
        job = self.submit(template_id='cinema')
        detail = self.client.get(f'/api/presentations/{job["run_id"]}')
        self.assertEqual(detail.json['source']['template_id'], 'cinema')
        download = self.client.get(f'/api/presentations/{job["run_id"]}/download')
        self.assertEqual(Presentation(BytesIO(download.data)).slide_width, template_record('cinema')['width'])

    def test_restart_marks_queued_ppt_interrupted_and_does_not_resend(self):
        stopped = JobQueue(lambda: module.db(), module.execute_job)
        # Hold the generation lock until the persisted task is visible.
        module.GENERATION_LOCK.acquire()
        try:
            response = self.client.post(f'/api/lectures/{self.lecture_id}/presentations', json=dict(base_revision=0, slide_count=6))
            self.assertEqual(response.status_code, 202)
            stopped.recover()
        finally:
            module.GENERATION_LOCK.release()
        self.assertTrue(self.queue.wait_idle(10))
        job = self.client.get('/api/jobs/' + response.json['id']).json
        self.assertEqual(job['status'], 'interrupted')
        self.assertEqual(self.client.get('/api/generation-runs/' + str(job['run_id'])).json['error_code'], 'process_interrupted')
