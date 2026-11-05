"""Batch CSV import of students and learning evidence: preview first, then one confirmed transaction.

Nothing is inferred. Every evidence row states its own source, observation date, decision and
reason, and is written through the same teacher-confirmed path as a single manual entry, so a
batch can never change mastery without an explicit row saying so. Any row error, or any change
to the roster or the affected states since the preview, rejects the whole batch.
"""
import csv
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import re

from flask import Response, jsonify, request

from catalog import load_catalog
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


def plan_students(conn, text):
    existing = {row['name'] for row in conn.execute('SELECT name FROM students')}
    seen, rows = set(), []
    for line, values in read_rows(text, COLUMNS['students']):
        errors = length_errors(values, COLUMNS['students'])
        if values['姓名'] in seen:
            errors.append('文件内姓名重复')
        seen.add(values['姓名'])
        skip = not errors and values['姓名'] in existing
        rows.append(dict(line=line, values=values, errors=errors,
                         action='error' if errors else 'skip' if skip else 'create',
                         note='已有同名学生，跳过；如需修改请在学生档案中维护。' if skip else ''))
    return rows, dict(names=sorted(existing))


def plan_evidence(conn, text, topic):
    roster = conn.execute('SELECT id,name FROM students WHERE id=ANY(%s)', (topic['student_ids'],)).fetchall()
    by_name = {}
    for student in roster:
        by_name.setdefault(student['name'], []).append(student['id'])
    points = {row['name']: row['id'] for row in conn.execute(
        'SELECT id,name FROM knowledge_points WHERE id=ANY(%s)', (topic['knowledge_ids'],))}
    aliases = {row['knowledge_id']: row['canonical_id'] for row in conn.execute(
        'SELECT knowledge_id,canonical_id FROM knowledge_aliases WHERE knowledge_id=ANY(%s)', (topic['knowledge_ids'],))}
    canonical = sorted({aliases.get(k, k) for k in topic['knowledge_ids']})
    current = {(row['student_id'], row['knowledge_id']): row for row in conn.execute(
        '''SELECT student_id,knowledge_id,state,revision FROM student_knowledge
           WHERE student_id=ANY(%s) AND knowledge_id=ANY(%s)''', (topic['student_ids'], canonical))}
    pending, rows = {}, []
    for line, values in read_rows(text, COLUMNS['evidence']):
        errors = length_errors(values, ['任务或观察内容', '学生表现', '确认理由'])
        ids = by_name.get(values['学生姓名'], [])
        if not ids:
            errors.append('所选课程中没有这名学生')
        elif len(ids) > 1:
            errors.append('课程中有同名学生，无法确定对应档案')
        kid = points.get(values['知识点'])
        if kid is None:
            errors.append('所选课程中没有这个知识点')
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
            sid, key = ids[0], (ids[0], aliases.get(kid, kid))
            state = pending.get(key, (current[key]['state'], current[key]['revision']) if key in current else ('unknown', 0))
            confirmed = new_state if decision == 'update' else state[0]
            row.update(student_id=sid, knowledge_id=kid, source=source, observed_at=observed.isoformat(),
                       decision=decision, new_state=new_state, expected_revision=state[1],
                       previous_label=STATE_LABELS[state[0]], confirmed_label=STATE_LABELS[confirmed])
            # Several rows for one point apply in file order, each against the version the previous one leaves.
            pending[key] = (confirmed, state[1] + 1)
        rows.append(row)
    return rows, {f'{s}:{k}': [r['state'], r['revision']] for (s, k), r in sorted(current.items())}


def plan(conn, kind, text, topic_key):
    if kind == 'students':
        return plan_students(conn, text)
    topics = load_catalog(conn)
    if topic_key not in topics:
        raise ProfileError('导入学习证据前，请选择对应的课程任务或备课副本。')
    return plan_evidence(conn, text, topics[topic_key])


def fingerprint(kind, text, topic_key, snapshot):
    payload = json.dumps(dict(kind=kind, topic=topic_key, text=sha256(text.encode()).hexdigest(), snapshot=snapshot),
                         ensure_ascii=False, sort_keys=True)
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
        return kind, text, request.form.get('topic', '')

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
        kind, text, topic = form()
        with db() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            rows, snapshot = plan(conn, kind, text, topic)
        return jsonify(rows=rows, summary=summary(kind, rows), token=fingerprint(kind, text, topic, snapshot))

    @app.post('/api/students/import')
    def roster_import():
        kind, text, topic = form()
        recorder = request.form.get('recorder', '').strip()
        if request.form.get('confirmed') != 'true':
            raise ProfileError('请由教师确认已核对预览内容。')
        if kind == 'evidence' and not 1 <= len(recorder) <= 80:
            raise ProfileError('请填写 1—80 字的录入者。')
        with db() as conn:
            # Serialize roster writers, then re-plan inside the same transaction that writes.
            conn.execute('LOCK TABLE students IN EXCLUSIVE MODE')
            rows, snapshot = plan(conn, kind, text, topic)
            if fingerprint(kind, text, topic, snapshot) != request.form.get('token'):
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
                elif row['action'] == 'record':
                    record_evidence(conn, row['student_id'], dict(
                        knowledge_id=row['knowledge_id'], expected_revision=row['expected_revision'], source=row['source'],
                        task_content=row['values']['任务或观察内容'], performance=row['values']['学生表现'],
                        observed_at=row['observed_at'], recorder=recorder, decision=row['decision'],
                        new_state=row['new_state'], reason=row['values']['确认理由'], confirmed=True))
        result = summary(kind, rows)
        return jsonify(**result, created_ids=created), 201
