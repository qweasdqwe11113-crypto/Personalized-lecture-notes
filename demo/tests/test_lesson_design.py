"""Lesson design: validation, rendering, the staged pipeline, teacher confirmation and text measures.

Real PostgreSQL in a disposable schema for the pipeline; every model reply is an explicit fixture.
"""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_courses_jobs as base  # importing the class itself would make unittest run its tests again here
import design as D
import designed
import style
from jobs import JobQueue
from progress import ProgressHub
from quality import check_output
from sections import prepare_section

module = base.module
OPTIONS = {'duration': 20, 'detail': 'brief', 'length_policy': 'duration-v2'}
TREE = {'type': 'probability_tree', 'title': '双产线', 'root': '全部产品', 'branches': [
    {'label': 'A1', 'p': 0.6, 'children': [{'label': 'D', 'p': 0.02}, {'label': '非D', 'p': 0.98}]},
    {'label': 'A2', 'p': 0.4, 'children': [{'label': 'D', 'p': 0.05}, {'label': '非D', 'p': 0.95}]}]}


def item(identity, role, stem, answer, **more):
    return {'id': identity, 'role': role, 'level': 'basic', 'knowledge_id': 13, 'title': '', 'stem': stem,
            'steps': [], 'answer': answer, 'check': '', 'mistake': '', 'sources': [], **more}


def design_reply():
    segment = dict(focus=False, knowledge_ids=[13], screen='', board=[], students='独立完成', questions=[],
                   items=[], if_short='', sources=[])
    return {
        'title': '隔离课堂设计',
        'focus': {'key_points': ['贝叶斯公式的推导'], 'difficulties': ['区分两个方向的条件概率'], 'not_today': ['连续情形只提一句']},
        'learner': '条件方向有混淆的记录，例1 用正误对比。',
        'plan_links': [{'knowledge_id': 11, 'segment': 'S1', 'how': '用正误对比澄清方向'},
                       {'knowledge_id': 12, 'segment': 'S2', 'how': '逐项加权'},
                       {'knowledge_id': 13, 'segment': 'S2', 'how': '先写先验再写似然'}],
        'notation': [{'symbol': '$D$', 'meaning': '抽到次品'}],
        'objectives': [{'text': '能用贝叶斯公式求后验', 'checked_by': ['P1']},
                       {'text': '能说明先验变化的影响', 'checked_by': ['P3', 'H1']}],
        'segments': [
            {**segment, 'id': 'S1', 'kind': 'concept', 'title': '贝叶斯公式', 'minutes': 3, 'focus': True, 'knowledge_ids': [11, 13],
             'screen': '贝叶斯公式与成立条件', 'board': ['$P(A_k\\mid B)$ 的公式'], 'teacher': ['从条件概率定义推到贝叶斯公式'],
             'questions': [{'ask': '分母是什么？', 'expect': '全概率', 'wrong': '只写一项', 'follow_up': '另一条产线呢？', 'level': 'basic'}],
             'if_short': '不能省', 'sources': ['M13']},
            {**segment, 'id': 'S2', 'kind': 'example', 'title': '双产线', 'minutes': 8, 'teacher': ['板演例1', '学生口述例2'],
             'items': ['E1', 'E2']},
            {**segment, 'id': 'S3', 'kind': 'practice', 'title': '当堂练习', 'minutes': 9, 'teacher': ['巡视，看分母是否写全'],
             'items': ['P1', 'P2', 'P3']}],
        'items': [
            item('E1', 'example', '两条产线 A1、A2 的产量占比为 0.6、0.4，次品率为 0.02、0.05。抽到一件次品，求它来自 A1 的概率。', '0.375',
                 title='双产线基准', sources=['M13'], check='$0.375 + 0.625 = 1$', mistake='把 0.02 当成答案',
                 steps=[{'do': '$P(D) = 0.6 \\times 0.02 + 0.4 \\times 0.05 = 0.032$', 'why': '全概率'},
                        {'do': '$P(A_1\\mid D) = 0.012 / 0.032 = 0.375$', 'why': '贝叶斯公式'}]),
            item('E2', 'example', '把占比改为 0.2、0.8，次品率不变，再求来自 A1 的概率。', '约 0.0909',
                 steps=[{'do': '$P(D) = 0.2 \\times 0.02 + 0.8 \\times 0.05 = 0.044$', 'why': '全概率'}]),
            item('P1', 'practice', '占比 0.7、0.3，次品率 0.03、0.08，求来自甲线的后验概率。', '7/15'),
            item('P2', 'practice', '已知 $P(B)=0.5$，$P(A\\cap B)=0.1$，求 $P(A\\mid B)$。', '0.2'),
            item('P3', 'practice', '先验从 0.6 降到 0.2，后验怎样变化？说明理由。', '变小', level='advanced'),
            item('H1', 'homework', '自拟一组先验，比较前后两个后验。', '言之成理即可')],
        'board_plan': {'main': ['贝叶斯公式', '全概率公式'], 'side': ['例1 演算']},
        'figures': [{'segment': 'S2', 'spec': TREE}],
        'summary': ['后验 = 先验 × 似然 ÷ 全概率'],
    }


CONTEXT = {'topic': {'title': '贝叶斯', 'knowledge_ids': [11, 12, 13]}, 'materials': [{'id': 11}, {'id': 12}, {'id': 13}],
           'teaching_options': {**OPTIONS, 'examples': 2, 'exercises': 3}, 'personalization_mode': 'profile',
           'student': {'name': '小许'},
           'personalization_plan': [{'knowledge_id': k, 'name': f'知识点{k}'} for k in (11, 12, 13)]}


def request_of(prepared):
    payload = json.loads(prepared.body)
    return payload, json.loads(payload['messages'][1]['content'])


class DesignRules(unittest.TestCase):
    def test_a_sound_design_passes_and_validating_it_again_changes_nothing(self):
        design, hard, soft = D.validate(design_reply(), CONTEXT)
        self.assertEqual((hard, soft), ([], []))
        self.assertEqual(D.validate(design, CONTEXT), (design, [], []))
        self.assertEqual(D.labels(design), {'E1': '例1', 'E2': '例2', 'P1': '练1', 'P2': '练2', 'P3': '练3', 'H1': '作业1'})
        self.assertEqual([s['id'] for s in D.writers(design)], ['S1', 'S2'])
        self.assertEqual(D.budgets(design, CONTEXT['teaching_options']), {'S1': {'low': 48, 'high': 120}, 'S2': {'low': 0, 'high': 160}})

    def test_time_counts_and_coverage_are_reminders_not_failures(self):
        reply = design_reply()
        reply['segments'][1]['minutes'] = 5          # 2 examples in 5 minutes, and the total no longer adds up
        reply['items'] = [i for i in reply['items'] if i['id'] != 'P3']
        reply['segments'][2]['items'] = ['P1', 'P2']
        reply['objectives'][1]['checked_by'] = ['P9']
        reply['plan_links'] = reply['plan_links'][:1]
        reply['items'][1]['steps'] = [{'do': '$0.2 \\times 0.02 + 0.8 \\times 0.05 = 0.06$', 'why': ''}]
        design, hard, soft = D.validate(reply, CONTEXT)
        self.assertEqual(hard, [])
        text = '\n'.join(soft)
        for expected in ('合计 17 分钟，授课时长是 20 分钟', '「双产线」有 2 道例题却只有 5 分钟', '当堂练习应为 3 道', '第 2 条目标没有对应',
                         '「知识点12」「知识点13」没有在 plan_links', '题目 E2 的算式复算不一致'):
            self.assertIn(expected, text)
        codes = {code: passed for code, _, passed, _ in D.report(design, CONTEXT)}
        self.assertEqual(codes, {'design_time': False, 'design_items': False, 'design_objectives': False,
                                 'design_focus': True, 'design_plan': False})

    def test_broken_structure_and_missing_sources_are_hard_problems(self):
        reply = design_reply()
        reply['focus']['key_points'] = []
        reply['segments'][1]['items'] = ['E1', 'E9']
        reply['segments'][2]['items'] = ['P1', 'P2', 'P3', 'E1']
        reply['items'][0]['sources'] = ['M99']
        reply['items'][2]['stem'] = '见资料 [V7]。'
        reply['segments'][0]['kind'] = 'lecture'
        _, hard, _ = D.validate(reply, CONTEXT)
        text = '\n'.join(hard)
        for expected in ('课堂设计的重点（key_points）应有 1—2 条', '引用了不存在的题目 E9', '题目 E1 被环节 S2 和 S3 重复使用', '题目 E2 没有放进任何环节',
                         '不存在的来源编号 M99', '不存在的来源编号 V7', '环节 S1 的 kind 应为'):
            self.assertIn(expected, text)
        self.assertEqual(D.validate('不是对象', CONTEXT)[0], None)
        with self.assertRaises(D.DesignError):
            D.parse('我先说明思路')

    def test_student_name_and_guessing_without_a_profile_are_reported(self):
        reply = design_reply()
        reply['items'][0]['stem'] = '小许抽到一件次品，求它来自 A1 的概率。'
        self.assertIn('学生姓名', '\n'.join(D.validate(reply, CONTEXT)[2]))
        anonymous = {**CONTEXT, 'personalization_mode': 'none', 'personalization_plan': []}
        self.assertIn('learner 应为空', '\n'.join(D.validate(design_reply(), anonymous)[2]))

    def test_under_escaped_latex_in_json_is_restored_and_real_line_breaks_are_kept(self):
        reply = D.parse('{"a":"$\\frac{1}{2} \\times 3 \\neq 4$，$p \\ne 0$，\\text{次品} \\binom{n}{k} \\right)"}')
        self.assertEqual(reply['a'], '$\\frac{1}{2} \\times 3 \\neq 4$，$p \\ne 0$，\\text{次品} \\binom{n}{k} \\right)')
        self.assertEqual(D.restore_latex('第一行\ne 是自然常数\n第二行'), '第一行\ne 是自然常数\n第二行')
        broken = design_reply()
        broken['items'][0]['answer'] = '$\x0crac{3}{8}$'  # a form feed the restore cannot explain
        self.assertEqual(D.validate(D._restore(broken), CONTEXT)[1], [])
        broken['items'][0]['answer'] = '结果\x0c是 0.375'
        self.assertIn('每个反斜杠要写成两个', '\n'.join(D.validate(broken, CONTEXT)[1]))

    def test_every_document_quotes_the_same_problem_text_and_the_student_view_has_no_answer(self):
        design = D.validate(design_reply(), CONTEXT)[0]
        teacher, student = D.item_block(design, 'E1'), D.item_block(design, 'E1', answers=False)
        stem = design['items'][0]['stem']
        self.assertIn(stem, teacher)
        self.assertIn(stem, student)
        self.assertIn('**答案**　0.375', teacher)
        for hidden in ('0.375', '解答', '易错', '[M13]'):
            self.assertNotIn(hidden, student)
        book = D.document(design, {'S1': '先问分母是什么。'}, CONTEXT)
        headings = [line for line in book.splitlines() if line.startswith('## ')]
        self.assertEqual(headings, ['## 本课要点', '## 一、新授：贝叶斯公式（3 分钟）· 重点', '## 二、例题：双产线（8 分钟）',
                                    '## 三、当堂练习（9 分钟）', '## 小结', '## 板书设计', '## 课后作业'])
        self.assertIn('| 3—11 | 例题：双产线 | 板演例1；学生口述例2（例1、例2） |', book)
        self.assertIn('（由练3、作业1检验）', book)
        self.assertIn('知识点12：逐项加权（第 2 环节）', book)
        self.assertEqual(book.count(stem), 1)
        self.assertIn('先问分母是什么。', book.split('## 一、')[1].split('## 二、')[0])
        self.assertIn('- 板演例1', book)                      # a segment without written notes shows the design's moves
        self.assertIn('```figure\n' + json.dumps(TREE, ensure_ascii=False), book)
        self.assertIn('**提问 1**（基础）分母是什么？\n- 预期回答：全概率\n- 常见错答：只写一项\n- 追问：另一条产线呢？', book)


    def test_readers_see_names_not_ids_and_homework_is_printed_once(self):
        reply = design_reply()
        reply['learner'] = 'S1 先看 W9 以外的情况；E1 做错的学生在 S3 再练 P1。'
        reply['segments'][1].update(title='例1：双产线', teacher=['板演 E1', '学生口述 E2'], if_short='E2 只列式')
        reply['segments'][2].update(title='当堂练习：后验计算', screen='显示 P1、P2、P3')
        reply['segments'].append({**reply['segments'][2], 'id': 'S4', 'kind': 'summary', 'title': '课堂小结与作业', 'minutes': 1,
                                  'items': ['H1'], 'teacher': ['布置 H1'], 'screen': ''})
        reply['segments'][2]['minutes'] = 8
        reply['items'][0]['stem'] += ' [M13]'
        reply['items'][1]['check'] = '与 E1 的 $P(D)$ 比较，$E_1$ 不是题号'
        reply['plan_links'] = [{'knowledge_id': '11', 'segment': ['S9', 'S1'], 'how': '看 E1'},
                               {'knowledge_id': 12, 'segment': 'S7', 'how': '逐项加权'}, {'knowledge_id': 99, 'segment': 'S1', 'how': '无关'}]
        design, hard, soft = D.validate(reply, CONTEXT)
        self.assertEqual(hard, [])
        text = '\n'.join(soft)
        for expected in ('plan_links 第 2 项的 segment「S7」不是已有的环节编号', 'plan_links 第 3 项的 knowledge_id「99」不在本次讲解安排里',
                         '「知识点12」「知识点13」没有在 plan_links'):
            self.assertIn(expected, text)
        self.assertEqual(design['plan_links'], [{'knowledge_id': 11, 'segment': 'S1', 'how': '看 E1'}])
        self.assertEqual([s['title'] for s in design['segments']], ['贝叶斯公式', '双产线', '后验计算', '课堂小结与作业'])
        book = D.document(design, {'S2': '先让学生读 E1，再看 S1 的板书。公式 $E1$ 里的不换。'}, CONTEXT)
        for shown in ('第 1 环节 先看 W9 以外的情况；例1 做错的学生在 第 3 环节 再练 练1。', '板演 例1；学生口述 例2（例1、例2）',
                      '先让学生读 例1，再看 第 1 环节 的板书。公式 $E1$ 里的不换。', '**时间不够时**　例2 只列式', '- **屏幕**：显示 练1、练2、练3',
                      '知识点11：看 例1（第 1 环节）', '与 例1 的 $P(D)$ 比较，$E_1$ 不是题号', '## 四、课堂小结与作业（1 分钟）',
                      '**布置作业**　作业1（基础），题目见「课后作业」。', '### 例1 双产线基准', '### 练3（提高）'):
            self.assertIn(shown, book)
        self.assertEqual(book.count('自拟一组先验，比较前后两个后验。'), 1)        # homework once, in its own section
        self.assertEqual(book.count('[M13]', book.index('### 例1'), book.index('**解答**')), 1)   # a source is not cited twice
        self.assertLess(book.index('**小结要点**'), book.index('**布置作业**'))
        self.assertNotIn('[M13]', D.item_block(design, 'E1', answers=False))
        self.assertEqual(D.validate(design, CONTEXT)[0], design)

    def test_a_numeric_binomial_coefficient_is_recomputed_as_one_number(self):
        import mathcheck
        right = mathcheck.check_text('$\\binom{10}{2}(0.1)^2(0.9)^8 = 45 \\times 0.01 \\times 0.43046721$')
        self.assertEqual((right['checked'], right['findings']), (1, []))
        wrong = mathcheck.check_text('$\\binom{10}{2} \\times 0.01 = 0.54$')
        self.assertEqual(len(wrong['findings']), 1)
        self.assertEqual(mathcheck.check_text('$\\binom{n}{x}p^x = 0.5$')['findings'], [])


class TextMeasures(unittest.TestCase):
    def test_hits_separate_record_keeping_from_subject_words(self):
        padded = ('教师首先带领全班回顾。需要特别说明的是，全班暂无足够的诊断数据，练习结果仅作为后续教学调整依据。'
                  '本节为四十五分钟新授课，面向十五名学生。第一，理解定义。第二，会计算。')
        counts = style.summary(padded)['counts']
        self.assertEqual(counts, {'system_talk': 1, 'disclaimer': 2, 'narration': 1, 'filler': 1, 'ordinal': 2, 'chinese_numeral': 2})
        plain = '先问先验概率是多少，再让学生根据证据更新。板书全概率公式，45 分钟内完成 3 道练习。'
        self.assertEqual(sum(style.summary(plain)['counts'].values()), 0)
        self.assertEqual(style.reminder(plain), '')
        self.assertIn('免责声明 2 处', style.reminder(padded))
        fenced = '```figure\n{"title":"暂无数据"}\n```\n正文'
        self.assertEqual(style.summary(fenced)['counts']['disclaimer'], 0)

    def test_repeated_phrases_report_the_longest_form_once(self):
        text = '当 $n>100$ 且 $p<0.05$ 时可以近似。' * 3 + '另外 n > 100 且 p < 0.05 也要检查。'
        phrases = dict(style.repeated(text))
        self.assertEqual(phrases['当n>100且p<0.05时可以近似。'], 3)
        self.assertNotIn('n>100且p<0.05', phrases)
        self.assertGreater(style.summary(text)['repeated_share'], 0.5)

    def test_baseline_reads_minutes_per_problem_from_a_design_or_an_old_schedule(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scale'))
        import quality_baseline as baseline
        design = D.validate(design_reply(), CONTEXT)[0]
        self.assertEqual(baseline.minutes_per_item('', {'lesson_design': design}), 3.4)
        old = ('# 课\n## 本次学习安排\n导入 10 分钟，贝叶斯推导与例题 15 分钟，练习与小结 10 分钟，总计 35 分钟。\n'
               '## 例题与检验\n例题 1 …… 例题 2 …… 练习 1 …… 练习 2 …… 练习 3 ……\n')
        self.assertEqual(baseline.minutes_per_item(old, {}), 5.0)
        chinese = '# 课\n## 本次学习安排\n第四环节，完成例一、例二以及练一，用时约十分钟。\n## 例题与检验\n例1 例2 练1\n'
        self.assertEqual(baseline.minutes_per_item(chinese, {}), 3.3)
        self.assertIsNone(baseline.minutes_per_item('# 课\n没有安排', {}))
        self.assertEqual(baseline.index_style('A1 与 A2')[0], 'plain')
        self.assertEqual(baseline.index_style('A₁ 与 A₂')[0], 'unicode')
        self.assertEqual(baseline.index_style('$A_1$ 与 $A_{2}$')[0], 'latex')
        deck = {'slides': [{'kind': 'question', 'notes': '', 'visual': {'type': 'practice', 'problem': '求 P(A|B)。'}},
                           {'kind': 'concept', 'notes': '', 'visual': {'type': 'recap'}},
                           {'kind': 'summary', 'notes': '', 'visual': {'type': 'recap'}}]}
        quiz = {'questions': [{'stem': '另一道题', 'answer': 'a', 'analysis': '等等，让我重新仔细计算'}]}
        found = baseline.companions('练习：求 P(A|B)。', deck, quiz)
        self.assertEqual((found['deck']['problems_verbatim'], found['deck']['closing_review_slides']), (1, 2))
        self.assertEqual((found['quiz']['stems_verbatim'], found['quiz']['self_corrections']), (0, 1))

    def test_live_preview_can_add_parts_once_the_design_is_known(self):
        hub = ProgressHub()
        hub.start(1, [('design', '课堂设计')])
        first = hub.changes(1, 0)
        hub.add(1, [('S1', '新授'), ('design', '重复的不再加')])
        second = hub.changes(1, first['version'])
        self.assertEqual(second['order'], ['design', 'S1'])
        self.assertEqual([(p['key'], p['heading'], p['state']) for p in second['parts']], [('S1', '新授', 'waiting')])


class DesignedPipeline(base.CourseJobTests):
    def new_payload(self, **overrides):
        return self.payload(generation_mode='designed-v1', teaching_options=dict(OPTIONS), **overrides)

    def staged(self, prepared, on_attempt):
        on_attempt()
        _, user = request_of(prepared)
        if 'target_segment' not in user:
            return module.ModelResult(json.dumps(design_reply(), ensure_ascii=False), {'total_tokens': 7})
        return module.ModelResult(f"先问分母是什么，再板书推导。{'讲' * user['budget']['low']} [M13]", {'total_tokens': 11})

    def run_job(self, model, payload=None, path='/api/jobs'):
        with patch.object(module, 'call_qwen', model):
            response = self.client.post(path, json=payload if payload is not None else self.new_payload())
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.tasks.wait_idle(10))
        job = self.tasks.detail(response.json['id'])
        with self.fixture_db() as conn:
            run = conn.execute('SELECT * FROM generation_runs WHERE id=%s', (job['run_id'],)).fetchone()
        return job, run

    def lecture(self, run):
        with self.fixture_db() as conn:
            return conn.execute('SELECT * FROM lectures WHERE id=%s', (run['lecture_id'],)).fetchone()

    def test_design_stops_for_the_teacher_and_the_confirmed_design_is_written_without_asking_again(self):
        before = self.count('lectures')
        job, run = self.run_job(self.staged)
        self.assertEqual((job['status'], job['design_review'], job['lecture_id']), ('succeeded', True, None))
        self.assertEqual(self.count('lectures'), before)
        self.assertEqual(run['response_metadata']['operation'], 'lesson_design')
        self.assertEqual(run['prompt_version'], 'lesson-design-v1+probability-v1')
        payload, user = request_of(type('P', (), {'body': run['request_body']}))
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        for private in ('designed', 'scope_check', 'input_budget', 'lecture_workflow'):
            self.assertNotIn(private, user['context'])
        self.assertNotIn('recorder', json.dumps(user['context'], ensure_ascii=False))
        self.assertEqual([k['id'] for k in user['context']['topic']['knowledge']], [11, 12, 13])
        self.assertIn('不要把它们的措辞写进任何输出', payload['messages'][0]['content'])
        self.assertIn('学科要求：概率论', payload['messages'][0]['content'])

        shown = self.client.get(f'/api/lesson-designs/{run["id"]}').json
        self.assertEqual((shown['duration'], shown['labels']['E1'], shown['reminders']), (20, '例1', []))
        self.assertIn('| 0—3 | 新授：贝叶斯公式（重点） |', shown['preview'])
        calls = []

        def writer(prepared, on_attempt):
            calls.append(request_of(prepared)[1])
            return self.staged(prepared, on_attempt)
        job, written = self.run_job(writer, {}, f'/api/lesson-designs/{run["id"]}/expand')
        self.assertEqual(job['status'], 'succeeded', written['error_message'])
        self.assertEqual(sorted(call['target_segment'] for call in calls), ['S1', 'S2'])   # no second design call
        self.assertEqual(written['request_body'], run['request_body'])
        self.assertEqual(written['context']['designed']['confirmed']['edited'], False)
        self.assertEqual(written['usage'], {'total_tokens': 22, 'model_calls': 2})
        concept = next(call for call in calls if call['target_segment'] == 'S1')
        self.assertEqual([m['citation_id'] for m in concept['context']['materials']], ['M11', 'M13'])
        self.assertEqual(concept['design']['items'][0]['answer'], '0.375')
        self.assertEqual(concept['budget'], {'low': 48, 'high': 120})
        lecture = self.lecture(written)
        self.assertEqual(lecture['context']['lesson_design']['title'], '隔离课堂设计')
        self.assertTrue(lecture['content'].startswith('# 隔离课堂设计\n\n## 本课要点\n\n**重点**　贝叶斯公式的推导'))
        self.assertEqual(lecture['content'].count(design_reply()['items'][0]['stem']), 1)
        report = {c['code']: c['status'] for c in written['citation_check']['quality_report']['automatic']}
        for code in ('sections', 'length', 'design_time', 'design_items', 'design_objectives', 'design_focus', 'design_plan',
                     'style', 'arithmetic', 'figures', 'citation_ids'):
            self.assertEqual(report[code], 'pass', code)
        self.assertNotIn('plan_locations', report)
        self.assertTrue(written['citation_check']['quality_report']['version'].endswith('+design-v1'))
        self.assertEqual(self.client.get(f'/api/lesson-designs/{written["id"]}').status_code, 404)

    def test_without_review_one_job_designs_and_writes_and_a_batch_never_waits(self):
        job, run = self.run_job(self.staged, self.new_payload(design_review=False))
        self.assertEqual((job['status'], job['design_review']), ('succeeded', False))
        self.assertEqual([s['stage'] for s in run['response_metadata']['stages']], ['design', 'S1', 'S2'])
        self.assertEqual(run['usage']['model_calls'], 3)
        self.assertEqual(run['response_metadata']['design']['version'], D.VERSION)
        self.assertEqual(self.lecture(run)['context']['lesson_design']['items'][0]['id'], 'E1')
        queue = JobQueue(lambda: module.db(), module.execute_job, capacity=4, workers=2, bulk_capacity=20)
        with patch.object(module, 'TASKS', queue), patch.object(module, 'call_qwen', self.staged):
            payload = {k: v for k, v in self.new_payload().items() if k != 'student_id'}
            response = self.client.post('/api/jobs/batch', json={**payload, 'student_ids': [11, 12]})
            self.assertTrue(queue.wait_idle(15))
            status = self.client.get('/api/job-status?ids=' + ','.join(j['job_id'] for j in response.json['jobs'])).json['jobs']
            queue.close()
        self.assertTrue(all(s['status'] == 'succeeded' and s['lecture_id'] for s in status), status)
        self.assertEqual(self.post(self.payload(generation_mode='designed-v1')).status_code, 400)  # needs lesson settings

    def test_design_reminders_get_one_retry_with_the_concrete_problems(self):
        seen = []

        def model(prepared, on_attempt):
            payload, user = request_of(prepared)
            if 'target_segment' in user:
                return self.staged(prepared, on_attempt)
            seen.append(payload['messages'])
            on_attempt()
            reply = design_reply()
            if len(payload['messages']) == 2:
                reply['segments'][2]['minutes'] = 6
            return module.ModelResult(json.dumps(reply, ensure_ascii=False))
        job, run = self.run_job(model, self.new_payload(design_review=False))
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertEqual([m['role'] for m in seen[1]], ['system', 'user', 'assistant', 'user'])
        self.assertIn('各环节合计 17 分钟，授课时长是 20 分钟', seen[1][3]['content'])
        self.assertIn('有 3 道当堂练习却只有 6 分钟', seen[1][3]['content'])
        stages = [(s['stage'], s['attempt'], bool(s['problems'])) for s in run['response_metadata']['stages']]
        self.assertEqual(stages[:2], [('design', 1, True), ('design', 2, False)])
        self.assertEqual(run['response_metadata']['design_reminders'], [])

    def test_a_recomputed_formula_alone_is_shown_but_does_not_cost_a_second_design_call(self):
        calls = []

        def model(prepared, on_attempt):
            _, user = request_of(prepared)
            if 'target_segment' in user:
                self.assertEqual(user['names']['E1'], '例1')
                self.assertEqual(user['names']['S2'], '第 2 环节')
                return self.staged(prepared, on_attempt)
            calls.append(prepared)
            on_attempt()
            reply = design_reply()
            reply['items'][1]['steps'] = [{'do': '$0.2 \\times 0.02 + 0.8 \\times 0.05 = 0.06$', 'why': ''}]
            return module.ModelResult(json.dumps(reply, ensure_ascii=False))
        job, run = self.run_job(model, self.new_payload(design_review=False))
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertEqual(len(calls), 1)
        self.assertIn('题目 E2 的算式复算不一致', run['response_metadata']['design_reminders'][0])
        report = {c['code']: c['status'] for c in run['citation_check']['quality_report']['automatic']}
        self.assertEqual(report['arithmetic'], 'warning')

    def test_a_design_that_stays_tight_is_kept_with_its_reminders_and_a_broken_one_fails(self):
        def tight(prepared, on_attempt):
            _, user = request_of(prepared)
            if 'target_segment' in user:
                return self.staged(prepared, on_attempt)
            on_attempt()
            reply = design_reply()
            reply['segments'][2]['minutes'] = 6
            return module.ModelResult(json.dumps(reply, ensure_ascii=False))
        job, run = self.run_job(tight, self.new_payload(design_review=False))
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertEqual(len(run['response_metadata']['design_reminders']), 2)
        report = {c['code']: c for c in run['citation_check']['quality_report']['automatic']}
        self.assertEqual(report['design_time']['status'], 'warning')
        self.assertIn('「当堂练习」6 分钟 3 题', report['design_time']['detail'])

        def broken(prepared, on_attempt):
            on_attempt()
            reply = design_reply()
            reply['segments'][1]['items'] = ['E1', 'E7']
            return module.ModelResult(json.dumps(reply, ensure_ascii=False))
        before = self.count('lectures')
        job, run = self.run_job(broken)
        self.assertEqual((job['status'], run['error_code']), ('failed', 'invalid_design'))
        self.assertIn('引用了不存在的题目 E7', run['error_message'])
        self.assertEqual(self.count('lectures'), before)
        self.assertEqual(self.client.get(f'/api/lesson-designs/{run["id"]}').status_code, 404)

    def test_segment_notes_are_rewritten_once_for_narration_and_fail_on_a_missing_source(self):
        seen = []

        def model(prepared, on_attempt):
            payload, user = request_of(prepared)
            if user.get('target_segment') == 'S1':
                seen.append(payload['messages'])
                on_attempt()
                if len(payload['messages']) == 2:
                    return module.ModelResult('## 一、新授\n教师首先带领全班回顾。需要特别说明的是，全班暂无足够的诊断数据。[K11]'
                                              + '讲' * user['budget']['low'])
            return self.staged(prepared, on_attempt)
        job, run = self.run_job(model, self.new_payload(design_review=False))
        self.assertEqual(job['status'], 'succeeded', run['error_message'])
        self.assertEqual(len(seen), 2)
        self.assertIn('表达需要改：', seen[1][3]['content'])
        self.assertIn('第三人称旁白 1 处（如「教师首先」）', seen[1][3]['content'])
        content = self.lecture(run)['content']
        self.assertNotIn('教师首先', content)
        self.assertNotIn('[K11]', content)

        def missing(prepared, on_attempt):
            _, user = request_of(prepared)
            if user.get('target_segment') == 'S2':
                on_attempt()
                return module.ModelResult('带着学生读题。[M999]')
            return self.staged(prepared, on_attempt)
        before = self.count('lectures')
        job, run = self.run_job(missing, self.new_payload(design_review=False))
        self.assertEqual((job['status'], run['error_code']), ('failed', 'invalid_section'))
        self.assertIn('M999', run['error_message'])
        self.assertEqual(self.count('lectures'), before)
        self.assertIn('## 一、新授：贝叶斯公式', run['content'])   # the readable draft is kept for the teacher
        self.assertEqual(run['response_metadata']['design']['title'], '隔离课堂设计')

    def test_teacher_edits_are_checked_recorded_and_used(self):
        _, run = self.run_job(self.staged)
        edited = copy.deepcopy(run['response_metadata']['design'])
        edited['items'][0]['answer'] = '3/8'
        edited['segments'][0]['minutes'], edited['segments'][2]['minutes'] = 4, 8
        checked = self.client.post(f'/api/lesson-designs/{run["id"]}/check', json={'design': edited})
        self.assertEqual(checked.status_code, 200, checked.json)
        self.assertIn('有 3 道当堂练习却只有 8 分钟', '\n'.join(checked.json['reminders']))
        invalid = copy.deepcopy(edited)
        invalid['segments'][1]['items'] = ['E1']
        refused = self.client.post(f'/api/lesson-designs/{run["id"]}/expand', json={'design': invalid})
        self.assertEqual(refused.status_code, 400)
        self.assertIn('题目 E2 没有放进任何环节', refused.json['problems'])
        empty = copy.deepcopy(edited)
        empty['items'][0]['stem'] = ''
        self.assertIn('题目 E1缺少题干（stem）', self.client.post(f'/api/lesson-designs/{run["id"]}/check', json={'design': empty}).json['problems'])
        job, written = self.run_job(self.staged, {'design': edited}, f'/api/lesson-designs/{run["id"]}/expand')
        self.assertEqual(job['status'], 'succeeded', written['error_message'])
        self.assertEqual(written['context']['designed']['confirmed']['edited'], True)
        self.assertIn('**答案**　3/8', self.lecture(written)['content'])
        self.assertIn('（4 分钟）· 重点', self.lecture(written)['content'])

    def test_a_written_request_produces_a_new_design_from_the_previous_one(self):
        _, run = self.run_job(self.staged)
        self.assertEqual(self.client.post(f'/api/lesson-designs/{run["id"]}/revise', json={'note': ' '}).status_code, 400)
        job, revised = self.run_job(self.staged, {'note': '例2 换成保险题'}, f'/api/lesson-designs/{run["id"]}/revise')
        self.assertEqual((job['status'], job['design_review']), ('succeeded', True))
        payload, user = request_of(type('P', (), {'body': revised['request_body']}))
        self.assertEqual(user['teacher_feedback'], '例2 换成保险题')
        self.assertEqual(user['previous_design']['items'][1]['id'], 'E2')
        self.assertIn('按教师意见修改', payload['messages'][0]['content'])
        self.assertEqual(revised['prompt_version'], 'lesson-design-v1+probability-v1+revision')
        self.assertEqual(revised['context']['designed']['feedback']['source_run_id'], run['id'])

    def test_retry_reuses_the_frozen_request_and_the_confirmed_design(self):
        def fail(prepared, on_attempt):
            on_attempt()
            raise module.ModelError('timeout', '测试超时')
        _, run = self.run_job(self.staged)
        with patch.object(module, 'call_qwen', fail):
            response = self.client.post(f'/api/lesson-designs/{run["id"]}/expand', json={})
            self.assertTrue(self.tasks.wait_idle(10))
        failed = self.tasks.detail(response.json['id'])
        self.assertEqual(failed['status'], 'failed')
        calls = []

        def writer(prepared, on_attempt):
            calls.append(request_of(prepared)[1])
            return self.staged(prepared, on_attempt)
        job, second = self.run_job(writer, None, f'/api/generation-runs/{failed["run_id"]}/retry')
        self.assertEqual(job['status'], 'succeeded', second['error_message'])
        self.assertTrue(all('target_segment' in call for call in calls))
        self.assertEqual(second['context']['designed']['confirmed']['source_run_id'], run['id'])

    def test_section_rewrites_of_staged_lectures_ask_for_prose_not_a_planning_object(self):
        _, run = self.run_job(self.staged, self.new_payload(design_review=False))
        lecture = self.lecture(run)
        edit = dict(base_content=lecture['content'], title='二、例题：双产线（8 分钟）', action='expand', instruction='')
        payload, user = request_of(prepare_section({**lecture['context'], 'section_edit': edit}, lecture['goal'], 'm'))
        self.assertNotIn('response_format', payload)
        self.assertNotIn('stream', payload)
        self.assertNotIn('只输出一个 JSON 对象', payload['messages'][0]['content'])
        self.assertIn('写给同行看的备课笔记', payload['messages'][0]['content'])
        self.assertIn('本次任务改为局部修改', payload['messages'][0]['content'])
        self.assertEqual(user['design']['items'][0]['id'], 'E1')
        self.assertNotIn('designed', user['context'])
        _, old = self.run_job(lambda prepared, attempted: self.sectioned_reply(prepared, attempted),
                              self.payload(generation_mode='sectioned-v1', lecture_format='plain', teaching_options=dict(OPTIONS)))
        old_lecture = self.lecture(old)
        payload, _ = request_of(prepare_section({**old_lecture['context'], 'section_edit': {**edit, 'title': '核心讲解',
                                                 'base_content': old_lecture['content']}}, old_lecture['goal'], 'm'))
        self.assertNotIn('response_format', payload)
        self.assertNotIn('本次只做规划', payload['messages'][0]['content'])
        self.assertIn('## 核心讲解', payload['messages'][0]['content'])

    def sectioned_reply(self, prepared, on_attempt):
        import sectioned
        on_attempt()
        _, user = request_of(prepared)
        if 'target_section' not in user:
            outline = {'title': '旧流程讲义', 'notation': '', 'sections': {name: ['要点一', '要点二'] for name, _, _ in sectioned.SECTIONS}}
            return module.ModelResult(json.dumps(outline, ensure_ascii=False))
        marks = ' [K11][K12][K13]' if user['target_section'] == '核心讲解' else ''
        return module.ModelResult(f"## {user['target_section']}\n{'讲' * user['section_quota']['low']} [M11]{marks}\n")

    def test_program_check_tolerates_a_designed_lecture_without_its_design(self):
        ctx = module.get_context(11, 'probability_advanced')
        ctx.update(lecture_pipeline='v3', lecture_format='rich-v1', generation_mode='designed-v1', teaching_options={**OPTIONS, 'examples': 2, 'exercises': 3})
        report = check_output('# 课\n## 本课要点\n- 重点 [M11]\n## 一、新授\n教师首先带领全班回顾，全班暂无足够的诊断数据。\n## 二、例题\n正文\n',
                              ctx, ctx['topic']['goal'])
        codes = {c['code']: c['status'] for c in report['automatic']}
        self.assertEqual((codes['sections'], codes['style']), ('pass', 'warning'))
        self.assertNotIn('design_time', codes)


# The inherited course contracts already run in their own module.
for _name in [n for n in dir(base.CourseJobTests) if n.startswith('test_')]:
    setattr(DesignedPipeline, _name, None)
