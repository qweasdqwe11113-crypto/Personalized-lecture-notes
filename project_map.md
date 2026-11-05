# 项目模块关联图

更新日期：2026-11-05。面向开发者：说明模块职责、调用关系、数据流和存储，修改代码前先确认影响范围。功能行为见 [docs/features.md](docs/features.md)。

## 1. 整体结构

Flask 单进程应用，前端为 `templates/index.html` 加原生 JavaScript 模块（无框架、无构建），通过 HTTP API 访问后端；页面用 URL hash 切换。

```mermaid
flowchart TD
    UI["前端：templates/index.html + static/*.js"] --> APP["app.py：入口与模块注册"]
    APP --> CT["课程与学生<br/>catalog / profiles / roster_import"]
    APP --> UP["资料与图片<br/>uploads / assets / vision"]
    APP --> RT["检索<br/>retrieval / embed"]
    APP --> GEN["讲义生成<br/>lecture_policy / teaching / prompts / jobs"]
    APP --> OUT["审核与派生<br/>reviews / sections / presentations / packages"]
    APP --> EXT["出题 / 阅卷 / 对照实验<br/>quizzes / grading / evaluation"]
    GEN --> LLM["qwen.py：模型调用"]
    OUT --> LLM
    EXT --> LLM
    UP --> VLM["vision.py：视觉模型"]
    CT --> PG[("PostgreSQL")]
    RT --> PG
    GEN --> PG
    OUT --> PG
    EXT --> PG
    UP --> USQ[("uploads.sqlite3")]
    EXT --> GSQ[("grading.sqlite3")]
```

| 入口 | 说明 |
| --- | --- |
| `run_demo.sh` | 检查依赖与 `.env`，运行 `demo/init_db.py`，再启动 `demo/app.py`（默认 127.0.0.1:5055） |
| `demo/app.py` | 讲义上下文与生成、导出、学生接口；创建共享任务队列与生成锁，注册其余模块的路由 |
| `demo/schema.sql`、`demo/init_db.py` | 表结构与增量初始化；种子数据在 `seed.json`、`university_seed.json` |
| `demo/tests/` | 单元与集成测试；`serve_*_fixture.py` 为固定响应的离线沙盒（5056 端口） |

## 2. 页面与前端模块

| 页面（路由） | 前端脚本 | 主要接口 |
| --- | --- | --- |
| 讲义工作台 `#workbench` | `app.js`、`jobs.js`、`teaching.js`、`lecture-workflow.js`、`lesson-settings.js`、`vision.js` | `/api/bootstrap`、`/api/context`、`/api/jobs` |
| 出题 `#quizzes` | `quizzes.js` | `/api/quizzes`（`lecture_id` 为配套出题） |
| 阅卷 `#grading` | `grading.js`、`grading-evidence.js` | `/api/grading`、`/api/grading/<id>/evidence-drafts` |
| 学生画像 `#profiles`、`#profiles/new`、`#profiles/import`、`#profiles/<id>` | `student-roster.js`、`profiles.js`、`roster-import.js` | `/api/students*`、`/api/students/import*` |
| 课程材料 `#materials`、`#materials/<key>` | `course-manager.js` | `/api/course-tasks*` |
| 上传资料 `#uploads` | `uploads.js`、`vision.js` | `/api/uploads*`、`/api/vision/*` |
| 生成记录 `#records/...` | `jobs.js`、`traces.js`、`evaluation.js` | `/api/generation-runs*`、`/api/lecture-experiments*` |
| 系统设置 `#settings` | `model-settings.js` | `/api/health`、`/api/model-settings*` |

讲义工具栏与弹窗：

| 前端脚本 | 后端模块 | 功能 |
| --- | --- | --- |
| `reviews.js` | `reviews.py` | 版本切换、问题记录、编辑与审核、程序检查面板 |
| `section-editor.js` | `sections.py` | 按章节生成候选并另存修订 |
| `traces.js` | `runs.py` | 查看生成输入、失败重试 |
| `profile-comparison.js`、`lecture-comparison.js` | `profiles.py`、`comparisons.py` | 画像对照、讲义比较 |
| `quizzes.js`（配套出题） | `quizzes.py` | 以当前讲义版本出题 |
| `presentations.js` | `presentations.py` | 生成课件、预览、单页微调 |
| `packages.js` | `packages.py` | 导出授课包，显示已有练习与课件数量 |

前端约定：`app.js` 定义 `$`、`api`、`el` 和共享状态（`lastResult`、`busy`、`topics` 等），后加载的脚本直接使用。跨模块通过事件同步：`lecture-version-changed`（讲义或忙碌状态变化）、`workspace-context`（课程上下文变化）、`grading-record-shown`（阅卷记录显示）。修改 DOM ID、脚本顺序或共享函数时，要同时检查依赖它的页面。`usability.css` 最后加载，覆盖前面的样式。

## 3. 后端模块

**课程、学生与资料**

| 模块 | 职责 |
| --- | --- |
| `courses.py` | 预置课程任务与课程类别判断 |
| `catalog.py` | 合并预置与自建备课副本；复制副本；把上传资料收录为知识点材料 |
| `profiles.py` | 学习证据、掌握状态、修改历史与画像对照；证据写入须教师确认 |
| `roster_import.py` | 学生名单与学习证据 CSV 的预览、指纹校验和整批事务导入 |
| `uploads.py`、`assets.py`、`vision.py` | 文件与网址导入、正文与图片提取、图片规范化与指纹、视觉模型识读 |

**检索与讲义生成**

| 模块 | 职责 |
| --- | --- |
| `retrieval.py`、`embed.py` | 分块、索引、余弦排序（pgvector 可替换），嵌入客户端 |
| `lecture_policy.py` | 本次讲解安排、证据提醒、材料与上下文字符预算 |
| `teaching.py` | 授课设置校验与篇幅规则 |
| `prompts.py`、`prompts/` | 组装请求、只序列化一次并计算 SHA-256、标记提示词版本 |
| `jobs.py`、`runs.py` | 有界任务队列、取消与重试、重启恢复；运行记录与讲义同事务保存 |
| `qwen.py`、`model_runtime.py` | OpenAI 兼容调用、错误分类、超时 |
| `quality.py` | 章节、篇幅、引用编号、范围与已知问题词语检查 |

**审核、派生与评测**

| 模块 | 职责 |
| --- | --- |
| `reviews.py` | 修订版本、问题记录、审核事务与并发冲突检查 |
| `sections.py`、`comparisons.py` | 章节候选与采用；讲义与版本比较 |
| `presentations.py` | 课件请求、输出校验、导出、内部视觉审查与单页微调 |
| `ppt_story.py`、`ppt_templates.py`、`pptkit_export.py`、`pptx_export.py` | 语义页面契约、模板白名单与映射、排版适配与 PPTX 导出 |
| `ppt_aesthetics.py`、`render_ppt_slides.ps1` | PowerPoint 渲染与本机 Ollama 视觉审查（仅 Windows） |
| `packages.py` | 授课包：只读已保存记录，组装教师手册、学生练习、课件与清单 |
| `quizzes.py` | 出题、配套出题、题目校验、独立队列、导出；为阅卷提供题组答案与知识点映射 |
| `grading.py` | 阅卷任务、识读与评分校验、证据草稿 |
| `evaluation.py`、`experiment.py` | 当前版本固定对照；历史文件式实验 |
| `rendering.py`、`model_settings.py` | 安全渲染与图片附录；模型配置的查看、测试与保存 |

## 4. 关键流程

### 4.1 生成一份讲义

```mermaid
sequenceDiagram
    participant UI as 工作台
    participant API as app.py
    participant DB as PostgreSQL
    participant Q as jobs.py
    participant M as qwen.py
    UI->>API: GET /api/context
    API->>DB: 读取画像、诊断、材料
    API-->>UI: 上下文、讲解安排、范围检查
    UI->>API: POST /api/jobs
    API->>API: finalize_context 与 prepare_request
    API->>DB: 保存冻结输入、SHA-256 和排队任务
    API->>Q: 加入队列
    Q->>M: 持有生成锁后调用模型
    M-->>Q: 正文与用量，或分类错误
    Q->>DB: 程序检查后保存讲义，或记录失败
    UI->>API: 轮询 GET /api/jobs/{id}
    API-->>UI: 状态与讲义编号
```

要点：输入先冻结再发送；向量模式在知识点范围内检索并记录命中片段；无画像模式不发送个体诊断；图片只以解析文字进入上下文；生成不修改学生状态；「按原输入重试」复用冻结字节，「重新生成」读取当前数据。

### 4.2 教学闭环

```mermaid
flowchart LR
    CSV["CSV 导入<br/>roster_import"] --> PF[("学生画像与证据")]
    PF --> LEC["生成讲义<br/>冻结画像快照"]
    LEC --> QZ["配套出题<br/>quiz-v1+lecture-v1"]
    LEC --> PPT["授课 PPT"]
    LEC --> PKG["授课包<br/>packages"]
    QZ --> PKG
    PPT --> PKG
    QZ --> GR["阅卷<br/>使用题组答案"]
    GR --> DR["证据草稿<br/>按知识点分组"]
    DR --> TC["教师逐条确认"]
    TC --> PF
```

授课包与证据草稿都不调用模型；只有教师确认才写画像，「保留原判断」只记录证据。题组保存题号与知识点的映射，阅卷借此把题目归到知识点。

### 4.3 授课 PPT

```mermaid
flowchart LR
    L["讲义版本"] --> P["presentations.py<br/>请求与输出校验"]
    P --> S["ppt_story.py<br/>语义契约"]
    S --> A["pptkit_export.py<br/>模板适配"]
    T["ppt_templates.py<br/>模板与映射"] --> A
    A --> O["可编辑 PPTX"]
    O --> R{"Windows 且有<br/>PowerPoint 与 Ollama"}
    R -->|是| V["渲染页视觉审查<br/>最多修改两轮"]
    R -->|否| F["交付课件"]
    V --> F
    F --> E["单页微调候选<br/>采用后成为新版本"]
```

新任务冻结模板 ID、模板 SHA-256、映射版本和完整请求；历史课件按原渲染版本导出。单页微调只让模型返回目标页，服务端合并后校验其他页不变。

### 4.4 上传资料进入讲义

```mermaid
flowchart TD
    F["本地文件 / 公开网址"] --> U["uploads.py"]
    U --> TXT["正文：存入上传库"]
    U --> IMG["图片：assets 与 vision 提取保存"]
    TXT --> C["catalog.py<br/>收录到知识点"]
    C --> MT[("materials 表")]
    MT --> SQL["精确 SQL 取材"]
    MT --> IDX["retrieval 与 embed<br/>分块索引"]
    IDX --> VS["向量检索"]
    IMG --> VR["视觉模型识读"]
    VR --> SEL["教师选择解析"]
    SQL --> CTX["生成上下文"]
    VS --> CTX
    SEL --> CTX
```

上传不等于进入课程，需显式收录（材料来源记为 `upload:<id>`）。引用编号：`[M编号]` 课程材料，`[V编号]` 图片解析，`[K编号]` 只用于定位本次讲解安排。

## 5. 任务队列与锁

| 对象 | 说明 |
| --- | --- |
| `TASKS`（`jobs.JobQueue`） | 讲义、章节调整、PPT、固定对照共用；1 个处理、3 个等待 |
| `QUIZZES`（`quizzes.QuizQueue`） | 出题独立队列，最多 4 个未完成任务 |
| 阅卷工作线程 | 每次处理一个阅卷任务，状态存 SQLite |
| `GENERATION_LOCK` | 同进程所有语言模型调用互斥 |
| `vision.VLM_LOCK` | 上传识图、阅卷识读与 PPT 视觉审查互斥 |

服务启动时把未完成的讲义、出题和对照任务标为中断、阅卷任务标为失败，不自动重发模型请求。只适用于本机单进程，不支持多 worker。

## 6. 数据存储

**PostgreSQL**（库名固定为 `lecture_demo_prototype`，表结构见 `demo/schema.sql`）

| 表 | 用途 |
| --- | --- |
| `students`、`knowledge_points`、`course_tasks`、`knowledge_aliases` | 学生、知识点、自建备课副本、副本诊断身份映射 |
| `student_knowledge`、`learning_evidence`、`knowledge_state_history`、`student_profile_history` | 当前掌握状态、证据与历史 |
| `materials`、`material_chunks` | 课程材料与向量分块 |
| `lectures`、`lecture_revisions`、`lecture_issues` | 讲义原稿、修订与问题 |
| `generation_runs`、`generation_jobs` | 冻结输入、运行结果、排队状态（讲义、章节、PPT 共用） |
| `quiz_jobs` | 题组任务、冻结输入与结果 |
| `lecture_experiments`、`lecture_experiment_cells` | 当前版本固定对照 |
| `material_assets`、`generation_run_assets` | 图片资产辅助表（上传图片实际存在 SQLite） |

**本地文件**（`.runtime/`，已被 git 忽略）

| 位置 | 内容 |
| --- | --- |
| `uploads.sqlite3` | 上传原件与正文、提取的图片、图片识读记录 |
| `grading.sqlite3` | 阅卷任务、提取文字、评分结果、所用题组与知识点映射 |
| `ppt-aesthetics/` | PPT 视觉审查记录、送审副本与渲染图 |
| `.env`（项目根目录） | 数据库与模型配置，含密钥 |

## 7. 修改时的关联检查

| 修改内容 | 先看 | 同时检查 |
| --- | --- | --- |
| 页面或导航 | `index.html`、`navigation.js`、对应 JS/CSS | DOM ID、脚本顺序、共享事件、窄屏布局 |
| 备课副本 | `catalog.py`、`course-manager.js` | 知识点身份与别名、材料关联、复制 |
| 学生画像 | `profiles.py`、`roster_import.py` | 证据确认路径、版本冲突、历史快照 |
| 上传与识图 | `uploads.py`、`assets.py`、`vision.py` | SQLite 结构、收录接口、图片指纹 |
| 检索 | `retrieval.py`、`embed.py`、`app.get_context()` | 索引更新、知识点范围、检索快照、无画像模式 |
| 讲义结构与篇幅 | `lecture_policy.py`、`teaching.py`、`prompts.py` | `quality.py`、字符预算、历史版本 |
| 队列与重试 | `jobs.py`、`runs.py`、`jobs.js` | 取消、共享锁、失败状态、重启恢复 |
| 审核与章节调整 | `reviews.py`、`sections.py` | 版本锁、问题记录、引用检查 |
| PPT | `presentations.py`、`ppt_story.py`、`pptkit_export.py` | 模板版本、视觉审查、单页微调、`test_presentations.py` |
| 出题 / 阅卷 / 授课包 | `quizzes.py`、`grading.py`、`packages.py` | 冻结版本与 JSON 模式、题号映射、学生练习不含答案 |
| 模型配置 | `model_settings.py`、`qwen.py`、`vision.py`、`embed.py` | 三个通道的区别、密钥遮蔽、重启生效 |

测试按功能命名为 `demo/tests/test_<功能>.py`，例如 `test_quizzes.py`、`test_packages.py`、`test_teaching_loop.py`、`test_presentations.py`。
