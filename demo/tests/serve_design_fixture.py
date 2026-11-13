"""Disposable UI on 5057 for the lesson-design flow. Touch .runtime/stop-design-fixture to stop and clean up.

Fixed replies stand in for the model: one lesson design, then short notes per segment. Nothing
here is real model output, and the temporary schema is dropped on exit.
"""
import json
import os
from pathlib import Path
import time

from werkzeug.serving import make_server
from test_lesson_design import DesignedPipeline, design_reply, module

NOTES = {
    'S1': ('- 先问：已知抽到次品，想知道它来自哪条产线，这是在求哪个方向的条件概率？停 10 秒再点人。\n'
           '- 板书条件概率定义 $P(A_1\\mid D)=\\frac{P(A_1\\cap D)}{P(D)}$，分子用乘法公式展开，分母留空，问“提问 1”。\n'
           '- 关键句：“后验等于先验乘似然，再除以所有来源加起来的总量。” [M13]\n'
           '- 基础弱的学生先让他说出两条产线各自的占比和次品率；学有余力的追问：如果只有一条产线，公式变成什么？'),
    'S2': ('- 读完例1 先让学生判断：0.02 是 $P(D\\mid A_1)$ 还是 $P(A_1\\mid D)$？答错的学生请他指出“已知”的是什么。\n'
           '- 例1 第 1 步教师示范，第 2 步让学生口述；算完马上做归一检查。\n'
           '- 例2 只改先验，让学生先猜后验变大还是变小，再算。'),
}


def model(prepared, attempted):
    attempted()
    time.sleep(1.5)
    user = json.loads(json.loads(prepared.body)['messages'][1]['content'])
    if 'target_segment' not in user:
        reply = design_reply()
        reply['title'] = '贝叶斯公式（隔离测试，非真实模型输出）'
        if user.get('teacher_feedback'):
            reply['focus']['not_today'].append('按意见调整：' + user['teacher_feedback'][:40])
        return module.ModelResult(json.dumps(reply, ensure_ascii=False), {'total_tokens': 1200})
    return module.ModelResult(NOTES.get(user['target_segment'], '- 巡视时看分母是否写全。'), {'total_tokens': 400})


if __name__ == '__main__':
    stop = Path(__file__).resolve().parents[2] / '.runtime' / 'stop-design-fixture'
    stop.parent.mkdir(exist_ok=True)
    stop.unlink(missing_ok=True)
    DesignedPipeline.setUpClass()
    fixture = DesignedPipeline()
    fixture.setUp()
    module.call_qwen = model
    module.MODEL = 'fixture-design-only'
    os.environ['QWEN_API_KEY'] = 'fixture-only'
    os.environ['QWEN_BASE_URL'] = 'https://fixture.invalid/v1'
    server = make_server('127.0.0.1', 5057, module.app, threaded=True)
    server.timeout = 1
    try:
        print('Isolated lesson-design UI: http://127.0.0.1:5057; temporary schema=' + fixture.schema, flush=True)
        deadline = time.monotonic() + 1800
        while not stop.exists() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
        fixture.tearDown()
        DesignedPipeline.tearDownClass()
        stop.unlink(missing_ok=True)
