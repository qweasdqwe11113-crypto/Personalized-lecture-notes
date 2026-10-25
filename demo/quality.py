"""Transparent text checks; no claim to prove mathematics or citation entailment."""
import re
from teaching import length_range
from courses import course_family

CHECK_VERSION = 'math-text-check-v1'
SECTIONS = ['学习目标', '本次学习安排', '核心讲解', '例题与检验', '易错点与小结']
REVIEW_ITEMS = {
    'goal': '共同目标、知识范围与材料覆盖',
    'math': '数学推导、适用条件与术语',
    'examples': '例题运算与代入检验',
    'citations': '引用材料是否支持对应表述',
    'personalization': '调整是否对应诊断且不擅自改变状态',
}
SCOPE = {
    1: ('等式两边同加同减与平衡', ['同加', '同减', '加减', '平衡']),
    2: ('等式两边同乘同除；除数非零', ['同乘', '同除', '乘除', '非零']),
    3: ('ax + b = c 两步求解、移项依据与两边含未知数的变式', ['移项', '两步', '两边含未知数']),
    11: ('条件概率、交集、方向与正分母', ['条件概率', '交集', '分母']),
    12: ('有限互斥完备划分与全概率', ['全概率', '互斥', '完备']),
    13: ('贝叶斯推导、后验归一及先验变化', ['贝叶斯', '后验', '先验']),
}
OUTSIDE = ['概率', '贝叶斯', '导数', '积分', '微分', '二次方程', '不等式', '三角函数', '几何']


def assess_scope(context, goal):
    ids = context['topic']['knowledge_ids']
    materials = context['materials']
    coverage = []
    family = course_family(context['topic'])
    general = family == 'general'
    canonical = context['topic'].get('canonical_knowledge_ids', {})
    names = dict(zip(ids, context['topic'].get('knowledge_names', [])))
    for kid in ids:
        selected = [m for m in materials if m['knowledge_id'] == kid]
        coverage.append({'knowledge_id': kid, 'scope': names.get(kid, '未定义范围') if general else SCOPE.get(canonical.get(str(kid), kid), ('未定义范围', []))[0],
                         'material_ids': [m['id'] for m in selected], 'has_material': bool(selected)})
    warnings = []
    missing = [c['knowledge_id'] for c in coverage if not c['has_material']]
    if missing:
        warnings.append('知识点 ' + '、'.join(map(str, missing)) + ' 缺少关联课程材料。')
    university = family == 'probability'
    outside = ['导数', '积分', '微分', '二次方程', '三角函数', '连续密度', '重复观测'] if university else OUTSIDE
    possible_outside = [] if general else [word for word in outside if word in goal]
    if university and any(word in goal for word in ['移项', '方程']):
        possible_outside.append('方程求解')
    if not general and not university and 3 not in [canonical.get(str(k),k) for k in ids] and any(word in goal for word in ['移项', '两步', '两边含未知数']):
        possible_outside.append('当前一步方程主题未包含移项知识点')
    if possible_outside:
        warnings.append('目标含可能超出所选范围的词：' + '、'.join(possible_outside) + '。请教师核查；词语匹配不解释否定语义。')
    if not general and any(word in goal for word in ['括号', '分配律']):
        warnings.append('当前材料摘要未提供分配律、去括号的课程依据，需补充材料或调整目标。')
    custom = goal != context['topic']['goal']
    if custom:
        warnings.append('这是自定义目标，程序不能确认完整目标覆盖，需教师对照下列范围。')
    return {'version': context['topic'].get('check_version', 'probability-text-check-v1') if university else context['topic'].get('check_version', CHECK_VERSION), 'goal': goal, 'default_goal': context['topic']['goal'],
            'custom_goal': custom, 'coverage': coverage, 'warnings': warnings,
            'note': '材料关联表示已提供该知识点资料，不证明材料支持讲义每个结论。'}


def citation_locations(content, context):
    available = {str(m['id']) for m in context['materials']}
    visuals = {v['citation_id'] for v in context.get('visual_evidence', [])}
    return [{'line': i, 'excerpt': line, 'material_ids': re.findall(r'\[M(\d+)\]', line),
             'invalid_ids': sorted(set(re.findall(r'\[M(\d+)\]', line)) - available),
             'visual_ids': re.findall(r'\[(V\d+)\]', line),
             'invalid_visual_ids': sorted(set(re.findall(r'\[(V\d+)\]', line)) - visuals)}
            for i, line in enumerate(content.splitlines(), 1) if re.search(r'\[[MV]\d+\]', line)]


def check_output(content, context, goal):
    general = course_family(context['topic']) == 'general'
    scope = assess_scope(context, goal)
    headings = re.findall(r'^##\s+(.+?)\s*$', content, re.M)
    missing = [s for s in SECTIONS if s not in headings]
    chinese = len(re.findall(r'[\u4e00-\u9fff]', content))
    citations = citation_locations(content, context)
    invalid = sorted({v for c in citations for v in c['invalid_ids']})
    invalid_visual = sorted({v for c in citations for v in c['invalid_visual_ids']})
    invalid_sources = ['M'+v for v in invalid] + invalid_visual
    checks = []
    def add(code, label, passed, detail):
        checks.append({'code': code, 'label': label, 'status': 'pass' if passed else 'warning', 'detail': detail})
    add('title', '讲义标题', len(re.findall(r'^#\s+\S', content, re.M)) == 1, '应有一个一级标题。')
    add('sections', '约定章节', not missing, '缺少：' + '、'.join(missing) if missing else '五个约定章节均已出现；不代表章节内容充分。')
    minimum, maximum = context['topic'].get('length_range', [600, 900])
    expanded = context.get('lecture_length_policy') == 'expanded-v1'
    if expanded:
        minimum, maximum = 4000, 8000
    if context.get('teaching_options'):
        minimum, maximum = length_range(context['teaching_options'], expanded)
    add('length', '篇幅', minimum <= chinese <= maximum, f'检测到 {chinese} 个汉字；当前模板建议 {minimum}—{maximum}，含标题，不计标点、数字和字母。')
    add('citations', '引用标记', bool(citations), '检测到来源编号。' if citations else '正文未检测到 [M数字] 或 [V数字] 引用。')
    add('citation_ids', '引用编号有效性', not invalid_sources, '不存在的来源编号：' + '、'.join(invalid_sources) if invalid_sources else '未检测到无效编号；仍需检查引用支持关系。')
    add('materials', '知识点资料关联', all(c['has_material'] for c in scope['coverage']), '仅检查所选知识点是否有关联材料，材料内容充分性需人工判断。')
    university = course_family(context['topic']) == 'probability'
    visible_brackets = not general and not university and bool(re.search(r'去括号|分配律|\d\s*[（(]\s*[-+]?\s*\d*\s*[xX]', content))
    if not general:
        add('bracket_evidence', '去括号的资料依据', not visible_brackets, '正文出现去括号、分配律或含括号方程，当前摘要未提供分配律依据，需复核来源与范围。' if visible_brackets else '未触发当前含括号关键词规则；不表示证明了范围完整。')
    add('goal_scope', '目标范围提示', not scope['warnings'], '；'.join(scope['warnings']) if scope['warnings'] else '使用主题默认目标，关联范围已列明；完整覆盖仍需教师复核。')
    if university:
        add('probability_conditions', '概率公式条件提示', all(word in content for word in ['互斥', '完备']) and bool(re.search(r'>\s*0|大于\s*(?:零|0)|正分母', content)), '仅匹配互斥、完备及正分母提示词；不证明每次公式使用都满足条件。')
    if university and scope['version'] == 'probability-text-check-v2':
        risky = bool(re.search(r'Ai\s*∩\s*Bi|(?:仅在|只有).*P\(A\)\s*=\s*P\(B\).*相等', content))
        add('known_probability_hints', '已发现问题的词语提示', not risky, '匹配未定义 Bi 的已知表达或过强相等条件；词语提示不解释否定或完整上下文，需人工判断。')
    approach = context.get('retrieval') or {}
    if approach.get('mode') == 'vector':
        recalled = {str(item['material_id']) for item in approach.get('items', [])}
        missing = sorted(recalled - set(re.findall(r'\[M(\d+)\]', content)))
        add('retrieval_usage', '召回片段的引用情况', not missing,
            '以下本次召回的材料未在正文引用：' + '、'.join('M' + item for item in missing) if missing
            else '本次召回的材料在正文中都出现了编号；不代表引用位置恰当或内容被正确理解。')
    if context.get('visual_evidence') or re.search(r'\[V\d+\]', content):
        available = {item['citation_id'] for item in context.get('visual_evidence', [])}
        used = set(re.findall(r'\[(V\d+)\]', content))
        add('visual_citations', '图片解析引用', bool(used) and used <= available,
            '已引用有效图片解析编号；识读与引用支持关系仍需教师核对。' if used and used <= available else
            '未引用图片解析或存在无效图片编号；图片可能与本次目标无关，请核对。')
    if context.get('personalization_plan'):
        planned = {str(p['knowledge_id']) for p in context['personalization_plan']}
        marked = set(re.findall(r'\[K(\d+)\]',content))
        add('plan_locations','讲解安排定位', planned <= marked and not marked-planned,
            '安排编号均可定位；是否落实及是否适合学生仍需教师核对。' if planned <= marked and not marked-planned else
            '缺少安排定位：'+ '、'.join('K'+k for k in sorted(planned-marked))+'；超出本次安排：'+'、'.join('K'+k for k in sorted(marked-planned))+'。编号只辅助定位。')
    teaching = context.get('teaching_options') or {}
    version = scope['version'] + ('+teaching-v2' if teaching.get('length_policy') == 'duration-v2' else '+teaching-v1' if teaching else '')
    if context.get('lecture_workflow') == 'teacher-v2':
        version += '+lecture-workflow-v2'
    return {'version': version, 'metrics': {'chinese_characters': chinese, 'line_count': len(content.splitlines())}, 'invalid_source_ids': invalid_sources,
            'scope': scope, 'automatic': checks, 'citation_locations': citations,
            'teacher_review': [{'code': key, 'label': label, 'status': 'needs_review'} for key, label in ({**REVIEW_ITEMS, 'math': '学科知识、适用条件与术语', 'examples': '示例与检验过程'} if general else REVIEW_ITEMS).items()],
            'note': '程序只检查文本结构、篇幅、标记和关联；不能判定任意数学结论或来源支持。'}
