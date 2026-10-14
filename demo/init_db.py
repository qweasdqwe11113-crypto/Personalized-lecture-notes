"""Create only the dedicated prototype database; seed without overwriting rows."""
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

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
dsn = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
params = conninfo_to_dict(dsn)
name = params.get('dbname', '')
if name != 'lecture_demo_prototype':
    raise SystemExit('初始化仅允许使用独立数据库 lecture_demo_prototype。')
# Existing demos need access only to their own DB; the optional "postgres"
# maintenance DB may not exist on this machine.
try:
    with psycopg.connect(dsn):
        pass
except psycopg.OperationalError:
    admin = make_conninfo(dsn, dbname='template1')
    with psycopg.connect(admin, autocommit=True) as conn:
        if conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', (name,)).fetchone():
            raise SystemExit('专用数据库已存在但无法连接，请检查本机连接配置。')
        try:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            print('Created dedicated database:', name)
        except psycopg.errors.DuplicateDatabase:
            print('Using existing demo database; preserving records.')
else:
    print('Using existing demo database; preserving records.')
seeds = [json.loads((ROOT / 'demo' / name).read_text(encoding='utf-8')) for name in ('seed.json', 'university_seed.json')]
seed = {key: [row for source in seeds for row in source[key]] for key in seeds[0]}
with psycopg.connect(dsn, row_factory=dict_row) as conn:
    conn.execute((ROOT / 'demo/schema.sql').read_text(encoding='utf-8'))
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
    retain_feedback_issues(conn)
    counts = {t: conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(t))).fetchone()['n']
              for t in ('students','student_knowledge','materials','lectures','learning_evidence','generation_runs','lecture_issues','lecture_revisions')}
print(json.dumps(counts, ensure_ascii=False))
