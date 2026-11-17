"""Local teaching workspace with separate language, vision and embedding services."""
import json
import math
import os
import re
import statistics
import time
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from model_settings import register_model_settings
from flask import Flask, Response, jsonify, render_template, request, g, has_request_context
from profiles import CLASS_STRATEGIES, ProfileError, compare_lecture_profile, decorate_states, evidence_history, register_profiles
from prompts import prepare_request
from vision import selected_evidence
from qwen import ModelError, ModelResult, call_qwen, config_flags, stream_to, visible_text
from progress import HUB
import figures
import sectioned
import designed
from runs import (create_run, fail_run, interrupted_runs, mark_attempt, recent_costs, recent_runs, run_detail, save_candidate,
                  save_success)
from quality import assess_scope, check_output
from reviews import ReviewError, add_issue, review_bundle, save_revision
from courses import TOPICS, REFERENCE_EXAMPLES, course_family  # noqa: F401  (experiment.py reads app.TOPICS)
from catalog import load_catalog, register_catalog
from deletion import register_deletion
from exports import register_exports
from retrieval_api import register_retrieval
from workspace import register_workspace
from classes import class_context_states, class_members, library_ids, register_classes, student_class
from comparisons import comparison_options, compare_lectures
from jobs import JobError, JobQueue
from gate import ModelGate, configured_limit
from teaching import teaching_options
from model_runtime import request_timeout
from lecture_policy import LectureInputError, finalize_context
from sections import execute_section, register_sections
from presentations import execute_presentation, register_presentations
from packages import register_packages
from roster_import import register_roster_import
from quizzes import QuizQueue, grading_reference, register_quizzes
from knowledge import KnowledgeQueue, register_knowledge
from grading import register_grading
from evaluation import register_evaluation
from edition import data_dir, release
from uploads import ImportError as UploadError, register_uploads
from embed import EmbeddingError, QwenEmbedder
from retrieval import (MODES as RETRIEVAL_MODES, RetrievalError, build_query,
                       index_knowledge, search as search_chunks, to_materials)

# Model settings (.env) and runtime stores (.runtime) live in the data folder: the repository in
# development, the user's own folder in the desktop release.
DATA = data_dir()
load_dotenv(DATA / '.env')
app = Flask(__name__)
register_model_settings(app, DATA / '.env')
app.config['MAX_CONTENT_LENGTH'] = 11 * 1024 * 1024
register_uploads(app, DATA, lambda course_id, class_id: upload_scope(course_id, class_id))
DSN = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
MODEL = os.environ.get('QWEN_MODEL', 'qwen3.8-27b')
# One gate for every language-model call; MODEL_CONCURRENCY=1 restores strictly serial calls.
GENERATION_LOCK = ModelGate(configured_limit())
# QUIZZES is created below; the lambda resolves it when a grading upload names a quiz.
recover_grading = register_grading(app, DATA, lambda prepared, attempt: call_qwen(prepared, attempt), GENERATION_LOCK,
                                   quiz_reference=lambda job_id: grading_reference(QUIZZES.detail(job_id)))
EMBEDDER = QwenEmbedder()
RETRIEVAL_LIMIT = 6


def db():
    return psycopg.connect(DSN, row_factory=dict_row, connect_timeout=5)


def upload_scope(course_id, class_id):
    with db() as conn:
        if not conn.execute('SELECT 1 FROM courses WHERE id=%s', (course_id,)).fetchone():
            raise UploadError('课程不存在，请刷新后重试。')
        if class_id is not None and not conn.execute('SELECT 1 FROM classes WHERE id=%s AND course_id=%s',
                                                     (class_id, course_id)).fetchone():
            raise UploadError('班级不属于所选课程，请刷新后重试。')


register_catalog(app, lambda: db(), DATA)
register_deletion(app, lambda: db(), DATA)


def topic_catalog():
    if has_request_context() and hasattr(g, 'course_topics'):
        return g.course_topics
    with db() as conn:
        result = load_catalog(conn)
    if has_request_context():
        g.course_topics = result
    return result


def scoped_materials(conn, knowledge_ids, class_id):
    """Course-wide material plus, when a class is in scope, that class's own material."""
    rows = conn.execute('''SELECT * FROM materials WHERE knowledge_id=ANY(%s)
        AND (class_id IS NULL OR class_id=%s) ORDER BY id''', (knowledge_ids, class_id)).fetchall()
    # Older inputs carry no class field; add it only where a material is class-only.
    return [{k: v for k, v in row.items() if k != 'class_id' or v is not None} for row in rows]


register_classes(app, lambda: db(), topic_catalog)


def course_knowledge_ids(course_id):
    """The course library first (diagnoses are kept per library point), then lesson-only points."""
    with db() as conn:
        shelf = library_ids(conn, course_id)
    return list(dict.fromkeys(shelf + [k for t in topic_catalog().values() if t.get('course_id') == course_id
                                       for k in t['knowledge_ids']]))


def get_context(student_id, topic_key, mode='profile', retrieval_mode='sql', goal=None, learner_note='', class_id=None):
    """Frozen generation input for one student, or for a whole class when class_id is given.

    A class target uses mode 'class' (aggregated class profile) or 'none' (generic lecture);
    either way the class's own material is in scope. Returns None for an invalid target.
    """
    topic = topic_catalog()[topic_key]
    whole_class = class_id is not None
    if whole_class:
        mode = 'none' if mode == 'none' else 'class'
    elif mode == 'class' or student_id not in topic['student_ids']:
        return None
    if retrieval_mode == 'vector':
        # Prepare missing or stale chunks before opening the read-only snapshot.
        with db() as conn:
            index_knowledge(conn, EMBEDDER, topic['knowledge_ids'])
    with db() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        if whole_class:
            klass = conn.execute('''SELECT c.*,co.name AS course_name FROM classes c JOIN courses co ON co.id=c.course_id
                WHERE c.id=%s''', (class_id,)).fetchone()
            if not klass or klass['course_id'] != topic.get('course_id'):
                return None
            members = class_members(conn, class_id)
            if not members:
                return None
            scope = class_id
            states = class_context_states(conn, members, topic['knowledge_ids'])
        else:
            student = conn.execute('SELECT * FROM students WHERE id=%s', (student_id,)).fetchone()
            if not student:
                return None
            scope = student_class(conn, student_id, topic.get('course_id'))
            states = conn.execute('''SELECT k.id, k.name, COALESCE(a.canonical_id,k.id) AS profile_knowledge_id, COALESCE(s.state,'unknown') AS state,
                COALESCE(s.evidence,'没有诊断记录。') AS evidence,
                COALESCE(s.revision,0) AS revision,s.evidence_id,s.updated_at
                FROM knowledge_points k LEFT JOIN knowledge_aliases a ON a.knowledge_id=k.id
                LEFT JOIN student_knowledge s ON s.knowledge_id=COALESCE(a.canonical_id,k.id) AND s.student_id=%s
                WHERE k.id=ANY(%s) ORDER BY k.id''', (student_id,topic['knowledge_ids'])).fetchall()
        # Exact SQL lookup, restricted to the selected topic.
        materials = scoped_materials(conn, topic['knowledge_ids'], scope)
        retrieval = {'mode': retrieval_mode, 'backend': None, 'embed_model': None, 'query': None, 'items': []}
        if retrieval_mode == 'vector':
            # Without a profile the question stays the shared goal, so ablation is still clean.
            query = build_query(goal or topic['goal'], states if mode in ('profile', 'class') else [])
            if mode == 'profile' and learner_note:
                query += '；本次学生备注：' + learner_note
            retrieval = search_chunks(conn, EMBEDDER, query, topic['knowledge_ids'], limit=RETRIEVAL_LIMIT, class_id=scope)
            materials = to_materials(retrieval['items'])
        # Fetch each point separately so a frequently updated point cannot crowd
        # another point's evidence out of the model input.
        recent = ({s['id']: s.pop('recent_evidence') for s in states} if whole_class else
                  {s['id']: evidence_history(conn, student_id, [s['profile_knowledge_id']], limit=3) for s in states})
        topic = {**topic, 'canonical_knowledge_ids': {str(s['id']): s['profile_knowledge_id'] for s in states}}
    decorate_states(states)
    for s in states:
        s['recent_evidence'] = recent[s['id']]
        if whole_class:
            s['strategy'] = CLASS_STRATEGIES[s['state']]
    if whole_class:
        student = {'name': f"{klass['name']}（全班）", 'subtitle': f"{klass['course_name']} · {len(members)} 名学生",
                   'profile': klass['profile'] or '教师尚未填写班级特点。'}
    ctx = {'student':student, 'topic':topic, 'states':states, 'materials':materials, 'retrieval': retrieval}
    ctx['lecture_length_policy'] = 'expanded-v1'
    ctx['personalization_mode'] = mode
    if whole_class:
        # Recorded with the run; the class name and size are teaching context, not learner identifiers.
        ctx['class_target'] = {'id': class_id}
        if mode == 'class':
            ctx['class_profile'] = {'id': class_id, 'name': klass['name'], 'course': klass['course_name'],
                                    'student_count': len(members), 'profile_revision': klass['profile_revision']}
    if mode in ('profile', 'class') and learner_note:
        ctx['learner_note'] = learner_note
    if course_family(topic) == 'probability':
        ctx['reference_examples'] = REFERENCE_EXAMPLES[:1] if (topic.get('reference_origin') or topic_key).endswith('basic') else REFERENCE_EXAMPLES
    if mode == 'none':
        # Paired runs retain their selected student ID in the database, while the
        # actual model context contains no learner identifiers or diagnostics.
        ctx['student'] = {'name': '通用讲义', 'profile': '未提供学生画像；使用通用适中解释。'}
        ctx['states'] = []
    ctx['lecture_workflow'] = 'teacher-v2'
    ctx = finalize_context(ctx, goal or topic['goal'])
    ctx['scope_check'] = assess_scope(ctx, goal or topic['goal'])
    return ctx


LOCAL_HOSTNAMES = ('localhost', '127.0.0.1', '::1')


def addressed_locally():
    """The request names this computer in its Host header. A web page that points its own domain
    at 127.0.0.1 (DNS rebinding) still sends its own name, so it can neither read the workspace
    nor pass the Origin check below, whose comparison uses the same header."""
    try:
        return urlsplit('http://' + request.host).hostname in LOCAL_HOSTNAMES
    except ValueError:
        return False


@app.before_request
def local_origin_only():
    if not addressed_locally():
        if request.path.startswith('/api/'):
            return jsonify(error='请通过本机地址（127.0.0.1）访问工作空间。'), 403
        return Response('请通过本机地址（127.0.0.1）访问工作空间。', 403, mimetype='text/plain')
    # Keep the local generation endpoint unavailable to cross-origin web pages.
    if request.method in ('POST', 'PUT') and request.path not in ('/api/uploads', '/api/grading') and request.content_length and request.content_length > 131072:
        return jsonify(error='提交内容过长，请缩短后重试。'), 413
    origin = request.headers.get('Origin')
    if request.method in ('POST', 'PUT') and origin and origin != request.host_url.rstrip('/'):
        return jsonify(error='请从知页页面发起请求。'), 403


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
    return render_template('index.html', release=release())


@app.get('/sw.js')
def service_worker():
    """Served from the root so its scope covers the whole page, not just /static/."""
    response = app.send_static_file('sw.js')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@app.get('/manifest.webmanifest')
def web_manifest():
    response = app.send_static_file('manifest.webmanifest')
    response.headers['Content-Type'] = 'application/manifest+json; charset=utf-8'
    return response


@app.get('/api/bootstrap')
def bootstrap():
    with db() as conn:
        students = conn.execute('SELECT * FROM students ORDER BY id').fetchall()
        history = conn.execute('''SELECT l.id,COALESCE(s.name,c.name||'（全班）') AS name,l.topic,l.created_at,
            l.student_id,l.class_id FROM lectures l LEFT JOIN students s ON s.id=l.student_id
            LEFT JOIN classes c ON c.id=l.class_id ORDER BY l.id DESC LIMIT 8''').fetchall()
    return jsonify(students=students, topics=topic_catalog(), model=MODEL, history=history,
                   database='PostgreSQL · 已连接', configured=config_flags()['endpoint_format_ok'] and config_flags()['model_present'],
                   embed_model=EMBEDDER.model)


@app.get('/api/context')
def context():
    student_id = request.args.get('student_id', type=int)
    class_id = request.args.get('class_id', type=int)
    topic = request.args.get('topic', 'linear')
    goal = request.args.get('goal', '').strip()
    mode = request.args.get('personalization_mode', 'profile')
    retrieval_mode = request.args.get('retrieval_mode', 'sql')
    note = learner_note(request.args)
    if ((student_id is None) == (class_id is None) or topic not in topic_catalog() or len(goal) > 400
            or mode not in ('profile', 'none', 'class') or retrieval_mode not in RETRIEVAL_MODES):
        return jsonify(error='请选择有效学生或班级，以及备课。'), 400
    data = get_context(student_id, topic, mode, retrieval_mode, goal, note, class_id)
    if data:
        data['scope_check'] = assess_scope(data, goal or topic_catalog()[topic]['goal'])
    return (jsonify(data) if data else (jsonify(error='班级不属于这份备课的课程，或班级还没有学生。' if class_id is not None
                                                     else '学生不存在。'), 404))


register_profiles(app, lambda: db(), course_knowledge_ids)


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
    if type(sid) is not int or not isinstance(topic_key, str) or topic_key not in topic_catalog():
        return jsonify(error='请选择有效学生和主题。'), 400
    if not isinstance(goal, str) or len(goal.strip()) > 400:
        return jsonify(error='教学目标请控制在 400 字以内。'), 400
    mode = data.get('personalization_mode', 'profile')
    if mode not in ('profile', 'none'):
        return jsonify(error='请选择有画像或无画像模式。'), 400
    retrieval_mode = data.get('retrieval_mode', 'sql')
    note = learner_note(data)
    options = validated_teaching_options(data)
    if retrieval_mode not in RETRIEVAL_MODES:
        return jsonify(error='请选择精确 SQL 或向量检索。'), 400
    goal = goal.strip() or topic_catalog()[topic_key]['goal']
    evidence = selected_evidence(DATA, data.get('visual_analysis_ids', []))
    if not GENERATION_LOCK.acquire(blocking=False):
        return jsonify(error='已有讲义正在生成，请等待完成后再试。'), 429
    result, status = generate_locked(sid, topic_key, goal, mode, retrieval_mode=retrieval_mode,
                                     visual_evidence=evidence, learner_note=note, teaching_options=options,
                                     plan_overrides=data.get('plan_overrides'))
    return jsonify(result), status


def generate_locked(sid, topic_key, goal, mode='profile', frozen=None, prepared=None, run_id=None,
                    job_id=None, retrieval_mode='sql', visual_evidence=None, learner_note='', teaching_options=None, plan_overrides=None):
    # Caller owns GENERATION_LOCK; release it for every result and exception.
    output, check, stage = ModelResult(''), {}, 'preparation'
    start = time.monotonic()
    try:
        ctx = frozen or get_context(sid, topic_key, mode, retrieval_mode, goal, learner_note)
        if not ctx:
            return {'error': '学生不存在。'}, 404
        if frozen is None and visual_evidence:
            ctx['visual_evidence'] = visual_evidence
        if frozen is None and teaching_options:
            ctx['teaching_options'] = teaching_options
        if frozen is None:
            ctx = finalize_context(ctx, goal, plan_overrides)
            ctx['scope_check'] = assess_scope(ctx, goal)
        prepared = prepared or prepare_request(ctx, goal, MODEL)
        if run_id is None:
            with db() as conn:
                run_id = create_run(conn, sid, topic_key, goal, ctx, prepared)
        if not ctx['materials'] and not ctx['topic'].get('preparation_copy'):
            raise ModelError('no_materials', '当前主题没有课程材料，请先导入资料。', 'preparation')
        def on_attempt():
            with db() as conn:
                mark_attempt(conn, run_id)
        stage = 'model'
        if ctx.get('generation_mode') == designed.MODE:
            HUB.start(run_id, [('design', '课堂设计')])
            output = designed.generate(lambda request, attempted: call_qwen(request, attempted), GENERATION_LOCK,
                                       prepared, ctx, goal, on_attempt,
                                       publish=lambda key, text, state: HUB.write(run_id, key, text, state),
                                       announce=lambda label: HUB.stage(run_id, label),
                                       declare=lambda parts: HUB.add(run_id, parts))
            if output.metadata.get('operation') == 'lesson_design':
                # A design waiting for the teacher: a finished run, but not a lecture yet.
                stage = 'storage'
                with db() as conn:
                    save_candidate(conn, run_id, output, round(time.monotonic() - start, 2))
                    if job_id:
                        conn.execute("UPDATE generation_jobs SET status='succeeded',finished_at=now() WHERE id=%s", (job_id,))
                return dict(design=output.metadata['design'], generation_run_id=run_id), 200
            # Saved with the lecture, so every later document is derived from the same design.
            ctx = {**ctx, 'lesson_design': output.metadata['design']}
        elif ctx.get('generation_mode') == sectioned.MODE:
            HUB.start(run_id, [(q['title'], q['title']) for q in ctx['sectioned']['quotas']])
            output = sectioned.generate(lambda request, attempted: call_qwen(request, attempted), GENERATION_LOCK,
                                        prepared, ctx, goal, on_attempt,
                                        publish=lambda key, text, state: HUB.write(run_id, key, text, state),
                                        announce=lambda label: HUB.stage(run_id, label))
        else:
            HUB.start(run_id, [('body', '')])
            HUB.stage(run_id, '模型写作中')
            pieces = []

            def on_piece(text):
                pieces.append(text)
                HUB.write(run_id, 'body', visible_text(''.join(pieces)))
            with stream_to(on_piece):
                output = call_qwen(prepared, on_attempt)
        stage = 'output'
        cited = set(re.findall(r'\[M(\d+)\]', output.content))
        available = {str(m['id']) for m in ctx['materials']}
        invalid = sorted(cited - available)
        check = {'cited_ids': sorted(cited), 'has_citations': bool(cited), 'invalid_ids': invalid,
                 'note': '仅核对引用编号；内容正确性与引用支持关系仍需人工检查。'}
        check['quality_report'] = check_output(output.content, ctx, goal)
        if invalid or check['quality_report']['invalid_source_ids']:
            raise ModelError('invalid_citation', '模型引用了不存在的材料，未保存为完整讲义，请重试。',
                             'output', output)
        elapsed = round(time.monotonic() - start, 2)
        stage = 'storage'
        with db() as conn:
            row = save_success(conn, run_id, sid, goal, ctx, prepared, output, elapsed, check)
            if job_id:
                conn.execute("UPDATE generation_jobs SET status='succeeded',finished_at=now() WHERE id=%s", (job_id,))
        return dict(**row, content=output.content, context=ctx, model=json.loads(prepared.body)['model'], elapsed_seconds=elapsed,
                    citation_check=check, usage=output.usage, goal=goal, generation_run_id=run_id), 200
    except LectureInputError as exc:
        error = ModelError('input_budget', str(exc), 'preparation')
        status = 400
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
        if run_id is not None:
            HUB.finish(run_id)
    saved = persist_failure(run_id, error, round(time.monotonic() - start, 2), check, job_id)
    message = str(error)
    if run_id is not None and not saved:
        message += ' 生成输入已留存，但结束状态未能更新，需复核该记录。'
    return dict(error=message, generation_run_id=run_id, failure_recorded=saved), status


def execute_job(job_id, run_id, sid, topic, goal, context, prepared):
    GENERATION_LOCK.acquire()
    try:
        with db() as conn:
            row = conn.execute("UPDATE generation_jobs SET status='running',started_at=now() WHERE id=%s AND status='queued' RETURNING id", (job_id,)).fetchone()
            if not row:
                GENERATION_LOCK.release()
                return
    except Exception:
        GENERATION_LOCK.release()
        raise
    if context.get('section_edit') or context.get('ppt_export'):
        start = time.monotonic()
        try:
            execute = execute_presentation if context.get('ppt_export') else execute_section
            return execute(lambda: db(), call_qwen, job_id, run_id, context, prepared)
        except ModelError as exc:
            persist_failure(run_id, exc, round(time.monotonic() - start, 2), {}, job_id)
        except Exception:
            persist_failure(run_id, ModelError('output_error', '课件或章节处理未完成，请核查生成记录后重试。', 'output'),
                            round(time.monotonic() - start, 2), {}, job_id)
        finally:
            GENERATION_LOCK.release()
        return
    return generate_locked(sid, topic, goal, context.get('personalization_mode', 'profile'),
                           context, prepared, run_id, job_id)


TASKS = JobQueue(lambda: db(), execute_job, capacity=GENERATION_LOCK.limit + 3, workers=GENERATION_LOCK.limit)
register_sections(app, lambda: db(), lambda: TASKS, lambda: MODEL)
register_presentations(app, lambda: db(), lambda: TASKS)
designed.register_designs(app, lambda: db(), lambda: TASKS, lambda: MODEL)
register_packages(app, lambda: db())
register_roster_import(app, lambda: db())
QUIZZES = QuizQueue(lambda: db(), lambda prepared, attempted: call_qwen(prepared, attempted), GENERATION_LOCK)
register_quizzes(app, lambda: db(), lambda: QUIZZES, topic_catalog, get_context, lambda: MODEL)
KNOWLEDGE = KnowledgeQueue(lambda: db(), DATA, lambda prepared, attempted: call_qwen(prepared, attempted),
                           GENERATION_LOCK, lambda: MODEL)
register_knowledge(app, lambda: db(), DATA, lambda: KNOWLEDGE, topic_catalog)
# The fixed experiment compares the built-in probability lesson, which the release does not carry.
recover_evaluation = (lambda: None) if release() else register_evaluation(
    app, lambda: db(), lambda: TASKS, get_context, lambda: MODEL,
    lambda: not GENERATION_LOCK.locked() and QUIZZES.wait_idle(0) and KNOWLEDGE.wait_idle(0)
    and not app.extensions['grading_busy']())


@app.errorhandler(JobError)
def job_error(exc):
    return jsonify(error=str(exc)), exc.status


@app.errorhandler(RetrievalError)
def retrieval_error(exc):
    return jsonify(error=str(exc)), 400


@app.errorhandler(EmbeddingError)
def embedding_error(exc):
    return jsonify(error=str(exc)), 502


@app.errorhandler(LectureInputError)
def lecture_input_error(exc):
    return jsonify(error=str(exc)), 400


def learner_note(data):
    note = data.get('learner_note', '')
    if not isinstance(note, str) or len(note.strip()) > 2000:
        raise JobError('本次学生备注请控制在 2000 字以内。')
    return note.strip()


def validated_teaching_options(data):
    try:
        return teaching_options(data)
    except ValueError as exc:
        raise JobError(str(exc)) from None


def job_input(data):
    if not isinstance(data, dict):
        raise JobError('请求格式必须是 JSON 对象。')
    learner_note(data)
    validated_teaching_options(data)
    sid, topic = data.get('student_id'), data.get('topic')
    class_id = data.get('class_id')
    goal, mode = data.get('goal', ''), data.get('personalization_mode', 'profile')
    # Exactly one target: a student, or a whole class.
    if class_id is not None:
        if type(class_id) is not int or sid is not None:
            raise JobError('请选择一个学生或一个班级作为讲义对象。')
    elif type(sid) is not int:
        raise JobError('请选择有效学生和主题。')
    if not isinstance(topic, str) or topic not in topic_catalog():
        raise JobError('请选择有效学生和主题。')
    if not isinstance(goal, str) or len(goal.strip()) > 400:
        raise JobError('教学目标请控制在 400 字以内。')
    if mode not in ('profile', 'none', 'class') or (mode == 'class' and class_id is None):
        raise JobError('请选择有画像或无画像模式。')
    retrieval_mode = data.get('retrieval_mode', 'sql')
    if retrieval_mode not in RETRIEVAL_MODES:
        raise JobError('请选择精确 SQL 或向量检索。')
    return sid, topic, goal.strip() or topic_catalog()[topic]['goal'], mode, retrieval_mode, class_id


def pipeline_options(data):
    """New clients choose how the lecture is produced; requests without these keys stay on the old path."""
    mode, layout = data.get('generation_mode'), data.get('lecture_format')
    if mode is None and layout is None:
        return {}
    if mode not in (None, 'single', sectioned.MODE, designed.MODE) or layout not in (None, 'plain', 'rich-v1'):
        raise JobError('生成方式或公式格式无效，请刷新页面后重试。')
    if mode == designed.MODE:
        # Every document of a designed lesson shares LaTeX formulas and program-drawn figures.
        return dict(lecture_pipeline='v3', generation_mode=mode, lecture_format='rich-v1')
    return dict(lecture_pipeline='v3', generation_mode=mode or 'single', lecture_format=layout or 'plain')


def build_job(data):
    """Validate one submission and freeze its context and request."""
    sid, topic, goal, mode, retrieval_mode, class_id = job_input(data)
    pipeline = pipeline_options(data)
    ctx = get_context(sid, topic, mode, retrieval_mode, goal, learner_note(data), class_id)
    if not ctx:
        raise JobError('班级不属于这份备课的课程，或班级还没有学生。' if class_id is not None else
                       '学生不存在，或不在这份备课副本的学生范围内。', 404)
    evidence = selected_evidence(DATA, data.get('visual_analysis_ids', []))
    if evidence:
        ctx['visual_evidence'] = evidence
    options = validated_teaching_options(data)
    if options:
        ctx['teaching_options'] = options
    ctx.update(pipeline)
    if ctx.get('generation_mode') == sectioned.MODE:
        ctx['sectioned'] = sectioned.freeze(ctx)
    if ctx.get('generation_mode') == designed.MODE:
        if not options:
            raise JobError('按课堂设计生成需要授课设置（时长、例题和练习数量），请刷新页面后重试。')
        # The teacher confirms the design before the lesson is written, unless told otherwise.
        ctx['designed'] = designed.freeze(ctx, stop=data.get('design_review') is not False)
    ctx = finalize_context(ctx, goal, data.get('plan_overrides'))
    ctx['scope_check'] = assess_scope(ctx, goal)
    return sid, topic, goal, ctx, prepare_request(ctx, goal, MODEL)


@app.post('/api/jobs')
def submit_job():
    sid, topic, goal, ctx, prepared = build_job(request.get_json(silent=True))
    return jsonify(TASKS.submit(sid, topic, goal, ctx, prepared)), 202


@app.post('/api/jobs/batch')
def submit_batch():
    """Queue the same lesson for several students; each gets its own frozen input and run."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise JobError('请求格式必须是 JSON 对象。')
    ids = data.get('student_ids')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 60 or any(type(i) is not int for i in ids)
            or len(set(ids)) != len(ids)):
        raise JobError('请选择 1—60 名学生。')
    results = []
    for sid in ids:
        try:
            # The per-topic plan is individual, so a batch always uses each student's default plan.
            # A batch is always per student; a class target on the form means "each classmate".
            # Nobody confirms sixty designs one by one: a batch writes each lesson straight through.
            each = {**{k: v for k, v in data.items() if k != 'class_id'}, 'student_id': sid, 'plan_overrides': None,
                    'design_review': False}
            if each.get('personalization_mode') == 'class':
                each['personalization_mode'] = 'profile'
            _, topic, goal, ctx, prepared = build_job(each)
            job = TASKS.submit(sid, topic, goal, ctx, prepared, bulk=True)
            results.append(dict(student_id=sid, job_id=job['id'], run_id=job['run_id'], status=job['status']))
        except (JobError, LectureInputError, RetrievalError, EmbeddingError) as exc:
            results.append(dict(student_id=sid, error=str(exc)))
    return jsonify(jobs=results, queued=sum('job_id' in item for item in results)), 202


@app.get('/api/job-status')
def job_status():
    raw = request.args.get('ids', '')
    if not re.fullmatch(r'[0-9a-f]{32}(,[0-9a-f]{32}){0,59}', raw):
        raise JobError('任务编号无效。')
    with db() as conn:
        rows = conn.execute("""SELECT j.id,j.status,j.run_id,r.lecture_id,r.error_message,r.student_id,r.class_id,
            COALESCE(s.name,c.name||'（全班）') AS name,r.elapsed_seconds FROM generation_jobs j
            JOIN generation_runs r ON r.id=j.run_id LEFT JOIN students s ON s.id=r.student_id
            LEFT JOIN classes c ON c.id=r.class_id WHERE j.id=ANY(%s)""", (raw.split(','),)).fetchall()
    return jsonify(jobs=rows)


@app.get('/api/jobs/<job_id>/stream')
def job_stream(job_id):
    """Server-sent live text of one running job. Display only; the saved lecture is checked separately."""
    with db() as conn:
        row = conn.execute('SELECT run_id FROM generation_jobs WHERE id=%s', (job_id,)).fetchone()
    if not row:
        raise JobError('任务不存在。', 404)
    run_id = row['run_id']

    def events():
        version, quiet = 0, 0
        while quiet < 1800:
            change = HUB.changes(run_id, version, timeout=1.0)
            if change is None:
                quiet += 1
                if quiet % 3 == 0:
                    with db() as conn:
                        state = conn.execute('SELECT status FROM generation_jobs WHERE id=%s', (job_id,)).fetchone()
                    if not state or state['status'] not in ('queued', 'running'):
                        break
                    yield ': waiting\n\n'
                continue
            quiet, version = 0, change['version']
            yield 'data: ' + json.dumps(change, ensure_ascii=False) + '\n\n'
            if change['done']:
                break
            time.sleep(0.2)  # coalesce bursts of tokens into a few updates per second
        yield 'event: done\ndata: {}\n\n'
    return Response(events(), mimetype='text/event-stream', headers={'X-Accel-Buffering': 'no'})


@app.post('/api/figures/render')
def render_figures():
    data = request.get_json(silent=True)
    sources = data.get('figures') if isinstance(data, dict) else None
    if not isinstance(sources, list) or len(sources) > 12 or any(not isinstance(s, str) for s in sources):
        return jsonify(error='请提交不超过 12 幅图的描述。'), 400
    return jsonify(figures=[figures.render(source) for source in sources])


@app.get('/api/jobs')
def recent_jobs():
    return jsonify(jobs=TASKS.recent(), capacity=TASKS.capacity, workers=TASKS.workers)


@app.get('/api/jobs/<job_id>')
def job_detail(job_id):
    return jsonify(TASKS.detail(job_id))


@app.post('/api/jobs/<job_id>/cancel')
def cancel_job(job_id):
    return jsonify(TASKS.cancel(job_id))


@app.post('/api/generation-runs/<int:run_id>/retry')
def retry_generation(run_id):
    return jsonify(TASKS.retry(run_id)), 202


@app.get('/api/jobs/estimate')
def estimate_jobs():
    """What N lessons written the given way may cost: the median of the latest 10 complete ones, times N."""
    count, mode = request.args.get('count', ''), request.args.get('mode') or designed.MODE
    if not count.isdigit() or not 1 <= int(count) <= 60 or mode not in ('single', sectioned.MODE, designed.MODE):
        raise JobError('请给出 1—60 的份数和有效的写作方式。')
    count = int(count)
    with db() as conn:
        rows = recent_costs(conn, mode)
    if not rows:
        return jsonify(count=count, basis=0, tokens=None, minutes=None, concurrency=TASKS.workers)
    tokens, seconds = statistics.median(r['tokens'] for r in rows), statistics.median(r['seconds'] for r in rows)
    lanes = max(1, min(TASKS.workers, count))   # lessons written at the same time
    return jsonify(count=count, basis=len(rows), tokens=round(count * tokens), minutes=math.ceil(count * seconds / lanes / 60),
                   concurrency=TASKS.workers)


@app.post('/api/jobs/retry-interrupted')
def retry_interrupted():
    """Send every run a restart cut off again, each with its own saved input, within the batch bound."""
    with db() as conn:
        rows = interrupted_runs(conn)
    queued, skipped = [], []
    for row in rows:
        try:
            job = TASKS.retry(row['id'], bulk=True)
            queued.append(dict(run_id=row['id'], job_id=job['id'], new_run_id=job['run_id']))
        except JobError as exc:
            skipped.append(dict(run_id=row['id'], error=str(exc)))
    return jsonify(queued=len(queued), jobs=queued, skipped=skipped), 202


register_retrieval(app, lambda: db(), topic_catalog, lambda: EMBEDDER, lambda: DATA)


@app.get('/api/health')
def health():
    database_ok = False
    try:
        with db() as conn:
            database_ok = conn.execute('SELECT current_database() AS name').fetchone()['name'] == 'lecture_demo_prototype'
    except psycopg.Error:
        pass
    return jsonify(database_ok=database_ok, **config_flags(),
                   request_timeout_seconds=request_timeout(),
                   queue_capacity=TASKS.capacity, workers=TASKS.workers,
                   model_concurrency=GENERATION_LOCK.limit, embed_model=EMBEDDER.model,
                   embedding_configured=EMBEDDER.configured(),
                   note='本机配置与数据库检查；未调用模型，不能证明密钥有效、服务可达或支持嵌入接口。')


register_workspace(app, lambda: db(), lambda: release())


@app.get('/api/generation-runs')
def generation_history():
    with db() as conn:
        rows, interrupted = recent_runs(conn), len(interrupted_runs(conn))
    return jsonify(runs=rows, interrupted=interrupted)


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
            trace = conn.execute("SELECT id,topic_key FROM generation_runs WHERE lecture_id=%s AND NOT (context ? 'section_edit') AND NOT (context ? 'ppt_export') ORDER BY id LIMIT 1", (lecture_id,)).fetchone()
            row['generation_run_id'] = trace['id'] if trace else None
            row['topic_key'] = trace['topic_key'] if trace else row['context']['topic'].get('key')
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


register_exports(app, lambda: db())


if __name__ == '__main__':
    recover_grading()
    TASKS.recover()
    QUIZZES.recover()
    KNOWLEDGE.recover()
    recover_evaluation()
    # Waitress rather than Flask's development server, still one process: the job queues live in it. A live
    # preview (/api/jobs/<id>/stream) holds one of the threads while it is open, and its events still go out
    # one by one. Only the data folder's .env counts (load_dotenv above); waitress reads none.
    import waitress
    waitress.serve(app, host='127.0.0.1', port=int(os.environ.get('PORT', '5055')), threads=16)
