"""Disposable UI on 5057 for the lesson-design flow. Touch .runtime/stop-design-fixture to stop and clean up.

Fixed replies stand in for the model: one lesson design, short notes per segment, and a companion
quiz whose first stem writes P(A1|D) the way the handout does not. Nothing here is real model
output, and the temporary schema is dropped on exit.
"""
import json
import os
from pathlib import Path
import time

from werkzeug.serving import make_server
from test_lesson_design import DesignedPipeline, module, with_notes

NOTES = {
    'S1': ('- 先问：已知抽到次品，想知道它来自哪条产线，这是在求哪个方向的条件概率？停 10 秒再点人。\n'
           '- 板书条件概率定义 $P(A_1\\mid D)=\\frac{P(A_1\\cap D)}{P(D)}$，分子用乘法公式展开，分母留空，问“提问 1”。\n'
           '- 关键句：“后验等于先验乘似然，再除以所有来源加起来的总量。” [M13]\n'
           '- 基础弱的学生先让他说出两条产线各自的占比和次品率；学有余力的追问：如果只有一条产线，公式变成什么？'),
    'S2': ('- 读完例1 先让学生判断：0.02 是 $P(D\\mid A_1)$ 还是 $P(A_1\\mid D)$？答错的学生请他指出“已知”的是什么。\n'
           '- 例1 第 1 步教师示范，第 2 步让学生口述；算完马上做归一检查。\n'
           '- 例2 只改先验，让学生先猜后验变大还是变小，再算。'),
}


def quiz(context):
    questions = []
    for p in context['question_plan']:
        q = {**p, 'stem': f'（隔离测试题 {p["number"]}）先验从 0.3 升到 0.6 时，后验怎样变化？', 'options': [],
             'answer': '变大', 'analysis': f'对应学习目标 {p.get("objective", 2)}。后验与先验同向变化，与练3同类。',
             'common_mistake': '把似然当成后验。', 'source_ids': []}
        if p['number'] == 1:
            q.update(stem='两条产线的产量占比为 0.7、0.3，次品率为 0.01、0.04。抽到一件次品，求 P(A1|D)。',
                     answer='$\\frac{7}{19}\\approx 0.368$',
                     analysis=f'对应学习目标 {p.get("objective", 1)}。$P(D) = 0.7 \\times 0.01 + 0.3 \\times 0.04 = 0.019$，'
                              '$P(A_1\\mid D) = \\frac{0.007}{0.019}$，与例1同类。')
        if context.get('replace'):
            q.update(stem=f'（隔离测试换题 {p["number"]}）某检测的灵敏度为 0.9，患病率为 0.01，检测阳性时真正患病的概率是多少？',
                     answer='约 0.083', options=[])
        if p['type'] == 'single_choice':
            q.update(options=['$P(D\\mid A_1)$', '$P(A_1\\mid D)$', '$P(A_1)$', '$P(D)$'], answer='B',
                     stem=f'（隔离测试题 {p["number"]}）已知抽到次品，求它来自第一条产线的概率，应求哪一个？')
        questions.append(q)
    return {'questions': questions}


def model(prepared, attempted):
    attempted()
    time.sleep(1.5)
    user = json.loads(json.loads(prepared.body)['messages'][1]['content'])
    if 'question_plan' in user.get('context', {}):
        return module.ModelResult(json.dumps(quiz(user['context']), ensure_ascii=False), {'total_tokens': 900})
    if 'target_segment' not in user:
        reply = with_notes()
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
