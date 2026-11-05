# VLM + RAG 接入方案（历史规划稿）

> 2026-10-25：本文保留方案讨论历史，不代表当前实现或新增执行授权。当前代码采用独立视觉模型识读、语言模型接收解析文字；实际流程以根目录README和image-lecture-workflow.md为准。下文“直接看图”等为历史设想。

制定日期：2026-10-21。规划稿，随实施推进更新；代码部分的进度见第六节里程碑。

> **通道口径已更正（2026-10-21）：不使用本机自行部署的模型**，全部走课题组内网服务。
> 本文残留的"本机 Ollama 副通道 / 本机 embedding"内容已作废，以
> [vlm-introduction.md](vlm-introduction.md) 为准：OpenAI 多模态请求格式可用、`qwen.py` 无需改造；
> 唯一前提是内网需有 VL 模型（当前 `/v1/models` 返回 502，尚未确认）。
已确认的三条选型：① 检索层目标 pgvector、原型期用 `float8[]` 过渡并留可替换接口；② 生成主通道坚持课题组内网 Qwen；③ VLM 采用"直接看图"，生成时模型读取图片本身。

## 一、目标与边界

在现有"按知识点精确 SQL 取材料"的基础上，增加两件事：

1. **资料入库**：上传的 PDF / DOCX / 网页正文及其中的图片成为可检索单元；
2. **多模态生成**：讲义生成时，模型除文本片段外还能看到图片。

明确不做：不在本轮声称"检索到的片段支持结论"、不声称"向量retrieval 比 SQL 一定更准"、不声称"模型看图了就等于正确理解了图"、不做教师审核自动化、不引入多 worker。程序检查与教师复核的分工边界沿用现有 `quality.py` 口径。

## 二、选型落地形态

| 环节 | 选型 | 理由 | 代价 / 风险 |
| --- | --- | --- | --- |
| 向量存储 | PostgreSQL。先 `material_chunks.embedding float8[]` 暴力余弦，后续 `CREATE EXTENSION vector` 切 `<=>` 与 HNSW | 沿用现有唯一数据源与事务；12 张表的模式和备份不用拆 | **本机 PG18.2 目前没有任何 vector 扩展**（实测 `pg_available_extensions` 里没有 vector）。已查证：pgvector 0.8.0+ 开始支持 PG18（最新 0.8.7，2026-10-01），Windows 可走 VS2022 源码编译（`nmake`）或手动部署预编译 `vector.dll` + `vector.control` + `vector--*.sql` 三件套；要用 HNSW 索引建议 ≥0.8.4（0.8.3 修过索引损坏）。M0 阶段要把这条路实际走通，走不通就长期停在 `float8[]` |
| 向量维度 | 1024 | 暂按 1024 预留，等内网 embedding 端点确认后可能调整 | 换 embedding 模型就要全量重算；故 `material_chunks` 必须记 `embed_model` 列 |
| Embedding 来源 | **待定，且不得使用本机 Ollama `bge-m3`**（用户口径更正） | 需课题组内网提供 embedding 端点；若没有，本阶段先不做向量检索，只做 SQL + 分块 | 维度、模型名与可用性均未确认。若走内网，embedding 与生成同属内网，留痕须记实际模型名 |
| 召回 / 排序 | `demo/retrieval.py` 抽象层，`search(conn, query, knowledge_ids, k)` | 候选召回（SQL）与相似度排序（后端）分开，将来只换后端实现 | 过渡期 Python 侧算余弦；数据量小（预计 < 1 万片段）足够 |
| 生成主通道 | 课题组内网 Qwen（`qwen.py` 现状不变） | 演示口径与周报口径一致 | 本机不可达时无法端到端验证真实生成 |
| 副通道 | **取消**（原"本机 Ollama 副通道"方案已于 2026-10-21 作废） | 用户明确不使用本机部署的模型；不存在离线端到端验证 | 所有真实生成必须内网可达时进行；本机只做与模型无关的部分（抽图、规范化、留痕结构、UI、假件测试） |
| PDF 页面渲染 | 建议 `pypdfium2`（BSD-3，自带 pdfium） | 无需系统 poppler；许可对商业/专利友好 | 新增依赖。PyMuPDF 是 AGPL，本项目背景不建议直接引入 |
| PDF 内嵌图提取 | 先用现有 `pypdf`（`page.images`） | 零新增依赖拿到原始图流 | 只能拿编码流，部分格式（JBIG2/CCITT）需要另行解码 |
| 图片处理 | `Pillow` | 规范化编码（缩放 / JPEG）是"确定性留痕"的前提 | 需新增依赖；本机目前**没有** Pillow |

## 三、关键技术难点与解法

### 1. 多模态下如何保住"序列化一次、SHA-256 可复算"

现有不变式（`prompts.py` / `runs.py` / `jobs.py`）在多模态下**几乎不用改**：请求体仍然只序列化一次，仍然先写 SHA-256 再发相同 UTF-8 字节，`jobs.retry` 仍是用库中 `request_body` 原文重建 `PreparedRequest`。难点只在于字节从哪来必须确定：

- 图片入库时先**规范化**：定长边、JPEG 编码、固定 quality，得到"发给模型的最终字节"；
- 以**规范化后字节**的 SHA-256 为资产指纹并存其 bytes；
- 构造 data URI 时直接读这笔字节做 base64，**中途不再重新编码**。

这样同一份输入任何时候重算，得到的 request_body 与 SHA-256 完全一致。反之，如果每次临时用 Pillow 重压一遍，不同 Pillow/JPEG 编码器版本可能产出不同字节，输入一致性与 SHA-256 自校验会当场失效。

### 2. context 快照不能装图片字节

`generation_runs.context` 是 JSONB，用来保存"生成当时的画像与材料快照"。图片 base64 必须**只**进 `request_body`，context 里只放引用描述符：`asset_id / label / sha256 / width / height / page`。否则 context 会涨到 MB 级，画像对照（既有 `compare_lecture_profile`）和讲义比较都受影响。

### 3. 重做“查看生成输入”面板

现有 `/api/generation-runs/<id>/input` 直接把整段 request_body 当附件返回。多模态后它可能达到数 MB，且满屏 base64 不可读。建议拆成两层：

- 摘要视图：把 text 段原样显示 + 图片以缩略图渲染（从 asset 读），逐张标注 sha256、字节数、来源材料/页号；
- 原始下载：仍返回完整 request_body 原文，保持"下载字节 = 实际发送字节、SHA-256 可逐字节核对"这一既有承诺不被削弱。

### 4. 图片体积预算（建议在 `prompts.py` 中硬约束）

单图 ≤ 1.2 MB、单次请求 ≤ 4 张图、图片一律排在 text 之后且按 (material_id, page, index) 稳定排序。理由：Qwen 侧上下文上限与耗时均不确定，而既有的 `MAX_CONTENT_LENGTH` 与 150 秒超时不应因为加了图就无声放宽。

## 四、数据模型草案（均为幂等增量迁移，沿用现有 `IF NOT EXISTS` 风格）

```sql
CREATE TABLE IF NOT EXISTS material_chunks (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  material_id integer REFERENCES materials(id),
  upload_id text,                       -- 来自 .runtime/uploads.sqlite3；与 material_id 二选一
  knowledge_id integer REFERENCES knowledge_points(id),
  chunk_index integer NOT NULL,
  content text NOT NULL,
  char_length integer NOT NULL,
  source_excerpt_sha256 text NOT NULL,
  embed_model text NOT NULL,
  embedding float8[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (length(embedding) = 1024)
);
CREATE INDEX IF NOT EXISTS material_chunks_knowledge
  ON material_chunks(knowledge_id, chunk_index);

CREATE TABLE IF NOT EXISTS material_assets (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  material_id integer REFERENCES materials(id),
  upload_id text,
  kind text NOT NULL CHECK (kind IN ('figure','page_scan','diagram')),
  label text NOT NULL,
  page_no integer,
  source_sha256 text NOT NULL,          -- 原始抽取字节
  normalized_sha256 text NOT NULL,      -- 规范化后字节，模型实际看到的
  mime text NOT NULL,
  width integer NOT NULL,
  height integer NOT NULL,
  byte_size integer NOT NULL,
  bytes bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (byte_size <= 1258291)
);
CREATE UNIQUE INDEX IF NOT EXISTS material_assets_norm
  ON material_assets(normalized_sha256);

CREATE TABLE IF NOT EXISTS generation_run_assets (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES generation_runs(id) ON DELETE CASCADE,
  asset_id bigint NOT NULL REFERENCES material_assets(id),
  ordinal integer NOT NULL,
  normalized_sha256 text NOT NULL,
  byte_size integer NOT NULL,
  UNIQUE(run_id, ordinal)
);

ALTER TABLE generation_runs ADD COLUMN IF NOT EXISTS model_channel text;
ALTER TABLE generation_runs ADD COLUMN IF NOT EXISTS retrieval jsonb NOT NULL DEFAULT '{}';
```

要点：`generation_runs.model_channel` 用来区分内网 Qwen 与本地副通道，避免口径混淆；`retrieval` 记本次实际召回的 chunk 列表、相似度与后端，便于事后核查"讲义为什么这么写"。`generation_runs`/`lectures` 原有内容一行不改。

## 五、代码改造点清单

| 文件 | 改动 |
| --- | --- |
| `demo/retrieval.py`（新） | `search()` / `index_material()` / `embedder` 接口；过渡后端 `ArrayBackend`，将来 `PgVectorBackend` 同签名替换 |
| `demo/assets.py`（新） | 图片抽取、规范化编码（Pillow）、sha256、`material_assets` 读写 |
| `demo/embed.py`（新） | Ollama `/api/embed` 封装，带 `embed_model` 版本标记与失败分类（沿用 `ModelError` 风格） |
| `demo/prompts.py` | `prepare_request` 的 user content 由 str 扩为多模态数组；图片按稳定顺序追加；不变式不变 |
| `demo/app.py` | `get_context` 增加 chunk / asset 装载；`/api/jobs`、`/api/generate` 接受"本次纳入哪些图"；新增资产查看 / 缩略图接口；`generation_input` 拆摘要与原始下载 |
| `demo/qwen.py` | 抽出 base_url / key 的解析，使副通道可注入；`request_metadata` 增 `model_channel` |
| `demo/runs.py` | `create_run` 增写 `model_channel` / `retrieval` 与 `generation_run_assets`；成功/失败事务不变 |
| `demo/quality.py` | 增-only 检查：本次提供的图是否在正文被（显式或隐式）使用、召回片段是否被引用；`teacher_review` 增"图表读数、趋势与结论"恒为 `needs_review` |
| `demo/uploads.py` | 抽文之外并行抽图；把 `uploads.sqlite3` 里的资料可转到 `materials` + `material_chunks` |
| `demo/experiments` 相关（`experiment.py`） | 冻结 manifest 要同时冻结"本次用了哪些图与哪些 chunk"，否则新一轮对照不可复现 |
| `demo/static/traces.js`、`templates/index.html` | 输入查看面板支持图片预览与 thumbnails；明确标注"这是模型实际看到的图" |
| `requirements.txt` | 新增 `pillow`、`pypdfium2`（仅当确认要做 PDF 页面渲染） |
| `demo/tests/` | 新增检索与多模态测试；embedding / 模型继续用假件，**不得在测试里真实调用内网或本地模型** |

## 六、里程碑（按"是否依赖内网"分组，宿舍也能推进前几步）

| 里程碑 | 内容 | 依赖内网 | 完成判据 |
| --- | --- | --- | --- |
| M0 环境探测 | 确认 PG18 能否装 pgvector；若不能，定夺是留 `float8[]` 还是换实例 | 否 | 书面结论 + 一条可执行的建索引 SQL（或明确的拒绝理由） |
| M1 资产层 | 图片抽取与规范化入库，`material_assets` 落地 | 否 | 一份含图的测试 PDF 被抽出 ≥1 张图，重复入库因 `normalized_sha256` 去重 |
| **M2 检索层** | ✅ **已完成 2026-10-21**：`demo/retrieval.py` + `demo/embed.py` + `material_chunks` 表 + 网页开关；嵌入走 `QwenEmbedder`（课题组 `/embeddings`，接受 OpenAI `data[].embedding` 与 Ollama `embeddings` 两种返回），排序走 `ArrayBackend`；额外实现了 `adopt_content()` 把上传资料转正为课程材料 | 否 | ✅ 125 项测试通过（新增 15 项）；同一查询按分值 / 材料号 / 块号三级稳定排序，Top-k 可复现；默认仍是精确 SQL，旧路径未受影响 |
| M3 多模态输入构造 | context 扩展、留痕、UI 缩略图 | 否 | 假件测试通过：消息顺序稳定、体积预算生效、SHA-256 可复算；**不调用任何真实模型** |
| M4 真实多模态生成 | 内网可达后用内网 VL 模型跑通带图生成 | 是 | 成功生成带图讲义，输入下载逐字节复算 SHA-256 一致，`model_channel` 与实际模型名留痕正确 |
| M5 小对照与口径同步 | 新实验目录：有图 / 无图 × 有检索 / 原 SQL，沿用现有"冻结 + 重复 + 不挑选结果"协议 | 是 | 实验 summary 与 README / `demo/prompts/README.md` / VERIFICATION 同步更新 |

## 七、红线（写 README 与汇报时必须照抄的口径）

- 检索相似度不等于语义蕴含，召回不等于被正确引用；
- 模型"看到了图"不等于读懂了图，图中的读数、单位、趋势仍需教师复核；
- 不同端点、不同模型名的结果不得混在同一份对照结论里，`model_channel` 与实际模型名都要留痕；
- 不使用本机部署的模型，所有生成与解析均走课题组内网服务，不存在"离线演示通道"这一说法；
- 这一阶段所有讲义与修订仍是助手草稿，教师检查项不得自动勾选。

## 八、待你拍板（三个前置决策）

1. **图片来源范围**：只做"课程 PDF / DOCX 自带的插图"，还是也做"整页扫描件"。后者需要 OCR，工作量与噪点明显不同；
2. **PG18 没有 pgvector 怎么走**：留在本实例继续 `float8[]`，还是我顺便准备一个降级的替代存储（如 SQLite + sqlite-vec）做过渡；
3. ~~是否允许把本地 Ollama 视觉模型作为"离线演示通道"~~ —— **已于 2026-10-21 否决**：不使用本机部署的模型，
   本机不做端到端演示。由此产生的新问题：**课题组内网是否有 embedding 端点**（bge-m3 只在本机，
   按新口径不可用）。若没有，本阶段是否接受"只做 SQL + 分块、不做向量检索"，把 pgvector 推到下一阶段。

   > **2026-10-21 实测**：本机对该地址的 `POST /v1/embeddings` 返回 **HTTP 502**，而此前对该主机的
   > 直连 socket 是 ConnectionRefused。**这两者都不足以判断服务端真实情况**——502 可能来自本机到内网之间的
   > 代理或网关，不一定是服务自己的响应。已按"不可知"处理：代码不承诺可用性，`/api/health` 只报配置是否齐全，
   > 首次真实调用之前不做任何可用性结论。**回到内网后第一件事就是打一次 `/v1/models` 与 `/v1/embeddings`
   > 确认是否真的提供嵌入接口**；若不提供，`QwenEmbedder` 保持原样不用即可，检索层退回精确 SQL，不影响其他部分。
