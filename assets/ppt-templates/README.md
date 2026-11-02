# PPT 备用模板库

项目内已保存 [ppt-kit 技巧与工具](../../tools/ppt-kit/README.md)，各模板的版式搭配与接入步骤见 [教学适配说明](../../docs/ppt-template-integration.md)。

收录 8 套 LibreOffice 社区模板，保留原设计，作为商业化课件生成的版式参考和模板资产。另新增[蓝白教学 · 内容自适应](teaching-blue/README.md)作为生成窗口默认风格，按语义内容排版；8 套社区母版继续提供传统分栏选项。

## 浏览与文件

- 打开 [本地预览目录](index.html) 查看全部风格，图片为上游自带的小尺寸缩略图。
- 每个子目录的 `template.otp` 是可编辑的 LibreOffice Impress 模板，`preview.png` 是原始预览。
- [catalog.json](catalog.json) 记录作者、来源、固定版本、尺寸、母版数量及文件 SHA-256。

| 名称 | 目录 | 建议用途 | 原始页面尺寸 |
| --- | --- | --- | --- |
| 简洁浅色 | `simple-clear` | 通用讲课、知识点与例题 | 28cm × 21cm |
| 简洁深色 | `simple-dark` | 深色投影、技术分享 | 28cm × 21cm |
| 河流蓝 | `peter-river` | 课程介绍、章节与总结 | 28cm × 21cm |
| 明亮蓝 | `vivid-blue` | 课堂教学、培训讲解 | 33.33cm × 21cm |
| 笔记本 | `notebook` | 学习笔记、练习与复习 | 28cm × 21cm |
| 铅笔 | `pencil` | 学校课堂、活动课程 | 28cm × 21cm |
| 技术办公 | `technical-office` | 理工课程、项目汇报 | 28cm × 21cm |
| 电影胶片 | `cinema` | 影视赏析、媒体课程 | 28cm × 15.75cm |

## 使用状态

8 套模板已转换为 PPTX 并接入项目的生成器，生成窗口可选择模板。转换文件与指纹见 [converted-pptx](converted-pptx/README.md)，母版映射和验证见 [教学适配说明](../../docs/ppt-template-integration.md)。原始 OTP 保留，从上游解包的 ODF 源文件重新封装，封装时将 `mimetype` 作为首个不压缩条目，未修改原 OTP 的设计和 XML 内容。

新增模板时的适配流程：

1. 在 LibreOffice Impress 中打开模板，另存为 PowerPoint `.pptx`，保留原版 OTP。
2. 为封面、知识点、例题、练习、总结等页面建立版式和占位符映射，再接入生成器。
3. 用真实中文课程验证长文本、公式、图片、字体替换及 PowerPoint/WPS 显示效果。原始尺寸不全相同，统一宽屏需要重新检查排版。

原 OTP 已校验 ZIP 完整性、ODF 类型、非空 XML 可解析性及缩略图，空配置 XML 按原样保留。接入后的 8 套 PPTX 已完成几何检查及 PowerPoint 封面/内页渲染检查，具体范围见教学适配说明。

## 来源与授权

来源：[dohliam/libreoffice-impress-templates](https://github.com/dohliam/libreoffice-impress-templates)，固定提交 `7bdfa347c6fbdc784e788f1108f2df848af19d6e`，收录日期 2026-11-01。

选中的 `lo5-design-candidates` 和 `lo4-design-candidates` 集合，其上游 LICENSE 均明确声明所有模板采用 [CC0](https://creativecommons.org/publicdomain/zero/1.0/)，允许商业使用、修改和再分发。原声明、作者列表及仓库 MIT 文本保存在 [licenses](licenses/) 中。**仓库 MIT 许可与模板 CC0 声明分别保存，模板授权依据是对应集合的声明。** 每套模板的固定版本来源见 catalog.json。

未附带字体文件，模板引用的字体不代表已取得字体的商用或嵌入授权。后续新增图片、字体、标识等素材时，应单独核对相应权利；CC0 不授予商标权或保证第三方权利。
