"""Classroom adapter for the project-local ppt-kit layout engine.

The validated classroom contract remains the source of truth. Do not run the
kit's business-story quotas or let model content control geometry or paths.
"""
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
import re
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Pt

from pptx_export import source_notes
from ppt_templates import template_record, MAPPING_VERSION

KIT_PATH = Path(__file__).resolve().parents[1] / 'tools' / 'ppt-kit' / 'pptkit'
KIT_NAME = '_lecture_pptkit'
if KIT_NAME not in sys.modules:
    spec = spec_from_file_location(KIT_NAME, KIT_PATH / '__init__.py',
                                  submodule_search_locations=[str(KIT_PATH)])
    kit = module_from_spec(spec)
    sys.modules[KIT_NAME] = kit
    spec.loader.exec_module(kit)

from _lecture_pptkit.layouts import cover_split, split_title_body
from _lecture_pptkit.theme import (CANVAS_W, CANVAS_H, Theme, add_text,
                                  blank_slide, set_slide_bg)
from _lecture_pptkit.check import check

EXPORT_VERSION = 'pptkit-classroom-v1'
KINDS = dict(cover='课程导入', concept='概念讲解', example='例题讲解',
             question='课堂练习', summary='本课回顾')


def plain(value):
    # Source snapshots may contain characters forbidden in XML 1.0.
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', str(value))


def fit_template_shapes(slide, width, height, region):
    """Uniformly fit toolkit geometry into a native template's safe region."""
    x, y, w, h = region
    scale = min(width * w / CANVAS_W, height * h / CANVAS_H)
    ox = width * x + (width * w - CANVAS_W * scale) / 2
    oy = height * y + (height * h - CANVAS_H * scale) / 2
    for shape in slide.shapes:
        shape.left = Pt(ox + shape.left / 12700 * scale)
        shape.top = Pt(oy + shape.top / 12700 * scale)
        shape.width = Pt(shape.width / 12700 * scale)
        shape.height = Pt(shape.height / 12700 * scale)
        if shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if run.font.size is not None:
                        run.font.size = Pt(run.font.size.pt * scale)


def build_pptx(deck, source, context):
    template = None
    if source.get('template_id'):
        try:
            template = template_record(source['template_id'])
            if (source.get('template_sha256') != template['sha256']
                    or source.get('template_mapping_version') != MAPPING_VERSION):
                raise ValueError('模板版本与任务提交时不一致，请核对模板资产。')
        except ValueError as exc:
            from reviews import ReviewError
            raise ReviewError(str(exc), 409) from exc
    prs = Presentation(template['path']) if template else Presentation()
    if template:
        # Remove sample slides and their relationships, preserving all masters,
        # artwork, themes and media from the actual selected PowerPoint file.
        for entry in list(prs.slides._sldIdLst):
            prs.part.drop_rel(entry.rId)
            prs.slides._sldIdLst.remove(entry)
    else:
        prs.slide_width, prs.slide_height = Pt(CANVAS_W), Pt(CANVAS_H)
    prs.core_properties.title = plain(deck['title'])
    dark = template and template['profile'].get('dark')
    theme = Theme.from_dict(dict(primary='#FFFFFF', accent='#B7E8FF', text='#FFFFFF',
                                 muted='#F2F6FA', hint='#D1E0E9') if dark else None)
    revision = f'R{source["base_revision"]}' if source['base_revision'] else '原稿'
    for index, content in enumerate(deck['slides'], 1):
        if template:
            master = template['profile']['masters'][0 if index == 1 else 1]
            slide = prs.slides.add_slide(prs.slide_masters[master].slide_layouts[6])
            for shape in list(slide.shapes):
                if shape.is_placeholder:
                    shape.element.getparent().remove(shape.element)
        else:
            slide = blank_slide(prs)
        slide.name = plain(content['title'])
        if not template:
            set_slide_bg(slide, RGBColor(255, 255, 255))
        spec = dict(title=plain(content['title']), eyebrow=KINDS[content['kind']],
                    bullets=[plain(b) for b in content['bullets']],
                    body=[plain(b) for b in content['bullets']])
        if content['kind'] == 'cover':
            cover_split(slide, spec, theme, body_size=26, decorate=not template)
        else:
            if content['question']:
                spec['subtitle'] = '课堂提问：' + plain(content['question'])
            split_title_body(slide, spec, theme, body_size=26,
                             body_spacing=1.35, subtitle_size=22)
        add_text(slide, 76, 650, 930, 24,
                 f'讲义 #{source["lecture_id"]} · {revision} · AI 改编稿，待教师核查',
                 size=14, color=theme.muted, theme=theme, line_spacing=1.2)
        add_text(slide, 1040, 650, 164, 24, f'{index:02d} / {len(deck["slides"]):02d}',
                 size=14, color=theme.muted, theme=theme, align='right', line_spacing=1.2)
        if template:
            profile = template['profile']
            region = profile.get('cover_region', profile['region']) if index == 1 else profile['region']
            fit_template_shapes(slide, prs.slide_width / 12700, prs.slide_height / 12700, region)
        notes = (content['notes'] + '\n\n本页材料依据（仅编号有效性经过程序检查，支持关系需人工核对）：\n'
                 + source_notes(content, context)
                 + f'\n\n讲义 #{source["lecture_id"]} / {revision}；源版本保存时状态：{source["source_status"]}'
                 + '\n课件为独立 AI 改编稿，源版本审核状态不代表课件已审核。'
                 + f'\n源正文 SHA-256：{source["content_sha256"]}'
                 + f'\n生成记录：{source.get("run_id", "见网页生成记录")}'
                 + f'\n渲染器：{EXPORT_VERSION}')
        if template:
            notes += (f'\n模板：{template["name"]} / {template["id"]}'
                      + f'\n模板 SHA-256：{template["sha256"]}\n母版映射：{MAPPING_VERSION}')
        slide.notes_slide.notes_text_frame.text = plain(notes)
    output = BytesIO()
    prs.save(output)
    # Geometry errors fail the queued run before it is advertised as usable.
    output.seek(0)
    errors = [message for level, message in check(output) if level == 'error']
    if errors:
        from qwen import ModelError
        raise ModelError('invalid_ppt', '课件排版检查未通过：' + '；'.join(errors), 'output')
    return output.getvalue()
