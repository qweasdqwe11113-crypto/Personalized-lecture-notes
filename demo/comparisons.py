"""Read-only lecture comparisons; common goal comes from saved rows, never today's template."""
import hashlib
import json
import re

from reviews import ReviewError, change_report, lecture_row, version_row


def knowledge_scope(context):
    ids = context.get('topic', {}).get('knowledge_ids')
    return tuple(sorted(set(ids))) if isinstance(ids, list) and ids and all(type(i) is int for i in ids) else None


def same_task(left, right):
    if not left['goal'].strip() or left['goal'] != right['goal'] or left['topic'] != right['topic']:
        return False
    a, b = knowledge_scope(left['context']), knowledge_scope(right['context'])
    return a is None or b is None or a == b


def identity(row):
    context = row['context']
    mode = context.get('personalization_mode', 'profile')
    return {'id': row['id'], 'student_id': row['student_id'],
            'name': context.get('student', {}).get('name', '旧记录未提供姓名'),
            'mode': mode, 'topic': row['topic'], 'goal': row['goal'],
            'created_at': row['created_at'].isoformat(), 'model': row['model']}


def comparison_options(conn, lecture_id):
    base = lecture_row(conn, lecture_id)
    if not base['goal'].strip():
        raise ReviewError('这份记录没有明确的教学目标，无法建立同目标比较。')
    rows = conn.execute('''SELECT id,student_id,topic,goal,context,created_at,model
        FROM lectures WHERE topic=%s AND goal=%s ORDER BY id DESC LIMIT 101''',
        (base['topic'], base['goal'])).fetchall()
    truncated = len(rows) > 100
    rows = [row for row in rows[:100] if same_task(base, row)]
    if not any(row['id'] == lecture_id for row in rows):
        rows.append(base)
    revisions = conn.execute('''SELECT lecture_id,revision_number,status,recorder,created_at
        FROM lecture_revisions WHERE lecture_id=ANY(%s) ORDER BY revision_number DESC''',
        ([row['id'] for row in rows],)).fetchall()
    candidates = []
    for row in rows:
        versions = [{'revision_number': 0, 'status': 'unreviewed', 'recorder': None,
                     'created_at': row['created_at'].isoformat()}]
        versions.extend({**r, 'created_at': r['created_at'].isoformat()}
                        for r in revisions if r['lecture_id'] == row['id'])
        candidates.append({**identity(row), 'versions': versions})
    return {'base_id': lecture_id, 'topic': base['topic'], 'goal': base['goal'],
            'candidates': candidates, 'truncated': truncated}


def split_sections(content):
    """Retain every line, repeated headings and headings inside code fences."""
    lines = content.splitlines(keepends=True)
    sections, seen = [], {}
    title, start, fence = '标题与开场', 0, None

    def append(end):
        if end <= start:
            return
        occurrence = seen.get(title, 0) + 1
        seen[title] = occurrence
        sections.append({'title': title, 'occurrence': occurrence, 'start_line': start + 1,
                         'end_line': end, 'text': ''.join(lines[start:end])})

    for index, line in enumerate(lines):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        heading = re.match(r'^##[ \t]+(.+?)\s*$', line) if fence is None else None
        if heading:
            append(index)
            title, start = re.sub(r'[ \t]+#+$', '', heading[1]).strip(), index
    append(len(lines))
    return sections


def align_sections(left, right):
    old, new = split_sections(left), split_sections(right)
    keys = lambda sections: [(s['title'], s['occurrence']) for s in sections]
    before, after = dict(zip(keys(old), old)), dict(zip(keys(new), new))
    result = []
    for key in list(before) + [key for key in after if key not in before]:
        a, b = before.get(key), after.get(key)
        changes = change_report(a['text'] if a else '', b['text'] if b else '')
        for block in changes['changed_blocks']:
            block['before_start'] += (a['start_line'] - 1) if a else 0
            block['after_start'] += (b['start_line'] - 1) if b else 0
        # Saving an editor strips the final newline. CRLF/LF and that trailing
        # newline are not a content change, while actual lines stay intact.
        status = 'right_only' if a is None else 'left_only' if b is None else 'same' if a['text'].splitlines() == b['text'].splitlines() else 'changed'
        result.append({'title': key[0], 'occurrence': key[1], 'left': a, 'right': b,
                       'status': status, 'changes': changes})
    return result, keys(old) != keys(new)


def recorded_settings(conn, lecture):
    run = conn.execute('''SELECT prompt_version,input_version,request_body,request_sha256
        FROM generation_runs WHERE lecture_id=%s''', (lecture['id'],)).fetchone()
    if not run or hashlib.sha256(run['request_body'].encode()).hexdigest() != run['request_sha256']:
        return None, None
    try:
        payload = json.loads(run['request_body'])
        messages = payload['messages']
        if not isinstance(payload, dict) or not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            return None, None
        fixed_messages = [m for m in messages if m.get('role') != 'user']
        if not fixed_messages:
            return None, None
        prompt = [run['prompt_version'], run['input_version'], fixed_messages]
        return prompt, {k: v for k, v in payload.items() if k != 'messages'}
    except (ValueError, KeyError, TypeError):
        return None, None


def version_data(conn, lecture, number):
    version = version_row(conn, lecture, number)
    return {**identity(lecture), 'revision_number': number, 'status': version['status'],
            'recorder': version.get('recorder'), 'reason': version.get('reason'),
            'version_created_at': version.get('created_at', lecture['created_at']).isoformat(),
            'content': version['content'],
            'profile': lecture['context'].get('student', {}).get('profile'),
            'states': lecture['context'].get('states', []),
            'materials': lecture['context'].get('materials')}


def compare_lectures(conn, lecture_id, other_id, left_revision=0, right_revision=0):
    for value, maximum in ((lecture_id, 2**63-1), (other_id, 2**63-1),
                           (left_revision, 2**31-1), (right_revision, 2**31-1)):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ReviewError('请选择有效的讲义与版本。')
    left, right = lecture_row(conn, lecture_id), lecture_row(conn, other_id)
    if not same_task(left, right):
        raise ReviewError('两份讲义的实际教学目标、课程任务或知识范围不同，不能作为同目标比较。')
    a, b = version_data(conn, left, left_revision), version_data(conn, right, right_revision)
    prompt_a, params_a = recorded_settings(conn, left)
    prompt_b, params_b = recorded_settings(conn, right)
    controls = []
    for code, label, before, after in (
        ('scope', '知识范围', knowledge_scope(left['context']), knowledge_scope(right['context'])),
        ('materials', '课程材料快照', a['materials'], b['materials']),
        ('model', '请求模型', a['model'], b['model']),
        ('prompt', '提示版本与实际系统消息', prompt_a, prompt_b),
        ('parameters', '实际生成参数', params_a, params_b),
        ('examples', '固定例题', left['context'].get('reference_examples'), right['context'].get('reference_examples')),
    ):
        status = 'unavailable' if before is None or after is None else 'same' if before == after else 'different'
        controls.append({'code': code, 'label': label, 'status': status})
    sections, order_changed = align_sections(a['content'], b['content'])
    kind = 'revision' if lecture_id == other_id and left_revision != right_revision else 'between_students' if left['student_id'] != right['student_id'] else 'repeated'
    return {'left': a, 'right': b, 'goal': left['goal'], 'topic': left['topic'],
            'kind': kind, 'controls': controls, 'sections': sections,
            'section_order_changed': order_changed,
            'summary': {'section_count': len(sections),
                        'different_sections': sum(s['status'] != 'same' for s in sections),
                        'added_lines': sum(s['changes']['added_lines'] for s in sections),
                        'removed_lines': sum(s['changes']['removed_lines'] for s in sections)}}
