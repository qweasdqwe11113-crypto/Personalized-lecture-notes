"""Geometry of the slides built from a lesson design: how much room a text needs, and how a page is drawn.

Pages are planned in lesson_slides.py from the measurements here, so the page count, and with
it the page numbers printed in the teacher's book, are known before anything is drawn. The
measure is the one ppt-kit's check() applies to the saved file (Chinese characters one em wide,
other characters 0.56 em), so a planned page passes that check by construction.

All shapes are native and editable: text boxes with formula runs (mathtext.py), rectangles,
lines, and figures redrawn from figures.py's display list instead of pasted as pictures.
"""
import json
import math
import re

from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

import figures
import mathtext
import pptkit_export  # noqa: F401  (registers the project-local ppt-kit as _lecture_pptkit)
from _lecture_pptkit.theme import _CN_RE as _WIDE, _set_ea_font, add_line, add_rect

LS = 1.3                          # line pitch of every text block, in em, set as exact points: renderers
                                  # scale a multiple of the font's own line height differently, points they agree on
LEFT, RIGHT = 64, 1216
WIDTH = RIGHT - LEFT
TOP, BOTTOM = 164, 652            # content area under the title, above the footer (the classroom pages' grid)
ROOM = BOTTOM - TOP
QUESTION = 78                     # a question box at the bottom, with its gap
MATH_FONT = 'Times New Roman'
DO_X, DO_W = LEFT + 56, 700       # solution step: what to write ...
WHY_X = DO_X + DO_W + 32          # ... and why
WHY_W = RIGHT - WHY_X
LABEL_W = 120                     # 「练1」 in front of an answer
INDENT = 34                       # bullet column


# ---------------------------------------------------------------- measuring

def runs(value):
    """A text value is a string, or {'text', 'answers'} for a handout note whose gaps are filled in."""
    if isinstance(value, dict):
        return mathtext.runs(value['text'], value.get('answers'))
    return mathtext.runs(value)


def chars(value):
    """The characters a text box will hold: formulas converted, gaps as wide spaces."""
    return ''.join(run.text for run in runs(value))


def width_of(text, size):
    """Like ppt-kit's estimate, but a formula's letters, digits and spaced operators count 0.62 em instead of
    0.56: fonts that stand in for the template's (LibreOffice, WPS) set them wider, and a line that wraps
    unexpectedly would run into the next row. check() measures with 0.56, so a planned box always passes it."""
    narrow = len(text) - len(_WIDE.findall(text))
    return (len(text) - narrow) * size + narrow * size * 0.62


def height(value, size, width):
    """Room one paragraph needs; never less than check() computes."""
    text = chars(value)
    if not text.strip():
        return 0.0
    return max(1, math.ceil(width_of(text, size) / width)) * size * LS


def stack(values, size, width, gap):
    heights = [height(value, size, width) for value in values]
    return sum(heights) + gap * max(0, len(heights) - 1)


def points_need(points, size):
    return stack(points, size, WIDTH - INDENT, size * 0.7)


def problems_need(items, size):
    """items: [{'label', 'stem'}]; a label line above each stem."""
    return sum(30 * bool(item['label']) + height(item['stem'], size, WIDTH) for item in items) + 26 * max(0, len(items) - 1)


def step_rows(rows, size):
    return [max(height(row['do'], size, DO_W), height(row['why'], size - 2, WHY_W)) + 18 for row in rows]


def steps_need(rows, size, tail=()):
    need = sum(step_rows(rows, size))
    if tail:
        need += 16 + sum(height(line, size - 2, WIDTH - 40) for line in tail) + 24 + 8 * (len(tail) - 1)
    return need


def answers_need(rows, size):
    return sum(max(size * LS, height(row['answer'], size, WIDTH - LABEL_W)) + 20 for row in rows) - 20 * bool(rows)


def summary_need(points, size):
    return sum(height(point, size, WIDTH - 110) + 36 for point in points) + 14 * max(0, len(points) - 1)


def homework_need(groups, size, full=True):
    need = 0
    for _, items in groups:
        need += 44
        for item in items:
            need += height(item['label'] + '　' + (item['stem'] if full else item['title']), size, WIDTH - INDENT) + 12
    return need + 16 * max(0, len(groups) - 1)


# ---------------------------------------------------------------- drawing primitives

def _rgb(value):
    return value if isinstance(value, RGBColor) else RGBColor.from_string(value.lstrip('#'))


def write(slide, th, x, y, w, h, value, size, color=None, bold=False, align='left', name='正文',
          anchor='top', accent=None):
    """One paragraph of mixed words and formulas; a blank is an underlined gap, an answer is underlined in the accent colour."""
    box = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
    box.name = name
    frame = box.text_frame
    frame.word_wrap, frame.auto_size = True, MSO_AUTO_SIZE.NONE
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = {'top': MSO_ANCHOR.TOP, 'middle': MSO_ANCHOR.MIDDLE}[anchor]
    paragraph = frame.paragraphs[0]
    paragraph.alignment = {'left': PP_ALIGN.LEFT, 'center': PP_ALIGN.CENTER, 'right': PP_ALIGN.RIGHT}[align]
    paragraph.line_spacing = Pt(round(size * LS, 1))
    for part in runs(value):
        run = paragraph.add_run()
        run.text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', part.text)
        font = run.font
        font.size = Pt(size)
        font.bold = bold
        font.italic = part.italic
        font.color.rgb = _rgb(accent or th.accent) if part.mark == 'answer' else _rgb(color or th.text)
        font.underline = bool(part.mark)
        font.name = MATH_FONT if part.math else th.font_en
        _set_ea_font(run, th.font_cn)
        if part.script:
            font._rPr.set('baseline', '30000' if part.script == 'sup' else '-25000')
    return box


def header(slide, th, page, progress):
    """Section word and page title; a row of marks shows where this segment sits in the lesson."""
    add_rect(slide, 0, 0, 1280, 6, fill=th.primary)
    write(slide, th, LEFT, 30, 600, 24, page['heading'], 16, th.accent, True, name='环节')
    total, current = progress
    if total > 1:
        width, gap = 26, 8
        x = RIGHT - total * width - (total - 1) * gap
        for index in range(total):
            add_rect(slide, x + index * (width + gap), 38, width, 7,
                     fill=th.primary if index == current else th.accent_pale if index < current else th.line)
    size = 34 if width_of(chars(page['title']), 34) <= WIDTH else 28
    write(slide, th, LEFT, 64, WIDTH, min(76, height(page['title'], size, WIDTH) + 4), page['title'], size, th.primary,
          True, name='页标题')
    add_line(slide, LEFT, 143, RIGHT, 143, th.line, .8)
    add_line(slide, LEFT, 143, LEFT + 76, 143, th.accent, 2)


def footer(slide, th, label, index, total):
    add_line(slide, LEFT, 674, RIGHT, 674, th.line, .7)
    write(slide, th, LEFT, 688, 900, 18, label, 12, th.hint, name='课题')
    if index:
        write(slide, th, RIGHT - 120, 688, 120, 18, f'{index:02d} / {total:02d}', 12, th.primary, align='right', name='页码')


def question(slide, th, text):
    add_rect(slide, LEFT, BOTTOM - 62, WIDTH, 62, fill=th.bg_sub)
    add_rect(slide, LEFT, BOTTOM - 62, 5, 62, fill=th.accent)
    size = 22 if height('想一想　' + text, 22, WIDTH - 48) <= 30 else 18
    write(slide, th, LEFT + 24, BOTTOM - 62, WIDTH - 48, 62, '想一想　' + text, size, th.primary,
          name='课堂提问', anchor='middle')


def figure(slide, th, spec, x, y, w, h):
    """A figure from figures.py, redrawn with native shapes on a white card so it stays editable."""
    try:
        _, canvas, _, _ = figures.build(json.dumps(spec, ensure_ascii=False))
    except figures.FigureError:
        return False
    scale = min(w / canvas.width, h / canvas.height, 1.8)
    cw, ch = canvas.width * scale, canvas.height * scale
    ox, oy = x + (w - cw) / 2, y + (h - ch) / 2
    card = add_rect(slide, ox - 16, oy - 12, cw + 32, ch + 24, fill=RGBColor(255, 255, 255), line=th.line, radius=True)
    card.name = '图'
    X = lambda value: ox + value * scale
    Y = lambda value: oy + value * scale
    for item in canvas.items:
        kind = item[0]
        if kind == 'line':
            _, x1, y1, x2, y2, stroke, width, dash = item
            line = add_line(slide, X(x1), Y(y1), X(x2), Y(y2), _rgb(stroke), width * scale)
            if dash:
                line.line.dash_style = MSO_LINE.DASH
        elif kind == 'rect':
            _, rx, ry, rw, rh, fill, stroke, radius = item
            add_rect(slide, X(rx), Y(ry), rw * scale, rh * scale, fill=_rgb(fill) if fill else None,
                     line=_rgb(stroke) if stroke else None, line_w=1.2, radius=bool(radius))
        elif kind == 'circle':
            _, cx, cy, r, fill, stroke = item
            dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Pt(X(cx - r)), Pt(Y(cy - r)), Pt(2 * r * scale), Pt(2 * r * scale))
            dot.fill.solid()
            dot.fill.fore_color.rgb = _rgb(fill)
            if stroke:
                dot.line.color.rgb, dot.line.width = _rgb(stroke), Pt(2 * scale)
            else:
                dot.line.fill.background()
            dot.shadow.inherit = False
        elif kind == 'text':
            _, tx, ty, value, size, anchor, fill, bold = item
            size = size * scale
            width = figures._width(value, size) + 10
            left = X(tx) - (width / 2 if anchor == 'middle' else width - 5 if anchor == 'end' else 5)
            box = slide.shapes.add_textbox(Pt(left), Pt(Y(ty) - size * 0.75), Pt(width), Pt(size * 1.5))
            frame = box.text_frame
            frame.word_wrap, frame.auto_size = False, MSO_AUTO_SIZE.NONE
            frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
            frame.vertical_anchor = MSO_ANCHOR.MIDDLE
            paragraph = frame.paragraphs[0]
            paragraph.alignment = {'middle': PP_ALIGN.CENTER, 'start': PP_ALIGN.LEFT, 'end': PP_ALIGN.RIGHT}[anchor]
            paragraph.line_spacing = 1.0
            run = paragraph.add_run()
            run.text = value
            run.font.size, run.font.bold, run.font.color.rgb = Pt(round(size, 1)), bold, _rgb(fill)
            run.font.name = th.font_en
            _set_ea_font(run, th.font_cn)
        elif kind == 'polyline':
            _, points, stroke, width = item
            builder = slide.shapes.build_freeform(round(X(points[0][0]) * 100), round(Y(points[0][1]) * 100), scale=127)
            builder.add_line_segments([(round(X(px) * 100), round(Y(py) * 100)) for px, py in points[1:]], close=False)
            shape = builder.convert_to_shape()
            shape.fill.background()
            shape.line.color.rgb, shape.line.width = _rgb(stroke), Pt(width * scale)
            shape.shadow.inherit = False
    return True


# ---------------------------------------------------------------- pages

def _bullets(slide, th, points, size, x, y, width, numbered=False):
    gap = size * 0.7
    for index, point in enumerate(points, 1):
        h = height(point, size, width - INDENT)
        if numbered:
            write(slide, th, x, y, INDENT - 4, size * LS, str(index), size * 0.8, th.accent, True, name='序号')
        else:
            add_rect(slide, x + 4, y + size * 0.42, 9, 9, fill=th.accent)
        write(slide, th, x + INDENT, y, width - INDENT, h + 2, point, size, name='要点')
        y += h + gap
    return y


def cover(slide, th, page):
    add_rect(slide, 0, 0, 14, 720, fill=th.primary)
    add_rect(slide, 760, 0, 520, 720, fill=th.bg_sub)
    write(slide, th, 72, 96, 620, 26, page['heading'], 18, th.accent, True, name='封面类别')
    add_line(slide, 72, 136, 136, 136, th.primary, 2.5)
    size = 52 if width_of(chars(page['title']), 52) <= 640 * 2 else 40
    title_h = height(page['title'], size, 640) + 6
    write(slide, th, 72, 168, 640, title_h, page['title'], size, th.primary, True, name='封面标题')
    y = 168 + title_h + 36
    if page.get('lines'):
        for line in page['lines']:
            h = height(line, 20, 640)
            write(slide, th, 72, y, 640, h + 2, line, 20, th.muted, name='封面说明')
            y += h + 10
    write(slide, th, 808, 120, 420, 30, '本课学习目标', 22, th.primary, True, name='目标标题')
    _bullets(slide, th, page['goals'], page['size'], 808, 176, 420, numbered=True)


def plain_cover(slide, th, page):
    """For masters whose artwork crosses the upper third (技术办公): the cover as an ordinary page."""
    header(slide, th, page, (0, -1))
    y = TOP + 8
    for line in page.get('lines', []):
        h = height(line, 20, WIDTH)
        write(slide, th, LEFT, y, WIDTH, h + 2, line, 20, th.muted, name='封面说明')
        y += h + 10
    write(slide, th, LEFT, y + 6, WIDTH, 30, '本课学习目标', 22, th.primary, True, name='目标标题')
    _bullets(slide, th, page['goals'], min(page['size'], 24), LEFT, y + 50, WIDTH, numbered=True)


def points(slide, th, page):
    room = ROOM - (QUESTION if page.get('question') else 0)
    need = points_need(page['points'], page['size'])
    _bullets(slide, th, page['points'], page['size'], LEFT, TOP + max(0, (room - need) / 3), WIDTH)


def problems(slide, th, page):
    y, size = TOP + 4, page['size']
    for item in page['items']:
        top = 30 if item['label'] else 0
        if top:
            write(slide, th, LEFT, y, WIDTH, 26, item['label'], 18, th.accent, True, name='题号')
        h = height(item['stem'], size, WIDTH)
        write(slide, th, LEFT, y + top, WIDTH, h + 2, item['stem'], size, name='题干')
        y += top + h + 26


def steps(slide, th, page):
    size, y = page['size'], TOP + 2
    for index, (row, h) in enumerate(zip(page['rows'], step_rows(page['rows'], size))):
        add_rect(slide, LEFT, y + 2, 34, 34, fill=th.primary)
        write(slide, th, LEFT, y + 2, 34, 34, str(row['number']), 18, th.on_primary, True, 'center', '步骤序号', 'middle')
        write(slide, th, DO_X, y, DO_W, h - 16, row['do'], size, name='步骤')
        write(slide, th, WHY_X, y + 2, WHY_W, h - 16, row['why'], size - 2, th.muted, name='依据')
        if index < len(page['rows']) - 1:
            add_line(slide, DO_X, y + h - 9, RIGHT, y + h - 9, th.line, .5)
        y += h
    if page.get('tail'):
        need = sum(height(line, size - 2, WIDTH - 40) for line in page['tail']) + 8 * (len(page['tail']) - 1)
        top = max(y + 16, BOTTOM - need - 24)
        add_rect(slide, LEFT, top, WIDTH, need + 24, fill=th.bg_sub)
        line_y = top + 12
        for line in page['tail']:
            h = height(line, size - 2, WIDTH - 40)
            write(slide, th, LEFT + 20, line_y, WIDTH - 40, h + 2, line, size - 2, th.primary, name='检验与易错')
            line_y += h + 8


def answers(slide, th, page):
    size, y = page['size'], TOP + 4
    for row in page['rows']:
        h = max(size * LS, height(row['answer'], size, WIDTH - LABEL_W))
        write(slide, th, LEFT, y, LABEL_W - 16, size * LS, row['label'], size, th.accent, True, name='题号')
        write(slide, th, LEFT + LABEL_W, y, WIDTH - LABEL_W, h + 2, row['answer'], size, name='答案')
        y += h + 20


def summary(slide, th, page):
    size, y = page['size'], TOP + 4
    for index, point in enumerate(page['points'], 1):
        h = height(point, size, WIDTH - 110) + 36
        add_rect(slide, LEFT, y, WIDTH, h, fill=th.bg_sub)
        add_rect(slide, LEFT, y, 5, h, fill=th.primary)
        write(slide, th, LEFT + 24, y, 60, h, f'{index:02d}', 26, th.accent, True, name='序号', anchor='middle')
        write(slide, th, LEFT + 92, y + 18, WIDTH - 110, h - 34, point, size, name='小结')
        y += h + 14


def homework(slide, th, page):
    size, y = page['size'], TOP + 2
    for name, items in page['groups']:
        write(slide, th, LEFT, y, WIDTH, 30, name, 20, th.accent, True, name='作业层次')
        y += 44
        for item in items:
            text = item['label'] + '　' + (item['stem'] if page['full'] else item['title'])
            h = height(text, size, WIDTH - INDENT)
            add_rect(slide, LEFT + 4, y + size * 0.42, 9, 9, fill=th.accent)
            write(slide, th, LEFT + INDENT, y, WIDTH - INDENT, h + 2, text, size, name='作业')
            y += h + 12
        y += 16
    if page.get('note'):
        write(slide, th, LEFT, BOTTOM - 28, WIDTH, 26, page['note'], 18, th.muted, name='说明')


def figure_page(slide, th, page):
    room = ROOM - (QUESTION if page.get('question') else 0)
    figure(slide, th, page['figure'], LEFT + 40, TOP + 12, WIDTH - 80, room - 24)


def notice(slide, th, page):
    add_rect(slide, LEFT, TOP, WIDTH, ROOM, fill=th.bg_sub)
    y = TOP + 24
    for line in page['lines']:
        h = height(line, 18, WIDTH - 48)
        write(slide, th, LEFT + 24, y, WIDTH - 48, h + 2, line, 18, th.text, name='核对说明')
        y += h + 10


PAGES = dict(cover=cover, points=points, problems=problems, steps=steps, answers=answers, summary=summary,
             homework=homework, figure=figure_page, notice=notice)


def draw(slide, th, page, progress, label, index, total, skin=None):
    if skin in {'notebook', 'peter-river'}:
        add_rect(slide, 24, 16, 1232, 696, fill=th.surface)   # a clean sheet over the master's ruled paper
    if page['layout'] == 'cover' and skin == 'technical-office':
        plain_cover(slide, th, page)
    else:
        if page['layout'] != 'cover':
            header(slide, th, page, progress)
        PAGES[page['layout']](slide, th, page)
    if page.get('question') and page['layout'] in ('points', 'problems', 'figure'):
        question(slide, th, page['question'])
    footer(slide, th, label, 0 if page.get('hidden') else index, total)
