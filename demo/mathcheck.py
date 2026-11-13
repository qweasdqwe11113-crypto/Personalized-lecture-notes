"""Recompute the arithmetic a lecture or quiz writes down; never judges the mathematics.

Two narrow, deterministic checks:
  numeric   - an equality chain made only of numbers, e.g. 0.6 × 0.02 + 0.4 × 0.05 = 0.032
  solution  - a stated solution such as x = 5 substituted back into the equations before it

Anything the small parser does not understand is skipped, never guessed. A finding means
"the written numbers do not agree", which a teacher still has to read in context.
"""
from fractions import Fraction
import re

VERSION = 'arithmetic-v1'
MAX_FINDINGS = 12


class MathError(ValueError):
    pass


_LATEX = [(r'\\(?:dfrac|tfrac|frac)\s*\{([^{}]*)\}\s*\{([^{}]*)\}', r'((\1)/(\2))'),
          (r'\\(?:left|right|,|;|!|quad|qquad|displaystyle)', ' '),
          (r'\\times|\\cdot|\\ast', '*'), (r'\\div', '/'), (r'\\%', '%'),
          (r'\\approx', '≈'), (r'\\neq|\\ne', '≠'), (r'\\(?:text|mathrm)\s*\{([^{}]*)\}', r'\1')]
_CHARS = str.maketrans({'×': '*', '·': '*', '⋅': '*', '∗': '*', '÷': '/', '−': '-', '–': '-', '－': '-',
                        '＋': '+', '（': '(', '）': ')', '＝': '=', '％': '%', '，': ',', '\u00a0': ' ',
                        '\u3000': ' ', '{': '(', '}': ')'})


def normalize(text):
    for pattern, replacement in _LATEX * 2:  # twice: one level of nested fractions
        text = re.sub(pattern, replacement, text)
    text = text.replace('$', ' ').translate(_CHARS)
    return ''.join(chr(ord(c) - 0xFEE0) if '０' <= c <= '９' else c for c in text)


def _tokens(text):
    result, position = [], 0
    for match in re.finditer(r'\s*(\d+\.\d+|\d+|[A-Za-z]|[-+*/^()%])', text):
        if match.start() != position:
            raise MathError('unsupported')
        result.append(match[1])
        position = match.end()
    if text[position:].strip():
        raise MathError('unsupported')
    return result


class _Parser:
    """expr := term (('+'|'-') term)* ; implicit multiplication binds like '*'."""

    def __init__(self, tokens, env):
        self.tokens, self.index, self.env = tokens, 0, env

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.index += 1
        return token

    def expression(self):
        value = self.term()
        while self.peek() in ('+', '-'):
            value = value + self.term() if self.take() == '+' else value - self.term()
        return value

    def term(self):
        value = self.unary()
        while True:
            token = self.peek()
            if token == '*':
                self.take()
                value *= self.unary()
            elif token == '/':
                self.take()
                divisor = self.unary()
                if divisor == 0:
                    raise MathError('division by zero')
                value /= divisor
            elif token is not None and (token == '(' or token.isalpha() or token[0].isdigit()):
                # 3x, 2(x+1), x(…): juxtaposition. Two bare numbers side by side are not a product.
                previous = self.tokens[self.index - 1]
                if token[0].isdigit() and (previous[0].isdigit() or previous == '%'):
                    raise MathError('adjacent numbers')
                value *= self.power()
            else:
                return value

    def unary(self):
        if self.peek() == '-':
            self.take()
            return -self.unary()
        if self.peek() == '+':
            self.take()
            return self.unary()
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == '^':
            self.take()
            exponent = self.unary()
            if exponent.denominator != 1 or abs(exponent) > 12:
                raise MathError('unsupported exponent')
            if base == 0 and exponent < 0:
                raise MathError('division by zero')
            base = base ** int(exponent)
        return base

    def atom(self):
        token = self.take()
        if token is None:
            raise MathError('incomplete')
        if token == '(':
            value = self.expression()
            if self.take() != ')':
                raise MathError('unbalanced')
        elif token[0].isdigit():
            value = Fraction(token)
        elif token.isalpha():
            if token not in self.env:
                raise MathError('unknown variable')
            value = self.env[token]
        else:
            raise MathError('unexpected token')
        while self.peek() == '%':
            self.take()
            value /= 100
        return value


def evaluate(text, env=None):
    tokens = _tokens(normalize(text))
    if not tokens:
        raise MathError('empty')
    parser = _Parser(tokens, env or {})
    value = parser.expression()
    if parser.index != len(tokens):
        raise MathError('trailing input')
    return value


def _precision(text):
    """Decimal places the writer showed; None for integers and fractions (exact)."""
    places = [len(m[1]) for m in re.finditer(r'\d+\.(\d+)', text)]
    if '%' in text:
        places = [p + 2 for p in places] or [2]
    return max(places) if places else None


def _agree(left, right, left_text, right_text, approximate=False):
    if left == right:
        return True
    # Only a bare number can be a rounded result; an expression is taken at its exact value.
    shown = [_precision(text) for text in (left_text, right_text)
             if not _has_operator(text) and _precision(text) is not None]
    if not shown:
        # Exact on both sides, e.g. 12/32 = 3/8. "≈" between exact values is the writer's choice.
        return approximate and abs(left - right) <= Fraction(1, 100) * max(abs(left), abs(right), 1)
    # A rounded decimal may differ from the exact value by half a unit in its last place;
    # allow a full unit so truncation and chained rounding are not reported.
    tolerance = Fraction(1, 10 ** min(shown)) * (2 if approximate else 1)
    return abs(left - right) <= tolerance


def _has_operator(text):
    return bool(re.search(r'[-+*/^]', normalize(text).strip().lstrip('-+')))


def _numeric_chains(line):
    """Yield (segments, relations) for runs of pure-number equalities inside one line."""
    flat = normalize(line)
    for run in re.finditer(r'[0-9+\-*/^().%\s=≈]+', flat):
        text = run[0]
        if '=' not in text and '≈' not in text:
            continue
        pieces = re.split(r'(=|≈)', text)
        segments, relations = pieces[0::2], pieces[1::2]
        values = []
        for index, segment in enumerate(segments):
            candidate = segment.strip()
            if index == 0:
                # "1. 0.6 × 0.02 = …": a list number is not part of the expression.
                candidate = re.sub(r'^\d+[.)]\s+(?=\d)', '', candidate)
                candidate = candidate.lstrip(')').strip()
            if index == len(segments) - 1:
                candidate = candidate.rstrip('(.').strip()
            try:
                values.append((candidate, evaluate(candidate)))
            except (MathError, ZeroDivisionError, OverflowError):
                values.append(None)
        start = 0
        while start < len(values):
            if values[start] is None:
                start += 1
                continue
            end = start
            while end + 1 < len(values) and values[end + 1] is not None:
                end += 1
            if end > start and any(_has_operator(values[i][0]) for i in range(start, end + 1)):
                yield values[start:end + 1], relations[start:end]
            start = end + 1


def _display(value):
    if value.denominator == 1:
        return str(value.numerator)
    number = float(value)
    return f'{number:.6g}'


def _candidate(line):
    """The formula part of a line: text after the last Chinese or ASCII colon, list marker removed."""
    text = re.split(r'[：:]', line)[-1]
    text = re.sub(r'^\s*(?:[-*•]|\d+[.、)])\s+', '', text)
    return re.sub(r'[，,。；;]\s*$', '', text.strip())


def check_text(text):
    findings, checked = [], 0
    pending = []  # (line number, excerpt, left, right) waiting for a stated solution
    blank = 0
    for number, raw in enumerate((text or '').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(('```', '~~~')):
            blank += 1
            if blank >= 2:
                pending = []
            continue
        if line.startswith('#'):
            pending, blank = [], 0
            continue
        blank = 0
        for values, relations in _numeric_chains(line):
            checked += 1
            for (left_text, left), (right_text, right), relation in zip(values, values[1:], relations):
                if not _agree(left, right, left_text, right_text, relation == '≈'):
                    findings.append(dict(line=number, kind='numeric', excerpt=line[:160],
                                         detail=f'{left_text.strip()} 计算得 {_display(left)}，与所写的 {right_text.strip()} 不一致。'))
                    break
        candidate = normalize(_candidate(line))
        if candidate.count('=') != 1:
            continue
        left_text, right_text = (part.strip() for part in candidate.split('='))
        variables = set(re.findall(r'[A-Za-z]', candidate))
        if len(variables) != 1 or not left_text or not right_text:
            continue
        variable = next(iter(variables))
        if left_text == variable:
            try:
                value = evaluate(right_text)
            except (MathError, ZeroDivisionError, OverflowError):
                value = None
            if value is not None:
                for item in pending:
                    if item['variable'] != variable:
                        continue
                    try:
                        left = evaluate(item['left'], {variable: value})
                        right = evaluate(item['right'], {variable: value})
                    except (MathError, ZeroDivisionError, OverflowError):
                        continue
                    checked += 1
                    if not _agree(left, right, item['left'], item['right']):
                        findings.append(dict(line=item['line'], kind='solution', excerpt=item['excerpt'][:160],
                                             detail=f'把第 {number} 行的 {variable} = {_display(value)} 代回，'
                                                    f'左边为 {_display(left)}，右边为 {_display(right)}，不相等。'))
                pending = [item for item in pending if item['variable'] != variable]
                continue
        try:  # Keep only equations the parser can read with a trial value.
            evaluate(left_text, {variable: Fraction(1)})
            evaluate(right_text, {variable: Fraction(1)})
        except ZeroDivisionError:
            pass
        except (MathError, OverflowError):
            continue
        pending.append(dict(variable=variable, line=number, excerpt=line, left=left_text, right=right_text))
        pending = pending[-12:]
    return dict(version=VERSION, checked=checked, findings=findings[:MAX_FINDINGS],
                truncated=len(findings) > MAX_FINDINGS,
                note='只重算能够识别的纯数字等式和一元方程的代回检验；未识别的式子不计入，结果可能误报，需结合上下文判断。')
