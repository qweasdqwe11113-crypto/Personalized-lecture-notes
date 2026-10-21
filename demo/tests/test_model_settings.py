"""Model settings use temporary config files and mocked network responses."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from dotenv import dotenv_values
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_settings as settings


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.env'
        self.path.write_text('# keep this comment\nDATABASE_URL=do-not-change\n'
                             'QWEN_BASE_URL=http://127.0.0.1:11434/v1\n'
                             'QWEN_MODEL=qwen3.5:9b\nQWEN_API_KEY=private-key\n'
                             'QWEN_EMBEDDING_MODEL=bge-m3:latest\n', encoding='utf-8')
        self.environment = patch.dict(os.environ, dict(dotenv_values(self.path)), clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = Flask(__name__)
        settings.register_model_settings(self.app, self.path)
        self.client = self.app.test_client()

    def payload(self):
        channels = self.client.get('/api/model-settings').json['channels']
        for channel in channels.values():
            channel.pop('key_present')
            channel['api_key'] = ''
        return {'channels': channels}

    def response(self, data):
        return patch.object(settings.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(data).encode()))

    def test_get_never_returns_secrets_and_inherits_embedding_connection(self):
        response = self.client.get('/api/model-settings')
        self.assertNotIn('private-key', response.text)
        self.assertTrue(response.json['channels']['embedding']['key_present'])
        self.assertFalse(response.json['restart_required'])

    def test_save_preserves_keys_and_unrelated_config_without_changing_active_environment(self):
        data = self.payload()
        data['channels']['llm']['model'] = 'another-model'
        data['channels']['vlm'].update(base_url='http://vision.invalid/v1', model='vision-model', api_key="secret'with$characters")
        response = self.client.post('/api/model-settings', json=data)
        self.assertEqual(response.status_code, 200)
        values = dotenv_values(self.path)
        self.assertEqual(values['DATABASE_URL'], 'do-not-change')
        self.assertIn('# keep this comment', self.path.read_text())
        self.assertEqual(values['QWEN_API_KEY'], 'private-key')
        self.assertEqual(values['VLM_API_KEY'], "secret'with$characters")
        self.assertEqual(os.environ['QWEN_MODEL'], 'qwen3.5:9b')
        self.assertNotIn('secret', response.text)
        self.assertTrue(self.client.get('/api/model-settings').json['restart_required'])

    def test_clear_key_requires_explicit_action(self):
        data = self.payload()
        data['channels']['llm']['clear_key'] = True
        response = self.client.post('/api/model-settings', json=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dotenv_values(self.path)['QWEN_API_KEY'], '')

    def test_failed_write_keeps_original_file_and_reports_safe_error(self):
        before = self.path.read_bytes()
        with patch.object(settings.os, 'replace', side_effect=OSError('private-key')):
            response = self.client.post('/api/model-settings', json=self.payload())
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private-key', response.text)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.model-settings-*')), [])

    def test_invalid_config_does_not_write_any_changes(self):
        before = self.path.read_bytes()
        for url in ('file:///tmp/example', 'http://user:password@host/v1', 'http://host:bad/v1', 'http://host/v1?key=secret'):
            data = self.payload()
            data['channels']['llm']['base_url'] = url
            response = self.client.post('/api/model-settings', json=data)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('password', response.text)
            self.assertEqual(self.path.read_bytes(), before)

    def test_test_uses_unsaved_form_and_retained_key_but_does_not_persist(self):
        before = self.path.read_bytes()
        with self.response({'choices': [{'message': {'content': '5'}, 'finish_reason': 'stop'}]}) as mock:
            response = self.client.post('/api/model-settings/test', json={
                'channel': 'llm', 'config': {'base_url': 'http://unsaved.invalid/v1', 'model': 'test', 'api_key': ''}})
        self.assertEqual(response.status_code, 200)
        req = mock.call_args.args[0]
        self.assertEqual(req.full_url, 'http://unsaved.invalid/v1/chat/completions')
        self.assertEqual(req.get_header('Authorization'), 'Bearer private-key')
        self.assertEqual(self.path.read_bytes(), before)

    def test_embedding_test_checks_returned_vector(self):
        config = self.payload()['channels']['embedding']
        with self.response({'data': [{'embedding': [0.5, 0.25, 1.0]}]}):
            result = self.client.post('/api/model-settings/test', json={'channel': 'embedding', 'config': config})
        self.assertEqual(result.json['dimensions'], 3)
        with self.response({'data': [{'embedding': []}]}):
            result = self.client.post('/api/model-settings/test', json={'channel': 'embedding', 'config': config})
        self.assertEqual(result.status_code, 400)

    def test_vision_probe_sends_image_and_checks_identification(self):
        config = self.payload()['channels']['llm']
        with self.response({'choices': [{'message': {'content': 'Red triangle, 739.'}}]}) as mock:
            response = self.client.post('/api/model-settings/test', json={'channel': 'vlm', 'config': config})
        self.assertTrue(response.json['vision_verified'])
        payload = json.loads(mock.call_args.args[0].data)
        self.assertTrue(payload['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        with self.response({'choices': [{'message': {'content': 'I cannot see the image.'}}]}):
            response = self.client.post('/api/model-settings/test', json={'channel': 'vlm', 'config': config})
        self.assertFalse(response.json['vision_verified'])

    def test_http_failures_never_expose_service_error_or_key(self):
        config = self.payload()['channels']['llm']
        failure = urllib.error.HTTPError('http://secret.invalid', 401, 'private-key', {}, None)
        with patch.object(settings.urllib.request, 'urlopen', side_effect=failure):
            response = self.client.post('/api/model-settings/test', json={'channel': 'llm', 'config': config})
        self.assertEqual(response.status_code, 400)
        self.assertIn('401', response.text)
        self.assertNotIn('private-key', response.text)

    def test_reasoning_only_response_does_not_count_as_success(self):
        with self.response({'choices': [{'message': {'content': '<think>Only thinking</think>'}}]}):
            response = self.client.post('/api/model-settings/test', json={
                'channel': 'llm', 'config': self.payload()['channels']['llm']})
        self.assertEqual(response.status_code, 400)

    def test_cross_site_and_untrusted_host_are_rejected(self):
        response = self.client.get('/api/model-settings', headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(response.status_code, 403)
        response = self.client.get('/api/model-settings', base_url='http://foreign.invalid')
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
