"""Deleting what a teacher no longer wants kept: an upload, a student, a lecture, a grading record.

Each kind has a preview (GET …/delete-preview) that counts what would go, and a delete (POST …/delete)
that removes it in one PostgreSQL transaction, in foreign-key order, so schema.sql needs no cascades.
Nothing is deleted while a task on it is still queued or running. What other records froze (a
lecture's copy of a material, a quiz's copy of a lecture) stays; the pages say the original is gone.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

from flask import jsonify, request


class DeletionError(ValueError):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status, self.details = status, details


ACTIVE = ('queued', 'running')


def _sqlite(root, name):
    path = Path(root) / '.runtime' / name
    if not path.exists():
        return None
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# --- uploads ---------------------------------------------------------------------------------------

def upload_material_ids(conn, upload_id):
    """Materials taken from this upload: the whole file (upload:<id>) or a part of it (upload:<id>#…)."""
    exact, prefix = f'upload:{upload_id}', f'upload:{upload_id}#'
    return [row['id'] for row in conn.execute(
        'SELECT id FROM materials WHERE source_url=%s OR left(source_url, %s)=%s ORDER BY id',
        (exact, len(prefix), prefix)).fetchall()]


def upload_preview(conn, root, upload_id):
    store = _sqlite(root, 'uploads.sqlite3')
    if store is None:
        raise DeletionError('资料不存在。', 404)
    with closing(store):
        row = store.execute('SELECT id,title FROM uploads WHERE id=?', (upload_id,)).fetchone()
        if row is None:
            raise DeletionError('资料不存在。', 404)
        images = (store.execute('SELECT count(*) FROM upload_images WHERE upload_id=?', (upload_id,)).fetchone()[0]
                  if _table(store, 'upload_images') else 0)
    materials = upload_material_ids(conn, upload_id)
    lectures = conn.execute('''SELECT count(DISTINCT l.id) AS n FROM lectures l, jsonb_array_elements(l.context->'materials') m
        WHERE (m->>'id')::int = ANY(%s)''', (materials,)).fetchone()['n'] if materials else 0
    draft = conn.execute('SELECT status FROM knowledge_drafts WHERE upload_id=%s', (upload_id,)).fetchone()
    return dict(kind='upload', id=upload_id, title=row['title'], materials=len(materials), images=images,
                knowledge_draft=bool(draft), lectures_citing=lectures,
                busy=bool(draft and draft['status'] in ACTIVE))


def delete_upload(conn, root, upload_id, with_materials):
    preview = upload_preview(conn, root, upload_id)
    if preview['busy']:
        raise DeletionError('这份资料的知识点识别还在进行，请等它结束后再删除。', 409)
    if preview['materials'] and not with_materials:
        raise DeletionError(f'这份资料已收录为 {preview["materials"]} 条课程材料。确认一并删除后再试。', 409,
                            materials=preview['materials'])
    materials = upload_material_ids(conn, upload_id)
    assets = [row['id'] for row in conn.execute(
        'SELECT id FROM material_assets WHERE upload_id=%s OR material_id=ANY(%s)', (upload_id, materials)).fetchall()]
    # Runs that showed these images keep their saved request; only the link to the deleted bytes goes.
    conn.execute('DELETE FROM generation_run_assets WHERE asset_id=ANY(%s)', (assets,))
    conn.execute('DELETE FROM material_assets WHERE id=ANY(%s)', (assets,))
    conn.execute('DELETE FROM material_chunks WHERE material_id=ANY(%s)', (materials,))
    conn.execute('DELETE FROM materials WHERE id=ANY(%s)', (materials,))
    conn.execute('DELETE FROM knowledge_drafts WHERE upload_id=%s', (upload_id,))
    return preview


def purge_upload_file(root, upload_id):
    """The stored file, its page images and their readings; after the PostgreSQL rows are gone."""
    store = _sqlite(root, 'uploads.sqlite3')
    if store is None:
        return
    with closing(store):
        with store:
            if _table(store, 'upload_images'):
                if _table(store, 'image_analyses'):
                    store.execute('DELETE FROM image_analyses WHERE image_id IN (SELECT id FROM upload_images WHERE upload_id=?)',
                                  (upload_id,))
                store.execute('DELETE FROM upload_images WHERE upload_id=?', (upload_id,))
            if _table(store, 'upload_notices'):
                store.execute('DELETE FROM upload_notices WHERE upload_id=?', (upload_id,))
            store.execute('DELETE FROM uploads WHERE id=?', (upload_id,))
        store.execute('VACUUM')   # the bytes leave the file, not only the index


# --- lectures and students -------------------------------------------------------------------------

def lecture_runs(conn, lecture_ids):
    """Every run of these lectures: the original, section rewrites and slides, finished or failed."""
    if not lecture_ids:
        return []
    keys = [str(i) for i in lecture_ids]
    return [row['id'] for row in conn.execute('''SELECT id FROM generation_runs WHERE lecture_id=ANY(%s)
        OR context->'section_edit'->>'lecture_id'=ANY(%s) OR context->'ppt_export'->>'lecture_id'=ANY(%s)''',
                                              (lecture_ids, keys, keys)).fetchall()]


def _guard_runs(conn, runs, what):
    if not runs:
        return
    if conn.execute('SELECT 1 FROM generation_jobs WHERE run_id=ANY(%s) AND status=ANY(%s) LIMIT 1',
                    (runs, list(ACTIVE))).fetchone():
        raise DeletionError(f'{what}还有排队或进行中的生成任务，请等它们结束或取消后再删除。', 409)
    if conn.execute('''SELECT 1 FROM lecture_experiment_cells c JOIN generation_jobs j ON j.id=c.job_id
        WHERE j.run_id=ANY(%s) LIMIT 1''', (runs,)).fetchone():
        raise DeletionError(f'{what}属于固定对照实验的记录，不能删除。', 409)


def _drop_runs(conn, runs):
    if not runs:
        return
    conn.execute('UPDATE generation_jobs SET parent_run_id=NULL WHERE parent_run_id=ANY(%s)', (runs,))
    conn.execute('DELETE FROM generation_jobs WHERE run_id=ANY(%s)', (runs,))
    conn.execute('DELETE FROM generation_runs WHERE id=ANY(%s)', (runs,))   # its image links cascade


def _drop_lectures(conn, lectures):
    if not lectures:
        return
    conn.execute('DELETE FROM lecture_issues WHERE lecture_id=ANY(%s)', (lectures,))
    conn.execute('DELETE FROM lecture_revisions WHERE lecture_id=ANY(%s)', (lectures,))
    conn.execute('DELETE FROM lectures WHERE id=ANY(%s)', (lectures,))


def lecture_preview(conn, lecture_id):
    row = conn.execute('SELECT id,topic,student_id,class_id FROM lectures WHERE id=%s', (lecture_id,)).fetchone()
    if not row:
        raise DeletionError('讲义不存在。', 404)
    count = lambda sql: conn.execute(sql, (lecture_id,)).fetchone()['n']
    return dict(kind='lecture', id=lecture_id, title=row['topic'], runs=len(lecture_runs(conn, [lecture_id])),
                issues=count('SELECT count(*) AS n FROM lecture_issues WHERE lecture_id=%s'),
                revisions=count('SELECT count(*) AS n FROM lecture_revisions WHERE lecture_id=%s'),
                quizzes_kept=count("SELECT count(*) AS n FROM quiz_jobs WHERE context->'lecture'->>'id'=%s::text"))


def delete_lecture(conn, lecture_id):
    preview = lecture_preview(conn, lecture_id)
    runs = lecture_runs(conn, [lecture_id])
    _guard_runs(conn, runs, '这份讲义')
    _drop_runs(conn, runs)
    _drop_lectures(conn, [lecture_id])
    return preview


def student_preview(conn, student_id):
    row = conn.execute('SELECT id,name FROM students WHERE id=%s', (student_id,)).fetchone()
    if not row:
        raise DeletionError('学生不存在。', 404)
    lectures = [r['id'] for r in conn.execute('SELECT id FROM lectures WHERE student_id=%s', (student_id,)).fetchall()]
    runs = sorted(set(lecture_runs(conn, lectures)) | {r['id'] for r in conn.execute(
        'SELECT id FROM generation_runs WHERE student_id=%s', (student_id,)).fetchall()})
    count = lambda sql: conn.execute(sql, (student_id,)).fetchone()['n']
    return dict(kind='student', id=student_id, name=row['name'], lectures=len(lectures), runs=len(runs),
                quizzes=count('SELECT count(*) AS n FROM quiz_jobs WHERE student_id=%s'),
                evidence=count('SELECT count(*) AS n FROM learning_evidence WHERE student_id=%s'),
                profile_versions=count('SELECT count(*) AS n FROM student_profile_history WHERE student_id=%s'),
                classes=count('SELECT count(*) AS n FROM class_students WHERE student_id=%s'),
                _lectures=lectures, _runs=runs)


def delete_student(conn, student_id, confirm_name):
    preview = student_preview(conn, student_id)
    if not isinstance(confirm_name, str) or confirm_name.strip() != preview['name']:
        raise DeletionError('请输入学生的姓名以确认删除。', 400)
    lectures, runs = preview.pop('_lectures'), preview.pop('_runs')
    _guard_runs(conn, runs, '这名学生')
    if conn.execute('SELECT 1 FROM quiz_jobs WHERE student_id=%s AND status=ANY(%s) LIMIT 1',
                    (student_id, list(ACTIVE))).fetchone():
        raise DeletionError('这名学生还有进行中的出题任务，请等它结束后再删除。', 409)
    _drop_runs(conn, runs)
    _drop_lectures(conn, lectures)
    quizzes = [r['id'] for r in conn.execute('SELECT id FROM quiz_jobs WHERE student_id=%s', (student_id,)).fetchall()]
    conn.execute('UPDATE quiz_jobs SET parent_id=NULL WHERE parent_id=ANY(%s) AND NOT id=ANY(%s)', (quizzes, quizzes))
    conn.execute('UPDATE quiz_jobs SET parent_id=NULL WHERE id=ANY(%s)', (quizzes,))
    conn.execute('DELETE FROM quiz_jobs WHERE id=ANY(%s)', (quizzes,))
    evidence = 'SELECT id FROM learning_evidence WHERE student_id=%s'
    conn.execute(f'DELETE FROM knowledge_state_history WHERE evidence_id IN ({evidence})', (student_id,))
    conn.execute('DELETE FROM student_knowledge WHERE student_id=%s', (student_id,))
    conn.execute('DELETE FROM learning_evidence WHERE student_id=%s', (student_id,))
    conn.execute('DELETE FROM student_profile_history WHERE student_id=%s', (student_id,))
    conn.execute('DELETE FROM class_students WHERE student_id=%s', (student_id,))
    # Lessons that listed the student as one they may serve no longer do.
    conn.execute('''UPDATE course_tasks SET payload=jsonb_set(payload, '{student_ids}', COALESCE((SELECT jsonb_agg(v)
        FROM jsonb_array_elements(payload->'student_ids') v WHERE v <> to_jsonb(%s::int)), '[]'::jsonb))
        WHERE payload->'student_ids' @> to_jsonb(ARRAY[%s::int])''', (student_id, student_id))
    conn.execute('DELETE FROM students WHERE id=%s', (student_id,))
    return preview


# --- grading ---------------------------------------------------------------------------------------

def grading_preview(root, identity):
    store = _sqlite(root, 'grading.sqlite3')
    if store is None:
        raise DeletionError('阅卷记录不存在。', 404)
    with closing(store):
        row = (store.execute('SELECT id,filename,status FROM grading_jobs WHERE id=?', (identity,)).fetchone()
               if _table(store, 'grading_jobs') else None)
    if row is None:
        raise DeletionError('阅卷记录不存在。', 404)
    return dict(kind='grading', id=identity, title=row['filename'], busy=row['status'] in ('reading', 'grading'))


def delete_grading(root, identity):
    preview = grading_preview(root, identity)
    if preview['busy']:
        raise DeletionError('这份答卷还在识读或评分，请等它结束后再删除。', 409)
    store = _sqlite(root, 'grading.sqlite3')
    with closing(store):
        with store:
            store.execute('DELETE FROM grading_jobs WHERE id=?', (identity,))
        store.execute('VACUUM')   # the read-out answers leave the file
    return preview


def register_deletion(app, db, root):
    @app.errorhandler(DeletionError)
    def deletion_error(exc):
        return jsonify(error=str(exc), **exc.details), exc.status

    def body():
        data = request.get_json(silent=True)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise DeletionError('请求格式必须是 JSON 对象。')
        return data

    @app.get('/api/uploads/<upload_id>/delete-preview')
    def deletion_upload_preview(upload_id):
        with db() as conn:
            return jsonify(upload_preview(conn, root, upload_id))

    @app.post('/api/uploads/<upload_id>/delete')
    def deletion_upload(upload_id):
        data = body()
        with db() as conn:
            result = delete_upload(conn, root, upload_id, data.get('with_materials') is True)
        purge_upload_file(root, upload_id)
        return jsonify(deleted=result)

    @app.get('/api/students/<int:student_id>/delete-preview')
    def deletion_student_preview(student_id):
        with db() as conn:
            result = student_preview(conn, student_id)
        return jsonify({k: v for k, v in result.items() if not k.startswith('_')})

    @app.post('/api/students/<int:student_id>/delete')
    def deletion_student(student_id):
        data = body()
        with db() as conn:
            return jsonify(deleted=delete_student(conn, student_id, data.get('confirm_name')))

    @app.get('/api/lectures/<int:lecture_id>/delete-preview')
    def deletion_lecture_preview(lecture_id):
        with db() as conn:
            return jsonify(lecture_preview(conn, lecture_id))

    @app.post('/api/lectures/<int:lecture_id>/delete')
    def deletion_lecture(lecture_id):
        with db() as conn:
            return jsonify(deleted=delete_lecture(conn, lecture_id))

    @app.get('/api/grading/<identity>/delete-preview')
    def deletion_grading_preview(identity):
        return jsonify(grading_preview(root, identity))

    @app.post('/api/grading/<identity>/delete')
    def deletion_grading(identity):
        return jsonify(deleted=delete_grading(root, identity))
