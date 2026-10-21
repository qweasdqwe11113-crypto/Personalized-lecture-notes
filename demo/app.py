"""Local-only PostgreSQL + Qwen teaching prototype. No vector or vision models."""
import html
import os
from io import BytesIO
import re
import time
from urllib.parse import quote
from pathlib import Path
from threading import Lock

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from model_settings import register_model_settings
from flask import Flask, Response, jsonify, render_template, request
from profiles import (ProfileError, compare_lecture_profile, decorate_states, evidence_history, get_profile,
                      record_evidence, update_profile)
from prompts import prepare_request
from qwen import ModelError, ModelResult, call_qwen, config_flags
from runs import create_run, fail_run, mark_attempt, recent_runs, run_detail, save_success
from quality import assess_scope, check_output
from reviews import (ReviewError, STATUS_LABELS, add_issue, lecture_row,
                     review_bundle, save_revision, version_row)
from courses import TOPICS, REFERENCE_EXAMPLES
from comparisons import comparison_options, compare_lectures
from jobs import JobError, JobQueue
from uploads import read_upload, register_uploads
from embed import EmbeddingError, OllamaEmbedder, QwenEmbedder
from retrieval import (MODES as RETRIEVAL_MODES, RetrievalError, adopt_content, build_query,
                       index_knowledge, search as search_chunks, status as retrieval_status,
                       to_materials)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
app = Flask(__name__)
register_model_settings(app, ROOT / '.env')
app.config['MAX_CONTENT_LENGTH'] = 11 * 1024 * 1024
register_uploads(app, ROOT)
DSN = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
MODEL = os.environ.get('QWEN_MODEL', 'qwen3.8-27b')
GENERATION_LOCK = Lock()
EMBEDDER = QwenEmbedder()
RETRIEVAL_LIMIT = 6


def db():
    return psycopg.connect(DSN, row_factory=dict_row, connect_timeout=5)


def get_context(student_id, topic_key, mode='profile', retrieval_mode='sql'):
    topic = TOPICS[topic_key]
    if student_id not in topic['student_ids']:
        return None
    if retrieval_mode == 'vector':
        # Prepare missing or stale chunks before opening the read-only snapshot.
        with db() as conn:
            index_knowledge(conn, EMBEDDER, topic['knowledge_ids'])
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        student = conn.execute('SELECT * FROM students WHERE id=%s', (student_id,)).fetchone()
        if not student:
            return None
        states = conn.execute('''SELECT k.id, k.name, COALESCE(s.state,'unknown') AS state,
            COALESCE(s.evidence,'没有诊断记录。') AS evidence,
            COALESCE(s.revision,0) AS revision,s.evidence_id,s.updated_at
            FROM knowledge_points k LEFT JOIN student_knowledge s
            ON s.knowledge_id=k.id AND s.student_id=%s
            WHERE k.id=ANY(%s) ORDER BY k.id''', (student_id,topic['knowledge_ids'])).fetchall()
        # Exact SQL lookup, restricted to the selected topic.
        materials = conn.execute('SELECT * FROM materials WHERE knowledge_id=ANY(%s) ORDER BY id',
                                 (topic['knowledge_ids'],)).fetchall()
        retrieval = {'mode': retrieval_mode, 'backend': None, 'embed_model': None, 'query': None, 'items': []}
        if retrieval_mode == 'vector':
            # Without a profile the question stays the shared goal, so ablation is still clean.
            query = build_query(topic['goal'], states if mode == 'profile' else [])
            retrieval = search_chunks(conn, EMBEDDER, query, topic['knowledge_ids'], limit=RETRIEVAL_LIMIT)
            materials = to_materials(retrieval['items'])
        # Fetch each point separately so a frequently updated point cannot crowd
        # another point's evidence out of the model input.
        recent = {s['id']: evidence_history(conn, student_id, [s['id']], limit=3) for s in states}
    decorate_states(states)
    for s in states:
        s['recent_evidence'] = recent[s['id']]
    ctx = {'student':student, 'topic':topic, 'states':states, 'materials':materials, 'retrieval': retrieval}
    ctx['personalization_mode'] = mode
    if topic.get('course') == '大学概率论':
        ctx['reference_examples'] = REFERENCE_EXAMPLES[:1] if topic_key.endswith('basic') else REFERENCE_EXAMPLES
    if mode == 'none':
        # Paired runs retain their selected student ID in the database, while the
        # actual model context contains no learner identifiers or diagnostics.
        ctx['student'] = {'name': '通用讲义', 'profile': '未提供学生画像；使用通用适中解释。'}
        ctx['states'] = []
    ctx['scope_check'] = assess_scope(ctx, topic['goal'])
    return ctx


@app.before_request
def local_origin_only():
    # Keep the local generation endpoint unavailable to cross-origin web pages.
    if request.method == 'POST' and request.path != '/api/uploads' and request.content_length and request.content_length > 131072:
        return jsonify(error='提交内容过长，请缩短后重试。'), 413
    origin = request.headers.get('Origin')
    if request.method == 'POST' and origin and origin != request.host_url.rstrip('/'):
        return jsonify(error='请从本地 demo 页面发起请求。'), 403


@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/api/bootstrap')
def bootstrap():
    with db() as conn:
        students = conn.execute('SELECT * FROM students ORDER BY id').fetchall()
        history = conn.execute('''SELECT l.id,s.name,l.topic,l.created_at FROM lectures l
                                  JOIN students s ON s.id=l.student_id ORDER BY l.id DESC LIMIT 8''').fetchall()
    return jsonify(students=students, topics=TOPICS, model=MODEL, history=history,
                   database='PostgreSQL · 已连接', configured=all(config_flags().values()),
                   embed_model=EMBEDDER.model)


@app.get('/api/context')
def context():
    student_id = request.args.get('student_id', type=int)
    topic = request.args.get('topic', 'linear')
    goal = request.args.get('goal', '').strip()
    mode = request.args.get('personalization_mode', 'profile')
    retrieval_mode = request.args.get('retrieval_mode', 'sql')
    if (student_id is None or topic not in TOPICS or len(goal) > 400
            or mode not in ('profile', 'none') or retrieval_mode not in RETRIEVAL_MODES):
        return jsonify(error='请选择有效学生和主题。'), 400
    data = get_context(student_id, topic, mode, retrieval_mode)
    if data:
        data['scope_check'] = assess_scope(data, goal or TOPICS[topic]['goal'])
    return (jsonify(data) if data else (jsonify(error='学生不存在。'),404))


@app.get('/api/students/<int:student_id>/profile')
def student_profile(student_id):
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        result = get_profile(conn, student_id)
    return jsonify(result)


@app.post('/api/students/<int:student_id>/profile')
def save_student_profile(student_id):
    with db() as conn:
        update_profile(conn, student_id, request.get_json(silent=True))
        result = get_profile(conn, student_id)
    return jsonify(result)


@app.post('/api/students/<int:student_id>/evidence')
def save_student_evidence(student_id):
    with db() as conn:
        record_evidence(conn, student_id, request.get_json(silent=True))
        result = get_profile(conn, student_id)
    return jsonify(result), 201


@app.errorhandler(ProfileError)
def profile_error(exc):
    return jsonify(error=str(exc)), exc.status


@app.errorhandler(ReviewError)
def review_error(exc):
    return jsonify(error=str(exc)), exc.status


@app.errorhandler(413)
def payload_too_large(_exc):
    return jsonify(error='提交内容过长，请缩短后重试。'), 413



def persist_failure(run_id, error, elapsed, check, job_id=None):
    if run_id is None:
        return False
    try:
        with db() as conn:
            if error.partial.content and 'quality_report' not in check:
                run = run_detail(conn, run_id)
                if run:
                    check['quality_report'] = check_output(error.partial.content, run['context'], run['goal'])
            fail_run(conn, run_id, error, elapsed, check)
            if job_id:
                conn.execute("UPDATE generation_jobs SET status='failed',finished_at=now() WHERE id=%s", (job_id,))
        return True
    except (psycopg.Error, RuntimeError):
        app.logger.error('Could not finalize generation record #%s.', run_id)
        return False


@app.post('/api/generate')
def generate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='请求格式必须是 JSON 对象。'), 400
    sid, topic_key, goal = data.get('student_id'), data.get('topic'), data.get('goal', '')
    if type(sid) is not int or not isinstance(topic_key, str) or topic_key not in TOPICS:
        return jsonify(error='请选择有效学生和主题。'), 400
    if not isinstance(goal, str) or len(goal.strip()) > 400:
        return jsonify(error='教学目标请控制在 400 字以内。'), 400
    mode = data.get('personalization_mode', 'profile')
    if mode not in ('profile', 'none'):
        return jsonify(error='请选择有画像或无画像模式。'), 400
    retrieval_mode = data.get('retrieval_mode', 'sql')
    if retrieval_mode not in RETRIEVAL_MODES:
        return jsonify(error='请选择精确 SQL 或向量检索。'), 400
    goal = goal.strip() or TOPICS[topic_key]['goal']
    if not GENERATION_LOCK.acquire(blocking=False):
        return jsonify(error='已有讲义正在生成，请等待完成后再试。'), 429
    result, status = generate_locked(sid, topic_key, goal, mode, retrieval_mode=retrieval_mode)
    return jsonify(result), status


def generate_locked(sid, topic_key, goal, mode='profile', frozen=None, prepared=None, run_id=None,
                    job_id=None, retrieval_mode='sql'):
    # Caller owns GENERATION_LOCK; release it for every result and exception.
    output, check, stage = ModelResult(''), {}, 'preparation'
    start = time.monotonic()
    try:
        ctx = frozen or get_context(sid, topic_key, mode, retrieval_mode)
        if not ctx:
            return {'error': '学生不存在。'}, 404
        if frozen is None:
            ctx['scope_check'] = assess_scope(ctx, goal)
        prepared = prepared or prepare_request(ctx, goal, MODEL)
        if run_id is None:
            with db() as conn:
                run_id = create_run(conn, sid, topic_key, goal, ctx, prepared)
        if not ctx['materials']:
            raise ModelError('no_materials', '当前主题没有课程材料，请先导入资料。', 'preparation')
        def on_attempt():
            with db() as conn:
                mark_attempt(conn, run_id)
        stage = 'model'
        output = call_qwen(prepared, on_attempt)
        stage = 'output'
        cited = set(re.findall(r'\[M(\d+)\]', output.content))
        available = {str(m['id']) for m in ctx['materials']}
        invalid = sorted(cited - available)
        check = {'cited_ids': sorted(cited), 'has_citations': bool(cited), 'invalid_ids': invalid,
                 'note': '仅核对引用编号；内容正确性与引用支持关系仍需人工检查。'}
        check['quality_report'] = check_output(output.content, ctx, goal)
        if invalid:
            raise ModelError('invalid_citation', '模型引用了不存在的材料，未保存为完整讲义，请重试。',
                             'output', output)
        elapsed = round(time.monotonic() - start, 2)
        stage = 'storage'
        with db() as conn:
            row = save_success(conn, run_id, sid, goal, ctx, prepared, output, elapsed, check)
            if job_id:
                conn.execute("UPDATE generation_jobs SET status='succeeded',finished_at=now() WHERE id=%s", (job_id,))
        import json
        return dict(**row, content=output.content, context=ctx, model=json.loads(prepared.body)['model'], elapsed_seconds=elapsed,
                    citation_check=check, usage=output.usage, goal=goal, generation_run_id=run_id), 200
    except ModelError as exc:
        error = exc
        status = 400 if exc.code == 'no_materials' else 502
    except psycopg.Error:
        error = ModelError('database_error', '数据库暂不可用，讲义未保存，请检查本机 PostgreSQL。', 'storage', output)
        status = 503
    except Exception:
        # Never forward exception text: it may contain service credentials or a DSN.
        app.logger.error('Generation failed at %s for record %s.', stage, run_id)
        error = ModelError('unexpected_error', '生成流程出现异常，未保存为完整讲义，请检查本地配置后重试。', stage, output)
        status = 502
    finally:
        GENERATION_LOCK.release()
    saved = persist_failure(run_id, error, round(time.monotonic() - start, 2), check, job_id)
    message = str(error)
    if run_id is not None and not saved:
        message += ' 生成输入已留存，但结束状态未能更新，需复核该记录。'
    return dict(error=message, generation_run_id=run_id, failure_recorded=saved), status


def execute_job(job_id, run_id, sid, topic, goal, context, prepared):
    GENERATION_LOCK.acquire()
    try:
        with db() as conn:
            conn.execute("UPDATE generation_jobs SET status='running',started_at=now() WHERE id=%s", (job_id,))
    except Exception:
        GENERATION_LOCK.release()
        raise
    return generate_locked(sid, topic, goal, context.get('personalization_mode', 'profile'),
                           context, prepared, run_id, job_id)


TASKS = JobQueue(lambda: db(), execute_job)


@app.errorhandler(JobError)
def job_error(exc):
    return jsonify(error=str(exc)), exc.status


@app.errorhandler(RetrievalError)
def retrieval_error(exc):
    return jsonify(error=str(exc)), 400


@app.errorhandler(EmbeddingError)
def embedding_error(exc):
    return jsonify(error=str(exc)), 502


def job_input(data):
    if not isinstance(data, dict):
        raise JobError('请求格式必须是 JSON 对象。')
    sid, topic = data.get('student_id'), data.get('topic')
    goal, mode = data.get('goal', ''), data.get('personalization_mode', 'profile')
    if type(sid) is not int or not isinstance(topic, str) or topic not in TOPICS:
        raise JobError('请选择有效学生和主题。')
    if not isinstance(goal, str) or len(goal.strip()) > 400:
        raise JobError('教学目标请控制在 400 字以内。')
    if mode not in ('profile', 'none'):
        raise JobError('请选择有画像或无画像模式。')
    retrieval_mode = data.get('retrieval_mode', 'sql')
    if retrieval_mode not in RETRIEVAL_MODES:
        raise JobError('请选择精确 SQL 或向量检索。')
    return sid, topic, goal.strip() or TOPICS[topic]['goal'], mode, retrieval_mode


@app.post('/api/jobs')
def submit_job():
    sid, topic, goal, mode, retrieval_mode = job_input(request.get_json(silent=True))
    ctx = get_context(sid, topic, mode, retrieval_mode)
    if not ctx:
        raise JobError('学生不存在。', 404)
    ctx['scope_check'] = assess_scope(ctx, goal)
    return jsonify(TASKS.submit(sid, topic, goal, ctx, prepare_request(ctx, goal, MODEL))), 202


@app.get('/api/jobs')
def recent_jobs():
    return jsonify(jobs=TASKS.recent(), capacity=4, workers=1)


@app.get('/api/jobs/<job_id>')
def job_detail(job_id):
    return jsonify(TASKS.detail(job_id))


@app.post('/api/generation-runs/<int:run_id>/retry')
def retry_generation(run_id):
    return jsonify(TASKS.retry(run_id)), 202


@app.get('/api/retrieval/status')
def retrieval_state():
    """Index state per knowledge point for the embedding model currently configured."""
    topic_key = request.args.get('topic', '')
    if topic_key not in TOPICS:
        return jsonify(error='请选择有效主题。'), 400
    try:
        with db() as conn:
            return jsonify(retrieval_status(conn, EMBEDDER, TOPICS[topic_key]['knowledge_ids']))
    except psycopg.Error:
        return jsonify(error='数据库暂不可用，请检查本机 PostgreSQL。'), 503


@app.post('/api/retrieval/index')
def build_retrieval_index():
    """Chunk and embed every material of one topic. Re-indexing replaces its own rows only."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('topic') not in TOPICS:
        return jsonify(error='请选择有效主题。'), 400
    ids = TOPICS[data['topic']]['knowledge_ids']
    try:
        with db() as conn:
            indexed = index_knowledge(conn, EMBEDDER, ids, force=bool(data.get('force')))
            return jsonify(indexed_materials=indexed, **retrieval_status(conn, EMBEDDER, ids))
    except EmbeddingError as exc:
        return jsonify(error=str(exc)), 502
    except psycopg.Error:
        return jsonify(error='数据库暂不可用，索引未写入。'), 503


@app.post('/api/retrieval/adopt')
def adopt_upload():
    """Adopt a stored upload as a course material of one knowledge point, then index it."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='请求格式必须是 JSON 对象。'), 400
    upload_id, knowledge_id = data.get('upload_id'), data.get('knowledge_id')
    if not isinstance(upload_id, str) or type(knowledge_id) is not int:
        return jsonify(error='请选择要收录的资料和知识点。'), 400
    try:
        with db() as conn:
            if not conn.execute('SELECT 1 FROM knowledge_points WHERE id=%s', (knowledge_id,)).fetchone():
                return jsonify(error='知识点不存在。'), 400
    except psycopg.Error:
        return jsonify(error='数据库暂不可用，请检查本机 PostgreSQL。'), 503
    record = read_upload(ROOT, upload_id)
    if record is None:
        return jsonify(error='资料不存在。'), 404
    try:
        with db() as conn:
            return jsonify(adopt_content(conn, EMBEDDER, knowledge_id=knowledge_id, title=record['title'],
                                         content=record['content'], source_title=record['filename'],
                                         source_url=f'upload:{upload_id}', section='')), 201
    except EmbeddingError as exc:
        return jsonify(error=str(exc)), 502
    except psycopg.Error:
        return jsonify(error='数据库暂不可用，资料未收录。'), 503


@app.get('/api/health')
def health():
    database_ok = False
    try:
        with db() as conn:
            database_ok = conn.execute('SELECT current_database() AS name').fetchone()['name'] == 'lecture_demo_prototype'
    except psycopg.Error:
        pass
    return jsonify(database_ok=database_ok, **config_flags(),
                   queue_capacity=4, workers=1, embed_model=EMBEDDER.model,
                   embedding_configured=EMBEDDER.configured(),
                   note='本机配置与数据库检查；未调用模型，不能证明密钥有效、服务可达或支持嵌入接口。')


@app.get('/api/generation-runs')
def generation_history():
    with db() as conn:
        rows = recent_runs(conn)
    return jsonify(runs=rows)


@app.get('/api/generation-runs/<int:run_id>')
def generation_detail(run_id):
    with db() as conn:
        row = run_detail(conn, run_id)
    return jsonify(row) if row else (jsonify(error='生成记录不存在。'), 404)


@app.get('/api/generation-runs/<int:run_id>/input')
def generation_input(run_id):
    with db() as conn:
        row = run_detail(conn, run_id)
    if not row:
        return jsonify(error='生成记录不存在。'), 404
    if not row['integrity_valid'] or row['request_payload'] is None:
        return jsonify(error='输入记录的校验值或格式不匹配，请核查存储记录。'), 409
    response = Response(row['request_body'], content_type='application/json; charset=utf-8')
    response.headers['Content-Disposition'] = f'attachment; filename=generation-{run_id}-input.json'
    return response


@app.get('/api/lectures/<int:lecture_id>')
def lecture(lecture_id):
    with db() as conn:
        row = conn.execute('SELECT * FROM lectures WHERE id=%s',(lecture_id,)).fetchone()
        if row:
            trace = conn.execute('SELECT id FROM generation_runs WHERE lecture_id=%s', (lecture_id,)).fetchone()
            row['generation_run_id'] = trace['id'] if trace else None
            row['quality_report'] = row['citation_check'].get('quality_report') or check_output(row['content'], row['context'], row['goal'])
            row['quality_report_origin'] = '生成时留存' if 'quality_report' in row['citation_check'] else '本次读取时检查，未改写原稿'
    return jsonify(row) if row else (jsonify(error='讲义不存在。'),404)


@app.get('/api/lectures/<int:lecture_id>/review')
def lecture_review(lecture_id):
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        bundle = review_bundle(conn, lecture_id)
    return jsonify(bundle)


@app.get('/api/lectures/<int:lecture_id>/profile-comparison')
def lecture_profile_comparison(lecture_id):
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        row = conn.execute('SELECT * FROM lectures WHERE id=%s', (lecture_id,)).fetchone()
        if not row:
            return jsonify(error='讲义不存在。'), 404
        result = compare_lecture_profile(conn, row)
    return jsonify(result)


@app.get('/api/lectures/<int:lecture_id>/comparison-options')
def lecture_comparison_options(lecture_id):
    if lecture_id > 2**63-1:
        raise ReviewError('请选择有效的讲义。')
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        result = comparison_options(conn, lecture_id)
    return jsonify(result)


@app.get('/api/lectures/<int:lecture_id>/comparison')
def lecture_comparison(lecture_id):
    values = []
    for key, default in (('other_id', ''), ('left_revision', '0'), ('right_revision', '0')):
        raw = request.args.get(key, default)
        if not re.fullmatch(r'[0-9]{1,19}', raw):
            raise ReviewError('请选择有效的讲义与版本。')
        values.append(int(raw))
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        result = compare_lectures(conn, lecture_id, *values)
    return jsonify(result)


@app.post('/api/lectures/<int:lecture_id>/issues')
def lecture_issue(lecture_id):
    with db() as conn:
        add_issue(conn, lecture_id, request.get_json(silent=True))
        bundle = review_bundle(conn, lecture_id)
    return jsonify(bundle), 201


@app.post('/api/lectures/<int:lecture_id>/revisions')
def lecture_revision(lecture_id):
    with db() as conn:
        saved = save_revision(conn, lecture_id, request.get_json(silent=True))
        bundle = review_bundle(conn, lecture_id)
    return jsonify(**bundle, saved_revision=saved['revision_number']), 201


@app.errorhandler(psycopg.Error)
def database_error(_error):
    return jsonify(error='数据库暂不可用。请检查本机 PostgreSQL，并运行初始化命令。'),503


def build_reference_text(row):
    sources = '\n'.join(
        f'- [M{m["id"]}] {m["source_title"]}: {m["source_url"]}\n  {m["section"]}'
        for m in row['context']['materials']
    )
    warning = row['citation_check'].get('review_warning', '')
    text = row.get('export_note', '') + row['content'] + '\n\n## 参考材料\n\n' + sources
    text += '\n\n说明：虚拟学生演示；模型生成内容需人工审核。\n'
    if warning:
        text += '\n审核提醒：' + warning + '\n'
    return text


def build_export_text(row):
    """用于 txt 下载：把 Markdown 标记去掉一部分，保持可读。"""
    content = build_reference_text(row)
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            stripped = stripped.lstrip('#').strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            stripped = '· ' + stripped[2:]
        lines.append(stripped)
    return '\n'.join(lines)


def build_export_html(row):
    """把模型返回的 Markdown 做保守转换；所有模型文本都先转义。"""
    body_parts = []
    in_list = False

    def inline_markdown(text):
        text = html.escape(text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\[M(\d+)\]', r'<span class="citation">[M\1]</span>', text)
        return text

    for raw in row['content'].splitlines():
        line = raw.strip()
        if not line:
            if in_list:
                body_parts.append('</ul>')
                in_list = False
            continue

        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        item = re.match(r'^([-*]|\d+[.、])\s+(.+)$', line)

        if heading:
            if in_list:
                body_parts.append('</ul>')
                in_list = False
            level = len(heading.group(1))
            body_parts.append(f'<h{level}>{inline_markdown(heading.group(2))}</h{level}>')
        elif item:
            if not in_list:
                body_parts.append('<ul>')
                in_list = True
            body_parts.append(f'<li>{inline_markdown(item.group(2))}</li>')
        else:
            if in_list:
                body_parts.append('</ul>')
                in_list = False
            body_parts.append(f'<p>{inline_markdown(line)}</p>')

    if in_list:
        body_parts.append('</ul>')

    sources_html = []
    for m in row['context']['materials']:
        sources_html.append(
            '<li>'
            f'<strong>[M{m["id"]}]</strong> '
            f'{html.escape(m["source_title"])}：'
            f'<a href="{html.escape(m["source_url"])}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(m["source_url"])}</a>'
            f'<br><span>{html.escape(m["section"])}</span>'
            '</li>'
        )

    warning = row['citation_check'].get('review_warning', '')
    warning_html = f'<p class="warning">审核提醒：{html.escape(warning)}</p>' if warning else ''
    student_name = html.escape(row['context']['student']['name'])
    topic_title = html.escape(row['context']['topic']['title'])

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{student_name} · {topic_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Noto Sans SC", "Microsoft YaHei", sans-serif;
       max-width: 820px; margin: 40px auto; padding: 0 24px; line-height: 1.9; color: #162235; }}
h1 {{ font-size: 28px; }}
h2 {{ font-size: 20px; margin-top: 32px; border-bottom: 1px solid #e4e8ef; padding-bottom: 8px; }}
.citation {{ color: #2858dc; font-size: 13px; font-weight: 600; }}
.meta {{ color: #68758a; font-size: 13px; margin-bottom: 28px; }}
.warning {{ background: #f3f6fb; padding: 12px 15px; border-radius: 6px; color: #66738a; font-size: 13px; }}
a {{ color: #4268ba; }}
</style>
</head>
<body>
<h1>{topic_title}</h1>
<p class="meta">学生：{student_name} · 模型：{html.escape(row['model'])} · 已保存 #{row['id']}</p>
<p class="meta" style="white-space:pre-wrap">{html.escape(row.get('export_note', ''))}</p>
{''.join(body_parts)}
<h2>{html.escape(row.get('source_heading', '参考材料'))}</h2>
<ul>{''.join(sources_html)}</ul>
<p class="meta">说明：虚拟学生演示；模型生成内容需人工审核。</p>
{warning_html}
</body>
</html>'''


def build_export_docx(row):
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Noto Sans SC'
    style.font.size = Pt(11)

    if row.get('export_note'):
        doc.add_paragraph(row['export_note'])

    for raw in row['content'].splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('### '):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith('## '):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith('# '):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith('- ') or line.startswith('* '):
            doc.add_paragraph(line[2:].strip(), style='List Bullet')
        else:
            doc.add_paragraph(line)

    doc.add_heading(row.get('source_heading', '参考材料'), level=2)
    for m in row['context']['materials']:
        doc.add_paragraph(
            f'[M{m["id"]}] {m["source_title"]}: {m["source_url"]} — {m["section"]}',
            style='List Bullet'
        )

    doc.add_paragraph('说明：虚拟学生演示；模型生成内容需人工审核。')
    warning = row['citation_check'].get('review_warning', '')
    if warning:
        doc.add_paragraph('审核提醒：' + warning)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def export_response(row, markdown, note, filename):
    fmt = request.args.get('format', 'md').lower()
    if fmt not in {'md', 'txt', 'html', 'docx'}:
        return jsonify(error='不支持的下载格式。'), 400
    export_row = {**row, 'export_note': note}
    if fmt == 'md':
        content, mime = markdown, 'text/markdown; charset=utf-8'
    elif fmt == 'txt':
        content, mime = build_export_text(export_row), 'text/plain; charset=utf-8'
    elif fmt == 'html':
        content, mime = build_export_html(export_row), 'text/html; charset=utf-8'
    else:
        content = build_export_docx(export_row).getvalue()
        mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    response = Response(content, content_type=mime)
    name = filename + '.' + fmt
    response.headers['Content-Disposition'] = (
        f"attachment; filename=lecture-{row['id']}.{fmt}; filename*=UTF-8''{quote(name)}")
    return response


@app.get('/api/lectures/<int:lecture_id>/download')
def download_lecture(lecture_id):
    with db() as conn:
        row = conn.execute('SELECT * FROM lectures WHERE id=%s',(lecture_id,)).fetchone()
    if not row:
        return jsonify(error='讲义不存在。'),404
    sources = '\n'.join(f'- [M{m["id"]}] {m["source_title"]}: {m["source_url"]}\n  {m["section"]}'
                        for m in row['context']['materials'])
    warning = row['citation_check'].get('review_warning','')
    content = '导出版本：模型原稿 #'+str(lecture_id)+'；审核状态：原稿未审核（教师修订另行导出）。\n\n'+row['content']+'\n\n## 参考材料\n\n'+sources+'\n\n说明：虚拟学生演示；模型生成内容需人工审核。\n'
    if warning:
        content += '\n审核提醒：'+warning+'\n'
    note = '导出版本：模型原稿 #'+str(lecture_id)+'；审核状态：原稿未审核（教师修订另行导出）。\n\n'
    return export_response(row, content, note, f'{row["context"]["student"]["name"]}-讲义-{lecture_id}')


@app.get('/api/lectures/<int:lecture_id>/revisions/<int:number>/download')
def download_revision(lecture_id, number):
    if number == 0:
        return download_lecture(lecture_id)
    with db() as conn:
        lecture = lecture_row(conn, lecture_id)
        revision = version_row(conn, lecture, number)
    sources = '\n'.join(f'- [M{m["id"]}] {m["source_title"]}: {m["source_url"]}\n  {m["section"]}' for m in lecture['context']['materials'])
    duration = f'{revision["review_seconds"]} 秒' if revision['review_seconds'] else '未记录'
    note = (f'导出版本：教师修订稿，讲义 #{lecture_id} / R{number}\n'
               f'保存时审核状态：{STATUS_LABELS[revision["status"]]}\n'
               f'记录者：{revision["recorder"]}；保存时间：{revision["created_at"].isoformat()}\n'
               f'修改理由：{revision["reason"]}\n审核/修改用时（记录者自报）：{duration}\n\n')
    content = (note + revision['content'] + '\n\n## 参考材料（原生成时快照）\n\n' + sources
               + '\n\n说明：虚拟学生演示；审核状态为该版本保存时的教师记录，不是程序质量认证。\n')
    export_row = {**lecture, 'content': revision['content'],
                  'source_heading': '参考材料（原生成时快照）',
                  'citation_check': {'review_warning': '审核状态为该版本保存时的教师记录，不是程序质量认证。'}}
    return export_response(export_row, content, note, f'lecture-{lecture_id}-R{number}')


if __name__ == '__main__':
    TASKS.recover()
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT','5055')), debug=False, threaded=True)
