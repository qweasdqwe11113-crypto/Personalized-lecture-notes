"""Small explicit course catalog. Task difficulty is separate from learner state."""
TOPICS = {
    'basic': {'title': '等式性质与一步方程', 'knowledge_ids': [1, 2], 'student_ids': [1, 2, 3],
              'course': '初中数学', 'task_level': '基础任务', 'length_range': [600, 900],
              'goal': '理解等式两边同加、同减、同乘、同除的依据，能解一步方程并检验。'},
    'linear': {'title': '一元一次方程：从等式到移项', 'knowledge_ids': [1, 2, 3], 'student_ids': [1, 2, 3],
               'course': '初中数学', 'task_level': '基础与变式', 'length_range': [600, 900],
               'goal': '理解移项的依据，独立求解两步方程，并通过代入检查答案。'},
    'probability_basic': {'title': '大学概率：条件概率与贝叶斯公式（基础任务）',
                         'knowledge_ids': [11, 12, 13], 'student_ids': [11, 12, 13],
                         'course': '大学概率论', 'task_level': '基础任务', 'length_range': [600, 1000], 'check_version': 'probability-text-check-v2',
                         'goal': '解释条件概率的方向与正分母条件，利用互斥完备划分计算全概率和贝叶斯后验，完成给定双产线例题并核对后验归一。'},
    'probability_advanced': {'title': '大学概率：贝叶斯推导与先验敏感性（进阶任务）',
                            'knowledge_ids': [11, 12, 13], 'student_ids': [11, 12, 13],
                            'course': '大学概率论', 'task_level': '进阶任务', 'length_range': [600, 1000], 'check_version': 'probability-text-check-v2',
                            'goal': '从条件概率和互斥完备划分推导全概率与贝叶斯公式，说明所有正分母条件；完成固定双产线例题及先验变化对照，核对后验归一，区分 P(A|B) 与 P(B|A)。'},
}

def course_family(topic):
    return topic.get('prompt_family') or ('general' if topic.get('custom') else
        'probability' if topic.get('course') == '大学概率论' else 'math')


REFERENCE_EXAMPLES = [
    {'name': '双产线基准', 'events': 'A1、A2 为互斥完备的产线来源，D 为次品',
     'prior': [0.6, 0.4], 'likelihood': [0.02, 0.05],
     'task': '计算 P(D)、P(A1|D)、P(A2|D)，用 10000 件的假设频数复核并检查后验和为 1。'},
    {'name': '先验变化', 'events': '同一双产线模型，仅改变先验比例；次品率保持不变',
     'prior': [0.2, 0.8], 'likelihood': [0.02, 0.05],
     'task': '计算新 P(D) 与两项后验，比较 A1 后验变化，说明推理仍需相同适用条件。'},
]
