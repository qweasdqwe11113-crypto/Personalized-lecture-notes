"""Generation trace persistence; successful trace and lecture commit together."""
import hashlib
import json

from psycopg.types.json import Jsonb


def create_run(conn, student_id, topic_key, goal, context, prepared):
    return conn.execute('''INSERT INTO generation_runs
        (student_id,topic_key,topic,goal,context,prompt_version,input_version,request_body,request_sha256,retrieval)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (student_id, topic_key, context['topic']['title'], goal, Jsonb(context),
         prepared.prompt_version, prepared.input_version, prepared.body, prepared.sha256,
         Jsonb(context.get('retrieval', {})))).fetchone()['id']


def mark_attempt(conn, run_id):
    row = conn.execute('''UPDATE generation_runs SET attempted_at=now()
        WHERE id=%s AND status='running' AND attempted_at IS NULL RETURNING id''', (run_id,)).fetchone()
    if not row:
        raise RuntimeError('Generation run is no longer available for dispatch.')


def fail_run(conn, run_id, error, elapsed, check=None):
    output = error.partial
    updated = conn.execute('''UPDATE generation_runs SET status='failed',finished_at=now(),elapsed_seconds=%s,
        content=%s,usage=%s,response_metadata=%s,citation_check=%s,
        error_stage=%s,error_code=%s,error_message=%s WHERE id=%s AND status='running' RETURNING id''',
        (elapsed, output.content, Jsonb(output.usage), Jsonb(output.metadata), Jsonb(check or {}),
         error.stage, error.code, str(error), run_id)).fetchone()
    if not updated:
        raise RuntimeError('Generation failure could not be finalized.')


def save_success(conn, run_id, student_id, goal, context, prepared, output, elapsed, check):
    model = json.loads(prepared.body)['model']
    lecture = conn.execute('''INSERT INTO lectures
        (student_id,topic,goal,context,content,model,elapsed_seconds,usage,citation_check)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at''',
        (student_id, context['topic']['title'], goal, Jsonb(context), output.content, model,
         elapsed, Jsonb(output.usage), Jsonb(check))).fetchone()
    updated = conn.execute('''UPDATE generation_runs SET status='succeeded',finished_at=now(),
        elapsed_seconds=%s,content=%s,usage=%s,response_metadata=%s,citation_check=%s,lecture_id=%s
        WHERE id=%s AND status='running' RETURNING id''',
        (elapsed, output.content, Jsonb(output.usage), Jsonb(output.metadata), Jsonb(check),
         lecture['id'], run_id)).fetchone()
    if not updated:
        raise RuntimeError('Generation run cannot be completed twice.')
    return lecture


def recent_runs(conn):
    return conn.execute('''SELECT r.id,r.status,r.topic,r.created_at,r.finished_at,r.elapsed_seconds,
        r.lecture_id,r.prompt_version,r.error_message,s.name FROM generation_runs r
        JOIN students s ON s.id=r.student_id ORDER BY r.id DESC LIMIT 10''').fetchall()


def run_detail(conn, run_id):
    row = conn.execute('SELECT * FROM generation_runs WHERE id=%s', (run_id,)).fetchone()
    if not row:
        return None
    row['integrity_valid'] = hashlib.sha256(row['request_body'].encode('utf-8')).hexdigest() == row['request_sha256']
    try:
        payload = json.loads(row['request_body'])
        row['request_payload'] = payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, TypeError):
        row['request_payload'] = None
    return row
