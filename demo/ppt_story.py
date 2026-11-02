"""Typed semantic page contracts. No model geometry or executable content."""
import re

DESIGN_VERSION = 'pptkit-classroom-v2'
LAYOUT_NAMES = dict(cover='学习目标', explain='重点讲解', definition='概念与条件', steps='分步推导',
                    comparison='对比辨析', table='对照表格', process='过程关系', metrics='数据解读',
                    practice='独立练习', recap='方法回顾')


def validate_visual(value, slide):
    """Return a canonical contract or raise ValueError; never silently drop data."""
    if (not isinstance(value, dict) or not isinstance(value.get('type'), str)
            or value['type'] not in LAYOUT_NAMES):
        raise ValueError('缺少有效的 visual.type')
    kind = value['type']
    fields = dict(cover={'subtitle', 'goals'}, explain={'lead', 'items'},
                  definition={'term', 'definition', 'formula', 'conditions'},
                  steps={'problem', 'steps', 'check'}, comparison={'left', 'right'},
                  table={'headers', 'rows'}, process={'nodes'}, metrics={'items'},
                  practice={'problem', 'prompts'}, recap={'items'})
    if set(value) - fields[kind] - {'type'}:
        raise ValueError('visual 含未支持字段，请将答案和详细讲解放 notes')
    if slide['kind'] == 'cover' and kind != 'cover':
        raise ValueError('封面只能使用 cover 结构')
    if slide['kind'] == 'question' and kind != 'practice':
        raise ValueError('练习页只能使用 practice 结构，答案放教师讲稿')
    if kind == 'steps' and slide['kind'] != 'example':
        raise ValueError('推导步骤仅用于例题页')
    if kind == 'cover' and slide['kind'] != 'cover':
        raise ValueError('内页不能使用封面结构')

    def text(obj, key, maximum, optional=False):
        item = obj.get(key, '' if optional else None)
        if (not isinstance(item, str) or len(item.strip()) > maximum
                or (not optional and not item.strip())
                or re.search(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]', item)):
            raise ValueError(f'{key} 必须为单行文字且不超过 {maximum} 字符')
        return item.strip()

    def texts(obj, key, low, high, maximum):
        items = obj.get(key)
        if not isinstance(items, list) or not low <= len(items) <= high:
            raise ValueError(f'{key} 数量应为 {low}—{high}')
        return [text({'item': item}, 'item', maximum) for item in items]

    def objects(key, low, high, fields):
        items = value.get(key)
        if not isinstance(items, list) or not low <= len(items) <= high:
            raise ValueError(f'{key} 数量应为 {low}—{high}')
        if any(not isinstance(item, dict) for item in items):
            raise ValueError(f'{key} 中每项必须是对象')
        if any(set(item) - {name for name, _, _ in fields} for item in items):
            raise ValueError(f'{key} 中含未支持字段，请将答案和详细讲解放 notes')
        return [{name: text(item, name, limit, optional) for name, limit, optional in fields} for item in items]

    result = dict(type=kind)
    if kind == 'cover':
        result.update(subtitle=text(value, 'subtitle', 80, True), goals=texts(value, 'goals', 1, 3, 48))
    elif kind == 'explain':
        result.update(lead=text(value, 'lead', 100), items=texts(value, 'items', 1, 4, 64))
    elif kind == 'definition':
        result.update(term=text(value, 'term', 32), definition=text(value, 'definition', 100),
                      formula=text(value, 'formula', 80, True), conditions=texts(value, 'conditions', 0, 3, 48))
    elif kind == 'steps':
        result.update(problem=text(value, 'problem', 80), check=text(value, 'check', 80, True),
                      steps=objects('steps', 2, 4, [('expression', 56, False), ('reason', 40, False)]))
    elif kind == 'comparison':
        for side in ['left', 'right']:
            data = value.get(side)
            if not isinstance(data, dict):
                raise ValueError(f'{side} 必须为对象')
            if set(data) - {'title', 'items'}:
                raise ValueError(f'{side} 含未支持字段')
            result[side] = dict(title=text(data, 'title', 24), items=texts(data, 'items', 1, 3, 64))
    elif kind == 'table':
        result['headers'] = texts(value, 'headers', 2, 4, 18)
        rows = value.get('rows')
        if not isinstance(rows, list) or not 2 <= len(rows) <= 4:
            raise ValueError('表格应有 2—4 行数据')
        result['rows'] = [texts({'row': row}, 'row', len(result['headers']), len(result['headers']), 36) for row in rows]
    elif kind == 'process':
        result['nodes'] = objects('nodes', 2, 5, [('title', 24, False), ('detail', 64, False)])
    elif kind == 'metrics':
        result['items'] = objects('items', 2, 3, [('value', 16, False), ('unit', 12, True), ('label', 32, False), ('context', 64, False)])
    elif kind == 'practice':
        result.update(problem=text(value, 'problem', 160), prompts=texts(value, 'prompts', 0, 3, 48))
    else:
        result['items'] = objects('items', 2, 4, [('title', 24, False), ('detail', 64, False)])
    if sum(len(s) for s in visual_texts(result)) > 520:
        raise ValueError('单页 visual 投屏文字超过 520 字符，请精简或重新安排页面')
    return result


def visual_texts(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in visual_texts(item)]
    if isinstance(value, dict):
        return [text for key, item in value.items() if key != 'type' for text in visual_texts(item)]
    return []


def fallback_visual(slide):
    """Developer fixture adapter; production v2 parsing requires explicit semantics."""
    bullets = slide['bullets']
    if slide['kind'] == 'cover':
        return dict(type='cover', subtitle='', goals=bullets)
    if slide['kind'] == 'question':
        return dict(type='practice', problem=slide['question'] or bullets[0], prompts=bullets)
    return dict(type='explain', lead=bullets[0], items=bullets[1:] or bullets)
