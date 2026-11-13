"""Text measures of saved lessons, so two pipeline versions can be compared on the same terms.

    .venv/bin/python demo/scale/quality_baseline.py --lectures 12 13
    .venv/bin/python demo/scale/quality_baseline.py --lectures 12 --deck <课件记录编号> --quiz <题组编号>
    .venv/bin/python demo/scale/quality_baseline.py --files before.md after.md --json out.json
    .venv/bin/python demo/scale/quality_baseline.py --sheet        # empty scoring sheet for a teacher

Reads the database in a read-only session and writes nothing to it. No model is called.

What is counted (see demo/style.py): record-keeping phrases, disclaimers, third-person narration
of the teacher, stock phrases, ordinal scaffolding, quantities spelled in Chinese numerals, and
phrases repeated three times or more. For a lesson and its slides or quiz it also reports how
indices are written in each document and how many slide problems are the lesson's own wording.

These are text matches. They say nothing about correctness or teaching quality, which is what
the scoring sheet is for; a teacher fills that in.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

import style  # noqa: E402

SHEET = [
    ('重点', '读完能说出本课重点和难点，篇幅与时间跟着重点走'),
    ('贴合', '例题、提问、分层对应这门课的资料和这些学生的情况，换一个班不能原样照搬'),
    ('可执行', '各环节时间加起来够用，讲台上扫一眼能知道下一步做什么'),
    ('配套', '学生材料、教师材料、课件里的例题、顺序、符号一致'),
    ('表达', '没有系统话术、免责声明、套话；教师材料像备课笔记，学生材料像学案'),
    ('正确', '推导、答案、适用条件无误（需学科教师判断）'),
]
NUMERALS = dict(zip('零一二两三四五六七八九', (0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))


def number(text):
    """An Arabic or small Chinese numeral (up to 99) as an int, or None."""
    if text.isdigit():
        return int(text)
    if not text or any(c not in NUMERALS and c != '十' for c in text):
        return None
    if '十' not in text:
        return NUMERALS[text] if len(text) == 1 else None
    tens, _, ones = text.partition('十')
    return (NUMERALS.get(tens, 1) if tens else 1) * 10 + (NUMERALS.get(ones, 0) if ones else 0)


def minutes_per_item(content, context):
    """Minutes the schedule gives each worked example or exercise, or None if it cannot be read.

    Lessons written from a lesson design state this exactly. For older lessons it is a guess from
    the schedule sentence that names examples or exercises and the numbered items in the text.
    """
    design = (context or {}).get('lesson_design')
    if design:
        timed = [(segment['minutes'], len(segment.get('items', []))) for segment in design['segments']
                 if segment['kind'] in ('example', 'practice') and segment.get('items')]
        total = sum(n for _, n in timed)
        return round(sum(m for m, _ in timed) / total, 1) if total else None
    schedule = re.search(r'^##\s*本次学习安排\s*$(.*?)(?=^##\s|\Z)', content, re.M | re.S)
    if not schedule:
        return None
    minutes = 0
    for sentence in re.split(r'[。；\n]', schedule[1]):
        # Each "N 分钟" belongs to the words since the previous one in the same sentence.
        last = 0
        for match in re.finditer(r'([0-9]+|[一二两三四五六七八九十]+)\s*分钟', sentence):
            if re.search(r'例|练', sentence[last:match.start()]):
                minutes += number(match[1]) or 0
            last = match.end()
    labels = re.findall(r'(例|练)[题习]?\s*([0-9]{1,2}|[一二三四五六七八九十]+)(?![0-9]|\s*分钟)', content)
    items = len({(kind, number(value)) for kind, value in labels})
    return round(minutes / items, 1) if minutes and items else None


def index_style(text):
    """How indexed symbols such as A1 are written: plain digits, Unicode subscripts or LaTeX."""
    counts = {'plain': len(re.findall(r'(?<![A-Za-z\\_{])[A-Z][0-9k]\b', text)),
              'unicode': len(re.findall(r'[A-Za-z][₀-₉ₐ-ₜ]', text)),
              'latex': len(re.findall(r'[A-Za-z]_\{?[0-9A-Za-z]', text))}
    used = {name: count for name, count in counts.items() if count}
    return max(used, key=used.get) if used else 'none', used


def squeeze(text):
    return re.sub(r'\s+', '', text or '')


def lecture_row(text, context=None):
    report = style.summary(text)
    return dict(chinese=report['chinese'], **report['counts'], repeated_share=report['repeated_share'],
                minutes_per_item=minutes_per_item(text, context), repeated=report['repeated'][:5],
                examples={code: found[:3] for code, found in report['examples'].items() if found})


def companions(lecture, deck=None, quiz=None):
    """Notation and shared-problem measures across a lesson, its slides and its quiz."""
    result = {'lecture_index_style': index_style(lecture)}
    body = squeeze(lecture)
    if deck:
        slides = deck['slides']
        problems = [s['visual']['problem'] for s in slides if s.get('visual', {}).get('problem')]
        text = '\n'.join(json.dumps(s.get('visual', s.get('bullets')), ensure_ascii=False) for s in slides)
        tail = 0
        for slide in reversed(slides):
            if slide.get('visual', {}).get('type') in ('recap', 'table') or slide['kind'] == 'summary':
                tail += 1
            else:
                break
        result['deck'] = dict(slides=len(slides), index_style=index_style(text), problems=len(problems),
                              problems_verbatim=sum(squeeze(p) in body for p in problems),
                              closing_review_slides=tail,
                              layouts=sorted({s.get('visual', {}).get('type', 'legacy') for s in slides}))
    if quiz:
        questions = quiz['questions']
        text = '\n'.join(q['stem'] + q['answer'] + q['analysis'] for q in questions)
        result['quiz'] = dict(questions=len(questions), index_style=index_style(text),
                              stems_verbatim=sum(squeeze(q['stem']) in body for q in questions),
                              self_corrections=sum(bool(re.search(r'等等|让我重新|重新(?:仔细)?计算|更正', q['analysis']))
                                                   for q in questions))
    return result


def connect():
    import psycopg
    from psycopg.rows import dict_row
    dsn = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
    return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5,
                           options='-c default_transaction_read_only=on')


def table(rows):
    head = ['讲义', '汉字', '系统话术', '免责声明', '旁白', '套话', '排比', '汉字数量', '重复占比', '每题分钟']
    lines = ['| ' + ' | '.join(head) + ' |', '| ' + ' | '.join(['---'] + ['---:'] * (len(head) - 1)) + ' |']
    for name, row in rows:
        lines.append('| ' + ' | '.join(str(v) for v in [
            name, row['chinese'], row['system_talk'], row['disclaimer'], row['narration'], row['filler'],
            row['ordinal'], row['chinese_numeral'], f"{row['repeated_share']:.1%}",
            row['minutes_per_item'] if row['minutes_per_item'] is not None else '读不出']) + ' |')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--lectures', type=int, nargs='*', default=[], help='数据库中的讲义编号（只读）')
    parser.add_argument('--files', nargs='*', default=[], help='讲义 Markdown 文件')
    parser.add_argument('--deck', type=int, help='课件的生成记录编号，与第一份讲义对照')
    parser.add_argument('--quiz', help='题组编号，与第一份讲义对照')
    parser.add_argument('--json', help='把全部结果写到这个文件')
    parser.add_argument('--sheet', action='store_true', help='输出一张空的人工评分表')
    args = parser.parse_args()
    if args.sheet:
        print('| 维度 | 看什么 | 0 分 | 1 分 | 2 分 | 评分 | 依据（引原文） |\n| --- | --- | --- | --- | --- | --- | --- |')
        for name, what in SHEET:
            print(f'| {name} | {what} | 明显不符 | 部分做到 | 做到 |  |  |')
        return
    rows, texts, first_context = [], [], None
    if args.lectures or args.deck or args.quiz:
        with connect() as conn:
            for identity in args.lectures:
                row = conn.execute('SELECT content,context FROM lectures WHERE id=%s', (identity,)).fetchone()
                if not row:
                    parser.error(f'讲义 #{identity} 不存在')
                rows.append((f'#{identity}', lecture_row(row['content'], row['context'])))
                texts.append(row['content'])
                first_context = first_context or row['context']
            deck = quiz = None
            if args.deck:
                found = conn.execute("SELECT content FROM generation_runs WHERE id=%s AND context ? 'ppt_export'", (args.deck,)).fetchone()
                deck = json.loads(found['content']) if found else parser.error('课件记录不存在')
            if args.quiz:
                found = conn.execute('SELECT result FROM quiz_jobs WHERE id=%s', (args.quiz,)).fetchone()
                quiz = found['result'] if found and found['result'] else parser.error('题组不存在或未成功')
    else:
        deck = quiz = None
    for name in args.files:
        text = Path(name).read_text(encoding='utf-8')
        rows.append((Path(name).name, lecture_row(text)))
        texts.append(text)
    if not rows:
        parser.error('请用 --lectures 或 --files 指定至少一份讲义')
    print(table(rows))
    for name, row in rows:
        print(f'\n{name} 重复 3 次以上的片段：')
        for phrase, count in row['repeated']:
            print(f'  {count} 次  {phrase}')
        for code, found in row['examples'].items():
            print(f'  {code}：' + '｜'.join(found))
    result = {'style_version': style.VERSION, 'lectures': dict(rows)}
    if deck or quiz:
        result['companions'] = companions(texts[0], deck, quiz)
        print('\n配套对照（与第一份讲义）：')
        print(json.dumps(result['companions'], ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n以上为文本匹配，不能判断正确性与教学质量；人工评分表见 --sheet。')


if __name__ == '__main__':
    main()
