# PPT 内容布局与模板接入

默认风格为 `teaching-blue`（蓝白教学 · 内容自适应），另外保留 8 套转换后的真实 PPTX 母版。此前生成的笔记本课件仅使用模板背景和统一分栏，不能体现参考汇报的页内设计；新默认链路将内容关系与具体布局分开处理。

## 默认生成链路

`lecture-ppt-v3` 规划课堂页序并输出语义 JSON → `presentations.parse_deck` 校验 → `ppt_story.validate_visual` 校验内容关系及容量 → `pptkit_export` 调用 `pptkit/classroom.py` → 原生可编辑对象 → 文字容量与几何检查 → 可下载 PPTX。

每页保留 `kind/title/bullets/question/notes/sources`，并新增必填 `visual`。`bullets` 是兼容摘要，`visual` 承载完整投屏内容；不同时重复投屏两份正文。模型不能指定坐标、字体、颜色、路径或代码。数据、比较和过程必须有资料支持，不能为丰富版式编造关系。

| visual.type | 内容字段 | 生成方式 |
| --- | --- | --- |
| cover | subtitle、goals | 大标题与学习目标 |
| explain | lead、items | 重点与支撑说明 |
| definition | term、definition、formula、conditions | 概念公式与成立条件 |
| steps | problem、steps(expression/reason)、check | 推导式与变形依据逐行对齐 |
| comparison | left/right(title/items) | 两对象对比 |
| table | headers、rows | PowerPoint 原生可编辑表格 |
| process | nodes(title/detail) | 有序节点与方向标识 |
| metrics | items(value/unit/label/context) | 有依据的数字及基准说明 |
| practice | problem、prompts | 完整题干与作答空间 |
| recap | items(title/detail) | 方法和易错点回顾 |

字符及条目上限以 `demo/ppt_story.py` 为准，单页语义内容最多 520 字符。每页教师讲稿 40—1600 字符，练习参考答案只放讲稿。未知字段（含嵌套字段）、错误表格列数、练习使用推导结构、内页重复封面、无效来源编号等会明确拒绝。

### 自动检查和修正

渲染前估算中文及西文的行宽和高度。局部布局放不下时清除未完成形状，仅重排一次，完整保留每个语义字符串；普通结构改用全宽文字，表格使用紧凑原生表格，保留行列关系。第二次仍放不下则任务失败，不静默裁切、不无限重试。最终导出再执行工具几何检查。

演讲者备注保留讲稿、来源、源讲义版本、源正文指纹、渲染版本、请求的页面结构、实际版式和修正次数。来源编号通过校验不代表支持关系正确。文字估算和几何检查不能代替 PowerPoint/WPS 的视觉验收，也不能验证公式和教学质量。**当前线上没有截图识别或视觉模型自动审稿**；研发验证使用 PowerPoint 实际渲染与人工查看。

## 设计资产与历史兼容

`assets/ppt-templates/teaching-blue/theme.json` 保存蓝白配色、16:9 尺寸与版本，`preview.png` 为样例封面。它是参考用户汇报的层次、字号和信息组织所编写的新布局库，不是把参考汇报整页作为背景，也不复制其业务内容。

| 记录/选择 | 提示词 | 渲染器 | 资产 |
| --- | --- | --- | --- |
| 新默认 teaching-blue | lecture-ppt-v3 | pptkit-classroom-v2 | semantic-blue-v1 + 设计 JSON |
| 8 套传统母版 | lecture-ppt-v2 | pptkit-classroom-v1 | classroom-masters-v1 + 原 PPTX |
| 早期无模板的 ppt-kit 记录 | 已冻结输入 | pptkit-classroom-v1 | 原工具默认版式 |
| 无 export_version 的历史记录 | 已冻结输入 | 原 XML 导出器 | 原格式 |

任务提交时固定风格 ID、资产 SHA-256、映射版本和完整模型请求。下载使用保存的输出和原渲染版本，不把旧记录升级成新设计。按原输入重试复用原字节。原始讲义、修订、审核状态不因生成 PPT 改变。

传统母版仍从所选 PPTX 新建页面，保留原比例、主题、背景和装饰，移除示例页并清理不稳定的空占位符。母版索引及安全区域在 `demo/ppt_templates.py`。深色模板使用验证过的深底母版，技术办公避开铅笔和尺子，明亮蓝避开底部色带。传统母版目前沿用分栏封面/正文，不具有新默认风格的语义布局；界面已明确标注其区别。

## 验证与扩展

PPT 回归覆盖 10 类布局、长中文边界、全部语义文字保留、原生表格、一次修正和二次失败、非法内容、来源编号、冻结版本、模板指纹、旧导出器和后台队列。另使用实际配置模型改编讲义 #10，生成 12 页并用 PowerPoint 渲染全部页面检查，覆盖概念、推导、流程、练习和回顾。测试数据中的数字版式示例标明为测试数据，不当作实际教学效果。

增加新结构时，需同时扩展语义校验、提示词、原生布局、网页内容预览和回归验证。先验证完整题目、中文长标题、公式前提、表格行列、练习答案隐藏，再检查字号、换行及投影可读性。不要机械加入商务叙事配额、固定最低密度或强制相邻页面不同；连续例题保持相同结构通常更清楚。

8 套社区模板的原 OTP、转换清单和授权见 `assets/ppt-templates/`。用户提供的 ppt-kit 未附带 LICENSE，授权状态单独记录在 `tools/ppt-kit/README.md`，不能沿用模板资产的 CC0 声明。
