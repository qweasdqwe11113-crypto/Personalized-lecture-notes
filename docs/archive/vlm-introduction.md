# VLM 引入路径（2026-10-21 实测修订 · 通道口径已更正）

本文聚焦 **VLM 怎么接进来**，是对 [vlm-rag-plan.md](vlm-rag-plan.md) 第三、五节的细化与修订。
RAG（分块、embedding、pgvector）另见该文。本文未改动任何业务代码。

## 〇、通道口径（用户更正，2026-10-21）

**不使用本机自行部署的模型。** 所有生成、解析、向量化一律走课题组内网服务
（`172.18.36.44:8000/v1`，OpenAI 兼容）。本机 Ollama（`qwen3.5:9b` / `bge-m3` 等）
**不作为任何通道**，此前方案中"本机 VLM 图转文""本地副通道"等内容一并作废。

由此产生的直接后果：**不存在离线端到端验证**。任何真实生成与真实"看图"都必须在内网可达时进行。
本机只能做与模型无关的部分（抽图、规范化、留痕结构、UI、假件测试）。

> 说明：本文写作过程中曾用本机 Ollama 做过 2 次调用，用途**仅为验证 OpenAI 多模态请求格式**
> （结论 2），不是通道验证，也不产生任何讲义或对照结果。按上述口径，后续不再调用本机模型。

## 一、实测结论

| # | 结论 | 证据 / 说明 |
| --- | --- | --- |
| 1 | **OpenAI 多模态请求格式可用** | 以 `content:[{type:'text'},{type:'image_url',image_url:{url:'data:...'}}]` 调用 OpenAI 兼容端点成功，返回 `usage` / `model` / `finish_reason`，与 `qwen.py` 现有解析字段完全一致。vLLM 与 Ollama 的 `/v1` 端点均为 OpenAI 兼容，此格式对课题组服务同样适用 |
| 2 | **`qwen.py` 不需要改造** | 它已按 OpenAI 协议解析 `choices[0].message.content`、usage、finish_reason；多模态只是让同一条消息携带图片，协议层不变 |
| 3 | **内网服务当前不可用** | `curl http://172.18.36.44:8000/v1/models` 返回 **HTTP 502**，响应体 `upstream connect failed: 由于目标计算机积极拒绝，无法连接。(os error 10061)`——网关在监听，上游推理服务未启动或已下线。因此**模型清单无法取得** |

结论 2 的实际意义：**多模态改造的核心只落在 `prompts.py` 一个文件**，把 user content 从字符串换成
`[text, image_url, ...]` 数组。其余不变式（序列化一次、先写 SHA-256 再发相同 UTF-8 字节、
`jobs.py:63` 用 `request_body` 原文重建 `PreparedRequest`）在多模态下**天然成立，无需改动**。

依赖缺口：`Pillow` 未安装（图片规范化编码需要它），`pypdfium2` 未安装（PDF 整页渲染需要）。
`pypdf` 6.20.1 与 `python-docx` 1.2.0 已就绪，可先用 `page.images` 抽原始图流。

## 二、决定性前提：课题组内网有没有视觉模型

按更正后的口径，这一条从"建议"升级为**唯一前提**——没有内网 VL 模型，VLM 就无从引入。
内网可达时执行：

```bash
curl -s http://172.18.36.44:8000/v1/models | head -c 3000
```

判断标准：

- 列表中出现 `qwen3-vl` / `qwen2.5-vl` / `qwen-vl` 一类名字，或某个模型文档标明支持 `image_url`
  → VLM 可引入，按路径 A 推进；
- 只有纯文本模型 → 生成时发图会被拒绝或静默忽略，需要向课题组申请部署 VL 模型，
  代码侧无论如何都先按 A 准备好。

当前 `.env` 的 `QWEN_MODEL=qwen3.8-27b` 从命名看不像是视觉模型，**大概率需要换模型名或申请新端点**。
在拿到清单之前，不得把"生成时模型看图了"写进任何汇报口径。

## 三、两条路径（均已改为纯内网）

| | 路径 A：生成时直接看图 | 路径 B：先用 VLM 把图转成文字 |
| --- | --- | --- |
| 前置条件 | 内网有 VL 模型 | 同样需要内网 VL 模型（只是用在解析阶段） |
| 请求体 | 单图约 0.5 MB base64，一次请求数 MB | 不变大 |
| 引用机制 | 需新增图引用编号（如 `[F1]`） | 沿用 `[M\d+]`，图描述即材料文本 |
| 可核对性 | 教师需对照原图判断 | 描述文本可直接读、可存档 |
| 主要风险 | "看到"不等于读懂 | 二手信息，VLM 描述可能错或丢细节 |
| 是否依赖内网 | 是 | 是（解析与生成都要） |

差别只在 **VL 模型用在哪个环节**：A 用在生成时（你已拍板的方案 B），B 用在入库时。
**推荐以 A 为主实现**，B 作为同一批资产的另一种消费方式，等模型能力确认后再决定是否需要。

## 四、留痕不变式怎么保住（最关键的一条）

现有承诺是"请求体只序列化一次，先写 SHA-256 再发相同 UTF-8 字节，下载的输入可逐字节复算"。
多模态下要保住它，**唯一要点是字节从哪来必须确定**：

1. 图片入库时就**规范化一次**：定长边 + JPEG 固定 quality，得到"发给模型的最终字节"；
2. 以**规范化后字节**的 SHA-256 为指纹存 `material_assets.bytes`（bytea）；
3. 构造 data URI 时**直接读这笔字节做 base64，中途绝不重新编码**。

反例：每次请求时用 Pillow 临时压一遍——不同 Pillow / JPEG 编码器版本会产出不同字节，
SHA-256 自校验当场失效，旧记录也无法复算。这一点没有变通余地。

配套约束：`generation_runs.context`（JSONB）**只放资产描述符**
（asset_id / label / sha256 / width / height / page / 来源材料），**不放 base64**；
否则画像对照与讲义比较会被 MB 级快照拖垮。

## 五、现在能做的 / 必须等内网的

**不依赖模型，本机即可完成并可验证：**

1. `demo/assets.py`：抽图、Pillow 规范化、双 SHA-256（原始 / 规范化）、`material_assets` 读写与去重；
2. `demo/schema.sql` 的增量迁移；
3. `demo/prompts.py` 的多模态消息构造（可用假字节做单元测试，断言顺序稳定、体积预算生效）；
4. `demo/app.py` 的输入面板拆分与缩略图接口；
5. `demo/quality.py` 的提示性检查与教师检查项；
6. 全部测试继续用假件，**不真实调用任何模型**。

**必须内网可达后才能验证：**

- 目标模型是否真的接受 `image_url`（可能返回 400 或忽略图片）；
- 单图 token 开销与耗时（决定是否守得住 150 秒超时与"≤4 张图"预算）；
- 生成的讲义是否真的引用到图（决定 `[F1]` 编号机制是否值得做）。

## 六、代码改造清单

| 文件 | 改动 | 依赖内网 |
| --- | --- | --- |
| `demo/assets.py`（新） | 抽图、Pillow 规范化、双 SHA-256、读写 `material_assets` | 否 | **已落地（2026-10-21）**，见下节 |
| `demo/prompts.py` | user content 由 `str` 扩为内容数组；图片按 `(material_id, page, index)` 稳定排序；硬约束单图 ≤1.2 MB、单次 ≤4 张 | 否 |
| `demo/schema.sql` | 加 `material_assets` / `generation_run_assets`；`generation_runs` 加 `model_channel`（端点标签，**不存地址**）、`retrieval` | 否 |
| `demo/runs.py` | `create_run` 增写 `model_channel` / `retrieval` / `generation_run_assets`；成功与失败事务不变 | 否 |
| `demo/app.py` | `get_context` 装载资产；`generation_input`（现 353–360 行直接回传整个 `request_body`）拆为「摘要视图 + 原文下载」；缩略图接口 | 否 |
| `demo/quality.py` | 增加"本次提供的图是否在正文被提及"的**提示性**检查；图相关教师检查项恒 `needs_review` | 否 |
| `demo/uploads.py` | 抽文之外并行抽图（PDF 用 `page.images`，DOCX 从 zip 取 media） | 否 |
| `demo/static/traces.js`、`templates/index.html` | 输入面板支持缩略图，标注"模型实际看到的图" | 否 |
| `requirements.txt` | 新增 `pillow`（必需）；`pypdfium2`（仅当要做整页渲染） | 否 |
| `demo/qwen.py` | **不改** | — |
| `.env` | 确认 `QWEN_MODEL` 是否为 VL 模型；新增可选 `QWEN_CHANNEL_TAG`（默认 `lab-internal`） | 是 |

## 七、待澄清（影响 RAG 部分，顺带确认）

`bge-m3` embedding 目前只存在于本机 Ollama。既然不用本机模型，RAG 的向量化有两条路：

1. 课题组内网提供 embedding 端点（需确认地址与维度，1024 维的假设可能要改）；
2. 本阶段**先不做向量检索**，只做"按知识点 SQL 取材料 + 关键字/分块"，把 pgvector 推到下一阶段。

这不影响 VLM 部分，但会影响 `material_chunks` 的表设计，建议一并拍板。

## 八、红线口径（写 README 与汇报时照抄）

- 模型"看到了图"不等于读懂了图：图中的读数、单位、趋势、结论仍需教师复核；
- 若采用图转文，该描述由 VLM 生成，是未复核的二手信息，不得作为引用支持性证据；
- 不得把不同模型端点、不同模型名的结果混在同一份对照结论里，`model_channel` 与实际模型名都要留痕；
- 本阶段所有讲义与修订仍是助手草稿，教师检查项不得自动勾选。
