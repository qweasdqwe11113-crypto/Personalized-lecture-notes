# 项目长期笔记：个性化讲义 Demo

## 这是什么

面向教师的本地原型：从专用 PostgreSQL 库 `lecture_demo_prototype` 读虚拟学生画像 →
按知识点精确 SQL 取少量课程材料 → 调课题组 Qwen 生成中文讲义 → 留痕（输入/SHA-256/运行/失败）
→ 教师提问题、另存修订版本并导出。团队 3 人，周报见 `part1.md`。

## 重要约定（写代码时必须遵守）

- **一切可追溯**：请求体只序列化一次，先写 SHA-256 再发相同 UTF-8 字节；失败保留分类与安全正文，
  不把失败包装成成功讲义。
- **不吹能力**：程序只检查标题/五章节/汉字篇幅/引用编号/资料关联，**不证明**数学正确或引用支持。
  教师检查项永远 `needs_review`，助手草稿不得勾"已审核"。
- **课程任务与学生状态正交**：task_level 决定教学任务，student state 只影响讲法，"未知 ≠ 薄弱"。
- 旧输入、旧模板、旧检查版本一律不改；历史报告保留原阈值。
- 密钥 / 认证头 / 服务地址 / 原始异常不进 `generation_runs`。
- **不使用本机自行部署的模型**（2026-10-21 用户明确）：生成 / 解析 / 向量化一律走课题组内网服务。
  本机 Ollama（qwen3.5:9b、bge-m3）不参与任何环节；**没有离线端到端验证**，
  真实生成只能内网可达时做，测试一律用假件、不许真调模型。

## 本机环境实测（2026-10-21）

| 项 | 状态 |
| --- | --- |
| Python venv `.venv`（Windows 用 `.venv/Scripts/python.exe`） | 依赖齐全 |
| PostgreSQL 18.2，`lecture_demo_prototype` | 6 学生 / 6 知识点 / 6 材料 / 1 讲义 / 0 运行 / 18 证据 |
| 实验记录 #8—#21 | 只在 `experiments/2026-10-12-bayes/` 文件里，不在本机库 |
| 课题组 Qwen `172.18.36.44:8000` | **本机不可达**，需内网 |
| pgvector | 本机未装，但 **PG18 可以装**：需 pgvector ≥0.8.0（最新 0.8.7），Windows 走 VS2022 源码编译或手动部署预编译 vector.dll/control/sql 三件套；此前"装不了"结论有误 |
| Ollama | 已运行：qwen3.5:9b(含 vision)、bge-m3(embedding)、qwen2.5-coder 7b/1.5b |

## 架构决策（2026-10-21 用户拍板）

1. **检索层**：商用方向定为 **PostgreSQL + pgvector**（不外挂 sqlite-vec/Chroma，不用 float8[] 暴力余弦上生产）；原型期可先用 float8[] 过渡，接口留一层可替换。
2. **生成通道**：**只用**课题组内网 Qwen，课堂演示用它。本机部署的模型一律不用（见重要约定）。
3. **VLM 深度**：选 **方案 B**——生成时模型直接看图（多模态输入）。
   ~~需要重新设计 SHA-256 留痕方案~~ 已澄清：现有不变式天然支持多模态（`jobs.py` 用库中
   `request_body` 原文重建 `PreparedRequest`），改造核心只在 `prompts.py` 一处；
   真正的硬约束是**图片必须入库时规范化一次并固化字节**，中途不得重新编码。
   **前提未确认**：内网需有 VL 模型；当前 `.env` 的 `qwen3.8-27b` 不像视觉模型，
   且 `/v1/models` 探测返回 502（网关在、上游未起），清单拿不到。
4. **embedding 来源未定**：bge-m3 只在本机，按新口径不可用。需确认内网有无 embedding 端点；
   没有则本阶段只做 SQL + 分块，pgvector 推到下一阶段。
5. **资产层与检索层分工**：文字走 `material_chunks`（检索层，另一条线在做），图片走
   `material_assets`（资产层，`demo/assets.py` 已落地）。图片**入库即规范化一次并固化字节**
   （固定阶梯 (1024,85)→(896,82)→(768,80)→(640,78)，取第一个满足 1.2MB 预算的档），
   之后只读库中 bytea 原文、绝不重新编码 —— 这是"请求体 SHA-256 可复算"的唯一前提。
   换规范化配方必须 bump `NORMALIZE_VERSION`。

## 已知缺口 / 待办线索

- README 与 demo/README.md 都链接 `TODO.md`，但该文件**不存在**。
- 最新提交新增 `demo/uploads.py`：上传资料存 `.runtime/uploads.sqlite3`，
  **目前不参与讲义生成** —— 这是接 RAG 的天然入口。
- 目录名 `vlm+rag` + 周报第三节未实现清单（PDF/图片解析、embedding/pgvector/语义检索、
  OCR 与 VLM 解析图表）指向下一阶段就是 VLM + RAG。

## 常用命令

```bash
./run_demo.sh                                          # 启动 127.0.0.1:5055
.venv/Scripts/python -m unittest discover -s demo/tests -v   # 105 项测试
.venv/Scripts/python demo/tests/serve_course_fixture.py      # 5056 隔离体验
```
