"""Courses, classes and enrollment.

A course owns its lessons (备课) and course-wide materials; a class belongs to one course and
holds its students. Students remain shared records: one student may join classes of several
courses, but only one class per course. A class profile is an aggregate of the students'
teacher-confirmed states; it never writes or infers any individual state.
"""
import re
import unicodedata

from flask import jsonify, request
from psycopg.types.json import Jsonb

from courses import PRESET_COURSES
from profiles import STATE_LABELS, ProfileError, json_dates

MIGRATION = 'course-class-v1'
STATE_ORDER = ('needs_support', 'partial', 'mastered', 'unknown')
# A class-level judgement is a stated rule over confirmed states, shown to the teacher as such.
CLASS_RULE = '按已诊断学生计：需要补充 ≥30% 记为需要补充，已掌握 ≥70% 记为已掌握，其余为部分掌握；无人诊断记为未知。'


class ClassError(ProfileError):
    pass


def field(data, key, label, maximum, optional=False):
    value = data.get(key, '' if optional else None)
    if not isinstance(value, str) or (not optional and not value.strip()) or len(value.strip()) > maximum:
        raise ClassError(f'{label}{"不超过" if optional else "请填写 1—"}{maximum} 个字。')
    return value.strip()


def ensure_course_structure(conn, presets=True):
    """Seed the demonstration courses and classes once; attach legacy lesson copies to a course.

    Without presets (the desktop release) no course or class is created; the migration still
    runs once, so a later lesson copy without a course is filed the same way.
    """
    if presets:
        for key, preset in PRESET_COURSES.items():
            conn.execute('''INSERT INTO courses(preset_key,name,stage,description) VALUES (%s,%s,%s,%s)
                ON CONFLICT DO NOTHING''', (key, preset['name'], preset['stage'], preset['description']))
    if not conn.execute('SELECT 1 FROM schema_migrations WHERE name=%s', (MIGRATION,)).fetchone():
        attach_legacy(conn)
    backfill_library(conn)


def attach_legacy(conn):
    for key, preset in PRESET_COURSES.items():
        course = conn.execute('SELECT id FROM courses WHERE preset_key=%s', (key,)).fetchone()
        if not course or conn.execute('SELECT 1 FROM classes WHERE course_id=%s', (course['id'],)).fetchone():
            continue
        klass = conn.execute('''INSERT INTO classes(course_id,name,profile) VALUES (%s,%s,%s) RETURNING id''',
                             (course['id'], preset['class'], preset['class_profile'])).fetchone()
        conn.execute('''INSERT INTO class_students(class_id,course_id,student_id)
            SELECT %s,%s,id FROM students WHERE id=ANY(%s) ON CONFLICT DO NOTHING''',
                     (klass['id'], course['id'], preset['student_ids']))
    for row in conn.execute("SELECT key,payload FROM course_tasks WHERE NOT payload ? 'course_id'").fetchall():
        name = (row['payload'].get('course') or '').strip() or '未命名课程'
        stage = (row['payload'].get('stage') or '').strip()[:40]
        conn.execute('INSERT INTO courses(name,stage) VALUES (%s,%s) ON CONFLICT DO NOTHING', (name[:80], stage))
        course = conn.execute('SELECT id FROM courses WHERE name=%s AND stage=%s', (name[:80], stage)).fetchone()
        conn.execute("UPDATE course_tasks SET payload=payload||jsonb_build_object('course_id',%s::integer) WHERE key=%s",
                     (course['id'], row['key']))
    conn.execute('INSERT INTO schema_migrations(name) VALUES (%s)', (MIGRATION,))


def backfill_library(conn):
    """File each lesson point without a course under the course of the first lesson using it.

    Runs on every start, so points saved by older code join their course's library. Points
    made by lesson copies are aliases; they get the course too but stay out of the library.
    """
    if not conn.execute('SELECT 1 FROM knowledge_points WHERE course_id IS NULL LIMIT 1').fetchone():
        return
    from catalog import load_catalog
    chapters = {key: preset.get('chapter', '') for key, preset in PRESET_COURSES.items()}
    for task in load_catalog(conn).values():
        if task.get('course_id') is None or not task['knowledge_ids']:
            continue
        chapter = chapters.get(task.get('course_key')) or task.get('chapter') or ''
        conn.execute('''UPDATE knowledge_points SET course_id=%s,
            chapter=CASE WHEN chapter='' THEN %s ELSE chapter END WHERE id=ANY(%s) AND course_id IS NULL''',
                     (task['course_id'], chapter[:100], task['knowledge_ids']))


def course_rows(conn):
    return conn.execute('SELECT id,preset_key,name,stage,description FROM courses ORDER BY id').fetchall()


def enrollment(conn):
    """{course_id: {class_id: [student ids]}} for the whole workspace; small at this scale."""
    result = {}
    for row in conn.execute('SELECT course_id,class_id,student_id FROM class_students ORDER BY class_id,student_id'):
        result.setdefault(row['course_id'], {}).setdefault(row['class_id'], []).append(row['student_id'])
    return result


def class_row(conn, class_id, lock=False):
    row = conn.execute('''SELECT c.*,co.name AS course_name,co.stage AS course_stage FROM classes c
        JOIN courses co ON co.id=c.course_id WHERE c.id=%s''' + (' FOR UPDATE OF c' if lock else ''),
                       (class_id,)).fetchone()
    if not row:
        raise ClassError('班级不存在，请刷新后重试。', 404)
    return row


def class_members(conn, class_id):
    return [r['student_id'] for r in conn.execute(
        'SELECT student_id FROM class_students WHERE class_id=%s ORDER BY student_id', (class_id,))]


def student_class(conn, student_id, course_id):
    """The student's class in this course, or None (one class per course)."""
    if course_id is None:
        return None
    row = conn.execute('SELECT class_id FROM class_students WHERE student_id=%s AND course_id=%s',
                       (student_id, course_id)).fetchone()
    return row['class_id'] if row else None


def list_courses(conn, topics):
    members = enrollment(conn)
    classes = conn.execute('''SELECT id,course_id,name,profile,profile_revision FROM classes ORDER BY course_id,id''').fetchall()
    enrolled = {sid for course in members.values() for ids in course.values() for sid in ids}
    result = []
    for course in course_rows(conn):
        lessons = [k for k, t in topics.items() if t.get('course_id') == course['id']]
        own = [{**c, 'student_ids': members.get(course['id'], {}).get(c['id'], [])}
               for c in classes if c['course_id'] == course['id']]
        result.append({**course, 'classes': own, 'lesson_keys': lessons,
                       'student_count': sum(len(c['student_ids']) for c in own)})
    unassigned = conn.execute('SELECT count(*) AS n FROM students WHERE NOT (id=ANY(%s))',
                              (sorted(enrolled),)).fetchone()['n']
    return {'courses': result, 'unassigned_students': unassigned}


def save_course(conn, data, course_id=None):
    if not isinstance(data, dict):
        raise ClassError('请填写课程信息。')
    name = field(data, 'name', '课程名称', 80)
    stage = field(data, 'stage', '学段', 40, optional=True)
    description = field(data, 'description', '课程说明', 600, optional=True)
    clash = conn.execute('SELECT id FROM courses WHERE name=%s AND stage=%s AND id IS DISTINCT FROM %s',
                         (name, stage, course_id)).fetchone()
    if clash:
        raise ClassError('已有同名同学段的课程，请换一个名称。', 409)
    if course_id is None:
        return conn.execute('INSERT INTO courses(name,stage,description) VALUES (%s,%s,%s) RETURNING *',
                            (name, stage, description)).fetchone()
    row = conn.execute('UPDATE courses SET name=%s,stage=%s,description=%s WHERE id=%s RETURNING *',
                       (name, stage, description, course_id)).fetchone()
    if not row:
        raise ClassError('课程不存在。', 404)
    # Lesson copies keep course and stage text for prompts and exports; follow the rename.
    conn.execute('''UPDATE course_tasks SET payload=payload||jsonb_build_object('course',%s::text,'stage',%s::text)
        WHERE (payload->>'course_id')::integer=%s''', (name, stage, course_id))
    return row


def create_class(conn, course_id, data):
    if not isinstance(data, dict):
        raise ClassError('请填写班级信息。')
    if not conn.execute('SELECT 1 FROM courses WHERE id=%s', (course_id,)).fetchone():
        raise ClassError('课程不存在。', 404)
    name = field(data, 'name', '班级名称', 80)
    profile = field(data, 'profile', '班级特点', 1200, optional=True)
    if conn.execute('SELECT 1 FROM classes WHERE course_id=%s AND name=%s', (course_id, name)).fetchone():
        raise ClassError('本课程已有同名班级。', 409)
    return conn.execute('INSERT INTO classes(course_id,name,profile) VALUES (%s,%s,%s) RETURNING *',
                        (course_id, name, profile)).fetchone()


def update_class(conn, class_id, data):
    """Name and class notes change together with a recorded reason, like student notes."""
    if not isinstance(data, dict):
        raise ClassError('请填写班级信息。')
    revision = data.get('expected_revision')
    if type(revision) is not int or revision < 0:
        raise ClassError('缺少有效的班级画像版本，请重新读取。')
    name = field(data, 'name', '班级名称', 80)
    profile = field(data, 'profile', '班级特点', 1200, optional=True)
    recorder = field(data, 'recorder', '录入者', 80)
    reason = field(data, 'reason', '修改理由', 600)
    old = class_row(conn, class_id, lock=True)
    if old['profile_revision'] != revision:
        raise ClassError('班级信息已被更新，本次未保存，请重新读取。', 409)
    if name == old['name'] and profile == old['profile']:
        raise ClassError('班级名称和班级特点没有变化，无需保存。')
    if conn.execute('SELECT 1 FROM classes WHERE course_id=%s AND name=%s AND id<>%s',
                    (old['course_id'], name, class_id)).fetchone():
        raise ClassError('本课程已有同名班级。', 409)
    snapshot = {k: old[k] for k in ('name', 'profile', 'profile_revision')}
    conn.execute('''INSERT INTO class_profile_history(class_id,previous_snapshot,new_snapshot,recorder,reason)
        VALUES (%s,%s,%s,%s,%s)''', (class_id, Jsonb(snapshot),
        Jsonb({'name': name, 'profile': profile, 'profile_revision': revision + 1}), recorder, reason))
    conn.execute('UPDATE classes SET name=%s,profile=%s,profile_revision=%s WHERE id=%s',
                 (name, profile, revision + 1, class_id))


def enroll(conn, class_id, student_id, move=False):
    klass = class_row(conn, class_id)
    if not conn.execute('SELECT 1 FROM students WHERE id=%s', (student_id,)).fetchone():
        raise ClassError('学生不存在。', 404)
    current = conn.execute('''SELECT s.class_id,c.name FROM class_students s JOIN classes c ON c.id=s.class_id
        WHERE s.student_id=%s AND s.course_id=%s FOR UPDATE OF s''', (student_id, klass['course_id'])).fetchone()
    if current and current['class_id'] == class_id:
        return
    if current and not move:
        raise ClassError(f'该学生已在本课程的「{current["name"]}」，一门课程只能属于一个班级；可选择转入本班。', 409)
    if current:
        conn.execute('DELETE FROM class_students WHERE class_id=%s AND student_id=%s', (current['class_id'], student_id))
    conn.execute('INSERT INTO class_students(class_id,course_id,student_id) VALUES (%s,%s,%s)',
                 (class_id, klass['course_id'], student_id))


def leave(conn, class_id, student_id):
    """Only the membership ends; the student record, evidence and lectures stay."""
    if not conn.execute('DELETE FROM class_students WHERE class_id=%s AND student_id=%s RETURNING 1',
                        (class_id, student_id)).fetchone():
        raise ClassError('该学生不在这个班级。', 404)


def class_state(counts):
    known = sum(counts[s] for s in ('needs_support', 'partial', 'mastered'))
    if not known:
        return 'unknown'
    if counts['needs_support'] / known >= 0.3:
        return 'needs_support'
    if counts['mastered'] / known >= 0.7:
        return 'mastered'
    return 'partial'


def aggregate(conn, student_ids, knowledge_ids):
    """Per requested point (aliases resolved to the diagnosed identity): who is in which state."""
    points = conn.execute('''SELECT k.id,k.name,COALESCE(a.canonical_id,k.id) AS canonical_id
        FROM knowledge_points k LEFT JOIN knowledge_aliases a ON a.knowledge_id=k.id
        WHERE k.id=ANY(%s)''', (list(knowledge_ids),)).fetchall()
    order = {k: i for i, k in enumerate(knowledge_ids)}
    points.sort(key=lambda p: order[p['id']])
    canonical = sorted({p['canonical_id'] for p in points})
    known = {}
    for row in conn.execute('''SELECT student_id,knowledge_id,state,revision FROM student_knowledge
            WHERE student_id=ANY(%s) AND knowledge_id=ANY(%s)''', (list(student_ids), canonical)):
        known[(row['student_id'], row['knowledge_id'])] = row
    result = []
    for point in points:
        groups = {state: [] for state in STATE_ORDER}
        revision = 0
        for sid in student_ids:
            row = known.get((sid, point['canonical_id']))
            groups[row['state'] if row else 'unknown'].append(sid)
            revision += row['revision'] if row else 0
        counts = {state: len(ids) for state, ids in groups.items()}
        state = class_state(counts)
        result.append({**point, 'groups': groups, 'counts': counts, 'state': state,
                       'label': STATE_LABELS[state], 'revision_sum': revision})
    return result


def distribution_text(counts, total):
    parts = [f'{STATE_LABELS[s]} {counts[s]} 人' for s in STATE_ORDER if counts[s]]
    return f'全班 {total} 人：' + '、'.join(parts) + '。' if parts else f'全班 {total} 人，暂无诊断。'


def evidence_samples(conn, student_ids, canonical_id, limit=3):
    """Recent confirmed evidence from a few classmates, without names or recorders."""
    rows = conn.execute('''SELECT e.id,e.source,e.task_content,e.performance,e.observed_at,
        h.decision,h.confirmed_state FROM learning_evidence e JOIN knowledge_state_history h ON h.evidence_id=e.id
        WHERE e.student_id=ANY(%s) AND e.knowledge_id=%s
        ORDER BY (h.confirmed_state IN ('needs_support','partial')) DESC, e.id DESC LIMIT %s''',
                        (list(student_ids), canonical_id, limit)).fetchall()
    return [json_dates(row) for row in rows]


def class_context_states(conn, student_ids, knowledge_ids):
    """States shaped like a student's, so the lecture plan, budget and checks work unchanged."""
    states = []
    for point in aggregate(conn, student_ids, knowledge_ids):
        total = len(student_ids)
        evidence = distribution_text(point['counts'], total) + ' ' + CLASS_RULE
        states.append({'id': point['id'], 'name': point['name'], 'profile_knowledge_id': point['canonical_id'],
                       'state': point['state'], 'evidence': evidence, 'revision': point['revision_sum'],
                       'evidence_id': None, 'updated_at': None,
                       'distribution': point['counts'], 'class_size': total,
                       'recent_evidence': evidence_samples(conn, student_ids, point['canonical_id'])})
    return states


def course_points(topics, course_id, topic_key=None):
    """Knowledge points of one lesson, or of every lesson in the course in lesson order."""
    lessons = [topics[topic_key]] if topic_key else [t for t in topics.values() if t.get('course_id') == course_id]
    return list(dict.fromkeys(k for lesson in lessons for k in lesson['knowledge_ids']))


LIBRARY = '''FROM knowledge_points k WHERE k.course_id=%s AND NOT k.archived
    AND NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id)'''


def library_ids(conn, course_id):
    """The course's knowledge library in teaching order: no archived points, no lesson-copy aliases."""
    return [r['id'] for r in conn.execute(f'SELECT k.id {LIBRARY} ORDER BY k.position,k.id', (course_id,))]


def name_key(name):
    """Point names match regardless of full- or half-width forms, spaces and letter case."""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', name)).casefold()


def library_names(conn, course_id):
    """{name_key: (id, name)} of the course library, archived points included.

    A visible point wins over an archived one with the same key, and the oldest visible point wins
    over later ones, so every writer that names a point reuses the same library entry."""
    found = {}
    for row in conn.execute('''SELECT k.id,k.name FROM knowledge_points k WHERE k.course_id=%s
            AND NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id)
            ORDER BY k.archived DESC,k.id DESC''', (course_id,)):
        found[name_key(row['name'])] = (row['id'], row['name'])
    return found


def diagnosed_library_ids(conn, course_id, student_ids):
    """Library points that at least one of these students has a recorded diagnosis for."""
    return [r['id'] for r in conn.execute(f'''SELECT k.id {LIBRARY} AND EXISTS (SELECT 1 FROM student_knowledge s
        WHERE s.knowledge_id=k.id AND s.student_id=ANY(%s) AND s.revision>0) ORDER BY k.position,k.id''',
                                          (course_id, list(student_ids)))]


def class_profile(conn, class_id, topics, topic_key=None):
    klass = class_row(conn, class_id)
    if topic_key and (topic_key not in topics or topics[topic_key].get('course_id') != klass['course_id']):
        raise ClassError('所选备课不属于这个班级的课程。')
    members = conn.execute('''SELECT s.id,s.name,s.subtitle,s.profile FROM class_students m
        JOIN students s ON s.id=m.student_id WHERE m.class_id=%s ORDER BY s.id''', (class_id,)).fetchall()
    ids = [m['id'] for m in members]
    names = {m['id']: m['name'] for m in members}
    points, seen = [], set()
    wanted = course_points(topics, klass['course_id'], topic_key)
    if not topic_key:
        # Library points outside every lesson still show once someone in the class is diagnosed.
        wanted = list(dict.fromkeys(wanted + diagnosed_library_ids(conn, klass['course_id'], ids)))
    for point in aggregate(conn, ids, wanted):
        # Lesson copies alias the same diagnosis; show each diagnosed point once.
        if point['canonical_id'] in seen:
            continue
        seen.add(point['canonical_id'])
        point['students'] = {state: [{'id': sid, 'name': names[sid]} for sid in group]
                             for state, group in point['groups'].items()}
        point['summary'] = distribution_text(point['counts'], len(ids))
        del point['groups']
        points.append(point)
    focus = {}
    for point in points:
        for student in point['students']['needs_support']:
            focus.setdefault(student['id'], {**student, 'points': []})['points'].append(point['name'])
    history = conn.execute('''SELECT * FROM class_profile_history WHERE class_id=%s ORDER BY id DESC LIMIT 20''',
                           (class_id,)).fetchall()
    diagnosed = sum(c for p in points for s, c in p['counts'].items() if s != 'unknown')
    cells = len(points) * len(ids)
    return {'class': json_dates(klass), 'students': members, 'points': points,
            'focus_students': sorted(focus.values(), key=lambda s: (-len(s['points']), s['id'])),
            'coverage': {'diagnosed': diagnosed, 'cells': cells}, 'rule': CLASS_RULE,
            'history': [json_dates(h) for h in history], 'state_labels': STATE_LABELS,
            'lessons': [{'key': k, 'title': t['title']} for k, t in topics.items() if t.get('course_id') == klass['course_id']]}


def register_classes(app, db, catalog):
    @app.errorhandler(ClassError)
    def class_error(exc):
        return jsonify(error=str(exc)), exc.status

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ClassError('请求格式必须是 JSON 对象。')
        return data

    @app.get('/api/courses')
    def courses():
        topics = catalog()
        with db() as conn:
            return jsonify(list_courses(conn, topics))

    @app.post('/api/courses')
    def create_course():
        with db() as conn:
            conn.execute('LOCK TABLE courses IN EXCLUSIVE MODE')
            row = save_course(conn, body())
        return jsonify(course=row), 201

    @app.put('/api/courses/<int:course_id>')
    def edit_course(course_id):
        with db() as conn:
            conn.execute('LOCK TABLE courses IN EXCLUSIVE MODE')
            row = save_course(conn, body(), course_id)
        return jsonify(course=row)

    @app.post('/api/courses/<int:course_id>/classes')
    def new_class(course_id):
        with db() as conn:
            conn.execute('LOCK TABLE classes IN EXCLUSIVE MODE')
            row = create_class(conn, course_id, body())
        return jsonify(**{'class': row}), 201

    @app.put('/api/classes/<int:class_id>')
    def edit_class(class_id):
        topics = catalog()
        with db() as conn:
            update_class(conn, class_id, body())
            result = class_profile(conn, class_id, topics)
        return jsonify(result)

    @app.get('/api/classes/<int:class_id>/profile')
    def read_class_profile(class_id):
        topics = catalog()
        with db() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            return jsonify(class_profile(conn, class_id, topics, request.args.get('topic') or None))

    @app.post('/api/classes/<int:class_id>/students')
    def add_student(class_id):
        """Enroll an existing student, or create one inside this class."""
        data = body()
        with db() as conn:
            if 'student_id' in data:
                if type(data['student_id']) is not int:
                    raise ClassError('请选择有效学生。')
                if data.get('move', False) not in (True, False):
                    raise ClassError('转班选项无效。')
                enroll(conn, class_id, data['student_id'], data.get('move', False))
                student = conn.execute('SELECT * FROM students WHERE id=%s', (data['student_id'],)).fetchone()
            else:
                class_row(conn, class_id)
                name = field(data, 'name', '姓名', 80)
                subtitle = field(data, 'subtitle', '年级或学生说明', 80)
                profile = field(data, 'profile', '学生信息与学习特点', 600)
                conn.execute('LOCK TABLE students IN EXCLUSIVE MODE')
                student = conn.execute('''INSERT INTO students(id,name,subtitle,profile)
                    VALUES ((SELECT COALESCE(max(id),0)+1 FROM students),%s,%s,%s) RETURNING *''',
                    (name, subtitle, profile)).fetchone()
                enroll(conn, class_id, student['id'])
        return jsonify(student=student), 201

    @app.post('/api/classes/<int:class_id>/students/<int:student_id>/remove')
    def remove_student(class_id, student_id):
        with db() as conn:
            leave(conn, class_id, student_id)
        return jsonify(removed=True)
