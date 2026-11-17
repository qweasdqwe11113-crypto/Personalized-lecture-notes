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
from collections import Counter
import copy
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
BLANK = re.compile(r'_{4,}')        # a gap the student fills in on the handout
ARITHMETIC = '算式复算不一致'   # a possible false alarm: reported, but never the only reason for a retry
TRIMMED = '只保留了前'          # an over-long list was cut: reported, not worth another model call
UNCHECKED = '里没有这条目标的内容'  # judged by shared words only: shown to the teacher, never the only reason for a retry
FLAG = '待核对：'               # a line the model wrote to point at something it could not reconcile
AUDIENCES = {'profile': 'student', 'class': 'class', 'none': 'general'}   # who the lesson is for, from personalization_mode
# Classroom organisation that has no place in one-to-one tutoring.
CLASSROOM = re.compile(r'巡视|小组|分组|举手|全班|全体|组长|同桌|各组|(?:学有余力|基础薄弱|基础待巩固)的(?:学生|同学)')
CHECKING = ('practice', 'homework')   # the roles whose answers show whether an objective was reached
# Characters of an answer, formulas' command names and spaces left out, beyond which it is a worked solution
# that belongs in the steps: (without steps, with steps). Real answers ran 24-80; written-out solutions 73-105.
ANSWER_LIMIT = (60, 100)
# Characters that glue words together: a pair holding one (「母为」 of 「分母为正」) is not a word.
FUNCTION_CHARS = set('的为是和与了在及或并且把被要各其之')
# Words any objective or problem may use; sharing one says nothing about checking that objective.
GENERIC = {'计算', '求解', '写出', '说出', '能够', '完成', '理解', '掌握', '运用', '利用', '结果', '比较', '说明', '验证',
           '检验', '核对', '区分', '给出', '得到', '并用', '学生', '问题', '题目', '方法', '能用', '能写', '能说', '能从',
           '能在', '能列', '能判', '能区', '能把', '能根', '根据', '已知', '求出'}
# A formula says in symbols what an objective says in words: P(A_1\mid D) is a conditional probability.
SPOKEN = {'mid': '条件概率', 'frac': '分母', 'dfrac': '分母', 'tfrac': '分母', 'cap': '交集', 'cup': '并集',
          'binom': '组合数', 'sum': '求和', 'int': '积分'}
# Words about the data or the writing, not about the lesson: a teacher or student can do nothing with them.
META = re.compile(r'依据诊断|根据诊断|未诊断|不预判|(?:暂无|没有|均无|并无|尚无)[^，。；\s]{0,4}(?:诊断|数据)|无(?:前测|诊断)数据|待核对|仍需(?:确认|核对)'
                  r'|根据[^，。；\s]{0,10}知识点|本题考查|仅供参考|提示词')
# A sentence cut off (「追问“分母应该是谁的概...”」), not a quoted pattern such as 「只有……才」.
UNFINISHED = re.compile(r"[一-鿿](?:\.{3,}|…+)(?=[ \t]*(?:[”\"’'」』)）。，；;,]|$))", re.M)
ABBREVIATION = re.compile(r'(?<![A-Za-z\\])(?:PMF|PDF|CDF|MGF|pmf|pdf|cdf)(?![A-Za-z])')
HOMEWORK_MINUTES = 2            # setting homework printed on the handout needs no more
# "（注：本题数据为自编假设模型。）": a note for the teacher that the model put into the problem itself.
_MADE_UP = r'(?:自编|虚构|编造|假设模型|数据(?:为|是|均为)假设)'
_SELF_MADE = re.compile(r'[ \t]*[（(][ \t]*(?:注|说明)?[ \t]*[:：]?[^（）()\n]{0,30}?' + _MADE_UP + r'[^（）()\n]{0,30}[）)]'
                        r'|[ \t]*(?:注|说明)[:：][^。\n]{0,40}?' + _MADE_UP + r'[^。\n]{0,40}(?:。|$)')
# In a check (printed under a worked example and on the slides) the note may stand as a sentence of its own.
_MADE_UP_SENTENCE = re.compile(r'[^。；;\n]*' + _MADE_UP + r'[^。；;\n]*(?:[。；;]|$)')
_NUMBER = re.compile(r'(?<![A-Za-z_{.\d])\d+(?:\.\d+)?')       # not the 1 of A1 or A_1
FIELDS = {'title': '标题', 'stem': '题干', 'answer': '答案', 'steps': '解答步骤', 'do': '步骤内容', 'teacher': '教师活动',
          'key_points': '重点', 'difficulties': '难点', 'not_today': '本课不展开', 'summary': '小结', 'text': '内容',
          'items': '题目', 'segments': '环节', 'objectives': '学习目标', 'ask': '问题', 'minutes': '分钟数',
          'notes': '学案笔记', 'board': '板书', 'questions': '提问', 'notation': '符号表'}
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
        self.hard, self.soft, self.notes = [], [], []   # notes: shown to the teacher, never a reason to retry

    def text(self, obj, key, limit, where, required=True, label=None):
        name = label or _name(key)
        value = obj.get(key) if isinstance(obj, dict) else None
        if value is None and not required:
            return ''
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, list) and value and all(isinstance(part, str) for part in value):
            value = '；'.join(part.strip() for part in value if part.strip())  # a multi-part answer written as a list
        if not isinstance(value, str):
            self.hard.append(f'{where}的{name}应为文字')
            return ''
        value = re.sub(r'[ \t\r]+', ' ', value).strip()
        # Quoted speech arrives in ASCII quotes because they need no escaping in JSON.
        value = re.sub(r"'([^'\n]{0,60}[一-鿿][^'\n]{0,60})'", r'“\1”', value)
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
            self.hard.append(f'{where}的{name}里有损坏的公式：JSON 字符串中的每个反斜杠要写成两个')
            value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
        if required and not value:
            self.hard.append(f'{where}缺少{name}')
        if len(value) > limit:
            self.hard.append(f'{where}的{name}超过 {limit} 字，请精简')
            value = value[:limit]
        return value

    def lines(self, obj, key, limit, low, high, where, strict=False):
        value = obj.get(key) if isinstance(obj, dict) else None
        if value is None:
            value = []
        if isinstance(value, str):
            value = [value] if value.strip() else []
        if not isinstance(value, list):
            self.hard.append(f'{where}的{_name(key)}应为列表')
            return []
        # A blank entry is the model spacing a list out (an empty line between problems on the board): dropped, not an error.
        result = [self.text({'item': item}, 'item', limit, f'{where}的{_name(key)}', label=f'第 {number} 条')
                  for number, item in enumerate(value, 1) if not (isinstance(item, str) and not item.strip())]
        result = [item for item in result if item]
        if len(result) < low:
            self.hard.append(f'{where}的{_name(key)}应有 {low}—{high} 条，现在 {len(result)} 条')
        elif len(result) > high:
            (self.soft if strict else self.notes).append(
                f'{where}的{_name(key)}有 {len(result)} 条，超过 {high} 条' + ('' if strict else f'，{TRIMMED} {high} 条'))
        return result[:high]

    def records(self, obj, key, low, high, where):
        value = obj.get(key) if isinstance(obj, dict) else None
        if value is None:
            value = []
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            self.hard.append(f'{where}的{_name(key)}应为对象列表')
            return []
        if len(value) < low:
            self.hard.append(f'{where}的{_name(key)}应有 {low}—{high} 项，现在 {len(value)} 项')
        elif len(value) > high:
            self.notes.append(f'{where}的{_name(key)}有 {len(value)} 项，超过 {high} 项，{TRIMMED} {high} 项')
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
        version=VERSION, audience=AUDIENCES.get(context.get('personalization_mode', 'profile'), 'student'),
        title=reader.text(raw, 'title', 60, '课堂设计'),
        focus=dict(key_points=reader.lines(focus, 'key_points', 80, 1, 2, '课堂设计', strict=True),
                   difficulties=reader.lines(focus, 'difficulties', 80, 0, 2, '课堂设计'),
                   not_today=reader.lines(focus, 'not_today', 80, 0, 4, '课堂设计')),
        learner=reader.text(raw, 'learner', 400, '学情与对策', required=False),
        notation=[], objectives=[], segments=[], items=[], plan_links=[], figures=[],
        board_plan=dict(main=reader.lines(raw.get('board_plan') or {}, 'main', 120, 0, 10, '主板书'),
                        side=reader.lines(raw.get('board_plan') or {}, 'side', 120, 0, 6, '副板书')),
        summary=reader.lines(raw, 'summary', 100, 0, 5, '课堂设计'))
    if not design['summary']:
        reader.soft.append('缺少小结（summary）：写 2—4 条学生口吻的要点')
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
        answer = reader.text(entry, 'answer', 600, f'题目 {identity}')
        if len(_bare(answer)) > ANSWER_LIMIT[bool(steps)]:
            reader.soft.append(f'题目 {identity} 的答案（answer）是一整段解答；answer 只写最终结果（几小问用“(1)……；(2)……”），'
                               '推导、判断条件和中间算式写进 steps')
        design['items'].append(dict(
            id=identity, role=role, level=level if isinstance(level, str) and level in LEVELS else 'basic',
            knowledge_id=knowledge if knowledge in known else None,
            title=reader.text(entry, 'title', 30, f'题目 {identity}', required=False),
            stem=without_note(reader.text(entry, 'stem', 800, f'题目 {identity}')), steps=steps, answer=answer,
            check=without_note(reader.text(entry, 'check', 300, f'题目 {identity}', required=False), sentence=True),
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
        notes = []
        for note in reader.records(entry, 'notes', 0, 5, f'环节 {identity}'):
            text = reader.text(note, 'text', 240, f'环节 {identity} 的学案笔记')
            answer = reader.text(note, 'answer', 200, f'环节 {identity} 的学案笔记', required=False)
            if text:
                if BLANK.search(text) and not answer:
                    reader.soft.append(f'环节 {identity} 的学案笔记「{text[:20]}」留了空却没有写答案')
                notes.append(dict(text=text, answer=answer if BLANK.search(text) else ''))
        ids = entry.get('knowledge_ids') or []
        design['segments'].append(dict(
            id=identity, kind=kind, minutes=minutes,
            title=_TITLE_PREFIX.sub('', reader.text(entry, 'title', 40, f'环节 {identity}')) or KINDS[kind],
            focus=entry.get('focus') is True,
            knowledge_ids=[k for k in ids if k in known] if isinstance(ids, list) else [],
            screen=reader.text(entry, 'screen', 160, f'环节 {identity}', required=False),
            board=reader.lines(entry, 'board', 120, 0, 8, f'环节 {identity}'),
            teacher=reader.lines(entry, 'teacher', 160, 1, 8, f'环节 {identity}'),
            students=reader.text(entry, 'students', 120, f'环节 {identity}', required=False),
            questions=questions, notes=notes, items=chosen,
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
            elif not any(bank[ref]['role'] in CHECKING for ref in checked):
                reader.soft.append(f'第 {number} 条目标只写由 {"、".join(checked)} 检验；例题和前测是课上讲的，检验要用当堂练习或课后作业：'
                                   '把真正考到这条目标的练习或作业写进 checked_by，没有就改一道练习或作业来考它')
            design['objectives'].append(dict(id=f'O{number}', text=text, checked_by=checked))
    for number, ref, words in objective_gaps(design):
        reader.soft.append(f'第 {number} 条目标写由 {ref} 检验，但 {ref} 的题干、步骤和答案{UNCHECKED}（如「'
                           + '」「'.join(words) + '」）；checked_by 只写真正考到这条目标的题')
    for ref, earlier in repeated_items(design):
        reader.soft.append(f'{ref} 用的是 {earlier} 的同一组数据，问的结果 {earlier} 已经算过；换数据、换情境或换问法')

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
        for segment in design['segments']:
            if segment['kind'] == 'homework' and segment['minutes'] > HOMEWORK_MINUTES:
                reader.soft.append(f'环节 {segment["id"]}「布置作业」占了 {segment["minutes"]} 分钟；作业已经印在学案上，指明题号即可，'
                                   f'最多 {HOMEWORK_MINUTES} 分钟，也可以并进小结，省下的时间给练习和讲评')
    if design['audience'] == 'student':
        for segment in design['segments']:
            words = sorted(set(CLASSROOM.findall(classroom_text(segment))))
            if words:
                reader.soft.append(f'这是一对一辅导，环节 {segment["id"]} 写了「' + '」「'.join(words)
                                   + '」；改成教师和这一名学生之间的动作（让学生说思路、看学生写到哪一步）')
    found = wording(design)
    for kind, where, phrase in found:
        if kind == 'meta':
            reader.soft.append(f'{where}写了「{phrase}」：这是关于数据或生成过程的话，教师和学生用不上；直接写学生的情况和这节课怎么做')
        elif kind == 'unfinished':
            reader.soft.append(f'{where}有一句没写完（「{phrase}」）；把这句写完整，不用省略号')
    short = [(where, phrase) for kind, where, phrase in found if kind == 'abbreviation']
    if short:
        reader.soft.append('、'.join(dict.fromkeys(where for where, _ in short)) + ' 写了英文缩写「'
                           + '」「'.join(dict.fromkeys(phrase for _, phrase in short))
                           + '」；学案和课件给学生看，写中文（如“概率公式”“分布函数”）')
    for ref, value, other in mismatched_values(design):
        reader.soft.append(f'题目 {ref} 的答案和步骤写 {value}，检验或板书写 {other}；同一个结果在各处写同一个值（先算出精确值再四舍五入），板书上的数要与题目算出的一致')
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
    shown = student_text(design)
    # The model is sent 「该生」 instead of the name (prompts.without_name); neither belongs on what the student sees.
    if context.get('personalization_mode') == 'profile' and (len(name) >= 2 and name in shown or re.search(r'(?<!应)该生(?![成物活产长])', shown)):
        reader.soft.append('学生会看到的内容（目标、屏幕、板书、题目、小结）里出现了学生姓名，请去掉')
    for item in design['items']:
        body = '\n'.join([item['stem']] + [step['do'] for step in item['steps']] + [item['answer'], item['check']])
        for finding in mathcheck.check_text(body)['findings'][:2]:
            reader.notes.append(f'题目 {item["id"]} 的{ARITHMETIC}：{finding["detail"]}')
    mixed = mixed_notation(design)
    if mixed:
        reader.soft.append('同一个符号有两种写法：' + '、'.join(f'{plain} 与 ${latex}$' for plain, latex in mixed[:4])
                           + '。所有字段里的数学符号都写成 LaTeX，全课只用一种写法')
    unique = lambda problems: list(dict.fromkeys(problems))
    return design, unique(reader.hard), unique(reader.soft + reader.notes)


def worth_retry(soft):
    """The reminders that justify asking the model again; the rest are only shown to the teacher."""
    return [problem for problem in soft if not any(mark in problem for mark in (ARITHMETIC, TRIMMED, UNCHECKED))]


# Fields a partial correction (prompts/lecture-design-patch-v1.md) replaces whole; segments and items go by id.
WHOLE_FIELDS = ('title', 'focus', 'learner', 'plan_links', 'notation', 'board_plan', 'figures', 'summary', 'objectives')


def merge_patch(base, patch):
    """The design base with a partial correction applied. A segment or item named by id takes the fields the patch
    gives and keeps the rest; a new item id is added; the ids in remove leave the items, the segments and every
    checked_by; the other fields are replaced whole. DesignError when the patch is not one."""
    if not isinstance(patch, dict) or not patch:
        raise DesignError('局部修正不是 JSON 对象，或者是空的')
    unknown = sorted(set(patch) - set(WHOLE_FIELDS) - {'segments', 'items', 'remove'})
    if unknown:
        raise DesignError('局部修正里有不认识的字段：' + '、'.join(unknown))
    remove = patch.get('remove') or []
    if not isinstance(remove, list) or not all(isinstance(entry, str) for entry in remove):
        raise DesignError('局部修正的 remove 应为题目编号列表')
    merged = copy.deepcopy(base)
    for key in ('segments', 'items'):
        changes = patch.get(key) or []
        if not isinstance(changes, list) or not all(isinstance(entry, dict) and isinstance(entry.get('id'), str)
                                                     for entry in changes):
            raise DesignError(f'局部修正的 {key} 应为带 id 的对象列表')
        current = [entry for entry in merged.get(key) or [] if isinstance(entry, dict)]
        where = {entry.get('id'): n for n, entry in enumerate(current)}
        for entry in changes:
            if entry['id'] in where:
                current[where[entry['id']]] = {**current[where[entry['id']]], **entry}
            elif key == 'segments':
                raise DesignError(f'局部修正不能新增环节（{entry["id"]}）')
            else:
                where[entry['id']] = len(current)
                current.append(entry)
        merged[key] = [entry for entry in current if entry.get('id') not in remove]
    for key in WHOLE_FIELDS:
        if key in patch:
            merged[key] = copy.deepcopy(patch[key])
    if remove:
        for segment in merged['segments']:
            if isinstance(segment.get('items'), list):
                segment['items'] = [ref for ref in segment['items'] if ref not in remove]
        for objective in merged.get('objectives') or []:
            if isinstance(objective, dict) and isinstance(objective.get('checked_by'), list):
                objective['checked_by'] = [ref for ref in objective['checked_by'] if ref not in remove]
    return merged


def without_note(text, sentence=False):
    """A problem as the student gets it: a note that the data are made up is the teacher's business, and the
    package's record says so once. sentence: also a plain sentence saying so (in a check, not in a stem, where
    「假设……」 may be the problem itself)."""
    text = _SELF_MADE.sub('', text)
    return (_MADE_UP_SENTENCE.sub('', text) if sentence else text).strip()


def classroom_text(segment):
    """What a segment asks the teacher and the students to do, where classroom organisation would show."""
    return '\n'.join([*segment['teacher'], segment['students'], segment['screen'], segment['if_short'],
                      *[question[key] for question in segment['questions'] for key in ('ask', 'follow_up')]])


def _spoken(text):
    """A text with LaTeX command names and E(X), D(X), Var(X) said in words."""
    text = re.sub(r'\\([A-Za-z]+)', lambda match: f' {SPOKEN.get(match[1], "")} ', text)
    return re.sub(r'(?<![A-Za-z])(?:(E)|D|Var)\s*\(', lambda match: ' 期望 (' if match[1] else ' 方差 (', text)


def _terms(text):
    """Pairs of Chinese characters and Latin words of a text, LaTeX command names left out: what a problem
    has to share with the objective it checks."""
    text = _spoken(text)
    pairs = {text[i:i + 2] for i in range(len(text) - 1)
             if re.fullmatch(r'[一-鿿]{2}', text[i:i + 2]) and not set(text[i:i + 2]) & FUNCTION_CHARS}
    return (pairs | {word.lower() for word in re.findall(r'[A-Za-z]{2,}', text)}) - GENERIC


def _bare(text):
    """A text as long as it reads: no LaTeX command names, braces, dollar signs or spaces."""
    return re.sub(r'\\[A-Za-z]+|[\s${}\\^_]', '', text)


def objective_gaps(design):
    """[(objective number, item id, words)]: a problem named as checking an objective that shares none of the
    objective's distinctive words. Words two thirds or more of the lesson's problems use (「概率」 in a probability
    lesson) and words no problem uses tell nothing and are left out; an objective with nothing left is not judged."""
    texts = {item['id']: _terms(' '.join([item['title'], item['stem'], item['answer'], item['check'],
                                          *[step['do'] for step in item['steps']]])) for item in design['items']}
    if len(texts) < 3:
        return []
    counts = Counter(term for terms in texts.values() for term in terms)
    gaps = []
    for number, objective in enumerate(design['objectives'], 1):
        own = {term for term in _terms(objective['text']) if 0 < counts[term] < len(texts) * 2 / 3}
        if len(own) < 2:   # one shared word (「参数」) is too little to say a problem does not check the objective
            continue
        gaps += [(number, ref, _phrases(objective['text'], own)[:3])
                 for ref in objective['checked_by'] if not own & texts[ref]]
    return gaps


def _phrases(text, terms):
    """The stretches of the text the terms cover, in order: 「先验变化」 rather than the pairs 「先验」「变化」,
    so the reminder quotes the objective and not fragments of it."""
    text = _spoken(text)
    found, end = [], -1
    for i in range(len(text) - 1):
        if text[i:i + 2] not in terms:
            continue
        if found and i <= end:
            found[-1] += text[end:i + 2]
        else:
            found.append(text[i:i + 2])
        end = i + 2
    words = [word for word in re.findall(r'[A-Za-z]{2,}', text) if word.lower() in terms]
    return list(dict.fromkeys(found + words))


def _numbers(text):
    """The numbers a problem gives or a solution reaches, without the 2 of 「例2」 or E2."""
    text = re.sub(r'(?:前测|例|练|作业|第)\s*\d+', ' ', text)
    return {round(float(value), 6) for value in _NUMBER.findall(text)}


def repeated_items(design):
    """[(item id, earlier item id)]: an exercise or homework problem whose data all come from the problems before
    it (an example may keep its predecessor's data: 「次品率不变」) and whose answer one of them has already worked
    out (a decimal, or two numbers, besides the data)."""
    bank = {item['id']: item for item in design['items']}
    order = [ref for ref in labels(design)]
    found, seen = [], set()
    for index, ref in enumerate(order):
        item = bank[ref]
        given = _numbers(item['stem'])
        if item['role'] not in ('practice', 'homework') or len(given) < 2 or not given <= seen:
            if item['role'] != 'homework':
                seen |= given
            continue
        reached = _numbers(item['answer'] + ' ' + ' '.join(step['do'] for step in item['steps'])) - given
        for earlier in order[:index]:
            other = bank[earlier]
            if other['role'] == 'homework':
                continue
            shared = reached & _numbers(other['answer'] + ' ' + ' '.join(step['do'] for step in other['steps']))
            if len(shared) >= 2 or any(value != int(value) for value in shared):
                found.append((ref, earlier))
                break
    return found


def _fields(design):
    """(where, text) of every sentence the model wrote into the design, for wording checks."""
    yield '学情与对策', design['learner']
    for key, name in (('key_points', '重点'), ('difficulties', '难点'), ('not_today', '本课不展开')):
        for text in design['focus'][key]:
            yield name, text
    for segment in design['segments']:
        where = f'环节 {segment["id"]}'
        yield where + ' 的屏幕', segment['screen']
        yield where + ' 的学生活动', segment['students']
        yield where + ' 的时间不够时', segment['if_short']
        for text in segment['teacher']:
            yield where + ' 的教师活动', text
        for text in segment['board']:
            yield where + ' 的板书', text
        for question in segment['questions']:
            for key in ('ask', 'expect', 'wrong', 'follow_up'):
                yield where + ' 的提问', question[key]
        for note in segment.get('notes', []):
            yield where + ' 的学案笔记', note['text'] + ' ' + note['answer']
    for item in design['items']:
        for text in [item['stem'], item['answer'], item['check'], item['mistake'],
                     *[step['do'] + ' ' + step['why'] for step in item['steps']]]:
            yield f'题目 {item["id"]}', text
    for objective in design['objectives']:
        yield '学习目标', objective['text']
    for text in design['summary']:
        yield '小结', text


def wording(design):
    """[(kind, where, phrase)] of words a teacher or student cannot use as written: talk about the data or
    the writing (「依据诊断」「待核对」), a sentence cut off with an ellipsis, an English abbreviation where
    students read. kind: 'meta' | 'unfinished' | 'abbreviation'."""
    student = re.compile(r'^(?:题目|学习目标|小结|环节 \w+ 的(?:屏幕|板书|学案笔记))')
    found = []
    for where, text in _fields(design):
        found += [('meta', where, match[0]) for match in META.finditer(text or '')]
        found += [('unfinished', where, text[max(0, match.start() - 12):match.end()]) for match in UNFINISHED.finditer(text or '')]
        if student.match(where):
            found += [('abbreviation', where, match[0]) for match in ABBREVIATION.finditer(text or '')]
    return list(dict.fromkeys(found))


def _decimals(text):
    return set(re.findall(r'(?<![\d.])\d+\.\d{2,}(?![\d])', text or ''))


def _split_top(text, separators):
    """text split at the separators that stand outside brackets: P(X=2)=0.027 splits once, not twice."""
    pieces, depth, start, index = [], 0, 0, 0
    while index < len(text):
        char = text[index]
        depth += char in '([{'
        depth -= char in ')]}'
        hit = next((sep for sep in separators if depth == 0 and text.startswith(sep, index)), None)
        if hit:
            pieces.append(text[start:index])
            start = index = index + len(hit)
            continue
        index += 1
    return pieces + [text[start:]]


def _value(text):
    """(written, number) for a plain number or a fraction of two integers, None for anything else."""
    text = re.sub(r'\s+', '', text)
    if re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        return text, float(text)
    fraction = re.fullmatch(r'\\[dt]?frac\{(\d+)\}\{(\d+)\}', text)
    return (text, int(fraction[1]) / int(fraction[2])) if fraction and int(fraction[2]) else None


def _results(text):
    """{left side: [(written, number)]} of the equations in a text's formulas: $P(D)=0.6\\times0.02+0.4\\times0.05=0.032$
    gives {'P(D)': [('0.032', 0.032)]}. Sides that are not a plain number or fraction are skipped."""
    found = {}
    for formula in re.findall(r'\$([^$\n]+)\$', text or ''):
        for clause in _split_top(formula, (',', ';', '，', '；', '\\quad', '\\;')):
            sides = _split_top(clause, ('=', '\\approx'))
            left = re.sub(r'\s+|\\left|\\right', '', sides[0])
            values = [value for value in map(_value, sides[1:]) if value]
            if values and re.search(r'[A-Za-z]', left):
                found.setdefault(left, []).extend(values)
    return found


def _close(a, b):
    """Two written values that agree to the coarser of their decimal places."""
    places = min(len(written.split('.')[1]) if '.' in written else (6 if 'frac' in written else 0) for written in (a[0], b[0]))
    return abs(a[1] - b[1]) <= 0.5 * 10 ** -places + 1e-9


def _stated(design, segment):
    """The board lines of a segment that state its own results: not a wrong working shown for contrast
    (≠, 错) and not a line naming a problem of another segment."""
    ids = {item['id'] for item in design['items']}
    for line in segment['board']:
        named = set(re.findall(r'(?<![A-Za-z0-9_\\])[A-Z]\d+(?![A-Za-z0-9_])', line)) & ids
        if not re.search(r'≠|\\neq|错', line) and not named - set(segment['items']):
            yield line


def board_slips(design):
    """[(item id, its value, board value)]: a board line gives a result of one of the segment's problems
    (the same left side, as P(C)) with a value no problem of the lesson reaches for it. Lines showing a wrong
    working (≠, 错) and lines naming a problem of another segment are left out."""
    every = {}
    for item in design['items']:
        for left, values in _results(' '.join([item['answer'], item['check'], item['mistake'],
                                               *[step['do'] for step in item['steps']]])).items():
            every.setdefault(left, []).extend(values)
    found = []
    for segment in design['segments']:
        own = {}
        for ref in segment['items']:
            item = next(item for item in design['items'] if item['id'] == ref)
            for left, values in _results(' '.join([item['answer'], *[step['do'] for step in item['steps']]])).items():
                own.setdefault(left, []).extend((ref, value) for value in values)
        for line in _stated(design, segment):
            for left, values in _results(line).items():
                if left not in own:
                    continue
                for value in values:
                    if not any(_close(value, other) for other in every.get(left, [])):
                        ref, reached = own[left][-1]
                        found.append((ref, reached[0], value[0]))
    return found


def mismatched_values(design):
    """[(item id, value, other value)]: a result written with the same number of decimals but a different last
    digit in the solution and in the check or on the board (0.1246 and 0.1247): one of them is a rounding slip.
    Also a board line giving a problem's result with a value the problem never reaches (board_slips)."""
    boards = {ref: ' '.join(_stated(design, segment)) for segment in design['segments'] for ref in segment['items']}
    found = board_slips(design)
    for item in design['items']:
        solved = _decimals(item['answer'] + ' ' + ' '.join(step['do'] for step in item['steps']))
        checked = _decimals(item['check'] + ' ' + boards.get(item['id'], '')) - solved
        pair = next(((a, c) for a in sorted(solved) for c in sorted(checked)
                     if len(a.split('.')[1]) == len(c.split('.')[1])
                     and abs(float(a) - float(c)) <= 2.5 * 10 ** -len(a.split('.')[1])), None)
        if pair:
            found.append((item['id'], *pair))
    return found


def mixed_notation(design):
    """Indexed symbols written both as plain text (A1) and as a LaTeX subscript ($A_1$), as [(plain, latex)]."""
    text = student_text(design) + '\n' + '\n'.join('\n'.join(segment['teacher']) for segment in design['segments'])
    ids = {entry['id'] for entry in design['items'] + design['segments']}
    formulas = re.findall(r'\$\$.*?\$\$|\$[^$\n]*\$', text, re.S)
    outside = re.sub(r'\$\$.*?\$\$|\$[^$\n]*\$', ' ', text, flags=re.S)
    plain = {match for match in re.findall(r'(?<![A-Za-z0-9_\[\\])[A-Z][0-9](?![A-Za-z0-9_])', outside)} - ids
    latex = {letter + digit: f'{letter}_{digit}' for formula in formulas
             for letter, digit in re.findall(r'([A-Z])_\{?([0-9])\}?', formula)}
    return sorted((name, latex[name]) for name in plain if name in latex)


def student_text(design):
    """Everything a student will read: objectives, screens, board, items, summary."""
    parts = [design['title'], *[o['text'] for o in design['objectives']], *design['summary'],
             *design['board_plan']['main'], *design['board_plan']['side']]
    for segment in design['segments']:
        parts += [segment['title'], segment['screen'], *segment['board'], *[n['text'] + n['answer'] for n in segment.get('notes', [])]]
    for item in design['items']:
        parts += [item['title'], item['stem'], item['answer'], item['check'], *[s['do'] for s in item['steps']]]
    return '\n'.join(parts)


# ---------------------------------------------------------------- derived views

def quiz_view(design):
    """What a matching quiz needs from the design: the notation, what to check, what not to test, and
    every problem the students have already met (in class or as homework on the handout), by its reader's name."""
    names = labels(design)
    bank = {item['id']: item for item in design['items']}
    clean = lambda text: humanize(design, re.sub(r'[ \t]*\[[MV]\d+\]', '', text)).strip()

    def objective(number, entry):
        checked = [bank[ref] for ref in entry.get('checked_by', []) if ref in bank]
        return dict(number=number, text=clean(entry['text']),
                    level=LEVELS['advanced' if any(item['level'] == 'advanced' for item in checked) else 'basic'],
                    knowledge_ids=list(dict.fromkeys(item['knowledge_id'] for item in checked)),
                    checked_by=[names[item['id']] for item in checked])
    return dict(
        notation=[dict(symbol=entry['symbol'], meaning=clean(entry['meaning'])) for entry in design['notation']],
        objectives=[objective(number, entry) for number, entry in enumerate(design['objectives'], 1)],
        key_points=[clean(text) for text in design['focus']['key_points']],
        difficulties=[clean(text) for text in design['focus']['difficulties']],
        not_today=[clean(text) for text in design['focus']['not_today']],
        used=[dict(name=names[item['id']], role=ROLE_NAMES[item['role']], level=LEVELS[item['level']],
                   stem=clean(item['stem']), answer=clean(item['answer'])) for item in design['items']])


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
    pattern = re.compile(r'( ?)(?<![A-Za-z0-9_\\\[])(' + '|'.join(sorted(map(re.escape, names), key=len, reverse=True))
                         + r')(?![A-Za-z0-9_])( ?)')
    wide = lambda char: bool(char) and char >= '\u2e80'

    def swap(match, part):
        # A space between Chinese text and an id is the model's habit, not typography; list markers keep theirs.
        lead = '' if wide(part[:match.start()][-1:]) else match[1]
        trail = '' if wide(part[match.end():][:1]) else match[3]
        return lead + names[match[2]] + trail
    lines, fenced = [], False
    for line in text.split('\n'):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        elif not fenced:
            parts = re.split(r'(\$\$.*?\$\$|\$[^$\n]*\$)', line)  # odd positions are formulas
            line = ''.join(part if index % 2 else _NUMBERED.sub('', _DOUBLED.sub(lambda m: next(filter(None, m.groups())),
                                                               pattern.sub(lambda m, part=part: swap(m, part), part)))
                           for index, part in enumerate(parts))
        lines.append(line)
    return '\n'.join(lines)


# 「用 W1 前测确认」 reads 「用前测1前测确认」 once the id is named; the kind is said once.
_DOUBLED = re.compile(r'(前测\d+) ?前测(?!\d)|(例\d+) ?例题(?!\d)|(练\d+) ?练习(?!\d)|(作业\d+) ?作业(?!\d)')
# 「小结要点与作业题号H1」 reads 「……作业题号作业1」: before a name the words 「作业题号」 go.
_NUMBERED = re.compile(r'(?:作业|练习|例题)?题号[ \t：:]*(?=(?:前测|例|练|作业)\d)|(?<=布置)作业(?=作业\d)')


def figure_spec(design, spec):
    """A figure's data with display names in its words (「例1 后验」, not 「E1 后验」); numbers untouched."""
    if isinstance(spec, dict):
        return {key: humanize(design, value) if key in ('title', 'label', 'root', 'note') and isinstance(value, str)
                else figure_spec(design, value) for key, value in spec.items()}
    if isinstance(spec, list):
        return [figure_spec(design, value) for value in spec]
    return spec


def gap_outside(text):
    """Move a relation sign that ends a formula right before a gap out of the formula:
    "$E(X)=$ ____" becomes "$E(X)$ = ____". A formula ending in a bare "=" is drawn with an error mark."""
    signs = {'=': '=', '<': '<', '>': '>', '\\approx': '≈', '\\le': '≤', '\\ge': '≥', '\\neq': '≠'}
    return re.sub(r'[ \t]*(=|<|>|\\approx|\\le|\\ge|\\neq)[ \t]*\$[ \t]*(?=_{4,}|【)',
                  lambda match: f'$ {signs[match[1]]} ', text)


def as_formula(text):
    """An answer the model wrote as bare LaTeX ("P(B\\mid A_i)", "P(B)>0") put between $ so it is drawn as a formula."""
    text = text.strip()
    if '$' in text or re.search(r'[\u4e00-\u9fff]', text) or not re.search(r'\\[A-Za-z]|[_^]|[A-Za-z]\(|[<>=]', text):
        return text
    return f'${text}$'


def note_answers(note):
    """The answers of a handout note, one per gap when they are separated by semicolons."""
    return [as_formula(part) for part in re.split(r'[；;]', note['answer']) if part.strip()]


_FORMULA = re.compile(r'(\$\$.*?\$\$|(?<!\\)\$[^$\n]*\$)')


def _gaps(text, replace):
    """Replace every gap with replace(index, inside_formula); a gap may sit inside a formula, as in \\frac{a}{____}."""
    parts, count = _FORMULA.split(text), 0
    for index, part in enumerate(parts):
        def swap(_, inside=bool(index % 2)):
            nonlocal count
            count += 1
            return replace(count - 1, inside)
        parts[index] = BLANK.sub(swap, part)
    return ''.join(parts)


def open_gaps(text):
    """The handout's view of a note: a gap inside a formula becomes \\gap, which the Word export draws as a line."""
    opened = _gaps(gap_outside(text), lambda _, inside: '\\gap ' if inside else '____')
    # The space only ends the command name before a letter; before $ it would stop Word reading the formula.
    return re.sub(r'\\gap (?![A-Za-z])', r'\\gap', opened)


def _inside(answer):
    """An answer to be placed inside a formula: its own $ removed, words kept as text."""
    if re.fullmatch(r'\$[^$]+\$', answer):
        return answer[1:-1]
    return f'\\text{{{answer}}}' if '$' not in answer else answer.replace('$', '')


def filled(note):
    """A handout note with its gaps filled in: one answer per gap when they pair up, else the answer after the sentence."""
    text = gap_outside(note['text'])
    gaps, answers = len(BLANK.findall(text)), note_answers(note)
    if not gaps:
        return text
    if gaps != len(answers):
        if gaps > 1:
            return f'{open_gaps(text)}（答：{as_formula(note["answer"])}）'
        answers = [as_formula(note['answer'])]
    return _gaps(text, lambda n, inside: f'\\text{{【}}{_inside(answers[n])}\\text{{】}}' if inside else f'【{answers[n]}】')


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
    lines = [title, '', f'**题目**　{item["stem"]}']
    if item['steps']:
        lines += ['', '**解答**', '']
        lines += [f'{n}. {step["do"]}' + (f'　——{step["why"]}' if step['why'] else '')
                  for n, step in enumerate(item['steps'], 1)]
    lines += ['', f'**答案**　{item["answer"]}']
    if item['check']:
        lines += ['', f'**检验**　{item["check"]}']
    if item['mistake']:
        lines += ['', f'**易错**　{item["mistake"]}']
    extra = _cite(item['sources'], '\n'.join(lines)).strip()   # sources the block has not cited inline
    if extra:
        lines += ['', f'**依据**　{extra}']
    return humanize(design, '\n'.join(lines))


def schedule(design, pages=None):
    """The one-page run sheet: when, which segment, what the students do, which problems.
    The teacher's moves are written once, under each segment. pages: {segment id: 「第 3—4 页」}
    adds the slides each segment uses."""
    names = labels(design)
    head = ['时间', '环节', '学生', '题目'] + (['课件'] if pages else [])
    rows, clock = ['| ' + ' | '.join(head) + ' |', '|' + ' --- |' * len(head)], 0
    cell = lambda text: re.sub(r'\s+', ' ', text).replace('|', '\\|') or '—'
    for segment in design['segments']:
        title = segment_title(segment) + ('（重点）' if segment['focus'] else '')
        used = '、'.join(names[ref] for ref in segment['items'])
        row = f'| {clock}—{clock + segment["minutes"]} | {cell(title)} | {cell(segment["students"])} | {cell(used)} |'
        rows.append(row + (f' {cell(pages.get(segment["id"], ""))} |' if pages else ''))
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
        goals.append(f'{number}. {objective["text"].rstrip("。；;.")}' + (f'（由{checked}检验）' if checked else ''))
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
            lines += ['', '```figure', json.dumps(figure_spec(design, figure['spec']), ensure_ascii=False), '```']
    if segment.get('notes'):
        lines += ['', '**学案笔记**（学生边听边填）', ''] + [f'- {filled(note)}' for note in segment['notes']]
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
    unchecked = [o['text'] for o in design['objectives'] if not any(bank[ref]['role'] in CHECKING for ref in o['checked_by'])]
    names = labels(design)
    loose = [f'目标 {number} 与{names[ref]}' for number, ref, _ in objective_gaps(design)]
    repeated = [f'{names[ref]}重复{names[earlier]}的数据和结果' for ref, earlier in repeated_items(design)]
    slips = [f'{names[ref]}写了 {value} 和 {other}' for ref, value, other in mismatched_values(design)]
    loose_words = [f'{humanize(design, re.sub(r"^(?:环节|题目) ", "", where))}「{phrase}」' for _, where, phrase in wording(design)]
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
        ('design_objectives', '目标与检验题对应', not unchecked and not loose,
         '；'.join(filter(None, [
             '以下目标没有对应的练习或作业：' + '；'.join(unchecked) if unchecked else '',
             '以下检验题里看不到对应目标的内容，请核对：' + '、'.join(loose) if loose else '']))
         or f'{len(design["objectives"])} 条目标都指定了检验它的题目，题目里也有目标的关键词；题目是否真能检验目标需教师判断。'),
        ('design_variety', '练习与例题不重复', not repeated,
         '；'.join(repeated) + '。换数据、换情境或换问法。' if repeated
         else '没有练习或作业沿用前面题目的数据并重算已有的结果；按数字比对，换了说法的重复需教师判断。'),
        ('design_values', '同一结果前后一致', not slips,
         '；'.join(slips) + '。同一个结果在步骤、答案、检验和板书里应写同一个值。' if slips
         else '步骤、答案、检验和板书里同样位数的数值没有发现末位不一致；不同位数的近似值不比较。'),
        ('design_wording', '可以直接使用的写法', not loose_words,
         '以下地方写了关于数据或生成过程的话、没写完的句子或英文缩写：' + '；'.join(loose_words[:6]) if loose_words
         else '没有发现「依据诊断」「待核对」这类关于数据的话、没写完的句子或学生材料里的英文缩写；措辞是否合适需教师判断。'),
        ('design_focus', '重点与难点', 1 <= len(design['focus']['key_points']) <= 2,
         f'重点 {len(design["focus"]["key_points"])} 条、难点 {len(design["focus"]["difficulties"])} 条、'
         f'本课不展开 {len(design["focus"]["not_today"])} 条；取舍是否合适需教师判断。'),
    ]
    if plan:
        result.append(('design_plan', '讲解安排落实位置', not unlinked,
                       '以下讲解安排没有落到环节：' + '、'.join(unlinked) if unlinked
                       else '每条讲解安排都指明了落实的环节；是否真正落实、是否适合学生需教师判断。'))
    return result
