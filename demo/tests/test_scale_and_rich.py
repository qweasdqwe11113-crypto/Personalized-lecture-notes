"""Scale corpus, arithmetic and figure checks, rich exports, and automatic repair of structured replies."""
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from docx import Document

import test_presentations as slides  # modules, not classes: importing a TestCase would rerun its tests here
import test_quizzes as quizzes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scale'))
import corpus
import eval_retrieval
import examples
import figures
import load
import mathcheck
import rendering
import richdoc
from embed import HashingEmbedder

module = quizzes.module
TREE = {'type': 'probability_tree', 'title': '概率树', 'root': '全部产品', 'branches': [
    {'label': 'A1', 'p': 0.6, 'children': [{'label': 'D', 'p': 0.02}, {'label': '非D', 'p': 0.98}]},
    {'label': 'A2', 'p': 0.4, 'children': [{'label': 'D', 'p': 0.05}, {'label': '非D', 'p': 0.95}]}]}


class ArithmeticTests(unittest.TestCase):
    def findings(self, text):
        return mathcheck.check_text(text)['findings']

    def test_numeric_chains_respect_shown_precision_and_report_real_disagreement(self):
        good = ('P(D) = 0.6 × 0.02 + 0.4 × 0.05 = 0.032\nP(A1|D) = 0.012 / 0.032 = 0.375，1/3 ≈ 0.333\n'
                '$\\frac{0.004}{0.044} \\approx 0.0909$，3.2% = 0.032，12/32 = 3/8\n1. 0.2 × 0.02 = 0.004')
        self.assertEqual(self.findings(good), [])
        self.assertGreaterEqual(mathcheck.check_text(good)['checked'], 6)
        wrong = self.findings('先算 0.6 × 0.02 + 0.4 × 0.05 = 0.034')
        self.assertEqual([(f['kind'], f['line']) for f in wrong], [('numeric', 1)])
        self.assertIn('0.032', wrong[0]['detail'])

    def test_stated_solution_is_substituted_into_the_equations_before_it_only(self):
        text = '3x + 5 = 20\n3x = 15\nx = 5\n\n2(x - 1) = 3x + 4\nx = 6\n2x = 6\nx = 3\n3x = 12\nx = 4'
        found = self.findings(text)
        self.assertEqual([(f['kind'], f['line']) for f in found], [('solution', 5)])
        self.assertIn('左边为 10，右边为 22', found[0]['detail'])

    def test_unreadable_or_non_arithmetic_text_is_skipped_not_guessed(self):
        text = '时间 = 路程 ÷ 速度\nP(A|B) = P(A∩B) / P(B)\n第 3 步：见上\n价格是 5 元 = 五元\nsin x = 1\n1 / 0 = 0'
        report = mathcheck.check_text(text)
        self.assertEqual((report['checked'], report['findings']), (0, []))
        self.assertEqual(mathcheck.evaluate('2(3+4)^2'), 98)
        with self.assertRaises(mathcheck.MathError):
            mathcheck.evaluate('__import__("os")')

    def test_generated_worked_examples_are_internally_consistent(self):
        for kind in examples.GENERATORS:
            text = examples.worked_examples(kind, 12, 3)
            report = mathcheck.check_text(text)
            self.assertEqual(report['findings'], [], kind)
            self.assertGreater(report['checked'], 0, kind)
            self.assertEqual(text, examples.worked_examples(kind, 12, 3))


class FigureTests(unittest.TestCase):
    def test_every_figure_type_renders_svg_and_validates_its_own_numbers(self):
        specs = [TREE,
                 {'type': 'number_line', 'min': -2, 'max': 8, 'points': [{'x': 5, 'label': 'x = 5'}]},
                 {'type': 'balance', 'steps': [{'left': '3x + 5', 'right': '20'}, {'left': 'x', 'right': '5'}], 'solution': {'x': 5}},
                 {'type': 'function_plot', 'x': [-4, 4], 'functions': [{'expr': '2x+1'}], 'points': [{'x': 1, 'y': 3}]},
                 {'type': 'bar_chart', 'bars': [{'label': '先验', 'value': 0.6}, {'label': '后验', 'value': 0.375}]}]
        for spec in specs:
            result = figures.render(json.dumps(spec, ensure_ascii=False))
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['svg'].startswith('<svg') and result['svg'].endswith('</svg>'))
            self.assertEqual(result['warnings'], [])
        self.assertIn('联合概率 0.012', figures.render(json.dumps(TREE, ensure_ascii=False))['svg'])

    def test_inconsistent_or_malformed_figures_are_reported_never_raised(self):
        bad_tree = {**TREE, 'branches': [{'label': 'A', 'p': 0.7}, {'label': 'B', 'p': 0.4}]}
        self.assertIn('不等于 1', figures.render(json.dumps(bad_tree))['warnings'][0])
        bad_steps = {'type': 'balance', 'steps': [{'left': '3x + 5', 'right': '20'}, {'left': '3x', 'right': '14'}], 'solution': {'x': 5}}
        self.assertIn('两边不相等', figures.render(json.dumps(bad_steps))['warnings'][0])
        for source in ['{"type":"pie"}', 'not json', '[]', '{"type":"number_line","min":3,"max":1}',
                       '{"type":"function_plot","x":[0,1],"functions":[{"expr":"__import__(1)"}]}',
                       '{"type":"bar_chart","bars":[{"label":"<script>","value":"1"}]}', 'x' * 5000]:
            result = figures.render(source)
            self.assertFalse(result['ok'], source[:40])
            self.assertTrue(result['error'])
        escaped = figures.render(json.dumps({'type': 'bar_chart', 'bars': [{'label': '<b>&', 'value': 1}, {'label': 'x', 'value': 2}]}))
        self.assertIn('&lt;b&gt;&amp;', escaped['svg'])
        self.assertNotIn('<b>', escaped['svg'])
        content = '前文\n```figure\n' + json.dumps(bad_tree) + '\n```\n```figure\n{"type":"pie"}\n```\n'
        self.assertEqual([p['line'] for p in figures.check(content)['problems']], [2, 5])


class RichExportTests(unittest.TestCase):
    CONTENT = ('# 标题\n## 核心讲解\n由 $P(A_1\\mid D)=\\frac{0.012}{0.032}$ 得 **后验** [M1]，花费 $5 和 $8。\n$$\nP(D)=\\sum_{i=1}^{2} P(A_i)\\,P(D\\mid A_i)+\\unknown\n$$\n'
               '| 产线 | 先验 |\n|---|---|\n| A1 | $0.6$ |\n```figure\n' + json.dumps(TREE, ensure_ascii=False) + '\n```\n- 列表 $x^2$\n<script>alert(1)</script>\n')

    def test_html_keeps_formula_source_embeds_figures_and_stays_script_free(self):
        text = rendering.markdown_html(self.CONTENT)
        self.assertIn('<span class="math-source">$P(A_1\\mid D)=\\frac{0.012}{0.032}$</span>', text)
        self.assertIn('花费 $5 和 $8', text)
        self.assertIn('<div class="math-display">P(D)=', text)
        self.assertIn('<table><tr><th>产线</th><th>先验</th></tr>', text)
        self.assertIn('<figure><img alt="概率树', text)
        self.assertIn('data:image/svg+xml;base64,', text)
        self.assertNotIn('<script', text)
        self.assertIn('&lt;script&gt;', text)

    def test_word_gets_native_formulas_a_table_and_a_picture_or_words(self):
        document = Document()
        for kind, value in richdoc.blocks(self.CONTENT):
            if kind == 'figure':
                richdoc.add_figure(document, value)
            elif kind == 'math':
                richdoc.add_display_math(document, value)
            elif kind == 'table':
                richdoc.add_table(document, value)
            elif value.strip():
                richdoc.add_inline(document.add_paragraph(), value.strip())
        buffer = BytesIO()
        document.save(buffer)
        saved = Document(BytesIO(buffer.getvalue()))
        xml = saved.element.xml
        self.assertEqual(xml.count('<m:oMathPara'), 1)
        self.assertGreaterEqual(xml.count('<m:f>'), 1)
        self.assertIn('<m:sSubSup>', xml)
        self.assertIn('\\unknown', xml)  # an unknown command stays visible instead of vanishing
        self.assertEqual(len(saved.tables), 1)
        text = '\n'.join(p.text for p in saved.paragraphs)
        self.assertIn('花费 $5 和 $8', text)
        self.assertTrue(len(saved.inline_shapes) == 1 or '【图：概率树】' in text)
        self.assertIn('【图：概率树】A1（0.6）', richdoc.plain_text(self.CONTENT))


class ScaleDataTests(quizzes.ProfileDatabaseFixture):
    def test_install_is_idempotent_exercises_budget_and_retrieval_and_removes_cleanly(self):
        with self.fixture_db() as conn:
            before = {t: conn.execute(f'SELECT count(*) AS n FROM {t}').fetchone()['n']
                      for t in ('students', 'knowledge_points', 'materials', 'course_tasks', 'learning_evidence')}
            summary = load.install(conn, students=5, examples=8)
            again = load.install(conn, students=5, examples=8)
        self.assertEqual((summary['students'], len(summary['courses'])), (10, 2))
        self.assertTrue(all('skipped' in course for course in again['courses']))
        probability = next(c for c in summary['courses'] if c['key'] == 'scale_probability_v1')
        self.assertEqual((probability['knowledge_points'], probability['materials']), (12, 24))
        self.assertGreater(probability['characters'], 16000)
        with self.fixture_db() as conn:
            student = conn.execute("SELECT id FROM students WHERE subtitle LIKE '合成·大学%' ORDER BY id LIMIT 1").fetchone()['id']
        # A chapter-sized task goes through the material budget that the three-paragraph seed never reached.
        context = module.get_context(student, 'scale_probability_v1')
        budget = context['input_budget']
        self.assertGreater(budget['original_material_characters'], 16000)
        self.assertLessEqual(budget['material_characters'], 16000)
        self.assertTrue(any('selection' in material for material in context['materials']))
        self.assertLessEqual(budget['context_characters'], 56000)
        self.assertEqual(len(context['states']), 12)
        self.assertTrue({s['state'] for s in context['states']} & {'needs_support', 'partial', 'mastered'})
        # Vector retrieval now has to choose: far more chunks than the six it may return.
        with patch.object(module, 'EMBEDDER', HashingEmbedder(256)):
            ranked = module.get_context(student, 'scale_probability_v1', retrieval_mode='vector', goal='贝叶斯公式的分母怎么求')
        with self.fixture_db() as conn:
            chunks = conn.execute('SELECT count(*) AS n FROM material_chunks').fetchone()['n']
        self.assertGreater(chunks, 40)
        self.assertEqual(len(ranked['retrieval']['items']), 6)
        with self.fixture_db() as conn:
            removed = load.remove(conn)
            after = {t: conn.execute(f'SELECT count(*) AS n FROM {t}').fetchone()['n'] for t in before}
        self.assertEqual((removed['removed_students'], removed['removed_courses'], removed['kept']), (10, 2, []))
        self.assertEqual(after, before)

    def test_corpus_is_deterministic_and_retrieval_baseline_reports_all_rankers(self):
        first = corpus.load_course('linear_equations', 4, 1)
        self.assertEqual(first, corpus.load_course('linear_equations', 4, 1))
        self.assertEqual(len(first['points']), 12)
        self.assertTrue(all(len(point['queries']) == 2 and len(point['materials']) == 2 for point in first['points']))
        report = eval_retrieval.evaluate(HashingEmbedder(512), examples=4)
        self.assertEqual(len(report), 2)
        for course in report:
            self.assertEqual(set(course['rankers']), {'vector', 'bm25', 'hybrid_rrf'})
            self.assertEqual(course['queries'], 24)
            for values in course['rankers'].values():
                self.assertTrue(0 <= values['recall@1'] <= values['recall@3'] <= values['recall@6'] <= 1)
            self.assertGreater(course['rankers']['bm25']['recall@6'], 0.7)


class QuizRepairTests(quizzes.QuizFixture):
    def test_invalid_json_gets_one_second_attempt_that_names_the_problem(self):
        seen = []

        def model(prepared, attempted):
            attempted()
            payload = json.loads(prepared.body)
            seen.append(payload['messages'])
            if len(seen) == 1:
                return module.ModelResult('{"questions": [ 这不是有效 JSON', {'total_tokens': 5})
            context = json.loads(payload['messages'][1]['content'])['context']
            return module.ModelResult(json.dumps(quizzes.fixture_result(context), ensure_ascii=False), {'total_tokens': 9})
        with patch.object(module, 'call_qwen', model):
            response = self.client.post('/api/quizzes', json=self.payload())
            self.assertTrue(self.queue.wait_idle(5))
        result = self.client.get('/api/quizzes/' + response.json['id']).json
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(seen), 2)
        self.assertEqual([m['role'] for m in seen[1]], ['system', 'user', 'assistant', 'user'])
        self.assertIn('没有通过程序检查', seen[1][3]['content'])
        self.assertEqual(result['usage']['total_tokens'], 14)
        self.assertEqual(result['usage']['repair']['policy'], 'retry-once-v1')
        self.assertTrue(result['integrity_valid'])  # the frozen first request is untouched

    def test_second_invalid_reply_fails_and_old_requests_without_the_policy_are_not_retried(self):
        calls = []

        def broken(prepared, attempted):
            attempted()
            calls.append(prepared.body)
            return module.ModelResult('仍然不是 JSON')
        with patch.object(module, 'call_qwen', broken):
            response = self.client.post('/api/quizzes', json=self.payload())
            self.assertTrue(self.queue.wait_idle(5))
        failed = self.client.get('/api/quizzes/' + response.json['id']).json
        self.assertEqual((failed['status'], len(calls)), ('failed', 2))
        with self.fixture_db() as conn:
            conn.execute("UPDATE quiz_jobs SET context = context - 'auto_repair' WHERE id=%s", (response.json['id'],))
        calls.clear()
        with patch.object(module, 'call_qwen', broken):
            retry = self.client.post(f"/api/quizzes/{response.json['id']}/retry")
            self.assertEqual(retry.status_code, 202, retry.json)
            self.assertTrue(self.queue.wait_idle(5))
        self.assertEqual(len(calls), 1)


class SlideRepairTests(slides.PresentationIntegrationTests):
    def test_invalid_deck_gets_one_second_attempt_and_merged_usage(self):
        bad = slides.fixture_deck()
        bad['slides'][1]['sources'] = ['M999']
        replies = [bad, slides.fixture_deck()]
        seen = []

        def model(prepared, attempted):
            attempted()
            seen.append(json.loads(prepared.body)['messages'])
            return module.ModelResult(json.dumps(replies[len(seen) - 1], ensure_ascii=False), {'total_tokens': 15})
        with patch.object(module, 'call_qwen', model):
            response = self.client.post(f'/api/lectures/{self.lecture_id}/presentations', json=dict(base_revision=0, slide_count=6))
            self.assertEqual(response.status_code, 202, response.json)
            self.assertTrue(self.queue.wait_idle(10))
        job = self.client.get('/api/jobs/' + response.json['id']).json
        self.assertEqual(job['status'], 'succeeded', job)
        self.assertEqual(len(seen), 2)
        self.assertIn('M999', seen[1][2]['content'])
        self.assertEqual(job['usage']['total_tokens'], 30)
        self.assertEqual(job['usage']['repair']['policy'], 'retry-once-v1')


# The inherited slide contracts already run in their own module. Nothing class-valued may be
# left at module level, or unittest would collect it again from here.
for _name in [n for n in dir(slides.PresentationIntegrationTests) if n.startswith('test_')]:
    setattr(SlideRepairTests, _name, None)
del _name
