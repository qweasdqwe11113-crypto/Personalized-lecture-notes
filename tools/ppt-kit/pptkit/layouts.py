# -*- coding: utf-8 -*-
"""10 个版式函数。每个函数只负责一页的几何排布，不做校验、不做页脚。

契约字段说明见 RULES/LAYOUTS.md。函数签名统一 (slide, spec, theme)。
"""
from typing import Optional

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Pt

from .theme import (CANVAS_H, CANVAS_W, CONTENT_R, CONTENT_W, CONTENT_X, FS,
                    MARGIN_R, Theme, ZONE_A_TOP, ZONE_B_TOP, add_line,
                    add_paragraphs, add_rect, add_text, para_height,
                    set_slide_bg)

# 正文行高系数
LH = 1.6


# ---------------------------------------------------------------- 标题区

def _title_size(t: str) -> float:
    """标题过长时降级，避免压行或溢出。三档足够，档位越多层级越乱。"""
    n = len(t)
    if n <= 20:
        return FS["h1"]          # 40
    if n <= 28:
        return 34
    return FS["h2"]              # 28


def header(slide, s: dict, th: Theme, *, color: Optional[RGBColor] = None) -> float:
    """标准标题区（eyebrow + 标题 + 副标题），返回内容区起始 y。"""
    y = ZONE_A_TOP
    c_title = color or th.primary
    if s.get("eyebrow"):
        add_text(slide, CONTENT_X, y, CONTENT_W, 18, s["eyebrow"].upper(),
                 size=FS["eyebrow"], color=th.accent, bold=True, theme=th,
                 char_spacing=3, line_spacing=1.0)
        y += 24
    ts = _title_size(s.get("title", ""))
    add_text(slide, CONTENT_X, y, CONTENT_W, ts * 1.3, s.get("title", ""),
             size=ts, color=c_title, bold=True, theme=th, line_spacing=1.15)
    y += ts * 1.25
    if s.get("subtitle"):
        h = para_height(s["subtitle"], FS["h3"], CONTENT_W, 1.4)
        add_text(slide, CONTENT_X, y + 2, CONTENT_W, h + 4, s["subtitle"],
                 size=FS["h3"], color=th.muted, theme=th, line_spacing=1.4)
        y += h + 10
    return max(y + 12, ZONE_B_TOP)


def _insight(slide, s: dict, th: Theme, y: float = 560.0) -> None:
    """底部论断条：细横线 + 左侧 3pt 竖条 + 一句话。"""
    txt = s.get("insight")
    if not txt:
        return
    add_line(slide, CONTENT_X, y, CONTENT_R, y, th.line, 0.75)
    add_rect(slide, CONTENT_X, y + 14, 3, 22, fill=th.primary)
    add_text(slide, CONTENT_X + 16, y + 12, CONTENT_W - 16, 26, txt,
             size=FS["h3"], color=th.primary, bold=True, theme=th,
             line_spacing=1.25)


# ---------------------------------------------------------------- 1 封面

def cover_split(slide, s: dict, th: Theme, *, body_size=None, decorate=True) -> None:
    if decorate:
        add_rect(slide, 0, 0, 13, CANVAS_H, fill=th.primary)
        add_rect(slide, 700, 0, CANVAS_W - 700, CANVAS_H, fill=th.bg_sub)

    x, w = CONTENT_X, 570
    y = 190.0
    if s.get("eyebrow"):
        add_text(slide, x, y, w, 18, s["eyebrow"], size=FS["eyebrow"],
                 color=th.accent, bold=True, theme=th, char_spacing=3, line_spacing=1.0)
        y += 32
    add_rect(slide, x, y, 54, 3, fill=th.accent)
    y += 30
    title = s.get("title", "")
    tsize = FS["display"] if len(title) <= 8 else 62 if len(title) <= 18 else 44
    title_h = para_height(title, tsize, w, 1.15) + 8
    add_text(slide, x, y, w, title_h, title, size=tsize,
             color=th.primary, bold=True, theme=th, line_spacing=1.08)
    y += title_h + 12
    if s.get("en_title"):
        eh = para_height(s["en_title"], FS["h3"], w, 1.4)
        add_text(slide, x, y, w, eh + 4, s["en_title"], size=FS["h3"], color=th.muted,
                 theme=th, line_spacing=1.4, font=th.font_en)
        y += eh + 20
    if s.get("subtitle"):
        add_text(slide, x, y, w, 34, s["subtitle"], size=FS["h3"] + 2,
                 color=th.text, theme=th, line_spacing=1.4)
    if s.get("footnote"):
        add_text(slide, x, 648, w, 18, s["footnote"], size=FS["caption"],
                 color=th.hint, theme=th, line_spacing=1.2)

    # 右侧浅色块承载三条要点：避免右侧出现整块空白
    bx, bw = 760, 430
    bullets = (s.get("bullets") or [])[:3]
    by = 250.0
    bs = body_size or FS["body"]
    for i, b in enumerate(bullets, 1):
        add_text(slide, bx, by, 40, 18, f"0{i}", size=FS["eyebrow"], color=th.accent,
                 bold=True, theme=th, char_spacing=2, line_spacing=1.0)
        bh = para_height(b, bs, bw - 52, 1.55)
        add_text(slide, bx + 40, by - 3, bw - 52, bh + 6, b, size=bs,
                 color=th.muted, theme=th, line_spacing=1.55)
        by += bh + 34


# ---------------------------------------------------------------- 2 目录

def catalog(slide, s: dict, th: Theme) -> None:
    add_rect(slide, 0, 0, 380, CANVAS_H, fill=th.primary)
    add_text(slide, 56, 96, 280, 60, s.get("title") or "目录", size=FS["h1"],
             color=RGBColor(0xFF, 0xFF, 0xFF), bold=True, theme=th, line_spacing=1.2)
    add_rect(slide, 56, 168, 46, 3, fill=th.accent)
    if s.get("subtitle"):
        add_text(slide, 56, 190, 270, 60, s["subtitle"], size=FS["small"],
                 color=RGBColor(0xC9, 0xD9, 0xF0), theme=th, line_spacing=1.6)

    items = s.get("items") or []
    n = max(len(items), 1)
    top, avail = 150.0, 460.0
    step = min(avail / n, 150)
    for i, it in enumerate(items):
        y = top + i * step
        add_text(slide, 448, y, 90, 46, it.get("no") or f"{i+1:02d}", size=34,
                 color=th.accent, bold=True, theme=th, line_spacing=1.0)
        add_text(slide, 552, y + 2, 600, 34, it.get("title", ""), size=FS["h2"],
                 color=th.text, bold=True, theme=th, line_spacing=1.2)
        if it.get("desc"):
            add_text(slide, 552, y + 40, 620, 26, it["desc"], size=FS["small"],
                     color=th.hint, theme=th, line_spacing=1.5)
        if i < n - 1:
            add_line(slide, 448, y + step - 34, 1128, y + step - 34, th.line, 0.75)


# ---------------------------------------------------------------- 3 章节扉页

def section(slide, s: dict, th: Theme) -> None:
    add_rect(slide, 0, 0, CANVAS_W, 6, fill=th.primary)
    no = s.get("no") or "01"
    add_text(slide, CONTENT_X, 132, 320, 150, no, size=120, color=th.primary_pale,
             bold=True, theme=th, line_spacing=1.0)
    add_text(slide, CONTENT_X, 288, 760, 52, s.get("title", ""), size=FS["h1"],
             color=th.primary, bold=True, theme=th, line_spacing=1.2)
    add_rect(slide, CONTENT_X, 350, 54, 3, fill=th.accent)

    y = 392
    for p in (s.get("points") or [])[:3]:
        ph = para_height(p, FS["h3"], 900, 1.5)
        add_rect(slide, CONTENT_X, y + 10, 6, 6, fill=th.accent)
        add_text(slide, CONTENT_X + 18, y, 900, ph + 6, p, size=FS["h3"],
                 color=th.muted, theme=th, line_spacing=1.5)
        y += ph + 12


# ---------------------------------------------------------------- 4 左标题右内容

def split_title_body(slide, s: dict, th: Theme, *, body_size=None,
                     body_spacing=LH, subtitle_size=None) -> None:
    bs = body_size or FS["body"]
    ss = subtitle_size or FS["body"]
    # 左栏：论断 + 竖线 + 一句解释
    lx, lw = CONTENT_X, 372
    y = 92.0
    if s.get("eyebrow"):
        add_text(slide, lx, y, lw, 18, s["eyebrow"], size=FS["eyebrow"],
                 color=th.accent, bold=True, theme=th, char_spacing=3, line_spacing=1.0)
        y += 26
    ts = min(_title_size(s.get("title", "")), 34)
    th_ = para_height(s.get("title", ""), ts, lw, 1.25)
    add_text(slide, lx, y, lw, th_ + 8, s.get("title", ""), size=ts,
             color=th.primary, bold=True, theme=th, line_spacing=1.25)
    y += th_ + 20
    add_rect(slide, lx, y, 40, 3, fill=th.accent)
    y += 24
    if s.get("subtitle"):
        sh = para_height(s["subtitle"], ss, lw, 1.4)
        add_text(slide, lx, y, lw, sh + 6, s["subtitle"], size=ss,
                 color=th.muted, theme=th, line_spacing=1.4)

    # 右栏：分段，剩余空间平均分配到段间距（垂直均衡）
    rx, rw = 480, CONTENT_R - 480
    blocks = []
    for blk in (s.get("body") or []):
        if isinstance(blk, dict):
            head, text = blk.get("heading"), blk.get("text", "")
        else:
            head, text = None, str(blk)
        h = 0.0
        if head:
            h += para_height(head, bs, rw, 1.3) + 8
        if text:
            h += para_height(text, bs, rw, body_spacing)
        blocks.append((head, text, h))

    top, bottom = 92.0, 540.0
    total = sum(b[2] for b in blocks)
    gap = 18.0 if len(blocks) < 2 else (
        max(18.0, min(62.0, (bottom - top - total) / (len(blocks) - 1)))
    )
    ry = top
    for head, text, h in blocks:
        if head:
            hh = para_height(head, bs, rw, 1.3)
            add_text(slide, rx, ry, rw, hh + 4, head, size=bs,
                     color=th.text, bold=True, theme=th, line_spacing=1.3)
            ry += hh + 8
        if text:
            bh = para_height(text, bs, rw, body_spacing)
            add_text(slide, rx, ry, rw, bh + 6, text, size=bs,
                     color=th.muted, theme=th, line_spacing=body_spacing)
            ry += bh
        ry += gap
    _insight(slide, s, th)


# ---------------------------------------------------------------- 5 非对称双栏

def asym_two_col(slide, s: dict, th: Theme) -> None:
    y0 = header(slide, s, th)
    # 左 60%：段间距弹性分配，把内容铺开到洞察条上方
    lx, lw = CONTENT_X, 600
    ly = y0
    left = s.get("left") or {}
    if left.get("heading"):
        add_text(slide, lx, ly, lw, 26, left["heading"], size=FS["body"],
                 color=th.primary, bold=True, theme=th, line_spacing=1.3)
        ly += 32
    paras = left.get("body") or []
    heights = [para_height(p, FS["body"], lw, LH) for p in paras]
    total = sum(heights)
    gap = 16.0 if len(paras) < 2 else max(
        16.0, min(56.0, (500 - ly - total) / (len(paras) - 1))
    )
    for para, bh in zip(paras, heights):
        add_text(slide, lx, ly, lw, bh + 6, para, size=FS["body"], color=th.muted,
                 theme=th, line_spacing=LH)
        ly += bh + gap

    # 右 40%：先算内容高度，让灰块贴合内容而不是铺满到底
    rx, rw = 748, CONTENT_R - 748
    right = s.get("right") or {}
    items = right.get("items") or []
    # 逐项累加真实高度，灰块贴合到最后一行的下方
    inner = 34.0 if right.get("heading") else 8.0
    desc_hs = []
    for it in items:
        dh = para_height(it["desc"], FS["small"], rw - 84, 1.5) if it.get("desc") else 0.0
        desc_hs.append(dh)
        inner += 24 + dh + 14
    box_h = min(inner + 14, 540 - y0 + 6)
    add_rect(slide, rx, y0 - 6, rw, box_h, fill=th.bg_sub)

    ry = y0 + 16
    if right.get("heading"):
        add_text(slide, rx + 24, ry, rw - 48, 24, right["heading"], size=FS["eyebrow"],
                 color=th.accent, bold=True, theme=th, char_spacing=2, line_spacing=1.2)
        ry += 34
    for i, (it, dh) in enumerate(zip(items, desc_hs)):
        add_text(slide, rx + 24, ry, 34, 26, it.get("no") or f"{i+1}", size=FS["body"],
                 color=th.accent, bold=True, theme=th, line_spacing=1.1)
        add_text(slide, rx + 60, ry - 2, rw - 84, 26, it.get("title", ""),
                 size=FS["body"], color=th.text, bold=True, theme=th, line_spacing=1.3)
        if dh:
            add_text(slide, rx + 60, ry + 24, rw - 84, dh + 4, it["desc"],
                     size=FS["small"], color=th.muted, theme=th, line_spacing=1.5)
        ry += 24 + dh + 14
    _insight(slide, s, th)


# ---------------------------------------------------------------- 6 表格 + 洞察

def _set_cell(cell, text, th: Theme, *, bold=False, size=None, color=None,
              fill=None, align=PP_ALIGN.LEFT):
    if fill is not None:
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill
    else:
        cell.fill.background()
    cell.margin_left = Pt(14)
    cell.margin_right = Pt(10)
    cell.margin_top = Pt(6)
    cell.margin_bottom = Pt(6)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf = cell.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = 1.4
    run = p.add_run()
    run.text = text
    f = run.font
    f.size = Pt(size or FS["small"])
    f.bold = bold
    f.color.rgb = color or th.text
    f.name = th.font_en
    from .theme import _set_ea_font
    _set_ea_font(run, th.font_cn)


def table_insight(slide, s: dict, th: Theme) -> None:
    y0 = header(slide, s, th)
    spec = s.get("table") or {}
    header_row = spec.get("header") or []
    rows = spec.get("rows") or []
    if not header_row:
        return
    ncol = len(header_row)
    nrow = len(rows) + 1
    top = y0 + 6
    # 表格铺满到洞察条上方，避免底部大块留白
    row_h = min(58, max(34, (534 - top) / nrow))
    gf = slide.shapes.add_table(nrow, ncol, Pt(CONTENT_X), Pt(top),
                                Pt(CONTENT_W), Pt(row_h * nrow))
    tbl = gf.table
    tbl.first_row = False
    tbl.horz_banding = False
    weights = spec.get("col_widths") or [1] * ncol
    tot = sum(weights)
    for i, wgt in enumerate(weights):
        tbl.columns[i].width = Pt(CONTENT_W * wgt / tot)
    for r in range(nrow):
        tbl.rows[r].height = Pt(row_h)
    for c, h in enumerate(header_row):
        _set_cell(tbl.cell(0, c), h, th, bold=True, size=FS["small"],
                  color=RGBColor(0xFF, 0xFF, 0xFF), fill=th.primary)
    for r, row in enumerate(rows):
        band = th.bg_sub if r % 2 == 0 else None
        for c in range(ncol):
            val = row[c] if c < len(row) else ""
            _set_cell(tbl.cell(r + 1, c), str(val), th, size=FS["small"],
                      color=th.muted if c > 0 else th.text, fill=band,
                      bold=(c == 0))
    _insight(slide, s, th)


# ---------------------------------------------------------------- 7 流程轴

def flow_axis(slide, s: dict, th: Theme) -> None:
    y0 = header(slide, s, th)
    nodes = s.get("nodes") or []
    if not nodes:
        return
    n = len(nodes)
    gap = 40
    bw = (CONTENT_W - gap * (n - 1)) / n
    bh = 152
    # 在内容区垂直居中，避免整条流程浮在上半页
    by = y0 + max(24.0, (540 - y0 - bh) / 2 - 10)
    for i, nd in enumerate(nodes):
        x = CONTENT_X + i * (bw + gap)
        add_rect(slide, x, by, bw, bh, fill=th.bg_sub)
        add_rect(slide, x, by, bw, 3, fill=th.primary)
        add_text(slide, x + 18, by + 44, bw - 36, 26, nd.get("label", ""),
                 size=FS["h3"], color=th.primary, bold=True, theme=th,
                 line_spacing=1.2, align="center")
        if nd.get("desc"):
            add_text(slide, x + 18, by + 84, bw - 36, 48, nd["desc"],
                     size=FS["small"], color=th.muted, theme=th, line_spacing=1.5,
                     align="center")
        if i < n - 1:
            ax, ay = x + bw + 10, by + bh / 2
            add_line(slide, ax, ay, ax + 20, ay, th.accent, 2)
            add_line(slide, ax + 20, ay, ax + 11, ay - 7, th.accent, 2)
            add_line(slide, ax + 20, ay, ax + 11, ay + 7, th.accent, 2)
    _insight(slide, s, th)


# ---------------------------------------------------------------- 8 卡片横排

def cards_row(slide, s: dict, th: Theme) -> None:
    y0 = header(slide, s, th)
    cards = s.get("cards") or []
    if not cards:
        return
    n = len(cards)
    gap = 32
    cw = (CONTENT_W - gap * (n - 1)) / n
    cy = max(y0 + 20, 196)
    ch = min(300, 540 - cy)
    for i, c in enumerate(cards):
        x = CONTENT_X + i * (cw + gap)
        add_rect(slide, x, cy, cw, ch, fill=th.bg_sub)
        add_rect(slide, x, cy, cw, 3, fill=th.primary)
        add_text(slide, x + 24, cy + 28, cw - 48, 30, c.get("title", ""),
                 size=FS["h3"], color=th.primary, bold=True, theme=th,
                 line_spacing=1.3)
        add_line(slide, x + 24, cy + 70, x + 24 + 40, cy + 70, th.accent, 2)
        if c.get("desc"):
            add_text(slide, x + 24, cy + 90, cw - 48, ch - 110, c["desc"],
                     size=FS["body"], color=th.muted, theme=th, line_spacing=LH)
    _insight(slide, s, th)


# ---------------------------------------------------------------- 9 大数字

def big_number_insight(slide, s: dict, th: Theme) -> None:
    y0 = header(slide, s, th)
    nums = s.get("numbers") or []
    if not nums:
        return
    n = len(nums)
    colw = CONTENT_W / n
    # 数字块整体在内容区垂直居中
    block_h = 84 + 8 + 22 + 14 + 56
    ny = y0 + max(28.0, (540 - y0 - block_h) / 2)
    for i, item in enumerate(nums):
        x = CONTENT_X + i * colw
        val = item.get("value", "")
        add_text(slide, x, ny, colw - 20, 84, val, size=FS["num"], color=th.accent,
                 bold=True, theme=th, line_spacing=1.0)
        if item.get("unit"):
            add_text(slide, x, ny + 86, colw - 20, 22, item["unit"],
                     size=FS["small"], color=th.hint, theme=th, line_spacing=1.2)
        if item.get("label"):
            add_text(slide, x, ny + 116, colw - 24, 56, item["label"],
                     size=FS["body"], color=th.muted, theme=th, line_spacing=1.5)
        if i > 0:
            add_line(slide, x - 12, ny + 14, x - 12, ny + block_h - 24, th.line, 0.75)
    _insight(slide, s, th)


# ---------------------------------------------------------------- 10 图表

def chart_insight(slide, s: dict, th: Theme) -> None:
    y0 = header(slide, s, th)
    spec = s.get("chart") or {}
    cats = spec.get("categories") or []
    series = spec.get("series") or []
    if not cats or not series:
        return
    cd = CategoryChartData()
    cd.categories = cats
    for sr in series:
        cd.add_series(sr.get("name", ""), sr.get("values", []))
    kind = (spec.get("kind") or "bar").lower()
    x, y = CONTENT_X, y0 + 4
    w, h = 700, min(340, 540 - y)
    ct = XL_CHART_TYPE.LINE_MARKERS if kind == "line" else XL_CHART_TYPE.COLUMN_CLUSTERED
    gf = slide.shapes.add_chart(ct, Pt(x), Pt(y), Pt(w), Pt(h), cd)
    chart = gf.chart
    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(FS["caption"])
        chart.legend.font.name = th.font_cn
    for ax in (chart.category_axis, chart.value_axis):
        ax.tick_labels.font.size = Pt(FS["caption"])
        ax.tick_labels.font.name = th.font_cn
        ax.tick_labels.font.color.rgb = th.hint
        ax.format.line.color.rgb = th.line
    chart.value_axis.has_major_gridlines = True
    chart.value_axis.major_gridlines.format.line.color.rgb = th.line
    chart.value_axis.major_gridlines.format.line.width = Pt(0.5)
    palette = [th.primary, th.accent, th.hint, th.muted]
    for i, sr in enumerate(chart.series):
        col = palette[i % len(palette)]
        if kind == "line":
            sr.format.line.color.rgb = col
            sr.format.line.width = Pt(2)
        else:
            sr.format.fill.solid()
            sr.format.fill.fore_color.rgb = col

    tx = x + w + 36
    tw = CONTENT_R - tx
    add_rect(slide, tx, y + 8, 3, 26, fill=th.primary)
    add_text(slide, tx + 14, y, tw - 14, 40, "怎么读", size=FS["body"],
             color=th.primary, bold=True, theme=th, line_spacing=1.3)
    body = s.get("body") or []
    ty = y + 46
    for para in body[:3]:
        text = para if isinstance(para, str) else para.get("text", "")
        add_text(slide, tx, ty, tw, 90, text, size=FS["body"], color=th.muted,
                 theme=th, line_spacing=1.6)
        ty += 26 * (max(1, int(len(text) * (FS["body"]) / tw) + 1)) + 14
    _insight(slide, s, th)


# ---------------------------------------------------------------- 路由表

LAYOUTS = {
    "cover_split": cover_split,
    "catalog": catalog,
    "section": section,
    "split_title_body": split_title_body,
    "asym_two_col": asym_two_col,
    "table_insight": table_insight,
    "flow_axis": flow_axis,
    "cards_row": cards_row,
    "big_number_insight": big_number_insight,
    "chart_insight": chart_insight,
}

# 非对称版式（用于骨架预算校验）
ASYMMETRIC = {"cover_split", "split_title_body", "asym_two_col", "flow_axis", "chart_insight"}
