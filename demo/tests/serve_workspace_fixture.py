"""All-page UI sandbox: disposable PostgreSQL, SQLite, configuration and offline models.

Run from the repository root; touch .runtime/stop-workspace-fixture to stop.
No real model endpoint or business data is used. The sandbox expires after 30 minutes.
"""
from contextlib import ExitStack
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import tempfile
import time
from unittest.mock import patch

from flask import Flask
from werkzeug.serving import make_server

from test_courses_jobs import CourseJobTests, module
from test_grading import result_fixture
from test_quizzes import fixture_result
from test_presentations import fixture_deck
from embed import HashingEmbedder
from grading import register_grading
from quizzes import QuizQueue
from uploads import read_upload, register_uploads


def main():
    project = Path(__file__).resolve().parents[2]
    stop = project / '.runtime' / 'stop-workspace-fixture'
    stop.unlink(missing_ok=True)
    CourseJobTests.setUpClass()
    fixture = CourseJobTests()
    try:
        fixture.setUp()
        with tempfile.TemporaryDirectory(prefix='lecture-ui-') as directory, ExitStack() as stack:
            root = Path(directory)
            settings = {'QWEN_BASE_URL': 'https://fixture.invalid/v1', 'QWEN_MODEL': 'fixture-offline',
                        'QWEN_API_KEY': '', 'VLM_BASE_URL': 'https://fixture.invalid/v1',
                        'VLM_MODEL': 'fixture-vision', 'VLM_API_KEY': '',
                        'QWEN_EMBEDDING_BASE_URL': 'https://fixture.invalid/v1',
                        'QWEN_EMBEDDING_MODEL': 'fixture-embedding', 'QWEN_EMBEDDING_API_KEY': ''}
            stack.enter_context(patch.dict(os.environ, settings))
            env = root / '.env'
            env.write_text('\n'.join(f'{key}={value}' for key, value in settings.items()))
            stack.enter_context(patch.dict(module.app.config, MODEL_ENV_FILE=str(env), TEMPLATES_AUTO_RELOAD=True))
            stack.enter_context(patch.object(module, 'ROOT', root))
            stack.enter_context(patch.object(module, 'MODEL', 'fixture-offline'))
            embedder = HashingEmbedder(dimensions=128)
            embedder.configured = lambda: True
            stack.enter_context(patch.object(module, 'EMBEDDER', embedder))
            stack.enter_context(patch('catalog.read_upload', lambda _, identity: read_upload(root, identity)))

            def model(prepared, attempted):
                attempted()
                time.sleep(.3)
                body = json.loads(json.loads(prepared.body)['messages'][1]['content'])
                if prepared.prompt_version.startswith('lecture-ppt'):
                    deck = fixture_deck(body['slide_count'])
                    source = body['sources'][0]['id'] if body['sources'] else None
                    for slide in deck['slides']:
                        slide['sources'] = [source] if source else []
                    return module.ModelResult(json.dumps(deck, ensure_ascii=False))
                if prepared.prompt_version.startswith('quiz-v1'):
                    return module.ModelResult(json.dumps(fixture_result(body['context']), ensure_ascii=False))
                if prepared.prompt_version == 'grading-v1':
                    return module.ModelResult(json.dumps(result_fixture(), ensure_ascii=False))
                if prepared.prompt_version == 'lecture-section-v1':
                    return module.ModelResult('## '+body['target_section']+'\n隔离测试章节候选，供界面操作核查。')
                ctx = body['context']
                source = f"[M{ctx['materials'][0]['id']}]" if ctx['materials'] else ''
                visual = '[V1]' if ctx.get('visual_evidence') else ''
                marker = f"[K{ctx['states'][0]['id']}]" if ctx['states'] else ''
                return module.ModelResult('# 隔离界面测试讲义\n\n## 学习目标\n核查操作流程，不代表真实教学内容。\n\n'
                    '## 本次学习安排\n测试响应。\n\n## 核心讲解\n'+marker+' 两边保持相等。'+source+visual+
                    '\n- 公式前的说明\n```\nx + 3 = 5\nx = 2\n```\n- 公式后的检验\n\n'
                    '## 例题与检验\n代回原式检验。\n\n## 易错点与小结\n仍需教师核对。', {'total_tokens': 100})

            stack.enter_context(patch.object(module, 'call_qwen', model))
            # No PowerPoint here: skip the rendered-page review instead of writing to the real .runtime store.
            stack.enter_context(patch('presentations.refine_presentation', lambda run_id, deck, payload, *args, **kwargs: (
                deck, payload, {'status': 'skipped', 'note': '隔离环境未运行 PowerPoint 视觉审查。'})))
            quizzes = QuizQueue(lambda: module.db(), model, module.GENERATION_LOCK)
            stack.enter_context(patch.object(module, 'QUIZZES', quizzes))
            stack.callback(quizzes.close)

            def vision(image, **_):
                body = json.dumps({'fixture': True, 'image_sha256': image['normalized_sha256']})
                return dict(model='fixture-vision', response_model='fixture-vision',
                            content='隔离识读测试响应：概率树。数值需人工核对。', request_body=body,
                            request_sha256=hashlib.sha256(body.encode()).hexdigest())

            stack.enter_context(patch('vision.analyse', vision))
            stack.enter_context(patch('grading.analyse', vision))
            stack.enter_context(patch('model_settings.probe', return_value={
                'message': '隔离连接测试通过（固定响应，未连接真实服务）。', 'vision_verified': True}))
            services = Flask('isolated-workspace-services')
            register_uploads(services, root)
            register_grading(services, root, model, module.GENERATION_LOCK,
                             quiz_reference=lambda job_id: module.grading_reference(quizzes.detail(job_id)))
            stack.enter_context(patch.dict(module.app.extensions, services.extensions))
            replacements = {name: view for name, view in services.view_functions.items() if name != 'static'}
            stack.enter_context(patch.dict(module.app.view_functions, replacements))
            client = module.app.test_client()
            client.post('/api/uploads', data={'file': (BytesIO('隔离教学资料：两边同加同减，保持相等。'.encode()), '隔离资料.txt')})
            client.post('/api/uploads', data={'file': (BytesIO((project/'demo/tests/fixtures/probability-tree.png').read_bytes()), '概率树.png')})

            server = make_server('127.0.0.1', 5056, module.app, threaded=True)
            server.timeout = .5
            interrupted = False
            def shutdown(*_):
                nonlocal interrupted
                interrupted = True
            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            print('Isolated full workspace: http://127.0.0.1:5056; all model replies are fixtures.', flush=True)
            try:
                deadline = time.monotonic() + 1800
                while not interrupted and not stop.exists() and time.monotonic() < deadline:
                    server.handle_request()
            finally:
                server.server_close()
                fixture.tasks.wait_idle(10)
                quizzes.wait_idle(10)
                deadline = time.monotonic() + 10
                while services.extensions['grading_busy']() and time.monotonic() < deadline:
                    time.sleep(.05)
    finally:
        fixture.tearDown()
        CourseJobTests.tearDownClass()
        stop.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
