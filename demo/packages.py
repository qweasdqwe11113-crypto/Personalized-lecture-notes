"""Teacher lesson package: one saved lecture version, its frozen inputs, an optional quiz and deck.

Nothing is regenerated here. Every part is read from saved records, labelled with its review
state and fingerprinted. The handbook is for the teacher; the worksheet carries neither answers
nor learner information. Program checks in the package never stand in for teacher review.
"""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import re

import handouts
import richdoc
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Response, jsonify, request

from presentations import lesson_pptx, presentation_pptx
from ppt_templates import DEFAULT_TEMPLATE, available_templates
from profiles import SOURCE_LABELS, STATE_LABELS
from quality import check_output
from quizzes import DIFFICULTIES, LEVELS, TYPES, lecture_topic_key, option_lines, quiz_markdown
from reviews import STATUS_LABELS, ReviewError, lecture_row, review_bundle, version_row
from teaching import DETAILS, LESSON_TYPES

PACKAGE_VERSION = 'lesson-package-v1'
DESIGNED_VERSION = 'lesson-package-v2'   # a lesson written from a lesson design: handout, teacher's book, record
FONT = 'Noto Sans SC'
PAGE_BREAK = '<!-- pagebreak -->'
VERSION_STATUS = {**STATUS_LABELS, 'unreviewed': '原稿未审核'}
ISSUE_LABELS = {'math': '数学或学科内容', 'terminology': '术语', 'citation_support': '引用支持',
                'scope': '范围', 'structure': '结构', 'other': '其他'}


def version_label(number):
    return '原稿' if not number else f'R{number}'


def oneline(value):
    return re.sub(r'\s+', ' ', str(value if value not in (None, '') else '—')).strip()


def cell(value):
    """Markdown table cell on one line; pipes stay as written (P(A|B)) through the standard escape."""
    return re.sub(r'\s+', ' ', str(value if value not in (None, '') else '—')).strip().replace('|', '\\|')


def demote(content, levels=2):
    """Nest the lecture's own headings below the handbook section; fenced text is untouched."""
    lines, fence = [], None
    for raw in content.splitlines():
        token = raw.strip()[:3]
        if token in ('```', '~~~'):
            fence = None if fence == token else fence or token
        elif fence is None and re.match(r'^#{1,6}\s', raw.strip()):
            raw = '#' * levels + raw.strip()
        lines.append(raw)
    return '\n'.join(lines)


# ---------------------------------------------------------------- collection

def load_quiz(conn, identity, lecture_id, topic_key):
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise ReviewError('请选择有效的题组。')
    row = conn.execute('SELECT * FROM quiz_jobs WHERE id=%s', (identity,)).fetchone()
    if not row:
        raise ReviewError('题组不存在。', 404)
    if row['status'] != 'succeeded' or sha256(row['request_body'].encode()).hexdigest() != row['request_sha256']:
        raise ReviewError('题组尚未生成成功或输入校验不一致，不能加入授课包。', 409)
    linked = row['context'].get('lecture')
    if linked and linked['id'] != lecture_id:
        raise ReviewError('该题组配套的是另一份讲义，请重新选择。')
    if not linked and row['topic_key'] != topic_key:
        raise ReviewError('该题组不属于这份讲义的备课副本，请重新选择。')
    return row


def collect(db, lecture_id, number, quiz_id=None, presentation_id=None, slides_template=None):
    """slides_template: build the slides of a designed lecture from its design on this template (no model call)."""
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        lecture = lecture_row(conn, lecture_id)
        version = version_row(conn, lecture, number)
        bundle = review_bundle(conn, lecture_id)
        topic_key = lecture_topic_key(conn, lecture)
        quiz = load_quiz(conn, quiz_id, lecture_id, topic_key) if quiz_id else None
    deck = None
    if presentation_id is not None:
        run, slides, payload = presentation_pptx(db, presentation_id)
        if run['context']['ppt_export']['lecture_id'] != lecture_id:
            raise ReviewError('该课件不是由这份讲义生成的，请重新选择。')
        deck = dict(run=run, slides=slides, pptx=payload)
    elif slides_template is not None:
        plan, template, payload = lesson_pptx(lecture, slides_template)
        deck = dict(design=True, plan=plan, template=template, pptx=payload)
    report = lecture['citation_check'].get('quality_report') if number == 0 else version.get('quality_report')
    origin = '生成时留存' if number == 0 else '修订保存时留存'
    if not report:
        report = check_output(version['content'], lecture['context'], lecture['goal'])
        origin = '导出时按保存版本检查，未改写原记录'
    return dict(lecture=lecture, version=version, bundle=bundle, topic_key=topic_key, quiz=quiz,
                deck=deck, report=report, report_origin=origin)


# ---------------------------------------------------------------- handbook text

def overview(parts):
    lecture, version, quiz, deck = parts['lecture'], parts['version'], parts['quiz'], parts['deck']
    ctx = lecture['context']
    topic = ctx.get('topic', {})
    student = ctx.get('student', {})
    profiled = ctx.get('personalization_mode', 'profile') != 'none'
    options = ctx.get('teaching_options') or {}
    retrieval = ctx.get('retrieval') or {}
    lines = ['## 一、授课概览', '', '| 项目 | 内容 |', '| --- | --- |']
    rows = [('课程', ' / '.join(filter(None, [topic.get('stage'), topic.get('course'), topic.get('chapter')])) or topic.get('course')),
            ('课题', topic.get('title', lecture['topic'])), ('教学目标', lecture['goal']),
            ('授课对象', f"{student.get('name')}（{student.get('subtitle', '')}）" if profiled else '通用讲义（无画像对照，未使用个体诊断）'),
            ('讲义版本', f"#{lecture['id']} / {version_label(version['revision_number'])} · {VERSION_STATUS.get(version['status'], version['status'])}"),
            ('生成模型', f"{lecture['model']} · {lecture['created_at']:%Y-%m-%d %H:%M}"),
            ('材料取用', '向量检索（按目标选取片段）' if retrieval.get('mode') == 'vector' else '精确 SQL（全部关联材料）')]
    if options:
        rows.append(('授课设置', f"{options.get('duration')} 分钟 · {LESSON_TYPES.get(options.get('lesson_type'), '—')} · "
                                 f"{DETAILS.get(options.get('detail'), '—')} · 例题 {options.get('examples')} 道 · 练习 {options.get('exercises')} 道"))
    else:
        rows.append(('授课设置', '历史记录未保存授课设置'))
    if quiz:
        o = quiz['options']
        linked = quiz['context'].get('lecture')
        relation = (f"配套本讲义 {version_label(linked['revision'])}" if linked else '按同一备课副本生成，未关联本讲义')
        rows.append(('配套练习', f"{o['count']} 题 · {DIFFICULTIES[o['difficulty']]} · {LEVELS[o['student_level']]} · {relation}"))
    else:
        rows.append(('配套练习', '未选择题组'))
    if deck and deck.get('design'):
        rows.append(('授课课件', deck_label(deck, version['revision_number'])))
    elif deck:
        source = deck['run']['context']['ppt_export']
        rows.append(('授课课件', f"#{deck['run']['id']} · {len(deck['slides']['slides'])} 页 · {source.get('template_name', '旧版版式')} · "
                                 f"依据 {version_label(source['base_revision'])}"))
    else:
        rows.append(('授课课件', '未选择课件'))
    lines.extend(f'| {k} | {cell(v)} |' for k, v in rows)
    if version['revision_number']:
        lines.extend(['', f"修订记录：{version.get('recorder', '')}，{version.get('created_at'):%Y-%m-%d %H:%M}；理由：{version.get('reason', '')}"])
    return lines


def learner_section(parts):
    ctx = parts['lecture']['context']
    lines = ['## 二、学生档案与本次讲解安排（生成时快照）', '']
    if ctx.get('personalization_mode', 'profile') == 'none' or not ctx.get('states'):
        lines.append('这份讲义为无画像对照或未使用个体诊断，模型输入不含学生信息。')
        note = (ctx.get('teaching_options') or {}).get('requirements')
        if note:
            lines.extend(['', '本次备注：' + note])
        return lines
    student = ctx['student']
    lines.extend([f"**{student['name']}** · {student.get('subtitle', '')}", '', student.get('profile', ''), '',
                  '> 以下为生成讲义时的画像快照，仅供教师备课参考，请勿分发给学生；当前画像可能已更新。', ''])
    plans = {p['knowledge_id']: p for p in ctx.get('personalization_plan', [])}
    lines.extend(['| 知识点 | 掌握状态 | 诊断依据 | 本次讲解安排 |', '| --- | --- | --- | --- |'])
    for state in ctx['states']:
        plan = plans.get(state['id'], {})
        action = plan.get('action') or state.get('strategy', '')
        if plan.get('teacher_modified'):
            action += '（教师已调整）'
        lines.append(f"| {cell(state['name'])} | {cell(STATE_LABELS.get(state['state'], state['state']))} | "
                     f"{cell(state.get('evidence'))} | {cell(action)} |")
    warnings = [f"{p['name']}：{w}" for p in plans.values() for w in p.get('warnings', [])]
    if warnings:
        lines.extend(['', '证据提醒：'] + ['- ' + w for w in warnings])
    evidence = [(s['name'], e) for s in ctx['states'] for e in s.get('recent_evidence', [])]
    if evidence:
        lines.extend(['', '### 近期学习证据', ''])
        for name, e in evidence:
            observed = (e.get('observed_at') or '观察时间未记录')[:10]
            lines.append(f"- {name} · {SOURCE_LABELS.get(e.get('source'), e.get('source'))} · {observed} · "
                         f"{e.get('recorder', '')}：{oneline(e.get('performance'))}")
    if ctx.get('learner_note'):
        lines.extend(['', '本次学生观察：' + ctx['learner_note']])
    note = (ctx.get('teaching_options') or {}).get('requirements')
    if note:
        lines.extend(['', '本次备注：' + note])
    return lines


def lecture_section(parts):
    version = parts['version']
    return [PAGE_BREAK, f"## 三、讲义正文（{version_label(version['revision_number'])}）", '',
            '> 正文中的 [M编号] 指课程材料，[V编号] 指图片解析，[K编号] 只用于定位本次讲解安排。', '',
            demote(version['content'])]


def quiz_section(parts):
    quiz = parts['quiz']
    if not quiz:
        return []
    names = {**{str(s['id']): s['name'] for s in quiz['context'].get('states', [])}, **quiz['context'].get('knowledge_names', {})}
    lines = [PAGE_BREAK, '## 四、配套练习（教师版：含答案与解析）', '',
             '> 题目、答案与难度为 AI 生成，使用前须教师逐题复核。学生版另见「学生练习」文件。', '']
    for q in quiz['result']['questions']:
        lines.extend([f"### 第 {q['number']} 题 · {TYPES[q['type']]} · {DIFFICULTIES[q['difficulty']]}", '',
                      f"考查知识点：{names.get(str(q['knowledge_id']), q['knowledge_id'])}", '', q['stem'], ''])
        lines.extend(option_lines(q['options']))
        lines.extend(['', '**参考答案**：' + q['answer'], '', '**解析**：' + q['analysis'], '',
                      '**易错点**：' + q['common_mistake'], '',
                      '材料依据：' + ('、'.join(q['source_ids']) if q['source_ids'] else '自编题目，未标注直接材料依据，请复核。'), ''])
    return lines


def sources_section(parts):
    ctx = parts['lecture']['context']
    lines = [PAGE_BREAK, '## 五、课程材料与来源（生成时快照）', '']
    retrieval = ctx.get('retrieval') or {}
    if retrieval.get('mode') == 'vector':
        lines.extend([f"向量检索问句：{retrieval.get('query')}；排序：{retrieval.get('backend')}；嵌入模型：{retrieval.get('embed_model')}。"
                      '相似度不等于内容支持。', ''])
    budget = ctx.get('input_budget') or {}
    if budget.get('omitted_material_ids'):
        lines.extend(['因字符预算未纳入的材料：' + '、'.join(f'M{i}' for i in budget['omitted_material_ids']), ''])
    if not ctx.get('materials'):
        lines.append('本次未提供课程参考材料，讲义来源不足，需教师补充核对。')
    for m in ctx.get('materials', []):
        lines.extend([f"### [M{m['id']}] {m.get('title', '')}", '',
                      f"来源：{m.get('source_title', '')} · {m.get('source_url', '')} · {m.get('section', '')}"])
        if m.get('selection'):
            lines.append(f"（节选：原文 {m['selection']['original_characters']} 字符，按目标选取片段）")
        if m.get('max_score') is not None:
            lines.append(f"（向量召回 {m.get('chunk_count')} 段，最高相似度 {m['max_score']}）")
        lines.extend(['', '~~~', m.get('content', ''), '~~~', ''])
    for item in ctx.get('visual_evidence', []):
        lines.extend([f"### [{item['citation_id']}] 图片解析 · {item.get('label', '图片')}", '',
                      f"识读模型：{item.get('model', '未记录')}；图片 SHA-256：{item.get('normalized_sha256', '未记录')}；需核对。",
                      '', '~~~', item['content'], '~~~', ''])
    return lines


def review_section(parts):
    report, bundle, version = parts['report'], parts['bundle'], parts['version']
    lines = ['## 六、审核状态与待处理事项', '',
             f"本版本审核状态：{VERSION_STATUS.get(version['status'], version['status'])}。程序检查（{parts['report_origin']}）：", '']
    for check in report.get('automatic', []):
        lines.append(f"- {'通过' if check['status'] == 'pass' else '提醒'} · {check['label']}：{check['detail']}")
    pending = [i for i in bundle['issues'] if i['id'] in set(bundle['pending_issue_ids'])]
    lines.extend(['', '待处理问题：' if pending else '待处理问题：无已记录的待处理问题。'])
    for issue in pending:
        lines.append(f"- [{ISSUE_LABELS.get(issue['category'], issue['category'])} · {version_label(issue['target_revision'])}] "
                     f"“{oneline(issue['excerpt'])[:80]}”：{oneline(issue['detail'])}（{issue['recorder']}）")
    lines.extend(['', '教师授课前核对：'] + [f"- [ ] {item['label']}" for item in report.get('teacher_review', [])])
    if parts['quiz']:
        lines.append('- [ ] 配套练习的题意、答案、难度与材料依据')
    if parts['deck']:
        lines.append('- [ ] 课件公式、答案是否提前泄露及来源')
    lines.extend(['', '说明：本手册汇总已保存记录，不重新生成内容；程序检查只核对结构、篇幅与编号，'
                  '不能证明学科正确性、来源支持或教学效果。学生均为虚拟数据时不代表真实学习结果。'])
    return lines


def handbook_markdown(parts):
    lecture = parts['lecture']
    title = lecture['context'].get('topic', {}).get('title', lecture['topic'])
    lines = [f'# {title} · 教师授课手册', '',
             f"授课包版本 {PACKAGE_VERSION} · 汇总时间 {datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M}", '',
             '> 讲义、题目和课件为 AI 生成或教师修订的待审核内容，请复核后使用。学生档案部分仅供教师参考。', '']
    for section in (overview, learner_section, lecture_section, quiz_section, sources_section, review_section):
        block = section(parts)
        if block:
            lines.extend(block + [''])
    return '\n'.join(lines).rstrip() + '\n'


def deck_label(deck, revision=0):
    """A deck built from the lesson design: how many pages, on which template. The design is the one the
    lecture was written from, so wording changed in a revision is not on the slides."""
    label = f"按课堂设计排版 · {deck['plan']['visible']} 页 · {deck['template']['name']}（课件页码见课堂流程表）"
    return label + ('；修订稿改动的文字不在课件里，请对照修改' if revision else '')


def slide_pages(parts):
    """{segment id: 「第 3—4 页」} of the slides in this package, when they were built from the design."""
    import lesson_slides
    deck = parts['deck']
    if not deck or not deck.get('design'):
        return None
    return {segment: lesson_slides.page_range(deck['plan'], segment) for segment in deck['plan']['pages']}


def designed(parts):
    """The lesson design this lecture was written from, or None for lectures of the older pipelines."""
    return parts['lecture']['context'].get('lesson_design')


def book_facts(parts):
    lecture, version, quiz, deck = parts['lecture'], parts['version'], parts['quiz'], parts['deck']
    ctx = lecture['context']
    topic, student, options = ctx.get('topic', {}), ctx.get('student', {}), ctx.get('teaching_options') or {}
    profiled = ctx.get('personalization_mode', 'profile') != 'none'
    rows = [('课程', ' / '.join(filter(None, [topic.get('stage'), topic.get('course'), topic.get('chapter')])) or topic.get('course')),
            ('教学目标', lecture['goal']),
            ('授课对象', f"{student.get('name')}（{student.get('subtitle', '')}）" if profiled else '通用（未使用学生信息）'),
            ('课时', f"{options.get('duration')} 分钟 · {LESSON_TYPES.get(options.get('lesson_type'), '—')}" if options else '未记录'),
            ('讲义版本', f"#{lecture['id']} / {version_label(version['revision_number'])} · {VERSION_STATUS.get(version['status'], version['status'])}")]
    if deck:
        rows.append(('配套课件', deck_label(deck, version['revision_number']) if deck.get('design') else f"#{deck['run']['id']} · {len(deck['slides']['slides'])} 页"))
    if quiz:
        rows.append(('配套练习', f"{quiz['options']['count']} 题，另见「学生练习」与「练习-教师版」"))
    return rows


def record_markdown(parts):
    """Provenance and review of a designed lesson, kept apart from what the teacher takes to class."""
    lecture = parts['lecture']
    title = lecture['context'].get('topic', {}).get('title', lecture['topic'])
    lines = [f'# {title} · 生成记录与核对', '',
             f"授课包版本 {DESIGNED_VERSION} · 汇总时间 {datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M}", '',
             '> 这份文件说明教师用书和学生学案是依据什么生成的，以及授课前要核对什么。学生档案部分仅供教师参考。', '']
    for section in (overview, learner_section, sources_section, review_section):
        block = [line for line in section(parts) if line != PAGE_BREAK]
        if block:
            lines.extend(block + [''])
    return '\n'.join(lines).rstrip() + '\n'


# ---------------------------------------------------------------- Word rendering

def _fonts(style):
    from docx.oxml.ns import qn
    style.font.name = FONT
    style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), FONT)


def _ruled(paragraph):
    """One empty line of a block to write on. Paragraphs with the same "between" border get a rule
    between each pair, so a block of N+1 of them shows N evenly spaced rules. (A plain bottom border
    will not do: Word and LibreOffice box identical neighbours and keep only the last rule.)"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    paragraph.paragraph_format.space_before, paragraph.paragraph_format.space_after = Pt(10), Pt(0)
    border = OxmlElement('w:pBdr')
    rule = OxmlElement('w:between')
    for key, value in (('val', 'single'), ('sz', '4'), ('space', '1'), ('color', '9AA3B2')):
        rule.set(qn('w:' + key), value)
    border.append(rule)
    paragraph._p.get_or_add_pPr().append(border)


def _inline(paragraph, text, italic=False):
    richdoc.add_inline(paragraph, text, italic)  # bold plus $...$ as native Word formulas


def markdown_docx(text):
    """Conservative Markdown → Word: headings, lists, tables, quotes, fenced text, page breaks."""
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Cm, Pt, RGBColor
    document = Document()
    for name in ('Normal', 'Title', 'Heading 1', 'Heading 2', 'Heading 3', 'Heading 4', 'List Bullet'):
        _fonts(document.styles[name])
    document.styles['Normal'].font.size = Pt(10.5)
    lines, index, fence = text.splitlines(), 0, None
    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        index += 1
        if fence is not None:
            if line.startswith(fence):
                fence = None
                continue
            run = document.add_paragraph().add_run(raw)
            run.font.name, run.font.size = 'Courier New', Pt(9.5)
            continue
        if re.fullmatch(r'```figure\s*', line):
            body = []
            while index < len(lines) and not lines[index].strip().startswith('```'):
                body.append(lines[index])
                index += 1
            index += 1
            richdoc.add_figure(document, '\n'.join(body).strip())
            continue
        if line[:3] in ('```', '~~~'):
            fence = line[:3]
            continue
        if not line:
            continue
        if line.startswith('$$'):
            body = [line[2:]]
            while not body[-1].endswith('$$') and index < len(lines):
                body.append(lines[index].strip())
                index += 1
            richdoc.add_display_math(document, '\n'.join(body).removesuffix('$$').strip())
            continue
        if line == PAGE_BREAK:
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            continue
        ruled = re.fullmatch(r'<!-- lines:(\d{1,2}) -->', line)
        if ruled:
            for _ in range(int(ruled[1]) + 1):
                _ruled(document.add_paragraph())
            continue
        if line.startswith('|'):
            rows = [line]
            while index < len(lines) and lines[index].strip().startswith('|'):
                rows.append(lines[index].strip())
                index += 1
            cells = [[c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', r[1:-1] if r.endswith('|') else r[1:])]
                     for r in rows if not re.fullmatch(r'\|?[\s:|-]+\|?', r)]
            table = document.add_table(rows=len(cells), cols=max(map(len, cells)))
            table.style = 'Table Grid'
            if len(table.columns) == 2:
                widths = [3.2, 12.8]
            else:
                # Share 16 cm by the longest text of each column, within bounds, so prose columns get room.
                weights = [min(34, max(7, max((len(re.sub(r'[$\\\\{}]', '', values[c])) for values in cells if c < len(values)), default=5)))
                           for c in range(len(table.columns))]
                widths = [16 * weight / sum(weights) for weight in weights]
            # Word reads the cell widths, LibreOffice the column grid: set both, and stop either from re-fitting.
            table.autofit = False
            for c, width in enumerate(widths):
                table.columns[c].width = Cm(width)
                for row in table.rows:
                    row.cells[c].width = Cm(width)
            for r, values in enumerate(cells):
                for c, value in enumerate(values):
                    paragraph = table.cell(r, c).paragraphs[0]
                    _inline(paragraph, value)
                    if r == 0:
                        for run in paragraph.runs:
                            run.bold = True
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading:
            document.add_heading(heading.group(2).strip(), min(len(heading.group(1)), 4))
            continue
        bullet = re.match(r'^[-*]\s+(.+)$', line)
        if bullet:
            _inline(document.add_paragraph(style='List Bullet'), bullet.group(1))
            continue
        if line.startswith('>'):
            paragraph = document.add_paragraph()
            _inline(paragraph, line.lstrip('>').strip(), italic=True)
            for run in paragraph.runs:
                run.font.color.rgb = RGBColor(0x55, 0x60, 0x73)
            continue
        _inline(document.add_paragraph(), line)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


# ---------------------------------------------------------------- package

def documents(parts):
    """Markdown of each document of a designed lesson: teacher's book, handout, handout with answers, record."""
    design = designed(parts)
    return dict(teacher=handouts.teacher_book(design, parts['version']['content'], book_facts(parts), slide_pages(parts)),
                handout=handouts.student_handout(design), key=handouts.student_handout(design, answers=True),
                record=record_markdown(parts))


def build_package(parts):
    lecture, version, quiz, deck = parts['lecture'], parts['version'], parts['quiz'], parts['deck']
    label = version_label(version['revision_number'])
    if designed(parts):
        docs = documents(parts)
        files = [('教师用书.docx', markdown_docx(docs['teacher'])), ('教师用书.md', docs['teacher'].encode('utf-8')),
                 ('学生学案.docx', markdown_docx(docs['handout'])), ('学生学案.md', docs['handout'].encode('utf-8')),
                 ('学生学案-含答案.docx', markdown_docx(docs['key'])),
                 ('生成记录与核对.docx', markdown_docx(docs['record']))]
    else:
        handbook = handbook_markdown(parts)
        files = [('教师授课手册.docx', markdown_docx(handbook)), ('教师授课手册.md', handbook.encode('utf-8')),
                 (f'讲义正文-{label}.md', version['content'].encode('utf-8'))]
    if quiz:
        files.append(('学生练习.docx', markdown_docx(quiz_markdown(quiz, False))))
        files.append(('练习-教师版.docx', markdown_docx(quiz_markdown(quiz, True))))
    if deck:
        files.append(('授课课件.pptx', deck['pptx']))
    manifest = dict(
        package_version=DESIGNED_VERSION if designed(parts) else PACKAGE_VERSION, generated_at=datetime.now(timezone.utc).isoformat(),
        lecture=dict(id=lecture['id'], revision=version['revision_number'], status=version['status'],
                     content_sha256=sha256(version['content'].encode('utf-8')).hexdigest(), model=lecture['model']),
        quiz=dict(id=quiz['id'], request_sha256=quiz['request_sha256'],
                  lecture=(quiz['context'].get('lecture') or {}).get('id')) if quiz else None,
        presentation=(dict(built_from='lesson_design', version=deck['plan']['version'], template=deck['template']['id'],
                           pages=deck['plan']['visible']) if deck and deck.get('design') else
                      dict(run_id=deck['run']['id'], request_sha256=deck['run']['request_sha256'],
                           template=deck['run']['context']['ppt_export'].get('template_id')) if deck else None),
        files=[dict(name=name, bytes=len(data), sha256=sha256(data).hexdigest()) for name, data in files],
        note='所有内容读取自已保存记录；AI 生成内容须教师复核。学生练习不含答案与学生信息。'
             + ('学生学案与教师用书由同一份课堂设计排版，题目逐字相同；「含答案」的学案和教师用书不要发给学生。' if designed(parts) else ''))
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
        archive.writestr('授课包清单.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return stream.getvalue(), manifest


def package_options(db, lecture_id):
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        lecture = lecture_row(conn, lecture_id)
        key = lecture_topic_key(conn, lecture)
        quizzes = conn.execute('''SELECT id,created_at,options,(context->'lecture'->>'id')::bigint AS lecture_id,
            (context->'lecture'->>'revision')::int AS lecture_revision FROM quiz_jobs WHERE status='succeeded'
            AND (context->'lecture'->>'id'=%s OR (NOT (context ? 'lecture') AND topic_key=%s))
            ORDER BY created_at DESC LIMIT 30''', (str(lecture_id), key)).fetchall()
        decks = conn.execute('''SELECT r.id AS run_id,r.created_at,r.context->'ppt_export'->>'base_revision' AS base_revision,
            r.context->'ppt_export'->>'slide_count' AS slide_count,r.context->'ppt_export'->>'template_name' AS template_name
            FROM generation_runs r WHERE r.status='succeeded' AND r.context->'ppt_export'->>'lecture_id'=%s
            AND (NOT (r.context ? 'ppt_slide_edit') OR r.response_metadata->>'edit_state'='accepted')
            ORDER BY r.id DESC LIMIT 30''', (str(lecture_id),)).fetchall()
    designed_lecture = bool(lecture['context'].get('lesson_design'))
    templates = ([dict(id=item['id'], name=item['name']) for item in available_templates() if item['semantic']]
                 if designed_lecture else [])
    return dict(quizzes=quizzes, presentations=decks, designed=designed_lecture, slide_templates=templates,
                default_template=DEFAULT_TEMPLATE)


def register_packages(app, db):
    def arguments():
        raw = request.args.get('revision', '0')
        deck = request.args.get('presentation', '')
        slides = request.args.get('slides') or None
        if (not re.fullmatch(r'[0-9]{1,9}', raw) or (deck and not re.fullmatch(r'[0-9]{1,18}', deck))
                or (deck and slides) or (slides and not re.fullmatch(r'[a-z0-9-]{1,40}', slides))):
            raise ReviewError('请选择有效的讲义版本与课件。')
        return int(raw), request.args.get('quiz') or None, int(deck) if deck else None, slides

    @app.get('/api/lectures/<int:lecture_id>/package-options')
    def lesson_package_options(lecture_id):
        return jsonify(package_options(db, lecture_id))

    @app.get('/api/lectures/<int:lecture_id>/package')
    def lesson_package(lecture_id):
        number, quiz, deck, slides = arguments()
        fmt = request.args.get('format', 'zip')
        single = {'handout': ('handout', '学生学案'), 'handout-key': ('key', '学生学案-含答案'), 'record': ('record', '生成记录与核对')}
        if fmt not in ('zip', 'docx', 'md', *single):
            raise ReviewError('请选择有效的导出内容。')
        # A handout or the record never shows the slides; only the teacher's documents carry their page numbers.
        parts = collect(db, lecture_id, number, quiz, deck, None if fmt in single else slides)
        label = version_label(number)
        word = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if fmt in single and not designed(parts):
            raise ReviewError('这份讲义不是按课堂设计生成的，没有学生学案；可以导出完整授课包或教师授课手册。')
        if fmt == 'zip':
            payload, _ = build_package(parts)
            mime, name = 'application/zip', f'授课包-讲义{lecture_id}-{label}.zip'
        elif fmt in single:
            key, title = single[fmt]
            payload, mime, name = markdown_docx(documents(parts)[key]), word, f'{title}-讲义{lecture_id}-{label}.docx'
        else:
            # The teacher's document: the book of a designed lesson, the handbook of an older one.
            text = documents(parts)['teacher'] if designed(parts) else handbook_markdown(parts)
            title = '教师用书' if designed(parts) else '教师授课手册'
            if fmt == 'docx':
                payload, mime = markdown_docx(text), word
            else:
                payload, mime = text.encode('utf-8'), 'text/markdown; charset=utf-8'
            name = f'{title}-讲义{lecture_id}-{label}.{fmt}'
        extension = name.rsplit('.', 1)[1]
        response = Response(payload, content_type=mime)
        response.headers['Content-Disposition'] = (
            f"attachment; filename=lesson-package-{lecture_id}.{extension}; filename*=UTF-8''{quote(name)}")
        return response
