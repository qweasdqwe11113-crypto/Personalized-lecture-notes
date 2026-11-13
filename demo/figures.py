"""Deterministic teaching figures drawn from a small JSON spec the model writes.

The model never draws: it states the data (branches, points, steps) inside a
```figure block, and this module validates the numbers and renders them. The same
display list feeds SVG (web, HTML export) and PNG (Word export), so a figure is
reproducible from the saved lecture text alone and its numbers can be checked.
"""
from fractions import Fraction
import html
import json
import math
import re

from mathcheck import MathError, evaluate, normalize

# v2: function graphs also accept exp, ln, sqrt, abs, sin, cos, tan, the constants e and π and
# non-integer powers (densities such as 0.5*exp(-0.5x) are drawn instead of refused).
VERSION = 'figure-v2'
TYPES = {'probability_tree': '概率树', 'number_line': '数轴', 'balance': '等式变形',
         'function_plot': '函数图像', 'bar_chart': '柱状对比'}
INK, ACCENT, SOFT, LINE, MUTED, WARN, PAPER = '#233249', '#365bd6', '#edf2ff', '#94a3b8', '#64748b', '#b45309', '#ffffff'
PALETTE = ['#365bd6', '#0f766e', '#b45309', '#9333ea', '#be123c', '#4d7c0f', '#0369a1', '#a16207']
MAX_SPEC = 4000


class FigureError(ValueError):
    pass


def _text(value, label, limit=40, required=True):
    if value is None and not required:
        return ''
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise FigureError(f'{label}应为文字。')
    value = str(value).strip()
    if (required and not value) or len(value) > limit or '\n' in value:
        raise FigureError(f'{label}不能为空，且不超过 {limit} 个字符。')
    return value


def _number(value, label, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) > 1e9:
        raise FigureError(f'{label}应为数字。')
    if (low is not None and value < low) or (high is not None and value > high):
        raise FigureError(f'{label}超出允许范围。')
    return value


def _items(value, label, low, high):
    if not isinstance(value, list) or not low <= len(value) <= high or any(not isinstance(v, dict) for v in value):
        raise FigureError(f'{label}应为 {low}—{high} 项的列表。')
    return value


def _fmt(value):
    return f'{value:.4f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)


def _width(text, size):
    return sum(size if ord(c) > 0x2e7f else size * 0.56 for c in text)


class Canvas:
    def __init__(self, width, height):
        self.width, self.height, self.items = width, height, []

    def line(self, x1, y1, x2, y2, stroke=LINE, width=1.5, dash=False):
        self.items.append(('line', x1, y1, x2, y2, stroke, width, dash))

    def rect(self, x, y, w, h, fill=SOFT, stroke=None, radius=8):
        self.items.append(('rect', x, y, w, h, fill, stroke, radius))

    def circle(self, x, y, r, fill=ACCENT, stroke=None):
        self.items.append(('circle', x, y, r, fill, stroke))

    def text(self, x, y, value, size=14, anchor='middle', fill=INK, bold=False):
        self.items.append(('text', x, y, str(value), size, anchor, fill, bold))

    def polyline(self, points, stroke=ACCENT, width=2):
        if len(points) > 1:
            self.items.append(('polyline', [(round(x, 2), round(y, 2)) for x, y in points], stroke, width))

    def label_box(self, x, y, value, size=14, fill=SOFT, color=INK, bold=False, pad=10):
        width = _width(value, size) + pad * 2
        self.rect(x - width / 2, y - size / 2 - 8, width, size + 16, fill)
        self.text(x, y, value, size, 'middle', color, bold)
        return width


def to_svg(canvas, title=''):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas.width} {canvas.height}" '
           f'width="{canvas.width}" height="{canvas.height}" role="img" '
           f'font-family="system-ui,-apple-system,\'Noto Sans SC\',\'Microsoft YaHei\',sans-serif">',
           f'<title>{html.escape(title)}</title>',
           f'<rect width="{canvas.width}" height="{canvas.height}" fill="{PAPER}"/>']
    for item in canvas.items:
        kind = item[0]
        if kind == 'line':
            _, x1, y1, x2, y2, stroke, width, dash = item
            out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" '
                       f'stroke-width="{width}" stroke-linecap="round"' + (' stroke-dasharray="5 5"' if dash else '') + '/>')
        elif kind == 'rect':
            _, x, y, w, h, fill, stroke, radius = item
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius}" fill="{fill}"'
                       + (f' stroke="{stroke}"' if stroke else '') + '/>')
        elif kind == 'circle':
            _, x, y, r, fill, stroke = item
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}"' + (f' stroke="{stroke}" stroke-width="2"' if stroke else '') + '/>')
        elif kind == 'text':
            _, x, y, value, size, anchor, fill, bold = item
            out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" dominant-baseline="central" '
                       f'fill="{fill}"' + (' font-weight="600"' if bold else '') + f'>{html.escape(value)}</text>')
        elif kind == 'polyline':
            _, points, stroke, width = item
            out.append('<polyline points="' + ' '.join(f'{x},{y}' for x, y in points)
                       + f'" fill="none" stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>')
    return ''.join(out) + '</svg>'


FONT_CANDIDATES = ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
                   '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc', '/System/Library/Fonts/PingFang.ttc',
                   '/System/Library/Fonts/STHeiti Light.ttc', 'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf']


def _font(size):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def to_png(canvas, scale=2):
    """PNG bytes for Word; None when no CJK font is installed, so callers fall back to text."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    if _font(12) is None:
        return None
    image = Image.new('RGB', (int(canvas.width * scale), int(canvas.height * scale)), PAPER)
    draw = ImageDraw.Draw(image)
    s = scale
    for item in canvas.items:
        kind = item[0]
        if kind == 'line':
            _, x1, y1, x2, y2, stroke, width, dash = item
            if dash:
                length = max(1, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
                steps = int(length // 10) or 1
                for i in range(steps):
                    a, b = i / steps, (i + 0.5) / steps
                    draw.line([(x1 + (x2 - x1) * a) * s, (y1 + (y2 - y1) * a) * s,
                               (x1 + (x2 - x1) * b) * s, (y1 + (y2 - y1) * b) * s], fill=stroke, width=int(width * s))
            else:
                draw.line([x1 * s, y1 * s, x2 * s, y2 * s], fill=stroke, width=max(1, int(width * s)))
        elif kind == 'rect':
            _, x, y, w, h, fill, stroke, radius = item
            draw.rounded_rectangle([x * s, y * s, (x + w) * s, (y + h) * s], radius=radius * s, fill=fill, outline=stroke)
        elif kind == 'circle':
            _, x, y, r, fill, stroke = item
            draw.ellipse([(x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s], fill=fill, outline=stroke, width=2 * s)
        elif kind == 'text':
            _, x, y, value, size, anchor, fill, bold = item
            draw.text((x * s, y * s), value, font=_font(int(size * s)), fill=fill,
                      anchor={'middle': 'mm', 'start': 'lm', 'end': 'rm'}[anchor],
                      stroke_width=1 if bold else 0, stroke_fill=fill)
        elif kind == 'polyline':
            _, points, stroke, width = item
            draw.line([(x * s, y * s) for x, y in points], fill=stroke, width=int(width * s), joint='curve')
    buffer = BytesIO()
    image.save(buffer, 'PNG', optimize=True)
    return buffer.getvalue()


def _title(canvas, spec):
    if spec.get('title'):
        canvas.text(canvas.width / 2, 22, spec['title'], 15, 'middle', INK, True)


def _probability_tree(spec, warnings):
    branches = _items(spec.get('branches'), '分支', 2, 4)
    root = _text(spec.get('root', '全体'), '根节点', 16)
    rows = []
    for index, branch in enumerate(branches):
        label = _text(branch.get('label'), '分支名称', 16)
        p = _number(branch.get('p'), f'{label} 的概率', 0, 1)
        children = branch.get('children') or []
        if children:
            _items(children, f'{label} 的子分支', 2, 3)
        leaves = []
        for child in children:
            name = _text(child.get('label'), '子分支名称', 16)
            q = _number(child.get('p'), f'{label}→{name} 的条件概率', 0, 1)
            leaves.append((name, q, p * q))
        if leaves and abs(sum(q for _, q, _ in leaves) - 1) > 1e-6:
            warnings.append(f'分支 {label} 下的条件概率之和为 {_fmt(round(sum(q for _, q, _ in leaves), 6))}，不等于 1。')
        rows.append((label, p, leaves))
    if abs(sum(p for _, p, _ in rows) - 1) > 1e-6:
        warnings.append(f'第一层分支概率之和为 {_fmt(round(sum(p for _, p, _ in rows), 6))}，不等于 1。')
    slots = sum(max(1, len(leaves)) for _, _, leaves in rows)
    top = 44 if spec.get('title') else 16
    canvas = Canvas(680, top + slots * 46 + 16)
    _title(canvas, spec)
    x0, x1, x2 = 70, 300, 500
    root_y = top + slots * 46 / 2
    canvas.label_box(x0, root_y, root, 14, SOFT, INK, True)
    cursor = top
    for index, (label, p, leaves) in enumerate(rows):
        height = max(1, len(leaves)) * 46
        y = cursor + height / 2
        color = PALETTE[index % len(PALETTE)]
        canvas.line(x0 + _width(root, 14) / 2 + 14, root_y, x1 - _width(label, 14) / 2 - 14, y, color, 2)
        canvas.text((x0 + x1) / 2 - 4, (root_y + y) / 2 - 11, _fmt(p), 13, 'middle', color, True)
        canvas.label_box(x1, y, label, 14, PAPER, color, True)
        for position, (name, q, joint) in enumerate(leaves):
            leaf_y = cursor + 23 + position * 46
            canvas.line(x1 + _width(label, 14) / 2 + 14, y, x2 - _width(name, 13) / 2 - 14, leaf_y, LINE, 1.5)
            canvas.text((x1 + x2) / 2, (y + leaf_y) / 2 - 10, _fmt(q), 12, 'middle', MUTED)
            canvas.label_box(x2, leaf_y, name, 13, SOFT, INK)
            canvas.text(x2 + 54, leaf_y, '联合概率 ' + _fmt(round(joint, 6)), 12, 'start', MUTED)
        cursor += height
    alt = '；'.join(f'{label}（{_fmt(p)}）' + ('：' + '、'.join(f'{n} {_fmt(q)}，联合 {_fmt(round(j, 6))}' for n, q, j in leaves) if leaves else '')
                   for label, p, leaves in rows)
    return canvas, alt


def _ticks(low, high, target=10):
    span = high - low
    step = 10 ** len(str(int(span))) / 10 if span >= 1 else 0.1
    for candidate in (step / 10, step / 5, step / 2, step, step * 2, step * 5):
        if candidate > 0 and span / candidate <= target:
            step = candidate
            break
    start = int(low // step) * step
    values, value = [], start
    while value <= high + step * 1e-9 and len(values) < 40:
        if value >= low - step * 1e-9:
            values.append(round(value, 6))
        value += step
    return values


def _number_line(spec, warnings):
    low, high = _number(spec.get('min'), '最小值'), _number(spec.get('max'), '最大值')
    if not low < high or high - low > 1e6:
        raise FigureError('数轴的最小值必须小于最大值。')
    points = _items(spec.get('points', []), '点', 0, 8)
    segments = _items(spec.get('segments', []), '区间', 0, 4)
    canvas = Canvas(680, 150 if spec.get('title') else 126)
    _title(canvas, spec)
    y, left, right = canvas.height - 56, 40, 640
    scale = lambda v: left + (v - low) / (high - low) * (right - left)
    canvas.line(left - 14, y, right + 14, y, INK, 2)
    canvas.polyline([(right + 6, y - 6), (right + 16, y), (right + 6, y + 6)], INK, 2)
    for tick in _ticks(low, high):
        canvas.line(scale(tick), y - 5, scale(tick), y + 5, INK, 1.5)
        canvas.text(scale(tick), y + 20, _fmt(int(tick) if float(tick).is_integer() else tick), 12, 'middle', MUTED)
    described = []
    for index, segment in enumerate(segments):
        a, b = _number(segment.get('from'), '区间起点', low, high), _number(segment.get('to'), '区间终点', low, high)
        if a > b:
            raise FigureError('区间起点不能大于终点。')
        color = PALETTE[(index + 1) % len(PALETTE)]
        canvas.line(scale(a), y - 14, scale(b), y - 14, color, 5)
        label = _text(segment.get('label'), '区间说明', 24, False)
        if label:
            canvas.text((scale(a) + scale(b)) / 2, y - 30, label, 12, 'middle', color)
        described.append(f'区间 {_fmt(a)}—{_fmt(b)}' + (f'（{label}）' if label else ''))
    for point in points:
        x = _number(point.get('x'), '点的位置', low, high)
        canvas.circle(scale(x), y, 6, ACCENT, PAPER)
        label = _text(point.get('label'), '点的说明', 24, False) or _fmt(x)
        canvas.text(scale(x), y - 22 - (16 if segments else 0), label, 13, 'middle', ACCENT, True)
        described.append(f'点 {_fmt(x)}（{label}）')
    return canvas, f'数轴 {_fmt(low)} 到 {_fmt(high)}；' + '；'.join(described)


def _balance(spec, warnings):
    steps = _items(spec.get('steps'), '变形步骤', 2, 6)
    solution = spec.get('solution') or {}
    if not isinstance(solution, dict) or len(solution) > 1:
        raise FigureError('solution 只能给出一个未知数的值。')
    env = {}
    for name, value in solution.items():
        if not re.fullmatch(r'[A-Za-z]', str(name)):
            raise FigureError('未知数应为单个字母。')
        env[name] = Fraction(str(_number(value, '未知数的值')))
    top = 44 if spec.get('title') else 14
    canvas = Canvas(680, top + len(steps) * 74)
    _title(canvas, spec)
    described = []
    for index, step in enumerate(steps):
        left, right = _text(step.get('left'), '左边', 36), _text(step.get('right'), '右边', 36)
        note = _text(step.get('note'), '步骤说明', 28, False)
        y = top + index * 74 + 26
        if env:
            try:
                a, b = evaluate(left, env), evaluate(right, env)
                if a != b:
                    warnings.append(f'第 {index + 1} 步代入 {next(iter(solution))} = {_fmt(next(iter(solution.values())))} 后左边为 {_fmt(float(a))}，右边为 {_fmt(float(b))}，两边不相等。')
            except (MathError, ZeroDivisionError):
                pass  # An unreadable expression is drawn as written and left to the teacher.
        canvas.rect(110, y - 20, 200, 40, SOFT, ACCENT if index == len(steps) - 1 else None)
        canvas.rect(370, y - 20, 200, 40, SOFT, ACCENT if index == len(steps) - 1 else None)
        canvas.text(210, y, left, 16, 'middle', INK, True)
        canvas.text(470, y, right, 16, 'middle', INK, True)
        canvas.text(340, y, '=', 18, 'middle', ACCENT, True)
        canvas.line(210, y + 20, 210, y + 26, LINE, 2)
        canvas.line(470, y + 20, 470, y + 26, LINE, 2)
        canvas.line(190, y + 26, 490, y + 26, LINE, 2)
        canvas.polyline([(330, y + 34), (340, y + 26), (350, y + 34)], LINE, 2)
        if note:
            canvas.text(588, y, note, 12, 'start', MUTED)
        if index < len(steps) - 1:
            canvas.line(60, y + 22, 60, y + 52, ACCENT, 2)
            canvas.polyline([(55, y + 46), (60, y + 53), (65, y + 46)], ACCENT, 2)
        canvas.text(60, y, str(index + 1), 13, 'middle', MUTED, True)
        described.append(f'{left} = {right}' + (f'（{note}）' if note else ''))
    return canvas, ' → '.join(described)


_FUNCTIONS = {'exp': math.exp, 'ln': math.log, 'log': math.log, 'sqrt': math.sqrt, 'abs': abs,
              'sin': math.sin, 'cos': math.cos, 'tan': math.tan}
_CONSTANTS = {'e': math.e, 'pi': math.pi, 'π': math.pi}
_NAMES = sorted([*_FUNCTIONS, *_CONSTANTS, 'x'], key=len, reverse=True)
_PLOT_RULE = '只支持含 x 的四则运算、乘方，以及 exp、ln、sqrt、abs、sin、cos、tan 和常数 e、π'


class _Undefined(ArithmeticError):
    """The curve has no real value at this x (log of a negative number, division by zero...)."""


def _plot_tokens(text):
    text = re.sub(r'\\(exp|ln|log|sqrt|sin|cos|tan|pi)(?![A-Za-z])', r'\1', text)
    # \sqrt{2\pi} inside \frac{1}{…}: turn function braces into parentheses first so \frac can match.
    while (flat := re.sub(r'(exp|ln|log|sqrt|sin|cos|tan|abs)\s*\{([^{}]*)\}', r'\1(\2)', text)) != text:
        text = flat
    text = normalize(text).replace('**', '^')
    tokens, position = [], 0
    for match in re.finditer(r'\s*(\d+\.\d+|\.\d+|\d+|[A-Za-zπ]+|[-+*/^(),])', text):
        if match.start() != position:
            raise MathError('unsupported')
        position = match.end()
        word = match[1]
        if not (word[0].isalpha() or word == 'π'):
            tokens.append(word)
            continue
        # 「xexp」 or 「ex」 are products of known names; the longest name wins, so 「exp」 stays one call.
        while word:
            name = next((n for n in _NAMES if word.startswith(n)), None)
            if name is None:
                raise MathError('unknown name')
            tokens.append(name)
            word = word[len(name):]
    if text[position:].strip():
        raise MathError('unsupported')
    return tokens


class _PlotParser:
    """Float version of the arithmetic grammar for drawing: expr := term (('+'|'-') term)*."""

    def __init__(self, tokens, x):
        self.tokens, self.index, self.x = tokens, 0, x

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self):
        self.index += 1
        return self.tokens[self.index - 1] if self.index <= len(self.tokens) else None

    def expression(self):
        value = self.term()
        while self.peek() in ('+', '-'):
            value = value + self.term() if self.take() == '+' else value - self.term()
        return value

    def term(self):
        value = self.unary()
        while True:
            token = self.peek()
            if token in ('*', '/'):
                self.take()
                right = self.unary()
                if token == '/' and right == 0:
                    raise _Undefined
                value = value * right if token == '*' else value / right
            elif token is not None and (token == '(' or token[0].isdigit() or token[0] == '.' or token in _NAMES):
                value *= self.power()
            else:
                return value

    def unary(self):
        if self.peek() in ('-', '+'):
            return -self.unary() if self.take() == '-' else self.unary()
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == '^':
            self.take()
            exponent = self.unary()
            try:
                value = base ** exponent
            except (ZeroDivisionError, OverflowError):
                raise _Undefined from None
            if isinstance(value, complex):
                raise _Undefined
            return value
        return base

    def atom(self):
        token = self.take()
        if token is None:
            raise MathError('incomplete')
        if token == '(':
            value = self.expression()
            if self.take() != ')':
                raise MathError('unbalanced')
            return value
        if token[0].isdigit() or token[0] == '.':
            return float(token)
        if token == 'x':
            return self.x
        if token in _CONSTANTS:
            return _CONSTANTS[token]
        if token in _FUNCTIONS:
            if self.take() != '(':
                raise MathError('function needs parentheses')
            argument = self.expression()
            if self.take() != ')':
                raise MathError('unbalanced')
            try:
                return _FUNCTIONS[token](argument)
            except (ValueError, OverflowError):
                raise _Undefined from None
        raise MathError('unexpected token')


def plot_value(expression, x):
    """y of a function graph at x; raises _Undefined where the curve has no real value."""
    parser = _PlotParser(_plot_tokens(expression), float(x))
    value = parser.expression()
    if parser.index != len(parser.tokens):
        raise MathError('trailing input')
    if not math.isfinite(value):
        raise _Undefined
    return value


def _function_plot(spec, warnings):
    span = spec.get('x')
    if not isinstance(span, list) or len(span) != 2:
        raise FigureError('x 应为 [最小值, 最大值]。')
    low, high = _number(span[0], 'x 最小值'), _number(span[1], 'x 最大值')
    if not low < high or high - low > 1e4:
        raise FigureError('x 的最小值必须小于最大值。')
    functions = _items(spec.get('functions'), '函数', 1, 4)
    points = _items(spec.get('points', []), '点', 0, 8)
    curves = []
    for function in functions:
        expression = _text(function.get('expr'), '函数表达式', 60)
        label = _text(function.get('label'), '函数说明', 30, False) or 'y = ' + expression
        samples = []
        for i in range(81):
            x = low + (high - low) * i / 80
            try:
                samples.append((x, plot_value(expression, x)))
            except _Undefined:
                samples.append(None)
            except MathError:
                raise FigureError(f'无法计算函数表达式 {expression}；{_PLOT_RULE}。') from None
        if not any(samples):
            raise FigureError(f'函数 {expression} 在 x 的范围内没有定义，请调整 x 的范围。')
        curves.append((label, samples))
    marked = [(_number(p.get('x'), '点的 x', low, high), _number(p.get('y'), '点的 y'), _text(p.get('label'), '点的说明', 24, False)) for p in points]
    ys = [y for _, samples in curves for s in samples if s for y in [s[1]]] + [y for _, y, _ in marked] + [0]
    y_low, y_high = min(ys), max(ys)
    if y_high - y_low < 1e-9:
        y_low, y_high = y_low - 1, y_high + 1
    if y_high - y_low > 1e6:
        raise FigureError('函数值范围过大，请缩小 x 的范围。')
    top = 44 if spec.get('title') else 18
    canvas = Canvas(680, top + 300)
    _title(canvas, spec)
    left, right, bottom = 56, 500, top + 270
    sx = lambda v: left + (v - low) / (high - low) * (right - left)
    sy = lambda v: bottom - (v - y_low) / (y_high - y_low) * (bottom - top - 10)
    for tick in _ticks(low, high, 8):
        canvas.line(sx(tick), top + 10, sx(tick), bottom, '#e2e8f0', 1)
        canvas.text(sx(tick), bottom + 14, _fmt(int(tick) if float(tick).is_integer() else tick), 11, 'middle', MUTED)
    for tick in _ticks(y_low, y_high, 8):
        canvas.line(left, sy(tick), right, sy(tick), '#e2e8f0', 1)
        canvas.text(left - 8, sy(tick), _fmt(int(tick) if float(tick).is_integer() else tick), 11, 'end', MUTED)
    if low <= 0 <= high:
        canvas.line(sx(0), top + 10, sx(0), bottom, INK, 1.5)
    canvas.line(left, sy(0), right, sy(0), INK, 1.5)
    for index, (label, samples) in enumerate(curves):
        color, run = PALETTE[index % len(PALETTE)], []
        for sample in samples + [None]:
            if sample is None:
                canvas.polyline(run, color, 2.5)
                run = []
            else:
                run.append((sx(sample[0]), sy(sample[1])))
        canvas.line(520, top + 22 + index * 24, 544, top + 22 + index * 24, color, 3)
        canvas.text(552, top + 22 + index * 24, label, 12, 'start', INK)
    for x, y, label in marked:
        canvas.circle(sx(x), sy(y), 5, INK, PAPER)
        canvas.text(sx(x) + 9, sy(y) - 11, label or f'({_fmt(x)}, {_fmt(y)})', 12, 'start', INK, True)
    return canvas, '；'.join(label for label, _ in curves) + (f'；标出 {len(marked)} 个点' if marked else '')


def _bar_chart(spec, warnings):
    bars = _items(spec.get('bars'), '柱', 2, 8)
    rows = [(_text(bar.get('label'), '柱的名称', 20), _number(bar.get('value'), '柱的数值', 0)) for bar in bars]
    unit = _text(spec.get('unit'), '单位', 8, False)
    ceiling = _number(spec['max'], '最大值', 0) if spec.get('max') is not None else max(v for _, v in rows)
    ceiling = max(ceiling, max(v for _, v in rows)) or 1
    top = 44 if spec.get('title') else 16
    canvas = Canvas(680, top + len(rows) * 40 + 12)
    _title(canvas, spec)
    for index, (label, value) in enumerate(rows):
        y = top + index * 40 + 18
        canvas.text(176, y, label, 13, 'end', INK)
        canvas.rect(188, y - 12, 380, 24, '#f1f5f9', None, 6)
        canvas.rect(188, y - 12, max(2, 380 * value / ceiling), 24, PALETTE[index % len(PALETTE)], None, 6)
        canvas.text(578, y, _fmt(value) + unit, 13, 'start', INK, True)
    return canvas, '；'.join(f'{label} {_fmt(value)}{unit}' for label, value in rows)


BUILDERS = {'probability_tree': _probability_tree, 'number_line': _number_line, 'balance': _balance,
            'function_plot': _function_plot, 'bar_chart': _bar_chart}


def build(source):
    """Parse and lay out one figure. Returns (spec, canvas, alt text, warnings)."""
    if not isinstance(source, str) or len(source) > MAX_SPEC:
        raise FigureError('图的描述过长。')
    try:
        spec = json.loads(source)
    except json.JSONDecodeError:
        raise FigureError('图的描述不是有效 JSON。') from None
    if not isinstance(spec, dict) or spec.get('type') not in BUILDERS:
        raise FigureError('不支持的图类型；可用：' + '、'.join(BUILDERS) + '。')
    if 'title' in spec:
        spec['title'] = _text(spec['title'], '标题', 40, False)
    warnings = []
    try:
        canvas, alt = BUILDERS[spec['type']](spec, warnings)
    except (AttributeError, TypeError, KeyError):
        raise FigureError('图的描述缺少必要字段或字段类型不对。') from None
    return spec, canvas, alt, warnings


def render(source):
    """Never raises: a figure that cannot be drawn becomes a readable message."""
    try:
        spec, canvas, alt, warnings = build(source)
    except FigureError as exc:
        return dict(ok=False, error=str(exc), warnings=[], svg=None, alt='')
    title = spec.get('title') or TYPES[spec['type']]
    return dict(ok=True, type=spec['type'], title=title, svg=to_svg(canvas, title), alt=alt, warnings=warnings,
                width=canvas.width, height=canvas.height)


def png(source):
    try:
        _, canvas, _, _ = build(source)
    except FigureError:
        return None
    return to_png(canvas)


FENCE = re.compile(r'^[ \t]*```figure[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*$', re.M | re.S)


def find(content):
    """Figure blocks of a lecture as (line number, source)."""
    return [(content.count('\n', 0, match.start()) + 1, match[1].strip()) for match in FENCE.finditer(content or '')]


def check(content):
    figures, problems = find(content), []
    for line, source in figures:
        result = render(source)
        if not result['ok']:
            problems.append(dict(line=line, detail='无法绘制：' + result['error']))
        problems.extend(dict(line=line, detail=warning) for warning in result['warnings'])
    return dict(version=VERSION, count=len(figures), problems=problems,
                note='只检查图的数据能否绘制及其自身是否一致（如分支概率之和）；图与正文是否对应仍需教师核对。')


def describe(source):
    """Plain-text stand-in for exports that cannot carry an image."""
    result = render(source)
    return f'【图：{result["title"]}】{result["alt"]}' if result['ok'] else '【图未能绘制：' + result['error'] + '】'
