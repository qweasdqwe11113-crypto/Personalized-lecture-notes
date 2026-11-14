"""Formulas and figures for exports: LaTeX to Word's native math, figures to pictures.

The LaTeX reader covers what lecture prompts ask for (fractions, scripts, roots, common
symbols, \\text). Anything it does not know is written out as plain text inside the
formula, so an export never fails or silently drops part of a formula.
"""
from base64 import b64encode
import html
from io import BytesIO
import re
from xml.sax.saxutils import escape

import figures

M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
SYMBOLS = {
    'times': '×', 'cdot': '·', 'div': '÷', 'pm': '±', 'mp': '∓', 'le': '≤', 'leq': '≤', 'ge': '≥', 'geq': '≥',
    'ne': '≠', 'neq': '≠', 'approx': '≈', 'equiv': '≡', 'sim': '∼', 'propto': '∝', 'cap': '∩', 'cup': '∪',
    'mid': '|', 'vert': '|', 'lvert': '|', 'rvert': '|', 'in': '∈', 'notin': '∉', 'subset': '⊂', 'subseteq': '⊆',
    'supset': '⊃', 'emptyset': '∅', 'varnothing': '∅', 'infty': '∞', 'to': '→', 'rightarrow': '→',
    'leftarrow': '←', 'Rightarrow': '⇒', 'Leftrightarrow': '⇔', 'iff': '⇔', 'implies': '⇒', 'sum': '∑',
    'prod': '∏', 'int': '∫', 'ldots': '…', 'cdots': '⋯', 'dots': '…', 'neg': '¬', 'land': '∧', 'lor': '∨',
    'forall': '∀', 'exists': '∃', 'partial': '∂', 'nabla': '∇', 'angle': '∠', 'triangle': '△', 'circ': '∘',
    'bullet': '•', 'star': '⋆', 'setminus': '∖', 'complement': '∁', 'because': '∵', 'therefore': '∴',
    'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ', 'epsilon': 'ε', 'varepsilon': 'ε', 'zeta': 'ζ',
    'eta': 'η', 'theta': 'θ', 'lambda': 'λ', 'mu': 'μ', 'nu': 'ν', 'xi': 'ξ', 'pi': 'π', 'rho': 'ρ',
    'sigma': 'σ', 'tau': 'τ', 'phi': 'φ', 'varphi': 'φ', 'chi': 'χ', 'psi': 'ψ', 'omega': 'ω',
    'Gamma': 'Γ', 'Delta': 'Δ', 'Theta': 'Θ', 'Lambda': 'Λ', 'Pi': 'Π', 'Sigma': 'Σ', 'Phi': 'Φ', 'Omega': 'Ω',
    'quad': '  ', 'qquad': '    ', ',': ' ', ';': ' ', ':': ' ', ' ': ' ', '!': '', '%': '%', '$': '$', '{': '{', '}': '}',
    '_': '_', '&': '&', '#': '#', '|': '‖', 'lbrace': '{', 'rbrace': '}', 'langle': '⟨', 'rangle': '⟩',
    'backslash': '\\', 'prime': '′', 'degree': '°',
}
IGNORED = {'left', 'right', 'displaystyle', 'textstyle', 'limits', 'nolimits', 'big', 'Big', 'bigg', 'Bigg',
           'bigl', 'bigr', 'Bigl', 'Bigr', 'middle'}
PLAIN_GROUP = {'text', 'mathrm', 'operatorname', 'textbf', 'textit', 'mbox', 'textrm'}
STYLE_GROUP = {'mathbf', 'mathit', 'mathbb', 'mathcal', 'boldsymbol', 'mathsf'}
ACCENTS = {'hat': '̂', 'tilde': '̃', 'vec': '⃗', 'dot': '̇', 'bar': None, 'overline': None}
FUNCTIONS = {'sin', 'cos', 'tan', 'log', 'ln', 'lim', 'max', 'min', 'exp', 'Pr', 'det', 'gcd'}


def _tokens(source):
    for match in re.finditer(r'\\[A-Za-z]+|\\.|[{}^_&]|\s+|.', source, re.S):
        token = match[0]
        if not token.isspace():
            yield token


def _run(text, plain=False):
    if not text:
        return ''
    properties = '<m:rPr><m:nor/></m:rPr>' if plain else ''
    return f'<m:r>{properties}<m:t xml:space="preserve">{escape(text)}</m:t></m:r>'


class _Reader:
    def __init__(self, source):
        self.tokens, self.index = list(_tokens(source)), 0

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.index += 1
        return token

    def group_text(self):
        """Raw text of the next {...} group, for \\text and friends."""
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

    def argument(self):
        if self.peek() == '{':
            self.take()
            return self.sequence('}')
        return self.atom()

    def sequence(self, stop=None):
        parts = []
        while self.peek() is not None:
            if self.peek() == stop:
                self.take()
                break
            if self.peek() == '}':  # stray brace: keep reading instead of losing the rest
                self.take()
                continue
            parts.append(self.scripted())
        return ''.join(parts)

    def scripted(self):
        base = self.atom()
        sub = sup = None
        while self.peek() in ('^', '_'):
            if self.take() == '^':
                sup = self.argument()
            else:
                sub = self.argument()
        if sub is not None and sup is not None:
            return f'<m:sSubSup><m:e>{base}</m:e><m:sub>{sub}</m:sub><m:sup>{sup}</m:sup></m:sSubSup>'
        if sup is not None:
            return f'<m:sSup><m:e>{base}</m:e><m:sup>{sup}</m:sup></m:sSup>'
        if sub is not None:
            return f'<m:sSub><m:e>{base}</m:e><m:sub>{sub}</m:sub></m:sSub>'
        return base

    def atom(self):
        token = self.take()
        if token is None:
            return ''
        if token == '{':
            return self.sequence('}')
        if token == '&':
            return ''
        if not token.startswith('\\'):
            return _run(token)
        name = token[1:]
        if name == '\\':
            return _run('  ')
        if name in ('frac', 'dfrac', 'tfrac'):
            top, bottom = self.argument(), self.argument()
            return f'<m:f><m:num>{top}</m:num><m:den>{bottom}</m:den></m:f>'
        if name in ('binom', 'dbinom', 'tbinom'):
            top, bottom = self.argument(), self.argument()
            return ('<m:d><m:dPr><m:begChr m:val="("/><m:endChr m:val=")"/></m:dPr><m:e><m:f><m:fPr><m:type m:val="noBar"/></m:fPr>'
                    f'<m:num>{top}</m:num><m:den>{bottom}</m:den></m:f></m:e></m:d>')
        if name == 'sqrt':
            degree = ''
            if self.peek() == '[':
                self.take()
                parts = []
                while self.peek() not in (']', None):
                    parts.append(self.take())
                self.take()
                degree = _run(''.join(parts))
            body = self.argument()
            hide = '' if degree else '<m:radPr><m:degHide m:val="1"/></m:radPr>'
            return f'<m:rad>{hide}<m:deg>{degree}</m:deg><m:e>{body}</m:e></m:rad>'
        if name in PLAIN_GROUP:
            return _run(self.group_text(), plain=True)
        if name in STYLE_GROUP:
            return self.argument()
        if name in ACCENTS:
            body = self.argument()
            if ACCENTS[name] is None:
                return f'<m:bar><m:barPr><m:pos m:val="top"/></m:barPr><m:e>{body}</m:e></m:bar>'
            return f'<m:acc><m:accPr><m:chr m:val="{ACCENTS[name]}"/></m:accPr><m:e>{body}</m:e></m:acc>'
        if name in ('begin', 'end'):
            self.group_text()
            return ''
        if name == 'gap':   # a handout gap inside a formula (design.open_gaps)
            return _run('＿＿＿＿', plain=True)
        if name in IGNORED:
            return ''
        if name in FUNCTIONS:
            return _run(name, plain=True)
        if name in SYMBOLS:
            return _run(SYMBOLS[name])
        return _run(token)


def latex_omml(tex, display=False):
    body = _Reader(tex).sequence() or _run(tex)
    math = f'<m:oMath xmlns:m="{M}">{body}</m:oMath>'
    return f'<m:oMathPara xmlns:m="{M}">{math.replace(f" xmlns:m=\"{M}\"", "")}</m:oMathPara>' if display else math


INLINE = re.compile(r'(\*\*[^*]+\*\*|\$\$[^$]+\$\$|(?<!\\)\$(?:\\.|[^$\\])+?\$)')


def add_inline(paragraph, text, italic=False):
    """Bold, inline formulas and plain text, in reading order."""
    from docx.oxml import parse_xml
    for piece in INLINE.split(text):
        if not piece:
            continue
        if len(piece) > 4 and piece.startswith('$$') and piece.endswith('$$'):
            paragraph._p.append(parse_xml(latex_omml(piece[2:-2].strip())))
        elif len(piece) > 2 and piece.startswith('$') and piece.endswith('$') and not re.search(r'^\$\s|\s\$$', piece):
            paragraph._p.append(parse_xml(latex_omml(piece[1:-1])))
        else:
            bold = piece.startswith('**') and piece.endswith('**') and len(piece) > 4
            run = paragraph.add_run(piece[2:-2] if bold else piece.replace('\\$', '$'))
            run.bold, run.italic = bold or None, italic or None


def add_display_math(document, tex):
    from docx.oxml import parse_xml
    paragraph = document.add_paragraph()
    paragraph._p.append(parse_xml(latex_omml(tex, display=True)))
    return paragraph


def add_figure(document, source):
    """A picture when a CJK font is available, otherwise the figure's data in words."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm
    result = figures.render(source)
    data = figures.png(source) if result['ok'] else None
    if data:
        document.add_picture(BytesIO(data), width=Cm(14))
        document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = caption.add_run(result['title'] + '（由程序按讲义中的数据绘制）')
        run.italic = True
    else:
        document.add_paragraph(figures.describe(source))
    for warning in result.get('warnings', []):
        document.add_paragraph('图中数据需核对：' + warning)


def blocks(content):
    """Split lecture Markdown into ('figure'|'math'|'table'|'code'|'line', value) blocks."""
    lines, index, result = content.splitlines(), 0, []
    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        index += 1
        if re.fullmatch(r'```figure\s*', line):
            body = []
            while index < len(lines) and not lines[index].strip().startswith('```'):
                body.append(lines[index])
                index += 1
            index += 1
            result.append(('figure', '\n'.join(body).strip()))
        elif line[:3] in ('```', '~~~'):
            body = []
            while index < len(lines) and not lines[index].strip().startswith(line[:3]):
                body.append(lines[index])
                index += 1
            index += 1
            result.append(('code', '\n'.join(body)))
        elif line.startswith('$$'):
            rest = line[2:]
            if len(rest) > 2 and rest.endswith('$$'):
                result.append(('math', rest[:-2].strip()))
                continue
            body = [rest]
            while index < len(lines):
                current = lines[index].strip()
                index += 1
                if current.endswith('$$'):
                    body.append(current[:-2])
                    break
                body.append(current)
            result.append(('math', '\n'.join(body).strip()))
        elif len(line) > 2 and line.startswith('|') and line.endswith('|'):
            rows = [line]
            while index < len(lines) and lines[index].strip().startswith('|') and lines[index].strip().endswith('|'):
                rows.append(lines[index].strip())
                index += 1
            cells = [[c.strip() for c in row[1:-1].split('|')] for row in rows
                     if not re.fullmatch(r'\|[\s:|-]+\|', row)]
            result.append(('table', cells) if cells else ('line', ''))
        else:
            result.append(('line', raw))
    return result


def add_table(document, cells):
    table = document.add_table(rows=len(cells), cols=max(map(len, cells)))
    table.style = 'Table Grid'
    for r, values in enumerate(cells):
        for c, value in enumerate(values):
            paragraph = table.cell(r, c).paragraphs[0]
            add_inline(paragraph, value)
            if r == 0:
                for run in paragraph.runs:
                    run.bold = True
    return table


def figure_html(source):
    result = figures.render(source)
    if not result['ok']:
        return f'<p class="warning">这幅图未能绘制：{html.escape(result["error"])}</p>'
    data = b64encode(result['svg'].encode('utf-8')).decode('ascii')
    warnings = ''.join(f'<p class="warning">图中数据需核对：{html.escape(w)}</p>' for w in result['warnings'])
    return (f'<figure><img alt="{html.escape(result["title"] + "：" + result["alt"])}" '
            f'src="data:image/svg+xml;base64,{data}"><figcaption>{html.escape(result["title"])}'
            f'（由程序按讲义中的数据绘制）</figcaption></figure>{warnings}')


def plain_text(content):
    """For .txt exports: a figure becomes one line of words, everything else is untouched."""
    return '\n'.join(figures.describe(value) if kind == 'figure'
                     else '$$' + value + '$$' if kind == 'math'
                     else '\n'.join(' | '.join(row) for row in value) if kind == 'table'
                     else value for kind, value in blocks(content))
