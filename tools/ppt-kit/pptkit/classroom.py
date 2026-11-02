"""Semantic, editable classroom pages in the reference report's blue-white style.

Geometry belongs here; model contracts contain only content and relationships.
"""
from pptx.dml.color import RGBColor
from pptx.util import Pt

from .theme import add_text, add_rect, add_line, para_height


class LayoutOverflow(ValueError):
    pass


def text(slide, th, value, x, y, w, h, size=24, color=None, bold=False, name='正文', spacing=1.3):
    if not value:
        return
    needed = para_height(value, size, w, spacing)
    if needed > h + 3:
        raise LayoutOverflow(f'{name} 需要 {needed:.0f}pt，可用 {h:.0f}pt')
    shape = add_text(slide, x, y, w, h, value, size=size, color=color or th.text,
                     bold=bold, theme=th, line_spacing=spacing)
    shape.name = name
    return shape


def header(slide, th, title, category, stripe=True):
    if stripe:
        add_rect(slide, 0, 0, 1280, 5, fill=th.primary)
    text(slide, th, category, 40, 24, 1000, 20, 13, th.hint, name='页面类别')
    text(slide, th, title, 40, 57, 1200, 82, 36 if len(title) <= 30 else 32,
         th.primary, True, '页标题', 1.12)
    add_line(slide, 40, 143, 1240, 143, th.line, .7)
    add_line(slide, 40, 143, 116, 143, th.accent, 2)


def footer(slide, th, label, index, total):
    add_line(slide, 40, 674, 1240, 674, th.line, .7)
    text(slide, th, label, 40, 688, 1000, 20, 12, th.hint, name='版本与状态')
    text(slide, th, f'{index:02d} / {total:02d}', 1120, 688, 120, 20, 12, th.primary, name='页码')


def blocks(slide, th, items, x, y, w, h, size=24, color=None, numbered=False):
    inner = w - 48 if numbered else w
    heights = [para_height(item, size, inner, 1.3) + 4 for item in items]
    gap = min(30, (h - sum(heights)) / max(1, len(items) - 1))
    if gap < 8 and len(items) > 1:
        raise LayoutOverflow('正文段落在当前版式中放不下')
    for index, (item, height) in enumerate(zip(items, heights), 1):
        if numbered:
            text(slide, th, f'{index:02d}', x, y + 2, 38, 28, 18, th.accent, True, '条目编号')
        text(slide, th, item, x + (48 if numbered else 0), y, inner, height, size,
             color or th.muted, name='内容条目')
        y += height + gap


def cover(slide, th, page, visual):
    add_rect(slide, 0, 0, 13, 720, fill=th.primary)
    add_rect(slide, 742, 0, 538, 660, fill=th.bg_sub)
    text(slide, th, '课堂讲解 · 学习目标', 64, 72, 640, 24, 16, th.accent, True, '封面类别')
    add_line(slide, 64, 111, 126, 111, th.primary, 2)
    title_size = 64 if len(page['title']) <= 16 else 48
    title_h = para_height(page['title'], title_size, 630, 1.15) + 8
    text(slide, th, page['title'], 64, 150, 630, title_h, title_size, th.primary, True, '封面标题', 1.15)
    if visual.get('subtitle'):
        text(slide, th, visual['subtitle'], 64, 174 + title_h, 630, 160, 26, name='封面副标题')
    blocks(slide, th, visual['goals'], 790, 180, 430, 420, 24, numbered=True)


def explain(slide, th, page, visual):
    text(slide, th, visual['lead'], 40, 178, 430, 410, 30, th.primary, True, '本页重点')
    add_line(slide, 500, 174, 500, 594, th.line, 1)
    blocks(slide, th, visual['items'], 540, 178, 700, 414, 24, numbered=True)


def definition(slide, th, page, visual):
    add_rect(slide, 40, 164, 790, 436, fill=th.bg_sub)
    y = 188
    for key, size, color, bold in [('term', 32, th.primary, True), ('definition', 28, th.text, False),
                                   ('formula', 28, th.primary, True)]:
        value = visual.get(key)
        if value:
            h = para_height(value, size, 742, 1.3) + 4
            text(slide, th, value, 64, y, 742, h, size, color, bold, key)
            y += h + 20
    if y > 620:
        raise LayoutOverflow('概念与公式超过内容区域')
    text(slide, th, '成立条件 / 注意', 866, 188, 350, 36, 22, th.primary, True, '条件标题')
    blocks(slide, th, visual['conditions'], 866, 244, 350, 340, 22, numbered=False)


def steps(slide, th, page, visual):
    add_rect(slide, 40, 164, 1200, 78, fill=th.bg_sub)
    text(slide, th, visual['problem'], 64, 170, 1152, 70, 26, th.primary, True, '例题题干')
    items = visual['steps']
    row = 284 / len(items)
    for i, item in enumerate(items):
        y = 252 + i * row
        add_rect(slide, 40, y + 3, 36, 36, fill=th.primary)
        text(slide, th, str(i + 1), 50, y + 6, 22, 30, 20, th.on_primary, True, '步骤序号')
        text(slide, th, item['expression'], 100, y, 750, row - 4, 22 if len(items) == 4 else 26,
             th.text, True, '推导表达式')
        text(slide, th, item['reason'], 898, y, 330, row - 4, 18 if len(items) == 4 else 22,
             th.muted, name='变形依据', spacing=1.2)
        if i < len(items) - 1:
            add_line(slide, 100, y + row - 6, 1230, y + row - 6, th.line, .5)
    if visual.get('check'):
        text(slide, th, '检验：' + visual['check'], 64, 540, 1152, 62, 22, th.primary, True, '检验')


def comparison(slide, th, page, visual):
    for key, x in [('left', 40), ('right', 660)]:
        data = visual[key]
        add_rect(slide, x, 164, 580, 436, fill=th.bg_sub)
        add_rect(slide, x, 164, 580, 3, fill=th.primary)
        text(slide, th, data['title'], x + 24, 186, 532, 78, 28, th.primary, True, '比较对象')
        blocks(slide, th, data['items'], x + 24, 280, 532, 302, 22)


def table(slide, th, page, visual, compact=False):
    rows, headers = visual['rows'], visual['headers']
    shape = slide.shapes.add_table(len(rows) + 1, len(headers), Pt(40), Pt(164), Pt(1200), Pt(436))
    shape.name = '可编辑对照表'
    tbl = shape.table
    tbl.first_row = False
    tbl.horz_banding = False
    tbl.rows[0].height = Pt(68)
    for row in list(tbl.rows)[1:]:
        row.height = Pt(368 / len(rows))
    from .layouts import _set_cell
    size = 17 if compact else 18 if len(headers) == 4 else 20
    for r, values in enumerate([headers, *rows]):
        for c, value in enumerate(values):
            _set_cell(tbl.cell(r, c), value, th, bold=r == 0, size=size,
                      fill=th.primary if r == 0 else th.bg_sub if r % 2 else th.surface,
                      color=th.on_primary if r == 0 else th.text)
            cell = tbl.cell(r, c)
            available_w = (tbl.columns[c].width - cell.margin_left - cell.margin_right) / 12700
            available_h = tbl.rows[r].height.pt - cell.margin_top.pt - cell.margin_bottom.pt
            if para_height(value, size, available_w, 1.4) > available_h + 2:
                raise LayoutOverflow('表格单元格文字过长')


def process(slide, th, page, visual):
    nodes = visual['nodes']
    width = (1200 - 28 * (len(nodes) - 1)) / len(nodes)
    for i, node in enumerate(nodes):
        x = 40 + i * (width + 28)
        add_rect(slide, x, 204, width, 396, fill=th.bg_sub)
        text(slide, th, f'{i+1:02d}', x + 18, 224, width - 36, 44, 32, th.accent, True, '流程序号')
        text(slide, th, node['title'], x + 18, 286, width - 36, 92, 22, th.primary, True, '流程节点')
        text(slide, th, node['detail'], x + 18, 386, width - 36, 200, 18, th.muted, name='流程说明')
        if i < len(nodes) - 1:
            text(slide, th, '→', x + width + 2, 346, 24, 30, 22, th.accent, name='过程方向')


def metrics(slide, th, page, visual):
    items = visual['items']
    width = (1200 - 32 * (len(items) - 1)) / len(items)
    for i, item in enumerate(items):
        x = 40 + i * (width + 32)
        add_rect(slide, x, 164, width, 436, fill=th.bg_sub)
        text(slide, th, item['value'], x + 24, 186, width - 48, 152, 40 if len(item['value']) > 10 else 56, th.primary, True, '关键数据')
        text(slide, th, item['unit'], x + 24, 340, width - 48, 36, 22, th.accent, name='数据单位')
        text(slide, th, item['label'], x + 24, 388, width - 48, 96, 24, th.text, True, '数据含义')
        text(slide, th, item['context'], x + 24, 494, width - 48, 96, 18, th.muted, name='数据基准')


def practice(slide, th, page, visual):
    text(slide, th, visual['problem'], 64, 182, 1152, 228, 32, th.primary, True, '完整练习题干')
    # Native lines provide writing space; answers remain in speaker notes.
    add_line(slide, 64, 432, 1216, 432, th.line, .6)
    add_line(slide, 64, 470, 1216, 470, th.line, .6)
    prompts = visual['prompts']
    if prompts:
        width = (1152 - 28 * (len(prompts) - 1)) / len(prompts)
        for i, prompt in enumerate(prompts):
            text(slide, th, prompt, 64 + i * (width + 28), 498, width, 102, 20,
                 th.muted, name='作答要求')


def recap(slide, th, page, visual):
    items = visual['items']
    row = (436 - 12 * (len(items) - 1)) / len(items)
    for i, item in enumerate(items):
        y = 164 + i * (row + 12)
        add_rect(slide, 40, y, 1200, row, fill=th.bg_sub)
        add_rect(slide, 40, y, 3, row, fill=th.primary)
        text(slide, th, f'{i+1:02d}', 64, y + 16, 62, 48, 32, th.accent, True, '回顾序号')
        text(slide, th, item['title'], 146, y + 8, 280, row - 16, 22 if len(item['title']) > 22 else 24, th.primary, True, '回顾方法')
        text(slide, th, item['detail'], 466, y + 12, 748, row - 24, 22, th.muted, name='回顾说明')


def reading(slide, th, page, visual, lines):
    """Bounded repair: full-width layout preserves every semantic string."""
    blocks(slide, th, lines, 64, 170, 1152, 430, 22)


LAYOUTS = dict(cover=cover, explain=explain, definition=definition, steps=steps,
               comparison=comparison, table=table, process=process, metrics=metrics,
               practice=practice, recap=recap)


def render(slide, th, page, visual, category, repair=False, lines=(), skin=None):
    if skin in {'notebook', 'peter-river'}:
        # Leave the native ruled sheet visible around a clean writing area.
        # Use one paper surface so rules never cross formulas or table cells.
        add_rect(slide, 24, 16, 1232, 696, fill=th.surface)
    if visual['type'] != 'cover' or repair or skin == 'technical-office':
        header(slide, th, page['title'], category, stripe=skin not in {'notebook', 'technical-office'})
    if repair and visual['type'] == 'table':
        # Preserve row/column relationships and native editability on repair.
        table(slide, th, page, visual, compact=True)
    elif repair:
        reading(slide, th, page, visual, lines)
    elif skin == 'technical-office' and visual['type'] == 'cover':
        text(slide, th, visual['subtitle'], 64, 178, 1152, 104, 30, th.primary, True, '封面副标题')
        blocks(slide, th, visual['goals'], 64, 304, 1152, 292, 28, numbered=True)
    else:
        LAYOUTS[visual['type']](slide, th, page, visual)
    if page['question'] and page['question'] != visual.get('problem'):
        add_rect(slide, 40, 610, 1200, 54, fill=th.bg_sub)
        text(slide, th, '课堂提问：' + page['question'], 56, 612, 1168, 52, 20,
             th.primary, name='课堂提问')
