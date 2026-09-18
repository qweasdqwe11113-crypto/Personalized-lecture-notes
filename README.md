# 个性化讲义 Demo

一个本地运行的可丢弃原型，用于验证：PostgreSQL 中的学生知识状态，能否通过明确的教学策略与课程证据，让 Qwen 生成具有实际差异的讲义。

## 启动

在项目根目录运行：

```bash
./run_demo.sh
```

打开 <http://127.0.0.1:5055>。停止时在运行终端按 Ctrl+C。服务仅监听本机，不对外部署。

本机已创建独立数据库 `lecture_demo_prototype`，所有者为 `dax`。初始化会跳过已有样例行，不清空讲义记录，不修改其他数据库。

## 使用

1. 选择小林、小周或小陈（三人都是虚拟学生）。
2. 选择一步方程或两步方程主题，查看知识点状态、诊断依据与课程证据。
3. 可修改教学目标，然后生成讲义。网页显示等待时间；模型请求最长等待 150 秒。
4. 查看正文与引用来源，下载 Markdown，或从“最近生成”打开已保存讲义。

学生切换会改变提示中的教学策略：基础补充、错误纠正或迁移应用。每次生成保留学生状态与材料快照，不自动更新掌握状态。

## 技术组成

- Flask 提供本地页面和 JSON 接口，原生 HTML / CSS / JavaScript 展示结果。
- PostgreSQL + psycopg 保存 3 名学生、3 个知识点、9 条掌握记录、3 条材料及生成讲义。
- SQL 按选中主题的知识点精确查询。没有 pgvector、embedding 或视觉模型。
- 后端调用 `.env` 指定的 Qwen `chat/completions` 接口；浏览器不接触 API 密钥。
- 讲义使用受限 Markdown 展示，模型内容作为文本节点写入，不执行模型返回的 HTML。
- 检查引用编号是否存在；不把编号正确当作内容正确。模型不可达、超时、截断或引用虚构编号时显示错误，不伪造离线生成结果。
- 初期仅允许单次生成并发。数据库记录完整生成；超时或失败不写入成功历史。

## 文件

| 文件 | 内容 |
| --- | --- |
| `app.py` | 查询、教学策略、Qwen 调用、讲义存储和网页 API |
| `schema.sql` | 独立 demo 数据库中的表结构 |
| `seed.json` | 少量学生情境、掌握记录和有来源的课程材料 |
| `init_db.py` | 建库检查、表创建、幂等样例导入 |
| `templates/index.html` | 网页结构 |
| `static/app.js`、`static/style.css` | 网页交互及样式 |
| 项目根目录 `.env` | 本机数据库和模型配置；不提交、不分享 |
| 项目根目录 `.env.example` | 无密钥配置模板 |
| 项目根目录 `requirements.txt` | 已安装版本锁定 |

## 在新环境配置

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 填写自己的模型密钥和 PostgreSQL 连接参数。Unix socket 默认使用当前操作系统用户：

```text
DATABASE_URL=postgresql:///lecture_demo_prototype
```

如使用密码认证，可改为 `postgresql://用户名:密码@127.0.0.1:5432/lecture_demo_prototype`，保留在本地 `.env`。不要将带密码的连接字符串提交到代码库。

若当前 PostgreSQL 角色没有建库权限，由管理员创建一次独立库，再运行启动命令：

```bash
sudo -u postgres createdb --owner="$USER" lecture_demo_prototype
```

无需给应用角色授予超级用户权限。初始化脚本只接受 `lecture_demo_prototype` 这个库名，避免误操作正式数据库。

若 Debian/Ubuntu 缺少 `ensurepip`，可安装系统 `python3-venv`；本机此次使用已有 pip 向独立环境安装，等价步骤如下：

```bash
python3 -m venv --without-pip .venv
python3 -m pip --python .venv/bin/python install -r requirements.txt
```

## 材料来源

知识摘要参考 OpenStax 的 *Prealgebra 2e*，中文重新整理，演算例子自行编写。只涉及下列三个主题，没有抓取整本教材；网页与导出文件都保留原始链接。

- [8.1 等式的加减性质](https://openstax.org/books/prealgebra-2e/pages/8-1-solve-equations-using-the-subtraction-and-addition-properties-of-equality)
- [8.2 等式的乘除性质](https://openstax.org/books/prealgebra-2e/pages/8-2-solve-equations-using-the-division-and-multiplication-properties-of-equality)
- [8.3 两边包含未知数的方程](https://openstax.org/books/prealgebra-2e/pages/8-3-solve-equations-with-variables-and-constants-on-both-sides)

## 当前边界

这是本机演示，不包含登录、多课程上传、自动诊断、出题阅卷、模型训练或公网部署。模型生成讲义需要人工核查；模拟学生数据不能用于声称真实教学效果提升。

待团队体验后判断：不同诊断是否带来有帮助的讲解差异，以及课程证据是否足够。验证通过的部分再纳入正式实现。
