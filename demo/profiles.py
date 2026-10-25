"""Teacher-confirmed profile changes. Evidence never infers mastery automatically."""
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

STATE_LABELS = {'needs_support': '需要补充', 'partial': '部分掌握',
                'mastered': '已掌握', 'unknown': '未知'}
STRATEGIES = {
    'needs_support': '补充基础解释，用一个小例子逐步说明每一步的原因。',
    'partial': '简要回顾，重点解释证据中暴露的错误，并给出正确步骤。',
    'mastered': '压缩基础复述；围绕本次目标给出一个迁移或条件变化的例子。',
    'unknown': '不判断为薄弱；先提供适中解释，并明确暂无充分诊断依据。'
}
SOURCE_LABELS = {'initial_snapshot': '原有画像留存', 'homework': '作业',
                 'quiz': '测验', 'observation': '课堂观察', 'teacher_review': '教师复核'}


class ProfileError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def json_dates(row):
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}


def decorate_states(states):
    for state in states:
        state['label'] = STATE_LABELS[state['state']]
        state['strategy'] = STRATEGIES[state['state']]
        if isinstance(state.get('updated_at'), datetime):
            state['updated_at'] = state['updated_at'].isoformat()
    return states


def evidence_history(conn, student_id, knowledge_ids=None, limit=50, after_revision=None):
    rows = conn.execute('''SELECT e.*, k.name AS knowledge_name,
        h.previous_state, h.confirmed_state, h.decision, h.reason,
        h.previous_evidence, h.previous_revision, h.new_revision
        FROM learning_evidence e JOIN knowledge_points k ON k.id=e.knowledge_id
        JOIN knowledge_state_history h ON h.evidence_id=e.id
        WHERE e.student_id=%s AND (%s::integer[] IS NULL OR e.knowledge_id=ANY(%s))
        AND (%s::integer IS NULL OR h.new_revision>%s)
        ORDER BY e.id DESC LIMIT %s''',
        (student_id, knowledge_ids, knowledge_ids, after_revision, after_revision, limit)).fetchall()
    return [json_dates(row) for row in rows]


def compare_lecture_profile(conn, lecture):
    """Compare stored model context with live evidence in one read-only snapshot.

    Missing legacy fields stay missing. No-profile inputs are never reconstructed
    from today's student record. Scope comes from the saved lecture, not catalog.
    """
    context = lecture['context']
    mode = context.get('personalization_mode', 'profile')
    historical_student = context.get('student') if mode == 'profile' else None
    historical_states = context.get('states', []) if mode == 'profile' else []
    previous = {s['id']: s for s in historical_states}
    knowledge_ids = list(dict.fromkeys([
        *context.get('topic', {}).get('knowledge_ids', []), *previous,
    ]))
    student = conn.execute('SELECT * FROM students WHERE id=%s',
                           (lecture['student_id'],)).fetchone()
    if not student:
        raise ProfileError('学生不存在。', 404)
    states = conn.execute('''SELECT k.id,k.name,COALESCE(a.canonical_id,k.id) AS profile_knowledge_id,COALESCE(s.state,'unknown') AS state,
        COALESCE(s.evidence,'没有诊断记录。') AS evidence,COALESCE(s.revision,0) AS revision,
        s.evidence_id,s.updated_at FROM knowledge_points k LEFT JOIN knowledge_aliases a ON a.knowledge_id=k.id
        LEFT JOIN student_knowledge s ON s.knowledge_id=COALESCE(a.canonical_id,k.id) AND s.student_id=%s WHERE k.id=ANY(%s) ORDER BY k.id''',
        (lecture['student_id'], knowledge_ids)).fetchall()
    current = {s['id']: s for s in decorate_states(states)}
    points = []
    for knowledge_id in knowledge_ids:
        before = previous.get(knowledge_id)
        after = current.get(knowledge_id)
        if after:
            after['recent_evidence'] = evidence_history(conn, student['id'], [after['profile_knowledge_id']], limit=3)
        changes = [field for field in ('state', 'evidence', 'strategy')
                   if before and after and isinstance(before.get(field), str)
                   and before[field] != after[field]]
        revision = before.get('revision') if before else None
        revision_known = type(revision) is int and revision >= 0
        added_count = None
        added = []
        if revision_known and after:
            added_count = conn.execute('''SELECT count(*) AS n FROM learning_evidence e
                JOIN knowledge_state_history h ON h.evidence_id=e.id
                WHERE e.student_id=%s AND e.knowledge_id=%s AND h.new_revision>%s''',
                (student['id'], after['profile_knowledge_id'], revision)).fetchone()['n']
            added = evidence_history(conn, student['id'], [after['profile_knowledge_id']], limit=3, after_revision=revision)
        missing = [field for field in ('state', 'evidence', 'strategy', 'revision', 'recent_evidence')
                   if before is None or before.get(field) is None]
        if before and not revision_known and 'revision' not in missing:
            missing.append('revision')
        points.append({
            'id': knowledge_id, 'name': (before or after or {}).get('name', '原知识点未找到'),
            'historical': before, 'current': after, 'changes': changes,
            'missing_historical_fields': missing, 'new_evidence_count': added_count,
            'new_evidence': added,
            'changed': bool(changes or added_count),
        })
    profile_changes = [field for field in ('subtitle', 'profile')
                       if historical_student and field in historical_student
                       and historical_student[field] != student[field]]
    return {
        'lecture_id': lecture['id'], 'topic': lecture['topic'], 'goal': lecture['goal'],
        'generated_at': lecture['created_at'].isoformat(),
        'compared_at': datetime.now(timezone.utc).isoformat(), 'personalization_mode': mode,
        'historical_student': historical_student, 'current_student': student,
        'profile_changes': profile_changes, 'points': points,
        'summary': {
            'changed_points': sum(p['changed'] for p in points),
            'state_changes': sum('state' in p['changes'] for p in points),
            'evidence_changes': sum('evidence' in p['changes'] for p in points),
            'strategy_changes': sum('strategy' in p['changes'] for p in points),
            'incomplete_points': sum(bool(p['missing_historical_fields']) for p in points) if mode == 'profile' else 0,
        },
        'source_labels': SOURCE_LABELS, 'state_labels': STATE_LABELS,
    }


def retain_initial_evidence(conn):
    """Retain legacy summaries once; do not invent an observation date or teacher."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute('''SELECT * FROM student_knowledge
            WHERE evidence_id IS NULL ORDER BY student_id,knowledge_id FOR UPDATE''').fetchall()
        for row in rows:
            evidence = cursor.execute('''INSERT INTO learning_evidence
                (student_id,knowledge_id,source,task_content,performance,observed_at,recorder)
                VALUES (%s,%s,'initial_snapshot',%s,%s,NULL,%s) RETURNING id''',
                (row['student_id'], row['knowledge_id'], '原有虚拟画像；未独立记录任务和观察时间。',
                 row['evidence'], '系统迁移（原录入者未记录）')).fetchone()
            cursor.execute('''INSERT INTO knowledge_state_history
                (evidence_id,previous_state,confirmed_state,decision,reason,
                 previous_evidence,previous_revision,new_revision)
                VALUES (%s,%s,%s,'baseline',%s,%s,%s,%s)''',
                (evidence['id'], row['state'], row['state'], '原样留存已有状态，迁移时间不是观察时间。',
                 row['evidence'], row['revision'], row['revision']))
            cursor.execute('''UPDATE student_knowledge SET evidence_id=%s
                WHERE student_id=%s AND knowledge_id=%s''',
                (evidence['id'], row['student_id'], row['knowledge_id']))


def get_profile(conn, student_id):
    student = conn.execute('SELECT * FROM students WHERE id=%s', (student_id,)).fetchone()
    if not student:
        raise ProfileError('学生不存在。', 404)
    states = conn.execute('''SELECT k.id,k.name,COALESCE(s.state,'unknown') AS state,
        COALESCE(s.evidence,'没有诊断记录。') AS evidence, COALESCE(s.revision,0) AS revision,
        s.evidence_id,s.updated_at FROM knowledge_points k LEFT JOIN student_knowledge s
        ON s.knowledge_id=k.id AND s.student_id=%s
        WHERE NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id) ORDER BY k.id''', (student_id,)).fetchall()
    edits = conn.execute('''SELECT * FROM student_profile_history
        WHERE student_id=%s ORDER BY id DESC LIMIT 20''', (student_id,)).fetchall()
    count = conn.execute('SELECT count(*) AS n FROM learning_evidence WHERE student_id=%s',
                         (student_id,)).fetchone()['n']
    return {'student': student, 'states': decorate_states(states),
            'evidence_history': evidence_history(conn, student_id),
            'evidence_count': count, 'profile_history': [json_dates(row) for row in edits],
            'source_labels': SOURCE_LABELS, 'state_labels': STATE_LABELS}


def text_field(data, key, label, maximum):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ProfileError(f'{label}不能为空，且不得超过 {maximum} 字。')
    return value.strip()


def check_confirmation(data):
    if not isinstance(data, dict):
        raise ProfileError('请求格式必须是 JSON 对象。')
    if data.get('confirmed') is not True:
        raise ProfileError('请由教师确认本次画像修改。')
    revision = data.get('expected_revision')
    if type(revision) is not int or not 0 <= revision <= 2147483646:
        raise ProfileError('缺少有效的画像版本，请重新读取当前画像。')
    return revision


def lock_student(conn, student_id):
    student = conn.execute('SELECT * FROM students WHERE id=%s FOR UPDATE', (student_id,)).fetchone()
    if not student:
        raise ProfileError('学生不存在。', 404)
    return student


def update_profile(conn, student_id, data):
    revision = check_confirmation(data)
    subtitle = text_field(data, 'subtitle', '学生说明', 80)
    profile = text_field(data, 'profile', '学习特点', 600)
    recorder = text_field(data, 'recorder', '录入者', 80)
    reason = text_field(data, 'reason', '修改理由', 600)
    student = lock_student(conn, student_id)
    name = text_field(data, 'name', '姓名', 80) if 'name' in data else student['name']
    if student['profile_revision'] != revision:
        raise ProfileError('学生说明已被更新。本次未保存，请重新读取并比较当前画像。', 409)
    if name == student['name'] and subtitle == student['subtitle'] and profile == student['profile']:
        raise ProfileError('学生说明和学习特点没有变化，无需保存。')
    updated = {**student, 'name': name, 'subtitle': subtitle, 'profile': profile, 'profile_revision': revision + 1}
    conn.execute('''INSERT INTO student_profile_history
        (student_id,previous_snapshot,new_snapshot,recorder,reason) VALUES (%s,%s,%s,%s,%s)''',
        (student_id, Jsonb(student), Jsonb(updated), recorder, reason))
    conn.execute('UPDATE students SET name=%s,subtitle=%s,profile=%s,profile_revision=%s WHERE id=%s',
                 (name, subtitle, profile, revision + 1, student_id))


def record_evidence(conn, student_id, data):
    revision = check_confirmation(data)
    knowledge_id = data.get('knowledge_id')
    if type(knowledge_id) is not int or knowledge_id <= 0:
        raise ProfileError('请选择有效知识点。')
    source = data.get('source')
    if not isinstance(source, str) or source not in SOURCE_LABELS or source == 'initial_snapshot':
        raise ProfileError('请选择作业、测验、课堂观察或教师复核作为证据来源。')
    task_content = text_field(data, 'task_content', '任务或观察内容', 1200)
    performance = text_field(data, 'performance', '学生表现', 2000)
    recorder = text_field(data, 'recorder', '录入者', 80)
    reason = text_field(data, 'reason', '确认理由', 600)
    decision, new_state = data.get('decision'), data.get('new_state')
    if decision not in ('update', 'retain'):
        raise ProfileError('请选择更新状态或保留原判断。')
    if decision == 'update' and (not isinstance(new_state, str) or new_state not in STATE_LABELS):
        raise ProfileError('请选择有效掌握状态。')
    try:
        observed_at = datetime.fromisoformat(data.get('observed_at', ''))
        if observed_at.utcoffset() is None or observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ProfileError('观察时间须包含时区，且不能晚于当前时间。') from None
    lock_student(conn, student_id)
    alias = conn.execute('SELECT canonical_id FROM knowledge_aliases WHERE knowledge_id=%s', (knowledge_id,)).fetchone()
    if alias:
        knowledge_id = alias['canonical_id']
    if not conn.execute('SELECT id FROM knowledge_points WHERE id=%s', (knowledge_id,)).fetchone():
        raise ProfileError('知识点不存在。', 404)
    conn.execute('''INSERT INTO student_knowledge (student_id,knowledge_id,state,evidence)
        VALUES (%s,%s,'unknown','没有诊断记录。') ON CONFLICT DO NOTHING''', (student_id, knowledge_id))
    old = conn.execute('''SELECT * FROM student_knowledge WHERE student_id=%s AND knowledge_id=%s
        FOR UPDATE''', (student_id, knowledge_id)).fetchone()
    if old['revision'] != revision:
        raise ProfileError('该知识点已被更新。本次证据和状态均未保存，请重新读取并比较当前画像。', 409)
    confirmed_state = new_state if decision == 'update' else old['state']
    evidence = conn.execute('''INSERT INTO learning_evidence
        (student_id,knowledge_id,source,task_content,performance,observed_at,recorder)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (student_id, knowledge_id, source, task_content, performance, observed_at, recorder)).fetchone()
    conn.execute('''INSERT INTO knowledge_state_history
        (evidence_id,previous_state,confirmed_state,decision,reason,
         previous_evidence,previous_revision,new_revision) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (evidence['id'], old['state'], confirmed_state, decision, reason, old['evidence'], revision, revision + 1))
    # Even retain increments the record version: another teacher must see the new evidence.
    conn.execute('''UPDATE student_knowledge SET state=%s,evidence=%s,evidence_id=%s,
        revision=%s,updated_at=now() WHERE student_id=%s AND knowledge_id=%s''',
        (confirmed_state, performance if decision == 'update' else old['evidence'],
         evidence['id'] if decision == 'update' else old['evidence_id'], revision + 1, student_id, knowledge_id))
