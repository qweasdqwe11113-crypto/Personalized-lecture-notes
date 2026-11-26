"""Retrieval over course material.

Five modes stay explicit, and every run records which one it used:
  sql      - the exact knowledge-point lookup: every material of the selected points, whole.
  vector   - chunks of the selected points only, ranked by embedding cosine.
  hybrid   - chunks of every material of the lesson's course (plus the class's own), ranked for
             up to three queries with their own purpose: 讲解依据 (the goal and the points),
             例题练习 (worked examples and exercises of the points) and 针对学情 (the learner's weak
             points and recent evidence). Each query ranks by BM25 and by embedding cosine, and
             the two orders are fused by reciprocal rank. The selected points are favoured, not
             required, and every selected point that has material keeps at least one chunk.
             Without an answering embedding service the ranking is BM25 alone, and the record
             says so.
  anchored - the lookup for the lesson's own points, retrieval for the rest: every material of the
             selected points, whole, as in sql; the 针对学情 query adds passages from elsewhere in
             the course (not from later chapters); and only when the lesson's material is longer
             than the budget are its passages ranked, so the ones that fit are the most relevant.
             Retrieval never decides which of the lesson's own material is left out while it fits.
  none     - no course material at all: the without-retrieval arm of a comparison.

suggest_points ranks the knowledge points of a course library for a teacher's free-text focus, so a
lesson can start without the teacher ticking points; the teacher confirms the suggestion.

Only the vector ranking of the vector mode is pluggable: ArrayBackend scores float8[] in
Python and PgVectorBackend takes over with '<=>' when the extension exists. Chunking is
deterministic, so the same text always yields the same chunks, a saved run can be
reproduced, and a chunk keeps a stable key (M<material>#<index>). A chunk keeps its parent
material's citation number, which leaves the meaning of [M编号] exactly as it was.
"""
from collections import Counter
import hashlib
import math
import re
import unicodedata

from embed import EmbeddingError

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
# A worked example, exercise, definition or theorem may run this long before it is cut, so a
# solution is not separated from its problem.
BLOCK_LIMIT = 900
MODES = ('sql', 'vector', 'hybrid', 'anchored', 'none')
MODE_LABELS = {'sql': '精确 SQL（所选知识点的全部资料）', 'vector': '向量检索（所选知识点内按目标选片段）',
               'hybrid': '整门课检索（关键词与语义融合）', 'anchored': '本课知识点的全部资料 + 整门课补充',
               'none': '不提供课程资料（对照）'}
HYBRID_VERSION = 'hybrid-v1'
ANCHORED_VERSION = 'anchored-v1'
ANCHOR_LABEL = '本课知识点'
SUGGEST_VERSION = 'point-suggest-v1'
# How a free-text focus becomes knowledge points: the course's best SUGGEST_DEPTH related chunks vote
# for their point (the best chunk's fused score plus half the second's, so one long material cannot
# outvote a short one by its length); a point is ticked when its vote reaches SUGGEST_SHARE of the
# best point's, at most SUGGEST_MAX of them, and SUGGEST_SHOWN points are listed for the teacher.
# Tuned on the evalset's dev lessons with text-embedding-v4 (scale/eval_point_suggest.py, 2026-11-26): of the
# rules that ticked every answer point, 15 and 0.6 ticked the fewest others (precision 0.70 against 0.45 for
# 30 and 0.5); a stricter 0.7 missed points, and a missed point's material is not given whole.
SUGGEST_DEPTH = 15
SUGGEST_SHARE = 0.6
SUGGEST_MAX = 6
SUGGEST_SHOWN = 12
GAP = len('\n……\n')     # what to_materials puts between two chunks of a material that are not adjacent
RRF_K = 60
# Being among the lesson's selected points weighs like a first place in one ranking; the kind a
# query asks for (an example for 例题练习) like half of one.
BOOST = 1 / (RRF_K + 1)   # one rank-1 vote of the fusion; the role table gives each role its share
PER_MATERIAL = 2   # chunks one material may give one query
ALTERNATIVES = 3   # runners-up per query the teacher can add on the page
# A chunk counts as related to a query only if its BM25 score reaches this share of the query's best,
# or its cosine similarity is within VECTOR_MARGIN of the query's best. Below both, a role takes fewer
# chunks instead of filling its limit with unrelated material (the fused rank alone has no floor).
LEXICAL_FLOOR = 0.3
VECTOR_MARGIN = 0.1
# BM25's share in the fusion. Measured on a real course (English slides, Chinese questions) and two
# Chinese chapters (docs/scale-data.md): full weight lowered the real course's MRR by 0.03, 0.5 by
# 0.006, while 0.5 raised the Chinese chapters' MRR by 0.006–0.02.
KEYWORD_WEIGHT = 0.5
# What a chunk's vector and BM25 entry are computed from: the material's title, then the chunk. The
# title is the teacher-confirmed name of the material and of its point, often the only Chinese words
# in front of English slides; with it the real course's MRR rose from 0.855 to 0.944 (embedding) and
# from 0.598 to 0.869 (BM25).
INDEX_FORMAT = 'title-text-v1'
# point: bonus for a chunk of a point the lesson selected; kind: bonus for a chunk of a preferred kind.
# The explanation leans on the lesson's own points; practice leans on worked examples and exercises,
# wherever in the course they are; the learner query follows the student's errors.
ROLES = {
    'explain': dict(label='讲解依据', limit=4, prefer=frozenset({'definition', 'theorem'}), point=1.0, kind=0.5),
    'practice': dict(label='例题练习', limit=3, prefer=frozenset({'example', 'exercise'}), point=0.5, kind=1.0),
    'learner': dict(label='针对学情', limit=2, prefer=frozenset({'example', 'exercise'}), point=0.0, kind=0.5),
}
TEACHER_LABEL = '教师保留'
# A chunk the teacher kept, as the page sends it: its key and the start of its content hash, so a
# chunk whose text changed after the teacher saw it is refused instead of silently replaced.
KEPT = re.compile(r'(M\d+#\d+)@([0-9a-f]{16})')


def kept_id(item):
    return f"{item['key']}@{item['content_sha256'][:16]}"


class RetrievalError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- chunking

_BULLET = re.compile(r'^[\s•·▪●◦\-*>]+')
_NUMBER = r'[\d一二三四五六七八九十]*'
_STARTS = (
    ('example', re.compile(rf'^(?:【?例(?:题)?\s*{_NUMBER}】?(?:[\s　:：.．、(（]|$)|example\b)', re.I)),
    ('exercise', re.compile(rf'^(?:【?(?:练习|习题|作业|思考题)\s*{_NUMBER}】?(?:[\s　:：.．、(（]|$)|exercises?\b|problem\s*\d)', re.I)),
    ('definition', re.compile(r'^(?:【?定义\s*[\d.]*】?(?:[\s　:：.．、(（]|$)|definition\b)', re.I)),
    ('theorem', re.compile(r'^(?:【?(?:定理|性质|推论|引理)\s*[\d.]*】?(?:[\s　:：.．、(（]|$)'
                           r'|(?:theorem|proposition|lemma|corollary|property)\b)', re.I)),
)
_DEFINES = re.compile(r'定义为|称为|叫做|记作|is defined as|is called|denoted by|we say that', re.I)
# Markdown headings, page markers, 第…章/节, Chapter n, and section numbers with at least two levels
# (3.2 Relationship …); a plain "1." list item is not a heading.
_HEADING = re.compile(r'^(?:#{1,6}\s|【第\s*\d+\s*页】|第[一二三四五六七八九十百\d]+[章节]|chapter\s+\d'
                      r'|\d+\.\d+(?:\.\d+){0,2}\s+[^\d\s=×+\-*/]|§)', re.I)


def _start_kind(line):
    line = _BULLET.sub('', line)
    return next((kind for kind, pattern in _STARTS if pattern.match(line)), None)


def _window(block, size, overlap):
    if len(block) <= size:
        return [block]
    pieces, start = [], 0
    while start < len(block):
        pieces.append(block[start:start + size])
        if start + size >= len(block):
            break
        start += max(size - overlap, 1)
    return pieces


def _pack_lines(lines, size, overlap):
    """Lines packed up to size; a single line longer than size is cut by a sliding window."""
    blocks, current = [], ''
    for line in lines:
        candidate = f'{current}\n{line}' if current else line
        if current and len(candidate) > size:
            blocks.append(current)
            current = line
        else:
            current = candidate
    if current:
        blocks.append(current)
    return [piece for block in blocks for piece in _window(block, size, overlap)]


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Structure first. A heading, a page marker, or the first line of an example, exercise,
    definition or theorem starts a new unit; units are packed up to size. Such a unit longer
    than size but within BLOCK_LIMIT stays one chunk, so a solution stays with its problem.
    Anything else that is still too long is packed line by line and cut by a sliding window."""
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    units = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not units or _HEADING.match(line) or _start_kind(line):
            units.append([line])
        else:
            units[-1].append(line)
    chunks, current = [], ''
    for lines in units:
        unit = '\n'.join(lines)
        whole = _start_kind(lines[0]) is not None and len(unit) <= max(size, BLOCK_LIMIT)
        if len(unit) > size and not whole:
            if current:
                chunks.append(current)
                current = ''
            chunks.extend(_pack_lines(lines, size, overlap))
            continue
        candidate = f'{current}\n{unit}' if current else unit
        if current and len(candidate) > size:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [piece for piece in chunks if piece.strip()]


def chunk_kind(text):
    """What a chunk holds, by the first words of its lines: example, exercise, definition, theorem
    or text, space-separated when several. A guess from wording, used only to favour a kind."""
    kinds = {kind for kind in map(_start_kind, text.splitlines()) if kind}
    if _DEFINES.search(text):
        kinds.add('definition')
    return ' '.join(sorted(kinds)) or 'text'


def describe(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Each chunk of text with its kind and the nearest heading or page marker above it."""
    text = (text or '').strip()
    pieces = chunk_text(text) if (size, overlap) == (CHUNK_SIZE, CHUNK_OVERLAP) else chunk_text(text, size, overlap)
    marks, offset = [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and _HEADING.match(stripped):
            marks.append((offset + line.index(stripped[0]), stripped[:80]))
        offset += len(line)
    result, cursor = [], 0
    for piece in pieces:
        first = piece.split('\n', 1)[0][:60]
        found = text.find(first, cursor)
        at = found if found >= 0 else cursor
        cursor = at
        heading = next((title for position, title in reversed(marks) if position <= at), '')
        result.append(dict(content=piece, kind=chunk_kind(piece), heading=heading))
    return result


# ---------------------------------------------------------------- ranking

_TERM = re.compile(r'[一-鿿]+|[a-z0-9]+(?:\.[0-9]+)?')


def terms(text):
    """Search terms: a run of Chinese characters gives its overlapping character pairs; Latin
    words and numbers stay whole. NFKC first, so the radical forms some PDFs carry (⽅) and
    full-width letters match their ordinary forms."""
    found = []
    for run in _TERM.findall(unicodedata.normalize('NFKC', text or '').lower()):
        if '一' <= run[0] <= '鿿':
            found.extend([run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)])
        elif len(run) > 1 or run.isdigit():
            found.append(run)
    return found


class BM25:
    """Okapi BM25 over terms(). Small enough to build per request: a course is a few hundred chunks."""

    def __init__(self, documents, k1=1.5, b=0.75):
        self.docs = [Counter(terms(document)) for document in documents]
        self.lengths = [sum(counts.values()) for counts in self.docs]
        self.average = (sum(self.lengths) / len(self.docs) if self.docs else 0) or 1
        self.k1, self.b = k1, b
        frequency = Counter()
        for counts in self.docs:
            frequency.update(counts.keys())
        total = len(self.docs)
        self.idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in frequency.items()}

    def scores(self, query):
        wanted = set(terms(query))
        result = []
        for counts, length in zip(self.docs, self.lengths):
            score = 0.0
            for term in wanted:
                tf = counts.get(term)
                if tf:
                    score += self.idf[term] * tf * (self.k1 + 1) / (
                        tf + self.k1 * (1 - self.b + self.b * length / self.average))
            result.append(score)
        return result


def ranking(scores, positive=False):
    """Indexes by score, ties by index. positive: leave out what scored nothing (no shared term)."""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return [i for i in order if scores[i] > 0] if positive else order


def rrf(*orders, k=RRF_K, weights=None):
    """Reciprocal-rank fusion: {index: score}. An index missing from a ranking gets nothing from it.
    weights: one factor per ranking (default 1 each)."""
    totals = {}
    for order, weight in zip(orders, weights or [1.0] * len(orders)):
        for rank, index in enumerate(order, 1):
            totals[index] = totals.get(index, 0.0) + weight / (k + rank)
    return totals


def fused_order(*orders, k=RRF_K, weights=None):
    totals = rrf(*orders, k=k, weights=weights)
    return sorted(totals, key=lambda i: (-totals[i], i))


def index_text(title, content):
    """The text a chunk is embedded and keyword-ranked by (INDEX_FORMAT); the model still gets content."""
    return f'{title}\n{content}' if title else content


def cosine(left, right):
    if len(left) != len(right):
        raise RetrievalError('dimension_mismatch',
                             f'向量维度不一致（{len(left)} 与 {len(right)}），请为当前知识点重新建立索引。')
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


# Course-wide material always; class-only material only for its own class.
SCOPE = '''material_id IN (SELECT id FROM materials WHERE class_id IS NULL OR class_id = %s)'''


class ArrayBackend:
    """Scan float8[] and score in Python. Exact at this size; it is not an index."""

    name = 'array-cosine'

    def available(self, conn):
        return True

    def search(self, conn, vector, knowledge_ids, limit, class_id=None):
        rows = conn.execute(f'''SELECT id, material_id, knowledge_id, chunk_index, title, source_title,
            source_url, section, content, char_length, embed_model, embedding
            FROM material_chunks WHERE knowledge_id = ANY(%s) AND {SCOPE}
            ORDER BY material_id, chunk_index''', (list(knowledge_ids), class_id)).fetchall()
        scored = [(cosine(vector, row['embedding']), row) for row in rows]
        # Score first, then stable identifiers: equal scores keep one fixed order.
        scored.sort(key=lambda item: (-item[0], item[1]['material_id'], item[1]['chunk_index']))
        results = []
        for score, row in scored[:limit]:
            row = dict(row)
            row.pop('embedding', None)
            results.append({**row, 'score': round(score, 6)})
        return results


class PgVectorBackend:
    """pgvector '<=>'. Used only when the extension is genuinely installed."""

    name = 'pgvector-cosine'

    COLUMNS = '''id, material_id, knowledge_id, chunk_index, title, source_title,
        source_url, section, content, char_length, embed_model'''

    def available(self, conn):
        try:
            return bool(conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone())
        except Exception:
            return False

    def search(self, conn, vector, knowledge_ids, limit, class_id=None):
        literal = '[' + ','.join(repr(round(value, 6)) for value in vector) + ']'
        rows = conn.execute(f'''SELECT {self.COLUMNS}, 1 - (embedding <=> %s::vector) AS score
            FROM material_chunks WHERE knowledge_id = ANY(%s) AND {SCOPE}
            ORDER BY embedding <=> %s::vector, material_id, chunk_index LIMIT %s''',
                            (literal, list(knowledge_ids), class_id, literal, limit)).fetchall()
        return [dict(row) for row in rows]


def select_backend(conn):
    """Prefer pgvector when it exists; the array scan is always the fallback."""
    for candidate in (PgVectorBackend(), ArrayBackend()):
        if candidate.available(conn):
            return candidate
    return ArrayBackend()


def build_query(goal, states=None):
    """Retrieval question of the vector mode. Without a profile only the shared goal is used."""
    parts = [goal.strip()]
    weak = [state['name'] for state in (states or []) if state.get('state') == 'needs_support']
    if weak:
        parts.append('薄弱知识点：' + '、'.join(weak))
    return '；'.join(part for part in parts if part)


def search(conn, embedder, query, knowledge_ids, limit=6, engine=None, class_id=None):
    engine = engine or select_backend(conn)
    items = engine.search(conn, embedder.embed([query])[0], knowledge_ids, limit, class_id=class_id)
    if not items:
        raise RetrievalError('no_chunks', '所选知识点还没有建立向量索引，请先建立索引或改用精确 SQL 取材料。')
    models = {item['embed_model'] for item in items}
    return {'mode': 'vector', 'backend': engine.name, 'embed_model': embedder.model,
            'query': query, 'knowledge_ids': list(knowledge_ids), 'limit': limit,
            'stored_embed_models': sorted(models), 'items': items}


# ---------------------------------------------------------------- hybrid retrieval

def plan_queries(goal, point_names, states=(), learner_note='', personal=True):
    """The queries of one hybrid run, each with its purpose. Without a profile (personal False) only
    the shared goal and point names are used, so a run without a profile retrieves the same chunks
    for every student and the with/without-profile comparison stays clean."""
    points = '、'.join(name for name in point_names if name)
    goal = (goal or '').strip()
    plan = [dict(role='explain', label=ROLES['explain']['label'], text='；'.join(part for part in (goal, points) if part)),
            dict(role='practice', label=ROLES['practice']['label'], text=f'{points or goal}的例题、解题步骤与练习')]
    if personal:
        notes = []
        for state in states or []:
            if state.get('state') not in ('needs_support', 'partial'):
                continue
            notes.append(state['name'])
            for evidence in (state.get('recent_evidence') or [])[:2]:
                text = (evidence.get('performance') or '').strip()
                if text:
                    notes.append(text[:160])
        if learner_note:
            notes.append(learner_note.strip()[:300])
        if notes:
            plan.append(dict(role='learner', label=ROLES['learner']['label'], text='；'.join(notes)[:600]))
    return plan


def candidates(conn, knowledge_ids, class_id=None):
    """Every chunk of every in-scope material, chunked in memory exactly as index_material stores them."""
    rows = conn.execute('''SELECT * FROM materials WHERE knowledge_id = ANY(%s)
        AND (class_id IS NULL OR class_id = %s) ORDER BY id''', (list(knowledge_ids), class_id)).fetchall()
    pieces = []
    for material in rows:
        for index, piece in enumerate(describe(material['content'])):
            content = piece['content']
            pieces.append(dict(
                key=f'M{material["id"]}#{index}', material_id=material['id'], knowledge_id=material['knowledge_id'],
                chunk_index=index, title=material['title'], source_title=material['source_title'],
                source_url=material['source_url'], section=material['section'], class_id=material.get('class_id'),
                kind=piece['kind'], heading=piece['heading'], content=content, char_length=len(content),
                content_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest()))
    return pieces


def stored_vectors(conn, embedder, pieces):
    """{chunk key: vector} for chunks whose stored row has the same model, position and content."""
    if not pieces:
        return {}
    rows = conn.execute('''SELECT material_id, chunk_index, content_sha256, embedding FROM material_chunks
        WHERE material_id = ANY(%s) AND embed_model = %s AND index_format = %s''',
                        (sorted({piece['material_id'] for piece in pieces}), embedder.model, INDEX_FORMAT)).fetchall()
    found = {(row['material_id'], row['chunk_index'], row['content_sha256']): row['embedding'] for row in rows}
    return {piece['key']: found[(piece['material_id'], piece['chunk_index'], piece['content_sha256'])]
            for piece in pieces if (piece['material_id'], piece['chunk_index'], piece['content_sha256']) in found}


def _item(piece, role, label, score, ranks, selected, coverage=False):
    item = {key: piece[key] for key in ('key', 'material_id', 'knowledge_id', 'chunk_index', 'title', 'source_title',
                                        'source_url', 'section', 'kind', 'heading', 'content', 'char_length',
                                        'content_sha256')}
    if piece.get('class_id') is not None:
        item['class_id'] = piece['class_id']
    item.update(role=role, role_label=label, score=None if score is None else round(score, 6),
                rank_bm25=ranks.get('bm25'), rank_vector=ranks.get('vector'),
                selected_point=piece['knowledge_id'] in selected, coverage=coverage)
    return item


def _prepare(conn, embedder, pieces, plan, vector_note=None):
    """BM25 over the pieces, their stored vectors and one vector per query of plan. embedder None, no
    stored vectors or a failing service: BM25 alone, and vector_note says why."""
    lexical = BM25([index_text(piece['title'], piece['content']) for piece in pieces])
    vectors, query_vectors = {}, {}
    if embedder is not None and pieces and plan:
        vectors = stored_vectors(conn, embedder, pieces)
        if vectors:
            try:
                embedded = embedder.embed([query['text'] for query in plan])
                query_vectors = {query['role']: vector for query, vector in zip(plan, embedded)}
            except EmbeddingError as exc:
                vector_note = str(exc)
        else:
            vector_note = vector_note or '资料片段还没有向量，本次只按关键词排序。'
    return lexical, vectors, query_vectors, vector_note


def _query_ranking(query, pieces, lexical, vectors, query_vectors, chosen_points=frozenset(), role=None):
    """One query over the pieces: the related ones in score order, every fused score, and each ranking's
    positions. role: the ROLES entry whose bonuses apply (None: the plain fused rank)."""
    lexical_scores = lexical.scores(query['text'])
    orders = {'bm25': ranking(lexical_scores, positive=True)}
    vector, similarity = query_vectors.get(query['role']), {}
    if vector is not None:
        similarity = {index: cosine(vector, vectors[piece['key']]) for index, piece in enumerate(pieces)
                      if piece['key'] in vectors}
        orders['vector'] = sorted(similarity, key=lambda index: (-similarity[index], index))
    lexical_top = max(lexical_scores, default=0.0)
    vector_top = max(similarity.values(), default=None)
    related = {index for index in range(len(pieces))
               if (lexical_top > 0 and lexical_scores[index] >= LEXICAL_FLOOR * lexical_top)
               or (vector_top is not None and index in similarity and similarity[index] >= vector_top - VECTOR_MARGIN)}
    positions = {name: {index: rank for rank, index in enumerate(order, 1)} for name, order in orders.items()}
    fused = rrf(*orders.values(), weights=[KEYWORD_WEIGHT if name == 'bm25' else 1.0 for name in orders])
    scores = {}
    for index, value in fused.items():
        piece = pieces[index]
        if role is not None:
            if piece['knowledge_id'] in chosen_points:
                value += role['point'] * BOOST
            if role['prefer'] & set(piece['kind'].split()):
                value += role['kind'] * BOOST
        scores[index] = value
    order = [index for index in sorted(scores, key=lambda i: (-scores[i], pieces[i]['material_id'],
                                                              pieces[i]['chunk_index'])) if index in related]
    return order, scores, positions


def _kept(keep, by_key):
    """The keys of a teacher's keep list (key@hash), refused when a chunk is gone or its text changed."""
    kept = [KEPT.fullmatch(entry) for entry in keep]
    if any(match is None or match[1] not in by_key or not by_key[match[1]]['content_sha256'].startswith(match[2])
           for match in kept):
        raise RetrievalError('stale_selection', '保留的资料片段已经变化（资料被修改或删除），请重新读取材料后再生成。')
    return [match[1] for match in kept]


def _vector_record(embedder, query_vectors, vectors, vector_note):
    used = bool(query_vectors)
    return used, dict(backend='bm25+array-cosine-rrf' if used else 'bm25', embed_model=embedder.model if used else None,
                      vector=dict(used=used, note=None if used else vector_note, chunks_with_vectors=len(vectors)),
                      weights=dict(bm25=KEYWORD_WEIGHT, vector=1.0) if used else dict(bm25=1.0))


def hybrid_search(conn, embedder, plan, *, scope_ids, selected, class_id=None, keep=None, vector_note=None):
    """Pick chunks of the course for each query of plan (plan_queries). embedder None: BM25 only, with
    vector_note saying why. keep: the chunks a teacher kept on the page (kept_id: key@hash); the result
    is exactly those chunks, and the automatic picks the teacher removed are recorded."""
    pieces = candidates(conn, scope_ids, class_id)
    selected = list(dict.fromkeys(selected))
    chosen_points = set(selected)
    lexical, vectors, query_vectors, vector_note = _prepare(conn, embedder, pieces, plan, vector_note)
    picked, taken, contents, ranked = [], set(), set(), []
    explain_scores = {}
    for query in plan:
        role = ROLES[query['role']]
        order, scores, positions = _query_ranking(query, pieces, lexical, vectors, query_vectors, chosen_points, role)
        if query['role'] == 'explain':
            explain_scores = scores
        per_material, count = Counter(), 0
        ranked.append((query, order, scores, positions))
        # A later query fills its own places even when its best chunks were taken by an earlier one.
        # Measured 2026-11-26 (docs/scale-data.md): counting those toward its places instead dropped
        # the out-of-lesson chunks no lecture cited, but also in-lesson examples four of five cited.
        for index in order:
            if count >= role['limit']:
                break
            piece = pieces[index]
            if (piece['key'] in taken or piece['content_sha256'] in contents
                    or per_material[piece['material_id']] >= PER_MATERIAL):
                continue
            ranks = {name: places.get(index) for name, places in positions.items()}
            picked.append(_item(piece, query['role'], query['label'], scores[index], ranks, chosen_points))
            taken.add(piece['key'])
            contents.add(piece['content_sha256'])
            per_material[piece['material_id']] += 1
            count += 1
    # A chunk is counted once, under the first query that took it; if it is also among the best of
    # another query (a worked example the explanation took first), it carries that use too.
    position = {piece['key']: index for index, piece in enumerate(pieces)}
    for item in picked:
        also = [query['role'] for query, order, _, _ in ranked if query['role'] != item['role']
                and position[item['key']] in order[:ROLES[query['role']]['limit']]]
        if also:
            item['also'] = also
    # Every selected point that has material keeps its best chunk for the explanation.
    covered = {item['knowledge_id'] for item in picked}
    explain = next((query for query in plan if query['role'] == 'explain'), plan[0] if plan else None)
    for point in selected:
        if point in covered or explain is None:
            continue
        options = [index for index, piece in enumerate(pieces) if piece['knowledge_id'] == point
                   and piece['key'] not in taken and piece['content_sha256'] not in contents]
        if not options:
            continue
        best = max(options, key=lambda i: (explain_scores.get(i, 0.0), -pieces[i]['material_id'], -pieces[i]['chunk_index']))
        picked.append(_item(pieces[best], explain['role'], explain['label'], explain_scores.get(best), {},
                            chosen_points, coverage=True))
        taken.add(pieces[best]['key'])
        contents.add(pieces[best]['content_sha256'])
    removed = []
    if keep is not None:
        by_key = {piece['key']: piece for piece in pieces}
        keep = _kept(keep, by_key)
        wanted = set(keep)
        removed = [item['key'] for item in picked if item['key'] not in wanted]
        picked = [item for item in picked if item['key'] in wanted]
        have = {item['key'] for item in picked}
        picked += [_item(by_key[key], 'teacher', TEACHER_LABEL, None, {}, chosen_points)
                   for key in keep if key not in have]
    # The next best chunks of every query, so the teacher can add one the automatic pick left out.
    have = {item['key'] for item in picked}
    hashes = {item['content_sha256'] for item in picked}
    alternatives = []
    for query, order, scores, positions in ranked:
        count = 0
        for index in order:
            piece = pieces[index]
            if count >= ALTERNATIVES:
                break
            if piece['key'] in have or piece['content_sha256'] in hashes:
                continue
            ranks = {name: places.get(index) for name, places in positions.items()}
            alternatives.append(_item(piece, query['role'], query['label'], scores[index], ranks, chosen_points))
            have.add(piece['key'])
            hashes.add(piece['content_sha256'])
            count += 1
    _, how = _vector_record(embedder, query_vectors, vectors, vector_note)
    return dict(
        mode='hybrid', version=HYBRID_VERSION, backend=how['backend'], embed_model=how['embed_model'], vector=how['vector'],
        queries=[dict(role=query['role'], label=query['label'], text=query['text']) for query in plan],
        scope=dict(knowledge_points=len(set(scope_ids)), materials=len({piece['material_id'] for piece in pieces}),
                   chunks=len(pieces)),
        selected_knowledge_ids=selected, limits={name: role['limit'] for name, role in ROLES.items()},
        relevance=dict(lexical_floor=LEXICAL_FLOOR, vector_margin=VECTOR_MARGIN),
        weights=how['weights'], index_format=INDEX_FORMAT,
        per_material=PER_MATERIAL, selection='teacher' if keep is not None else 'automatic', removed=removed,
        items=picked, alternatives=alternatives)


ROLE_ORDER = {name: number for number, name in enumerate([*ROLES, 'teacher'])}


def to_materials(items):
    """Shape chunks like the material rows every downstream check already expects: one row per
    material, its chunks in text order, a gap between non-adjacent chunks marked with ……"""
    grouped = {}
    for item in items:
        grouped.setdefault(item['material_id'], []).append(item)
    materials = []
    for material_id, pieces in grouped.items():
        roles = sorted({role for piece in pieces for role in [piece.get('role'), *piece.get('also', [])] if role},
                       key=lambda role: ROLE_ORDER.get(role, 9))
        uses = [ROLES[role]['label'] if role in ROLES else TEACHER_LABEL for role in roles]
        pieces = sorted(pieces, key=lambda piece: piece['chunk_index'])
        head = pieces[0]
        parts = [head['content']]
        for before, after in zip(pieces, pieces[1:]):
            parts.append(('\n' if after['chunk_index'] == before['chunk_index'] + 1 else '\n……\n') + after['content'])
        scores = [piece['score'] for piece in pieces if piece.get('score') is not None]
        row = {'id': material_id, 'knowledge_id': head['knowledge_id'], 'title': head['title'],
               'source_title': head['source_title'], 'source_url': head['source_url'], 'section': head['section'],
               'content': ''.join(parts), 'citation_id': f'M{material_id}', 'chunk_count': len(pieces),
               'max_score': round(max(scores), 6) if scores else None}
        if uses:
            row['use'] = '、'.join(dict.fromkeys(uses))
        if head.get('class_id') is not None:
            row['class_id'] = head['class_id']
        materials.append(row)
    materials.sort(key=lambda row: row['id'])
    return materials


# ---------------------------------------------------------------- the lesson's own material, plus retrieval

def library_order(conn, point_ids):
    """{point id: (position, chapter)}: the library's teaching order, which chapter boundaries are drawn on."""
    rows = conn.execute('SELECT id, position, chapter FROM knowledge_points WHERE id = ANY(%s)',
                        (list(point_ids),)).fetchall()
    return {row['id']: (row['position'], (row['chapter'] or '').strip()) for row in rows}


def later_points(order, lesson_ids=(), chapter=None):
    """Points of the chapters after the lesson's chapters (or after chapter): not taught yet, when the library
    is in teaching order (knowledge.renumber keeps it in chapter order). A point without a chapter is never
    later, and without a known chapter there is no boundary."""
    chapters = {chapter} if chapter else {order[k][1] for k in lesson_ids if k in order and order[k][1]}
    positions = [position for position, name in order.values() if name in chapters]
    if not positions:
        return set()
    cut = max(positions)
    return {k for k, (position, name) in order.items() if name and name not in chapters and position > cut}


def anchored_search(conn, embedder, plan, *, lesson_rows, lesson_ids, scope_ids, class_id=None, keep=None,
                    vector_note=None, budget=16000):
    """The anchored mode (module docstring). lesson_rows: the lookup's rows for the lesson's points, read as
    the sql mode reads them. Returns (record, materials).

    The 针对学情 query ranks the lesson's own chunks and the rest of the course together, later chapters left
    out: a hit in the lesson's own material marks that material, which is given whole anyway; a hit elsewhere
    becomes a passage of its own. Only when the lesson's material and those passages exceed budget are the
    lesson's chunks ranked by the 讲解依据 and 例题练习 queries and kept in that order while they fit, the
    learner's hits and each point's best chunk first. keep: the chunks a teacher kept on the page (key@hash):
    the passages and, for a cut lesson, its chunks; whole materials are not part of it."""
    lesson = set(lesson_ids)
    total = sum(len(row['content']) for row in lesson_rows)
    learner = next((query for query in plan if query['role'] == 'learner'), None)
    later = later_points(library_order(conn, scope_ids), lesson_ids)
    eligible = [point for point in scope_ids if point not in later]
    pieces = candidates(conn, eligible, class_id) if learner is not None or total > budget or keep else []
    lexical, vectors, query_vectors = None, {}, {}
    if pieces:
        lexical, vectors, query_vectors, vector_note = _prepare(conn, embedder, pieces, plan, vector_note)
    ranked, items, taken, contents, hits = {}, [], set(), set(), set()

    def cost(chunk):
        # What a chunk adds to the materials: its text, and at most one 「……」 gap before it (to_materials).
        return chunk['char_length'] + GAP

    def take(index, role, anchor=False):
        piece = pieces[index]
        _, scores_, positions_ = ranked.get(role, ([], {}, {}))
        ranks = {name: places.get(index) for name, places in positions_.items()}
        item = _item(piece, role, ROLES[role]['label'], scores_.get(index), ranks, lesson)
        if anchor:
            item['anchor'] = True
        items.append(item)
        taken.add(piece['key'])
        contents.add(piece['content_sha256'])
        return item

    if learner is not None and pieces:
        ranked['learner'] = _query_ranking(learner, pieces, lexical, vectors, query_vectors, lesson, ROLES['learner'])
        count, per_material = 0, Counter()
        for index in ranked['learner'][0]:
            if count >= ROLES['learner']['limit']:
                break
            piece = pieces[index]
            if piece['knowledge_id'] in lesson:
                hits.add(index)
                count += 1
            elif piece['content_sha256'] not in contents and per_material[piece['material_id']] < PER_MATERIAL:
                take(index, 'learner')
                per_material[piece['material_id']] += 1
                count += 1
    cut = total + sum(cost(item) for item in items) > budget
    if cut and pieces:
        own = [index for index, piece in enumerate(pieces) if piece['knowledge_id'] in lesson]
        best = {}
        for name in ('explain', 'practice'):
            query = next((query for query in plan if query['role'] == name), None)
            if query is None:
                continue
            ranked[name] = _query_ranking(query, pieces, lexical, vectors, query_vectors, lesson, ROLES[name])
            for index in own:
                score = ranked[name][1].get(index)
                if score is not None and score > best.get(index, (0.0, None))[0]:
                    best[index] = (score, name)
        def rank(index):
            return (-best.get(index, (0.0, None))[0], pieces[index]['material_id'], pieces[index]['chunk_index'])
        firsts = [min(options, key=rank) for point in lesson_ids
                  if (options := [index for index in own if pieces[index]['knowledge_id'] == point])]
        room = budget - sum(cost(item) for item in items)
        for index in dict.fromkeys(sorted(hits, key=rank) + firsts + sorted(own, key=rank)):
            piece = pieces[index]
            if piece['key'] in taken or piece['content_sha256'] in contents or cost(piece) > room:
                continue
            item = take(index, 'learner' if index in hits else best.get(index, (0.0, 'explain'))[1], anchor=True)
            if index in hits and index in best:
                item['also'] = [best[index][1]]
            room -= cost(piece)
    # Runners-up the teacher can add: passages from elsewhere for the learner, and more of a cut lesson's chunks.
    alternatives, have, hashes = [], set(taken), set(contents)
    for name, (order, scores, positions) in ranked.items():
        count = 0
        for index in order:
            piece = pieces[index]
            if count >= ALTERNATIVES:
                break
            if piece['key'] in have or piece['content_sha256'] in hashes or (piece['knowledge_id'] in lesson) == (name == 'learner'):
                continue
            ranks = {key: places.get(index) for key, places in positions.items()}
            alternative = _item(piece, name, ROLES[name]['label'], scores[index], ranks, lesson)
            if piece['knowledge_id'] in lesson:
                alternative['anchor'] = True
            alternatives.append(alternative)
            have.add(piece['key'])
            hashes.add(piece['content_sha256'])
            count += 1
    removed = []
    if keep is not None:
        by_key = {piece['key']: piece for piece in pieces}
        wanted = _kept(keep, by_key)
        removed = [item['key'] for item in items if item['key'] not in set(wanted)]
        present = {item['key'] for item in items}
        items = [item for item in items if item['key'] in set(wanted)]
        for key in wanted:
            piece = by_key[key]
            # The lesson's own material is already given whole unless the lesson was cut to the budget.
            if key in present or (piece['knowledge_id'] in lesson and not cut):
                continue
            item = _item(piece, 'teacher', TEACHER_LABEL, None, {}, lesson)
            if piece['knowledge_id'] in lesson:
                item['anchor'] = True
            items.append(item)
    marked = {pieces[index]['material_id'] for index in hits}
    if cut:
        materials = to_materials(items)
        for row in materials:
            if row['knowledge_id'] in lesson:
                roles = {role for item in items if item['material_id'] == row['id'] for role in [item['role'], *item.get('also', [])]}
                row['use'] = f'{ANCHOR_LABEL}（节选）' + ('、' + ROLES['learner']['label'] if 'learner' in roles else '')
    else:
        materials = [{**row, 'use': ANCHOR_LABEL + ('、' + ROLES['learner']['label'] if row['id'] in marked else '')}
                     for row in lesson_rows] + to_materials(items)
        materials.sort(key=lambda row: row['id'])
    if keep is not None:
        if not materials:
            raise RetrievalError('empty_selection', '请至少保留一段课程资料。')
        size = sum(len(row['content']) for row in materials)
        if size > budget:
            raise RetrievalError('over_budget', f'本课知识点的资料加上所选片段共 {size} 字，超过 {budget} 字的上限，请少选几段。')
    _, how = _vector_record(embedder, query_vectors, vectors, vector_note)
    return dict(
        mode='anchored', version=ANCHORED_VERSION, ranked=bool(pieces),
        backend=how['backend'] if pieces else 'lookup', embed_model=how['embed_model'],
        vector=how['vector'] if pieces else dict(used=False, note=None, chunks_with_vectors=0),
        queries=[dict(role=query['role'], label=query['label'], text=query['text']) for query in plan if query['role'] in ranked],
        base=dict(kind='chunks' if cut else 'materials', knowledge_ids=list(lesson_ids), characters=total, budget=budget,
                  materials=[dict(id=row['id'], title=row['title'], knowledge_id=row['knowledge_id'],
                                  characters=len(row['content'])) for row in lesson_rows]),
        boundary=dict(later_points=len(later)),
        scope=dict(knowledge_points=len(eligible), materials=len({piece['material_id'] for piece in pieces}),
                   chunks=len(pieces)),
        selected_knowledge_ids=list(lesson_ids), limits=dict(learner=ROLES['learner']['limit']),
        relevance=dict(lexical_floor=LEXICAL_FLOOR, vector_margin=VECTOR_MARGIN), weights=how['weights'],
        index_format=INDEX_FORMAT, per_material=PER_MATERIAL, learner_in_lesson=sorted(marked),
        selection='teacher' if keep is not None else 'automatic', removed=removed, items=items,
        alternatives=alternatives), materials


def suggest_points(conn, embedder, text, *, library, class_id=None, chapter=None, vector_note=None):
    """Knowledge points of a course library for a teacher's free-text focus (SUGGEST_* above). library: the
    course's points in teaching order, each with id, name, chapter and position. chapter: the teacher's
    「本课在哪一章」; points of later chapters are listed but never ticked. A point without material has nothing
    to be found by. Returns the record the page shows and the new lesson keeps."""
    by_id = {point['id']: point for point in library}
    pieces = candidates(conn, list(by_id), class_id)
    query = dict(role='focus', label='本课重点', text=text)
    lexical, vectors, query_vectors, vector_note = _prepare(conn, embedder, pieces, [query], vector_note)
    order, scores, positions = _query_ranking(query, pieces, lexical, vectors, query_vectors)
    found = {}
    for index in order[:SUGGEST_DEPTH]:
        found.setdefault(pieces[index]['knowledge_id'], []).append(index)
    votes = {}
    for point, indexes in found.items():
        best = sorted((scores[index] for index in indexes), reverse=True)
        votes[point] = best[0] + (best[1] / 2 if len(best) > 1 else 0.0)
    later = later_points({p['id']: (p['position'], (p['chapter'] or '').strip()) for p in library}, chapter=chapter) if chapter else set()
    ranked = sorted(votes, key=lambda point: (-votes[point], by_id[point]['position'], point))
    top = votes[ranked[0]] if ranked else 0.0
    close = [point for point in ranked if votes[point] >= SUGGEST_SHARE * top]
    ticked = [point for point in close if point not in later][:SUGGEST_MAX]
    shown = sorted(set(ranked[:SUGGEST_SHOWN]) | set(ticked), key=ranked.index)

    def evidence(point):
        return [dict(key=pieces[index]['key'], material_id=pieces[index]['material_id'], title=pieces[index]['title'],
                     heading=pieces[index]['heading'], kind=pieces[index]['kind'], excerpt=pieces[index]['content'][:160],
                     score=round(scores[index], 6), rank_bm25=positions.get('bm25', {}).get(index),
                     rank_vector=positions.get('vector', {}).get(index)) for index in found[point][:2]]
    warnings = []
    if len(set(terms(text))) < 4:
        warnings.append('重点写得太短，找到的知识点可能不准：请写出本课要讲的概念、方法或题型。')
    if not order:
        warnings.append('没有找到与重点相关的资料：请换一种说法，或从知识点库里直接选。')
    crowd = [point for point in close if point not in later]
    if len(crowd) > SUGGEST_MAX:
        warnings.append(f'与重点差不多同样相关的知识点有 {len(crowd)} 个，重点可能写得太笼统；只勾选了最相关的 {SUGGEST_MAX} 个，请核对。')
    chapters = {by_id[point]['chapter'] for point in ticked if by_id[point]['chapter']}
    if len(chapters) >= 3:
        warnings.append(f'勾选的知识点分散在 {len(chapters)} 个章节，请确认本课范围，或在「本课在哪一章」里限定。')
    skipped = [point for point in close if point in later]
    if skipped:
        warnings.append(f'有 {len(skipped)} 个相关的知识点在「{chapter}」之后的章节，没有勾选。')
    with_material = {piece['knowledge_id'] for piece in pieces}
    _, how = _vector_record(embedder, query_vectors, vectors, vector_note)
    return dict(
        version=SUGGEST_VERSION, text=text, chapter=chapter, backend=how['backend'], embed_model=how['embed_model'],
        vector=how['vector'], weights=how['weights'], index_format=INDEX_FORMAT,
        rule=dict(depth=SUGGEST_DEPTH, share=SUGGEST_SHARE, limit=SUGGEST_MAX),
        scope=dict(knowledge_points=len(by_id), materials=len({piece['material_id'] for piece in pieces}),
                   chunks=len(pieces), without_material=len(set(by_id) - with_material)),
        suggested=ticked,
        points=[dict(knowledge_id=point, name=by_id[point]['name'], chapter=by_id[point]['chapter'],
                     position=by_id[point]['position'], score=round(votes[point], 6),
                     share=round(votes[point] / top, 3) if top else 0.0, suggested=point in ticked,
                     later=point in later, evidence=evidence(point)) for point in shown],
        warnings=warnings)


def rank_texts(query, texts):
    """BM25 scores of texts for query; used to pick a segment's share of a frozen pool, offline."""
    return BM25(texts).scores(query) if texts else []


# ---------------------------------------------------------------- index

def index_material(conn, embedder, material, force=False):
    """Reuse only chunks whose model, content, boundaries, kind and source still match."""
    # Serialize replacements, including concurrent automatic indexing requests.
    conn.execute('SELECT pg_advisory_xact_lock(73521, %s)', (material['id'],))
    pieces = describe(material['content'])
    rows = conn.execute('''SELECT embed_model, chunk_index, content_sha256, knowledge_id,
        title, source_title, source_url, section, kind, heading, index_format FROM material_chunks
        WHERE material_id = %s ORDER BY chunk_index''', (material['id'],)).fetchall()
    if not pieces:
        conn.execute('DELETE FROM material_chunks WHERE material_id = %s', (material['id'],))
        conn.commit()
        return 0
    chunks = [piece['content'] for piece in pieces]
    hashes = [hashlib.sha256(chunk.encode('utf-8')).hexdigest() for chunk in chunks]
    metadata = ('knowledge_id', 'title', 'source_title', 'source_url', 'section')
    current = len(rows) == len(chunks) and all(
        row['embed_model'] == embedder.model and row['chunk_index'] == index
        and row['content_sha256'] == hashes[index]
        and row['kind'] == pieces[index]['kind'] and row['heading'] == pieces[index]['heading']
        and row['index_format'] == INDEX_FORMAT
        and all(row[key] == material[key] for key in metadata)
        for index, row in enumerate(rows))
    if current and not force:
        return len(rows)
    vectors = embedder.embed([index_text(material['title'], chunk) for chunk in chunks])
    payload = [(material['id'], material['knowledge_id'], index, material['title'],
                material['source_title'], material['source_url'], material['section'],
                piece['content'], len(piece['content']), hashes[index], embedder.model, vector,
                piece['kind'], piece['heading'], INDEX_FORMAT)
               for index, (piece, vector) in enumerate(zip(pieces, vectors))]
    conn.execute('DELETE FROM material_chunks WHERE material_id = %s', (material['id'],))
    with conn.cursor() as cursor:  # psycopg3 batches through a cursor
        cursor.executemany('''INSERT INTO material_chunks (material_id, knowledge_id, chunk_index,
            title, source_title, source_url, section, content, char_length, content_sha256,
            embed_model, embedding, kind, heading, index_format)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', payload)
    # psycopg3 closes the connection on `with conn:`; this connection is borrowed, so commit only.
    conn.commit()
    return len(payload)


def adopt_content(conn, embedder, *, knowledge_id, title, content, source_title, source_url, section=''):
    """Turn outside text into a real material row, then index it.

    Idempotent through source_url: adopting the same document twice updates it instead
    of stacking duplicates. The material id it lands on is what citations will use.
    """
    content = (content or '').strip()
    if not content:
        raise RetrievalError('empty_content', '资料正文为空，无法收录。')
    row = conn.execute('SELECT * FROM materials WHERE source_url = %s', (source_url,)).fetchone()
    if row is None:
        row = conn.execute('''INSERT INTO materials
            (id, knowledge_id, title, content, source_title, source_url, section)
            VALUES ((SELECT COALESCE(max(id), 0) + 1 FROM materials), %s, %s, %s, %s, %s, %s)
            RETURNING *''', (knowledge_id, title[:200], content, source_title, source_url, section)).fetchone()
    elif row['content'] != content:
        conn.execute('UPDATE materials SET title = %s, content = %s WHERE id = %s',
                     (title[:200], content, row['id']))
        row = dict(row, title=title[:200], content=content)
    return {'material_id': row['id'], 'citation_id': f"M{row['id']}",
            'chunks': index_material(conn, embedder, row, force=True)}


def index_knowledge(conn, embedder, knowledge_ids, force=False):
    """Index every material of the given knowledge points. Returns indexed material count."""
    materials = conn.execute('SELECT * FROM materials WHERE knowledge_id = ANY(%s) ORDER BY id',
                             (list(knowledge_ids),)).fetchall()
    for material in materials:
        index_material(conn, embedder, material, force=force)
    return len(materials)


def status(conn, embedder, knowledge_ids=None):
    """Report indexing state per knowledge point for the embedding model in use."""
    rows = conn.execute('''SELECT knowledge_id, count(*) AS chunks, max(char_length) AS longest,
        count(DISTINCT material_id) AS materials FROM material_chunks
        WHERE embed_model = %s GROUP BY knowledge_id ORDER BY knowledge_id''',
                        (embedder.model,)).fetchall()
    by_point = {row['knowledge_id']: row for row in rows}
    points = conn.execute('SELECT id, name FROM knowledge_points ORDER BY id').fetchall()
    wanted = set(knowledge_ids or [])
    return {'embed_model': embedder.model, 'backend': select_backend(conn).name,
            'total_chunks': sum(row['chunks'] for row in rows),
            'points': [{'knowledge_id': point['id'], 'name': point['name'],
                        'indexed': point['id'] in by_point,
                        'chunks': by_point.get(point['id'], {}).get('chunks', 0),
                        'materials': by_point.get(point['id'], {}).get('materials', 0)}
                       for point in points if not wanted or point['id'] in wanted]}
