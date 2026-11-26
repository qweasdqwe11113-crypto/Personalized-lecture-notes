"""Starting a lesson from a teacher's free-text focus instead of ticked knowledge points.

The teacher writes what the lesson is about; retrieval ranks the course library's points for it
(retrieval.suggest_points); the teacher confirms, drops or adds points; and a lesson (备课) is saved with
them exactly like one made on the course page, so generation, quizzes, packages and the profile all treat it
as any other lesson. What was suggested and what the teacher changed is kept with the lesson
(payload['quick_start']). The focus text goes to the embedding service only with the names of the course's
students replaced by 「该生」.
"""
from datetime import datetime, timezone

from flask import jsonify, request
from psycopg.types.json import Jsonb

from catalog import CatalogError, save_task
from classes import LIBRARY
from embed import EmbeddingError
from prompts import without_name
from retrieval import SUGGEST_VERSION, index_knowledge, suggest_points

FOCUS_LIMIT = 400   # the focus becomes the lesson's goal, which holds 400 characters


def library(conn, course_id, class_id=None):
    """The course library in teaching order, each point with how many materials it has in this scope."""
    rows = conn.execute(f'''SELECT k.id, k.name, k.chapter, k.position,
        (SELECT count(*) FROM materials m WHERE m.knowledge_id=k.id AND (m.class_id IS NULL OR m.class_id=%s)) AS materials
        {LIBRARY} ORDER BY k.position, k.id''', (class_id, course_id)).fetchall()
    return [dict(row) for row in rows]


def chapters(points):
    return list(dict.fromkeys(point['chapter'] for point in points if point['chapter']))


def scope(conn, data):
    course_id, class_id = data.get('course_id'), data.get('class_id')
    if type(course_id) is not int:
        raise CatalogError('请先在页面顶部选择课程。')
    if not conn.execute('SELECT 1 FROM courses WHERE id=%s', (course_id,)).fetchone():
        raise CatalogError('课程不存在，请刷新后重试。', 404)
    if class_id is not None and (type(class_id) is not int or not conn.execute(
            'SELECT 1 FROM classes WHERE id=%s AND course_id=%s', (class_id, course_id)).fetchone()):
        raise CatalogError('班级不属于所选课程，请刷新后重试。')
    return course_id, class_id


def focus_text(data):
    value = data.get('text')
    if not isinstance(value, str) or not 4 <= len(value.strip()) <= FOCUS_LIMIT:
        raise CatalogError(f'请用 4—{FOCUS_LIMIT} 个字写出本课要讲的重点。')
    return value.strip()


def boundary(data, points):
    """The teacher's 「本课在哪一章」: one of the library's chapters, or None for no boundary."""
    chapter = data.get('chapter')
    if chapter in (None, ''):
        return None
    if not isinstance(chapter, str) or chapter not in chapters(points):
        raise CatalogError('所选章节不在本课程的知识点库中，请刷新后重试。')
    return chapter


def scrub(text, conn, course_id):
    """The focus with the names of the course's students replaced, longest first (小林 inside 小林林)."""
    names = {row['name'].strip() for row in conn.execute('''SELECT DISTINCT s.name FROM students s
        JOIN class_students cs ON cs.student_id=s.id WHERE cs.course_id=%s''', (course_id,))}
    for name in sorted(names, key=len, reverse=True):
        text = without_name(text, name)
    return text


def register_quick_start(app, db, get_embedder, embedder_ready):
    @app.get('/api/quick-lesson/library')
    def quick_library():
        with db() as conn:
            course_id, class_id = scope(conn, {'course_id': request.args.get('course_id', type=int),
                                               'class_id': request.args.get('class_id', type=int)})
            points = library(conn, course_id, class_id)
        return jsonify(points=points, chapters=chapters(points))

    @app.post('/api/quick-lesson/suggest')
    def quick_suggest():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise CatalogError('请求格式必须是 JSON 对象。')
        text = focus_text(data)
        with db() as conn:
            course_id, class_id = scope(conn, data)
            points = library(conn, course_id, class_id)
            chapter = boundary(data, points)
            query = scrub(text, conn, course_id)
        if not points:
            raise CatalogError('这门课的知识点库还是空的：请先在「课程资料」上传资料并确认知识点。', 409)
        # The library is indexed incrementally, as for whole-course retrieval; without an answering embedding
        # service the points are found by keywords alone, and the result says so.
        embedder, note = get_embedder(), None
        if not embedder_ready():
            note = '嵌入模型尚未配置，本次只按关键词查找。'
        else:
            try:
                with db() as conn:
                    index_knowledge(conn, embedder, [point['id'] for point in points])
            except EmbeddingError as exc:
                note = f'{exc} 本次只按关键词查找。'
        with db() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            result = suggest_points(conn, embedder if note is None else None, query, library=points,
                                    class_id=class_id, chapter=chapter, vector_note=note)
        return jsonify(suggestion=result, points=points, chapters=chapters(points))

    @app.post('/api/quick-lesson')
    def quick_create():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise CatalogError('请求格式必须是 JSON 对象。')
        text = focus_text(data)
        title = data.get('title') or ''
        if not isinstance(title, str) or len(title.strip()) > 120:
            raise CatalogError('标题请控制在 120 字以内。')
        ids, suggested = data.get('knowledge_ids'), data.get('suggested_ids', [])
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 20 or any(type(k) is not int for k in ids)
                or len(set(ids)) != len(ids)):
            raise CatalogError('请确认 1—20 个知识点。')
        if not isinstance(suggested, list) or len(suggested) > 20 or any(type(k) is not int for k in suggested):
            raise CatalogError('建议的知识点记录无效，请重新查找。')
        with db() as conn:
            course_id, class_id = scope(conn, data)
            points = library(conn, course_id, class_id)
            chapter = boundary(data, points)
            by_id = {point['id']: point for point in points}
            if any(k not in by_id for k in ids):
                raise CatalogError('所选知识点不在本课程的知识点库中，请刷新后重试。', 409)
            ordered = sorted(ids, key=lambda k: (by_id[k]['position'], k))
            names = [by_id[k]['name'] for k in ordered]
            title = title.strip() or ('、'.join(names[:3]) + ('等' if len(names) > 3 else ''))[:120]
            # Saved like a lesson made on the course page (catalog.save_task): the same checks, the same shape.
            result = save_task(conn, {'preparation_copy': True, 'course_id': course_id, 'knowledge_ids': ordered,
                                      'knowledge_names': [], 'chapter': (chapter or by_id[ordered[0]]['chapter'])[:100],
                                      'title': title, 'goal': text, 'task_level': '', 'lesson_plan': ''})
            offered = [k for k in dict.fromkeys(suggested) if k in by_id]
            record = dict(version=SUGGEST_VERSION, chapter=chapter, suggested=offered, confirmed=ordered,
                          added=[k for k in ordered if k not in offered], dropped=[k for k in offered if k not in ids],
                          created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            conn.execute('UPDATE course_tasks SET payload = payload || %s WHERE key=%s',
                         (Jsonb({'quick_start': record}), result['key']))
        return jsonify(key=result['key'], task={**result['task'], 'quick_start': record}), 201
