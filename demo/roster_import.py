"""Batch CSV import of students and learning evidence: preview first, then one confirmed transaction.

Nothing is inferred. Every evidence row states its own source, observation date, decision and
reason, and is written through the same teacher-confirmed path as a single manual entry, so a
batch can never change mastery without an explicit row saying so. Any row error, or any change
to the roster or the affected states since the preview, rejects the whole batch.
"""
import csv
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
from hashlib import sha256
import io
import json
import re

from flask import Response, jsonify, request

from catalog import load_catalog
from classes import library_ids, name_key
from profiles import SOURCE_LABELS, STATE_LABELS, ProfileError, record_evidence

MAX_BYTES = 120_000
MAX_ROWS = 300
COLUMNS = {
    'students': ['姓名', '年级或说明', '学习特点'],
    'evidence': ['学生姓名', '知识点', '证据来源', '任务或观察内容', '学生表现', '观察日期', '判断', '新掌握状态', '确认理由'],
}
EXAMPLES = {
    'students': ['小王', '初二 · 方程基础巩固', '计算较稳，解释等式变形依据时容易跳步；喜欢先用具体数字检验。'],
    'evidence': ['小林', '等式的加减性质', '作业', '作业第3题：解 x + 5 = 12 并说明依据',
                 '答案正确，但写成“把5移过去”，没有说明两边同减5。', '2026-10-20', '更新状态', '部分掌握',
                 '计算正确但依据表述不完整，确认部分掌握。'],
}
SOURCES = {label: code for code, label in SOURCE_LABELS.items() if code != 'initial_snapshot'}
STATES = {label: code for code, label in STATE_LABELS.items()}
DECISIONS = {'更新状态': 'update', '更新': 'update', '保留原判断': 'retain', '保留': 'retain'}
LIMITS = {'姓名': 80, '年级或说明': 80, '学习特点': 600, '任务或观察内容': 1200, '学生表现': 2000, '确认理由': 600}


def decode(raw):
    if not raw:
        raise ProfileError('请选择 CSV 文件。')
    if len(raw) > MAX_BYTES:
        raise ProfileError(f'文件超过 {MAX_BYTES // 1000} KB，请拆分后分批导入。')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ProfileError('无法识别文件编码，请另存为 UTF-8 或 GBK 编码的 CSV。')


def read_rows(text, columns):
    reader = csv.reader(io.StringIO(text))
    try:
        rows = [(reader.line_num, [c.strip() for c in row]) for row in reader]
    except csv.Error:
        raise ProfileError('CSV 格式无法解析，请检查引号是否成对。') from None
    rows = [(line, row) for line, row in rows if any(row)]
    if not rows:
        raise ProfileError('文件没有内容。')
    header = [c.lstrip('﻿') for c in rows[0][1]]
    missing = [c for c in columns if c not in header]
    if missing:
        raise ProfileError('缺少列：' + '、'.join(missing) + '。请下载页面提供的模板填写。')
    if not rows[1:]:
        raise ProfileError('文件只有表头，没有数据行。')
    if len(rows) - 1 > MAX_ROWS:
        raise ProfileError(f'每次最多导入 {MAX_ROWS} 行，请拆分文件。')
    index = {c: header.index(c) for c in columns}
    return [(line, {c: row[index[c]] if index[c] < len(row) else '' for c in columns}) for line, row in rows[1:]]


def parse_date(value):
    match = re.fullmatch(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?', value.strip())
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    try:
        # A date alone means that day in local time; noon keeps it on the same calendar day.
        parsed = datetime(int(year), int(month), int(day), int(hour or 12), int(minute or 0)).astimezone()
    except ValueError:
        return None
    return None if parsed > datetime.now(timezone.utc) + timedelta(minutes=5) else parsed


def length_errors(values, keys):
    return [f'{key}须为 1—{LIMITS[key]} 字' for key in keys if not values[key] or len(values[key]) > LIMITS[key]]


def plan_students(conn, text, class_id=None):
    """Into a class, a name already in that class is skipped; same names elsewhere are other people."""
    if class_id is None:
        existing = {row['name'] for row in conn.execute('SELECT name FROM students')}
        elsewhere = set()
    else:
        if not conn.execute('SELECT 1 FROM classes WHERE id=%s', (class_id,)).fetchone():
            raise ProfileError('班级不存在，请刷新后重试。', 404)
        existing = {row['name'] for row in conn.execute('''SELECT s.name FROM students s
            JOIN class_students m ON m.student_id=s.id WHERE m.class_id=%s''', (class_id,))}
        elsewhere = {row['name'] for row in conn.execute('SELECT name FROM students')} - existing
    seen, rows = set(), []
    for line, values in read_rows(text, COLUMNS['students']):
        errors = length_errors(values, COLUMNS['students'])
        if values['姓名'] in seen:
            errors.append('文件内姓名重复')
        seen.add(values['姓名'])
        skip = not errors and values['姓名'] in existing
        note = ('本班已有同名学生，跳过；如需修改请在学生档案中维护。' if class_id is not None else
                '已有同名学生，跳过；如需修改请在学生档案中维护。') if skip else (
                '其他班级有同名学生，将新建档案；如是同一人，请改用「添加已有学生」。'
                if not errors and values['姓名'] in elsewhere else '')
        rows.append(dict(line=line, values=values, errors=errors,
                         action='error' if errors else 'skip' if skip else 'create', note=note))
    return rows, dict(names=sorted(existing), class_id=class_id)


def evidence_scope(conn, topic, class_id, course_id):
    """(course, students, knowledge ids) an evidence file may name.

    Points are the course knowledge library, so any confirmed point can take evidence without a
    lesson that ticks it. A chosen lesson adds its own points (copies, lessons saved before courses).
    """
    if class_id is not None:
        row = conn.execute('SELECT course_id FROM classes WHERE id=%s', (class_id,)).fetchone()
        if not row:
            raise ProfileError('班级不存在，请刷新后重试。', 404)
        course_id = row['course_id']
    elif course_id is None and topic:
        course_id = topic.get('course_id')
    if course_id is not None and not conn.execute('SELECT 1 FROM courses WHERE id=%s', (course_id,)).fetchone():
        raise ProfileError('课程不存在，请刷新后重试。', 404)
    if topic and topic.get('course_id') not in (None, course_id):
        raise ProfileError('所选备课不属于当前课程，请重新选择。')
    if course_id is None and not topic:
        raise ProfileError('导入学习证据前，请先选择课程或班级。')
    if class_id is not None:
        students = {r['student_id'] for r in conn.execute('SELECT student_id FROM class_students WHERE class_id=%s', (class_id,))}
    else:
        students = set(topic['student_ids']) if topic else set()
        if course_id is not None:
            students |= {r['student_id'] for r in conn.execute(
                'SELECT student_id FROM class_students WHERE course_id=%s', (course_id,))}
    ids = library_ids(conn, course_id) if course_id is not None else []
    ids += [k for k in (topic or {}).get('knowledge_ids', []) if k not in ids]
    return course_id, sorted(students), ids


ARCHIVED = '''SELECT name FROM knowledge_points k WHERE k.course_id=%s AND k.archived
    AND NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id)'''


def point_finder(conn, course_id, ids):
    """name -> (knowledge id, error): exact names after name_key, archived points and near misses."""
    aliases = {row['knowledge_id']: row['canonical_id'] for row in conn.execute(
        'SELECT knowledge_id,canonical_id FROM knowledge_aliases WHERE knowledge_id=ANY(%s)', (ids,))}
    names = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM knowledge_points WHERE id=ANY(%s)', (ids,))}
    points = {}
    for kid in ids:  # library order first: a lesson's copy of a library point resolves to the library point
        points.setdefault(name_key(names[kid]), []).append(kid)
    archived = {name_key(row['name']) for row in conn.execute(ARCHIVED, (course_id,))} if course_id is not None else set()
    # Near misses are compared with and without a bracketed gloss such as 「（Poisson）」.
    variants = {}
    for name in names.values():
        for variant in (name_key(name), name_key(re.sub(r'[（(][^）)]*[）)]', '', name))):
            variants.setdefault(variant, name)
    where = '本课程知识点库' if course_id is not None else '所选备课'

    def find(name):
        key = name_key(name)
        matches = points.get(key, [])
        # Older data may hold 「A（B）」 and 「A(B)」 side by side: a name written exactly as one of them picks it.
        exact = [k for k in matches if names[k] == name]
        if len({aliases.get(k, k) for k in exact}) == 1:
            matches = exact
        if len({aliases.get(k, k) for k in matches}) > 1:
            return None, '有多个同名知识点，无法确定对应哪一个；请在知识点库中改名后重试'
        if matches:
            return matches[0], None
        if key in archived:
            return None, '这个知识点已归档；如需记录，请先在知识点库中恢复'
        near = get_close_matches(key, variants, n=1, cutoff=0.6)
        return None, (f'{where}中没有这个知识点' + (f'，是否指「{variants[near[0]]}」？' if near else '')
                      + ('；名称须与「课程 → 知识点库」中的一致' if course_id is not None and not near else ''))
    return find, aliases


def plan_evidence(conn, text, topic, class_id=None, course_id=None):
    course_id, allowed, ids = evidence_scope(conn, topic, class_id, course_id)
    by_name = {}
    for student in conn.execute('SELECT id,name FROM students WHERE id=ANY(%s)', (allowed,)):
        by_name.setdefault(student['name'], []).append(student['id'])
    find, aliases = point_finder(conn, course_id, ids)
    rows = []
    for line, values in read_rows(text, COLUMNS['evidence']):
        errors = length_errors(values, ['任务或观察内容', '学生表现', '确认理由'])
        sids = by_name.get(values['学生姓名'], [])
        if not sids:
            errors.append('所选班级中没有这名学生' if class_id is not None else '所选课程中没有这名学生')
        elif len(sids) > 1:
            errors.append('课程中有同名学生，无法确定对应档案')
        kid, problem = find(values['知识点'])
        if problem:
            errors.append(problem)
        source = SOURCES.get(values['证据来源'])
        if source is None:
            errors.append('证据来源须为作业、测验、课堂观察或教师复核')
        observed = parse_date(values['观察日期'])
        if observed is None:
            errors.append('观察日期须为 2026-10-20 形式的有效日期，且不晚于今天')
        decision = DECISIONS.get(values['判断'])
        if decision is None:
            errors.append('判断须为「更新状态」或「保留原判断」')
        new_state = STATES.get(values['新掌握状态']) if decision == 'update' else None
        if decision == 'update' and new_state is None:
            errors.append('更新状态时须填写需要补充、部分掌握、已掌握或未知')
        row = dict(line=line, values=values, errors=errors, action='error' if errors else 'record')
        if not errors:
            row.update(student_id=sids[0], knowledge_id=kid, source=source, observed_at=observed.isoformat(),
                       decision=decision, new_state=new_state)
        rows.append(row)
    # Only the states this file touches are read and fingerprinted: other diagnoses in the class may
    # change between preview and import without voiding the batch.
    keys = {(r['student_id'], aliases.get(r['knowledge_id'], r['knowledge_id'])) for r in rows if not r['errors']}
    current = {(row['student_id'], row['knowledge_id']): row for row in conn.execute(
        '''SELECT student_id,knowledge_id,state,revision FROM student_knowledge
           WHERE student_id=ANY(%s) AND knowledge_id=ANY(%s)''',
        (sorted({s for s, _ in keys}), sorted({k for _, k in keys})))
        if (row['student_id'], row['knowledge_id']) in keys}
    pending = {}
    for row in rows:
        if row['errors']:
            continue
        key = (row['student_id'], aliases.get(row['knowledge_id'], row['knowledge_id']))
        state = pending.get(key, (current[key]['state'], current[key]['revision']) if key in current else ('unknown', 0))
        confirmed = row['new_state'] if row['decision'] == 'update' else state[0]
        row.update(expected_revision=state[1], previous_label=STATE_LABELS[state[0]], confirmed_label=STATE_LABELS[confirmed])
        # Several rows for one point apply in file order, each against the version the previous one leaves.
        pending[key] = (confirmed, state[1] + 1)
    # A row now naming another student or point (a rename, a roster change) also voids the preview.
    snapshot = dict(course=course_id, rows=[[r['line'], r.get('student_id'), r.get('knowledge_id')] for r in rows],
                    states={f'{s}:{k}': [r['state'], r['revision']] for (s, k), r in sorted(current.items())})
    return rows, snapshot


def plan(conn, kind, text, topic_key, class_id=None, course_id=None):
    if kind == 'students':
        return plan_students(conn, text, class_id)
    topic = None
    if topic_key:
        topics = load_catalog(conn)
        if topic_key not in topics:
            raise ProfileError('所选备课不存在，请刷新后重试。', 404)
        topic = topics[topic_key]
    return plan_evidence(conn, text, topic, class_id, course_id)


def fingerprint(kind, text, topic_key, snapshot, class_id=None):
    fields = dict(kind=kind, topic=topic_key, text=sha256(text.encode()).hexdigest(), snapshot=snapshot)
    if class_id is not None:
        fields['class_id'] = class_id
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return sha256(payload.encode()).hexdigest()


def summary(kind, rows):
    counts = {}
    for row in rows:
        counts[row['action']] = counts.get(row['action'], 0) + 1
    return dict(kind=kind, total=len(rows), errors=counts.get('error', 0),
                ready=counts.get('create', 0) + counts.get('record', 0), skipped=counts.get('skip', 0))


def register_roster_import(app, db):
    def form():
        kind = request.form.get('kind', '')
        if kind not in COLUMNS:
            raise ProfileError('请选择导入学生名单或学习证据。')
        upload = request.files.get('file')
        text = decode(upload.read(MAX_BYTES + 1) if upload else b'')
        ids = {}
        for field, label in (('class_id', '班级'), ('course_id', '课程')):
            raw = request.form.get(field, '')
            if raw and not raw.isdigit():
                raise ProfileError(f'{label}编号无效，请刷新后重试。')
            ids[field] = int(raw) if raw else None
        # Evidence points come from the class's course (or the course given); students go into the class.
        return kind, text, request.form.get('topic', ''), ids['class_id'], ids['course_id']

    @app.get('/api/students/import/template')
    def roster_template():
        kind = request.args.get('kind', '')
        if kind not in COLUMNS:
            raise ProfileError('请选择有效的模板。')
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(COLUMNS[kind])
        writer.writerow(EXAMPLES[kind])
        name = '学生名单模板.csv' if kind == 'students' else '学习证据模板.csv'
        from urllib.parse import quote
        return Response('﻿' + stream.getvalue(), content_type='text/csv; charset=utf-8', headers={
            'Content-Disposition': f"attachment; filename=roster-{kind}.csv; filename*=UTF-8''{quote(name)}"})

    @app.post('/api/students/import/preview')
    def roster_preview():
        kind, text, topic, class_id, course_id = form()
        with db() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            rows, snapshot = plan(conn, kind, text, topic, class_id, course_id)
        return jsonify(rows=rows, summary=summary(kind, rows), token=fingerprint(kind, text, topic, snapshot, class_id))

    @app.post('/api/students/import')
    def roster_import():
        kind, text, topic, class_id, course_id = form()
        recorder = request.form.get('recorder', '').strip()
        if request.form.get('confirmed') != 'true':
            raise ProfileError('请由教师确认已核对预览内容。')
        if kind == 'evidence' and not 1 <= len(recorder) <= 80:
            raise ProfileError('请填写 1—80 字的录入者。')
        with db() as conn:
            # Serialize roster writers, then re-plan inside the same transaction that writes.
            conn.execute('LOCK TABLE students IN EXCLUSIVE MODE')
            conn.execute('LOCK TABLE class_students IN EXCLUSIVE MODE')
            rows, snapshot = plan(conn, kind, text, topic, class_id, course_id)
            if fingerprint(kind, text, topic, snapshot, class_id) != request.form.get('token'):
                raise ProfileError('预览之后学生名单或画像已变化，本次未导入。请重新预览并核对。', 409)
            failed = [row['line'] for row in rows if row['errors']]
            if failed:
                raise ProfileError(f'仍有 {len(failed)} 行未通过检查（第 {"、".join(map(str, failed[:10]))} 行），请修改文件后重新预览。')
            created = []
            for row in rows:
                if row['action'] == 'create':
                    created.append(conn.execute('''INSERT INTO students(id,name,subtitle,profile)
                        VALUES ((SELECT COALESCE(max(id),0)+1 FROM students),%s,%s,%s) RETURNING id''',
                        (row['values']['姓名'], row['values']['年级或说明'], row['values']['学习特点'])).fetchone()['id'])
                    if class_id is not None:
                        conn.execute('''INSERT INTO class_students(class_id,course_id,student_id)
                            SELECT id,course_id,%s FROM classes WHERE id=%s''', (created[-1], class_id))
                elif row['action'] == 'record':
                    record_evidence(conn, row['student_id'], dict(
                        knowledge_id=row['knowledge_id'], expected_revision=row['expected_revision'], source=row['source'],
                        task_content=row['values']['任务或观察内容'], performance=row['values']['学生表现'],
                        observed_at=row['observed_at'], recorder=recorder, decision=row['decision'],
                        new_state=row['new_state'], reason=row['values']['确认理由'], confirmed=True))
        result = summary(kind, rows)
        return jsonify(**result, created_ids=created), 201
