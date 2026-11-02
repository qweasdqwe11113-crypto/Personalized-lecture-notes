"""Isolated PPT UI on 5056; fixed responses, temporary PostgreSQL schema."""
import json
from pathlib import Path
import time
from unittest.mock import patch

from werkzeug.serving import make_server
from test_presentations import PresentationIntegrationTests, fixture_deck, fixture_context, module
from pptx_export import build_pptx


if __name__ == '__main__':
    stop = Path(__file__).resolve().parents[2] / '.runtime' / 'stop-ppt-fixture'
    stop.unlink(missing_ok=True)
    PresentationIntegrationTests.setUpClass()
    fixture = PresentationIntegrationTests()
    fixture.setUp()
    # Also create a local export for actual PowerPoint rendering checks.
    deck = fixture_deck()
    deck['slides'][1]['title'] = '等式两边进行相同运算的依据与检验方法'
    deck['slides'][1]['bullets'] = [
        '等式的两边必须同时进行相同运算，才能保持原来的相等关系。注意运算对象、符号与结果。',
        '从 x + 3 = 5 开始，在等式两边同时减去 3。',
        '得到 x = 2，再把结果代回原方程进行检验。',
        '检验时分别计算等式左右两边，确认左右两边的值相等。']
    context = fixture_context()
    (stop.parent / 'ppt-fixture.pptx').write_bytes(build_pptx(deck, context['ppt_export'], context))

    def model(prepared, attempted):
        attempted(); time.sleep(1)
        if prepared.prompt_version in {'lecture-ppt-v1', 'lecture-ppt-v2', 'lecture-ppt-v3'}:
            user = json.loads(json.loads(prepared.body)['messages'][1]['content'])
            return module.ModelResult(json.dumps(fixture_deck(user['slide_count']), ensure_ascii=False))
        return module.ModelResult('# 隔离测试讲义\n等式两边进行相同运算。 [M3]')

    server = make_server('127.0.0.1', 5056, module.app, threaded=True)
    server.timeout = 1
    try:
        with patch.object(module, 'call_qwen', model):
            print('Isolated PPT UI: http://127.0.0.1:5056; fixed test responses only.', flush=True)
            deadline = time.monotonic()+1200
            while not stop.exists() and time.monotonic() < deadline:
                server.handle_request()
    finally:
        server.server_close(); fixture.tearDown(); PresentationIntegrationTests.tearDownClass()
        stop.unlink(missing_ok=True)
