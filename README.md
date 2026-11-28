# Zhiye · Personalized Lecture, Quiz and Grading Assistant for Teachers

A VLM + RAG prototype for lesson preparation. It generates editable, teacher-facing lecture notes from **course materials**, **student profiles** and **the requirements of the current lesson**, then builds on the same lecture to create matching quizzes, classroom slides and a lesson package, grade student work, and record the grading results back into the student profile.

> Single-machine prototype; all students are synthetic. Lectures, questions, slides and scores are model-generated. Automated checks only verify structure and citation IDs — correctness and teaching value must be reviewed by a teacher.

## Features

| Page | What it does |
| --- | --- |
| Lecture Workbench (讲义工作台) | Pick a lesson of the current course and a target — the whole class (class profile) or one student — set duration, detail level, number of examples and exercises, and adjust the per-topic teaching plan; generate a personalized lecture; read, revise, rewrite single sections, compare and export |
| Writing mode and format (高级设置) | **Lesson design first (default)**: one model call returns a structured lesson design (objectives, timed segments, one bank of examples and exercises); the program checks it and asks for one repair if needed, the teacher confirms or edits it, then each teaching segment is written by its own call. The student worksheet, teacher's book and slides are laid out from the same design. Outline-then-sections and single-call writing remain as older modes. **LaTeX formulas, tables and program-drawn figures** (probability tree, number line, equation steps, function plot, bar chart); a live preview while the model writes |
| Class batch (批量生成) | Queue the same lesson for up to 60 students; each gets a separately frozen input, run and lecture |
| Lesson prep (授课准备, lecture toolbar) | **Matching quiz** (配套出题, questions follow the lecture's methods and notation), **classroom slides** (生成授课 PPT, editable PPTX with per-slide edits), **lesson package** (导出授课包, teacher handbook + student worksheet + slides) |
| Quizzes (出题) | Generate exercises by course, student level, difficulty and question type; export student and teacher versions |
| Grading (阅卷) | Upload student answers (images / PDF / Word); suggested per-question scores against a generated quiz or an uploaded answer key; record results into the student profile by knowledge point |
| Course / class switcher (课程 › 班级) | Every working page follows the course and class chosen at the top; students are shared across courses, one class per course |
| Courses (课程) | Per course: lesson prep (goals, lesson plan, knowledge points ticked from the course library), course resources shared by all its classes, a **knowledge library** (知识点库), and its classes; uploads are recognized into knowledge points automatically (section headings by rule, fine-grained points by the model), reviewed and confirmed by the teacher, and each point keeps its source pages; images are read by a vision model |
| Classes & profiles (班级与画像) | Class profile (per-point mastery distribution, students to watch, class notes with history), one-click class lecture, class roster and CSV import, class-only resources, and each student's record and learning evidence |
| Records (生成记录) | Lecture, quiz and grading history, background jobs, fixed-input comparison experiments |
| Settings (系统设置) | Configure and test the language, vision and embedding models. Quick setup finds model servers running on this computer (Ollama, LM Studio, vLLM, llama.cpp …) with their ports and models, or reads a pasted provider configuration (one-click import links from Cherry Studio, CC Switch, NextChat or LobeChat, curl commands, environment variables, JSON) and lists the service's models |

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

Only the release edition is published:

| | 知页 |
| --- | --- |
| Starts with | An empty workspace: no courses, classes, students, materials or built-in lessons, and no comparison-experiment page. On first start a short tour highlights the main areas, and a getting-started guide on the workbench walks through the first steps |
| Data folder | `%LOCALAPPDATA%\Zhiye` · `~/.local/share/zhiye` |
| Installer | `zhiye-<version>-win-x64.exe` · `zhiye-<version>-linux-amd64.deb` (Ubuntu / Debian) · `zhiye-<version>-linux-x86_64.AppImage` / `zhiye-<version>-linux-x64.tar.gz` |
| Linux package | `zhiye`, installed in `/opt/知页`, started as `zhiye` |

Model keys are entered in Settings (系统设置) and saved in the data folder; a button there restarts the service. The 服务 menu restarts the service, opens the page in a browser, makes and restores backups (a copy of the stopped database and workspace; restoring moves the current data aside rather than deleting it), and opens the data folder or the log. Uninstalling or upgrading keeps the data folder. The installers are not code-signed, and updates are not installed automatically: after each start the client looks up the latest GitHub release in the background and offers the download page only when a newer version is out (with 跳过这个版本 to stop being asked about it); 知页 → 检查更新 checks on demand and 知页 → 启动时检查更新 turns the start-up check off. On Ubuntu 24.04 and later, AppArmor blocks Chromium's sandbox for programs without a profile (AppImage, tar.gz, a development checkout), so the client starts without it there instead of failing. The `.deb` (`sudo apt install ./zhiye-<version>-linux-amd64.deb`) keeps the sandbox on: its install script makes `chrome-sandbox` root-owned with the setuid bit and loads an AppArmor profile (`/etc/apparmor.d/zhiye`) that allows user namespaces, so Chromium uses its namespace sandbox where the profile is loaded and the setuid helper otherwise ([desktop/scripts/deb-after-install.tpl](desktop/scripts/deb-after-install.tpl)). For the tar.gz, `sudo chown root:root chrome-sandbox && sudo chmod 4755 chrome-sandbox` in its folder brings the sandbox back.

**Releases** are built by GitHub Actions ([.github/workflows/desktop.yml](.github/workflows/desktop.yml)). Push a tag `vX.Y.Z` that matches the version in `desktop/package.json`: for each system the workflow runs the unit tests, stages the runtime, runs a smoke test (start the database and service, restart, back up, start again) and builds the installers, then drafts a GitHub Release with the four files (the Windows `.exe`; an AppImage, a tar.gz and a `.deb` on Linux). Only the release edition is built. Publish the draft by hand once it looks right. Pull requests that touch the client or the service build and test the same packages and attach them as artifacts, without a release; once the workflow is on `main`, *Run workflow* on the Actions page does the same.

**Developing the client** (Node.js 22+; the repository's `.venv` must exist):

```bash
cd desktop
npm install
npm run dev        # empty, from the repository code (data in ~/.local/share/zhiye-dev)
npm test           # client unit tests
```

Building an installer locally, for the current system only:

```bash
cd desktop
node scripts/stage.mjs      # portable Python + requirements, service code, PostgreSQL → build/resources
node scripts/smoke.mjs --resources build/resources
node scripts/dist.mjs       # → dist/ (Linux: AppImage, tar.gz and .deb)
```

The web setup above is unchanged: `./run_demo.sh` still serves the demonstration data from your own PostgreSQL.

## Configuration

All settings live in the local `.env` (ignored by git); see [.env.example](.env.example).

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection; the database name must be `lecture_demo_prototype`. With a password: `postgresql://user:password@127.0.0.1:5432/lecture_demo_prototype` |
| `QWEN_BASE_URL` / `QWEN_MODEL` / `QWEN_API_KEY` | Language model for lectures, quizzes, slides and grading. Model Studio example: `https://dashscope.aliyuncs.com/compatible-mode/v1`, `qwen3.8-max` |
| `QWEN_TIMEOUT_SECONDS` | Timeout per model call, 30–600 seconds, default 600 |
| `VLM_BASE_URL` / `VLM_MODEL` / `VLM_API_KEY` | Vision model for reading uploaded images and answer sheets. In our tests `qwen3.8-flash` marks unreadable content honestly instead of guessing |
| `QWEN_EMBEDDING_MODEL` / `_BASE_URL` / `_API_KEY` | Embedding model for retrieval: the one-click lesson's point suggestions, the learner passages of the default material mode, whole-course and vector retrieval (optional; without it these rank by keywords only); URL and key fall back to the language model's |
| `PPT_AESTHETIC_BASE_URL` / `PPT_AESTHETIC_MODEL` | Visual review of rendered slides (optional; Windows + PowerPoint + local Ollama only, skipped elsewhere) |
| `MODEL_CONCURRENCY` | Language-model calls allowed at the same time, 1–8, default 3. Used by sectioned writing and class batches; `1` restores strictly serial calls |
| `MODEL_RETRIES` | How many times a model request is sent again after a rate limit (HTTP 429), a busy service (500/502/503/504) or a failed connection, when nothing of the reply has arrived; 0–3, default 2. Waits as long as `Retry-After` says (at most 60 s), otherwise 5, 20 and 40 s. A timeout is not retried |
| `QWEN_EMBEDDING_BATCH_SIZE` | Texts per embedding request, default 10 (the Model Studio limit); long documents are embedded in batches |
| `PORT` | Service port, default 5055; listens on localhost only |
| `ZHIYE_EDITION` | `release` starts a new database empty (no demonstration data, preset courses or built-in lessons) and hides the comparison experiments. The desktop client always sets it; the development setup and the tests leave it unset and keep the demonstration data. Existing data is never deleted |
| `ZHIYE_DATA_DIR` | Folder holding `.env` and `.runtime/`; defaults to the repository root. The desktop client points it at its data folder |

## Project Structure

```text
.
├── run_demo.sh              # Start script: check dependencies → init database → start server
├── requirements.txt         # Python dependencies
├── requirements-dev.txt     # Development-only tools (ruff); ruff.toml holds the lint rules
├── .env.example             # Configuration template
├── LICENSE                  # MIT license for the project's own code and documentation
├── project_map.md           # Module map and data flow (developer docs, Chinese)
├── desktop/                 # Desktop client: Electron shell, bundled PostgreSQL and Python, installers
├── .github/workflows/       # tests.yml lints and runs the Python tests; desktop.yml builds the Windows and Linux installers
├── demo/                    # The Flask application
│   ├── app.py               # Entry point: lecture context and generation, exports, module registration
│   ├── edition.py           # Desktop client or development setup, and the data folder (ZHIYE_EDITION, ZHIYE_DATA_DIR)
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
│   ├── retrieval.py         # Default anchored mode, whole-course hybrid and vector retrieval, point suggestions; embed.py embedding client; quick_start.py one-click lesson
│   ├── reviews.py           # Revisions and review; quality.py automated checks
│   ├── sectioned.py         # Outline → per-section calls → checks and one rewrite → assembly
│   ├── gate.py, progress.py # Shared model-concurrency gate; live text of running jobs (display only)
│   ├── mathcheck.py         # Recomputes written arithmetic and substitutes stated solutions
│   ├── figures.py           # Validates figure specs and renders SVG/PNG; richdoc.py exports formulas, figures, tables
│   ├── scale/               # Optional chapter-sized corpus, synthetic class, retrieval and generation benchmarks, evaluation set
│   ├── templates/, static/  # Frontend page, scripts and styles (vanilla JS, no build step)
│   └── tests/               # Unit and integration tests; offline sandboxes serve_*_fixture.py
├── docs/                    # Top-level design (design.md), feature documentation and topic notes (Chinese); scale-data.md for the benchmarks
├── tools/ppt-kit/           # In-repo slide layout engine
├── assets/ppt-templates/    # Slide templates and previews
├── experiments/             # 2026-10-12 fixed-input comparison experiment; 2026-11-26 retrieval and one-click lesson evaluations
└── artifacts/               # Sample files for grading
```

Runtime data is written to `.runtime/` (ignored; below `ZHIYE_DATA_DIR` when set): the upload store, grading store, slide-review records and exported lesson packages.

## Tests

```bash
.venv/bin/python -m unittest discover -s demo/tests -v
```

Tests create a temporary schema in the dedicated database and drop it afterwards; they never touch production data or call a real model. With Node.js installed, frontend behaviour regressions run as well. The desktop client has its own unit tests (`cd desktop && npm test`) and a smoke test of the bundled runtime (see [Desktop Client](#desktop-client-windows--linux)).

GitHub Actions runs both: [.github/workflows/tests.yml](.github/workflows/tests.yml) lints and runs the Python tests against a PostgreSQL service container on every push to `main` and on pull requests that touch the service; [.github/workflows/desktop.yml](.github/workflows/desktop.yml) runs the client's tests. The lint is `ruff check demo tools` (syntax errors, unused imports, undefined names; no formatting), with the version pinned in [requirements-dev.txt](requirements-dev.txt), which is kept apart from `requirements.txt` because that file is installed into the desktop packages.

## Documentation

The detailed documents are written in Chinese.

| Document | Contents |
| --- | --- |
| [docs/design.md](docs/design.md) | **Top-level design**: how the topic (VLM + RAG for personalised lectures, quizzes and grading) maps onto the system, the design principles, the architecture, how retrieval, image reading and personalisation each work, the key decisions in order with their reasons, the evidence so far and the open problems |
| [docs/user-guide.md](docs/user-guide.md) | **Teacher's guide**: installing the client, connecting a model, setting up courses, classes and students, then lectures, quizzes, slides, grading, backup and common problems |
| [docs/features.md](docs/features.md) | Behaviour, limits and design details of each page: lectures, profiles, retrieval, quizzes, slides, lesson packages, grading, job queue, experiments |
| [project_map.md](project_map.md) | Module responsibilities, call relationships, data flow and storage — read before changing code |
| [docs/image-lecture-workflow.md](docs/image-lecture-workflow.md) | How image reading feeds into lectures, and its limits |
| [docs/scale-data.md](docs/scale-data.md) | Optional chapter-sized corpus and synthetic class, the retrieval evaluation, the generation benchmark and the fixed evaluation set of the default writing mode |
| [docs/ppt-template-integration.md](docs/ppt-template-integration.md) | Semantic slide layouts, template adaptation and version compatibility |
| [demo/prompts/README.md](demo/prompts/README.md) | Prompt versions and the actual input structure |
| [docs/archive/](docs/archive/) | Early VLM + RAG planning drafts from 2026-10-21; historical only, not the current implementation |
| [experiments/2026-11-26-rag](experiments/2026-11-26-rag/README.md) | Files of the whole-course retrieval evaluation: retrieval results, citation judgements and blind copies for teacher scoring; results in docs/scale-data.md |
| [experiments/2026-11-26-quick-start](experiments/2026-11-26-quick-start/README.md) | Files of the one-click lesson and default material mode evaluation: suggestion accuracy, real-course scenarios and citation judgements; results in docs/scale-data.md |
| [experiments/2026-10-12-bayes](experiments/2026-10-12-bayes/summary.md), [demo/VERIFICATION.md](demo/VERIFICATION.md) | Historical comparison experiment and early engineering verification |

## License

The project's own code and documentation, including the slide layout engine in `tools/ppt-kit/`, are released under the [MIT License](LICENSE). The software is provided as is; everything a model generates (lectures, quizzes, slides, grading suggestions) must be reviewed by a teacher before use.

Third-party material keeps its own terms and is not relicensed:

| Material | Where | Terms |
| --- | --- | --- |
| KaTeX 0.16.11 | `demo/static/vendor/katex/` | MIT (license in that folder) |
| Lucide 0.469.0 icons (only the shapes the interface uses, copied unchanged; rebuilt by `tools/build_icons.py`) | `demo/static/vendor/lucide/` | ISC, with portions from Feather under MIT (license in that folder) |
| Slide templates from the LibreOffice design candidates | `assets/ppt-templates/` | CC0 and MIT, see `assets/ppt-templates/licenses/` |
| Bundled in the desktop packages | PostgreSQL 17, CPython 3.12, Electron with Chromium, and the Python packages in `requirements.txt` | Each under its own license (PostgreSQL License, PSF License, MIT, BSD, Apache-2.0 and similar; psycopg is LGPL-3.0). The license texts ship inside the packages |

## Status

- **Implemented**: lecture generation and review, matching quizzes, classroom slides, lesson packages, grading into profiles, and batch import of student data. All are covered by automated tests and have been run end to end once with a real model (qwen3.8-max).
- **Lesson-design writing (the default since 2026-11-14)**: covered by automated tests. Measured with a real model (qwen3.8-max) on 2026-11-17 with the released checks, outside the app's own pages: three saved lessons rewritten through `demo/scale/design_compare.py` (57k–98k tokens, 162–259 seconds each), and four lessons of the new fixed evaluation set (`demo/scale/evalset.py`, [docs/scale-data.md](docs/scale-data.md#评测集默认写作方式)). Of those four, three were saved (53k–138k tokens, 178–371 seconds; the 90-minute lesson cost the most) and one failed because the design check treats a blank line in a board list as missing text. Every saved lesson needed the design's one automatic repair, and the design step takes about nine tenths of the time. On 2026-11-18 the same four lessons were run again after each round of changes ([docs/scale-data.md](docs/scale-data.md#2026-11-18改动前后对照dev-的-l1l2p1p2)): all four were saved every time; a design that is usable but has reminders is now corrected in place (only the changed parts are sent back) instead of being rewritten, which cut that second call from a median of 77 seconds to 8–13 seconds and the whole lesson from 216 to 128–161 seconds (median); tokens per lesson still vary between 63k and 95k, mostly with how many segments need a rewrite. The design still needs that second call in every lesson, and the most common reminder left is an objective that the exercises named for it do not test. No lesson written this way has been reviewed by a teacher.
- **Added on 2026-11-10, covered by automated tests and exercised in the offline sandbox**: sectioned writing with automatic rewrite, streaming preview, LaTeX formulas and program-drawn figures, arithmetic recomputation, class batches with bounded model concurrency, automatic single retry for invalid quiz and slide output, an installable (PWA) shell, and the scale corpus with its benchmarks. Real-model use of these is small so far: two sectioned lectures through the batch queue (`demo/scale/bench.py --real`, 2026-11-10), and the lesson-design runs above, which use the formulas, the figures and the arithmetic check.
- **Whole-course retrieval (2026-11-26; the page default for part of that day, now an option)**: material is retrieved from every passage of the course, not only the selected knowledge points. Three queries (explanation, worked examples, the learner's difficulties) are each ranked by BM25 and by embedding similarity, fused by weighted reciprocal rank, held to a relevance floor, and every selected point keeps at least one passage. The teacher sees the picked passages and runners-up under 「课程资料依据」 and can drop or add them before generating. Two checks were added: whether the retrieved material was cited, and whether a citation points to a material that shares almost nothing with the sentence while another one matches it. Measured ([docs/scale-data.md](docs/scale-data.md#整门课检索2026-11-26)): on a real course of 97 materials (622 passages), the right knowledge point ranks in the top three for 96.5% of 57 questions, which the coding assistant wrote from the knowledge-point names and no teacher has reviewed. On four lessons of the evaluation set, compared with exact selection and with no material (one lecture per lesson and condition), it used 12% fewer tokens than exact selection and failed 3 automated checks against 6. A model of the same family judged 87% of its cited lines supported by the cited material, against 66%, but that judge agreed with itself on only about 86% of lines across two passes, and a later rerun of exact selection on the same code scored 85%, so the gap is within run-to-run variation. The six passages it retrieved from outside the lesson were never cited. Without material, the model did not invent citation numbers.
- **One-click lesson and the default material mode (2026-11-26, the page default since then)**: a teacher can write the lesson's focus instead of ticking knowledge points; retrieval over the whole course suggests points, each with the passages behind it, and the teacher removes or adds points before the lesson is created. The default material mode is now "all material of the lesson's points, plus course-wide passages for the learner": the confirmed points' material is given whole, exactly as exact selection gives it; retrieval adds passages from elsewhere in the course only when the learner's errors point there (never from later chapters), and picks excerpts inside the lesson's own material only when it exceeds the 16,000-character budget. Measured ([docs/scale-data.md](docs/scale-data.md#一键备课与本课资料保底2026-11-26)): the suggestion rule, tuned on the evaluation set's dev lessons, ticked every right point on all 12 lessons, and 70% (dev) and 88% (holdout) of the ticked points were right; on the real course it ticked 94% of the right points for questions the coding assistant wrote. On the eight evaluation lessons the default mode gave the model exactly the material exact selection gives. Against exact selection rerun on the same code (four lessons, one lecture each) it used 445,764 tokens against 395,632, failed 11 automated checks against 9, and had 79% of its cited lines judged supported against 85%: no larger than the differences between two runs of exact selection. A learner note pointing to an earlier point brought in a passage, which the lecture used only for its pretest. Four holdout lessons were created through the one-click flow with every suggestion accepted and generated end to end. On the real course, two of the three course-wide passages picked for learner notes were off target. No teacher has scored any of these lectures.
- **Desktop client (2026-11-13)**: the Linux packages were built and run on Linux: first start on an empty data folder, the getting-started guide, restarting the service, backup, and a clean shutdown. The `.deb` package was installed in an Ubuntu 24.04 virtual machine (with the AppArmor user-namespace restriction on) and started with Chromium's sandbox; they have not been tried on Debian or older Ubuntu releases. The Windows installer is built and smoke-tested on GitHub Actions and has been installed and tested on a real Windows computer. On 2026-11-18 the client moved from Electron 38 to 45 (electron-builder 26.18) and the service from Flask's development server to waitress: the client tests, the smoke test and the Linux package build pass, but the installed packages have not yet been retried in the Ubuntu virtual machine or on Windows.
- **Known issues**: single-call lectures came back at about a third of the requested length (1,177 of 3,600–5,400 characters in the one qwen3.8-max run). Sectioned writing was meant to fix this and has been measured once (`demo/scale/bench.py --real`, two lectures, 2026-11-10): 5,816 and 5,554 characters, both still flagged by the length check, at six and seven model calls and about 158k tokens per lecture. Lesson-design writing sets a length limit per segment from its minutes instead of a target for the whole lecture; its cost is given above. Both samples are too small to say how often either mode meets its limits. Lectures and quizzes still contain occasional math errors from the model; the arithmetic check only catches numbers that disagree, so teachers still need to check every question.
- **Not yet done**: teacher scoring of the with/without-profile comparison (on 2026-11-18 the evaluation set produced the pairs for four one-to-one lessons, eight lectures in all, with blind A/B copies; the program statistics show no consistent difference, which says nothing about usefulness), teacher scoring of the three-way material comparison (whole-course retrieval, exact selection, no material; blind copies in `experiments/2026-11-26-rag/blind/`), an ablation of the vision component, and independent teacher review. Until then, no claims can be made about saving preparation time or improving learning. Slides adapted by the model still use plain-text formulas and carry no figures (slides laid out from a lesson design use editable text with real sub- and superscripts and redraw the figures). Exported HTML and plain text write formulas as plain Unicode (P(A₁ | D), x²), not typeset.
