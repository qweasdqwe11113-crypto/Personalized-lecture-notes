"""Semantic contracts, editable layout families, bounded repair and v1 isolation."""
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_presentations import fixture_context, fixture_deck
from presentations import parse_deck, build_pptx, prepare_slides
from ppt_templates import template_record
from ppt_story import DESIGN_VERSION, visual_texts
from pptkit_export import classroom_layouts, check
from pptx import Presentation
from qwen import ModelError


def story_context():
    ctx = fixture_context()
    record = template_record('teaching-blue')
    ctx['ppt_export'].update(export_version=DESIGN_VERSION, template_id=record['id'],
        template_sha256=record['sha256'], template_mapping_version=record['mapping_version'], slide_count=10)
    return ctx


def story_deck():
    visuals = [
        dict(type='cover', subtitle='从等式性质理解变形的依据', goals=['保持等式两边相等', '求解后独立检验']),
        dict(type='definition', term='等式的性质', definition='等式两边同时加上或减去同一个数，等式仍然成立。',
             formula='a = b ⇒ a − c = b − c', conditions=['两边必须进行相同运算', '同除时除数必须非零']),
        dict(type='steps', problem='解方程 3x − 4 = 11，并检验。', steps=[
            dict(expression='3x − 4 + 4 = 11 + 4', reason='等式两边同时加4'),
            dict(expression='3x = 15；x = 5', reason='等式两边同时除以3')], check='3 × 5 − 4 = 11，满足原方程。'),
        dict(type='comparison', left=dict(title='保持相等', items=['两边同时减6', '等式两边仍然相等']),
             right=dict(title='破坏相等', items=['只在左边减6', '变形后不再等价'])),
        dict(type='table', headers=['原方程', '相同运算', '结果'], rows=[['x + 6 = 14', '两边同时减6', 'x = 8'],
             ['4x = 20', '两边同时除以4', 'x = 5'], ['3x − 4 = 11', '先加4，再除以3', 'x = 5']]),
        dict(type='process', nodes=[dict(title='观察方程', detail='确认未知数与已知条件'),
             dict(title='等价变形', detail='两边同时做相同运算'), dict(title='独立检验', detail='代入原方程核对两边')]),
        dict(type='metrics', items=[dict(value='2', unit='步', label='测试用步骤数', context='固定测试数据，不是教学效果统计'),
             dict(value='1', unit='次', label='测试用检验次数', context='用于验证数字版式，不是实际学习记录')]),
        dict(type='practice', problem='解方程 2x + 5 = 17。说明每一步的变形依据，并代入原方程检验。',
             prompts=['写出完整过程', '说明两边的相同运算', '独立完成检验']),
        dict(type='explain', lead='求解与检验承担不同任务。', items=['求解用来寻找未知数', '检验用来核对是否满足原方程']),
        dict(type='recap', items=[dict(title='保持相等', detail='两边同时进行相同运算'),
             dict(title='说明依据', detail='变形不能只记成“搬过去变号”'), dict(title='代入检验', detail='将结果代回原方程')]),
    ]
    titles = ['一元一次方程', '等式的性质与成立条件', '例题：逐步求解与检验', '两种变形的差别', '三道方程的对照',
              '从观察到检验', '数字版式隔离测试', '独立练习：两步方程', '求解与检验的分工', '本课方法回顾']
    deck = fixture_deck(10)
    for i, slide in enumerate(deck['slides']):
        slide.update(title=titles[i], question='', kind='cover' if i == 0 else 'summary' if i == 9 else 'example' if i == 2 else 'question' if i == 7 else 'concept', visual=visuals[i])
    return deck


def all_visible(prs):
    result = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                result.append(shape.text)
            if shape.has_table:
                result.extend(cell.text for row in shape.table.rows for cell in row.cells)
    return '\n'.join(result)


class SemanticPresentationTests(unittest.TestCase):
    def test_ten_layouts_render_and_preserve_every_semantic_string(self):
        ctx = story_context()
        deck = parse_deck(json.dumps(story_deck(), ensure_ascii=False), ctx)
        payload = build_pptx(deck, ctx['ppt_export'], ctx)
        prs = Presentation(BytesIO(payload))
        visible = all_visible(prs)
        self.assertEqual(len(prs.slides), 10)
        for page in deck['slides']:
            for text in visual_texts(page['visual']):
                self.assertIn(text, visible)
        self.assertTrue(any(shape.has_table for page in prs.slides for shape in page.shapes))
        self.assertFalse([message for level, message in check(BytesIO(payload)) if level == 'error'])
        self.assertNotIn('预期回答', visible)
        for page, slide in zip(deck['slides'], prs.slides):
            self.assertIn(page['notes'], slide.notes_slide.notes_text_frame.text)
            self.assertIn('页面结构：' + page['visual']['type'], slide.notes_slide.notes_text_frame.text)

    def test_bad_semantics_missing_fields_invalid_cites_and_answer_fields_rejected(self):
        examples = []
        for visual in [None, [], dict(type='unknown'), dict(type='steps', problem='题目', steps=[]),
                       dict(type='definition', term='概念', definition='正文\n换行', formula='', conditions=[])]:
            bad = story_deck(); bad['slides'][2]['visual'] = visual; examples.append(bad)
        bad = story_deck(); bad['slides'][4]['visual']['rows'][0].pop(); examples.append(bad)
        bad = story_deck(); bad['slides'][7]['visual'] = bad['slides'][2]['visual']; examples.append(bad)
        bad = story_deck(); bad['slides'][1]['visual']['formula'] = '错误来源 [M999]'; examples.append(bad)
        bad = story_deck(); bad['slides'][7]['visual']['answer'] = 'x=6'; examples.append(bad)
        bad = story_deck(); bad['slides'][2]['visual']['steps'][0]['answer'] = 'x=6'; examples.append(bad)
        bad = story_deck(); bad['slides'][3]['visual']['left']['extra'] = '不能静默丢弃'; examples.append(bad)
        for deck in examples:
            with self.subTest(deck=deck['slides'][2]['visual']), self.assertRaises(ModelError):
                parse_deck(json.dumps(deck, ensure_ascii=False), story_context())

    def test_one_layout_repair_preserves_content_and_reports_actual_repair(self):
        ctx = story_context()
        deck = parse_deck(json.dumps(story_deck(), ensure_ascii=False), ctx)
        original = classroom_layouts.render
        used = []
        def fail_once(*args, **kwargs):
            if not used:
                used.append(True)
                raise classroom_layouts.LayoutOverflow('测试需要重新布局')
            return original(*args, **kwargs)
        with patch.object(classroom_layouts, 'render', side_effect=fail_once):
            prs = Presentation(BytesIO(build_pptx(deck, ctx['ppt_export'], ctx)))
        self.assertIn('自动布局修正：1 次', prs.slides[0].notes_slide.notes_text_frame.text)
        self.assertIn('实际版式：reading', prs.slides[0].notes_slide.notes_text_frame.text)
        for text in visual_texts(deck['slides'][0]['visual']):
            self.assertIn(text, all_visible(prs))

    def test_dense_chinese_contracts_preserve_text_and_native_table(self):
        deck = story_deck()
        # Exercise maximum Chinese widths, not just short mathematical expressions.
        for page in deck['slides']:
            visual = page['visual']
            kind = visual['type']
            if kind == 'definition':
                visual.update(term='概' * 32, definition='定' * 100, formula='式' * 80, conditions=['条' * 48] * 3)
            elif kind == 'steps':
                visual.update(problem='题' * 80, check='验' * 80,
                              steps=[dict(expression='式' * 56, reason='据' * 34)] * 4)
            elif kind == 'table':
                visual.update(headers=['列' * 18] * 4, rows=[['格' * 28] * 4] * 4)
            elif kind == 'process':
                visual['nodes'] = [dict(title='步' * 24, detail='说' * 64)] * 5
            elif kind == 'practice':
                visual.update(problem='题' * 160, prompts=['求' * 48] * 3)
            elif kind == 'recap':
                visual['items'] = [dict(title='法' * 24, detail='述' * 64)] * 4
        ctx = story_context()
        parsed = parse_deck(json.dumps(deck, ensure_ascii=False), ctx)
        prs = Presentation(BytesIO(build_pptx(parsed, ctx['ppt_export'], ctx)))
        for page, slide in zip(parsed['slides'], prs.slides):
            for value in visual_texts(page['visual']):
                self.assertIn(value, all_visible(SimpleNamespace(slides=[slide])))
        self.assertTrue(any(shape.has_table for shape in prs.slides[4].shapes))

    def test_table_repair_stays_native_and_two_failed_attempts_are_rejected(self):
        ctx = story_context()
        deck = parse_deck(json.dumps(story_deck(), ensure_ascii=False), ctx)
        original = classroom_layouts.render
        def fail_table(*args, **kwargs):
            if args[3]['type'] == 'table' and not kwargs.get('repair'):
                raise classroom_layouts.LayoutOverflow('表格需要修正')
            return original(*args, **kwargs)
        with patch.object(classroom_layouts, 'render', side_effect=fail_table):
            prs = Presentation(BytesIO(build_pptx(deck, ctx['ppt_export'], ctx)))
        self.assertTrue(any(shape.has_table for shape in prs.slides[4].shapes))
        self.assertIn('实际版式：table', prs.slides[4].notes_slide.notes_text_frame.text)
        with patch.object(classroom_layouts, 'render', side_effect=classroom_layouts.LayoutOverflow('仍放不下')) as render:
            with self.assertRaisesRegex(ModelError, '两次排版仍放不下'):
                build_pptx(deck, ctx['ppt_export'], ctx)
            self.assertEqual(render.call_count, 2)

    def test_prompt_and_renderer_versions_are_frozen_by_selected_design(self):
        self.assertEqual(prepare_slides(story_context(), 'test-model').prompt_version, 'lecture-ppt-v3')
        self.assertEqual(prepare_slides(fixture_context(), 'test-model').prompt_version, 'lecture-ppt-v2')
