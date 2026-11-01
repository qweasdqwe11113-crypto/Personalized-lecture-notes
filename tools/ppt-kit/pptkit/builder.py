# -*- coding: utf-8 -*-
"""契约 → pptx。

    from pptkit.builder import build
    build(deck, "out.pptx")

渲染前会自动跑 contract.validate，有 error 直接抛异常（除非 force=True）。
"""
import json
import os
from typing import Optional

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Pt

from .contract import ok, validate
from .layouts import LAYOUTS
from .theme import (CANVAS_H, CANVAS_W, CONTENT_R, CONTENT_X, FOOT_LINE_Y,
                   FOOT_TEXT_Y, FS, Theme, add_line, add_text, blank_slide,
                   set_slide_bg)


class ContractError(Exception):
    pass


def _footer(slide, s: dict, meta: dict, th: Theme, idx: int, total: int) -> None:
    if s.get("type") in ("cover", "ending"):
        return
    left = s.get("footnote") or " · ".join(
        [x for x in [meta.get("org"), meta.get("date")] if x]
    ) or meta.get("title", "")

    # 目录页左侧是整幅深色块，页脚线会横穿过去，改成块内反白 + 右侧页码
    if s.get("type") == "catalog":
        pale = RGBColor(0x9D, 0xB8, 0xDE)
        add_text(slide, 56, FOOT_TEXT_Y, 300, 18, left, size=FS["caption"],
                 color=pale, theme=th, line_spacing=1.2)
        add_text(slide, CONTENT_R - 160, FOOT_TEXT_Y, 160, 18,
                 f"{idx:02d} / {total:02d}", size=FS["caption"], color=th.hint,
                 theme=th, align="right", line_spacing=1.2)
        return

    add_line(slide, CONTENT_X, FOOT_LINE_Y, CONTENT_R, FOOT_LINE_Y, th.line, 0.5)
    add_text(slide, CONTENT_X, FOOT_TEXT_Y, 800, 18, left, size=FS["caption"],
             color=th.hint, theme=th, line_spacing=1.2)
    add_text(slide, CONTENT_R - 160, FOOT_TEXT_Y, 160, 18,
             f"{idx:02d} / {total:02d}", size=FS["caption"], color=th.hint,
             theme=th, align="right", line_spacing=1.2)


def build(deck: dict, out_path: str, *, force: bool = False,
          verbose: bool = True) -> str:
    issues = validate(deck)
    errors = [m for lv, m in issues if lv == "error"]
    if errors and not force:
        raise ContractError("契约校验未通过：\n" + "\n".join("  - " + e for e in errors))
    if verbose:
        for lv, m in issues:
            if lv == "warn" and m != "无问题":
                print(f"[warn] {m}")

    meta = deck.get("meta") or {}
    th = Theme.from_dict(deck.get("theme"))
    slides = deck["slides"]
    total = len(slides)

    prs = Presentation()
    prs.slide_width = Pt(CANVAS_W)
    prs.slide_height = Pt(CANVAS_H)

    for idx, s in enumerate(slides, 1):
        slide = blank_slide(prs)
        set_slide_bg(slide, _white())
        fn = LAYOUTS[s["layout"]]
        fn(slide, s, th)
        _footer(slide, s, meta, th, idx, total)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    prs.save(out_path)
    if verbose:
        print(f"[ok] {out_path}  ({total} 页)")
    return out_path


def _white():
    from pptx.dml.color import RGBColor
    return RGBColor(0xFF, 0xFF, 0xFF)


def build_from_file(contract_path: str, out_path: Optional[str] = None,
                    **kw) -> str:
    with open(contract_path, "r", encoding="utf-8") as f:
        deck = json.load(f)
    if out_path is None:
        name = (deck.get("meta") or {}).get("title") or "deck"
        out_path = os.path.join(os.path.dirname(contract_path), name + ".pptx")
    return build(deck, out_path, **kw)
