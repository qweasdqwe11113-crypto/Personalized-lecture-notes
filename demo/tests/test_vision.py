"""Image uploads and isolated VLM transport, with no real model requests."""
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from flask import Flask
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uploads import register_uploads
import vision
from prompts import prepare_request


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = self.directory.name
        self.app = Flask(__name__)
        self.app.testing = True
        register_uploads(self.app, self.root)
        self.client = self.app.test_client()

    def tearDown(self):
        self.directory.cleanup()

    def upload(self):
        raw = BytesIO()
        Image.new('RGB', (200, 150), 'red').save(raw, format='PNG')
        r = self.client.post('/api/uploads', data={'file': (BytesIO(raw.getvalue()), 'figure.png')})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json['upload']['images'], 1)
        uid = r.json['upload']['id']
        image = self.client.get('/api/uploads/' + uid + '/images').json['images'][0]
        return uid, image

    def interpret(self, image):
        body = BytesIO(json.dumps({'model': 'local-vision', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': '红色矩形。数字无法确认。'}}]}).encode())
        with patch.dict(os.environ, {'VLM_BASE_URL': 'http://127.0.0.1:11434/v1',
                'VLM_MODEL': 'local-vision', 'VLM_API_KEY': 'vision-secret',
                'QWEN_BASE_URL': 'https://llm.invalid/v1', 'QWEN_API_KEY': 'llm-secret'}):
            with patch('vision.urllib.request.urlopen', return_value=body) as call:
                response = self.client.post('/api/vision/images/' + image['id'] + '/analyse')
                req = call.call_args.args[0]
        self.assertEqual(response.status_code, 201)
        self.assertEqual(req.full_url, 'http://127.0.0.1:11434/v1/chat/completions')
        self.assertEqual(req.get_header('Authorization'), 'Bearer vision-secret')
        return response.json['analysis'], req

    def test_upload_vision_and_text_only_generation_snapshot(self):
        uid, image = self.upload()
        raw = self.client.get('/api/vision/images/' + image['id']).data
        self.assertEqual(hashlib.sha256(raw).hexdigest(), image['normalized_sha256'])
        analysis, req = self.interpret(image)
        payload = json.loads(req.data)
        uri = payload['messages'][1]['content'][1]['image_url']['url']
        self.assertEqual(base64.b64decode(uri.split(',', 1)[1]), raw)
        evidence = vision.selected_evidence(self.root, [analysis['id']])
        context = {'topic': {'course': '初中数学'}, 'materials': [], 'visual_evidence': evidence}
        prepared = prepare_request(context, '测试目标', 'language-model')
        text = prepared.body
        self.assertNotIn('base64', text)
        self.assertNotIn('vision-secret', text)
        self.assertNotIn('llm-secret', text)
        self.assertIn('红色矩形', text)
        self.assertIn('V1', text)
        self.assertEqual(prepared.input_version, 'lecture-context-vision-v1')
        self.assertEqual(evidence[0]['model'], 'local-vision')
        self.assertEqual(evidence[0]['review_status'], 'needs_review')
        # A new interpretation never changes the old analysis or its frozen text.
        again, _ = self.interpret(image)
        self.assertNotEqual(again['id'], analysis['id'])
        self.assertEqual(vision.selected_evidence(self.root, [analysis['id']]), evidence)

    def test_selection_and_missing_images(self):
        for ids in ('bad', [1], ['x' * 32] * 2, ['x' * 32] * 5, ['x' * 32]):
            with self.assertRaises(vision.VisionError):
                vision.selected_evidence(self.root, ids)
        self.assertEqual(self.client.post('/api/vision/images/missing/analyse').status_code, 404)

    def test_error_and_truncation_do_not_save_analysis(self):
        _, image = self.upload()
        with patch.dict(os.environ, {'VLM_BASE_URL': 'http://127.0.0.1:11434/v1', 'VLM_MODEL': 'vision'}):
            failure = urllib.error.HTTPError('http://secret.invalid', 500, 'private error', {}, None)
            with patch('vision.urllib.request.urlopen', side_effect=failure):
                r = self.client.post('/api/vision/images/' + image['id'] + '/analyse')
            self.assertEqual(r.status_code, 400)
            self.assertNotIn('private error', r.json['error'])
            response = {'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]}
            with patch('vision.urllib.request.urlopen', return_value=BytesIO(json.dumps(response).encode())):
                r = self.client.post('/api/vision/images/' + image['id'] + '/analyse')
            self.assertEqual(r.status_code, 400)
            self.assertEqual(self.client.get('/api/vision/analyses').json['analyses'], [])

    def test_image_only_docx_extracts_and_limit_is_explicit(self):
        from docx import Document
        raw = BytesIO(); Image.new('RGB', (50, 50), 'blue').save(raw, format='PNG'); raw.seek(0)
        doc = Document(); doc.add_picture(raw)
        data = BytesIO(); doc.save(data)
        r = self.client.post('/api/uploads', data={'file': (BytesIO(data.getvalue()), 'images.docx')})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json['upload']['images'], 1)
        with patch('vision.assets.extract', return_value=[None] * 9):
            with self.assertRaises(vision.VisionError):
                vision.prepare_images(b'', 'images.pdf')
