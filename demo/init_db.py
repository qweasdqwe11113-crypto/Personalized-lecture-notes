"""Create only the dedicated prototype database; seed without overwriting rows.

The desktop release (ZHIYE_EDITION=release) builds the same tables but seeds nothing: no
demonstration students, knowledge points, materials, courses or classes.
"""
import json
import os
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from dotenv import load_dotenv
from profiles import retain_initial_evidence
from reviews import retain_feedback_issues
from classes import ensure_course_structure
from edition import data_dir, release

ROOT = Path(__file__).resolve().parents[1]
DATABASE = 'lecture_demo_prototype'
COUNTED = ('courses', 'classes', 'class_students', 'students', 'student_knowledge', 'materials', 'lectures',
           'learning_evidence', 'generation_runs', 'lecture_issues', 'lecture_revisions')


def create_database(dsn):
    """Connect to the dedicated database, creating it when it does not exist yet."""
    if conninfo_to_dict(dsn).get('dbname', '') != DATABASE:
        raise SystemExit(f'初始化仅允许使用独立数据库 {DATABASE}。')
    # Existing demos need access only to their own DB; the optional "postgres"
    # maintenance DB may not exist on this machine.
    try:
        with psycopg.connect(dsn):
            pass
    except psycopg.OperationalError:
        admin = make_conninfo(dsn, dbname='template1')
        with psycopg.connect(admin, autocommit=True) as conn:
            if conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', (DATABASE,)).fetchone():
                raise SystemExit('专用数据库已存在但无法连接，请检查本机连接配置。')
            try:
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DATABASE)))
                print('Created dedicated database:', DATABASE)
            except psycopg.errors.DuplicateDatabase:
                print('Using existing demo database; preserving records.')
    else:
        print('Using existing demo database; preserving records.')


def demo_seed():
    seeds = [json.loads((ROOT / 'demo' / name).read_text(encoding='utf-8'))
             for name in ('seed.json', 'university_seed.json')]
    return {key: [row for source in seeds for row in source[key]] for key in seeds[0]}


def initialize(conn, demo=True):
    """Apply the schema and migrations; with demo, add the demonstration rows that are missing."""
    conn.execute((ROOT / 'demo/schema.sql').read_text(encoding='utf-8'))
    if demo:
        seed = demo_seed()
        for r in seed['students']:
            conn.execute('INSERT INTO students (id,name,subtitle,profile) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         (r['id'], r['name'], r['subtitle'], r['profile']))
        for r in seed['knowledge_points']:
            conn.execute('INSERT INTO knowledge_points VALUES (%s,%s) ON CONFLICT DO NOTHING',
                         (r['id'], r['name']))
        for r in seed['states']:
            conn.execute('INSERT INTO student_knowledge (student_id,knowledge_id,state,evidence) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', r)
        for r in seed['materials']:
            conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         tuple(r[k] for k in ('id','knowledge_id','title','content','source_title','source_url','section')))
    retain_initial_evidence(conn)
    if demo:
        # Reviewer feedback on the demonstration lectures; the release has none of those lectures.
        retain_feedback_issues(conn)
    # Demo courses and classes, and legacy lesson copies filed under a course (once).
    ensure_course_structure(conn, presets=demo)
    return {t: conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(t))).fetchone()['n']
            for t in COUNTED}


def main():
    load_dotenv(data_dir() / '.env')
    dsn = os.environ.get('DATABASE_URL', f'postgresql:///{DATABASE}')
    create_database(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        counts = initialize(conn, demo=not release())
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    main()
