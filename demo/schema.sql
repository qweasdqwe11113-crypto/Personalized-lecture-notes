CREATE TABLE IF NOT EXISTS students (
 id integer PRIMARY KEY,
 name text NOT NULL,
 subtitle text NOT NULL,
 profile text NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_points (
 id integer PRIMARY KEY,
 name text NOT NULL
);
CREATE TABLE IF NOT EXISTS student_knowledge (
 student_id integer REFERENCES students(id),
 knowledge_id integer REFERENCES knowledge_points(id),
 state text NOT NULL CHECK (state IN ('needs_support','partial','mastered','unknown')),
 evidence text NOT NULL,
 PRIMARY KEY(student_id, knowledge_id)
);
CREATE TABLE IF NOT EXISTS materials (
 id integer PRIMARY KEY,
 knowledge_id integer NOT NULL REFERENCES knowledge_points(id),
 title text NOT NULL,
 content text NOT NULL,
 source_title text NOT NULL,
 source_url text NOT NULL,
 section text NOT NULL
);
CREATE TABLE IF NOT EXISTS lectures (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 student_id integer NOT NULL REFERENCES students(id),
 topic text NOT NULL,
 goal text NOT NULL,
 context jsonb NOT NULL,
 content text NOT NULL,
 model text NOT NULL,
 elapsed_seconds numeric NOT NULL,
 usage jsonb NOT NULL DEFAULT '{}',
 citation_check jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now()
);

-- Additive migration: preserve the original rows and lecture snapshots.
CREATE TABLE IF NOT EXISTS course_tasks (
 key text PRIMARY KEY,
 payload jsonb NOT NULL,
 revision integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now()
);

-- A copied lesson owns its materials but reuses the original diagnostic identity.
-- Existing unrelated points are never merged by name.
CREATE TABLE IF NOT EXISTS knowledge_aliases (
 knowledge_id integer PRIMARY KEY REFERENCES knowledge_points(id),
 canonical_id integer NOT NULL REFERENCES knowledge_points(id),
 CHECK (knowledge_id <> canonical_id)
);

ALTER TABLE students ADD COLUMN IF NOT EXISTS profile_revision integer NOT NULL DEFAULT 0;
ALTER TABLE student_knowledge ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0;
ALTER TABLE student_knowledge ADD COLUMN IF NOT EXISTS updated_at timestamptz;

CREATE TABLE IF NOT EXISTS learning_evidence (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 student_id integer NOT NULL REFERENCES students(id),
 knowledge_id integer NOT NULL REFERENCES knowledge_points(id),
 source text NOT NULL CHECK (source IN ('initial_snapshot','homework','quiz','observation','teacher_review')),
 task_content text NOT NULL,
 performance text NOT NULL,
 observed_at timestamptz,
 recorder text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE student_knowledge ADD COLUMN IF NOT EXISTS evidence_id bigint REFERENCES learning_evidence(id);

CREATE TABLE IF NOT EXISTS knowledge_state_history (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 evidence_id bigint NOT NULL UNIQUE REFERENCES learning_evidence(id),
 previous_state text NOT NULL CHECK (previous_state IN ('needs_support','partial','mastered','unknown')),
 confirmed_state text NOT NULL CHECK (confirmed_state IN ('needs_support','partial','mastered','unknown')),
 decision text NOT NULL CHECK (decision IN ('baseline','update','retain')),
 reason text NOT NULL,
 previous_evidence text NOT NULL,
 previous_revision integer NOT NULL CHECK (previous_revision >= 0),
 new_revision integer NOT NULL CHECK (new_revision >= 0),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evidence_student_history ON learning_evidence(student_id, id DESC);

CREATE TABLE IF NOT EXISTS student_profile_history (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 student_id integer NOT NULL REFERENCES students(id),
 previous_snapshot jsonb NOT NULL,
 new_snapshot jsonb NOT NULL,
 recorder text NOT NULL,
 reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS profile_student_history ON student_profile_history(student_id, id DESC);

CREATE TABLE IF NOT EXISTS generation_runs (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 student_id integer NOT NULL REFERENCES students(id),
 topic_key text NOT NULL,
 topic text NOT NULL,
 goal text NOT NULL,
 context jsonb NOT NULL,
 prompt_version text NOT NULL,
 input_version text NOT NULL,
 request_body text NOT NULL,
 request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed')),
 attempted_at timestamptz,
 finished_at timestamptz,
 elapsed_seconds numeric,
 content text NOT NULL DEFAULT '',
 usage jsonb NOT NULL DEFAULT '{}',
 response_metadata jsonb NOT NULL DEFAULT '{}',
 citation_check jsonb NOT NULL DEFAULT '{}',
 error_stage text,
 error_code text,
 error_message text,
 lecture_id bigint UNIQUE REFERENCES lectures(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((status = 'succeeded') = (lecture_id IS NOT NULL)),
 CHECK ((status = 'running') = (finished_at IS NULL)),
 CHECK (status <> 'failed' OR (error_stage IS NOT NULL AND error_code IS NOT NULL AND error_message IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS generation_runs_recent ON generation_runs(id DESC);
-- A lecture has one original generation and may have many section proposals.
ALTER TABLE generation_runs DROP CONSTRAINT IF EXISTS generation_runs_lecture_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS generation_runs_original_lecture
 ON generation_runs(lecture_id) WHERE NOT (context ? 'section_edit');

-- Review event revision changes only on issue/revision writes; original prose stays intact.
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS review_revision integer NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS lecture_issues (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 lecture_id bigint NOT NULL REFERENCES lectures(id),
 target_revision integer NOT NULL CHECK (target_revision >= 0),
 category text NOT NULL CHECK (category IN ('math','terminology','citation_support','scope','structure','other')),
 excerpt text NOT NULL,
 start_line integer NOT NULL CHECK (start_line > 0),
 end_line integer NOT NULL CHECK (end_line >= start_line),
 content_sha256 text NOT NULL,
 detail text NOT NULL,
 material_id integer,
 support text NOT NULL CHECK (support IN ('not_applicable','unreviewed','supported','unsupported','unclear')),
 source text NOT NULL CHECK (source IN ('feedback','teacher')),
 recorder text NOT NULL,
 external_key text UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lecture_issues_history ON lecture_issues(lecture_id,id);
ALTER TABLE lecture_issues ADD COLUMN IF NOT EXISTS visual_citation_id text;
CREATE TABLE IF NOT EXISTS lecture_revisions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 lecture_id bigint NOT NULL REFERENCES lectures(id),
 revision_number integer NOT NULL CHECK (revision_number > 0),
 base_revision integer NOT NULL CHECK (base_revision >= 0),
 content text NOT NULL,
 status text NOT NULL CHECK (status IN ('draft','needs_revision','reviewed')),
 recorder text NOT NULL,
 reason text NOT NULL,
 review_seconds integer NOT NULL CHECK (review_seconds BETWEEN 0 AND 86400),
 checklist jsonb NOT NULL,
 resolutions jsonb NOT NULL,
 changes jsonb NOT NULL,
 quality_report jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(lecture_id,revision_number)
);

CREATE TABLE IF NOT EXISTS generation_jobs (
 id text PRIMARY KEY,
 run_id bigint NOT NULL UNIQUE REFERENCES generation_runs(id),
 parent_run_id bigint REFERENCES generation_runs(id),
 status text NOT NULL CHECK (status IN ('queued','running','succeeded','failed','interrupted')),
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS generation_jobs_recent ON generation_jobs(created_at DESC);
ALTER TABLE generation_jobs DROP CONSTRAINT IF EXISTS generation_jobs_status_check;
ALTER TABLE generation_jobs ADD CONSTRAINT generation_jobs_status_check
 CHECK (status IN ('queued','running','succeeded','failed','interrupted','cancelled'));

CREATE TABLE IF NOT EXISTS lecture_experiments (
 id text PRIMARY KEY,
 created_at timestamptz NOT NULL DEFAULT now(),
 status text NOT NULL DEFAULT 'prepared' CHECK (status IN ('prepared','running','completed','interrupted')),
 manifest jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS lecture_experiment_cells (
 experiment_id text NOT NULL REFERENCES lecture_experiments(id),
 cell integer NOT NULL CHECK (cell BETWEEN 1 AND 12),
 snapshot jsonb NOT NULL,
 status text NOT NULL DEFAULT 'prepared' CHECK (status IN ('prepared','dispatching','queued','succeeded','failed','interrupted')),
 job_id text REFERENCES generation_jobs(id),
 error text NOT NULL DEFAULT '',
 PRIMARY KEY (experiment_id,cell)
);

-- Retrieval: one row per text chunk. Source metadata is duplicated here because a
--   chunk must be usable without joining another store; the citation number stays
--   tied to the parent material, so [M编号] keeps exactly its old meaning.
CREATE TABLE IF NOT EXISTS material_chunks (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  material_id integer REFERENCES materials(id) ON DELETE CASCADE,
  knowledge_id integer NOT NULL REFERENCES knowledge_points(id),
  chunk_index integer NOT NULL,
  title text NOT NULL,
  source_title text NOT NULL,
  source_url text NOT NULL,
  section text NOT NULL,
  content text NOT NULL,
  char_length integer NOT NULL CHECK (char_length > 0),
  content_sha256 text NOT NULL,
  embed_model text NOT NULL,
  embedding float8[] NOT NULL CHECK (cardinality(embedding) > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS material_chunks_knowledge ON material_chunks(knowledge_id, material_id, chunk_index);
CREATE UNIQUE INDEX IF NOT EXISTS material_chunks_identity ON material_chunks(material_id, chunk_index);

-- Freeze how this run picked its material: mode, backend, embedding model and scored items.
ALTER TABLE generation_runs ADD COLUMN IF NOT EXISTS retrieval jsonb NOT NULL DEFAULT '{}';

-- Image assets. `bytes` holds the normalized bytes the model is actually shown;
-- they are written once and never re-encoded, so saved request bodies stay reproducible.
CREATE TABLE IF NOT EXISTS material_assets (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  material_id integer REFERENCES materials(id) ON DELETE CASCADE,
  upload_id text,
  kind text NOT NULL CHECK (kind IN ('figure','page_scan','diagram')),
  label text NOT NULL,
  page_no integer,
  source_sha256 text NOT NULL,
  normalized_sha256 text NOT NULL,
  mime text NOT NULL,
  width integer NOT NULL CHECK (width > 0),
  height integer NOT NULL CHECK (height > 0),
  byte_size integer NOT NULL CHECK (byte_size > 0 AND byte_size <= 1258291),
  normalize_version text NOT NULL,
  bytes bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (material_id IS NOT NULL OR upload_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS material_assets_identity ON material_assets(normalized_sha256);
CREATE INDEX IF NOT EXISTS material_assets_material ON material_assets(material_id, page_no);

-- Which images this run actually sent, in the exact order they appeared.
CREATE TABLE IF NOT EXISTS generation_run_assets (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES generation_runs(id) ON DELETE CASCADE,
  asset_id bigint NOT NULL REFERENCES material_assets(id),
  ordinal integer NOT NULL CHECK (ordinal > 0),
  normalized_sha256 text NOT NULL,
  byte_size integer NOT NULL CHECK (byte_size > 0),
  UNIQUE(run_id, ordinal)
);
CREATE INDEX IF NOT EXISTS generation_run_assets_run ON generation_run_assets(run_id, ordinal);

-- Independent question sets: frozen inputs and answers never become lectures or student diagnoses.
CREATE TABLE IF NOT EXISTS quiz_jobs (
 id text PRIMARY KEY,
 parent_id text REFERENCES quiz_jobs(id),
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','interrupted')),
 topic_key text NOT NULL,
 student_id integer REFERENCES students(id),
 context jsonb NOT NULL,
 options jsonb NOT NULL,
 request_body text NOT NULL,
 request_sha256 text NOT NULL,
 result jsonb,
 usage jsonb NOT NULL DEFAULT '{}',
 error_message text,
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 finished_at timestamptz,
 CHECK ((status='succeeded') = (result IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS quiz_jobs_recent ON quiz_jobs(created_at DESC);
