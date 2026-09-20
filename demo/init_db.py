"""Create only the dedicated prototype database; seed without overwriting rows."""
import json
import os
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
dsn = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
params = conninfo_to_dict(dsn)
name = params.get('dbname', '')
if name != 'lecture_demo_prototype':
    raise SystemExit('初始化仅允许使用独立数据库 lecture_demo_prototype。')
admin = make_conninfo(dsn, dbname='postgres')
with psycopg.connect(admin, autocommit=True) as conn:
    if not conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', (name,)).fetchone():
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        print('Created dedicated database:', name)
    else:
        print('Using existing demo database; preserving records.')

# ========== 修改这里：增加 encoding="utf-8" ==========
seed = json.loads((ROOT / 'demo/seed.json').read_text(encoding="utf-8"))

with psycopg.connect(dsn) as conn:
    # ========== 修改这里：schema.sql 同样加上utf-8，防止后面再报同样编码错误 ==========
    conn.execute((ROOT / 'demo/schema.sql').read_text(encoding="utf-8"))
    
    for r in seed['students']:
        conn.execute('INSERT INTO students VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (r['id'], r['name'], r['subtitle'], r['profile']))
    for r in seed['knowledge_points']:
        conn.execute('INSERT INTO knowledge_points VALUES (%s,%s) ON CONFLICT DO NOTHING',
                     (r['id'], r['name']))
    for r in seed['states']:
        conn.execute('INSERT INTO student_knowledge VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', r)
    for r in seed['materials']:
        conn.execute('INSERT INTO materials VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     tuple(r[k] for k in ('id','knowledge_id','title','content','source_title','source_url','section')))
    counts = {t: conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0]
              for t in ('students','student_knowledge','materials','lectures')}
print(json.dumps(counts, ensure_ascii=False))
