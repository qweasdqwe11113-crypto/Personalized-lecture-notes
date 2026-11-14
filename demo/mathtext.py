"""Inline LaTeX as editable slide text: Unicode symbols plus sub- and superscript runs.

Slides keep formulas as ordinary text so a teacher can still correct them in PowerPoint, WPS
or LibreOffice; every one of those draws a run with a baseline offset as a sub- or superscript.
A fraction is written on one line (a/b, with brackets where the reading would otherwise be
ambiguous), \\binom as C with a subscript and a superscript, and anything unknown is kept as
written, so nothing is silently dropped. plain() gives the same text without run styles, for
speaker notes and for measuring how much room a line needs.
"""
from collections import namedtuple
import re

from richdoc import ACCENTS, FUNCTIONS, IGNORED, PLAIN_GROUP, STYLE_GROUP, SYMBOLS, _tokens

VERSION = 'mathtext-v1'
Run = namedtuple('Run', 'text math italic script mark', defaults=('',))   # script: '' | 'sub' | 'sup'
# mark: '' | 'blank' (a gap the student fills in) | 'answer' (what goes in that gap)
GAP = re.compile(r'_{4,}')
BLANK = '\u3000' * 5              # a gap is as wide as five characters
_PIECES = re.compile(r'(\$\$.+?\$\$|(?<!\\)\$(?:\\.|[^$\\])+?\$)', re.S)
RELATIONS = {'=', '<', '>', '≤', '≥', '≈', '≠', '∼', '≡', '∝', '→', '⇒', '⇔', '∈', '∉', '⊂', '⊆'}
MINUS = '−'
BINARY = {'+', MINUS, '×', '÷', '±', '∓', '·'}
_SUP = dict(zip('0123456789+-−=()nixkabcdefghjlmoprstuvwyz', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱˣᵏᵃᵇᶜᵈᵉᶠᵍʰʲˡᵐᵒᵖʳˢᵗᵘᵛʷʸᶻ'))
_SUB = dict(zip('0123456789+-−=()aeoxhklmnpstijruv', '₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢⱼᵣᵤᵥ'))
_MACRON = {'bar': '̄', 'overline': '̅'}


def _text(runs):
    return ''.join(run.text for run in runs)


def _single(runs):
    """One number, one letter (with its scripts) or a factorial: needs no brackets in a fraction."""
    base = ''.join(run.text for run in runs if not run.script).strip()
    return bool(re.fullmatch(r'\d+(?:\.\d+)?|[A-Za-zα-ωΑ-Ω][̀-ͯ⃗]?|[\w.]+!|\(.*\)|√.*', base))


def _top_level(text, signs):
    depth = 0
    for char in text:
        depth += char in '([{'
        depth -= char in ')]}'
        if depth == 0 and char in signs:
            return True
    return False


class _Math:
    def __init__(self, source, fill):
        self.tokens, self.index, self.fill = list(_tokens(source)), 0, fill

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.index += 1
        return token

    def group_text(self):
        if self.peek() != '{':
            return self.take() or ''
        self.take()
        depth, parts = 1, []
        while self.peek() is not None:
            token = self.take()
            if token == '{':
                depth += 1
            elif token == '}':
                depth -= 1
                if depth == 0:
                    break
            parts.append(SYMBOLS.get(token[1:], token) if token.startswith('\\') else token)
        return ''.join(parts)

    def argument(self, depth):
        if self.peek() == '{':
            self.take()
            return self.sequence(depth, '}')
        return self.atom(depth)

    def sequence(self, depth=0, stop=None):
        parts, spaced = [], False
        while self.peek() is not None:
            if self.peek() == stop:
                self.take()
                break
            if self.peek() == '}':
                self.take()
                continue
            fraction = self.peek() in ('\\frac', '\\dfrac', '\\tfrac')
            new = self.scripted(depth)
            if not new:
                continue
            last = parts[-1].text if parts else ''
            if depth == 0 and len(new) == 1 and new[0].text in BINARY and last.strip() and last[-1] not in '( ,':
                new = [new[0]._replace(text=f' {new[0].text} ')]   # binary, not a sign: a - b, not -b
            elif spaced and re.match(r'[\w(√∑]', new[0].text):
                new = [Run(' ', True, False, ''), *new]          # 1/2 P(A), not 1/2P(A)
            spaced = fraction or (bool(new) and new[0].text in ('∑', '∏', '∫'))
            parts += new
        return parts

    def scripted(self, depth):
        base = self.atom(depth)
        sub = sup = None
        while self.peek() in ('^', '_'):
            if self.take() == '^':
                sup = self.argument(depth + 1)
            else:
                sub = self.argument(depth + 1)
        scripted = lambda runs, kind: [run._replace(script=kind, italic=run.italic) for run in runs]
        return base + scripted(sub or [], 'sub') + scripted(sup or [], 'sup')

    def symbol(self, value, depth):
        if depth == 0 and value in RELATIONS:
            return [Run(f' {value} ', True, False, '')]
        if depth == 0 and value == ',':
            return [Run(', ', True, False, '')]
        if value == '-':
            value = MINUS
        return [Run(value, True, bool(re.fullmatch(r'[A-Za-z]', value)), '')]

    def atom(self, depth):
        token = self.take()
        if token is None:
            return []
        if token == '{':
            return self.sequence(depth, '}')
        if token == '&':
            return [Run(' ', True, False, '')]
        if not token.startswith('\\'):
            return self.symbol(token, depth)
        name = token[1:]
        if name == 'gap':
            return self.fill()
        if name == '\\':
            return [Run('；', False, False, '')]
        if name in ('frac', 'dfrac', 'tfrac'):
            top, bottom = self.argument(depth), self.argument(depth)
            if _top_level(_text(top), '+-−±∓'):
                top = [Run('(', True, False, ''), *top, Run(')', True, False, '')]
            if not _single(bottom):
                bottom = [Run('(', True, False, ''), *bottom, Run(')', True, False, '')]
            return [*top, Run('/', True, False, ''), *bottom]
        if name in ('binom', 'dbinom', 'tbinom'):
            top, bottom = self.argument(depth + 1), self.argument(depth + 1)
            return ([Run('C', True, True, '')] + [run._replace(script='sub') for run in top]
                    + [run._replace(script='sup') for run in bottom])
        if name == 'sqrt':
            degree = ''
            if self.peek() == '[':
                self.take()
                parts = []
                while self.peek() not in (']', None):
                    parts.append(self.take())
                self.take()
                degree = ''.join(parts)
            body = self.argument(depth)
            if not _single(body):
                body = [Run('(', True, False, ''), *body, Run(')', True, False, '')]
            lead = [Run(degree, True, False, 'sup')] if degree else []
            return [*lead, Run('√', True, False, ''), *body]
        if name in PLAIN_GROUP:
            return [Run(self.group_text(), False, False, '')]
        if name in STYLE_GROUP:
            return [run._replace(italic=False) for run in self.argument(depth)]
        if name in ACCENTS:
            body = self.argument(depth)
            mark = _MACRON.get(name) or ACCENTS[name]
            if len(_text(body)) == 1:
                return [body[0]._replace(text=body[0].text + mark)]
            return [Run(_text(body), True, False, ''), Run(mark, True, False, '')] if mark else body
        if name in ('begin', 'end'):
            self.group_text()
            return []
        if name in IGNORED:
            return []
        if name in FUNCTIONS:
            return [Run(name, True, False, '')]
        if name == 'mid':
            return [Run(' | ' if depth == 0 else '|', True, False, '')]
        if name in SYMBOLS:
            return self.symbol(SYMBOLS[name], depth)
        return [Run(token, True, False, '')]


def merge(runs):
    """Adjacent runs with the same style become one; empty runs disappear."""
    result = []
    for run in runs:
        if not run.text:
            continue
        if result and result[-1][1:] == run[1:]:
            result[-1] = result[-1]._replace(text=result[-1].text + run.text)
        else:
            result.append(run)
    # Spacing commands next to an added space should not widen a formula: one space is enough.
    return [run._replace(text=re.sub(r' {2,}', ' ', run.text)) if run.math else run for run in result]


def runs(text, answers=None):
    """Styled runs of a line that mixes words and $...$ formulas.

    A gap (four or more underscores, inside or outside a formula) becomes a blank run; with
    answers, the gaps are filled in order, each answer marked so it can be shown in another colour."""
    queue = list(answers) if answers is not None else None

    def fill():
        if queue is None:
            return [Run(BLANK, False, False, '', 'blank')]
        return [run._replace(mark='answer') for run in runs(queue.pop(0) if queue else '…')]

    result = []
    for index, piece in enumerate(_PIECES.split(text or '')):
        if not piece:
            continue
        if index % 2:
            source = piece[2:-2] if piece.startswith('$$') else piece[1:-1]
            converted = _Math(GAP.sub(r'\\gap ', source.strip()), fill).sequence()
            # A relation at the very start or end of a formula should not leave a double space in the sentence.
            if converted:
                converted[0] = converted[0]._replace(text=converted[0].text.lstrip(' '))
                converted[-1] = converted[-1]._replace(text=converted[-1].text.rstrip(' '))
            result += converted or [Run(source, True, False, '')]
        else:
            words = GAP.split(piece.replace('\\$', '$'))
            for position, word in enumerate(words):
                if position:
                    result += fill()
                result.append(Run(word, False, False, ''))
    return merge(result)


def _script_text(text, table, mark):
    if all(char in table for char in text):
        return ''.join(table[char] for char in text)
    return mark + (text if len(text) == 1 else f'({text})')


def plain(text, answers=None):
    """The line as plain Unicode: superscripts as ², or ^(…) where Unicode has no such character.
    A gap is written ____; a filled-in answer is put in 【】."""
    out, inside = [], False
    for run in runs(text, answers):
        if (run.mark == 'answer') != inside:
            out.append('【' if not inside else '】')
            inside = not inside
        if run.mark == 'blank':
            out.append('____')
        elif run.script == 'sup':
            out.append(_script_text(run.text, _SUP, '^'))
        elif run.script == 'sub':
            out.append(_script_text(run.text, _SUB, '_'))
        else:
            out.append(run.text)
    return ''.join(out) + ('】' if inside else '')
