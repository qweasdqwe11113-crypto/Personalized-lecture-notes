"""Conservative HTML export: formulas become plain Unicode text, never executable HTML."""
import html
import re

import mathtext
import richdoc

_FORMULA = re.compile(r'(?<!\\)\$(?!\s)(?:\\.|[^$\\])+?(?<!\s)\$')


def formula_text(source):
    """$P(A_1\\mid D)$ as P(A₁ | D): the export carries no script, and LaTeX source is hard to read.
    The plain form of a formula mathtext cannot read is the source itself."""
    try:
        return mathtext.plain(source)
    except Exception:  # noqa: BLE001 - a formula must never break the export
        return source


def inline(text):
    # Formulas are marked with private-use characters first, so bold and citations around them still work.
    text = _FORMULA.sub(lambda match: '\ue000' + formula_text(match.group(0)) + '\ue001',
                        text.replace('\ue000', '').replace('\ue001', ''))
    escaped = html.escape(text)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    escaped = escaped.replace('\ue000', '<span class="math">').replace('\ue001', '</span>')
    return re.sub(r'\[([MV]\d+)\]', r'<span class="citation">[\1]</span>', escaped)


def plain_formulas(content):
    """The plain-text export: every $…$ and $$…$$ formula as Unicode text, the rest unchanged."""
    lines, display = [], False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == '$$':
            display = not display
            continue
        if display:
            lines.append(formula_text('$' + stripped + '$') if stripped else line)
        elif len(stripped) > 4 and stripped.startswith('$$') and stripped.endswith('$$'):
            lines.append(formula_text('$' + stripped[2:-2].strip() + '$'))
        else:
            lines.append(_FORMULA.sub(lambda match: formula_text(match.group(0)), line))
    return '\n'.join(lines)


def display_html(source):
    return '<div class="math-display">' + html.escape(formula_text('$' + source.strip() + '$')) + '</div>'


def table_html(rows):
    cells = [[cell.strip() for cell in row[1:-1].split('|')] for row in rows]
    head = len(cells) > 1 and all(re.fullmatch(r':?-{2,}:?', cell) for cell in cells[1])
    out = ['<table>']
    for index, row in enumerate(cells):
        if head and index == 1:
            continue
        tag = 'th' if head and index == 0 else 'td'
        out.append('<tr>' + ''.join(f'<{tag}>{inline(cell)}</{tag}>' for cell in row) + '</tr>')
    return ''.join(out) + '</table>'


def markdown_html(content):
    parts, list_type, fence, block = [], None, None, []
    figure, math, table = None, None, []

    def close_table():
        if table:
            parts.append(table_html(table))
            table.clear()

    def close_list():
        nonlocal list_type
        if list_type:
            parts.append(f'</{list_type}>')
            list_type = None

    for raw in content.splitlines():
        line = raw.strip()
        if figure is not None:
            if line.startswith('```'):
                parts.append(richdoc.figure_html('\n'.join(figure).strip()))
                figure = None
            else:
                figure.append(raw)
            continue
        if math is not None:
            math.append(line[:-2] if line.endswith('$$') else raw)
            if line.endswith('$$'):
                parts.append(display_html('\n'.join(math)))
                math = None
            continue
        if fence is None and re.fullmatch(r'```figure\s*', line):
            close_list()
            close_table()
            figure = []
            continue
        if fence is None and line.startswith('$$'):
            close_list()
            close_table()
            rest = line[2:]
            if len(rest) > 2 and rest.endswith('$$'):
                parts.append(display_html(rest[:-2]))
            else:
                math = [rest]
            continue
        if fence is None and len(line) > 2 and line.startswith('|') and line.endswith('|'):
            close_list()
            table.append(line)
            continue
        close_table()
        if line.startswith(('```', '~~~')):
            token = line[:3]
            if fence == token:
                parts.append('<pre class="formula-block">' + html.escape('\n'.join(block)) + '</pre>')
                fence, block = None, []
            elif fence is None:
                close_list()
                fence = token
            else:
                block.append(raw)
            continue
        if fence:
            block.append(raw)
            continue
        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        item = re.match(r'^([-*]|\d+[.、])\s+(.+)$', line)
        if not line:
            close_list()
        elif heading:
            close_list()
            level = len(heading[1])
            parts.append(f'<h{level}>{inline(heading[2])}</h{level}>')
        elif item:
            kind = 'ol' if item[1][0].isdigit() else 'ul'
            if list_type != kind:
                close_list()
                list_type = kind
                parts.append(f'<{kind}>')
            value = f' value="{int(re.match(r"\d+", item[1])[0])}"' if kind == 'ol' else ''
            parts.append(f'<li{value}>{inline(item[2])}</li>')
        else:
            close_list()
            parts.append('<p>' + inline(line) + '</p>')
    close_list()
    close_table()
    if block:
        parts.append('<pre class="formula-block">' + html.escape('\n'.join(block)) + '</pre>')
    return ''.join(parts)


def visual_appendix(context):
    evidence = context.get('visual_evidence', [])
    if not evidence:
        return ''
    lines = ['\n\n## 图片解析依据（生成时快照，需核对）']
    for item in evidence:
        lines.extend(['', f'### [{item["citation_id"]}] {item.get("label", "图片")}',
                      item['content'],
                      f'识读模型：{item.get("model", "历史未记录")}；图片 SHA-256：{item.get("normalized_sha256", "历史未记录")}'])
    return '\n'.join(lines)
