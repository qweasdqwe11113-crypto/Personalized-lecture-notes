# 知课 AI：VLM + RAG 教学助手（简易原型）

这是一个带前端的轻量演示项目，用于展示“资料入库 → RAG 检索 → 个性化讲义/出题 → 来源引用”的基本流程。

## 启动

需要 Node.js 18 或更高版本，无需安装第三方依赖。

```powershell
cd "C:\Users\jiahjq\Desktop\vlm+rag"
npm start
```

浏览器访问：<http://localhost:3000>

## 已实现

- 教学聊天工作台
- 根据学生水平和薄弱项生成不同内容
- 文本、Markdown、JSON、CSV 文件上传与切分
- PDF、PPT、图片等多模态文件的上传占位流程
- 本地 JSON 知识库及简单关键词检索
- 讲义、试题、答案和评分点生成演示
- 中文维基百科联网搜索及自动入库，失败时使用演示资料
- 检索来源展示和知识库管理

## 演示版边界

- 当前生成逻辑是本地规则引擎，尚未调用真实大语言模型。
- 当前检索是关键词匹配，尚未接入 Embedding 和向量数据库。
- PDF、PPT 和图片只保存元数据，尚未启用 OCR/VLM 解析。
- 联网资料会直接加入本地演示库；正式系统应增加可信来源筛选和教师审核。

## 后续接入真实模型的位置

- 将 `server.js` 中的 `buildChatResponse()` 替换为大模型调用。
- 将 `retrieve()` 替换为 pgvector/Qdrant 的向量与关键词混合检索。
- 在 `/api/documents` 入库流程中加入 PDF 解析、OCR、VLM 图片理解和 Embedding。
- 在 `/api/web-search` 中增加来源白名单、正文抓取、可信度评分和人工审核状态。

数据保存在 `data/knowledge.json`，首次启动时自动创建。
