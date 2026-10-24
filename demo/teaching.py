"""Validated per-lesson controls and versioned teacher-facing instructions."""

DEFAULTS = {'duration': 45, 'lesson_type': 'new', 'detail': 'standard',
            'examples': 2, 'exercises': 3, 'requirements': ''}
LESSON_TYPES = {'new': '新授', 'practice': '巩固', 'correction': '纠错', 'review': '复习'}
DETAILS = {'brief': '简明提纲', 'standard': '常规讲解', 'detailed': '详细讲解'}


def teaching_options(data):
    value = data.get('teaching_options')
    if value is None and 'teaching_options' not in data:
        return None  # Legacy API clients and frozen experiments keep their original prompts.
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('授课设置格式无效。')
    result = {**DEFAULTS, **value}
    for key, low, high, label in [('duration', 5, 180, '授课时长'),
                                  ('examples', 0, 6, '例题数'), ('exercises', 0, 10, '练习数')]:
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{label}请填写 {low}—{high} 的整数。')
    if (not isinstance(result['lesson_type'], str) or result['lesson_type'] not in LESSON_TYPES
            or not isinstance(result['detail'], str) or result['detail'] not in DETAILS):
        raise ValueError('请选择有效的授课类型与讲解详略。')
    if not isinstance(result['requirements'], str) or len(result['requirements'].strip()) > 2000:
        raise ValueError('本次授课要求请控制在 2000 字以内。')
    result['requirements'] = result['requirements'].strip()
    return result


def length_range(options, expanded=False):
    ranges = {'brief': (1500, 2500), 'standard': (4000, 6000), 'detailed': (6000, 8000)} if expanded else {
        'brief': (500, 1000), 'standard': (900, 1800), 'detailed': (1400, 2800)}
    base = ranges[options['detail']]
    extra = max(0, options['examples'] - 2) * 180 + max(0, options['exercises'] - 3) * 100
    return base[0], min(8000, base[1] + extra) if expanded else base[1] + extra


def instructions(options, expanded=False):
    low, high = length_range(options, expanded)
    return f'''
【授课讲义规则 teaching-v1：以下结构与篇幅要求替代前面的固定章节与字数要求】
这份讲义供教师授课时使用。依据原有学科约束、材料和学生证据，组织可直接用于课堂的讲解。
本次为 {options['duration']} 分钟{LESSON_TYPES[options['lesson_type']]}，{DETAILS[options['detail']]}，
安排 {options['examples']} 道完整例题、{options['exercises']} 道课堂练习。例题与练习分别编号，练习附参考答案和判断要点。
先给一个 # 主题标题，再依次使用五个二级标题：
二级标题只写下列冒号前的名称，冒号后的文字是写作要求，不属于标题。
## 学习目标：给出课堂结束时可检查的目标。
## 本次学习安排：简要说明诊断依据与讲解调整，给出各环节建议分钟数，总计 {options['duration']} 分钟；这是教学建议，不是实际计时。
## 核心讲解：按顺序给出教师讲解、关键板书步骤和课堂提问；提问附预期回答、典型错误与追问引导。详略服从本次设置，避免重复画像记录。
## 例题与检验：提供指定数量的例题与课堂练习，保留必要推导、适用条件、检验及参考答案；区分课内必做与时间允许时选做。
## 易错点与小结：给出易错点处理和简短的课堂达成检查。
篇幅参考 {low}—{high} 个汉字，以满足目标、推导正确及授课可用为先，不为凑字重复内容。
teaching_options.requirements 为教师本次的授课要求，不自动写回长期画像；其中涉及个体的观察不能变成已确认诊断。
授课类型、详略和题量不得扩大共同知识范围。要求与资料或时长冲突时说明取舍，不虚构依据。
个性化应体现在步骤详略、例子、追问及错误引导中；不反复复述数据库字段和记录缺失说明。
图片解析仍须核对；相关时自然组织为题目或讲解，并保留 [V编号] 与原有不确定性。
'''.strip()
