"""Slides laid out from a lesson design: formulas as editable text, page planning, every template,
and the run sheet's page numbers. No database and no model call; the routes and the package are
tested in test_lesson_design.DesignedPipeline.
"""
import copy
import unittest
from io import BytesIO
from zipfile import ZipFile

from pptx import Presentation

from test_lesson_design import CONTEXT, with_notes   # first: it puts demo/ on the import path
import design as D
import handouts
import lesson_slides as LS
import mathtext
import packages
from ppt_templates import available_templates
from pptkit_export import LESSON_TEMPLATE_ERROR, build_lesson_pptx


def lesson():
    return D.validate(with_notes(), CONTEXT)[0]


def texts(slide):
    return '\n'.join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame)


class FormulaText(unittest.TestCase):
    def test_latex_becomes_text_with_real_scripts_and_math_spacing(self):
        runs = mathtext.runs('$P(A_1\\mid B) = \\frac{0.012}{0.032}$')
        self.assertEqual(''.join(run.text for run in runs), 'P(A1 | B) = 0.012/0.032')
        self.assertIn(('1', 'sub'), [(run.text, run.script) for run in runs])
        self.assertTrue(all(run.math for run in runs))
        self.assertEqual({run.text: run.italic for run in runs if run.text in ('P', 'A', 'B')}, {'P': True, 'A': True, 'B': True})
        self.assertFalse(any(run.italic for run in runs if any(c.isdigit() for c in run.text)))
        self.assertEqual(mathtext.plain('$P(A_1\\mid B) = \\frac{0.012}{0.032}$'), 'P(A₁ | B) = 0.012/0.032')
        self.assertEqual(mathtext.plain('$x^2 - 1 = 0$'), 'x² − 1 = 0')
        self.assertEqual(mathtext.plain('$\\binom{5}{2}p^2(1-p)^3$'), 'C₅²p²(1 − p)³')
        self.assertEqual(mathtext.plain('两条产线 $A_1$、$A_2$'), '两条产线 A₁、A₂')

    def test_gaps_are_blank_until_answers_fill_them_in_order(self):
        blank = mathtext.runs('$P(D) = ____$ 和 ____')
        self.assertEqual([run.mark for run in blank if run.mark], ['blank', 'blank'])
        self.assertTrue(all(run.text == mathtext.BLANK for run in blank if run.mark))
        filled = mathtext.runs('$P(D) = ____$ 和 ____', ['0.032', '全概率'])
        self.assertEqual([run.text for run in filled if run.mark == 'answer'], ['0.032', '全概率'])
        self.assertEqual(mathtext.plain('后验 = 先验 × 似然 ÷ ____'), '后验 = 先验 × 似然 ÷ ____')
        self.assertEqual(mathtext.plain('后验 = 先验 × 似然 ÷ ____', ['全概率']), '后验 = 先验 × 似然 ÷ 【全概率】')

    def test_a_gap_inside_a_formula_reaches_the_word_handout_as_a_line(self):
        self.assertEqual(D.open_gaps('若 $P(D) = ____$，则 ____'), '若 $P(D) = \\gap$，则 ____')
        self.assertEqual(D.open_gaps('$____ x + ____$'), '$\\gap x + \\gap$')
        self.assertEqual(D.filled({'text': '$P(D) = ____$ 且 ____', 'answer': '0.032；全概率'}),
                         '$P(D) = \\text{【}\\text{0.032}\\text{】}$ 且 【全概率】')
        reply = with_notes()
        reply['segments'][0]['notes'].append({'text': '若 $P(D) = ____$，则分母不能为 ____', 'answer': '0.032；0'})
        design = D.validate(reply, CONTEXT)[0]
        sheet = handouts.student_handout(design)
        self.assertIn('- 若 $P(D) = \\gap$，则分母不能为 ____', sheet)
        xml = ZipFile(BytesIO(packages.markdown_docx(sheet))).read('word/document.xml').decode()
        self.assertIn('＿＿＿＿', xml)           # drawn inside the Word formula
        self.assertNotIn('\\gap', xml)          # never the source
        key = handouts.student_handout(design, answers=True)
        self.assertIn('- 若 $P(D) = \\text{【}\\text{0.032}\\text{】}$，则分母不能为 【0】', key)


class SlidePlan(unittest.TestCase):
    def setUp(self):
        self.design = lesson()
        self.deck = LS.plan(self.design, label='隔离课堂设计', lines=['大学 · 概率论'])
        self.pages = LS.outline(self.deck)

    def test_pages_follow_the_class_order_and_the_last_page_is_hidden(self):
        self.assertEqual(self.deck['version'], 'lesson-slides-v1')
        self.assertEqual(self.deck['pages'], {'S1': [2, 3], 'S2': [4, 8], 'S3': [9, 11], 'homework': [12, 12]})
        self.assertEqual((self.deck['visible'], len(self.deck['slides'])), (12, 13))
        self.assertEqual([page['layout'] for page in self.pages],
                         ['cover', 'points', 'points', 'problems', 'figure', 'steps', 'problems', 'steps',
                          'problems', 'problems', 'answers', 'homework', 'notice'])
        self.assertEqual([page['hidden'] for page in self.pages], [False] * 12 + [True])
        self.assertEqual((LS.page_range(self.deck, 'S1'), LS.page_range(self.deck, 'homework'), LS.page_range(self.deck, 'S9')),
                         ('第 2—3 页', '第 12 页', ''))
        cover = self.pages[0]
        self.assertEqual(cover['body'], ['能用贝叶斯公式求后验', '能说明先验变化的影响'])
        for shown in ('重点：贝叶斯公式的推导', '难点：区分两个方向的条件概率', '学情与对策：'):
            self.assertIn(shown, cover['notes'])

    def test_slides_quote_the_handout_problems_and_keep_answers_for_later_pages(self):
        shown = '\n'.join(line for page in self.pages for line in page['body'])
        for item in self.design['items']:
            self.assertIn(mathtext.plain(handouts._clean(item['stem'])), shown, item['id'])
        notes, filled = self.pages[1], self.pages[2]
        self.assertIn('后验 = 先验 × 似然 ÷ ____', notes['body'])
        self.assertIn('后验 = 先验 × 似然 ÷ 【全概率】', filled['body'])
        self.assertEqual(filled['title'], '贝叶斯公式（填空答案）')
        self.assertIn('想一想：分母是什么？', notes['body'])
        example, practice = self.pages[3], self.pages[8]
        self.assertEqual(example['body'], ['两条产线 A₁、A₂ 的产量占比为 0.6、0.4，次品率为 0.02、0.05。抽到一件次品，求它来自 A₁ 的概率。'])
        for page in (example, practice, self.pages[9]):
            for answer in ('0.375', '7/15', '变小'):
                self.assertNotIn(answer, '\n'.join(page['body']))
        self.assertIn('练1 答案：7/15', practice['notes'])           # the teacher sees it in the notes
        self.assertEqual(self.pages[10]['body'], ['练1　7/15', '练2　0.2', '练3　变小'])
        self.assertIn('答案：0.375', self.pages[5]['body'])
        everything = shown + '\n'.join(page['title'] + page['notes'] for page in self.pages)
        for hidden in ('$', '\\frac', '\\mid', '[M13]', 'AI 改编稿', 'S1', 'E1'):
            self.assertNotIn(hidden, everything)

    def test_every_template_draws_the_deck_and_passes_the_layout_check(self):
        semantic = [item['id'] for item in available_templates() if item['semantic']]
        self.assertGreaterEqual(len(semantic), 9)
        long = with_notes()
        long['items'][0]['steps'] = [{'do': f'$P(D) = 0.6 \\times 0.02 + 0.4 \\times 0.05 + {n} \\times 0.001 = 0.03{n}$',
                                      'why': f'第 {n} 步：把每条产线的贡献逐项加起来，不能漏掉任何一项'} for n in range(1, 9)]
        long['items'] += [dict(copy.deepcopy(long['items'][2]), id=f'P{n}', title='',
                               stem='某校三个年级人数之比为 3:3:4，各年级近视率依次为 0.30、0.42、0.55。随机抽一名学生发现近视，'
                                    f'求他来自三年级的概率，并说明先验改变时后验怎样变化（第 {n} 组数据）。')
                          for n in range(4, 10)]
        long['segments'][2]['items'] += [f'P{n}' for n in range(4, 10)]
        long['segments'][2]['minutes'] = 9
        crowded = LS.plan(D.validate(long, CONTEXT)[0], label='很长的一节课')
        self.assertGreater(crowded['visible'], self.deck['visible'])
        for deck in (self.deck, crowded):
            for template in semantic:
                prs = Presentation(BytesIO(build_lesson_pptx(deck, template, ['依据：测试讲义'])))
                slides = list(prs.slides)
                self.assertEqual(len(slides), len(deck['slides']), template)
                self.assertEqual([slide._element.get('show') for slide in slides], [None] * (len(slides) - 1) + ['0'], template)
                self.assertIn('依据：测试讲义', texts(slides[-1]))
                drawn = '\n'.join(texts(slide) for slide in slides)
                for hidden in ('$', '\\frac', '\\times'):
                    self.assertNotIn(hidden, drawn, template)
                self.assertTrue(all(slide.notes_slide.notes_text_frame.text.strip() for slide in slides), template)
        with self.assertRaisesRegex(ValueError, LESSON_TEMPLATE_ERROR):
            build_lesson_pptx(self.deck, 'no-such-template')

    def test_run_sheet_names_the_slides_and_an_edited_sheet_gets_its_own_table(self):
        pages = {segment: LS.page_range(self.deck, segment) for segment in self.deck['pages']}
        self.assertNotIn('课件', D.schedule(self.design))
        sheet = D.schedule(self.design, pages)
        self.assertIn('| 时间 | 环节 | 教师 | 学生 | 题目 | 课件 |', sheet)
        self.assertIn('| 例题：双产线 | 板演例1；学生口述例2 | 独立完成 | 例1、例2 | 第 4—8 页 |', sheet)
        lesson_text = D.document(self.design, {'S1': '先问分母是什么。'}, CONTEXT)
        book = handouts.teacher_book(self.design, lesson_text, [('课程', '概率论')], pages)
        self.assertIn('第 4—8 页 |', book)
        self.assertNotIn('**课件页码**', book)
        self.assertEqual(handouts.teacher_book(self.design, lesson_text, []),
                         handouts.teacher_book(self.design, lesson_text, [], None))
        edited = lesson_text.replace(D.schedule(self.design), '老师改过的课堂流程')
        book = handouts.teacher_book(self.design, edited, [], pages)
        self.assertIn('老师改过的课堂流程', book)
        self.assertIn('**课件页码**\n\n| 环节 | 课件 |\n| --- | --- |\n| 新授：贝叶斯公式 | 第 2—3 页 |', book)


if __name__ == '__main__':
    unittest.main()
