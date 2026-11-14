"""Slides of a lesson written from a lesson design, planned page by page. No model call.

The deck follows the design's segments in classroom order and takes every problem, step,
answer, note and figure from the design, so it shows the same problems, in the same order and
with the same symbols as the student handout and the teacher's book (handouts.py). What goes on
a page:

- warm-up and practice: the problems, then a page of answers;
- new material: the handout's fill-in notes with gaps, then the same notes filled in;
- an example: the problem, then the worked solution (continued on another page when long);
- summary and homework, and a figure on a page of its own where the design has one.

Students see no source markers, AI notices or category labels. Teaching notes, questions with
expected answers and the review reminder go to the speaker notes and to a last page that is
hidden during the slide show. Page numbers are fixed here, before drawing, so the teacher's book
can print them in its run sheet.
"""
import re

import design as D
import lesson_layout as L
import mathtext

VERSION = 'lesson-slides-v1'
HEADINGS = {'warmup': '课前回顾', 'intro': '导入', 'concept': '新知', 'example': '例题', 'practice': '课堂练习',
            'summary': '课堂小结', 'homework': '课后作业'}
SIZES = dict(cover=(26, 24, 22, 20), points=(32, 30, 28, 26, 24), problems=(32, 30, 28, 26, 24),
             steps=(28, 26, 24, 22), answers=(30, 28, 26, 24, 22), summary=(30, 28, 26, 24, 22), homework=(30, 28, 26, 24, 22, 20))
_VERB = re.compile(r'^(?:屏幕上?)?(?:展示|显示|呈现|播放|出示|投影|放映|给出)[：:]?\s*')


def _choose(sizes, need, room):
    """The largest size whose text fits the room, or None."""
    return next((size for size in sizes if need(size) <= room), None)


def _split(values, need, room, sizes, limit=None):
    """Greedy pages of consecutive values, each at the largest size that fits it."""
    pages, current = [], []
    for value in values:
        trial = current + [value]
        if current and (limit and len(trial) > limit or _choose(sizes, lambda s: need(trial, s), room) is None):
            pages.append(current)
            current = [value]
        else:
            current = trial
    if current:
        pages.append(current)
    return [(group, _choose(sizes, lambda s, g=group: need(g, s), room) or sizes[-1]) for group in pages]


def _balanced(rows, count, need):
    """rows cut into count consecutive pages whose tallest page is as short as possible."""
    if count == 1:
        return [rows]
    best = None
    cuts = [(a,) for a in range(1, len(rows))] if count == 2 else \
        [(a, b) for a in range(1, len(rows) - 1) for b in range(a + 1, len(rows))]
    for cut in cuts:
        edges = (0, *cut, len(rows))
        groups = [rows[edges[n]:edges[n + 1]] for n in range(count)]
        tallest = max(need(group, n == count - 1) for n, group in enumerate(groups))
        if best is None or tallest < best[0]:
            best = (tallest, groups)
    return best[1] if best else None


class _Deck:
    def __init__(self, design):
        self.design = design
        self.names = D.labels(design)
        self.bank = {item['id']: item for item in design['items']}
        self.slides, self.pages = [], {}

    # ------------------------------------------------------------ text
    def shown(self, text):
        """A string as students see it: display names instead of ids, no source markers."""
        return re.sub(r'[ \t]*\[[MV]\d+\]', '', D.humanize(self.design, text or '')).strip()

    def said(self, text):
        """A string for the speaker notes: plain Unicode, ids replaced, sources kept."""
        return mathtext.plain(D.humanize(self.design, text or '')).strip()

    def notes_of(self, note, filled):
        """A handout note as a slide line: gaps left open, or filled in with the answers in order."""
        text = D.gap_outside(self.shown(note['text']))
        gaps = len(D.BLANK.findall(text))
        if not filled or not gaps:
            return dict(text=text, answers=None)
        answers = [self.shown(answer) for answer in D.note_answers(note)]
        if gaps == len(answers):
            return dict(text=text, answers=answers)
        whole = self.shown(D.as_formula(note['answer']))
        if gaps == 1:
            return dict(text=text, answers=[whole])
        return dict(text=text + '（答：' + whole + '）', answers=None)

    def label(self, item):
        level = f'（{D.LEVELS[item["level"]]}）' if item['role'] in ('practice', 'homework') else ''
        return self.names[item['id']] + level

    # ------------------------------------------------------------ pages
    def position(self, page):
        """Index of this very page (two pages may hold equal content)."""
        return next(index for index, slide in enumerate(self.slides) if slide is page)

    def add(self, segment, layout, title, **fields):
        heading = fields.pop('heading', HEADINGS.get(segment['kind'], '') if segment else '')
        page = dict(layout=layout, segment=segment['id'] if segment else None, heading=heading,
                    title=self.shown(title), **fields)
        self.slides.append(page)
        return page

    def problems(self, segment, items, title, question='', labelled=True):
        room = L.ROOM - (L.QUESTION if question else 0)
        entries = [dict(label=self.label(item) if labelled else '', stem=self.shown(item['stem'])) for item in items]
        made = []
        for group, size in _split(entries, L.problems_need, room, SIZES['problems'], limit=2):
            made.append(self.add(segment, 'problems', title, items=group, size=size, question='' if made else question))
        return made

    def answers(self, segment, items, title):
        rows = [dict(label=self.names[item['id']], answer=self.shown(item['answer'])) for item in items]
        return [self.add(segment, 'answers', title + ('（续）' if index else ''), rows=group, size=size)
                for index, (group, size) in enumerate(_split(rows, L.answers_need, L.ROOM, SIZES['answers']))]

    def solution(self, segment, item):
        name = self.names[item['id']]
        rows = [dict(number=n, do=self.shown(step['do']), why=self.shown(step['why']))
                for n, step in enumerate(item['steps'], 1)]
        tails = [line for line in ('答案：' + self.shown(item['answer']),
                                   '检验：' + self.shown(item['check']) if item['check'] else '',
                                   '易错：' + self.shown(item['mistake']) if item['mistake'] else '') if line]
        if not rows:
            return [self.add(segment, 'answers', f'{name} 解答', rows=[dict(label=name, answer=self.shown(item['answer']))],
                             size=_choose(SIZES['answers'], lambda s: L.answers_need([dict(answer=item['answer'])], s), L.ROOM) or 18)]
        # One page at a readable size if possible; otherwise two or three pages of about equal height at one
        # common size, with the answer (then the check, then the common mistake) on the last page.
        for count in range(1, min(3, len(rows)) + 1):
            sizes = SIZES['steps'][:3] if count == 1 else SIZES['steps']
            for keep in (len(tails), 2, 1):
                tail = tails[:keep]
                for size in sizes:
                    groups = _balanced(rows, count, lambda group, last: L.steps_need(group, size, tail if last else ()))
                    if groups and all(L.steps_need(group, size, tail if n == count - 1 else ()) <= L.ROOM
                                      for n, group in enumerate(groups)):
                        return [self.add(segment, 'steps', f'{name} 解答' + ('（续）' if n else ''), rows=group,
                                         tail=tail if n == count - 1 else [], size=size) for n, group in enumerate(groups)]
        made, groups = [], _split(rows, L.steps_need, L.ROOM, SIZES['steps'])
        for index, (group, size) in enumerate(groups):
            made.append(self.add(segment, 'steps', f'{name} 解答' + ('（续）' if index else ''), rows=group, tail=[], size=size))
        made.append(self.add(segment, 'answers', f'{name} 答案', rows=[dict(label=name, answer=self.shown(item['answer']))], size=22))
        return made

    def bullets(self, segment, title, points, question='', layout='points'):
        room = L.ROOM - (L.QUESTION if question else 0)
        made = []
        for group, size in _split(points, lambda values, s: L.points_need(values, s), room, SIZES['points']):
            made.append(self.add(segment, layout, title + ('（续）' if made else ''), points=group, size=size,
                                 question='' if made else question))
        return made

    def figure_spec(self, spec):
        """The figure's data with display names in its words (「例1 后验」, not 「E1 后验」); numbers untouched."""
        if isinstance(spec, dict):
            return {key: self.shown(value) if key in ('title', 'label', 'root', 'note') and isinstance(value, str)
                    else self.figure_spec(value) for key, value in spec.items()}
        if isinstance(spec, list):
            return [self.figure_spec(value) for value in spec]
        return spec

    def homework(self, segment):
        items = [item for item in self.design['items'] if item['role'] == 'homework']
        if not items:
            return []
        groups = [(name, [dict(label=self.names[item['id']], stem=self.shown(item['stem']), title=self.shown(item['title']))   # the group names the level
                          for item in items if item['level'] == level])
                  for level, name in (('basic', '基础（必做）'), ('advanced', '提高（选做）'))]
        groups = [(name, entries) for name, entries in groups if entries]
        size = _choose(SIZES['homework'], lambda s: L.homework_need(groups, s), L.ROOM)
        if size:
            return [self.add(segment, 'homework', '课后作业', groups=groups, full=True, size=size)]
        size = _choose(SIZES['homework'], lambda s: L.homework_need(groups, s, full=False), L.ROOM - 40) or 18
        return [self.add(segment, 'homework', '课后作业', groups=groups, full=False, size=size,
                         note='完整题目见学案「课后作业」。')]

    def segment(self, segment):
        kind, title = segment['kind'], segment['title']
        items = [self.bank[ref] for ref in segment['items'] if self.bank[ref]['role'] != 'homework']
        ask = self.shown(segment['questions'][0]['ask']) if segment['questions'] else ''
        made = []
        if kind == 'homework':
            return self.homework(segment)
        if kind == 'summary':
            points = [self.shown(point) for point in self.design['summary']]
            size = _choose(SIZES['summary'], lambda s: L.summary_need(points, s), L.ROOM) or 20
            made.append(self.add(segment, 'summary', title, points=points, size=size))
        notes = segment.get('notes') or []
        if notes:
            key = [self.notes_of(note, True) for note in notes]
            for page in self.bullets(segment, title, [self.notes_of(note, False) for note in notes], ask):
                made.append(page)
                page.update(fill='blank', key=key)
            for page in self.bullets(segment, title + '（填空答案）', key):
                made.append(page)
                page['fill'] = 'answers'
            ask = ''
        elif kind in ('intro', 'concept') or (kind not in ('summary',) and not items):
            lines = [self.shown(line) for line in segment['board']] or [_VERB.sub('', self.shown(segment['screen']))]
            lines = [line for line in lines if line]
            if lines:
                made += self.bullets(segment, title, lines, ask)
                ask = ''
        examples = [item for item in items if item['role'] == 'example']
        others = [item for item in items if item['role'] != 'example']
        for item in examples:
            made += self.problems(segment, [item], f'{self.names[item["id"]]} {item["title"]}', ask, labelled=False)
            ask = ''
            made += self.solution(segment, item)
        if others:
            heading = title if kind != 'practice' or '练' in title else f'{HEADINGS[kind]}：{title}'
            made += self.problems(segment, others, heading, ask)
            made += self.answers(segment, others, f'{heading} · 答案')
        figures = [self.figure_spec(entry['spec']) for entry in self.design['figures'] if entry['segment'] == segment['id']]
        for number, spec in enumerate(figures):
            page = dict(layout='figure', segment=segment['id'], heading=HEADINGS[kind],
                        title=self.shown(spec.get('title') or title), figure=spec, question='')
            # Right after the page it illustrates: the first page of the segment.
            position = self.position(made[0]) + 1 + number if made else len(self.slides)
            self.slides.insert(position, page)
            made.insert(number + 1 if made else 0, page)
        return made


def _segment_notes(deck, segment, index):
    design = deck.design
    lines = [f'第 {index + 1} 环节 · {D.KINDS[segment["kind"]]} · {segment["minutes"]} 分钟' + ('（重点）' if segment['focus'] else ''),
             f'教师用书：{D.ORDINALS[index] if index < len(D.ORDINALS) else index + 1}、{D.segment_title(segment)}']
    if segment['teacher']:
        lines.append('教师活动：' + '；'.join(deck.said(move).rstrip('。；;') for move in segment['teacher']))
    if segment['students']:
        lines.append('学生活动：' + deck.said(segment['students']))
    for question in segment['questions']:
        parts = [f'提问：{deck.said(question["ask"])}']
        parts += [f'{label}：{deck.said(question[key])}' for key, label in
                  (('expect', '预期回答'), ('wrong', '常见错答'), ('follow_up', '追问')) if question.get(key)]
        lines.append('；'.join(parts))
    if segment['board']:
        lines.append('板书：' + '；'.join(deck.said(line) for line in segment['board']))
    if segment['if_short']:
        lines.append('时间不够时：' + deck.said(segment['if_short']))
    return lines


def _page_notes(deck, page):
    bank = {deck.names[item['id']]: item for item in deck.design['items']}
    lines = []
    if page['layout'] in ('problems', 'answers', 'steps'):
        labels = ([page['title'].split(' ')[0]] if page['layout'] == 'steps'
                  else [entry['label'] for entry in page.get('items', page.get('rows', []))])
        for label in labels:
            item = bank.get(re.sub(r'（.*?）$', '', str(label)))
            if not item:
                continue
            parts = [f'{deck.names[item["id"]]} 答案：{deck.said(item["answer"])}']
            if item['check']:
                parts.append('检验：' + deck.said(item['check']))
            if item['mistake']:
                parts.append('易错：' + deck.said(item['mistake']))
            if item['sources']:
                parts.append('依据材料：' + '、'.join(item['sources']))
            lines.append('；'.join(parts))
    if page.get('fill') == 'answers':
        lines.append('这一页是学案填空的答案。')
    elif page.get('fill') == 'blank':
        lines.append('学生在学案上填空；下一页给出答案：')
        lines += ['· ' + mathtext.plain(point['text'], point['answers']) for point in page['key']]
    if page['layout'] == 'homework':
        for _, entries in page['groups']:
            for entry in entries:
                item = bank.get(re.sub(r'（.*?）$', '', entry['label']))
                if item:
                    lines.append(f'{entry["label"]} 答案：{deck.said(item["answer"])}')
    return lines


REVIEW = '内容由 AI 生成，程序只核对了结构、时间、数量和编号；上课前请核对推导、答案和提问。'


def plan(design, label='', lines=()):
    """The deck as data: pages with their content and font size, the speaker notes, and the pages of each segment.

    label: the footer text (the lesson title by default); lines: short facts printed on the cover."""
    deck = _Deck(design)
    deck.add(None, 'cover', design['title'], heading='课堂讲解', goals=[deck.shown(o['text']) for o in design['objectives']],
             lines=[deck.shown(line) for line in lines] + ['本课重点：' + '；'.join(deck.shown(t) for t in design['focus']['key_points'])],
             size=_choose(SIZES['cover'], lambda s: L.stack([deck.shown(o['text']) for o in design['objectives']], s, 420 - L.INDENT, s * 0.7), 440) or 18)
    deck.slides[0]['notes'] = '\n'.join(
        [f'重点：{"；".join(deck.said(t) for t in design["focus"]["key_points"])}'] +
        ([f'难点：{"；".join(deck.said(t) for t in design["focus"]["difficulties"])}'] if design['focus']['difficulties'] else []) +
        ([f'本课不展开：{"；".join(deck.said(t) for t in design["focus"]["not_today"])}'] if design['focus']['not_today'] else []) +
        ([f'学情与对策：{deck.said(design["learner"])}'] if design['learner'] else []) + ['', REVIEW])
    homework_placed = False
    for index, segment in enumerate(design['segments']):
        made = deck.segment(segment)
        homework_placed |= segment['kind'] == 'homework' and bool(made)
        if made:
            numbers = [deck.position(page) + 1 for page in made]
            deck.pages[segment['id']] = [min(numbers), max(numbers)]
            head = _segment_notes(deck, segment, index)
            for position, page in enumerate(made):
                own = _page_notes(deck, page)
                page['notes'] = '\n'.join((head if position == 0 else [f'第 {index + 1} 环节（续）'])
                                          + ([''] + own if own else []))
            for page in made:
                page['progress'] = [len(design['segments']), index]
    if not homework_placed:
        made = deck.homework(None)
        for page in made:
            page.update(heading='课后作业', progress=[len(design['segments']), len(design['segments'])],
                        notes='\n'.join(['课后作业（布置时说明必做和选做）', ''] + _page_notes(deck, page)))
        if made:
            deck.pages['homework'] = [len(deck.slides)] * 2
    visible = len(deck.slides)
    deck.add(None, 'notice', '授课前请核对（放映时不显示）', hidden=True, lines=[
        REVIEW, '题目、步骤和答案与学生学案、教师用书取自同一份课堂设计，逐字相同；改动题目时三份材料要一起改。',
        '公式写成可编辑的文字（上下标、分式写作 a/b），可以直接在 PowerPoint 或 WPS 里修改。',
        f'页面由程序排版（{VERSION}），没有调用模型。'],
        notes=REVIEW)
    for page in deck.slides:
        page.setdefault('notes', '')
        page.setdefault('progress', [len(design['segments']), -1])
    return dict(version=VERSION, title=deck.shown(design['title']), label=deck.shown(label or design['title']),
                slides=deck.slides, pages=deck.pages, visible=visible)


def page_range(deck, segment_id):
    """「第 3—4 页」 / 「第 5 页」 for the run sheet, or '' when the segment has no slide."""
    pages = deck['pages'].get(segment_id)
    if not pages:
        return ''
    return f'第 {pages[0]} 页' if pages[0] == pages[1] else f'第 {pages[0]}—{pages[1]} 页'


def outline(deck):
    """What each page shows, as plain text, for the web preview and for tests."""
    def flat(value):
        if isinstance(value, list):
            return ''.join(flat(piece) for piece in value)
        if isinstance(value, dict):
            return mathtext.plain(value['text'], value.get('answers'))
        return mathtext.plain(value)
    result = []
    for number, page in enumerate(deck['slides'], 1):
        body = []
        if page['layout'] == 'cover':
            body = [flat(goal) for goal in page['goals']]
        elif page['layout'] in ('points', 'summary'):
            body = [flat(point) for point in page['points']]
        elif page['layout'] == 'problems':
            body = [(f'{entry["label"]}　' if entry['label'] else '') + flat(entry['stem']) for entry in page['items']]
        elif page['layout'] == 'answers':
            body = [f'{row["label"]}　{flat(row["answer"])}' for row in page['rows']]
        elif page['layout'] == 'steps':
            body = [f'{row["number"]}. {flat(row["do"])}' + (f'（{flat(row["why"])}）' if row['why'] else '') for row in page['rows']]
            body += [flat(line) for line in page.get('tail', [])]
        elif page['layout'] == 'homework':
            body = [f'{entry["label"]}　{flat(entry["stem"] if page["full"] else entry["title"])}'
                    for _, entries in page['groups'] for entry in entries]
        elif page['layout'] == 'figure':
            body = ['【图】' + flat(page['title'])]
        elif page['layout'] == 'notice':
            body = list(page['lines'])
        if page.get('question'):
            body.append('想一想：' + flat(page['question']))
        result.append(dict(number=number, layout=page['layout'], heading=page['heading'], title=flat(page['title']),
                           body=body, notes=page['notes'], hidden=bool(page.get('hidden'))))
    return result
