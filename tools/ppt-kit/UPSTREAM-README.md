# ppt-kit

契约驱动的 PPT 生成器。模型只输出一份 JSON 页面契约，渲染器负责画成可编辑的 pptx，
自检脚本负责抓越界、溢出、重叠。

这套东西的核心判断：**PPT 做得不好，问题几乎不在渲染器，在中间层。**
所以 90% 的价值在 `RULES/` 和 `prompts/`，`pptkit/` 只是把契约翻译成形状。

---

## 5 分钟跑通

```bash
pip install python-pptx lxml

python examples/build_demo.py
```

输出 `examples/课程知识库问答系统.pptx`（10 页）+ 自检报告。

想看图：

```bash
python -m pptkit preview "examples/课程知识库问答系统.pptx" -o examples/shots
```

依赖 PowerPoint（导 PDF）和 poppler 的 `pdftoppm`（转过图）。缺了会明确报错，不静默降级。

---

## 目录

```
RULES/
  STORY.template.md      叙事层：意图对齐 → 页面骨架 → 逐页契约（喂给模型）
  DESIGN.rules.md        设计宪法：网格、字号阶梯、色彩、密度、反模式总表
  LAYOUTS.md             10 个版式的字段、几何、反模式
  CONTRACT.schema.json   页面契约的 JSON Schema
prompts/
  system.md              直接给模型的系统提示词
pptkit/
  theme.py               画布常量、色板、字号阶梯、排版原语（含中文字体处理）
  layouts.py             10 个版式函数
  contract.py            契约校验：把 DESIGN 的硬门禁变成可执行检查
  builder.py             契约 → pptx
  check.py               几何自检：越界 / 溢出 / 重叠 / 字号失衡 / 页码
  preview.py             pptx → 逐页 png
examples/
  deck.json              10 页示例契约
  build_demo.py          最小可跑示例
```

---

## 三层职责

| 层 | 谁做 | 产出 |
|---|---|---|
| 叙事层 | 模型 | 页面契约 JSON（含 title / insight / anti_pattern） |
| 渲染层 | `pptkit/layouts.py` | pptx |
| 检查层 | `contract.py` + `check.py` | 错误清单，不通过就重做 |

**关键约束**：模型不许直接写坐标、字号、颜色。它只填语义字段，几何全由版式函数决定。
这是质量稳定的根本原因——模型负责「说什么」，代码负责「长什么样」。

---

## 接进你自己的智能体

### 第一步：把规则喂给模型

用 `prompts/system.md` 作为系统提示词。需要更细的约束时，把
`RULES/STORY.template.md` 和 `RULES/LAYOUTS.md` 作为附件上下文一并给进去。

### 第二步：拿到 JSON，先校验

```python
import json
from pptkit import build, validate, check, ContractError

deck = json.loads(llm_output)
issues = validate(deck)
errors = [m for lv, m in issues if lv == "error"]
if errors:
    # 把 errors 原样回灌给模型让它改，这是最有效的一轮修复
    raise ContractError("\n".join(errors))

build(deck, "out.pptx")
for lv, msg in check("out.pptx"):
    print(lv, msg)
```

**回灌错误是本方案质量的关键**：模型的第一次输出几乎一定会踩 1–3 条门禁
（标题写成栏目名、章节数对不上、相邻版式重复）。把报错原文塞回对话，第二轮通常就干净了。

### 第三步：渲染成图再看一眼

```python
from pptkit.preview import render
render("out.pptx", "shots")
```

自检只能抓「字压出框」这类几何问题，抓不到「这页读起来没劲」。
所以最后一步必须出图，逐页看。

---

## 加一个新版式

1. 在 `layouts.py` 写函数，签名 `(slide, spec, theme)`，只用 `add_text / add_rect / add_line`；
2. 注册到文件底部的 `LAYOUTS`；
3. 在 `contract.py` 的 `need` 字典里补必填字段；
4. 在 `RULES/LAYOUTS.md` 补一节说明；
5. 内容不对称的版式加入 `ASYMMETRIC` 集合（影响非对称预算校验）。

写版式时守住两条：
- 高度用 `para_height(text, size, width, line_spacing)` 估算后再推进 y，不要写死；
- 内容不足时把剩余空间分配到段间距（参考 `split_title_body` 的弹性 gap），别让整页堆在上半部分。

---

## 已知限制

- 只支持 Windows 的渲染预览（依赖 PowerPoint COM）；生成 pptx 本身跨平台。
- 中文字体默认 `Microsoft YaHei`，换字体要在 theme 里同时设 `font_cn` 和 `font_en`，
  否则中文会掉回宋体（`theme._set_ea_font` 已处理 eastasia，但字体必须本机存在）。
- 图表只支持柱状和折线，且不带数据标签。
- 版式固定 10 种，不做自由画布。需要新形态就加版式函数，不要在契约里塞坐标。
