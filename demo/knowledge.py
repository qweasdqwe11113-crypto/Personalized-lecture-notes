"""Course knowledge library and knowledge-point recognition of uploaded material.

A course owns a library of knowledge points shared by its lessons, so a diagnosis and the
source segments recorded for a point serve every lesson that ticks it. Recognition of an
upload runs in two passes: rules split it into units (PDF pages, or heading-led blocks), drop
repeated page furniture and find numbered section headings; the model then proposes
fine-grained points, each tied to a unit range. The result is a draft: nothing enters the
library until the teacher edits and confirms it. Confirming files each kept point (reusing a
same-named one) and stores its unit range as material of that point.
"""
from collections import Counter, deque
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from threading import Condition, Thread

from flask import jsonify, request
from psycopg.types.json import Jsonb

from classes import LIBRARY
from prompts import PreparedRequest
from qwen import ModelError
from uploads import pdf_pages, read_upload

PROMPT = Path(__file__).with_name('prompts') / 'knowledge-recognition-v1.md'
PROMPT_VERSION, INPUT_VERSION = 'knowledge-recognition-v1', 'knowledge-units-v1'
UNIT_CHARS = 1600        # target size of a text block when the upload has no pages
PROMPT_CHARS = 60000     # unit text sent to the model, shared evenly across units
MATERIAL_CHARS = 12000   # one confirmed point's source segment
MAX_POINTS = 60
NUMBERED = re.compile(r'^\s*(\d{1,2}(?:\.\d{1,2}){1,3})\.?\s*([A-Za-z一-鿿][^\n]{0,78}?)\s*$')
CHINESE = re.compile(r'^\s*(第[一二三四五六七八九十百零\d]{1,4}[章节])\s*([^\s\d][^\n]{0,40}?)\s*$')
MARKDOWN = re.compile(r'^\s*(#{1,4})\s+(\S[^\n]{0,78}?)\s*#*\s*$')
LISTED = re.compile(r'^\s*[•·\-\*]?\s*\d{1,2}(?:\.\d{1,2}){1,3}\.?\s+[A-Za-z一-鿿]')
FAILED = '识别未完成，请检查本地模型服务后重试。'


class KnowledgeError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def heading_of(line):
    """(number, title, depth, marked) for a heading line; depth 0 is a chapter. Markdown headings are
    marked: the author set them, so a number in the title is kept and never taken for a stray value."""
    if m := NUMBERED.match(line):
        return m[1], m[2], m[1].count('.'), False
    if m := CHINESE.match(line):
        return m[1], m[2], 0 if m[1].endswith('章') else 1, False
    if m := MARKDOWN.match(line):
        if (inner := heading_of(m[2])) and inner[0]:
            return inner[0], inner[1], inner[2], True
        return '', m[2], len(m[1]) - 1, True
    return None


def blocks(text):
    """Heading-led blocks of about UNIT_CHARS characters, for uploads without pages."""
    result, current, size = [], [], 0
    for line in text.split('\n'):
        starts = heading_of(line) is not None and size >= 200
        full = size >= UNIT_CHARS and (not line.strip() or size >= 2 * UNIT_CHARS)
        if current and (starts or full):
            result.append('\n'.join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    result.append('\n'.join(current))
    return [(f'第 {i} 段', block.strip()) for i, block in enumerate((b for b in result if b.strip()), 1)]


def units_of(record):
    """[(label, text)]: the pages of a PDF, otherwise heading-led blocks of the stored text."""
    if (record.get('filename') or '').lower().endswith('.pdf') and record.get('raw'):
        try:
            from pypdf import PdfReader
            pages = pdf_pages(PdfReader(BytesIO(record['raw'])))
            return [(f'第 {i} 页', page.strip()) for i, page in enumerate(pages, 1)]
        except Exception:
            pass
    return blocks(record.get('content') or '')


def norm(line):
    return re.sub(r'\s+', ' ', re.sub(r'\d+', '#', line)).strip()


def strip_furniture(units):
    """Drop short lines repeated on many pages (running heads, page footers); keep headings."""
    if len(units) < 5:
        return units
    seen = Counter()
    for _, text in units:
        seen.update({norm(line) for line in text.split('\n') if line.strip() and not heading_of(line)})
    limit = max(3, len(units) * 0.3)
    furniture = {key for key, count in seen.items() if count >= limit and len(key) <= 60}
    return [(label, '\n'.join(line for line in text.split('\n')
                              if heading_of(line) or norm(line) not in furniture).strip()) for label, text in units]


def structure(units):
    """Sections found from numbered headings, the chapter title, and which units are contents pages."""
    found, toc = [], set()
    for n, (_, text) in enumerate(units, 1):
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        listed = sum(bool(LISTED.match(line)) for line in lines)
        if listed >= 3 and listed * 2 >= len(lines):
            toc.add(n)
            found.append([])
            continue
        found.append([h for h in map(heading_of, lines) if h])
    # A numbered "heading" whose leading number appears only once is more likely a stray value.
    leads = Counter(h[0].split('.')[0] for heads in found for h in heads if '.' in h[0])
    chapter, sections, current = '', [], None
    for n, heads in enumerate(found, 1):
        heads = [h for h in heads if h[3] or '.' not in h[0] or leads[h[0].split('.')[0]] >= 2 or len(leads) == 1]
        for h in heads:
            if h[2] == 0 and not chapter:
                chapter = (h[0] + ' ' + h[1]).strip()
        inner = [h for h in heads if h[2] > 0]
        if inner:
            # Every heading at the finest level opens (or returns to) a section: a slide page usually
            # has one, a block of a short text file can hold several.
            deepest = max(h[2] for h in inner)
            for number, title, _, _ in (h for h in inner if h[2] == deepest):
                current = next((s for s in sections if s['number'] == number and number), None)
                if current is None:
                    current = {'number': number, 'title': title, 'units': [n, n]}
                    sections.append(current)
                current['units'] = [min(current['units'][0], n), max(current['units'][1], n)]
        if current and n not in toc:
            current['units'] = [min(current['units'][0], n), max(current['units'][1], n)]
    return chapter, sections, sorted(toc)


def rule_items(chapter, sections):
    return [dict(name=s['title'][:40], chapter=chapter, section=s['number'], units=list(s['units']),
                 summary='', source='rules') for s in sections][:MAX_POINTS]


def prepare_recognition(course, upload, units, sections, toc, model):
    per = max(300, PROMPT_CHARS // max(1, len(units)))
    data = {'course': course, 'upload': upload, 'sections': sections,
            'units': [{'n': n, 'label': label, **({'contents_page': True} if n in toc else {}),
                       'text': text[:200 if n in toc else per]} for n, (label, text) in enumerate(units, 1)]}
    payload = dict(model=model, temperature=0.2, max_tokens=8000, chat_template_kwargs={'enable_thinking': False},
                   response_format={'type': 'json_object'}, messages=[
                       {'role': 'system', 'content': PROMPT.read_text(encoding='utf-8')},
                       {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}])
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return PreparedRequest(body, sha256(body.encode()).hexdigest(), PROMPT_VERSION, INPUT_VERSION)


def parse_points(content, count):
    """(chapter, items) from the model's JSON; unusable points are dropped, same names merged."""
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', (content or '').strip())
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        try:
            data = json.loads(text[start:end + 1]) if 0 <= start < end else None
        except ValueError:
            data = None
    if not isinstance(data, dict):
        raise ModelError('invalid_knowledge', '模型返回的知识点不是有效 JSON。')
    chapter = str(data.get('chapter') or '').strip()[:100]
    items, seen = [], {}
    for point in data.get('points') or []:
        if not isinstance(point, dict):
            continue
        name, span = str(point.get('name') or '').strip()[:40], point.get('units')
        if type(span) is int:
            span = [span, span]
        if not name or not isinstance(span, list) or len(span) != 2 or any(type(x) is not int for x in span):
            continue
        first, last = max(1, min(span)), min(count, max(span))
        if first > last:
            continue
        if name in seen:
            seen[name]['units'] = [min(seen[name]['units'][0], first), max(seen[name]['units'][1], last)]
            continue
        seen[name] = dict(name=name, chapter=str(point.get('chapter') or chapter).strip()[:100],
                          section=str(point.get('section') or '').strip()[:20], units=[first, last],
                          summary=str(point.get('summary') or '').strip()[:200], source='model')
        items.append(seen[name])
    if not items:
        raise ModelError('invalid_knowledge', '模型没有给出可用的知识点。')
    return chapter, items[:MAX_POINTS]


def numbered(items):
    return [{**item, 'id': f'k{i}', 'keep': True} for i, item in enumerate(items, 1)]


class KnowledgeQueue:
    """One background worker; recognition shares the model gate with every other model call."""

    def __init__(self, db, root, call_model, gate, model):
        self.db, self.root, self.call_model, self.gate, self.model = db, root, call_model, gate, model
        self.condition, self.pending, self.active, self.thread = Condition(), deque(), None, None

    def recover(self):
        with self.db() as conn:
            conn.execute('''UPDATE knowledge_drafts SET status='failed',updated_at=now(),
                error='本地服务已重启，识别未完成，可重新识别。' WHERE status IN ('queued','running')''')

    def submit(self, upload_id):
        with self.condition:
            if upload_id != self.active and upload_id not in self.pending:
                self.pending.append(upload_id)
            if self.thread is None:
                self.thread = Thread(target=self._work, daemon=True, name='knowledge-worker')
                self.thread.start()
            self.condition.notify_all()

    def wait_idle(self, timeout=5):
        with self.condition:
            return self.condition.wait_for(lambda: self.active is None and not self.pending, timeout)

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending)
                upload_id = self.active = self.pending.popleft()
            try:
                self.run(upload_id)
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ModelError, KnowledgeError)) else FAILED
                try:
                    with self.db() as conn:
                        conn.execute('''UPDATE knowledge_drafts SET status='failed',error=%s,updated_at=now()
                            WHERE upload_id=%s''', (message, upload_id))
                except Exception:
                    pass
            finally:
                with self.condition:
                    self.active = None
                    self.condition.notify_all()

    def run(self, upload_id):
        record = read_upload(self.root, upload_id, raw=True)
        if record is None:
            raise KnowledgeError('资料已不存在。', 404)
        with self.db() as conn:
            draft = conn.execute('''UPDATE knowledge_drafts SET status='running',updated_at=now()
                WHERE upload_id=%s RETURNING course_id''', (upload_id,)).fetchone()
            course = conn.execute('SELECT name,stage FROM courses WHERE id=%s', (draft['course_id'],)).fetchone()
        units = strip_furniture(units_of(record))
        if not any(text for _, text in units):
            raise KnowledgeError('这份资料没有可识别的正文。')
        chapter, sections, toc = structure(units)
        prepared = prepare_recognition(dict(course), record['title'], units, sections, toc, self.model())
        method, notice = 'rules+model', ''
        try:
            with self.gate:
                output = self.call_model(prepared, lambda: None)
            named, items = parse_points(output.content, len(units))
            chapter = named or chapter
        except ModelError as exc:
            method, items = 'rules', rule_items(chapter or record['title'], sections)
            notice = f'模型识别未完成（{exc}），以下草稿只按资料中的编号标题给出，请补充或改名。'
            if not items:
                notice = f'模型识别未完成（{exc}），资料中也没有找到编号标题，请手动添加知识点。'
        stored = [{'n': n, 'label': label, 'text': text} for n, (label, text) in enumerate(units, 1)]
        with self.db() as conn:
            conn.execute('''UPDATE knowledge_drafts SET status='ready',method=%s,units=%s,items=%s,notice=%s,error='',
                model=%s,prompt_version=%s,input_sha256=%s,updated_at=now() WHERE upload_id=%s''',
                         (method, Jsonb(stored), Jsonb(numbered(items)), notice, self.model(),
                          prepared.prompt_version, prepared.sha256, upload_id))


def label_range(units, first, last):
    a, b = units[first - 1]['label'], units[last - 1]['label']
    if first == last:
        return a
    unit = '页' if a.endswith('页') else '段'
    return f'第 {first}—{last} {unit}'


def segment(units, first, last):
    text = '\n\n'.join(f"【{u['label']}】\n{u['text']}" for u in units[first - 1:last] if u['text'].strip())
    return text[:MATERIAL_CHARS]


def text_value(data, key, label, maximum, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise KnowledgeError(f'{label}请填写{"1—" if required else "不超过 "}{maximum} 个字。')
    return value.strip()


def confirmed_items(data, count):
    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_POINTS:
        raise KnowledgeError(f'请保留 1—{MAX_POINTS} 个知识点后再确认。')
    result, names = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise KnowledgeError('知识点格式无效。')
        name = text_value(item, 'name', '知识点名称', 40, True)
        if name in names:
            raise KnowledgeError(f'知识点「{name}」重复了，请合并或改名。')
        names.add(name)
        span = item.get('units')
        if (not isinstance(span, list) or len(span) != 2 or any(type(x) is not int for x in span)
                or not 1 <= span[0] <= span[1] <= count):
            raise KnowledgeError(f'知识点「{name}」的资料范围无效。')
        result.append(dict(name=name, chapter=text_value(item, 'chapter', '章节', 100),
                           section=text_value(item, 'section', '小节编号', 20),
                           summary=text_value(item, 'summary', '概括', 200), units=list(span)))
    return result


DIGITS = {c: i for i, c in enumerate('零一二三四五六七八九', 0)}
NUMBERED_CHAPTER = re.compile(r'第\s*([0-9]{1,3}|[零一二三四五六七八九十百]{1,4})\s*[章编部分单元]')


def chapter_rank(chapter):
    """Numbered chapters by their number; other named chapters before them; no chapter last."""
    if not chapter.strip():
        return 10 ** 6
    m = NUMBERED_CHAPTER.search(chapter)
    if not m:
        return 0
    text = m[1]
    if text.isdigit():
        return int(text)
    if '百' in text:
        return 100
    if '十' in text:
        tens, _, ones = text.partition('十')
        return DIGITS.get(tens, 1) * 10 + DIGITS.get(ones, 0)
    return DIGITS.get(text, 0)


def renumber(conn, course_id):
    """Keep the library in chapter order; within a chapter, points keep their recognized order."""
    rows = conn.execute('SELECT id,chapter,position FROM knowledge_points WHERE course_id=%s', (course_id,)).fetchall()
    rows.sort(key=lambda r: (chapter_rank(r['chapter']), r['position'], r['id']))
    for position, row in enumerate(rows, 1):
        if row['position'] != position:
            conn.execute('UPDATE knowledge_points SET position=%s WHERE id=%s', (position, row['id']))


def shelf(conn, course_id):
    """{name: id} of the course library, preferring a visible point over an archived one."""
    found = {}
    for row in conn.execute(f'''SELECT k.id,k.name FROM knowledge_points k WHERE k.course_id=%s
            AND NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id)
            ORDER BY k.archived DESC,k.id DESC''', (course_id,)):
        found[row['name']] = row['id']
    return found


def confirm(conn, upload_id, data, record):
    draft = conn.execute('SELECT * FROM knowledge_drafts WHERE upload_id=%s FOR UPDATE', (upload_id,)).fetchone()
    if not draft:
        raise KnowledgeError('这份资料还没有识别知识点。', 404)
    if draft['status'] not in ('ready', 'confirmed'):
        raise KnowledgeError('识别尚未完成，请稍后再确认。', 409)
    if data.get('version') != draft['updated_at'].isoformat():
        raise KnowledgeError('识别结果已更新，请重新打开后再确认。', 409)
    if record.get('course_id') is None:
        raise KnowledgeError('请先把这份资料归入课程，再确认知识点。')
    course_id, class_id = record['course_id'], record.get('class_id')
    units = draft['units']
    items = confirmed_items(data, len(units))
    conn.execute('LOCK TABLE knowledge_points IN EXCLUSIVE MODE')
    conn.execute('LOCK TABLE materials IN EXCLUSIVE MODE')
    names = shelf(conn, course_id)
    position = conn.execute('SELECT COALESCE(max(position),0) AS p FROM knowledge_points WHERE course_id=%s',
                            (course_id,)).fetchone()['p']
    prefix = 'p' if units and units[0]['label'].endswith('页') else 's'
    created = reused = 0
    for item in items:
        kid = names.get(item['name'])
        if kid is None:
            position += 1
            kid = conn.execute('''INSERT INTO knowledge_points(id,name,course_id,chapter,position,summary)
                VALUES ((SELECT COALESCE(max(id),0)+1 FROM knowledge_points),%s,%s,%s,%s,%s) RETURNING id''',
                               (item['name'], course_id, item['chapter'], position, item['summary'])).fetchone()['id']
            names[item['name']] = kid
            created += 1
        else:
            conn.execute('''UPDATE knowledge_points SET archived=false,
                chapter=CASE WHEN chapter='' THEN %s ELSE chapter END,
                summary=CASE WHEN summary='' THEN %s ELSE summary END WHERE id=%s''',
                         (item['chapter'], item['summary'], kid))
            reused += 1
        item['knowledge_id'] = kid
        first, last = item['units']
        source = f'upload:{upload_id}#{prefix}{first}-{last}'
        section = ' · '.join(filter(None, [label_range(units, first, last), item['section'], item['chapter']]))
        content = segment(units, first, last)
        row = conn.execute('SELECT id FROM materials WHERE knowledge_id=%s AND source_url=%s', (kid, source)).fetchone()
        if row:
            conn.execute('UPDATE materials SET content=%s,section=%s,class_id=%s WHERE id=%s',
                         (content, section, class_id, row['id']))
        else:
            conn.execute('''INSERT INTO materials(id,knowledge_id,title,content,source_title,source_url,section,class_id)
                VALUES ((SELECT COALESCE(max(id),0)+1 FROM materials),%s,%s,%s,%s,%s,%s,%s)''',
                         (kid, f"{item['name']} · {record['title']}"[:200], content, record['filename'] or record['title'],
                          source, section, class_id))
    renumber(conn, course_id)
    conn.execute('''UPDATE knowledge_drafts SET status='confirmed',items=%s,course_id=%s,class_id=%s,
        confirmed_at=now(),updated_at=now() WHERE upload_id=%s''',
                 (Jsonb(numbered(items)), course_id, class_id, upload_id))
    return {'created': created, 'reused': reused, 'items': items}


def library(conn, course_id, topics):
    """Every point of the course library (archived ones flagged), with sources, lessons and diagnoses."""
    points = conn.execute('''SELECT k.id,k.name,k.chapter,k.position,k.summary,k.archived FROM knowledge_points k
        WHERE k.course_id=%s AND NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id)
        ORDER BY k.position,k.id''', (course_id,)).fetchall()
    ids = [p['id'] for p in points]
    sources, diagnosed = {}, {}
    for row in conn.execute('''SELECT id,knowledge_id,title,source_title,source_url,section,class_id FROM materials
            WHERE knowledge_id=ANY(%s) ORDER BY id''', (ids,)):
        sources.setdefault(row.pop('knowledge_id'), []).append(row)
    for row in conn.execute('''SELECT knowledge_id,count(*) AS n FROM student_knowledge
            WHERE knowledge_id=ANY(%s) AND revision>0 GROUP BY knowledge_id''', (ids,)):
        diagnosed[row['knowledge_id']] = row['n']
    lessons = {}
    for key, topic in topics.items():
        if topic.get('course_id') == course_id:
            for kid in topic['knowledge_ids']:
                lessons.setdefault(kid, []).append({'key': key, 'title': topic.get('title') or '未命名备课'})
    return [{**p, 'sources': sources.get(p['id'], []), 'lessons': lessons.get(p['id'], []),
             'diagnosed': diagnosed.get(p['id'], 0)} for p in points]


def drafts(conn, course_id):
    return conn.execute('''SELECT upload_id,status,method,notice,error,jsonb_array_length(items) AS count,
        updated_at,confirmed_at FROM knowledge_drafts WHERE course_id=%s ORDER BY created_at,upload_id''',
                        (course_id,)).fetchall()


def rename_in_lessons(conn, kid, name):
    """Lessons copy their point names; keep those copies in step and bump the lesson revision."""
    for row in conn.execute("SELECT key,payload FROM course_tasks WHERE payload->'knowledge_ids' @> %s::jsonb",
                            (json.dumps([kid]),)).fetchall():
        payload = row['payload']
        names = [name if k == kid else n for k, n in zip(payload['knowledge_ids'], payload['knowledge_names'])]
        conn.execute('''UPDATE course_tasks SET payload=payload||jsonb_build_object('knowledge_names',%s::jsonb),
            revision=revision+1 WHERE key=%s''', (json.dumps(names, ensure_ascii=False), row['key']))


def register_knowledge(app, db, root, queue, topics):
    @app.errorhandler(KnowledgeError)
    def knowledge_error(exc):
        return jsonify(error=str(exc)), exc.status

    def course_or_404(conn, course_id):
        if not conn.execute('SELECT 1 FROM courses WHERE id=%s', (course_id,)).fetchone():
            raise KnowledgeError('课程不存在，请刷新后重试。', 404)

    @app.get('/api/courses/<int:course_id>/knowledge')
    def course_knowledge(course_id):
        catalog = topics()
        with db() as conn:
            course_or_404(conn, course_id)
            points = library(conn, course_id, catalog)
            found = drafts(conn, course_id)
        for draft in found:
            draft['title'] = (read_upload(root, draft['upload_id']) or {}).get('title') or '已删除的资料'
        return jsonify(points=points, drafts=found)

    @app.post('/api/courses/<int:course_id>/knowledge')
    def add_point(course_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise KnowledgeError('请填写知识点。')
        name = text_value(data, 'name', '知识点名称', 40, True)
        chapter, summary = text_value(data, 'chapter', '章节', 100), text_value(data, 'summary', '概括', 200)
        with db() as conn:
            course_or_404(conn, course_id)
            conn.execute('LOCK TABLE knowledge_points IN EXCLUSIVE MODE')
            existing = conn.execute(f'SELECT k.id {LIBRARY} AND k.name=%s', (course_id, name)).fetchone()
            if existing:
                raise KnowledgeError('知识点库中已有同名知识点。', 409)
            row = conn.execute('''INSERT INTO knowledge_points(id,name,course_id,chapter,position,summary)
                VALUES ((SELECT COALESCE(max(id),0)+1 FROM knowledge_points),%s,%s,%s,
                        (SELECT COALESCE(max(position),0)+1 FROM knowledge_points WHERE course_id=%s),%s)
                RETURNING id,name,chapter,position,summary,archived''',
                               (name, course_id, chapter, course_id, summary)).fetchone()
            renumber(conn, course_id)
        return jsonify(point={**row, 'sources': [], 'lessons': [], 'diagnosed': 0}), 201

    @app.put('/api/knowledge/<int:kid>')
    def edit_point(kid):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise KnowledgeError('请提交知识点修改。')
        with db() as conn:
            conn.execute('LOCK TABLE knowledge_points IN EXCLUSIVE MODE')
            point = conn.execute('''SELECT k.* FROM knowledge_points k WHERE k.id=%s AND k.course_id IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM knowledge_aliases a WHERE a.knowledge_id=k.id)''', (kid,)).fetchone()
            if not point:
                raise KnowledgeError('知识点不在任何课程的知识点库中。', 404)
            name = text_value(data, 'name', '知识点名称', 40, True) if 'name' in data else point['name']
            chapter = text_value(data, 'chapter', '章节', 100) if 'chapter' in data else point['chapter']
            summary = text_value(data, 'summary', '概括', 200) if 'summary' in data else point['summary']
            archived = data.get('archived', point['archived'])
            if type(archived) is not bool:
                raise KnowledgeError('归档状态无效。')
            if not archived and conn.execute(f'SELECT 1 {LIBRARY} AND k.name=%s AND k.id<>%s',
                                             (point['course_id'], name, kid)).fetchone():
                raise KnowledgeError('知识点库中已有同名知识点。', 409)
            conn.execute('UPDATE knowledge_points SET name=%s,chapter=%s,summary=%s,archived=%s WHERE id=%s',
                         (name, chapter, summary, archived, kid))
            if name != point['name']:
                rename_in_lessons(conn, kid, name)
            if chapter != point['chapter']:
                renumber(conn, point['course_id'])
        return jsonify(point={'id': kid, 'name': name, 'chapter': chapter, 'summary': summary, 'archived': archived})

    @app.post('/api/knowledge/recognize')
    def recognize():
        data = request.get_json(silent=True)
        upload_id = data.get('upload_id') if isinstance(data, dict) else None
        if not isinstance(upload_id, str) or not re.fullmatch(r'[0-9a-f]{32}', upload_id):
            raise KnowledgeError('请选择要识别的资料。')
        record = read_upload(root, upload_id)
        if record is None:
            raise KnowledgeError('资料不存在。', 404)
        if record.get('course_id') is None:
            raise KnowledgeError('请先把这份资料归入课程，再识别知识点。')
        if not (record.get('content') or '').strip():
            raise KnowledgeError('这份资料没有可识别的正文；图片资料请通过图片识读加入讲义。')
        with db() as conn:
            current = conn.execute('SELECT status FROM knowledge_drafts WHERE upload_id=%s FOR UPDATE', (upload_id,)).fetchone()
            if current and current['status'] in ('queued', 'running'):
                raise KnowledgeError('这份资料正在识别，请稍候。', 409)
            conn.execute('''INSERT INTO knowledge_drafts(upload_id,course_id,class_id,status) VALUES (%s,%s,%s,'queued')
                ON CONFLICT (upload_id) DO UPDATE SET course_id=EXCLUDED.course_id,class_id=EXCLUDED.class_id,
                status='queued',error='',notice='',updated_at=now()''',
                         (upload_id, record['course_id'], record.get('class_id')))
        queue().submit(upload_id)
        return jsonify(upload_id=upload_id, status='queued'), 202

    @app.get('/api/knowledge/drafts/<upload_id>')
    def draft_detail(upload_id):
        with db() as conn:
            draft = conn.execute('SELECT * FROM knowledge_drafts WHERE upload_id=%s', (upload_id,)).fetchone()
            if not draft:
                raise KnowledgeError('这份资料还没有识别知识点。', 404)
            names = shelf(conn, draft['course_id'])
        record = read_upload(root, upload_id) or {}
        units = [{'n': u['n'], 'label': u['label'], 'preview': u['text'][:240], 'characters': len(u['text'])}
                 for u in draft.pop('units')]
        draft['version'] = draft['updated_at'].isoformat()
        return jsonify(draft=draft, units=units, library=sorted(names), upload={
            'title': record.get('title'), 'filename': record.get('filename')})

    @app.post('/api/knowledge/drafts/<upload_id>/confirm')
    def confirm_draft(upload_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise KnowledgeError('请提交确认的知识点。')
        record = read_upload(root, upload_id)
        if record is None:
            raise KnowledgeError('资料不存在。', 404)
        with db() as conn:
            result = confirm(conn, upload_id, data, record)
        return jsonify(result)
