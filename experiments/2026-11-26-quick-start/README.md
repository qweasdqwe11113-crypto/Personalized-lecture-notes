# 一键备课与本课资料保底：建议准确率、真实课程取材、生成对照与引用评审

完成日期：2026-11-26。结果、表格和结论写在 [docs/scale-data.md 的「一键备课与本课资料保底」](../../docs/scale-data.md#一键备课与本课资料保底2026-11-26)，这里只说明每个文件是什么、怎么来的。嵌入模型都是 text-embedding-v4，判断模型 qwen3.8-max。

| 文件 | 内容 | 来源 |
| --- | --- | --- |
| `point-suggest-sweep-qwen.json` | 建议规则的参数比较：评测集 dev 8 节课上 45 条规则里 F1 最高的 8 条，以及 F1 最高那条在 holdout 上的成绩；`sets` 是当时的初稿规则（0.5 / 30 / 6）在三组问题上的逐条结果 | `demo/scale/eval_point_suggest.py --embedder qwen --sweep` |
| `point-suggest-adopted-qwen.json` | 采用的规则（达到最高票 60%、前 15 个片段投票、最多勾 6 个）在 dev、holdout 和真实课程 57 条问题上的逐条结果：重点、答案、勾选、提醒 | `eval_point_suggest.py --embedder qwen --rule 0.6 15 6` |
| `point-suggest-share0.7-qwen.json`、`point-suggest-limit4-qwen.json` | 两条没采用的规则（0.7 / 15 / 6，dev 上 F1 最高但漏勾；0.6 / 15 / 4）的同样结果 | 同上，`--rule 0.7 15 6`、`--rule 0.6 15 4` |
| `point-suggest-keywords-only.json` | 采用的规则在没有嵌入模型、只按关键词时的结果 | `eval_point_suggest.py --embedder none` |
| `anchored-course2-qwen.json` | 默认取用方式在课程 2（大学概率论，97 个知识点）上的 5 个场景：本课资料整份还是节选、每个知识点是否留下资料、学情补充取到哪些片段（各属哪一章）、不设章节边界时会取到什么 | `demo/scale/eval_anchored.py --embedder qwen`；场景的知识点、目标和学情备注**由编码助手编写** |
| `citations.json` | 四次运行（见下）讲义里带 `[M编号]` 的每一行，以及模型的判断和理由 | `demo/scale/judge_citations.py --real` |

真实课程只在只读事务里读出，复制到一次性 schema 里评测，不写课程自己的表；57 条问题来自 `experiments/2026-11-26-rag/course2-questions.json`（编码助手按知识点名称编写，未经教师审核）。评测集的课程是扩展语料，学生和班级都是虚拟的。

讲义原稿、各阶段记录和交给模型的材料在 `.runtime/evalset/` 下（不进仓库）：

| 目录 | 运行 |
| --- | --- |
| `20261126-113956` | dev 的 L1、L2、P1、P2，默认取用方式（`--retrieval anchored`） |
| `20261126-115845` | 同 4 节课，精确 SQL，同一份代码重跑（`--retrieval sql`） |
| `20261126-115845-2` | P1，默认取用方式，加一条指向前面知识点的学情备注（`--note`） |
| `20261126-114644` | holdout 的 L5、L6、P5、P6，用一键备课建课（`--quick`），默认取用方式 |

`20261126-115845-2` 与精确 SQL 那次同一秒启动，原先写进了同一个目录，后完成的那次覆盖了它的 `results.json` 和 `summary.md`；这两个文件按运行时打印的结果恢复（`results.json` 里有 `restored` 说明），讲义、设计、材料和阶段记录是原件。`evalset.py` 已改为同一秒启动的运行各用一个目录。

## 局限

- 每种方式每节课只生成了一份；两次精确 SQL 运行之间在单节课上就差得很多，几项差别分不清是方式带来的还是波动。
- 评测集的课题标题里就有知识点名称，建议知识点的任务偏于字面；holdout 在选定规则前看过一次 F1 最高那条规则的成绩，不再是干净的留出。
- 引用评审的模型与写讲义的模型同一家族，同样的输入两次判断并不完全一致（见 scale-data.md）。
- 还没有教师评分，也没有教师亲手写的「本课重点」。
