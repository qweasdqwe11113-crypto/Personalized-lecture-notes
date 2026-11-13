"""Lesson design: the structured plan that every document of one lesson is derived from.

The model proposes the design as JSON: what matters most, the objectives, timed segments in
classroom order, and one bank of worked examples, exercises and homework. This module checks
structure, counts, minutes and references, and renders the parts that have to be identical in
the teacher's book, the student handout and the slides, so an example is written once and only
quoted afterwards.

Nothing here judges subject correctness or teaching quality. `hard` problems make a design
unusable (it cannot be rendered or cites a source that does not exist); `soft` problems are
worth one more attempt and are then shown to the teacher as reminders.
"""
import json
import re

import figures
import mathcheck

VERSION = 'lesson-design-v1'
KINDS = {'warmup': '前测与回顾', 'intro': '导入', 'concept': '新授', 'example': '例题', 'practice': '当堂练习',
         'summary': '小结', 'homework': '布置作业'}
ROLES = {'warmup': '前测', 'example': '例', 'practice': '练', 'homework': '作业'}
LEVELS = {'basic': '基础', 'advanced': '提高'}
MIN_MINUTES = {'example': 4, 'practice': 3}   # per item, including questions and feedback
WRITTEN = ('concept', 'example')              # segments whose explanation a second call writes out
RATES = {'brief': 20, 'standard': 45, 'detailed': 75}  # written characters per classroom minute
ORDINALS = '一二三四五六七八九十'
ROLE_NAMES = {'warmup': '前测题', 'example': '例题', 'practice': '当堂练习', 'homework': '作业题'}
KIND_WORDS = {'warmup': ('前测', '回顾'), 'intro': ('导入',), 'concept': ('新授',), 'example': ('例题', '例'),
              'practice': ('练习',), 'summary': ('小结',), 'homework': ('作业',)}
_TITLE_PREFIX = re.compile(r'^(?:前测与回顾|前测|回顾|导入|新授|例题?[ \t]*\d*|当堂练习|课堂练习|练习?[ \t]*\d*|课堂小结|小结|布置作业|作业)[ \t]*[:：][ \t]*')
ARITHMETIC = '算式复算不一致'   # a possible false alarm: reported, but never the only reason for a retry
FIELDS = {'title': '标题', 'stem': '题干', 'answer': '答案', 'steps': '解答步骤', 'do': '步骤内容', 'teacher': '教师活动',
          'key_points': '重点', 'difficulties': '难点', 'not_today': '本课不展开', 'summary': '小结', 'text': '内容',
          'items': '题目', 'segments': '环节', 'objectives': '学习目标', 'ask': '问题', 'minutes': '分钟数'}
LIMIT = 24000

# JSON turns an under-escaped LaTeX command into a control character plus the rest of its name
# ("\frac" arrives as form feed + "rac"). Put the backslash back where the rest is a known command.
_ESCAPED = {'\x08': 'b', '\x0c': 'f', '\t': 't', '\r': 'r', '\n': 'n'}
_COMMANDS = {
    'b': 'ar|eta|egin|igg?|inom|oldsymbol|ullet|mod|ot|oxed',
    'f': 'rac|orall|lat',
    't': 'imes|ext(?:bf|it|rm)?|o|heta|au|anh?|frac|op|riangle|ilde',
    'r': 'ight(?:arrow)?|ho|angle|ceil|floor|m',
    'n': 'eq|eg|abla|otin|ot|mid|leq|geq',
}
_RESTORE = re.compile('|'.join(f'(?P<{letter}>{re.escape(char)}(?:{_COMMANDS[letter]})(?![A-Za-z]))'
                               for char, letter in _ESCAPED.items()))


class DesignError(ValueError):
    pass


def restore_latex(text):
    text = _RESTORE.sub(lambda m: '\\' + m.lastgroup + m[0][1:], text)
    return re.sub(r'\n(e)(?=[ \d])(?=[^$\n]*\$)', r'\\n\1', text)  # "\ne 0" inside a formula


def _restore(value):
    if isinstance(value, str):
        return restore_latex(value)
    if isinstance(value, list):
        return [_restore(item) for item in value]
    if isinstance(value, dict):
        return {key: _restore(item) for key, item in value.items()}
    return value


def parse(content):
    """The model's reply as a dictionary, or DesignError."""
    text = (content or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    if len(text) > LIMIT:
        raise DesignError(f'课堂设计超过 {LIMIT} 字符，请精简')
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise DesignError('输出不是有效的 JSON 对象') from None
    if not isinstance(data, dict):
        raise DesignError('输出不是 JSON 对象')
    return _restore(data)


def _name(key):
    return f'{FIELDS[key]}（{key}）' if key in FIELDS else key


class _Reader:
    def __init__(self):
        self.hard, self.soft = [], []

    def text(self, obj, key, limit, where, required=True):
        value = obj.get(key) if isinstance(obj, dict) else None
        if value is None and not required:
            return ''
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            self.hard.append(f'{where}的{_name(key)}应为文字')
            return ''
        value = re.sub(r'[ \t\r]+', ' ', value).strip()
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
            self.hard.append(f'{where}的{_name(key)}里有损坏的公式：JSON 字符串中的每个反斜杠要写成两个')
            value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
        if required and not value:
            self.hard.append(f'{where}缺少{_name(key)}')
        if len(value) > limit:
            self.hard.append(f'{where}的{_name(key)}超过 {limit} 字，请精简')
            value = value[:limit]
        return value

    def lines(self, obj, key, limit, low, high, where):
        value = obj.get(key) if isinstance(obj, dict) else None
        if value is None:
            value = []
        if isinstance(value, str):
            value = [value] if value.strip() else []
        if not isinstance(value, list):
            self.hard.append(f'{where}的{_name(key)}应为列表')
            return []
        result = [self.text({'item': item}, 'item', limit, f'{where}的{_name(key)}') for item in value]
        result = [item for item in result if item]
        if not low <= len(result) <= high:
            (self.hard if len(result) < low else self.soft).append(f'{where}的{_name(key)}应有 {low}—{high} 条，现在 {len(result)} 条')
        return result[:high]

    def records(self, obj, key, low, high, where):
        value = obj.get(key) if isinstance(obj, dict) else None
        if value is None:
            value = []
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            self.hard.append(f'{where}的{_name(key)}应为对象列表')
            return []
        if not low <= len(value) <= high:
            (self.hard if len(value) < low else self.soft).append(f'{where}的{_name(key)}应有 {low}—{high} 项，现在 {len(value)} 项')
        return value[:high]


def _sources(reader, obj, where, allowed):
    value = obj.get('sources') or []
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        name = str(item).strip().strip('[]')
        if name.isdigit():
            name = 'M' + name
        if name in allowed:
            result.append(name)
        else:
            reader.hard.append(f'{where}引用了不存在的来源编号 {name}；只能使用输入中提供的编号')
    return list(dict.fromkeys(result))


def _allowed_sources(context):
    return ({f'M{m["id"]}' for m in context.get('materials', [])}
            | {item['citation_id'] for item in context.get('visual_evidence', [])})


def validate(raw, context):
    """Return (design, hard, soft). design is None when the reply has no usable shape at all."""
    reader = _Reader()
    if not isinstance(raw, dict):
        return None, ['输出不是 JSON 对象'], []
    options = context.get('teaching_options') or {}
    allowed = _allowed_sources(context)
    known = set(context['topic'].get('knowledge_ids', []))
    focus = raw.get('focus') if isinstance(raw.get('focus'), dict) else {}
    if not focus:
        reader.hard.append('缺少 focus（重点、难点、本课不展开的内容）')
    design = dict(
        version=VERSION, title=reader.text(raw, 'title', 60, '课堂设计'),
        focus=dict(key_points=reader.lines(focus, 'key_points', 60, 1, 2, '课堂设计'),
                   difficulties=reader.lines(focus, 'difficulties', 60, 0, 2, '课堂设计'),
                   not_today=reader.lines(focus, 'not_today', 80, 0, 4, '课堂设计')),
        learner=reader.text(raw, 'learner', 400, '学情与对策', required=False),
        notation=[], objectives=[], segments=[], items=[], plan_links=[], figures=[],
        board_plan=dict(main=reader.lines(raw.get('board_plan') or {}, 'main', 120, 0, 10, '主板书'),
                        side=reader.lines(raw.get('board_plan') or {}, 'side', 120, 0, 6, '副板书')),
        summary=reader.lines(raw, 'summary', 100, 1, 5, '课堂设计'))
    for entry in reader.records(raw, 'notation', 0, 10, '课堂设计'):
        symbol, meaning = reader.text(entry, 'symbol', 60, '符号表'), reader.text(entry, 'meaning', 80, '符号表')
        if symbol and meaning:
            design['notation'].append(dict(symbol=symbol, meaning=meaning))

    # The item bank first: segments and objectives refer to it.
    seen = set()
    for number, entry in enumerate(reader.records(raw, 'items', 1, 16, '课堂设计'), 1):
        where = f'第 {number} 道题'
        identity = reader.text(entry, 'id', 8, where)
        role, level = entry.get('role'), entry.get('level', 'basic')
        if not re.fullmatch(r'[A-Za-z]{1,3}\d{1,2}', identity) or identity in seen:
            reader.hard.append(f'{where}的 id「{identity}」无效或重复')
            continue
        if not isinstance(role, str) or role not in ROLES:
            reader.hard.append(f'题目 {identity} 的 role 应为 {"、".join(ROLES)} 之一')
            continue
        seen.add(identity)
        steps = []
        for step in reader.records(entry, 'steps', 1 if role == 'example' else 0, 8, f'题目 {identity}'):
            do = reader.text(step, 'do', 300, f'题目 {identity} 的步骤')
            if do:
                steps.append(dict(do=do, why=reader.text(step, 'why', 160, f'题目 {identity} 的步骤', required=False)))
        knowledge = entry.get('knowledge_id')
        design['items'].append(dict(
            id=identity, role=role, level=level if isinstance(level, str) and level in LEVELS else 'basic',
            knowledge_id=knowledge if knowledge in known else None,
            title=reader.text(entry, 'title', 30, f'题目 {identity}', required=False),
            stem=reader.text(entry, 'stem', 800, f'题目 {identity}'), steps=steps,
            answer=reader.text(entry, 'answer', 600, f'题目 {identity}'),
            check=reader.text(entry, 'check', 300, f'题目 {identity}', required=False),
            mistake=reader.text(entry, 'mistake', 200, f'题目 {identity}', required=False),
            sources=_sources(reader, entry, f'题目 {identity}', allowed)))
    bank = {item['id']: item for item in design['items']}

    seen, used = set(), {}
    for number, entry in enumerate(reader.records(raw, 'segments', 2, 12, '课堂设计'), 1):
        where = f'第 {number} 个环节'
        identity = reader.text(entry, 'id', 8, where)
        kind, minutes = entry.get('kind'), entry.get('minutes')
        if not re.fullmatch(r'[A-Za-z]{1,3}\d{1,2}', identity) or identity in seen:
            reader.hard.append(f'{where}的 id「{identity}」无效或重复')
            continue
        if not isinstance(kind, str) or kind not in KINDS:
            reader.hard.append(f'环节 {identity} 的 kind 应为 {"、".join(KINDS)} 之一')
            continue
        if isinstance(minutes, float) and minutes == int(minutes):
            minutes = int(minutes)
        if type(minutes) is not int or not 1 <= minutes <= 180:
            reader.hard.append(f'环节 {identity} 的 minutes 应为 1—180 的整数')
            continue
        seen.add(identity)
        refs = entry.get('items') or []
        if not isinstance(refs, list):
            refs = []
        chosen = []
        for ref in refs:
            ref = str(ref).strip()
            if ref not in bank:
                reader.hard.append(f'环节 {identity} 引用了不存在的题目 {ref}')
            elif ref in used:
                reader.hard.append(f'题目 {ref} 被环节 {used[ref]} 和 {identity} 重复使用；每道题只放在一个环节')
            else:
                used[ref] = identity
                chosen.append(ref)
        questions = []
        for question in reader.records(entry, 'questions', 0, 3, f'环节 {identity}'):
            ask = reader.text(question, 'ask', 200, f'环节 {identity} 的提问')
            if ask:
                level = question.get('level', 'basic')
                questions.append(dict(
                    ask=ask, expect=reader.text(question, 'expect', 300, f'环节 {identity} 的提问', required=False),
                    wrong=reader.text(question, 'wrong', 200, f'环节 {identity} 的提问', required=False),
                    follow_up=reader.text(question, 'follow_up', 200, f'环节 {identity} 的提问', required=False),
                    level=level if isinstance(level, str) and level in LEVELS else 'basic'))
        ids = entry.get('knowledge_ids') or []
        design['segments'].append(dict(
            id=identity, kind=kind, minutes=minutes,
            title=_TITLE_PREFIX.sub('', reader.text(entry, 'title', 40, f'环节 {identity}')) or KINDS[kind],
            focus=entry.get('focus') is True,
            knowledge_ids=[k for k in ids if k in known] if isinstance(ids, list) else [],
            screen=reader.text(entry, 'screen', 160, f'环节 {identity}', required=False),
            board=reader.lines(entry, 'board', 120, 0, 5, f'环节 {identity}'),
            teacher=reader.lines(entry, 'teacher', 160, 1, 6, f'环节 {identity}'),
            students=reader.text(entry, 'students', 120, f'环节 {identity}', required=False),
            questions=questions, items=chosen,
            if_short=reader.text(entry, 'if_short', 120, f'环节 {identity}', required=False),
            sources=_sources(reader, entry, f'环节 {identity}', allowed)))
    segments = {segment['id']: segment for segment in design['segments']}
    for item in design['items']:
        if item['role'] != 'homework' and item['id'] not in used:
            reader.hard.append(f'题目 {item["id"]} 没有放进任何环节')

    for number, entry in enumerate(reader.records(raw, 'objectives', 1, 4, '课堂设计'), 1):
        refs = entry.get('checked_by') if isinstance(entry.get('checked_by'), list) else []
        checked = [str(ref).strip() for ref in refs if str(ref).strip() in bank]
        text = reader.text(entry, 'text', 120, f'第 {number} 条目标')
        if text:
            if not checked:
                reader.soft.append(f'第 {number} 条目标没有对应的练习或作业来检验（checked_by 为空或编号不存在）')
            design['objectives'].append(dict(id=f'O{number}', text=text, checked_by=checked))

    plan = context.get('personalization_plan') or []
    names = {entry['knowledge_id']: entry['name'] for entry in plan}
    for number, entry in enumerate(raw.get('plan_links') if isinstance(raw.get('plan_links'), list) else [], 1):
        if not isinstance(entry, dict) or not names:
            continue
        knowledge = entry.get('knowledge_id')
        if isinstance(knowledge, str) and knowledge.strip().lstrip('K').isdigit():
            knowledge = int(knowledge.strip().lstrip('K'))
        target = entry.get('segment')
        if isinstance(target, list):
            target = next((ref for ref in target if ref in segments), None)
        if knowledge not in names:
            reader.soft.append(f'plan_links 第 {number} 项的 knowledge_id「{entry.get("knowledge_id")}」不在本次讲解安排里，可用的是 '
                               + '、'.join(str(k) for k in names))
        elif target not in segments:
            reader.soft.append(f'plan_links 第 {number} 项的 segment「{entry.get("segment")}」不是已有的环节编号')
        elif knowledge not in {link['knowledge_id'] for link in design['plan_links']}:
            how = reader.text(entry, 'how', 160, '讲解安排落实位置')
            if how:
                design['plan_links'].append(dict(knowledge_id=knowledge, segment=target, how=how))
    missing = [names[k] for k in names if k not in {link['knowledge_id'] for link in design['plan_links']}]
    if missing:
        reader.soft.append('本次讲解安排里的「' + '」「'.join(missing) + '」没有在 plan_links 中落到具体环节；'
                           'personalization_plan 有几条，plan_links 就要有几项')

    for entry in (raw.get('figures') if isinstance(raw.get('figures'), list) else [])[:3]:
        if not isinstance(entry, dict) or entry.get('segment') not in segments or not isinstance(entry.get('spec'), dict):
            reader.soft.append('figures 中有一项缺少有效的 segment 或 spec，未采用')
            continue
        drawn = figures.render(json.dumps(entry['spec'], ensure_ascii=False))
        if not drawn['ok']:
            reader.soft.append(f'环节 {entry["segment"]} 的图无法绘制（{drawn["error"]}），未采用')
            continue
        reader.soft.extend(f'环节 {entry["segment"]} 的图：{warning}' for warning in drawn['warnings'])
        design['figures'].append(dict(segment=entry['segment'], spec=entry['spec']))

    # Counts and time. Reported, retried once, then left to the teacher: they may want slack.
    if design['segments']:
        duration = options.get('duration')
        total = sum(segment['minutes'] for segment in design['segments'])
        if duration and total != duration:
            reader.soft.append(f'各环节合计 {total} 分钟，授课时长是 {duration} 分钟，两者要相等')
        for segment in design['segments']:
            for role, least in MIN_MINUTES.items():
                count = sum(bank[ref]['role'] == role for ref in segment['items'])
                if count and segment['minutes'] < least * count:
                    reader.soft.append(f'环节 {segment["id"]}「{segment["title"]}」有 {count} 道{ROLE_NAMES[role]}却只有 {segment["minutes"]} 分钟；'
                                       f'每道至少 {least} 分钟，请减题或加时间')
        focused = sum(segment['focus'] for segment in design['segments'])
        if not 1 <= focused <= 3:
            reader.soft.append(f'应有 1—3 个重点环节（focus 为 true），现在 {focused} 个')
    for role, key in (('example', 'examples'), ('practice', 'exercises')):
        count = sum(item['role'] == role for item in design['items'])
        if key in options and count != options[key]:
            reader.soft.append(f'{ROLE_NAMES[role]}应为 {options[key]} 道（授课设置），现在 {count} 道')

    text = json.dumps(design, ensure_ascii=False)
    stray = sorted({f'{kind}{number}' for kind, number in re.findall(r'\[([MV])(\d+)\]', text)} - allowed)
    if stray:
        reader.hard.append('正文里引用了不存在的来源编号 ' + '、'.join(stray))
    if context.get('personalization_mode') == 'none' and design['learner']:
        reader.soft.append('没有提供学生信息时 learner 应为空，不猜测学生情况')
    name = (context.get('student') or {}).get('name', '')
    if context.get('personalization_mode') == 'profile' and len(name) >= 2 and name in student_text(design):
        reader.soft.append('学生会看到的内容（目标、屏幕、板书、题目、小结）里出现了学生姓名，请去掉')
    for item in design['items']:
        body = '\n'.join([item['stem']] + [step['do'] for step in item['steps']] + [item['answer'], item['check']])
        for finding in mathcheck.check_text(body)['findings'][:2]:
            reader.soft.append(f'题目 {item["id"]} 的{ARITHMETIC}：{finding["detail"]}')
    unique = lambda problems: list(dict.fromkeys(problems))
    return design, unique(reader.hard), unique(reader.soft)


def student_text(design):
    """Everything a student will read: objectives, screens, board, items, summary."""
    parts = [design['title'], *[o['text'] for o in design['objectives']], *design['summary'],
             *design['board_plan']['main'], *design['board_plan']['side']]
    for segment in design['segments']:
        parts += [segment['title'], segment['screen'], *segment['board']]
    for item in design['items']:
        parts += [item['title'], item['stem'], item['answer'], item['check'], *[s['do'] for s in item['steps']]]
    return '\n'.join(parts)


# ---------------------------------------------------------------- derived views

def labels(design):
    """Display names of the items in classroom order: 例1, 练2, 作业1."""
    bank = {item['id']: item for item in design['items']}
    order = [ref for segment in design['segments'] for ref in segment['items']]
    order += [item['id'] for item in design['items'] if item['id'] not in order]
    counts, result = {}, {}
    for ref in order:
        role = bank[ref]['role']
        counts[role] = counts.get(role, 0) + 1
        result[ref] = f'{ROLES[role]}{counts[role]}'
    return result


def writers(design):
    """Segments whose explanation is written by a second model call."""
    return [segment for segment in design['segments'] if segment['kind'] in WRITTEN or segment['focus']]


def budgets(design, options):
    """Upper (and, for focus segments, lower) character bounds of each written segment."""
    rate = RATES.get((options or {}).get('detail', 'standard'), RATES['standard'])
    duration = sum(segment['minutes'] for segment in design['segments']) or 1
    target = (options or {}).get('target_characters') or 0
    scale = target / (duration * rate) if target else 1
    result = {}
    for segment in writers(design):
        high = max(120, round(segment['minutes'] * rate * scale * (1.4 if segment['focus'] else 1)))
        result[segment['id']] = dict(low=round(high * 0.4) if segment['focus'] else 0, high=high)
    return result


def humanize(design, text):
    """Replace internal ids with the names a reader sees: E1 -> 例1, S2 -> 第 2 环节. Formulas and figure data are left alone."""
    names = {**{segment['id']: f'第 {n} 环节' for n, segment in enumerate(design['segments'], 1)}, **labels(design)}
    if not names:
        return text
    pattern = re.compile(r'(?<![A-Za-z0-9_\\\[])(' + '|'.join(sorted(map(re.escape, names), key=len, reverse=True))
                         + r')(?![A-Za-z0-9_])')
    lines, fenced = [], False
    for line in text.split('\n'):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        elif not fenced:
            parts = re.split(r'(\$\$.*?\$\$|\$[^$\n]*\$)', line)  # odd positions are formulas
            line = ''.join(part if index % 2 else pattern.sub(lambda m: names[m[1]], part) for index, part in enumerate(parts))
        lines.append(line)
    return '\n'.join(lines)


def segment_title(segment):
    """「例题：泊松概率计算」; the kind is left out when the title already says it."""
    title = segment['title']
    return title if any(word in title for word in KIND_WORDS[segment['kind']]) else f'{KINDS[segment["kind"]]}：{title}'


def heading(design, segment):
    index = design['segments'].index(segment)
    number = ORDINALS[index] if index < len(ORDINALS) else str(index + 1)
    return f'## {number}、{segment_title(segment)}（{segment["minutes"]} 分钟）' + ('· 重点' if segment['focus'] else '')


def _cite(sources, text=''):
    return ''.join(f' [{name}]' for name in sources if f'[{name}]' not in text)


def item_block(design, identity, answers=True):
    """One example or exercise exactly as every document shows it. answers=False is the student view."""
    item = next(item for item in design['items'] if item['id'] == identity)
    name = labels(design)[identity]
    level = f'（{LEVELS[item["level"]]}）' if item['role'] != 'example' or item['level'] != 'basic' else ''
    title = f'### {name}{level}' + (f' {item["title"]}' if item['title'] else '')
    if not answers:
        # Source markers and solutions are the teacher's; the student gets the problem only.
        return humanize(design, '\n'.join([title, '', '**题目**　' + re.sub(r'[ \t]*\[[MV]\d+\]', '', item['stem'])]))
    lines = [title, '', f'**题目**　{item["stem"]}' + _cite(item['sources'], item['stem'])]
    if item['steps']:
        lines += ['', '**解答**', '']
        lines += [f'{n}. {step["do"]}' + (f'　——{step["why"]}' if step['why'] else '')
                  for n, step in enumerate(item['steps'], 1)]
    lines += ['', f'**答案**　{item["answer"]}']
    if item['check']:
        lines += ['', f'**检验**　{item["check"]}']
    if item['mistake']:
        lines += ['', f'**易错**　{item["mistake"]}']
    return humanize(design, '\n'.join(lines))


def schedule(design):
    """The one-page run sheet: when, what, who does what, what goes on the board."""
    names = labels(design)
    rows, clock = ['| 时间 | 环节 | 教师 | 学生 | 板书 |', '| --- | --- | --- | --- | --- |'], 0
    cell = lambda text: re.sub(r'\s+', ' ', text).replace('|', '\\|') or '—'
    for segment in design['segments']:
        title = segment_title(segment) + ('（重点）' if segment['focus'] else '')
        used = '、'.join(names[ref] for ref in segment['items'])
        teacher = '；'.join(segment['teacher']) + (f'（{used}）' if used else '')
        rows.append(f'| {clock}—{clock + segment["minutes"]} | {cell(title)} | {cell(teacher)} | '
                    f'{cell(segment["students"])} | {cell("；".join(segment["board"]))} |')
        clock += segment['minutes']
    return humanize(design, '\n'.join(rows))


def overview(design, context):
    names = labels(design)
    parts = ['## 本课要点', '**重点**　' + '；'.join(design['focus']['key_points'])]
    if design['focus']['difficulties']:
        parts.append('**难点**　' + '；'.join(design['focus']['difficulties']))
    if design['focus']['not_today']:
        parts.append('**本课不展开**　' + '；'.join(design['focus']['not_today']))
    goals = []
    for number, objective in enumerate(design['objectives'], 1):
        checked = '、'.join(names[ref] for ref in objective['checked_by'])
        goals.append(f'{number}. {objective["text"]}' + (f'（由{checked}检验）' if checked else ''))
    parts += ['**学习目标**', '\n'.join(goals)]
    if design['notation']:
        parts.append('**符号**　' + '；'.join(f'{n["symbol"]} 表示{n["meaning"]}' for n in design['notation']))
    if design['learner'] or design['plan_links']:
        parts.append('**学情与对策**　' + design['learner'])
        plan = {entry['knowledge_id']: entry['name'] for entry in context.get('personalization_plan') or []}
        order = {segment['id']: n for n, segment in enumerate(design['segments'], 1)}
        if design['plan_links']:
            parts.append('\n'.join(f'- {plan.get(link["knowledge_id"], link["knowledge_id"])}：{link["how"]}（第 {order[link["segment"]]} 环节）'
                                   for link in design['plan_links']))
    return humanize(design, '\n\n'.join(parts + ['**课堂流程**', schedule(design)]))


def segment_block(design, segment, prose=''):
    """A segment of the teacher's book. prose is the written explanation, if this segment has one."""
    lines = [heading(design, segment), '']
    facts = [('屏幕', segment['screen']), ('板书', '；'.join(segment['board'])), ('学生', segment['students'])]
    shown = [f'- **{label}**：{value}' for label, value in facts if value]
    if shown:
        lines += shown + ['']
    bank = {item['id']: item for item in design['items']}
    names = labels(design)
    body = prose.strip() or '\n'.join(f'- {move}' for move in segment['teacher'])
    # Homework is printed once, in its own section; a segment only says which problems are set.
    blocks = [item_block(design, ref) for ref in segment['items'] if bank[ref]['role'] != 'homework']
    homework = [f'{names[ref]}（{LEVELS[bank[ref]["level"]]}）' for ref in segment['items'] if bank[ref]['role'] == 'homework']
    if segment['kind'] == 'summary' and design['summary']:
        body += '\n\n**小结要点**\n\n' + '\n'.join(f'- {point}' for point in design['summary'])
    if homework:
        body += '\n\n**布置作业**　' + '、'.join(homework) + '，题目见「课后作业」。'
    # Explanation leads a teaching segment; in an example or exercise the problem comes first.
    ordered = [body, *blocks] if segment['kind'] in ('concept', 'intro', 'summary', 'homework') else [*blocks, body]
    lines.append('\n\n'.join(part for part in ordered if part))
    for figure in design['figures']:
        if figure['segment'] == segment['id']:
            lines += ['', '```figure', json.dumps(figure['spec'], ensure_ascii=False), '```']
    for number, question in enumerate(segment['questions'], 1):
        lines += ['', f'**提问 {number}**（{LEVELS[question["level"]]}）{question["ask"]}']
        lines += [f'- {label}：{question[key]}' for key, label in
                  (('expect', '预期回答'), ('wrong', '常见错答'), ('follow_up', '追问')) if question[key]]
    if segment['if_short']:
        lines += ['', f'**时间不够时**　{segment["if_short"]}']
    if segment['sources'] and not re.search(r'\[[MV]\d+\]', '\n'.join(lines)):
        lines += ['', '依据：' + _cite(segment['sources']).strip()]
    return humanize(design, '\n'.join(lines))


def document(design, prose, context):
    """The teacher's book as Markdown: overview, run sheet, segments, board plan, homework."""
    parts = [f'# {design["title"]}', overview(design, context)]
    parts += [segment_block(design, segment, prose.get(segment['id'], '')) for segment in design['segments']]
    if not any(segment['kind'] == 'summary' for segment in design['segments']) and design['summary']:
        parts.append('## 小结\n\n' + '\n'.join(f'- {point}' for point in design['summary']))
    board = design['board_plan']
    if board['main'] or board['side']:
        lines = ['## 板书设计', '']
        if board['main']:
            lines += ['**主板书**（整节课保留）', ''] + [f'{n}. {line}' for n, line in enumerate(board['main'], 1)]
        if board['side']:
            lines += ['', '**副板书**（演算区）', ''] + [f'- {line}' for line in board['side']]
        parts.append('\n'.join(lines))
    homework = [item['id'] for item in design['items'] if item['role'] == 'homework']
    if homework:
        parts.append('## 课后作业\n\n' + '\n\n'.join(item_block(design, ref) for ref in homework))
    return humanize(design, '\n\n'.join(parts)) + '\n'


def report(design, context):
    """Structural facts for the program check; every entry is (code, label, passed, detail)."""
    options = context.get('teaching_options') or {}
    bank = {item['id']: item for item in design['items']}
    total, duration = sum(s['minutes'] for s in design['segments']), options.get('duration')
    tight = [f'「{segment_title(s)}」{s["minutes"]} 分钟 {len(s["items"])} 题' for s in design['segments']
             if any(sum(bank[ref]['role'] == role for ref in s['items']) * least > s['minutes']
                    for role, least in MIN_MINUTES.items())]
    counts = {role: sum(item['role'] == role for item in design['items']) for role in ROLES}
    unchecked = [o['text'] for o in design['objectives'] if not o['checked_by']]
    plan = context.get('personalization_plan') or []
    linked = {link['knowledge_id'] for link in design['plan_links']}
    unlinked = [entry['name'] for entry in plan if entry['knowledge_id'] not in linked]
    wanted = [(role, options[key]) for role, key in (('example', 'examples'), ('practice', 'exercises')) if key in options]
    result = [
        ('design_time', '课堂时间', (not duration or total == duration) and not tight,
         f'各环节合计 {total} 分钟' + (f'，授课时长 {duration} 分钟' if duration else '')
         + ('；时间偏紧的环节：' + '；'.join(tight) if tight else '；例题每道不少于 4 分钟、练习每道不少于 3 分钟')
         + '。这是按题数估算的下限，实际用时需教师判断。'),
        ('design_items', '例题与练习数量', all(counts[role] == number for role, number in wanted),
         f'例题 {counts["example"]} 道、当堂练习 {counts["practice"]} 道、课后作业 {counts["homework"]} 道'
         + ('；授课设置为' + '、'.join(f'{ROLE_NAMES[role]} {number} 道' for role, number in wanted) if wanted else '') + '。'),
        ('design_objectives', '目标与检验题对应', not unchecked,
         '以下目标没有对应的练习或作业：' + '；'.join(unchecked) if unchecked
         else f'{len(design["objectives"])} 条目标都指定了检验它的题目；题目是否真能检验目标需教师判断。'),
        ('design_focus', '重点与难点', 1 <= len(design['focus']['key_points']) <= 2,
         f'重点 {len(design["focus"]["key_points"])} 条、难点 {len(design["focus"]["difficulties"])} 条、'
         f'本课不展开 {len(design["focus"]["not_today"])} 条；取舍是否合适需教师判断。'),
    ]
    if plan:
        result.append(('design_plan', '讲解安排落实位置', not unlinked,
                       '以下讲解安排没有落到环节：' + '、'.join(unlinked) if unlinked
                       else '每条讲解安排都指明了落实的环节；是否真正落实、是否适合学生需教师判断。'))
    return result
