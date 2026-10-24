"""Disposable UI on 5056. Touch .runtime/stop-teaching-fixture to stop and clean up."""
import os
from pathlib import Path
import time
import json

from werkzeug.serving import make_server
from test_teaching_sections import TeachingSectionTests, module, ORIGINAL

if __name__ == '__main__':
    stop = Path(__file__).resolve().parents[2] / '.runtime' / 'stop-teaching-fixture'
    stop.unlink(missing_ok=True)
    TeachingSectionTests.setUpClass()
    fixture = TeachingSectionTests()
    fixture.setUp()
    def model(prepared, attempted):
        attempted()
        time.sleep(1)
        if prepared.prompt_version == 'lecture-section-v1':
            user = json.loads(json.loads(prepared.body)['messages'][1]['content'])
            return module.ModelResult('## '+user['target_section']+'\n这是隔离测试的章节候选，不是真实模型输出。\n### 课堂提问\n为什么两边要做相同运算？\n预期回答：保持相等。[M1]')
        return module.ModelResult(ORIGINAL.replace('授课讲义', '隔离测试授课讲义（测试响应）'))
    module.call_qwen = model
    module.MODEL = 'fixture-teaching-only'
    os.environ['QWEN_API_KEY'] = 'fixture-only'
    os.environ['QWEN_BASE_URL'] = 'https://fixture.invalid/v1'
    server = make_server('127.0.0.1', 5056, module.app, threaded=True)
    server.timeout = 1
    try:
        print('Isolated teaching UI: http://127.0.0.1:5056; temporary schema='+fixture.schema, flush=True)
        deadline=time.monotonic()+1200
        while not stop.exists() and time.monotonic()<deadline:
            server.handle_request()
    finally:
        server.server_close()
        fixture.tearDown()
        TeachingSectionTests.tearDownClass()
        stop.unlink(missing_ok=True)
