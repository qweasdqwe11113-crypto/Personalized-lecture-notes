# -*- coding: utf-8 -*-
"""几何自检：越界 / 文字溢出 / 重叠 / 字号失衡 / 页码缺失。

    python -m pptkit.check out.pptx

渲染后用眼睛看之前，先让这个脚本扫一遍——它抓的是"字压出框"这类肉眼容易漏的问题。
"""
import math
import re
import sys
from typing import List, Tuple

from pptx import Presentation
from pptx.util import Emu

EMU_PER_PT = 12700
CN_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def _text_width(text: str, size: float) -> float:
    cn = len(CN_RE.findall(text))
    other = len(text) - cn
    return cn * size + other * size * 0.56


def _need_height(shape) -> float:
    """估算文本所需高度(pt)。"""
    tf = shape.text_frame
    avail = (shape.width - (tf.margin_left or 0) - (tf.margin_right or 0)) / EMU_PER_PT
    if avail <= 4:
        return 0.0
    need = 0.0
    for p in tf.paragraphs:
        text = "".join(r.text for r in p.runs)
        sizes = [r.font.size.pt for r in p.runs if r.font.size is not None]
        size = max(sizes) if sizes else 17.0
        if not text.strip():
            need += size * 0.7
            continue
        lines = max(1, math.ceil(_text_width(text, size) / avail))
        ls = p.line_spacing if isinstance(p.line_spacing, float) else 1.3
        need += lines * size * ls
        if p.space_after is not None:
            need += p.space_after / EMU_PER_PT
    return need


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def check(path: str, *, overflow_tol: float = 1.08,
          overlap_ratio: float = 0.15) -> List[Tuple[str, str]]:
    prs = Presentation(path)
    W = prs.slide_width / EMU_PER_PT
    H = prs.slide_height / EMU_PER_PT
    out: List[Tuple[str, str]] = []
    total = len(prs.slides)

    for idx, slide in enumerate(prs.slides, 1):
        tag = f"p{idx:02d}"
        boxes = []
        sizes = set()
        has_pageno = False

        for sh in slide.shapes:
            if sh.left is None or sh.top is None or sh.width is None or sh.height is None:
                continue
            l = sh.left / EMU_PER_PT
            t = sh.top / EMU_PER_PT
            w = sh.width / EMU_PER_PT
            h = sh.height / EMU_PER_PT
            r, b = l + w, t + h

            # 越界（1pt 容差）
            if l < -1 or t < -1 or r > W + 1 or b > H + 1:
                out.append(("error", f"{tag} 越界 L{l:.0f} T{t:.0f} R{r:.0f} B{b:.0f} "
                                      f"(画布 {W:.0f}x{H:.0f}) name={sh.name}"))

            if not getattr(sh, "has_text_frame", False) or not sh.text_frame.text.strip():
                continue
            text = sh.text_frame.text.strip()

            for p in sh.text_frame.paragraphs:
                # 页脚区（y>600）不计入字号统计
                if t < 600:
                    for run in p.runs:
                        if run.font.size is not None:
                            sizes.add(round(run.font.size.pt, 1))

            if re.fullmatch(r"\d{2}\s*/\s*\d{2}", text):
                has_pageno = True

            need = _need_height(sh)
            if need > h * overflow_tol:
                out.append(("error", f"{tag} 文字溢出 {need/h:.0%}: "
                                     f"「{text[:20]}」需 {need:.0f}pt / 框高 {h:.0f}pt"))
            boxes.append((l, t, r, b, text, sh.name))

        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b_ = boxes[i], boxes[j]
                area = _overlap(a[:4], b_[:4])
                if area <= 0:
                    continue
                sa = (a[2] - a[0]) * (a[3] - a[1])
                sb = (b_[2] - b_[0]) * (b_[3] - b_[1])
                denom = min(sa, sb)
                if denom and area / denom > overlap_ratio:
                    out.append(("warn", f"{tag} 文字重叠 {area/denom:.0%}: "
                                        f"「{a[4][:14]}」x「{b_[4][:14]}」"))

        if len(sizes) > 6:
            out.append(("warn", f"{tag} 用了 {len(sizes)} 种字号 {sorted(sizes)}，层级过多"))
        if idx > 1 and not has_pageno:
            out.append(("warn", f"{tag} 缺页码"))

    if not out:
        out.append(("ok", f"通过：{total} 页，无越界/溢出/重叠"))
    return out


def report(path: str) -> int:
    issues = check(path)
    errs = [m for lv, m in issues if lv == "error"]
    print(f"检查 {path}")
    for lv, m in issues:
        if lv == "ok":
            print(f"  [ok] {m}")
        else:
            print(f"  [{lv}] {m}")
    print(f"\n结论：{len(errs)} 个错误，{len(issues) - len(errs)} 条提示")
    return 1 if errs else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m pptkit.check <file.pptx>")
        sys.exit(2)
    sys.exit(report(sys.argv[1]))
