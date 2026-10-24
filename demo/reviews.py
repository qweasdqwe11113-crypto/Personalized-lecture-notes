"""Teacher review audit. All writes lock a lecture and check its event revision."""
import difflib
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb
from quality import REVIEW_ITEMS, check_output

CATEGORIES = {'math','terminology','citation_support','scope','structure','other'}
SUPPORT = {'unreviewed','supported','unsupported','unclear'}
STATUS_LABELS = {'draft':'草稿', 'needs_revision':'待修订', 'reviewed':'教师已审核'}


class ReviewError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text_field(data, name, maximum):
    value = data.get(name)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ReviewError(f'{name} 内容缺失或超出长度限制。')
    return value.strip()


def lecture_row(conn, lecture_id, lock=False):
    row = conn.execute('SELECT * FROM lectures WHERE id=%s' + (' FOR UPDATE' if lock else ''),
                       (lecture_id,)).fetchone()
    if not row:
        raise ReviewError('讲义不存在。', 404)
    return row


def version_row(conn, lecture, number):
    if type(number) is not int or number < 0:
        raise ReviewError('请选择有效的原稿或修订版本。')
    if number == 0:
        return {'revision_number':0, 'content':lecture['content'], 'status':'unreviewed'}
    row = conn.execute('SELECT * FROM lecture_revisions WHERE lecture_id=%s AND revision_number=%s',
                       (lecture['id'], number)).fetchone()
    if not row:
        raise ReviewError('修订版本不存在。', 404)
    return row


def check_event(lecture, data):
    if not isinstance(data, dict):
        raise ReviewError('请求格式必须是 JSON 对象。')
    if type(data.get('expected_revision')) is not int:
        raise ReviewError('缺少审核记录版本，请重新读取。')
    if lecture['review_revision'] != data['expected_revision']:
        raise ReviewError('审核或问题记录已更新。本次未保存，请保留输入并重新读取后确认。', 409)
    if data.get('confirmed') is not True:
        raise ReviewError('请确认本次问题或修订记录。')


def locate(content, excerpt):
    offset = content.find(excerpt)
    if offset < 0:
        raise ReviewError('问题原文未出现在所选的已保存版本中，请复制该版本的准确片段。')
    if content.find(excerpt, offset + 1) >= 0:
        raise ReviewError('问题片段出现多次，请包含更多上下文以准确定位。')
    start = content.count('\n', 0, offset) + 1
    return start, start + excerpt.count('\n')


def insert_issue(conn, lecture, version, data, source='teacher', external_key=None):
    category = data.get('category')
    if not isinstance(category, str) or category not in CATEGORIES:
        raise ReviewError('请选择有效的问题类型。')
    excerpt = text_field(data, 'excerpt', 1200)
    detail = text_field(data, 'detail', 1500)
    recorder = text_field(data, 'recorder', 80)
    start, end = locate(version['content'], excerpt)
    material_id, support = data.get('material_id'), data.get('support', 'not_applicable')
    if category == 'citation_support':
        available = {m['id'] for m in lecture['context']['materials']}
        if type(material_id) is not int or material_id not in available:
            raise ReviewError('请选择这份讲义材料快照中的来源编号。')
        if not isinstance(support, str) or support not in SUPPORT:
            raise ReviewError('请选择引用支持判断。')
    elif material_id is not None or support != 'not_applicable':
        raise ReviewError('非引用问题不填写材料编号或引用支持判断。')
    result = conn.execute('''INSERT INTO lecture_issues
        (lecture_id,target_revision,category,excerpt,start_line,end_line,content_sha256,
         detail,material_id,support,source,recorder,external_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (lecture['id'], version['revision_number'], category, excerpt, start, end,
         hashlib.sha256(version['content'].encode()).hexdigest(), detail, material_id,
         support, source, recorder, external_key)).fetchone()
    conn.execute('UPDATE lectures SET review_revision=review_revision+1 WHERE id=%s', (lecture['id'],))
    return result['id']


def add_issue(conn, lecture_id, data):
    lecture = lecture_row(conn, lecture_id, True)
    check_event(lecture, data)
    version = version_row(conn, lecture, data.get('target_revision'))
    return insert_issue(conn, lecture, version, data)


def retain_feedback_issues(conn):
    """Only import feedback if its exact excerpt matches that original lecture."""
    catalog = json.loads(Path(__file__).with_name('feedback_issues.json').read_text(encoding='utf-8'))
    for issue in catalog:
        lecture = conn.execute('SELECT * FROM lectures WHERE id=%s FOR UPDATE', (issue['lecture_id'],)).fetchone()
        if (not lecture or issue['excerpt'] not in lecture['content'] or
                hashlib.sha256(lecture['content'].encode()).hexdigest() != issue['content_sha256']):
            continue
        if conn.execute('SELECT id FROM lecture_issues WHERE external_key=%s', (issue['key'],)).fetchone():
            continue
        data = {**issue, 'recorder':'回复稿核对（原反馈作者与时间未单独记录）'}
        insert_issue(conn, lecture, version_row(conn, lecture, 0), data, 'feedback', issue['key'])


def change_report(before, after):
    old, new = before.splitlines(), after.splitlines()
    changes = []
    for tag, i, j, a, b in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != 'equal':
            changes.append({'operation':tag, 'before_start':i+1, 'after_start':a+1,
                            'before':old[i:j], 'after':new[a:b]})
    return {'changed_blocks':changes, 'removed_lines':sum(len(c['before']) for c in changes),
            'added_lines':sum(len(c['after']) for c in changes)}


def save_revision(conn, lecture_id, data, *, preserve_whitespace=False):
    lecture = lecture_row(conn, lecture_id, True)
    check_event(lecture, data)
    base = version_row(conn, lecture, data.get('base_revision'))
    content = text_field(data, 'content', 16000)
    if preserve_whitespace:
        content = data['content']
    recorder, reason = text_field(data, 'recorder', 80), text_field(data, 'reason', 1500)
    status, seconds = data.get('status'), data.get('review_seconds')
    if not isinstance(status, str) or status not in STATUS_LABELS:
        raise ReviewError('请选择有效的审核状态。')
    if type(seconds) is not int or not 0 <= seconds <= 86400:
        raise ReviewError('实际审核/修改用时请填写 0—86400 的整数秒。')
    checklist = data.get('checklist')
    if not isinstance(checklist, dict) or set(checklist) != set(REVIEW_ITEMS) or any(type(v) is not bool for v in checklist.values()):
        raise ReviewError('请核对完整的教师检查表。')
    issues = conn.execute('SELECT * FROM lecture_issues WHERE lecture_id=%s ORDER BY id', (lecture_id,)).fetchall()
    issue_ids = {r['id'] for r in issues}
    resolutions = data.get('resolutions')
    if not isinstance(resolutions, list) or len(resolutions) > len(issues):
        raise ReviewError('问题处理记录格式错误。')
    seen = set()
    for item in resolutions:
        if not isinstance(item, dict) or type(item.get('issue_id')) is not int or item['issue_id'] not in issue_ids or item['issue_id'] in seen:
            raise ReviewError('问题处理引用了重复或不属于本讲义的记录。')
        item['note'] = text_field(item, 'note', 800)
        seen.add(item['issue_id'])
    resolutions = [{'issue_id':item['issue_id'], 'note':item['note']} for item in resolutions]
    if status == 'reviewed' and (not all(checklist.values()) or issue_ids - seen):
        raise ReviewError('标记教师已审核前，须确认全部检查项，并说明每个已记录问题如何处理。')
    report = check_output(content, lecture['context'], lecture['goal'])
    if status == 'reviewed' and any(c['invalid_ids'] for c in report['citation_locations']):
        raise ReviewError('正文仍含不存在的材料编号，不能标记教师已审核。')
    # Incremental version number is serialized by the lecture row lock.
    number = conn.execute('SELECT COALESCE(max(revision_number),0)+1 AS n FROM lecture_revisions WHERE lecture_id=%s', (lecture_id,)).fetchone()['n']
    row = conn.execute('''INSERT INTO lecture_revisions
        (lecture_id,revision_number,base_revision,content,status,recorder,reason,review_seconds,
         checklist,resolutions,changes,quality_report)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,revision_number''',
        (lecture_id, number, base['revision_number'], content, status, recorder, reason, seconds,
         Jsonb(checklist), Jsonb(resolutions), Jsonb(change_report(base['content'], content)),
         Jsonb(report))).fetchone()
    conn.execute('UPDATE lectures SET review_revision=review_revision+1 WHERE id=%s', (lecture_id,))
    return row


def review_bundle(conn, lecture_id):
    lecture = lecture_row(conn, lecture_id)
    revisions = conn.execute('SELECT * FROM lecture_revisions WHERE lecture_id=%s ORDER BY revision_number DESC', (lecture_id,)).fetchall()
    issues = conn.execute('SELECT * FROM lecture_issues WHERE lecture_id=%s ORDER BY id', (lecture_id,)).fetchall()
    latest = revisions[0] if revisions else None
    resolved = {r['issue_id'] for r in latest['resolutions']} if latest else set()
    for issue in issues:
        issue['latest_resolution'] = next((r['note'] for r in latest['resolutions'] if r['issue_id'] == issue['id']), None) if latest else None
    return {'lecture_id':lecture_id, 'expected_revision':lecture['review_revision'],
            'original':version_row(conn, lecture, 0), 'revisions':revisions, 'issues':issues,
            'review_items':REVIEW_ITEMS, 'pending_issue_ids':[i['id'] for i in issues if i['id'] not in resolved]}
