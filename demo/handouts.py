"""The student handout and the teacher's book of a lesson written from a lesson design.

Both are laid out by the program from the saved design (design.py), so the handout shows the same
problems, in the same order and with the same symbols as the teacher's book and, later, the
slides. No model is called here.

The handout never carries answers, source markers, learner information or teaching notes unless
the answer version is asked for; that version is for the teacher, for absent students and for
review. The teacher's book wraps the saved lesson text of the version being read with an answer
key and a page for notes after class.
"""
import json
import re

import design as D

LINES = '<!-- lines:{} -->'       # ruled space to write on; packages.markdown_docx draws it
PAGE_BREAK = '<!-- pagebreak -->'
SPACE = {'warmup': 2, 'practice': 4, 'homework': 5}   # ruled lines per problem; one more for an advanced one


def _clean(text):
    """A string as a student reads it: no source markers."""
    return re.sub(r'[ \t]*\[[MV]\d+\]', '', text)


def _hint(why, stem):
    """A step's reason as a hint on the blank handout: a formula carrying a number the problem does not give
    (「$P(D)=0.044>0$」, a result of an earlier step) would hand the student the answer, so it is left out."""
    given = D._numbers(stem) | {0, 1}
    kept = re.sub(r'\$[^$]*\$', lambda m: m[0] if D._numbers(m[0]) <= given else '', _clean(why))
    return re.sub(r'\s+', ' ', re.sub(r'[，,；;、：:\s]+$|^[，,；;、：:\s]+', '', kept)).strip()


def _problem(design, item, answers, worked, heading=''):
    """One problem of the handout. worked: show the full solution even in the blank version."""
    name = D.labels(design)[item['id']]
    level = f'（{D.LEVELS[item["level"]]}）' if item['role'] != 'example' or item['level'] != 'basic' else ''
    title = item['title'] if item['title'] and item['title'] not in heading else ''   # not a second time under its own heading
    lines = [f'### {name}{level}' + (f' {title}' if title else ''), '', '**题目**　' + _clean(item['stem'])]
    steps = [f'{n}. {_clean(step["do"])}' + (f'　——{_clean(step["why"])}' if step['why'] else '')
             for n, step in enumerate(item['steps'], 1)]
    if answers or worked:
        if steps:
            lines += ['', '**解答**', '', *steps]
        lines += ['', '**答案**　' + _clean(item['answer'])]
        if item['check']:
            lines += ['', '**检验**　' + _clean(item['check'])]
        if answers and item['mistake']:
            lines += ['', '**注意**　' + _clean(item['mistake'])]
    elif item['role'] == 'example' and item['steps']:
        # A later example keeps the reason for each step and leaves the step itself to the student.
        lines += ['', '**解答**（按提示补全每一步）', '']
        room = 2 if len(item['steps']) <= 4 else 1   # a long solution would otherwise fill a page with rules
        for n, step in enumerate(item['steps'], 1):
            hint = _hint(step['why'], item['stem'])
            lines += [f'{n}. ' + (f'（{hint}）' if hint else ''), LINES.format(room)]
        lines += ['', '**答案**　____________']
    else:
        lines += ['', LINES.format(SPACE.get(item['role'], 5) + (1 if item['level'] == 'advanced' else 0))]
    return '\n'.join(lines)


def student_handout(design, answers=False):
    """The handout as Markdown, in classroom order. answers=True fills every gap and adds the solutions."""
    bank = {item['id']: item for item in design['items']}
    one = design.get('audience') == 'student'   # one-to-one tutoring: no class to write down
    parts = [f'# {design["title"]} · 学案' + ('（含答案）' if answers else ''),
             ('' if one else '班级：____________　') + '姓名：____________　日期：____________',
             '## 学习目标', '\n'.join(f'{n}. {_clean(o["text"])}' for n, o in enumerate(design['objectives'], 1))]
    first_example = next((item['id'] for segment in design['segments'] for ref in segment['items']
                          for item in [bank[ref]] if item['role'] == 'example'), None)
    for index, segment in enumerate(design['segments']):
        items = [bank[ref] for ref in segment['items'] if bank[ref]['role'] != 'homework']
        notes = segment.get('notes', [])
        figures = [figure for figure in design['figures'] if figure['segment'] == segment['id']]
        closing = segment['kind'] == 'summary'
        if not (items or notes or figures or closing or (segment['kind'] in ('concept', 'intro') and segment['board'])):
            continue  # nothing of this segment belongs on the student's sheet
        number = D.ORDINALS[index] if index < len(D.ORDINALS) else str(index + 1)
        block = [f'## {number}、{D.segment_title(segment)}']
        if notes:
            block.append('\n'.join('- ' + _clean(D.filled(note) if answers else D.open_gaps(note['text'])) for note in notes))
        elif segment['kind'] in ('concept', 'intro') and segment['board']:
            block.append('\n'.join('- ' + _clean(line) for line in segment['board']))
        for figure in figures:
            block.append('```figure\n' + json.dumps(D.figure_spec(design, figure['spec']), ensure_ascii=False) + '\n```')
        block += [_problem(design, item, answers, worked=item['id'] == first_example, heading=block[0]) for item in items]
        if closing:
            block.append('\n'.join('- ' + _clean(point) for point in design['summary']) if answers
                         else '这节课我学会了什么，还有哪里不清楚：\n' + LINES.format(3))
        parts.append('\n\n'.join(block))
    if not any(segment['kind'] == 'summary' for segment in design['segments']):
        parts += ['## 小结', '\n'.join('- ' + _clean(point) for point in design['summary']) if answers
                  else '这节课我学会了什么，还有哪里不清楚：\n' + LINES.format(3)]
    homework = [item for item in design['items'] if item['role'] == 'homework']
    if homework:
        parts += ['## 课后作业', *[_problem(design, item, answers, worked=False) for item in homework]]
    return D.humanize(design, '\n\n'.join(parts)) + '\n'


def answer_key(design):
    """Every gap and every problem on one page, for marking and for a quick look at the board."""
    names = D.labels(design)
    cell = lambda text: re.sub(r'\s+', ' ', text).replace('|', '\\|') or '—'
    lines = ['## 答案速查', '']
    notes = [(segment, note) for segment in design['segments'] for note in segment.get('notes', []) if note['answer']]
    if notes:
        lines += ['**学案填空**', ''] + [f'- {D.filled(note)}' for _, note in notes] + ['']
    lines += ['| 题目 | 答案 | 易错 |', '| --- | --- | --- |']
    order = [ref for segment in design['segments'] for ref in segment['items']]
    order += [item['id'] for item in design['items'] if item['id'] not in order]
    bank = {item['id']: item for item in design['items']}
    for ref in order:
        item = bank[ref]
        lines.append(f'| {names[ref]}（{D.LEVELS[item["level"]]}） | {cell(item["answer"])} | {cell(item["mistake"])} |')
    return D.humanize(design, '\n'.join(lines))


def after_class(design):
    """A page to write on after the lesson; the prompts follow this lesson's objectives and warm-up."""
    names = D.labels(design)
    one = design.get('audience') == 'student'   # one student: whether, not how many
    lines = ['## 课后记录', '', '**实际用时与调整**（哪个环节超时、删了什么）', LINES.format(3), '',
             '**学生表现**', '']
    for item in design['items']:
        if item['role'] == 'warmup':
            lines.append(f'- {names[item["id"]]}：' + ('□ 答对　□ 答错' if one else '答对 ____ 人 / 共 ____ 人'))
    for number, objective in enumerate(design['objectives'], 1):
        checked = '、'.join(names[ref] for ref in objective['checked_by'])
        lines.append(f'- 目标 {number}' + (f'（看{checked}）' if checked else '') + '：'
                     + ('□ 达成　□ 部分达成　□ 未达成' if one else '达成 ____ 人') + '；主要问题：____________')
    lines += ['', '**教学反思**', LINES.format(5)]
    return '\n'.join(lines)


def lesson_body(content):
    """The saved lesson text without its own title line; the book has one."""
    return re.sub(r'\A\s*#[ \t]+[^\n]*\n+', '', content, count=1).strip()


def with_slide_pages(design, body, pages):
    """The run sheet with the slides each segment uses. A sheet the teacher edited in a revision is
    left as written, and the page numbers follow it in a table of their own."""
    if not pages:
        return body
    plain, numbered = D.schedule(design), D.schedule(design, pages)
    if plain in body:
        return body.replace(plain, numbered, 1)
    rows = ['**课件页码**', '', '| 环节 | 课件 |', '| --- | --- |']
    rows += [f'| {D.segment_title(segment)} | {pages.get(segment["id"]) or "—"} |' for segment in design['segments']]
    return body + '\n\n' + D.humanize(design, '\n'.join(rows))


def teacher_book(design, content, facts, pages=None, sources=()):
    """The teacher's book as Markdown. facts: (label, value) rows describing this lesson and version;
    pages: {segment id: 「第 3—4 页」} when the package carries the slides built from the same design;
    sources: (code, what it is) of the materials the text cites, listed after it so a printed 「[M96]」 reads."""
    cell = lambda text: re.sub(r'\s+', ' ', str(text if text not in (None, '') else '—')).replace('|', '\\|')
    table = ['| 项目 | 内容 |', '| --- | --- |'] + [f'| {label} | {cell(value)} |' for label, value in facts]
    cited = ['**资料对照**（正文里方括号中的编号）', '', '| 编号 | 资料 |', '| --- | --- |'] + \
        [f'| {code} | {cell(label)} |' for code, label in sources]
    return '\n\n'.join(part for part in [
        f'# {design["title"]} · 教师用书', '\n'.join(table),
        with_slide_pages(design, lesson_body(content), pages), '\n'.join(cited) if sources else '',
        PAGE_BREAK, answer_key(design), PAGE_BREAK, after_class(design)] if part) + '\n'
