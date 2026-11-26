"""The fixed evaluation set (demo/scale/evalset.py) end to end with a simulated model; never calls a real model."""
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scale'))
import corpus  # noqa: E402
import evalset  # noqa: E402
from qwen import ModelResult  # noqa: E402


def design(user, total_off=0):
    """A sound design for whatever lesson is asked; total_off shortens the first segment."""
    context, options = user['context'], user['context']['teaching_options']
    knowledge = [point['id'] for point in context['topic']['knowledge']]
    cite = context['materials'][0]['citation_id']

    def item(identity, role, number):
        return {'id': identity, 'role': role, 'level': 'basic', 'knowledge_id': knowledge[0], 'title': '',
                'stem': f'第 {number} 题：写出解题步骤，并说明每一步的依据。', 'steps': [{'do': '写出这一步', 'why': '依据定义'}],
                'answer': f'结果 {number}', 'check': '', 'mistake': '', 'sources': [cite]}
    examples = [item(f'E{n}', 'example', n) for n in range(1, options['examples'] + 1)]
    practice = [item(f'P{n}', 'practice', 10 + n) for n in range(1, options['exercises'] + 1)]
    segment = dict(focus=False, knowledge_ids=knowledge, screen='', board=[], students='', questions=[], if_short='', sources=[])
    segments = [{**segment, 'id': 'S1', 'kind': 'concept', 'title': '依据', 'focus': True, 'sources': [cite], 'items': [],
                 'teacher': ['先讲依据'], 'minutes': options['duration'] - 4 * len(examples) - 3 * len(practice) - total_off},
                {**segment, 'id': 'S2', 'kind': 'example', 'title': '例题', 'items': [e['id'] for e in examples],
                 'teacher': ['板演例题'], 'minutes': 4 * len(examples)},
                {**segment, 'id': 'S3', 'kind': 'practice', 'title': '练习', 'items': [p['id'] for p in practice],
                 'teacher': ['看写到哪一步'], 'minutes': 3 * len(practice)}]
    return {'title': '评测课', 'focus': {'key_points': ['每一步的依据'], 'difficulties': [], 'not_today': []},
            'learner': '' if context['personalization_mode'] == 'none' else '先让学生说依据。',
            'plan_links': [{'knowledge_id': entry['knowledge_id'], 'segment': 'S1', 'how': '先讲依据'}
                           for entry in context.get('personalization_plan', [])],
            'notation': [], 'objectives': [{'text': '能说明每一步的依据', 'checked_by': ['P1']}],
            'segments': segments, 'items': examples + practice, 'board_plan': {'main': ['依据'], 'side': []},
            'figures': [], 'summary': ['每一步都有依据']}


def model(prepared, on_attempt):
    on_attempt()
    payload = json.loads(prepared.body)
    user = json.loads(payload['messages'][1]['content'])
    if 'target_segment' in user:
        cite = user['context']['materials'][0]['citation_id']
        return ModelResult(f'先请学生说出依据，再看写到哪一步。{"讲" * user["budget"]["low"]} [{cite}]', {'total_tokens': 100})
    # The first design is five minutes short, so the program asks once more with that problem.
    first = len(payload['messages']) == 2
    return ModelResult(json.dumps(design(user, 5 if first else 0), ensure_ascii=False), {'total_tokens': 1000})


class EvaluationSetTests(unittest.TestCase):
    def test_the_set_covers_both_courses_targets_and_lengths_and_the_holdout_shares_no_point(self):
        data = evalset.load_set()
        cases = data['cases']
        self.assertEqual(Counter(case['split'] for case in cases), {'dev': 8, 'holdout': 4})
        self.assertEqual(len({case['id'] for case in cases}), 12)
        for split in ('dev', 'holdout'):
            chosen = [case for case in cases if case['split'] == split]
            self.assertEqual({case['course'] for case in chosen}, set(corpus.COURSES), split)
            self.assertEqual({case['target']['kind'] for case in chosen}, {'student', 'class'}, split)
            self.assertEqual({case['duration'] for case in chosen}, {40, 45, 90}, split)
        for name in corpus.COURSES:
            points = {point['name'] for point in corpus.load_course(name, 0)['points']}
            used = {split: {k for case in cases if case['course'] == name and case['split'] == split for k in case['knowledge']}
                    for split in ('dev', 'holdout')}
            self.assertLessEqual(used['dev'] | used['holdout'], points)
            self.assertFalse(used['dev'] & used['holdout'], name)
        self.assertEqual([case['id'] for case in evalset.pick(data, ids=['P2', 'L1'])], ['L1', 'P2'])
        with self.assertRaises(ValueError):
            evalset.pick(data, ids=['X9'])

    def test_categories_follow_the_wording_of_the_checks(self):
        self.assertEqual(evalset.category('各环节合计 17 分钟，授课时长是 20 分钟，两者要相等'), '时间合计')
        self.assertEqual(evalset.category('第 1 条目标写由 H1 检验，但 H1 的题干、步骤和答案里没有这条目标的内容'), '目标与检验题')
        self.assertEqual(evalset.category('这是一对一辅导，环节 S5 写了「巡视」'), '一对一措辞')
        self.assertEqual(evalset.category('题目 E1 的算式复算不一致：0.3 × 2 = 0.5'), '算式复算')
        self.assertEqual(evalset.category('篇幅有 900 个汉字，超过本环节上限 400；删去重复'), '环节过长')
        self.assertEqual(evalset.category('没有见过的提醒'), '其他')

    def test_runs_started_in_the_same_second_get_folders_of_their_own(self):
        with tempfile.TemporaryDirectory() as base:
            first, second = evalset.new_folder(Path(base)), evalset.new_folder(Path(base))
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir() and second.is_dir())
            if first.name[:15] == second.name[:15]:
                self.assertEqual(second.name, first.name + '-2')

    def test_a_failed_lesson_is_counted_but_shows_no_numbers_it_does_not_have(self):
        base = dict(case='L1', target='全班', mode='class', duration=45, seconds=180.0, design_attempts=2, design_problems=[],
                    rewritten=[], rewrite_problems={}, reminders=[], warnings=[], tokens=50000, calls=5, chinese_characters=3000,
                    status='succeeded', error=None)
        failed = {**base, 'case': 'L2', 'status': 'failed', 'error': '课堂设计重试一次后仍未通过检查', 'tokens': 30000,
                  'calls': 2, 'warnings': [], 'chinese_characters': None}
        old = {**failed, 'case': 'P2', 'tokens': None, 'calls': None, 'design_attempts': 0}   # a failure recorded before F3
        text = evalset.summary([base, failed, old], 'qwen', False)
        self.assertIn('| L2 | 全班 | 班级画像 | 所选知识点全部材料 · — 份 | 45 | 失败：课堂设计重试一次后仍未通过检查 | 30000 | 180.0 | 2 | 是 | — | — | — | — |', text)
        self.assertIn('| P2 | 全班 | 班级画像 | 所选知识点全部材料 · — 份 | 45 | 失败：课堂设计重试一次后仍未通过检查 | — | 180.0 | — | — | — | — | — | — |', text)
        self.assertIn('成功 1/3；tokens 合计 80000（含失败的 1 节；1 节失败的课没有用量记录）。', text)
        self.assertIn('课堂设计重试（成功的课）：1/1。', text)

    def test_a_partial_correction_is_counted_as_a_retry_with_its_own_cost(self):
        base = dict(case='L1', target='全班', mode='class', duration=45, seconds=180.0, design_attempts=2, design_problems=[],
                    rewritten=[], rewrite_problems={}, reminders=[], warnings=[], tokens=50000, calls=5, chinese_characters=3000,
                    status='succeeded', error=None, design_retry='design-patch', retry_seconds=30.5, retry_tokens=4000)
        whole = {**base, 'case': 'L2', 'design_retry': 'design', 'retry_seconds': 90.0, 'retry_tokens': 20000}
        once = {**base, 'case': 'P1', 'design_attempts': 1, 'design_retry': None, 'retry_seconds': None, 'retry_tokens': None,
                'retrieval': 'hybrid', 'materials': 7}
        text = evalset.summary([base, whole, once], 'qwen', False)
        self.assertIn('| L1 | 全班 | 班级画像 | 所选知识点全部材料 · — 份 | 45 | 成功 | 50000 | 180.0 | 5 | 是（局部修正） |', text)
        self.assertIn('| L2 | 全班 | 班级画像 | 所选知识点全部材料 · — 份 | 45 | 成功 | 50000 | 180.0 | 5 | 是 |', text)
        self.assertIn('| P1 | 全班 | 班级画像 | 整门课检索 · 7 份 | 45 | 成功 | 50000 | 180.0 | 5 | 否 |', text)
        self.assertIn('课堂设计重试（成功的课）：2/3，其中局部修正 1 节；重试那一次的耗时中位数 60 秒、tokens 中位数 12000。', text)
        stages = [dict(stage='design', attempt=1, seconds=80, total_tokens=9000, problems=['a']),
                  dict(stage='design-patch', attempt=2, seconds=20, total_tokens=3000, problems=[]),
                  dict(stage='S2', attempt=1, seconds=10, problems=['b']), dict(stage='S2', attempt=2, seconds=9, problems=[])]
        row = dict(meta=dict(stages=stages), status='succeeded', report={}, usage={}, error_message=None, run_id=1,
                   prompt_version='v', active=0)
        case = dict(id='L1', split='dev', course='c', title='t', duration=40, examples=2, exercises=3)
        entry = evalset.record(case, {'target': '学生'}, {'personalization_mode': 'profile'}, row)
        self.assertEqual((entry['design_attempts'], entry['design_retry'], entry['retry_seconds'], entry['rewritten']),
                         (2, 'design-patch', 20, ['S2']))

    def test_dry_run_builds_every_lesson_through_the_page_routes_without_a_model(self):
        cases = evalset.pick(evalset.load_set(), ids=['L1', 'P4'])
        sizes = evalset.run(cases, students=4, log=lambda text: None)
        self.assertEqual([(s['case'], s['mode'], s['target']) for s in sizes], [('L1', 'profile', sizes[0]['target']), ('P4', 'class', '全班')])
        self.assertTrue(sizes[0]['target'].startswith('学生（'))
        for size in sizes:
            self.assertGreater(size['user'], 1000)
            self.assertGreaterEqual(size['materials'], 4)   # two points, an explanation and worked examples each
        # The courses are given their subjects, as a teacher would on the course page.
        self.assertEqual([s['prompt_version'] for s in sizes],
                         ['lesson-design-v1+math-general-v1+tutor-v1', 'lesson-design-v1+probability-general-v1'])
        plain = evalset.run(cases[:1], students=4, log=lambda text: None, subjects=False)
        self.assertEqual(plain[0]['prompt_version'], 'lesson-design-v1+tutor-v1')

    def test_a_simulated_run_writes_results_a_summary_and_blind_pairs(self):
        cases = evalset.pick(evalset.load_set(), ids=['L1', 'P4'])
        with tempfile.TemporaryDirectory() as folder:
            records = evalset.run(cases, ('profile', 'none'), model, concurrency=2, students=4, out=folder, log=lambda text: None)
            out = Path(folder)
            self.assertEqual([(r['case'], r['mode'], r['status']) for r in records],
                             [('L1', 'profile', 'succeeded'), ('L1', 'none', 'succeeded'),
                              ('P4', 'class', 'succeeded'), ('P4', 'none', 'succeeded')])
            for entry in records:
                self.assertEqual(entry['design_attempts'], 2)
                self.assertEqual({evalset.category(p) for p in entry['design_problems']}, {'时间合计'})
                self.assertEqual(entry['calls'], 4)   # two design calls, then the concept and example segments
                self.assertEqual(entry['rewritten'], [])
                self.assertGreater(entry['chinese_characters'], 0)
                self.assertTrue((out / f'{entry["case"]}-{entry["mode"]}.md').read_text(encoding='utf-8').startswith('# 评测课'))
                self.assertTrue((out / f'{entry["case"]}-{entry["mode"]}-design.json').exists())
            saved = json.loads((out / 'results.json').read_text(encoding='utf-8'))
            self.assertEqual((saved['model'], saved['modes'], len(saved['results'])), ('模拟', ['profile', 'none'], 4))
            text = (out / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('课堂设计重试（成功的课）：4/4', text)
            self.assertIn('第一次课堂设计的问题（按类别，数字是出现的课数）：时间合计 4', text)
            self.assertEqual(text.count('| 成功 |'), 4)
            key = json.loads((out / 'blind' / 'key.json').read_text(encoding='utf-8'))
            self.assertEqual({case: sorted(pair.values()) for case, pair in key.items()},
                             {'L1': ['none', 'profile'], 'P4': ['class', 'none']})
            for case, pair in key.items():
                for letter, mode in pair.items():
                    self.assertEqual((out / 'blind' / f'{case}-{letter}.md').read_text(encoding='utf-8'),
                                     (out / f'{case}-{mode}.md').read_text(encoding='utf-8'))
            sheet = (out / 'blind' / '评分表.md').read_text(encoding='utf-8')
            self.assertIn('| 维度 | 看什么 | A 评分 | B 评分 | 依据 |', sheet)
            self.assertNotIn('profile', sheet)


if __name__ == '__main__':
    unittest.main()
