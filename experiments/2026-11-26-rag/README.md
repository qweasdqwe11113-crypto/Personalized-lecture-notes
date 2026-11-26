# 整门课检索：检索评测、生成对照与引用评审

完成日期：2026-11-26。结果、表格和结论写在 [docs/scale-data.md 的「整门课检索」](../../docs/scale-data.md#整门课检索2026-11-26)，这里只说明每个文件是什么、怎么来的。

| 文件 | 内容 | 来源 |
| --- | --- | --- |
| `course2-questions.json` | 课程 2（大学概率论，97 份资料）的 57 条中文检索问题，每条标注所属知识点 | **编码助手按知识点名称编写并标注，未经教师审核** |
| `course2-retrieval-qwen.json` | 上面问题在整门课 622 个片段上的检索结果：各排序方法的 recall@k、MRR，相关性下限的统计，每条问题排在前 3 的片段 | `demo/scale/eval_course_rag.py --embedder qwen`，嵌入模型 text-embedding-v4，数据库只读 |
| `scale-retrieval-qwen.json` | 扩展语料两章（一元一次方程、条件概率与贝叶斯）的检索结果 | `demo/scale/eval_retrieval.py --embedder qwen` |
| `citations.json` | 评测集 dev 的 L1、L2、P1、P2，精确 SQL 与整门课检索两种讲义里带 `[M编号]` 的每一行，以及模型的判断和理由 | `demo/scale/judge_citations.py --real`，判断模型 qwen3.8-max（第二遍；第一遍的问题见 scale-data.md） |
| `blind/` | 同 4 节课在精确 SQL、整门课检索、不提供材料三种方式下的讲义，随机记为 A、B、C；`key.json` 是对应关系，`评分表.md` 给教师填写 | `demo/scale/blind_conditions.py`；`[M编号]` 和「依据」行已删去 |

讲义原稿、各阶段记录和取到的材料在 `.runtime/evalset/` 下（不进仓库）：精确 SQL 用 2026-11-18 的基线 `20261118-142313`，整门课检索是 `20261126-014718`，不提供材料是 `20261126-015357`。评测集的学生和班级都是虚拟的。

## 局限

- 每种方式每节课只生成了一份，四节课的差别分不清是材料带来的还是生成之间的波动。
- 检索问题由编码助手编写，知识点名称又是资料标题，「标题 + 正文」的检索成绩偏高。
- 引用评审的模型与写讲义的模型同一家族，两遍判断一致的约 86%；人工核对由编码助手做，不是教师。
- 还没有教师评分。评分前不要打开 `blind/key.json`。
