"""Persistent teacher-created tasks alongside the unchanged demonstration catalog."""
from uuid import uuid4

from flask import jsonify, request
from psycopg.types.json import Jsonb

from courses import TOPICS
from uploads import read_upload


class CatalogError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def load_catalog(conn):
    students = [s['id'] for s in conn.execute('SELECT id FROM students ORDER BY id')]
    custom = {}
    for row in conn.execute('SELECT * FROM course_tasks ORDER BY created_at,key'):
        task = {**row['payload'], 'revision': row['revision']}
        if task.get('preparation_copy'):
            task['student_ids'] = students
        custom[row['key']] = task
    return {**TOPICS, **custom}


def text(data, key, maximum, label):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise CatalogError(f'{label}请填写 1—{maximum} 个字符。')
    return value.strip()


def save_task(conn, data, key=None):
    if not isinstance(data, dict):
        raise CatalogError('请提交课程任务信息。')
    payload = {field: text(data, field, size, label) for field, size, label in (
        ('stage', 40, '学段'), ('course', 80, '课程名称'), ('chapter', 100, '章节名称'), ('title', 120, '任务名称'),
        ('goal', 400, '教学目标'), ('task_level', 40, '任务层次'))}
    points = data.get('knowledge_names')
    if (not isinstance(points, list) or not 1 <= len(points) <= 20
            or any(not isinstance(p, str) or not p.strip() or len(p.strip()) > 80 for p in points)):
        raise CatalogError('请填写 1—20 个知识点，每个不超过 80 字。')
    points = [p.strip() for p in points]
    if len(set(points)) != len(points):
        raise CatalogError('知识点名称不能重复。')
    preparation_copy = data.get('preparation_copy', False)
    if type(preparation_copy) is not bool:
        raise CatalogError('备课副本类型无效。')
    lesson_plan = data.get('lesson_plan', '')
    if not isinstance(lesson_plan, str) or len(lesson_plan) > 20000:
        raise CatalogError('教案正文请控制在 20000 字以内。')
    students = ([s['id'] for s in conn.execute('SELECT id FROM students ORDER BY id')]
                if preparation_copy else data.get('student_ids'))
    if (not isinstance(students, list) or not (0 if preparation_copy else 1) <= len(students) <= 200
            or any(type(s) is not int for s in students) or len(set(students)) != len(students)):
        raise CatalogError('请选择适用学生。')
    if len(conn.execute('SELECT id FROM students WHERE id=ANY(%s)', (students,)).fetchall()) != len(students):
        raise CatalogError('选择的学生不存在，请刷新后重试。')
    # Existing integer IDs predate sequences. Serialize allocations against other writers.
    conn.execute('LOCK TABLE course_tasks IN EXCLUSIVE MODE')
    previous = None
    if key:
        previous = conn.execute('SELECT * FROM course_tasks WHERE key=%s FOR UPDATE', (key,)).fetchone()
        if not previous:
            raise CatalogError('只能编辑已保存的自建任务。', 404)
        if type(data.get('revision')) is not int or data['revision'] != previous['revision']:
            raise CatalogError('任务已被修改，请重新读取后再保存。', 409)
    conn.execute('LOCK TABLE knowledge_points IN EXCLUSIVE MODE')
    old = dict(zip(previous['payload']['knowledge_names'], previous['payload']['knowledge_ids'])) if previous else {}
    ids = []
    for name in points:
        kid = old.get(name)
        if kid is None:
            kid = conn.execute('''INSERT INTO knowledge_points(id,name)
                VALUES ((SELECT COALESCE(max(id),0)+1 FROM knowledge_points),%s) RETURNING id''', (name,)).fetchone()['id']
        ids.append(kid)
    payload.update(knowledge_names=points, knowledge_ids=ids, student_ids=students,
                   preparation_copy=preparation_copy, lesson_plan=lesson_plan.strip(),
                   custom=True, length_range=[600, 1000], check_version='general-text-check-v1')
    key = key or 'custom_' + uuid4().hex
    row = conn.execute('''INSERT INTO course_tasks(key,payload) VALUES (%s,%s)
        ON CONFLICT(key) DO UPDATE SET payload=EXCLUDED.payload, revision=course_tasks.revision+1
        RETURNING revision''', (key, Jsonb(payload))).fetchone()
    return {'key': key, 'task': {**payload, 'revision': row['revision']}}


def register_catalog(app, db, root):
    @app.errorhandler(CatalogError)
    def catalog_error(exc):
        return jsonify(error=str(exc)), exc.status

    @app.get('/api/course-tasks')
    def list_tasks():
        with db() as conn:
            return jsonify(topics=load_catalog(conn), students=conn.execute(
                'SELECT id,name,subtitle FROM students ORDER BY id').fetchall(),
                knowledge_points=conn.execute('SELECT id,name FROM knowledge_points ORDER BY id').fetchall())

    @app.get('/api/course-tasks/<key>/materials')
    def task_materials(key):
        with db() as conn:
            task = load_catalog(conn).get(key)
            if not task:
                raise CatalogError('备课副本不存在。', 404)
            materials = conn.execute('SELECT * FROM materials WHERE knowledge_id=ANY(%s) ORDER BY id',
                                     (task['knowledge_ids'],)).fetchall()
        return jsonify(materials=materials)

    @app.post('/api/course-tasks')
    def create_task():
        with db() as conn:
            result = save_task(conn, request.get_json(silent=True))
        return jsonify(result), 201

    @app.put('/api/course-tasks/<key>')
    def update_task(key):
        with db() as conn:
            result = save_task(conn, request.get_json(silent=True), key)
        return jsonify(result)

    @app.post('/api/course-tasks/<key>/copy')
    def copy_task(key):
        with db() as conn:
            original = load_catalog(conn).get(key)
            if original is None:
                raise CatalogError('备课材料不存在。', 404)
            points = conn.execute('SELECT id,name FROM knowledge_points WHERE id=ANY(%s)',
                                  (original['knowledge_ids'],)).fetchall()
            names = {p['id']: p['name'] for p in points}
            payload = {**original, 'title': original['title'][:116] + '（副本）',
                       'preparation_copy': True, 'stage': original.get('stage', '大学' if
                       original.get('course') == '大学概率论' else '初中'),
                       'chapter': original.get('chapter', original['title']),
                       'knowledge_names': [names[k] for k in original['knowledge_ids']]}
            result = save_task(conn, payload)
            mapping = dict(zip(original['knowledge_ids'], result['task']['knowledge_ids']))
            conn.execute('LOCK TABLE materials IN EXCLUSIVE MODE')
            for material in conn.execute('SELECT * FROM materials WHERE knowledge_id=ANY(%s) ORDER BY id',
                                         (original['knowledge_ids'],)).fetchall():
                conn.execute('''INSERT INTO materials
                    (id,knowledge_id,title,content,source_title,source_url,section)
                    VALUES ((SELECT COALESCE(max(id),0)+1 FROM materials),%s,%s,%s,%s,%s,%s)''',
                    (mapping[material['knowledge_id']], material['title'], material['content'],
                     material['source_title'], material['source_url'], material['section']))
        return jsonify(result), 201

    @app.post('/api/course-tasks/<key>/materials')
    def attach_material(key):
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or not isinstance(data.get('upload_id'), str)
                or type(data.get('knowledge_id')) is not int):
            raise CatalogError('请选择上传资料和目标知识点。')
        record = read_upload(root, data['upload_id'])
        if record is None:
            raise CatalogError('上传资料不存在。', 404)
        if not record['content'].strip():
            raise CatalogError('该资料没有可收录的正文；图片请通过图片识读加入讲义。')
        with db() as conn:
            topic = load_catalog(conn).get(key)
            if topic is None or data['knowledge_id'] not in topic['knowledge_ids']:
                raise CatalogError('知识点不属于所选课程任务。')
            conn.execute('LOCK TABLE materials IN EXCLUSIVE MODE')
            source = 'upload:' + data['upload_id']
            row = conn.execute('SELECT id FROM materials WHERE source_url=%s AND knowledge_id=%s',
                               (source, data['knowledge_id'])).fetchone()
            if not row:
                row = conn.execute('''INSERT INTO materials
                    (id,knowledge_id,title,content,source_title,source_url,section)
                    VALUES ((SELECT COALESCE(max(id),0)+1 FROM materials),%s,%s,%s,%s,%s,%s) RETURNING id''',
                    (data['knowledge_id'], record['title'], record['content'], record['filename'], source,
                     topic.get('chapter', ''))).fetchone()
        return jsonify(material_id=row['id'], citation_id=f"M{row['id']}"), 201
