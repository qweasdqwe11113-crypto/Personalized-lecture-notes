"""Persistent teacher-created tasks alongside the unchanged demonstration catalog."""
from uuid import uuid4

from flask import jsonify, request
from psycopg.types.json import Jsonb

from courses import TOPICS, course_family
from classes import library_ids
from uploads import read_upload


class CatalogError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def load_catalog(conn):
    """Preset and saved lessons, each with its course and the students it may be used for.

    A lesson serves the students enrolled in its course's classes. Presets also keep their
    original demonstration students, and copies saved before courses existed (no course_id)
    keep serving every student, so older records and integrations behave as before.
    """
    students = [s['id'] for s in conn.execute('SELECT id FROM students ORDER BY id')]
    courses = conn.execute('SELECT id,preset_key,name,stage FROM courses').fetchall()
    by_key = {c['preset_key']: c for c in courses if c['preset_key']}
    by_name = {(c['name'], c['stage']): c for c in courses}
    enrolled = {}
    for row in conn.execute('SELECT course_id,student_id FROM class_students ORDER BY student_id'):
        enrolled.setdefault(row['course_id'], []).append(row['student_id'])
    tasks = {key: {**task, 'student_ids': list(task['student_ids'])} for key, task in TOPICS.items()}
    for key, task in tasks.items():
        course = by_key.get(task.get('course_key'))
        # Course text stays as written: it selects the preset's prompt family and checks.
        task['course_id'] = course['id'] if course else None
    for row in conn.execute('SELECT * FROM course_tasks ORDER BY created_at,key'):
        task = {**row['payload'], 'revision': row['revision']}
        if 'course_id' not in task:
            course = by_name.get((task.get('course', ''), task.get('stage', '')))
            task['course_id'] = course['id'] if course else None
            if task.get('preparation_copy'):
                task['student_ids'] = students
        elif task.get('preparation_copy'):
            task['student_ids'] = []
        tasks[row['key']] = task
    for task in tasks.values():
        task['student_ids'] = sorted(set(task['student_ids']) | set(enrolled.get(task['course_id'], [])))
    return {key: {**task, 'key': key} for key, task in tasks.items()}


def text(data, key, maximum, label, optional=False):
    value = data.get(key, '' if optional else None)
    if not isinstance(value, str) or (not optional and not value.strip()) or len(value.strip()) > maximum:
        raise CatalogError(f'{label}请填写 {0 if optional else 1}—{maximum} 个字符。')
    return value.strip()


def save_task(conn, data, key=None, origin=None):
    if not isinstance(data, dict):
        raise CatalogError('请提交课程任务信息。')
    preparation_copy = data.get('preparation_copy', False)
    if type(preparation_copy) is not bool:
        raise CatalogError('备课副本类型无效。')
    course_id = data.get('course_id')
    if course_id is not None:
        if type(course_id) is not int:
            raise CatalogError('请选择有效课程。')
        course = conn.execute('SELECT name,stage FROM courses WHERE id=%s', (course_id,)).fetchone()
        if not course:
            raise CatalogError('课程不存在，请刷新后重试。', 404)
        # The course record owns its name and stage; lessons copy them for prompts and exports.
        data = {**data, 'course': course['name'], 'stage': course['stage']}
    payload = {field: text(data, field, size, label, optional=preparation_copy) for field, size, label in (
        ('stage', 40, '学段'), ('course', 80, '课程名称'), ('chapter', 100, '章节名称'), ('title', 120, '任务名称'),
        ('goal', 400, '教学目标'), ('task_level', 40, '任务层次'))}
    # A course lesson ticks points of the course library (knowledge_ids) and may name new ones.
    library = course_id is not None and origin is None
    picked = data.get('knowledge_ids', []) if library else []
    if (not isinstance(picked, list) or len(picked) > 20 or any(type(k) is not int for k in picked)
            or len(set(picked)) != len(picked)):
        raise CatalogError('请从知识点库中选择有效的知识点。')
    minimum = 0 if preparation_copy or picked else 1
    points = data.get('knowledge_names', [] if minimum == 0 or 'knowledge_ids' in data and library else None)
    if (not isinstance(points, list) or not minimum <= len(points) <= 20
            or any(not isinstance(p, str) or not p.strip() or len(p.strip()) > 80 for p in points)):
        raise CatalogError(f'请填写 {0 if preparation_copy else 1}—20 个知识点，每个不超过 80 字。')
    points = [p.strip() for p in points]
    if len(set(points)) != len(points):
        raise CatalogError('知识点名称不能重复。')
    lesson_plan = data.get('lesson_plan', '')
    if not isinstance(lesson_plan, str) or len(lesson_plan) > 20000:
        raise CatalogError('教案正文请控制在 20000 字以内。')
    students = ([] if course_id is not None else [s['id'] for s in conn.execute('SELECT id FROM students ORDER BY id')]
                ) if preparation_copy else data.get('student_ids')
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
    same_subject = previous and previous['payload']['course'] == payload['course'] and previous['payload']['stage'] == payload['stage']
    old = dict(zip(previous['payload']['knowledge_names'], previous['payload']['knowledge_ids'])) if same_subject else {}
    origin_ids = dict(zip(origin['knowledge_names'], origin['knowledge_ids'])) if origin else {}
    ids, names = [], []
    if picked:
        kept = set(previous['payload']['knowledge_ids']) if previous else set()
        shelf = set(library_ids(conn, course_id))
        rows = {r['id']: r['name'] for r in conn.execute('SELECT id,name FROM knowledge_points WHERE id=ANY(%s)', (picked,))}
        for kid in picked:
            # A lesson keeps the points it already has (e.g. a copy's own points) even outside the library.
            if kid not in rows or (kid not in shelf and kid not in kept):
                raise CatalogError('所选知识点不在本课程的知识点库中，请刷新后重试。', 409)
            ids.append(kid)
            names.append(rows[kid])
    shelf_names = {}
    if library:
        for row in conn.execute('''SELECT k.id,k.name FROM knowledge_points k WHERE k.course_id=%s
                AND NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id)
                ORDER BY k.archived DESC,k.id DESC''', (course_id,)):
            shelf_names[row['name']] = row['id']
    for name in points:
        if name in names:
            continue
        # Same name in the course library: the lesson shares that point (and its diagnoses).
        kid = old.get(name) or shelf_names.get(name)
        if kid is None:
            kid = conn.execute('''INSERT INTO knowledge_points(id,name,course_id,chapter,position)
                VALUES ((SELECT COALESCE(max(id),0)+1 FROM knowledge_points),%s,%s,%s,
                        (SELECT COALESCE(max(position),0)+1 FROM knowledge_points WHERE course_id=%s)) RETURNING id''',
                               (name, course_id, payload['chapter'][:100], course_id)).fetchone()['id']
            if name in origin_ids:
                source_id = origin_ids[name]
                alias = conn.execute('SELECT canonical_id FROM knowledge_aliases WHERE knowledge_id=%s', (source_id,)).fetchone()
                conn.execute('INSERT INTO knowledge_aliases VALUES (%s,%s)', (kid, alias['canonical_id'] if alias else source_id))
        elif library:
            conn.execute('UPDATE knowledge_points SET archived=false WHERE id=%s AND course_id=%s', (kid, course_id))
        if kid in ids:
            continue
        ids.append(kid)
        names.append(name)
    if not (0 if preparation_copy else 1) <= len(ids) <= 20:
        raise CatalogError(f'请选择 {0 if preparation_copy else 1}—20 个知识点。')
    if course_id is not None:
        payload['course_id'] = course_id
    payload.update(knowledge_names=names, knowledge_ids=ids, student_ids=students,
                   preparation_copy=preparation_copy, lesson_plan=lesson_plan.strip(),
                   custom=True, length_range=[600, 1000], check_version='general-text-check-v1')
    inherited = origin or (previous['payload'] if same_subject else None)
    if inherited:
        payload['prompt_family'] = course_family(inherited)
        payload['check_version'] = inherited.get('check_version', 'math-text-check-v1')
        payload['reference_origin'] = inherited.get('reference_origin', inherited.get('key'))
    key = key or 'custom_' + uuid4().hex
    row = conn.execute('''INSERT INTO course_tasks(key,payload) VALUES (%s,%s)
        ON CONFLICT(key) DO UPDATE SET payload=EXCLUDED.payload, revision=course_tasks.revision+1
        RETURNING revision''', (key, Jsonb(payload))).fetchone()
    notice = ('学段或课程已改变，已建立新的知识点身份；原资料与诊断仍保留，请为新课程重新关联材料并确认诊断。'
              if previous and not same_subject else '')
    return {'key': key, 'task': {**payload, 'revision': row['revision']}, 'notice': notice}


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
            materials = conn.execute('''SELECT m.*,c.name AS class_name FROM materials m
                LEFT JOIN classes c ON c.id=m.class_id WHERE m.knowledge_id=ANY(%s) ORDER BY m.id''',
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
            payload = {**{k: v for k, v in original.items() if k != 'course_id'}, 'title': original['title'][:116] + '（副本）',
                       'preparation_copy': True, 'stage': original.get('stage', '大学' if
                       original.get('course') == '大学概率论' else '初中'),
                       'chapter': original.get('chapter', original['title']),
                       'knowledge_names': [names[k] for k in original['knowledge_ids']]}
            if original.get('course_id') is not None:
                payload['course_id'] = original['course_id']
            result = save_task(conn, payload, origin={**original, 'knowledge_names': payload['knowledge_names']})
            mapping = dict(zip(original['knowledge_ids'], result['task']['knowledge_ids']))
            conn.execute('LOCK TABLE materials IN EXCLUSIVE MODE')
            for material in conn.execute('SELECT * FROM materials WHERE knowledge_id=ANY(%s) ORDER BY id',
                                         (original['knowledge_ids'],)).fetchall():
                conn.execute('''INSERT INTO materials
                    (id,knowledge_id,title,content,source_title,source_url,section,class_id)
                    VALUES ((SELECT COALESCE(max(id),0)+1 FROM materials),%s,%s,%s,%s,%s,%s,%s)''',
                    (mapping[material['knowledge_id']], material['title'], material['content'],
                     material['source_title'], material['source_url'], material['section'], material['class_id']))
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
            # Scope travels with the upload: course resources serve every class, class resources one class.
            class_id = record.get('class_id')
            if record.get('course_id') is not None and topic.get('course_id') is not None and record['course_id'] != topic['course_id']:
                raise CatalogError('这份资料属于其他课程，请在对应课程中收录。')
            if class_id is not None and not conn.execute('SELECT 1 FROM classes WHERE id=%s AND course_id=%s',
                                                         (class_id, topic.get('course_id'))).fetchone():
                raise CatalogError('这份班级资料的班级不属于所选备课的课程。')
            conn.execute('LOCK TABLE materials IN EXCLUSIVE MODE')
            source = 'upload:' + data['upload_id']
            row = conn.execute('SELECT id FROM materials WHERE source_url=%s AND knowledge_id=%s',
                               (source, data['knowledge_id'])).fetchone()
            if not row:
                row = conn.execute('''INSERT INTO materials
                    (id,knowledge_id,title,content,source_title,source_url,section,class_id)
                    VALUES ((SELECT COALESCE(max(id),0)+1 FROM materials),%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                    (data['knowledge_id'], record['title'], record['content'], record['filename'], source,
                     topic.get('chapter', ''), class_id)).fetchone()
        return jsonify(material_id=row['id'], citation_id=f"M{row['id']}", class_id=class_id), 201
