"""Quick setup of the model settings: pasted configurations, local services and model lists.

Network calls are replaced by canned replies; nothing leaves this computer."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from dotenv import dotenv_values
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_import as mi
import model_settings as settings

KEY = 'sk-test1234567890abcdefgh'


def canned(routes):
    """fetch_json replacement: routes maps a URL (or 'URL key') to (status, data); anything else is closed."""
    calls = []
    def fetch(url, api_key='', timeout=15.0, method='GET', payload=None):
        calls.append((method, url, api_key, payload))
        for route in (f'{url} {api_key}', url):
            if route in routes:
                answer = routes[route]
                return answer(payload) if callable(answer) else answer
        return 0, None
    return fetch, calls


class PastedConfiguration(unittest.TestCase):
    def parse(self, text, provider=''):
        return mi.parse_config(text, provider)

    def test_one_click_links_from_clients_and_relay_sites(self):
        data = base64.b64encode(json.dumps({'id': 'new-api', 'baseUrl': 'https://relay.example.com', 'apiKey': KEY}).encode())
        cherry = self.parse('cherrystudio://providers/api-keys?v=1&data=' + quote(data.decode()))
        self.assertEqual((cherry['base_url'], cherry['api_key'], cherry['source']),
                         ('https://relay.example.com/v1', KEY, 'Cherry Studio 一键导入链接'))
        switch = self.parse('ccswitch://v1/import?resource=provider&app=claude&name=relay&endpoint='
                            + quote('https://relay.example.com') + f'&apiKey={KEY}&model=glm-4.6')
        self.assertEqual((switch['base_url'], switch['model']), ('https://relay.example.com/v1', 'glm-4.6'))
        self.assertIn('Claude Code', switch['notes'][0])
        nextchat = self.parse('https://chat.example.com/#/?settings={"key":"%s","url":"https://relay.example.com"}' % KEY)
        self.assertEqual((nextchat['api_key'], nextchat['source']), (KEY, 'NextChat 导入链接'))
        lobe = self.parse('https://lobe.example.com/?settings=' + quote(json.dumps(
            {'keyVaults': {'openai': {'apiKey': KEY, 'baseURL': 'https://relay.example.com/v1'}}})))
        self.assertEqual((lobe['base_url'], lobe['source']), ('https://relay.example.com/v1', 'LobeChat 导入链接'))
        with self.assertRaisesRegex(mi.ConfigImportError, 'Cherry Studio'):
            self.parse('cherrystudio://providers/api-keys?v=1&data=not-base64')

    def test_curl_commands_give_the_address_key_and_model(self):
        found = self.parse('curl https://api.deepseek.com/chat/completions \\\n  -H "Content-Type: application/json" \\\n'
                           f'  -H "Authorization: Bearer {KEY}" \\\n  -d \'{{"model": "deepseek-chat", "messages": []}}\'')
        self.assertEqual((found['base_url'], found['api_key'], found['model'], found['provider']),
                         ('https://api.deepseek.com/v1', KEY, 'deepseek-chat', 'DeepSeek'))

    def test_environment_variables_fill_one_or_all_three_channels(self):
        ours = self.parse('QWEN_BASE_URL="http://127.0.0.1:11434/v1"\nQWEN_MODEL=qwen3.5:9b\n'
                          'VLM_BASE_URL=http://127.0.0.1:11434/v1\nVLM_MODEL=qwen2.5vl:7b\nQWEN_EMBEDDING_MODEL=bge-m3:latest')
        self.assertEqual((ours['model'], ours['local']), ('qwen3.5:9b', True))
        self.assertEqual(ours['channels']['vlm']['model'], 'qwen2.5vl:7b')
        self.assertEqual(ours['channels']['embedding'], {'base_url': 'http://127.0.0.1:11434/v1', 'api_key': '', 'model': 'bge-m3:latest'})
        relay = self.parse(f'export OPENAI_BASE_URL=https://relay.example.com/v1\nexport OPENAI_API_KEY={KEY}\nOPENAI_MODEL=gpt-4o-mini')
        self.assertEqual((relay['base_url'], relay['model'], relay['channels']), ('https://relay.example.com/v1', 'gpt-4o-mini', {}))
        # Only a provider's key variable: the provider's address is known.
        dashscope = self.parse('DASHSCOPE_API_KEY=sk-0123456789abcdef0123456789abcdef')
        self.assertEqual(dashscope['base_url'], 'https://dashscope.aliyuncs.com/compatible-mode/v1')

    def test_json_code_samples_and_labelled_text(self):
        continued = self.parse(json.dumps({'models': [{'title': 'x', 'provider': 'openai', 'model': 'qwen-max',
                                                       'apiBase': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'apiKey': KEY}]}))
        self.assertEqual((continued['model'], continued['source']), ('qwen-max', 'JSON 配置'))
        code = self.parse(f'client = OpenAI(api_key="{KEY}", base_url="https://api.moonshot.cn/v1")\n'
                          'client.chat.completions.create(model="kimi-k2", messages=[])')
        self.assertEqual((code['base_url'], code['model'], code['provider']), ('https://api.moonshot.cn/v1', 'kimi-k2', '月之暗面 Kimi'))
        page = self.parse(f'API 地址：https://api.relay.cn/v1/chat/completions\n密钥：{KEY}\n模型名称：glm-4.5\n'
                          '使用说明见 https://docs.relay.cn')
        self.assertEqual((page['base_url'], page['api_key'], page['model']), ('https://api.relay.cn/v1', KEY, 'glm-4.5'))

    def test_addresses_are_completed_and_cleaned(self):
        cases = {'localhost:1234': 'http://localhost:1234/v1', 'http://127.0.0.1:11434': 'http://127.0.0.1:11434/v1',
                 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation':
                     'https://dashscope.aliyuncs.com/compatible-mode/v1',
                 'https://open.bigmodel.cn': 'https://open.bigmodel.cn/api/paas/v4',
                 'https://user:pw@relay.example.com/v1/models?x=1#y': 'https://relay.example.com/v1',
                 'relay.example.com/v1/': 'https://relay.example.com/v1', 'BASE_URL': '', 'ftp://example.com': ''}
        for text, expected in cases.items():
            self.assertEqual(mi.normalize_base_url(text), expected, text)

    def test_a_key_alone_needs_a_provider(self):
        with self.assertRaisesRegex(mi.ConfigImportError, '选择服务商'):
            self.parse(KEY)
        found = self.parse(KEY, 'deepseek')
        self.assertEqual((found['base_url'], found['api_key']), ('https://api.deepseek.com/v1', KEY))
        self.assertEqual(self.parse('', 'siliconflow')['base_url'], 'https://api.siliconflow.cn/v1')
        for text in ('随便写的一段话', 'base_url=BASE_URL\napi_key=os.environ["KEY"]'):
            with self.assertRaises(mi.ConfigImportError):
                self.parse(text)
        with self.assertRaisesRegex(mi.ConfigImportError, '太长'):
            self.parse('x' * (mi.MAX_TEXT + 1))


class ModelsOfAService(unittest.TestCase):
    def test_models_are_sorted_into_channels_by_what_the_server_says_or_their_names(self):
        models = mi.describe([{'id': i} for i in ('qwen3.5:9b', 'qwen2.5vl:7b', 'bge-m3:latest', 'nomic-embed-text',
                                                   'text-embedding-v4', 'gte-rerank', 'whisper-1', 'gemma3:1b')]
                             + [{'id': 'mystery', 'hint': 'embedding'}])
        kinds = {m['id']: m['kind'] for m in models}
        self.assertEqual(kinds, {'qwen3.5:9b': 'chat', 'qwen2.5vl:7b': 'vision', 'bge-m3:latest': 'embedding',
                                 'nomic-embed-text': 'embedding', 'text-embedding-v4': 'embedding', 'gte-rerank': 'other',
                                 'whisper-1': 'other', 'gemma3:1b': 'chat', 'mystery': 'embedding'})
        self.assertEqual(mi.suggest(models), {'llm': 'qwen3.5:9b', 'vlm': 'qwen2.5vl:7b', 'embedding': 'bge-m3:latest'})
        # The model already set stays when the service has it.
        self.assertEqual(mi.suggest(models, {'embedding': 'nomic-embed-text'})['embedding'], 'nomic-embed-text')
        # Dozens of cloud models without sizes: no guess for the language model.
        cloud = mi.describe([{'id': f'model-{n}'} for n in range(12)] + [{'id': 'text-embedding-v1'}, {'id': 'text-embedding-v3'}])
        self.assertEqual(mi.suggest(cloud), {'llm': '', 'vlm': '', 'embedding': 'text-embedding-v3'})

    def test_listing_tries_with_and_without_v1_and_explains_failures(self):
        fetch, calls = canned({'https://relay.example.com/models ' + KEY: (200, {'data': [{'id': 'deepseek-chat'}]}),
                               'https://relay.example.com/v1/models ' + KEY: (404, None)})
        with patch.object(mi, 'fetch_json', fetch):
            listed = mi.list_models('https://relay.example.com/v1', KEY)
        self.assertEqual((listed['base_url'], listed['suggested']['llm']), ('https://relay.example.com', 'deepseek-chat'))
        for routes, message in (({'https://x.example.com/v1/models': (401, None)}, '401/403'),
                                ({}, '无法连接'), ({'https://x.example.com/v1/models': (404, None)}, '没有提供模型列表')):
            with patch.object(mi, 'fetch_json', canned(routes)[0]), self.assertRaisesRegex(mi.ConfigImportError, message):
                mi.list_models('https://x.example.com/v1', 'wrong')

    def test_local_services_are_found_on_their_usual_ports(self):
        shows = {'qwen3.5:9b': ['completion'], 'qwen2.5vl:7b': ['completion', 'vision'], 'bge-m3:latest': ['embedding']}
        fetch, calls = canned({
            'http://127.0.0.1:11434/v1/models': (200, {'data': [{'id': k, 'owned_by': 'library'} for k in shows]}),
            'http://127.0.0.1:11434/api/tags': (200, {'models': [{'name': 'qwen3.5:9b', 'details': {'parameter_size': '9.7B'}}]}),
            'http://127.0.0.1:11434/api/show': lambda payload: (200, {'capabilities': shows[payload['model']]}),
            'http://127.0.0.1:1234/v1/models': (200, {'data': [{'id': 'qwen3-8b'}, {'id': 'text-embedding-nomic'}]}),
            'http://127.0.0.1:1234/api/v0/models': (200, {'data': [{'id': 'qwen3-8b', 'type': 'llm'},
                                                                   {'id': 'text-embedding-nomic', 'type': 'embeddings'}]}),
            'http://127.0.0.1:8000/v1/models': (401, None)})
        with patch.object(mi, 'fetch_json', fetch):
            services = {s['port']: s for s in mi.detect_local()}
        self.assertEqual(sorted(services), [1234, 8000, 11434])
        ollama = services[11434]
        self.assertEqual((ollama['name'], ollama['base_url'], ollama['needs_key']), ('Ollama', 'http://127.0.0.1:11434/v1', False))
        self.assertEqual(ollama['suggested'], {'llm': 'qwen3.5:9b', 'vlm': 'qwen2.5vl:7b', 'embedding': 'bge-m3:latest'})
        self.assertEqual((services[1234]['name'], services[1234]['suggested']['embedding']), ('LM Studio', 'text-embedding-nomic'))
        self.assertEqual((services[8000]['needs_key'], services[8000]['models']), (True, []))
        # Only this computer is asked, never with a key.
        self.assertTrue(all(url.startswith('http://127.0.0.1:') and not key for _, url, key, _ in calls))

    def test_requests_to_this_computer_skip_any_proxy(self):
        proxies = lambda opener: [h.proxies for h in opener.handlers if getattr(h, 'proxies', None)]
        with patch.dict(os.environ, {'http_proxy': 'http://proxy.invalid:9', 'HTTP_PROXY': 'http://proxy.invalid:9'}):
            self.assertEqual(proxies(mi._opener(True)), [])
            self.assertTrue(proxies(mi._opener(False)), 'other addresses keep the system proxy')


class QuickSetupRoutes(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.env'
        self.path.write_text('QWEN_BASE_URL=https://relay.example.com/v1\nQWEN_MODEL=old-model\n'
                             f'QWEN_API_KEY={KEY}\n', encoding='utf-8')
        environment = patch.dict(os.environ, dict(dotenv_values(self.path)), clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        app = Flask(__name__)
        settings.register_model_settings(app, self.path)
        self.client = app.test_client()

    def test_import_reads_the_text_lists_models_and_saves_nothing(self):
        before = self.path.read_bytes()
        fetch, calls = canned({'https://api.deepseek.com/v1/models sk-new1234567890abcdefgh':
                               (200, {'data': [{'id': 'deepseek-chat'}, {'id': 'deepseek-reasoner'}]})})
        with patch.object(mi, 'fetch_json', fetch):
            response = self.client.post('/api/model-settings/import', json={'text': 'sk-new1234567890abcdefgh', 'provider': 'deepseek'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['found']['base_url'], 'https://api.deepseek.com/v1')
        self.assertEqual([m['id'] for m in response.json['models']], ['deepseek-chat', 'deepseek-reasoner'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn(KEY, response.text, 'the saved key is never sent back')

    def test_import_without_a_key_uses_the_saved_key_for_the_same_address_and_reports_listing_trouble(self):
        fetch, calls = canned({})
        with patch.object(mi, 'fetch_json', fetch):
            response = self.client.post('/api/model-settings/import', json={'text': 'https://relay.example.com/v1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0][2], KEY)
        self.assertEqual(response.json['found']['api_key'], '')
        self.assertIsNone(response.json['models'])
        self.assertIn('没有读到模型列表', response.json['found']['notes'][0])
        self.assertEqual(self.client.post('/api/model-settings/import', json={'text': '随便写的'}).status_code, 400)

    def test_model_list_uses_the_form_and_the_saved_key_unless_cleared(self):
        fetch, calls = canned({'https://relay.example.com/v1/models ' + KEY: (200, {'data': [{'id': 'old-model'}, {'id': 'bge-m3'}]})})
        with patch.object(mi, 'fetch_json', fetch):
            listed = self.client.post('/api/model-settings/models', json={
                'channel': 'embedding', 'config': {'base_url': 'https://relay.example.com/v1', 'api_key': ''}})
            self.assertEqual(listed.json['suggested'], {'llm': 'old-model', 'vlm': '', 'embedding': 'bge-m3'})
            self.client.post('/api/model-settings/models', json={
                'channel': 'llm', 'config': {'base_url': 'https://relay.example.com/v1', 'api_key': '', 'clear_key': True}})
        self.assertEqual([c[2] for c in calls[:2]], [KEY, ''])
        self.assertNotIn(KEY, listed.text)
        self.assertEqual(self.client.post('/api/model-settings/models', json={'channel': 'x', 'config': {}}).status_code, 400)

    def test_detection_and_providers_are_offered(self):
        with patch.object(mi, 'detect_local', return_value=[{'name': 'Ollama'}]) as detect:
            self.assertEqual(self.client.post('/api/model-settings/detect').json['services'], [{'name': 'Ollama'}])
        self.assertEqual(detect.call_args.args[0]['llm'], 'old-model')
        providers = self.client.get('/api/model-settings').json['providers']
        self.assertIn('https://dashscope.aliyuncs.com/compatible-mode/v1', [p['base_url'] for p in providers])

    def test_quick_setup_routes_are_local_only(self):
        for path in ('/api/model-settings/import', '/api/model-settings/detect', '/api/model-settings/models'):
            response = self.client.post(path, json={}, headers={'Sec-Fetch-Site': 'cross-site'})
            self.assertEqual(response.status_code, 403, path)


if __name__ == '__main__':
    unittest.main()
