"""A blind set across evalset runs: the same lessons generated under different conditions (for example
--retrieval sql / hybrid / none, each its own run), lettered A, B, C in a random order per lesson.

    .venv/bin/python demo/scale/blind_conditions.py sql=<run folder> hybrid=<run folder> none=<run folder> \
        --cases L1 L2 P1 P2 --out experiments/2026-11-26-rag/blind

Citation markers ([M编号], [V编号]) and the program-written 「**依据**　[M…]」 lines are removed from every
copy, since a lecture made without material has none and would be recognised at a glance; whether the
citations hold is judged separately (judge_citations.py). The key goes in key.json beside the copies.
"""
import argparse
import json
from pathlib import Path
import random
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quality_baseline import SHEET  # noqa: E402

SOURCE_LINE = re.compile(r'^\*\*依据\*\*[\s　]*(?:\[[MV]\d+\][\s　、,，]*)+$')
MARKER = re.compile(r'[ \t　]*\[[MVK]\d+\]')


def unmarked(text):
    lines = [line for line in text.splitlines() if not SOURCE_LINE.match(line.strip())]
    return '\n'.join(MARKER.sub('', line) for line in lines) + '\n'


def lesson_file(folder, case):
    found = [path for path in sorted(folder.glob(f'{case}-*.md')) if not path.name.endswith('-failed.md')]
    if len(found) != 1:
        raise SystemExit(f'{folder.name} 里 {case} 的讲义有 {len(found)} 份，应为 1 份。')
    return found[0]


def build(conditions, cases, out, chooser=random.SystemRandom()):
    out.mkdir(parents=True, exist_ok=False)
    titles = {}
    for folder in conditions.values():
        results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
        titles.update({case['id']: case for case in results['cases']})
    key = {}
    sheet = ['# 盲评评分表', '', f'每节课有 {len(conditions)} 份讲义（' + '、'.join('ABCD'[:len(conditions)]) +
             '），分别打分，不要打开 key.json。每项 0 分明显不符，1 分部分做到，2 分做到；「依据」处引一句原文。',
             '', '各份讲义里的资料编号（[M编号]）和「依据」行已删去，引用是否站得住另有评审。', '']
    for case in cases:
        order = list(conditions)
        chooser.shuffle(order)
        key[case] = {}
        for letter, name in zip('ABCD', order):
            source = lesson_file(conditions[name], case)
            (out / f'{case}-{letter}.md').write_text(unmarked(source.read_text(encoding='utf-8')), encoding='utf-8')
            key[case][letter] = dict(condition=name, source=f'{conditions[name].name}/{source.name}')
        letters = 'ABCD'[:len(order)]
        info = titles.get(case, {})
        sheet += [f'## {case} {info.get("title", "")}（{info.get("duration", "?")} 分钟）', '',
                  '| 维度 | 看什么 | ' + ' | '.join(f'{letter} 评分' for letter in letters) + ' | 依据 |',
                  '| --- | --- | ' + ' | '.join('---' for _ in letters) + ' | --- |']
        sheet += [f'| {name} | {what} | ' + ' | '.join('' for _ in letters) + ' |  |' for name, what in SHEET] + ['']
    (out / 'key.json').write_text(json.dumps(key, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (out / '评分表.md').write_text('\n'.join(sheet), encoding='utf-8')
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('conditions', nargs='+', help='条件名=evalset 结果目录，如 hybrid=.runtime/evalset/20261126-014718')
    parser.add_argument('--cases', nargs='+', required=True)
    parser.add_argument('--out', required=True, help='盲评目录（须不存在）')
    args = parser.parse_args()
    conditions = dict(item.split('=', 1) for item in args.conditions)
    if not 2 <= len(conditions) <= 4:
        raise SystemExit('需要 2—4 个条件。')
    build({name: Path(folder) for name, folder in conditions.items()}, args.cases, Path(args.out))
    print(f'已写入 {args.out}：{len(args.cases)} 节课 × {len(conditions)} 份，key.json 另存。')


if __name__ == '__main__':
    main()
