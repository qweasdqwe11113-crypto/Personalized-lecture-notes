# Zhiye · Personalized Lecture, Quiz and Grading Assistant for Teachers

A VLM + RAG prototype for lesson preparation. It generates editable, teacher-facing lecture notes from **course materials**, **student profiles** and **the requirements of the current lesson**, then builds on the same lecture to create matching quizzes, classroom slides and a lesson package, grade student work, and record the grading results back into the student profile.

> Single-machine prototype; all students are synthetic. Lectures, questions, slides and scores are model-generated. Automated checks only verify structure and citation IDs — correctness and teaching value must be reviewed by a teacher.

## Features

| Page | What it does |
| --- | --- |
| Lecture Workbench (讲义工作台) | Pick a lesson of the current course and a target — the whole class (class profile) or one student — set duration, detail level, number of examples and exercises, and adjust the per-topic teaching plan; generate a personalized lecture; read, revise, rewrite single sections, compare and export |
| Writing mode and format (高级设置) | **Outline first, then one model call per section** with its own length quota and one automatic rewrite on failed checks; **LaTeX formulas, tables and program-drawn figures** (probability tree, number line, equation steps, function plot, bar chart); a live preview while the model writes |
| Class batch (批量生成) | Queue the same lesson for up to 60 students; each gets a separately frozen input, run and lecture |
| Lesson prep (授课准备, lecture toolbar) | **Matching quiz** (配套出题, questions follow the lecture's methods and notation), **classroom slides** (生成授课 PPT, editable PPTX with per-slide edits), **lesson package** (导出授课包, teacher handbook + student worksheet + slides) |
| Quizzes (出题) | Generate exercises by course, student level, difficulty and question type; export student and teacher versions |
| Grading (阅卷) | Upload student answers (images / PDF / Word); suggested per-question scores against a generated quiz or an uploaded answer key; record results into the student profile by knowledge point |
| Course / class switcher (课程 › 班级) | Every working page follows the course and class chosen at the top; students are shared across courses, one class per course |
| Courses (课程) | Per course: lesson prep (goals, lesson plan, knowledge points ticked from the course library), course resources shared by all its classes, a **knowledge library** (知识点库), and its classes; uploads are recognized into knowledge points automatically (section headings by rule, fine-grained points by the model), reviewed and confirmed by the teacher, and each point keeps its source pages; images are read by a vision model |
| Classes & profiles (班级与画像) | Class profile (per-point mastery distribution, students to watch, class notes with history), one-click class lecture, class roster and CSV import, class-only resources, and each student's record and learning evidence |
| Records (生成记录) | Lecture, quiz and grading history, background jobs, fixed-input comparison experiments |
| Settings (系统设置) | Configure and test the language, vision and embedding models |

## Core Workflow

```mermaid
flowchart LR
    M["Course materials<br/>lesson plan, documents, images"] --> L["Generate personalized lecture"]
    P["Student profile<br/>mastery and evidence"] --> L
    L --> R["Teacher review and revision"]
    R --> Q["Matching quiz"]
    R --> S["Classroom slides"]
    Q --> K["Lesson package"]
    S --> K
    R --> K
    Q --> G["Grading"]
    G --> E["Teacher-confirmed<br/>profile evidence"]
    E --> P
```

Design principles:

- **Frozen, traceable inputs**: every request is saved with its SHA-256 before it is sent, so failures can be retried byte for byte, and history never changes when materials or profiles change later.
- **Teacher confirmation**: the model never edits a student profile; every evidence entry and mastery change is confirmed by a teacher.
- **Separate vision step**: images are transcribed by a dedicated vision model; the language model only receives the transcribed text.
- **Checks are not correctness**: the program checks structure, length and citation IDs; subject correctness, source support and fit for the student are the teacher's call.
- **Figures are drawn by code**: the model only states a figure's data as JSON; the program validates it (branch probabilities sum to 1, a stated solution satisfies every step) and renders it, so a figure is reproducible from the saved text.
- **Arithmetic is recomputed**: numeric equalities and stated solutions written in a lecture are re-evaluated with exact fractions and disagreements are flagged; anything the parser cannot read is skipped, not guessed.

## Quick Start

### 1. Requirements

- Python 3.12+ and PostgreSQL 13+ (local connection is enough)
- An OpenAI-compatible model service. [Alibaba Cloud Model Studio (百炼)](https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen) is recommended: one API key covers the language, vision and embedding models
- Optional: Node.js (frontend regression tests); Windows with PowerPoint and Ollama (visual review of rendered slides)

### 2. Install and configure

Linux / macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and fill in at least the model keys (`QWEN_API_KEY`, `VLM_API_KEY`) and the database connection `DATABASE_URL`. See [Configuration](#configuration).

### 3. Run

Linux / macOS:

```bash
./run_demo.sh
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -X utf8 demo\init_db.py
.\.venv\Scripts\python.exe -X utf8 demo\app.py
```

Open <http://127.0.0.1:5055>; stop with Ctrl+C. Startup initializes the dedicated database `lecture_demo_prototype`: it is created automatically if missing and your account may create databases, and existing data is never cleared. Without that permission, ask an administrator to create it once:

```bash
sudo -u postgres createdb --owner="$USER" lecture_demo_prototype
```

Restart the service after editing `.env`. In Settings (系统设置), use each model's test button to confirm it can actually be called.

**After `git pull`**, stop the service and start it again the same way. Startup adds new tables and columns and migrates existing records in place, so nothing needs resetting. For example, the course and class structure and each course's knowledge library (知识点库) are built from your existing lessons. Check `requirements.txt` and `.env.example` for new entries. The database, `.runtime/` uploads and `.env` stay on each machine and are never pushed: a fresh setup starts from the seed data (two preset courses, each with a demo class of three fictional students), and records you add stay on your machine.

### 4. Five-minute tour

1. **Lecture Workbench**: choose 「大学概率：贝叶斯推导与先验敏感性（进阶任务）」 and student 「小唐」, then click 「生成个性化讲义」. It takes about a minute.
2. Read the lecture, check the program-check summary above the text, and click citation IDs such as `[M11]` to see the source; to change the text, use 「编辑与审核」 and save it as R1.
3. In the toolbar's 「授课准备」 group, click **配套出题** (10 questions, mixed difficulty) and **生成授课 PPT** (12 slides).
4. Click **导出授课包** to download the ZIP: teacher handbook, student worksheet, slides and manifest.
5. After students answer, open **阅卷**, choose that quiz as the answer source and upload the answers; when grading finishes, confirm each evidence draft under 「记入学生画像」.

To try everything without calling a real model, start the offline sandbox. All model replies are fixed test responses and data goes into a temporary schema, so production data is untouched:

```bash
.venv/bin/python demo/tests/serve_workspace_fixture.py   # then open http://127.0.0.1:5056
```

## Desktop Client (Windows / Linux)

`desktop/` packages the app as an installable program (Electron). It carries its own PostgreSQL 17 and Python 3.12 with every dependency, so a teacher installs one file and needs no command line: on first start it creates a private database in the user's data folder, starts the service on 127.0.0.1 and opens the workspace in its window. Closing the window shuts the service and database down cleanly.

Teachers: see the step-by-step [user guide](docs/user-guide.md) (Chinese), from installation and model setup to daily use. The client's 帮助 → 使用说明 menu opens it.

Two editions are built from the same code and can be installed side by side:

| | 知页 (release) | 知页测试版 (test) |
| --- | --- | --- |
| Starts with | An empty workspace: no courses, classes, students, materials or built-in lessons, and no comparison-experiment page. A getting-started guide on the workbench walks through the first steps | The demonstration courses, classes and fictional students, as in the development setup |
| Data folder | `%LOCALAPPDATA%\Zhiye` · `~/.local/share/zhiye` | `%LOCALAPPDATA%\Zhiye-Test` · `~/.local/share/zhiye-test` |
| Installer | `zhiye-<version>-win-x64.exe` · `zhiye-<version>-linux-x86_64.AppImage` / `.tar.gz` | `zhiye-test-…` with the same suffixes |

Model keys are entered in Settings (系统设置) and saved in the data folder; a button there restarts the service. The 服务 menu restarts the service, opens the page in a browser, makes and restores backups (a copy of the stopped database and workspace; restoring moves the current data aside rather than deleting it), and opens the data folder or the log. Uninstalling or upgrading keeps the data folder. The installers are not code-signed, and there is no automatic update; 知页 → 检查更新 looks up the latest GitHub release. On Ubuntu 24.04 and later, AppArmor blocks Chromium's sandbox for programs without a profile (AppImage, tar.gz, a development checkout), so the client starts without it there instead of failing; for the tar.gz, `sudo chown root:root chrome-sandbox && sudo chmod 4755 chrome-sandbox` in its folder brings the sandbox back.

**Releases** are built by GitHub Actions ([.github/workflows/desktop.yml](.github/workflows/desktop.yml)). Push a tag `vX.Y.Z` that matches the version in `desktop/package.json`: for each system and edition the workflow runs the unit tests, stages the runtime, runs a smoke test (start the database and service, restart, back up, start again) and builds the installers, then drafts a GitHub Release with all six files. Publish the draft by hand once it looks right. Pull requests that touch the client or the service build and test the same packages and attach them as artifacts, without a release; once the workflow is on `main`, *Run workflow* on the Actions page does the same.

**Developing the client** (Node.js 22+; the repository's `.venv` must exist):

```bash
cd desktop
npm install
npm run dev        # release edition, empty, from the repository code (data in ~/.local/share/zhiye-dev)
npm run dev:test   # test edition with the demonstration data
npm test           # client unit tests
```

Building an installer locally, for the current system only:

```bash
cd desktop
node scripts/stage.mjs --flavor release      # portable Python + requirements, service code, PostgreSQL → build/resources
node scripts/smoke.mjs --resources build/resources --edition release
node scripts/dist.mjs --flavor release       # → dist/release/
```

Use `--flavor demo` / `--edition demo` for the test edition. The web setup above is unchanged: `./run_demo.sh` still serves the demonstration data from your own PostgreSQL.

## Configuration

All settings live in the local `.env` (ignored by git); see [.env.example](.env.example).

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection; the database name must be `lecture_demo_prototype`. With a password: `postgresql://user:password@127.0.0.1:5432/lecture_demo_prototype` |
| `QWEN_BASE_URL` / `QWEN_MODEL` / `QWEN_API_KEY` | Language model for lectures, quizzes, slides and grading. Model Studio example: `https://dashscope.aliyuncs.com/compatible-mode/v1`, `qwen3.8-max` |
| `QWEN_TIMEOUT_SECONDS` | Timeout per model call, 30–600 seconds, default 600 |
| `VLM_BASE_URL` / `VLM_MODEL` / `VLM_API_KEY` | Vision model for reading uploaded images and answer sheets. In our tests `qwen3.8-flash` marks unreadable content honestly instead of guessing |
| `QWEN_EMBEDDING_MODEL` / `_BASE_URL` / `_API_KEY` | Embedding model for vector retrieval (optional); URL and key fall back to the language model's |
| `PPT_AESTHETIC_BASE_URL` / `PPT_AESTHETIC_MODEL` | Visual review of rendered slides (optional; Windows + PowerPoint + local Ollama only, skipped elsewhere) |
| `MODEL_CONCURRENCY` | Language-model calls allowed at the same time, 1–8, default 3. Used by sectioned writing and class batches; `1` restores strictly serial calls |
| `QWEN_EMBEDDING_BATCH_SIZE` | Texts per embedding request, default 10 (the Model Studio limit); long documents are embedded in batches |
| `PORT` | Service port, default 5055; listens on localhost only |
| `ZHIYE_EDITION` | `release` starts a new database empty (no demonstration data, preset courses or built-in lessons) and hides the comparison experiments; anything else keeps the demonstration data. Existing data is never deleted. The desktop client sets it |
| `ZHIYE_DATA_DIR` | Folder holding `.env` and `.runtime/`; defaults to the repository root. The desktop client points it at its data folder |

## Project Structure

```text
.
├── run_demo.sh              # Start script: check dependencies → init database → start server
├── requirements.txt         # Python dependencies
├── .env.example             # Configuration template
├── project_map.md           # Module map and data flow (developer docs, Chinese)
├── desktop/                 # Desktop client: Electron shell, bundled PostgreSQL and Python, installers
├── .github/workflows/       # desktop.yml builds the Windows and Linux installers
├── demo/                    # The Flask application
│   ├── app.py               # Entry point: lecture context and generation, exports, module registration
│   ├── edition.py           # Release or test edition, and the data folder (ZHIYE_EDITION, ZHIYE_DATA_DIR)
│   ├── schema.sql           # PostgreSQL schema; init_db.py creates it and loads seed data
│   ├── prompts.py, prompts/ # Prompt assembly and versioned prompt files (lectures, quizzes, slides)
│   ├── quizzes.py           # Quizzes and lecture-linked quizzes
│   ├── presentations.py     # Classroom slides (rendering in ppt_*.py and pptkit_export.py)
│   ├── packages.py          # Lesson package export
│   ├── grading.py           # Grading and evidence drafts
│   ├── profiles.py          # Student profiles and evidence; roster_import.py for CSV import
│   ├── catalog.py           # Lesson-prep copies; uploads.py / vision.py for uploads and image reading
│   ├── courses.py, classes.py # Courses, their classes and rosters, class profiles; legacy data migration
│   ├── knowledge.py         # Course knowledge library; recognizes points in uploads → teacher review → library
│   ├── retrieval.py         # SQL and vector material selection; embed.py embedding client
│   ├── reviews.py           # Revisions and review; quality.py automated checks
│   ├── sectioned.py         # Outline → per-section calls → checks and one rewrite → assembly
│   ├── gate.py, progress.py # Shared model-concurrency gate; live text of running jobs (display only)
│   ├── mathcheck.py         # Recomputes written arithmetic and substitutes stated solutions
│   ├── figures.py           # Validates figure specs and renders SVG/PNG; richdoc.py exports formulas, figures, tables
│   ├── scale/               # Optional chapter-sized corpus, synthetic class, retrieval and generation benchmarks
│   ├── templates/, static/  # Frontend page, scripts and styles (vanilla JS, no build step)
│   └── tests/               # Unit and integration tests; offline sandboxes serve_*_fixture.py
├── docs/                    # Feature documentation and topic notes (Chinese); scale-data.md for the benchmarks
├── tools/ppt-kit/           # In-repo slide layout engine
├── assets/ppt-templates/    # Slide templates and previews
├── experiments/             # 2026-10-12 fixed-input comparison experiment
└── artifacts/               # Sample files for grading
```

Runtime data is written to `.runtime/` (ignored; below `ZHIYE_DATA_DIR` when set): the upload store, grading store, slide-review records and exported lesson packages.

## Tests

```bash
.venv/bin/python -m unittest discover -s demo/tests -v
```

Tests create a temporary schema in the dedicated database and drop it afterwards; they never touch production data or call a real model. With Node.js installed, frontend behaviour regressions run as well. The desktop client has its own unit tests (`cd desktop && npm test`) and a smoke test of the bundled runtime (see [Desktop Client](#desktop-client-windows--linux)). All 378 Python tests and 32 desktop unit tests pass as of 2026-11-13.

## Documentation

The detailed documents are written in Chinese.

| Document | Contents |
| --- | --- |
| [docs/user-guide.md](docs/user-guide.md) | **Teacher's guide**: installing the client, connecting a model, setting up courses, classes and students, then lectures, quizzes, slides, grading, backup and common problems |
| [docs/features.md](docs/features.md) | Behaviour, limits and design details of each page: lectures, profiles, retrieval, quizzes, slides, lesson packages, grading, job queue, experiments |
| [project_map.md](project_map.md) | Module responsibilities, call relationships, data flow and storage — read before changing code |
| [docs/image-lecture-workflow.md](docs/image-lecture-workflow.md) | How image reading feeds into lectures, and its limits |
| [docs/scale-data.md](docs/scale-data.md) | Optional chapter-sized corpus and synthetic class, the retrieval evaluation and the generation benchmark |
| [docs/ppt-template-integration.md](docs/ppt-template-integration.md) | Semantic slide layouts, template adaptation and version compatibility |
| [demo/prompts/README.md](demo/prompts/README.md) | Prompt versions and the actual input structure |
| [docs/archive/](docs/archive/) | Early VLM + RAG planning drafts from 2026-10-21; historical only, not the current implementation |
| [experiments/2026-10-12-bayes](experiments/2026-10-12-bayes/summary.md), [demo/VERIFICATION.md](demo/VERIFICATION.md) | Historical comparison experiment and early engineering verification |

## Status

- **Implemented**: lecture generation and review, matching quizzes, classroom slides, lesson packages, grading into profiles, and batch import of student data. All are covered by automated tests and have been run end to end once with a real model (qwen3.8-max).
- **Added on 2026-11-10, covered by automated tests and exercised in the offline sandbox, not yet run against a real model**: sectioned writing with automatic rewrite, streaming preview, LaTeX formulas and program-drawn figures, arithmetic recomputation, class batches with bounded model concurrency, automatic single retry for invalid quiz and slide output, an installable (PWA) shell, and the scale corpus with its benchmarks.
- **Desktop client (2026-11-13)**: the Linux packages were built and run on Linux: first start on an empty data folder, the getting-started guide, restarting the service, backup, and a clean shutdown. The Windows installer is built and smoke-tested only on GitHub Actions and has not yet been tried on a real Windows computer.
- **Known issues**: single-call lectures came back at about a third of the requested length (1,177 of 3,600–5,400 characters in the one qwen3.8-max run). Sectioned writing is meant to fix this, but whether it does has to be measured with `demo/scale/bench.py --real`. It costs about six model calls per lecture and roughly six times the input tokens. Lectures and quizzes still contain occasional math errors from the model; the arithmetic check only catches numbers that disagree, so teachers still need to check every question.
- **Not yet done**: a with/without-profile comparison on the current version, ablations of the vision and retrieval components with a real embedding model, and independent teacher review. Until then, no claims can be made about saving preparation time or improving learning. Slides still use plain-text formulas and carry no figures; exported HTML keeps formulas as LaTeX source.
