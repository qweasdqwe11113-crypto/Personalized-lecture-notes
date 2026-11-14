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
from jobs import JobQueue
import qwen
import sectioned
from grading import register_grading
from quizzes import QuizQueue
from uploads import read_upload, register_uploads
from classes import ensure_course_structure


def main():
    project = Path(__file__).resolve().parents[2]
    stop = project / '.runtime' / 'stop-workspace-fixture'
    stop.unlink(missing_ok=True)
    CourseJobTests.setUpClass()
    fixture = CourseJobTests()
    try:
        fixture.setUp()
        with fixture.fixture_db() as conn:
            ensure_course_structure(conn)
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
            stack.enter_context(patch.object(module, 'DATA', root))
            stack.enter_context(patch.object(module, 'MODEL', 'fixture-offline'))
            embedder = HashingEmbedder(dimensions=128)
            embedder.configured = lambda: True
            stack.enter_context(patch.object(module, 'EMBEDDER', embedder))
            stack.enter_context(patch('catalog.read_upload', lambda _, identity: read_upload(root, identity)))

            def streamed(text, usage):
                """Imitate a streaming service so the live preview can be exercised offline."""
                listener = qwen.stream_listener()
                if listener:
                    for start in range(0, len(text), 24):
                        listener(text[start:start + 24])
                        time.sleep(.03)
                return module.ModelResult(text, usage)

            def demo_section(title, ctx, low, rich):
                source = f" [M{ctx['materials'][0]['id']}]" if ctx['materials'] else ''
                marks = ''.join(f" [K{s['id']}]" for s in ctx.get('states', [])) if title == '核心讲解' else ''
                probability = '概率' in ctx['topic'].get('course', '')
                tex = (lambda a, b: f'${a}$') if rich else (lambda a, b: b)
                lines = [f'## {title}', f'隔离界面测试内容，用于核查操作流程，不代表真实教学内容。{source}{marks}', '']
                if title == '核心讲解' and probability:
                    lines += ['全概率把次品事件按产线拆开：', '',
                              '$$P(D) = 0.6 \\times 0.02 + 0.4 \\times 0.05 = 0.032$$' if rich else 'P(D) = 0.6 × 0.02 + 0.4 × 0.05 = 0.032', '']
                    if rich:
                        lines += ['```figure', json.dumps({'type': 'probability_tree', 'title': '双产线次品概率树', 'root': '全部产品', 'branches': [
                            {'label': 'A1', 'p': 0.6, 'children': [{'label': 'D', 'p': 0.02}, {'label': '非D', 'p': 0.98}]},
                            {'label': 'A2', 'p': 0.4, 'children': [{'label': 'D', 'p': 0.05}, {'label': '非D', 'p': 0.95}]}]}, ensure_ascii=False), '```', '']
                elif title == '核心讲解':
                    lines += ['解两步方程时，每一步都对等式两边做同样的运算：', '', '```' if not rich else '', '3x + 5 = 20', '3x = 15', 'x = 5', '```' if not rich else '', '']
                    if rich:
                        lines += ['```figure', json.dumps({'type': 'balance', 'title': '两步方程的变形', 'steps': [
                            {'left': '3x + 5', 'right': '20', 'note': '原方程'}, {'left': '3x', 'right': '15', 'note': '两边同减 5'},
                            {'left': 'x', 'right': '5', 'note': '两边同除以 3'}], 'solution': {'x': 5}}, ensure_ascii=False), '```', '']
                elif title == '例题与检验' and probability:
                    lines += ['后验为 ' + tex('P(A_1\\mid D) = \\frac{0.012}{0.032} = 0.375', 'P(A1|D) = 0.012 / 0.032 = 0.375') + '，两项后验之和为 1。', '']
                    if rich:
                        lines += ['| 产线 | 先验 | 后验 |', '|---|---|---|', '| A1 | 0.6 | 0.375 |', '| A2 | 0.4 | 0.625 |', '',
                                  '```figure', json.dumps({'type': 'bar_chart', 'title': '先验与后验', 'max': 1, 'bars': [
                                      {'label': '先验 P(A1)', 'value': 0.6}, {'label': '后验 P(A1|D)', 'value': 0.375},
                                      {'label': '先验 P(A2)', 'value': 0.4}, {'label': '后验 P(A2|D)', 'value': 0.625}]}, ensure_ascii=False), '```', '']
                elif title == '例题与检验':
                    lines += ['检验：把 ' + tex('x = 5', 'x = 5') + ' 代回原方程，' + tex('3 \\times 5 + 5 = 20', '3 × 5 + 5 = 20') + '，左右相等。', '']
                    if rich:
                        lines += ['```figure', json.dumps({'type': 'number_line', 'title': '解在数轴上的位置', 'min': 0, 'max': 8,
                                                         'points': [{'x': 5, 'label': 'x = 5'}]}, ensure_ascii=False), '```', '']
                filler, index = '这一段是占位的讲解步骤，教师提问并等待学生回答，再根据回答追问理由。', 1
                while sectioned.chinese('\n'.join(lines)) < low:
                    lines.append(f'{index}. {filler}')
                    index += 1
                return '\n'.join(line for line in lines) + '\n'

            def model(prepared, attempted):
                attempted()
                time.sleep(.3)
                body = json.loads(json.loads(prepared.body)['messages'][1]['content'])
                if prepared.prompt_version.startswith('lecture-sectioned-section'):
                    ctx = body['context']
                    return streamed(demo_section(body['target_section'], ctx, body['section_quota']['low'],
                                                 ctx.get('lecture_format') == 'rich-v1'), {'total_tokens': 60})
                if '+sectioned-v1' in prepared.prompt_version:
                    return module.ModelResult(json.dumps({'title': '隔离界面测试讲义（分章节）', 'notation': '', 'sections': {
                        name: ['占位要点一', '占位要点二'] for name, _, _ in sectioned.SECTIONS}}, ensure_ascii=False), {'total_tokens': 40})
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
                return streamed('# 隔离界面测试讲义\n\n## 学习目标\n核查操作流程，不代表真实教学内容。\n\n'
                    '## 本次学习安排\n测试响应。\n\n## 核心讲解\n'+marker+' 两边保持相等。'+source+visual+
                    '\n- 公式前的说明\n```\nx + 3 = 5\nx = 2\n```\n- 公式后的检验\n\n'
                    '## 例题与检验\n代回原式检验。\n\n## 易错点与小结\n仍需教师核对。', {'total_tokens': 100})

            stack.enter_context(patch.object(module, 'call_qwen', model))
            # The suite pins one model call at a time; the sandbox shows the parallel default instead.
            module.GENERATION_LOCK.set_limit(3)
            stack.callback(module.GENERATION_LOCK.set_limit, 1)
            parallel = JobQueue(lambda: module.db(), module.execute_job, capacity=6, workers=3)
            stack.enter_context(patch.object(module, 'TASKS', parallel))
            stack.callback(parallel.close)
            stack.callback(parallel.wait_idle, 10)
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
            register_uploads(services, root, module.upload_scope)
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
