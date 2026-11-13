# -*- coding: utf-8 -*-
"""主题系统：画布、色板、字号阶梯、中文排版原语。

所有几何数值以 pt 为单位，画布 1280x720。
版式函数只调用本模块的 add_text / add_rect / add_line，不直接碰 python-pptx。
"""
import math
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from lxml import etree
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Pt

# ---------------------------------------------------------------- 画布常量

CANVAS_W = 1280
CANVAS_H = 720
MARGIN_L = 76
MARGIN_R = 76
CONTENT_W = CANVAS_W - MARGIN_L - MARGIN_R   # 1128
CONTENT_X = MARGIN_L
CONTENT_R = CANVAS_W - MARGIN_R              # 1204
ZONE_A_TOP = 64                               # 标题区
ZONE_B_TOP = 176                              # 内容区
ZONE_B_BOTTOM = 560
FOOT_LINE_Y = 612                             # 页脚横线
FOOT_TEXT_Y = 640

# 字号阶梯，比例 1.3-1.5
FS = {
    "display": 76,
    "num": 72,
    "h1": 40,
    "h2": 28,
    "h3": 22,
    "body": 17,
    "small": 14,
    "eyebrow": 13,
    "caption": 12,
}

DEFAULT_THEME = {
    "primary": "#0E3F8C",
    "accent": "#3D7BD9",
    "text": "#1A2230",
    "muted": "#4A5568",
    "hint": "#8B97A8",
    "bg_sub": "#F7F9FC",
    "line": "#D8E2F0",
    "font_cn": "Microsoft YaHei",
    "font_en": "Microsoft YaHei",
}


def hex2rgb(s: str) -> RGBColor:
    s = s.strip().lstrip("#")
    return RGBColor(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


@dataclass
class Theme:
    primary: RGBColor
    accent: RGBColor
    text: RGBColor
    muted: RGBColor
    hint: RGBColor
    bg_sub: RGBColor
    line: RGBColor
    font_cn: str = "Microsoft YaHei"
    font_en: str = "Microsoft YaHei"
    surface: RGBColor = field(default_factory=lambda: RGBColor(255, 255, 255))
    on_primary: RGBColor = field(default_factory=lambda: RGBColor(255, 255, 255))
    # 浅色系：由 primary / accent 与白色混合得到，避免手工维护
    primary_pale: RGBColor = field(init=False)
    accent_pale: RGBColor = field(init=False)

    def __post_init__(self):
        self.primary_pale = self._mix(self.primary, RGBColor(0xFF, 0xFF, 0xFF), 0.90)
        self.accent_pale = self._mix(self.accent, RGBColor(0xFF, 0xFF, 0xFF), 0.86)

    @staticmethod
    def _mix(a: RGBColor, b: RGBColor, ratio: float) -> RGBColor:
        """ratio = b 的占比"""
        return RGBColor(
            int(a[0] * (1 - ratio) + b[0] * ratio),
            int(a[1] * (1 - ratio) + b[1] * ratio),
            int(a[2] * (1 - ratio) + b[2] * ratio),
        )

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Theme":
        d = {**DEFAULT_THEME, **(d or {})}
        kw = {}
        for key in ("primary", "accent", "text", "muted", "hint", "bg_sub", "line"):
            kw[key] = hex2rgb(d[key])
        for key in ('surface', 'on_primary'):
            kw[key] = hex2rgb(d.get(key, '#FFFFFF'))
        kw["font_cn"] = d.get("font_cn") or DEFAULT_THEME["font_cn"]
        kw["font_en"] = d.get("font_en") or DEFAULT_THEME["font_en"]
        return cls(**kw)


# ---------------------------------------------------------------- 字体工具

def _set_ea_font(run, typeface: str) -> None:
    """python-pptx 只设 a:latin，中文会掉回宋体。这里补 a:ea。"""
    rPr = run.font._rPr
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rPr, qn(tag))
        el.set("typeface", typeface)


# ---------------------------------------------------------------- 排版估算

_CN_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def text_width(text: str, size: float) -> float:
    """估算单行宽度(pt)：中文按 1 个字号宽，西文按 0.56。"""
    if not text:
        return 0.0
    cn = len(_CN_RE.findall(text))
    return cn * size + (len(text) - cn) * size * 0.56


def para_height(text: str, size: float, width: float,
                line_spacing: float = 1.6) -> float:
    """估算段落所需高度(pt)。width 为可用宽度。"""
    if width <= 0:
        return size * line_spacing
    lines = max(1, math.ceil(text_width(text, size) / width))
    return lines * size * line_spacing


# ---------------------------------------------------------------- 形状原语

def add_rect(slide, x, y, w, h, fill: Optional[RGBColor] = None,
             line: Optional[RGBColor] = None, line_w: float = 0.5,
             radius: bool = False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(shape_type, Pt(x), Pt(y), Pt(w), Pt(h))
    if radius:
        try:
            sh.adjustments[0] = 0.06
        except (IndexError, KeyError):
            pass
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(line_w)
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.text = ""
    return sh


def add_line(slide, x1, y1, x2, y2, color: RGBColor, w: float = 1.0):
    from pptx.enum.shapes import MSO_CONNECTOR
    ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Pt(x1), Pt(y1), Pt(x2), Pt(y2))
    ln.line.color.rgb = color
    ln.line.width = Pt(w)
    return ln


def add_text(slide, x, y, w, h, text: str, *, size: float = FS["body"],
             color: Optional[RGBColor] = None, bold: bool = False,
             theme: Optional[Theme] = None, align: str = "left",
             line_spacing: float = 1.6, spacing_after: float = 0,
             anchor: str = "top", char_spacing: float = 0,
             italic: bool = False, font: Optional[str] = None):
    """单段落文本框。多段落用 add_paragraphs。"""
    box = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                          "bottom": MSO_ANCHOR.BOTTOM}[anchor]
    p = tf.paragraphs[0]
    p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                   "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}[align]
    p.line_spacing = line_spacing
    if spacing_after:
        p.space_after = Pt(spacing_after)
    run = p.add_run()
    run.text = text
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = color if color is not None else (theme.text if theme else hex2rgb(DEFAULT_THEME["text"]))
    cn = font or (theme.font_cn if theme else "Microsoft YaHei")
    en = theme.font_en if theme else cn
    f.name = en
    _set_ea_font(run, cn)
    if char_spacing:
        run.font._rPr.set("spc", str(int(char_spacing * 100)))
    return box


def add_paragraphs(slide, x, y, w, h, paras: Sequence[dict], *,
                   theme: Theme, default_size: float = FS["body"],
                   gap: float = 12, line_spacing: float = 1.6):
    """多段落文本框。paras: [{text, size?, color?, bold?, gap?}]"""
    box = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for i, spec in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.line_spacing = spec.get("line_spacing", line_spacing)
        p.space_after = Pt(spec.get("gap", gap))
        run = p.add_run()
        run.text = spec["text"]
        f = run.font
        f.size = Pt(spec.get("size", default_size))
        f.bold = spec.get("bold", False)
        f.color.rgb = spec.get("color", theme.text)
        f.name = theme.font_en
        _set_ea_font(run, theme.font_cn)
        if spec.get("char_spacing"):
            run.font._rPr.set("spc", str(int(spec["char_spacing"] * 100)))
    return box


def set_slide_bg(slide, color: RGBColor) -> None:
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = color


def blank_slide(prs):
    layout = prs.slide_layouts[6]  # blank
    return prs.slides.add_slide(layout)
