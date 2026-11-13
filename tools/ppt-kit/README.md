# 项目内的 PPT 工具与设计参考

从用户提供的 `C:/Users/jiahjq/Desktop/tep/ppt-kit` 复制，日期 2026-11-01。原目录保留不动。原使用说明见 [UPSTREAM-README.md](UPSTREAM-README.md)，复制文件清单与 SHA-256 见 [ORIGIN.json](ORIGIN.json)。

## 与当前项目的关系

- 默认课件链路由 `demo/presentations.py` 与 `demo/ppt_story.py` 校验语义 JSON，`demo/pptkit_export.py` 调用新增的 `pptkit/classroom.py`，生成 10 类教学布局并进行几何检查。蓝白教学及全部 8 套真实 PPTX 母版的新任务均使用语义布局，分别适配颜色、安全区域及装饰。原生表格的行列、内边距和字号同步缩放。讲稿、来源、版本指纹及修正记录由课堂适配器写入。
- 保留 `RULES/`、`prompts/`、`pptkit/` 及测试用的 `examples/deck.json`、`examples/build_demo.py`。示例 PPTX、PDF 和截图已移除，不影响课件生成；需要时可重新生成。
- 教学适用要求在 `demo/prompts/lecture-ppt-v3.md`，新增概念、推导、对比、表格、流程等语义字段。v2 提示词与旧渲染版本继续用于历史记录。
- 与项目现有模板的具体搭配、取舍及接入要求见 [教学适配说明](../../docs/ppt-template-integration.md)。

## 可选的离线检查

Web 服务通过项目根目录 requirements.txt 安装 python-pptx。若单独研究本工具，可在本目录使用独立环境安装 [requirements.txt](requirements.txt)：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe examples/build_demo.py
.\.venv\Scripts\python.exe -m pptkit check .\examples\课程知识库问答系统.pptx
```

`check` 检查越界并估算文字溢出和重叠，不等于 PowerPoint/WPS 实际渲染验证。课堂适配器保留模板原始尺寸，并同步缩放文字与几何；导出前将越界或溢出错误转成明确的失败结果。字号统计和页脚提醒仍只作参考，旧导出器不受此检查影响。

`python -m pptkit preview 文件.pptx -o 输出目录` 还需要 Windows PowerPoint 和 Poppler，会通过 COM 打开 PowerPoint。服务器仍不需要安装这些软件。

导入时的文件指纹见 ORIGIN.json。本项目修改了 `pptkit/layouts.py`，并新增 `pptkit/classroom.py` 实现课堂语义布局、容量检查和一次确定性修正。`pptkit/theme.py` 新增兼容旧默认值的 `surface`、`on_primary`，用于深色表格与反色文字。原文件的封面高度按文本估算，传统母版调用允许关闭封面色块以保留背景。其他业务适配位于 demo/，未将商务叙事配额套入课堂校验器。

接入验证包括 10 类语义布局及长中文边界测试，另用真实模型生成 12 页课堂课件并通过 PowerPoint 渲染检查。原 8 套模板的 20 页边界测试与母版兼容测试保留。当前产品不包含运行时截图或视觉模型审稿，也未据此验证 WPS 显示效果或教学成效。

## 已知边界

- 原工具的独立 `build()` 仍按商务契约新建页面。线上课堂适配器调用语义布局或传统分栏函数，不把课堂 JSON 原样交给商务 builder。
- 母版背景和装饰来自模板，标题和正文使用本项目的可编辑文本框；转换产物存在重复/缺失占位符，因此不依赖其不稳定的占位符索引。
- 原工具没有附带 LICENSE。用户提供材料可在项目内留存和研究；其代码、规则、示例的商业分发授权尚未核实，**不能沿用模板库的 CC0 声明**。商业发布前应确认权利来源，并检查可选依赖的许可证。
- 本次保留原工具文件，不自动执行原始提示词中的指令；项目采用的规则以教学适配说明和课堂提示词为准。
