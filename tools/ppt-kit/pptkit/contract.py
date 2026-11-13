# -*- coding: utf-8 -*-
"""契约校验：把 DESIGN.rules.md 里的硬门禁变成可执行的检查。

用法：
    from pptkit.contract import validate
    issues = validate(deck)
    for lv, msg in issues: print(lv, msg)

返回 [(level, message)]，level ∈ {"error", "warn"}。有 error 时不应渲染。
"""
import math
import re
from typing import List, Tuple

from .layouts import ASYMMETRIC, LAYOUTS

VALID_TYPE = {"cover", "catalog", "section", "content", "ending"}
VALID_ROLE = {"hero", "supporting", "transition"}
VALID_RHYTHM = {"peak", "valley", "transition"}

# 栏目名黑名单：标题写成这些就等于没写
BARE_TITLES = {
    "背景", "研究背景", "项目背景", "介绍", "项目介绍", "概述", "项目概述",
    "架构", "系统架构", "总体架构", "方案", "设计方案", "技术方案",
    "实验结果", "结果", "结论", "总结", "展望", "下一步", "下一步计划",
    "目录", "汇报提纲", "提纲", "参考文献", "致谢", "谢谢",
}


def _cnt(text: str) -> int:
    """中文按 1、英文数字按 0.55 计的有效字数。"""
    if not text:
        return 0
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(text) - cn
    return int(cn + en * 0.55)


# 这些键存的是枚举值或约束声明，不参与密度统计
_SKIP_KEYS = {"anti_pattern", "layout", "type", "role", "rhythm", "no",
              "kind", "col_widths"}


def _all_text(obj) -> str:
    """递归收集本页所有可见文字。"""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, list):
        return " ".join(_all_text(x) for x in obj)
    if isinstance(obj, dict):
        return " ".join(_all_text(v) for k, v in obj.items() if k not in _SKIP_KEYS)
    return ""


def validate(deck: dict) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    err = lambda m: out.append(("error", m))
    warn = lambda m: out.append(("warn", m))

    meta = deck.get("meta") or {}
    slides = deck.get("slides") or []
    n = len(slides)

    if not meta.get("title"):
        err("meta.title 缺失：无法确定文件名与页脚文案")
    if n == 0:
        err("slides 为空")
        return out
    if not (3 <= len(meta.get("tone") or []) <= 5) and not meta.get("tone"):
        warn("meta.tone 缺失：视觉调性未锁定，设计无依据（建议 3-5 个词）")

    # ---- 逐页基础校验
    seen_layout_ids = {}
    for i, s in enumerate(slides, 1):
        tag = f"p{i:02d}"
        for key, valid in (("type", VALID_TYPE), ("role", VALID_ROLE),
                           ("rhythm", VALID_RHYTHM)):
            if s.get(key) not in valid:
                err(f"{tag} {key}={s.get(key)!r} 不在 {sorted(valid)} 内")
        if s.get("layout") not in LAYOUTS:
            err(f"{tag} layout={s.get('layout')!r} 未实现")
            continue

        title = (s.get("title") or "").strip()
        if s.get("type") in ("content", "cover") and not title:
            err(f"{tag} 缺 title")
        if title:
            # 栏目名判定只对内容页生效：目录 / 扉页的标题本就是标签
            if s.get("type") == "content" and title in BARE_TITLES:
                err(f"{tag} 标题「{title}」是栏目名，必须改写成论断句")
            elif s.get("type") == "content" and len(title) < 5:
                warn(f"{tag} 标题「{title}」过短，多半仍不是论断")
            if title.endswith(("。", ".", "，", ",")):
                warn(f"{tag} 标题结尾不要标点")

        # 版式必需字段
        need = {
            "cover_split": ["title"],
            "catalog": ["items"],
            "section": ["title", "points"],
            "split_title_body": ["title", "body"],
            "asym_two_col": ["title", "left", "right"],
            "table_insight": ["title", "table"],
            "flow_axis": ["title", "nodes"],
            "cards_row": ["title", "cards"],
            "big_number_insight": ["title", "numbers"],
            "chart_insight": ["title", "chart"],
        }[s["layout"]]
        for f in need:
            if not s.get(f):
                err(f"{tag} layout={s['layout']} 缺字段 {f}")

        # 密度：递归收集本页所有可见文字（排除枚举值与 anti_pattern）
        if s.get("type") == "content":
            c = _cnt(_all_text(s))
            if c < 90:
                warn(f"{tag} 有效字数约 {c}，内容偏薄（<120 视为稀薄）")
            elif c > 420:
                warn(f"{tag} 有效字数约 {c}，超过 420 建议拆页")

        if s["layout"] not in seen_layout_ids:
            seen_layout_ids[s["layout"]] = []
        seen_layout_ids[s["layout"]].append(tag)

    # ---- 目录 ↔ 章节扉页契约
    chapters = meta.get("chapters") or []
    secs = [s for s in slides if s.get("type") == "section"]
    if chapters:
        if len(chapters) != len(secs):
            err(f"目录声明 {len(chapters)} 章，实际 {len(secs)} 个章节扉页")
        else:
            for k, (c, sec) in enumerate(zip(chapters, secs), 1):
                if sec.get("no") != f"{k:02d}":
                    err(f"第{k}章扉页编号为 {sec.get('no')!r}，应为 {k:02d}")
                if (sec.get("title") or "").strip() != c.strip():
                    err(f"第{k}章扉页标题「{sec.get('title')}」与目录「{c}」不一致")
    elif secs:
        warn("存在章节扉页但 meta.chapters 未声明，无法校验契约")

    # ---- Hero 配额
    heroes = [i for i, s in enumerate(slides, 1) if s.get("role") == "hero"]
    hmin = max(1, math.floor(0.2 * n))
    hmax = max(2, math.ceil(0.3 * n))
    if not (hmin <= len(heroes) <= hmax):
        err(f"Hero 页 {len(heroes)} 个 / 共 {n} 页，应落在 {hmin}–{hmax}（20%–30%）")
    for a, b in zip(heroes, heroes[1:]):
        if b - a < 2:
            err(f"p{a:02d} 与 p{b:02d} 是相邻 Hero，中间必须隔至少 1 页")

    # ---- 节奏曲线
    run = 0
    for i, s in enumerate(slides, 1):
        if s.get("rhythm") == "valley":
            run += 1
            if run >= 3:
                err(f"从 p{i-1:02d} 起连续 {run} 页 valley，需插入 peak 或 transition")
        else:
            run = 0
    for i, s in enumerate(slides, 1):
        if s.get("type") == "section" and s.get("rhythm") != "transition":
            warn(f"p{i:02d} 是章节扉页，rhythm 应为 transition")
    peaks = sum(1 for s in slides if s.get("rhythm") == "peak")
    if peaks < 2 and n >= 8:
        warn(f"全篇只有 {peaks} 个 peak，缺乏视觉高潮")

    # ---- 版式预算（只在内容页里算）
    content = [s for s in slides if s.get("type") == "content"]
    if len(content) >= 3:
        asym = sum(1 for s in content if s["layout"] in ASYMMETRIC)
        ratio = asym / len(content)
        if ratio < 0.4:
            warn(f"非对称版式 {asym}/{len(content)} = {ratio:.0%}，低于 40%")
        for lay, tags in seen_layout_ids.items():
            r = len(tags) / len(slides)
            if r > 0.4 and len(tags) > 2:
                warn(f"layout={lay} 用了 {len(tags)} 次（{r:.0%}），超过全篇 40%")

    # ---- 相邻版式不重复
    for i in range(1, len(slides)):
        if slides[i]["layout"] == slides[i - 1]["layout"]:
            err(f"p{i:02d} 与 p{i+1:02d} 版式相同（{slides[i]['layout']}）")

    # ---- anti_pattern 建议
    for i, s in enumerate(slides, 1):
        if s.get("type") == "content" and not s.get("anti_pattern"):
            warn(f"p{i:02d} 未写 anti_pattern，设计约束缺位")

    if not out:
        out.append(("warn", "无问题"))
    return out


def ok(issues) -> bool:
    return not any(lv == "error" for lv, _ in issues)
