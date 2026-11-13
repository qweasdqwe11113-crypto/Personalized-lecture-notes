"""PPT validation, native editability, version isolation and queued recovery."""
import copy
from io import BytesIO
import json
from pathlib import Path
import posixpath
import unittest
from unittest.mock import patch, Mock
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from test_profiles import ProfileDatabaseFixture, module
from jobs import JobQueue
from presentations import MIME, fingerprint, parse_deck, prepare_slides, refine_presentation, deck_fingerprint
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


class PresentationRefinementTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture_context()
        self.context['ppt_export']['aesthetic_review'] = True
        self.deck = parse_deck(json.dumps(fixture_deck()), self.context)
        self.prepared = prepare_slides(self.context, 'fixture')
        self.manager = Mock()

    def report(self, severity=None, identity='a'):
        issue = dict(severity=severity, category='readability', location='body', evidence='crowded', suggestion='shorten')
        return dict(id=identity*32, status='succeeded', results=[dict(page=2,
            verdict='needs_revision' if severity else 'acceptable', issues=[issue] if severity else [])])

    def execute(self, reports, candidate=None):
        self.manager.review_sync.side_effect = reports
        model = Mock(return_value=module.ModelResult(json.dumps(candidate or self.deck)))
        with patch('presentations._aesthetic_reviews', self.manager), patch('presentations.build_pptx', return_value=b'improved'):
            result = refine_presentation(21, self.deck, b'original', self.context, self.prepared, model)
        return result, model

    def test_good_deck_has_no_unnecessary_rewrite(self):
        (deck, payload, audit), model = self.execute([self.report()])
        self.assertEqual(payload, b'original')
        self.assertEqual(audit['status'], 'completed')
        self.assertEqual(audit['repairs'], 0)
        model.assert_not_called()

    def test_repair_accepts_improvement_and_preserves_other_pages_and_notes(self):
        candidate = copy.deepcopy(self.deck)
        candidate['slides'][0]['title'] = 'Unexpected cover change'
        candidate['slides'][1]['bullets'] = ['Shorter visible explanation']
        candidate['slides'][1]['notes'] = 'changed notes ' * 5
        candidate['slides'][1]['sources'] = []
        (deck, payload, audit), model = self.execute([self.report('high'), self.report(identity='b')], candidate)
        self.assertEqual(payload, b'improved')
        self.assertEqual(deck['slides'][0], self.deck['slides'][0])
        self.assertEqual(deck['slides'][1]['notes'], self.deck['slides'][1]['notes'])
        self.assertEqual(deck['slides'][1]['sources'], self.deck['slides'][1]['sources'])
        self.assertEqual(deck['slides'][1]['bullets'], ['Shorter visible explanation'])
        self.assertEqual(audit['accepted'], 1)
        self.assertEqual(audit['status'], 'completed')
        request = model.call_args.args[0]
        self.assertEqual(request.sha256, fingerprint(request.body))
        self.assertIn('visual_feedback', request.body)
        self.assertEqual(self.manager.save_repair.call_count, 2)

    def test_worse_repair_is_discarded(self):
        (_, payload, audit), _ = self.execute([self.report('low'), self.report('high', 'b')])
        self.assertEqual(payload, b'original')
        self.assertEqual(audit['accepted'], 0)
        self.assertEqual(audit['status'], 'limit_reached')

    def test_no_more_than_two_repairs_even_if_issues_remain(self):
        (_, _, audit), model = self.execute([self.report('high'), self.report('medium', 'b'), self.report('low', 'c')])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(audit['repairs'], 2)
        self.assertEqual(audit['accepted'], 2)
        self.assertEqual(audit['status'], 'limit_reached')

    def test_failed_reinspection_keeps_original_deck(self):
        (_, payload, audit), _ = self.execute([self.report('high'), dict(status='failed')])
        self.assertEqual(payload, b'original')
        self.assertEqual(audit['status'], 'unavailable')

    def test_invalid_repair_does_not_destroy_valid_deck(self):
        candidate = copy.deepcopy(self.deck)
        candidate['slides'][1]['sources'] = ['M999']
        (_, payload, audit), _ = self.execute([self.report('high')], candidate)
        self.assertEqual(payload, b'original')
        self.assertEqual(audit['status'], 'unavailable')

    def test_single_page_refinement_sends_and_renders_only_target(self):
        self.manager.review_sync.side_effect = [self.report('high'), self.report(identity='b')]
        updated = copy.deepcopy(self.deck['slides'][1]); updated['bullets'] = ['Shorter text']
        model = Mock(return_value=module.ModelResult(json.dumps(updated)))
        with patch('presentations._aesthetic_reviews', self.manager), patch('presentations.build_pptx', return_value=b'changed'):
            deck, payload, audit = refine_presentation(21, self.deck, b'original', self.context, self.prepared, model, only_pages=[2])
        self.assertEqual(payload, b'changed')
        self.assertEqual(audit['accepted'], 1)
        self.assertEqual(deck['slides'][0], self.deck['slides'][0])
        for call in self.manager.review_sync.call_args_list:
            self.assertEqual(call.args[2], [2])
        latest_message = json.loads(model.call_args.args[0].body)['messages'][-1]['content']
        self.assertIn('target_slide', latest_message)
        self.assertNotIn('current_deck', latest_message)


class PresentationIntegrationTests(ProfileDatabaseFixture):
    def setUp(self):
        super().setUp()
        # These tests stub language generation; never send their fake decks to a real VLM.
        self.aesthetic_patch = patch('presentations._aesthetic_reviews', None)
        self.aesthetic_patch.start()
        self.queue = JobQueue(lambda: module.db(), module.execute_job)
        self.queue_patch = patch.object(module, 'TASKS', self.queue)
        self.queue_patch.start()

    def tearDown(self):
        self.queue.wait_idle(10); self.queue.close(); self.queue_patch.stop()
        self.aesthetic_patch.stop()

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

    def edit_slide(self, parent, page=2, reply=None, instruction='简化本页文字，保留公式与来源'):
        original = self.client.get(f'/api/presentations/{parent}').json
        slide = copy.deepcopy(original['deck']['slides'][page-1])
        slide['bullets'] = ['等式两边同减相同数，仍然相等。']
        if 'visual' in slide:
            slide['visual'] = fallback_visual(slide)
        def model(prepared, attempted):
            self.assertTrue(module.GENERATION_LOCK.locked())
            body = json.loads(prepared.body)
            sent = json.loads(body['messages'][1]['content'])
            self.assertEqual(sent['target_slide'], original['deck']['slides'][page-1])
            self.assertEqual(sent['teacher_instruction'], instruction)
            self.assertNotIn('slides', sent)
            self.assertNotIn('lecture', sent)
            attempted()
            return module.ModelResult(json.dumps(reply if reply is not None else slide, ensure_ascii=False))
        with patch.object(module, 'call_qwen', model):
            response = self.client.post(f'/api/presentations/{parent}/slide-edits', json=dict(
                page=page, instruction=instruction, base_sha256=original['deck_sha256']))
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.queue.wait_idle(15))
        return original, self.client.get('/api/jobs/' + response.json['id']).json

    def test_slide_edit_preview_accept_and_continue_preserve_all_other_pages(self):
        original_job = self.submit()
        parent = original_job['run_id']
        original, job = self.edit_slide(parent)
        self.assertEqual(job['status'], 'succeeded', job)
        candidate_id = job['run_id']
        self.assertEqual(self.client.get(f'/api/presentations/{candidate_id}/download').status_code, 409)
        candidate = self.client.get(f'/api/presentation-edits/{candidate_id}').json
        self.assertEqual(candidate['state'], 'pending')
        self.assertEqual(candidate['before'], original['deck']['slides'][1])
        self.assertNotEqual(candidate['after']['bullets'], candidate['before']['bullets'])
        history = self.client.get(f'/api/lectures/{self.lecture_id}/presentations').json['presentations']
        self.assertNotIn(candidate_id, [item['run_id'] for item in history])
        for _ in range(2):
            self.assertEqual(self.client.post(f'/api/presentation-edits/{candidate_id}/accept').status_code, 200)
        updated = self.client.get(f'/api/presentations/{candidate_id}').json
        self.assertEqual(updated['edit'], dict(parent_run_id=parent, page=2))
        self.assertEqual(updated['deck']['title'], original['deck']['title'])
        for index, (before, after) in enumerate(zip(original['deck']['slides'], updated['deck']['slides'])):
            self.assertEqual(after, candidate['after'] if index == 1 else before)
        self.assertEqual(self.client.get(f'/api/presentations/{parent}').json['deck'], original['deck'])
        download = self.client.get(f'/api/presentations/{candidate_id}/download')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(len(Presentation(BytesIO(download.data)).slides), 6)
        self.assertEqual(self.client.post(f'/api/presentation-edits/{candidate_id}/discard').status_code, 409)
        _, second = self.edit_slide(candidate_id, page=3)
        self.assertEqual(second['status'], 'succeeded')
        self.assertEqual(self.client.post(f'/api/presentation-edits/{second["run_id"]}/accept').status_code, 200)
        latest = self.client.get(f'/api/presentations/{second["run_id"]}').json
        self.assertEqual(latest['deck']['slides'][1], updated['deck']['slides'][1])
        with self.fixture_db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM lectures').fetchone()['n'], 1)

    def test_discard_is_durable_and_does_not_create_downloadable_version(self):
        parent = self.submit()['run_id']
        _, job = self.edit_slide(parent)
        identity = job['run_id']
        for _ in range(2):
            self.assertEqual(self.client.post(f'/api/presentation-edits/{identity}/discard').status_code, 200)
        self.assertEqual(self.client.get(f'/api/presentation-edits/{identity}').json['state'], 'discarded')
        self.assertEqual(self.client.post(f'/api/presentation-edits/{identity}/accept').status_code, 409)
        self.assertEqual(self.client.get(f'/api/presentations/{identity}').status_code, 409)
        self.assertEqual(self.client.get(f'/api/presentations/{parent}').status_code, 200)

    def test_edit_input_page_instruction_and_stale_snapshot_are_rejected(self):
        parent = self.submit()['run_id']
        detail = self.client.get(f'/api/presentations/{parent}').json
        valid = dict(page=2, instruction='简化本页', base_sha256=detail['deck_sha256'])
        for field, value in [('page', True), ('page', 0), ('page', 7), ('page', '2'),
                             ('instruction', ''), ('instruction', 'x'*1501), ('instruction', [])]:
            with self.subTest(field=field, value=value):
                data = {**valid, field:value}
                self.assertEqual(self.client.post(f'/api/presentations/{parent}/slide-edits', json=data).status_code, 400)
        self.assertEqual(self.client.post(f'/api/presentations/{parent}/slide-edits', json={**valid, 'base_sha256':'stale'}).status_code, 409)
        self.assertEqual(self.client.get('/api/presentation-edits/99999').status_code, 404)

    def test_whole_deck_or_unknown_citations_cannot_be_used_as_single_page_output(self):
        parent = self.submit()['run_id']
        for bad in (fixture_deck(), dict(fixture_deck()['slides'][1], sources=['M999'])):
            _, job = self.edit_slide(parent, reply=bad)
            self.assertEqual(job['status'], 'failed')
            self.assertEqual(self.client.post(f'/api/presentation-edits/{job["run_id"]}/accept').status_code, 409)
        self.assertEqual(self.client.get(f'/api/presentations/{parent}/download').status_code, 200)

    def test_accept_rejects_tampered_non_target_page(self):
        parent = self.submit()['run_id']
        _, job = self.edit_slide(parent)
        with self.fixture_db() as conn:
            row = conn.execute('SELECT content FROM generation_runs WHERE id=%s', (job['run_id'],)).fetchone()
            deck = json.loads(row['content']); deck['slides'][2]['title'] = '其他页面被篡改'
            conn.execute('UPDATE generation_runs SET content=%s WHERE id=%s', (json.dumps(deck, ensure_ascii=False), job['run_id']))
        self.assertEqual(self.client.post(f'/api/presentation-edits/{job["run_id"]}/accept').status_code, 409)

    def test_edit_visual_review_is_scoped_to_target_page(self):
        parent = self.submit()['run_id']
        observed = []
        def refine(run_id, deck, payload, context, prepared, model, only_pages=None):
            observed.append(only_pages)
            return deck, payload, dict(status='completed')
        with patch('presentations.refine_presentation', side_effect=refine):
            _, job = self.edit_slide(parent, page=4)
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(observed, [[4]])

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

    def test_automatic_visual_review_finishes_before_commit_and_failure_keeps_ppt(self):
        manager = Mock()
        observed = {}
        def inspect(run_id, payload, pages):
            with self.fixture_db() as conn:
                run = conn.execute('SELECT status FROM generation_runs WHERE id=%s', (run_id,)).fetchone()
            observed.update(status=run['status'], pages=pages, slides=len(Presentation(BytesIO(payload)).slides))
            raise RuntimeError('fixture failure')
        manager.review_sync.side_effect = inspect
        with patch('presentations._aesthetic_reviews', manager):
            job = self.submit()
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(observed, dict(status='running', pages=list(range(1, 7)), slides=6))
        manager.review_sync.assert_called_once()
        run = self.client.get('/api/generation-runs/' + str(job['run_id'])).json
        self.assertEqual(run['response_metadata']['aesthetic_refinement']['status'], 'unavailable')
        detail = self.client.get(f'/api/presentations/{job["run_id"]}')
        self.assertTrue(detail.json['source']['aesthetic_review'])
        self.assertEqual(self.client.get(f'/api/presentations/{job["run_id"]}/download').status_code, 200)

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
        # New exports freeze service-side JSON mode into both the context and the exact request bytes.
        self.assertTrue(run['context']['ppt_export']['json_mode'])
        self.assertEqual(json.loads(run['request_body'])['response_format'], {'type': 'json_object'})
        self.assertTrue(run['prompt_version'].endswith('+json-v1'))

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
