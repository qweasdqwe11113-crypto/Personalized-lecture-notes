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
