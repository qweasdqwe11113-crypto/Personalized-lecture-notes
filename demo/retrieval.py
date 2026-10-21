"""Retrieval over material chunks.

Both modes stay explicit:
  sql    - the original exact knowledge-point SQL lookup, still the default, so every
           earlier lecture remains comparable.
  vector - recall every chunk belonging to the selected points, then rank by cosine.

Only the ranking half is pluggable: ArrayBackend scores float8[] in Python today and
PgVectorBackend takes over with '<=>' later, with no caller changing. Chunking is
deliberately deterministic, so the same input always yields the same chunks and a
saved run can be reproduced. A chunk keeps its parent material's citation number,
which leaves the meaning of [M编号] exactly as it was.
"""
import hashlib
import math

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
MODES = ('sql', 'vector')


class RetrievalError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Paragraphs first, then a sliding window for blocks that are still too long."""
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    blocks, current = [], ''
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        candidate = f'{current}\n{line}' if current else line
        if current and len(candidate) > size:
            blocks.append(current)
            current = line
        else:
            current = candidate
    if current:
        blocks.append(current)
    chunks = []
    for block in blocks:
        if len(block) <= size:
            chunks.append(block)
            continue
        start = 0
        while start < len(block):
            chunks.append(block[start:start + size])
            if start + size >= len(block):
                break
            start += max(size - overlap, 1)
    return [piece for piece in chunks if piece.strip()]


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


class ArrayBackend:
    """Scan float8[] and score in Python. Exact at this size; it is not an index."""

    name = 'array-cosine'

    def available(self, conn):
        return True

    def search(self, conn, vector, knowledge_ids, limit):
        rows = conn.execute('''SELECT id, material_id, knowledge_id, chunk_index, title, source_title,
            source_url, section, content, char_length, embed_model, embedding
            FROM material_chunks WHERE knowledge_id = ANY(%s)
            ORDER BY material_id, chunk_index''', (list(knowledge_ids),)).fetchall()
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

    def search(self, conn, vector, knowledge_ids, limit):
        literal = '[' + ','.join(repr(round(value, 6)) for value in vector) + ']'
        rows = conn.execute(f'''SELECT {self.COLUMNS}, 1 - (embedding <=> %s::vector) AS score
            FROM material_chunks WHERE knowledge_id = ANY(%s)
            ORDER BY embedding <=> %s::vector, material_id, chunk_index LIMIT %s''',
                            (literal, list(knowledge_ids), literal, limit)).fetchall()
        return [dict(row) for row in rows]


def select_backend(conn):
    """Prefer pgvector when it exists; the array scan is always the fallback."""
    for candidate in (PgVectorBackend(), ArrayBackend()):
        if candidate.available(conn):
            return candidate
    return ArrayBackend()


def build_query(goal, states=None):
    """Retrieval question for one run. Without a profile only the shared goal is used."""
    parts = [goal.strip()]
    weak = [state['name'] for state in (states or []) if state.get('state') == 'needs_support']
    if weak:
        parts.append('薄弱知识点：' + '、'.join(weak))
    return '；'.join(part for part in parts if part)


def search(conn, embedder, query, knowledge_ids, limit=6, engine=None):
    engine = engine or select_backend(conn)
    items = engine.search(conn, embedder.embed([query])[0], knowledge_ids, limit)
    if not items:
        raise RetrievalError('no_chunks', '所选知识点还没有建立向量索引，请先建立索引或改用精确 SQL 取材料。')
    models = {item['embed_model'] for item in items}
    return {'mode': 'vector', 'backend': engine.name, 'embed_model': embedder.model,
            'query': query, 'knowledge_ids': list(knowledge_ids), 'limit': limit,
            'stored_embed_models': sorted(models), 'items': items}


def to_materials(items):
    """Shape chunks like the material rows every downstream check already expects."""
    grouped = {}
    for item in items:
        grouped.setdefault(item['material_id'], []).append(item)
    materials = []
    for material_id, pieces in grouped.items():
        pieces.sort(key=lambda piece: piece['chunk_index'])
        head = pieces[0]
        materials.append({'id': material_id, 'knowledge_id': head['knowledge_id'],
                          'title': head['title'], 'source_title': head['source_title'],
                          'source_url': head['source_url'], 'section': head['section'],
                          'content': '\n'.join(piece['content'] for piece in pieces),
                          'citation_id': f'M{material_id}', 'chunk_count': len(pieces),
                          'max_score': round(max(piece['score'] for piece in pieces), 6)})
    materials.sort(key=lambda row: row['id'])
    return materials


def index_material(conn, embedder, material, force=False):
    """Reuse only chunks whose model, content, boundaries and source still match."""
    # Serialize replacements, including concurrent automatic indexing requests.
    conn.execute('SELECT pg_advisory_xact_lock(73521, %s)', (material['id'],))
    chunks = chunk_text(material['content'])
    rows = conn.execute('''SELECT embed_model, chunk_index, content_sha256, knowledge_id,
        title, source_title, source_url, section FROM material_chunks
        WHERE material_id = %s ORDER BY chunk_index''', (material['id'],)).fetchall()
    if not chunks:
        conn.execute('DELETE FROM material_chunks WHERE material_id = %s', (material['id'],))
        conn.commit()
        return 0
    hashes = [hashlib.sha256(chunk.encode('utf-8')).hexdigest() for chunk in chunks]
    metadata = ('knowledge_id', 'title', 'source_title', 'source_url', 'section')
    current = len(rows) == len(chunks) and all(
        row['embed_model'] == embedder.model and row['chunk_index'] == index
        and row['content_sha256'] == hashes[index]
        and all(row[key] == material[key] for key in metadata)
        for index, row in enumerate(rows))
    if current and not force:
        return len(rows)
    vectors = embedder.embed(chunks)
    payload = [(material['id'], material['knowledge_id'], index, material['title'],
                material['source_title'], material['source_url'], material['section'],
                chunk, len(chunk), hashlib.sha256(chunk.encode('utf-8')).hexdigest(),
                embedder.model, vector)
               for index, (chunk, vector) in enumerate(zip(chunks, vectors))]
    conn.execute('DELETE FROM material_chunks WHERE material_id = %s', (material['id'],))
    with conn.cursor() as cursor:  # psycopg3 batches through a cursor
        cursor.executemany('''INSERT INTO material_chunks (material_id, knowledge_id, chunk_index,
            title, source_title, source_url, section, content, char_length, content_sha256,
            embed_model, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', payload)
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
