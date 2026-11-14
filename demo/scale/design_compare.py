"""Write saved lessons again from a lesson design, with the same frozen inputs, and compare the text measures.

    .venv/bin/python demo/scale/design_compare.py --lectures 12 13            # dry run: request sizes only
    .venv/bin/python demo/scale/design_compare.py --lectures 12 13 --real     # the configured model (real calls, real cost)

Each lesson is read from the database in a read-only session; nothing is written to it. The saved
context of the old lesson (target, topic, materials, learner facts, lesson settings) is reused as
it was frozen then, so the two versions differ in the pipeline and its prompts, not in the input.
Results go to .runtime/design-compare/<time>/ under the data folder: the old text, the new text,
the design, the program check and one table of measures (demo/style.py). That folder is ignored by
git and may hold learner data; do not copy it into the repository.

The measures are text matches. They cannot say which lesson teaches better; a teacher's reading
with the scoring sheet (quality_baseline.py --sheet) decides that.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

DEMO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(DEMO)]

import designed  # noqa: E402
from edition import data_dir  # noqa: E402
from gate import ModelGate  # noqa: E402
from prompts import prepare_request  # noqa: E402
from quality import check_output  # noqa: E402
from quality_baseline import connect, lecture_row, table  # noqa: E402
from teaching import DEFAULTS  # noqa: E402

LEGACY = ('sectioned', 'designed', 'lesson_design')


def design_context(saved):
    """The old lesson's frozen context, switched to the lesson-design pipeline and written straight through."""
    context = {key: value for key, value in saved.items() if key not in LEGACY}
    options = {**DEFAULTS, 'length_policy': 'duration-v2', **(context.get('teaching_options') or {})}
    context.update(lecture_pipeline='v3', generation_mode=designed.MODE, lecture_format='rich-v1', teaching_options=options)
    context.setdefault('personalization_mode', 'profile')
    context.setdefault('personalization_plan', [])
    context['designed'] = designed.freeze(context, stop=False)
    return context


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--lectures', type=int, nargs='+', required=True, help='数据库中的讲义编号（只读）')
    parser.add_argument('--real', action='store_true', help='调用已配置的真实模型；不加时只统计请求大小')
    parser.add_argument('--concurrency', type=int, default=3, help='同时进行的模型调用数（默认 3）')
    args = parser.parse_args()
    with connect() as conn:
        rows = {identity: conn.execute('SELECT content,context,goal,model FROM lectures WHERE id=%s', (identity,)).fetchone()
                for identity in args.lectures}
    missing = [identity for identity, row in rows.items() if not row]
    if missing:
        parser.error('讲义不存在：' + '、'.join(map(str, missing)))
    if args.real:
        from dotenv import load_dotenv
        load_dotenv(data_dir() / '.env')  # the same file the service reads; its values are never printed
        from qwen import call_qwen, config_flags
        if not config_flags()['endpoint_format_ok']:
            parser.error('没有找到可用的模型配置。请用 ZHIYE_DATA_DIR 指向放有 .env 的数据目录。')
    model = os.environ.get('QWEN_MODEL', 'qwen3.8-27b')
    out = data_dir() / '.runtime' / 'design-compare' / datetime.now().strftime('%Y%m%d-%H%M%S')
    measures, summary = [], []
    for identity, row in rows.items():
        context = design_context(row['context'])
        prepared = prepare_request(context, row['goal'], model)
        payload = json.loads(prepared.body)
        sizes = [len(message['content']) for message in payload['messages']]
        print(f'#{identity} {context["topic"]["title"]}：设计请求 系统提示 {sizes[0]} 字符，输入 {sizes[1]} 字符，'
              f'材料 {len(context["materials"])} 条，原讲义模型 {row["model"]}')
        measures.append((f'#{identity} 原', lecture_row(row['content'], row['context'])))
        if not args.real:
            continue
        out.mkdir(parents=True, exist_ok=True)
        gate = ModelGate(max(1, min(8, args.concurrency)))
        gate.acquire()
        replies = []

        def call(request, attempted, replies=replies):
            reply = call_qwen(request, attempted)
            replies.append(dict(version=request.prompt_version, sha256=request.sha256, content=reply.content))
            return reply
        try:
            result = designed.generate(call, gate, prepared, context, row['goal'], lambda: None,
                                       announce=lambda label, n=identity: print(f'  #{n} {label}', flush=True))
        finally:
            gate.release()
        design = result.metadata['design']
        saved = {**context, 'lesson_design': design}
        report = check_output(result.content, saved, row['goal'])
        (out / f'lecture-{identity}-old.md').write_text(row['content'], encoding='utf-8')
        (out / f'lecture-{identity}-new.md').write_text(result.content, encoding='utf-8')
        (out / f'lecture-{identity}-design.json').write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / f'lecture-{identity}-replies.json').write_text(json.dumps(replies, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / f'lecture-{identity}-check.json').write_text(json.dumps(
            dict(automatic=report['automatic'], design_reminders=result.metadata['design_reminders'],
                 stages=result.metadata['stages'], usage=result.usage, model=model), ensure_ascii=False, indent=2), encoding='utf-8')
        measures.append((f'#{identity} 新', lecture_row(result.content, saved)))
        warnings = [check['label'] for check in report['automatic'] if check['status'] != 'pass']
        summary.append(f'#{identity}：{result.usage.get("model_calls")} 次调用，{result.usage.get("total_tokens")} tokens，'
                       f'{result.metadata["elapsed_seconds"]} 秒；程序提醒：{"、".join(warnings) or "无"}')
    print('\n' + table(measures))
    for line in summary:
        print(line)
    if args.real:
        (out / 'measures.json').write_text(json.dumps(dict(measures), ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'measures.md').write_text(table(measures) + '\n\n' + '\n'.join(summary) + '\n', encoding='utf-8')
        print(f'\n结果保存在 {out}（不进入 git）。以上为文本匹配，教学质量需教师阅读后判断。')
    else:
        print('\n这是预演，没有调用模型。加 --real 才会真实生成并产生用量。')


if __name__ == '__main__':
    main()
