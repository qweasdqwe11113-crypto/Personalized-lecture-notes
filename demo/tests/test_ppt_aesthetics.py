"""Visual review contracts and durable queue; never call Office or a real model."""
import base64
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask
from PIL import Image
import ppt_aesthetics as review


def observation():
    return dict(verdict='acceptable', summary='Clear title', strengths=['Readable'], issues=[])


class ReviewContractTests(unittest.TestCase):
    def test_malformed_model_output_is_a_controlled_error(self):
        cases = [None, [], {}, dict(observation(), verdict=[]),
                 dict(observation(), summary=' '), dict(observation(), summary='x'*401),
                 dict(observation(), strengths=['x']*4), dict(observation(), issues={})]
        issue = dict(severity='high', category='clipping', location='right',
                     evidence='Text crosses edge', suggestion='Reduce text')
        cases.extend(dict(observation(), issues=[dict(issue, **{key: []})])
                     for key in ('severity', 'category', 'location'))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(review.AestheticError):
                review.validate_review(value)
        self.assertEqual(review.validate_review(dict(observation(), issues=[issue]))['verdict'], 'needs_revision')

    def test_settings_restrict_model_requests_to_local_ollama(self):
        for base in ('https://localhost:11434', 'http://example.com',
                     'http://localhost/api', 'http://user:pass@localhost', 'http://localhost?token=x'):
            with self.subTest(base=base), patch.dict('os.environ', PPT_AESTHETIC_BASE_URL=base):
                with self.assertRaises(review.AestheticError):
                    review.settings()
        with patch.dict('os.environ', PPT_AESTHETIC_BASE_URL='http://127.0.0.1:11434'):
            self.assertEqual(review.settings()[0], 'http://127.0.0.1:11434')

    def test_model_must_declare_vision(self):
        for capabilities in (None, 'vision', {}, ['completion']):
            with self.subTest(capabilities=capabilities), patch.object(review, 'ollama_json', return_value=dict(capabilities=capabilities)):
                with self.assertRaises(review.AestheticError):
                    review.check_model('fixture')
        with patch.object(review, 'ollama_json', return_value=dict(capabilities=['vision'])):
            self.assertEqual(review.check_model('fixture'), 'fixture')

    def test_request_contains_real_normalized_pixels_and_digest(self):
        with TemporaryDirectory() as root:
            path = Path(root) / 'page.png'
            Image.new('RGBA', (2000, 1000), (40, 80, 120, 255)).save(path)
            def respond(endpoint, payload, timeout):
                self.assertTrue(review.VLM_LOCK.locked())
                self.assertEqual(endpoint, '/api/chat')
                self.assertEqual(timeout, 300)
                self.assertFalse(payload['stream'])
                pixels = base64.b64decode(payload['messages'][1]['images'][0])
                with Image.open(BytesIO(pixels)) as img:
                    self.assertEqual(img.size, (1440, 720))
                    self.assertEqual(img.mode, 'RGB')
                    self.assertEqual(img.getpixel((5, 5)), (40, 80, 120))
                self.sent_hash = hashlib.sha256(pixels).hexdigest()
                return dict(done=True, model='fixture-response', message=dict(content=json.dumps(observation())))
            with patch.object(review, 'ollama_json', side_effect=respond):
                result = review.review_image(path, 'fixture')
            self.assertEqual(result['image_sha256'], self.sent_hash)
            self.assertEqual(result['response_model'], 'fixture-response')
            self.assertEqual(result['prompt_version'], review.PROMPT_VERSION)
            for response in (dict(done=False), dict(done=True, done_reason='length'),
                             dict(done=True, message=dict(content='not JSON'))):
                with patch.object(review, 'ollama_json', return_value=response), self.assertRaises(review.AestheticError):
                    review.review_image(path, 'fixture')


class DurableReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = review.AestheticReviews(self.tmp.name)
        self.dispatch = patch.object(self.manager, 'dispatch', side_effect=self.capture)
        self.dispatch.start()
        self.addCleanup(self.dispatch.stop)

    def capture(self, task):
        self.task = task
        self.manager.active = task[0]

    def run_review(self, effects):
        job = self.manager.start(21, b'exact PPTX bytes', [1, 3])
        def render(source, output, pages):
            self.assertEqual(source.read_bytes(), b'exact PPTX bytes')
            self.assertEqual(pages, [1, 3])
            return [output / f'slide-{page}.png' for page in pages]
        with patch.object(review, 'check_model'), patch.object(review, 'render_slides', side_effect=render), patch.object(review, 'review_image', side_effect=effects):
            self.manager.work(*self.task)
        return job, self.manager.get(21)

    def test_success_and_saved_source_survive_new_manager(self):
        job, result = self.run_review([observation(), observation()])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([r['page'] for r in result['results']], [1, 3])
        self.assertEqual(result['pptx_sha256'], hashlib.sha256(b'exact PPTX bytes').hexdigest())
        restored = review.AestheticReviews(self.tmp.name).get(21)
        self.assertEqual(restored['results'], result['results'])
        self.assertEqual((self.manager.root / job['id'] / 'source.pptx').read_bytes(), b'exact PPTX bytes')

    def test_mixed_success_and_failure_is_partial(self):
        _, result = self.run_review([observation(), review.AestheticError('fixture error')])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['results'][1]['error'], 'fixture error')

    def test_all_pages_failed_is_failed(self):
        _, result = self.run_review([review.AestheticError('one'), review.AestheticError('two')])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['results']), 2)

    def test_render_failure_stops_before_model_request(self):
        self.manager.start(21, b'pptx', [1])
        with patch.object(review, 'check_model'), patch.object(review, 'render_slides', side_effect=review.AestheticError('Office unavailable')), patch.object(review, 'review_image') as model:
            self.manager.work(*self.task)
        self.assertEqual(self.manager.get(21)['status'], 'failed')
        model.assert_not_called()
        self.assertIsNone(self.manager.active)

    def test_duplicate_and_queue_limit_and_fifo(self):
        self.manager.start(1, b'a', [1])
        with self.assertRaises(review.AestheticError):
            self.manager.start(1, b'a', [1])
        for run_id in (2, 3, 4):
            self.manager.start(run_id, b'b', [1])
        first_pending = self.manager.pending[0][0]
        with self.assertRaises(review.AestheticError):
            self.manager.start(5, b'c', [1])
        with patch.object(review, 'check_model', side_effect=review.AestheticError('offline')):
            self.manager.work(*self.task)
        self.assertEqual(self.manager.active, first_pending)
        self.assertEqual(len(self.manager.pending), 2)

    def test_restart_marks_incomplete_interrupted_without_resubmission(self):
        self.manager.start(21, b'pptx', [1])
        fresh = review.AestheticReviews(self.tmp.name)
        with patch.object(fresh, 'dispatch') as dispatch:
            self.assertEqual(fresh.get(21)['status'], 'interrupted')
            dispatch.assert_not_called()
        self.assertEqual(fresh.get(21)['results'], [])

    def test_submission_failure_is_persisted(self):
        self.manager.record_failure(21, b'pptx', [1], 'queue full')
        self.assertEqual(self.manager.get(21)['status'], 'failed')
        self.assertEqual(self.manager.get(21)['error'], 'queue full')


class ReviewAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        app = Flask(__name__)
        app.testing = True
        self.export_calls = []
        def completed(run_id):
            return dict(context=dict(ppt_export=dict(template_id='fixture'))), dict(slides=[{}, {}, {}])
        def exporter(deck, source, context):
            self.export_calls.append(copy.deepcopy(source))
            return b'exported snapshot'
        self.manager = review.register_aesthetics(app, completed, exporter, self.tmp.name)
        self.client = app.test_client()
        self.base = '/api/presentations/21/aesthetic-review'
        self.dispatch = patch.object(self.manager, 'dispatch')
        self.dispatch.start()
        self.addCleanup(self.dispatch.stop)

    def test_page_selection_rejects_invalid_input_before_export(self):
        for pages in ([], [0], [4], [True], [1, 1], ['1'], {}, None):
            with self.subTest(pages=pages):
                self.assertEqual(self.client.post(self.base, json=dict(pages=pages)).status_code, 400)
        self.assertEqual(self.client.post(self.base, json=[]).status_code, 400)
        self.assertEqual(self.export_calls, [])

    def test_all_pages_default_and_latest_status_and_image_association(self):
        self.assertIsNone(self.client.get(self.base).json['review'])
        response = self.client.post(self.base, json={})
        self.assertEqual(response.status_code, 202)
        job = response.json['review']
        self.assertEqual(job['pages'], [1, 2, 3])
        self.assertEqual(self.export_calls[0]['run_id'], 21)
        self.assertEqual(self.client.get(self.base).json['review']['id'], job['id'])
        url = f"{self.base}/{job['id']}/slides/1"
        self.assertEqual(self.client.get(url).status_code, 404)
        path = self.manager.root / job['id'] / 'slide-1.png'
        Image.new('RGB', (12, 8), 'white').save(path)
        with self.client.get(url) as image:
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.mimetype, 'image/png')
            self.assertEqual(image.data, path.read_bytes())
        self.assertEqual(self.client.get(url.replace('/21/', '/22/')).status_code, 404)
        self.assertEqual(self.client.get(url.replace('/slides/1', '/slides/4')).status_code, 404)
        self.assertEqual(self.client.get(url.replace(job['id'], 'invalid')).status_code, 404)


if __name__ == '__main__':
    unittest.main()
