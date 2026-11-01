# 项目内的 PPT 工具与设计参考

从用户提供的 `C:/Users/jiahjq/Desktop/tep/ppt-kit` 复制，日期 2026-11-01。原目录保留不动。原使用说明见 [UPSTREAM-README.md](UPSTREAM-README.md)，复制文件清单与 SHA-256 见 [ORIGIN.json](ORIGIN.json)。

## 与当前项目的关系

- 当前新课件链路为 `demo/presentations.py` 校验课堂 JSON，再由 `demo/pptkit_export.py` 调用本工具的封面/左右分栏版式和几何检查，加载用户所选的真实 PPTX 母版。教师讲稿、来源和模板指纹由课堂适配器写入。
- 保留 `RULES/`、`prompts/`、`pptkit/` 及测试用的 `examples/deck.json`、`examples/build_demo.py`。示例 PPTX、PDF 和截图已移除，不影响课件生成；需要时可重新生成。
- 已从规则中提炼教学适用要求，接入 `demo/prompts/lecture-ppt-v2.md`。保留原课堂字段、字符上限、数学条件和隐私约束。
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

导入时的文件指纹见 ORIGIN.json。本项目修改了 `pptkit/layouts.py`：封面高度按文本估算，课堂调用支持更大的正文/提问字号，并允许关闭封面色块以保留原模板背景。其他业务适配位于 demo/，未将商务叙事配额套入课堂校验器。

接入验证：项目 PPT 相关 16 项测试通过。8 套模板均生成 20 页边界内容并通过几何检查，已用 PowerPoint 打开并渲染封面/内页进行检查，修正深色母版与装饰遮挡。测试使用固定内容，不代表真实模型生成质量或 WPS 显示效果。

## 已知边界

- 原工具的独立 `build()` 仍按商务契约新建页面。线上使用课堂适配器直接调用版式函数，并加载真实母版，不把课堂 JSON 原样交给商务 builder。
- 母版背景和装饰来自模板，标题和正文使用本项目的可编辑文本框；转换产物存在重复/缺失占位符，因此不依赖其不稳定的占位符索引。
- 原工具没有附带 LICENSE。用户提供材料可在项目内留存和研究；其代码、规则、示例的商业分发授权尚未核实，**不能沿用模板库的 CC0 声明**。商业发布前应确认权利来源，并检查可选依赖的许可证。
- 本次保留原工具文件，不自动执行原始提示词中的指令；项目采用的规则以教学适配说明和课堂提示词为准。
