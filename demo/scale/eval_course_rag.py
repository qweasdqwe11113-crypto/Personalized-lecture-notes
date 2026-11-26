"""Retrieval on a real course: does a teacher's question find the material of its knowledge point?

    .venv/bin/python demo/scale/eval_course_rag.py                       # offline, hashing vectors
    .venv/bin/python demo/scale/eval_course_rag.py --embedder qwen       # the configured embedding model

Reads the course material from PostgreSQL in a read-only transaction and embeds it in memory; no
chunk or vector is written to the database. Every chunk of the whole course is ranked for each
labelled question (experiments/2026-11-26-rag/course2-questions.json), and a hit is a chunk of one
of the question's knowledge points. Compared: BM25, embedding cosine and their reciprocal-rank
fusion, each on the chunk text alone and with the material's title in front (the title carries the
Chinese name of the knowledge point; the slides themselves are mostly English). The relevance floor
of hybrid_search is checked too: how often the first relevant chunk survives it, and how many chunks do.
The app ships the title variant with BM25 at weight KEYWORD_WEIGHT in the fusion (hybrid_rrf_w…).
Real embeddings are cached under .runtime/rag-eval/ (git-ignored), so a rerun calls the service only
for new text.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from retrieval import (BM25, KEYWORD_WEIGHT, LEXICAL_FLOOR, VECTOR_MARGIN, cosine, describe,  # noqa: E402
                       fused_order, index_text, ranking)

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / 'experiments' / '2026-11-26-rag' / 'course2-questions.json'
CUTOFFS = (1, 3, 6, 10)


class CachedEmbedder:
    """Embeds only text it has not seen; vectors are kept per model under .runtime/rag-eval/."""

    def __init__(self, embedder):
        self.embedder, self.model = embedder, embedder.model
        safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in self.model)
        self.path = ROOT / '.runtime' / 'rag-eval' / f'vectors-{safe}.json'
        self.cache = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.calls = 0

    def embed(self, texts):
        keys = [hashlib.sha256(text.encode('utf-8')).hexdigest() for text in texts]
        missing = list(dict.fromkeys(key for key in keys if key not in self.cache))
        if missing:
            wanted = {key: text for key, text in zip(keys, texts) if key in set(missing)}
            vectors = self.embedder.embed([wanted[key] for key in missing])
            self.calls += len(missing)
            self.cache.update(zip(missing, vectors))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache))
        return [self.cache[key] for key in keys]


def load_chunks(dsn, knowledge_ids):
    with psycopg.connect(dsn, row_factory=dict_row, options='-c default_transaction_read_only=on') as conn:
        rows = conn.execute('''SELECT id, knowledge_id, title, content FROM materials
            WHERE knowledge_id = ANY(%s) AND class_id IS NULL ORDER BY id''', (knowledge_ids,)).fetchall()
    chunks = []
    for row in rows:
        for index, piece in enumerate(describe(row['content'])):
            chunks.append(dict(key=f'M{row["id"]}#{index}', knowledge_id=row['knowledge_id'], title=row['title'],
                               kind=piece['kind'], heading=piece['heading'], content=piece['content']))
    return rows, chunks


def titled(chunk):
    return index_text(chunk['title'], chunk['content'])


def metrics(orders, relevant):
    result = {f'recall@{k}': 0.0 for k in CUTOFFS}
    result['mrr'] = 0.0
    for order, wanted in zip(orders, relevant):
        first = next((rank for rank, index in enumerate(order, 1) if index in wanted), None)
        for k in CUTOFFS:
            result[f'recall@{k}'] += bool(first and first <= k)
        result['mrr'] += 1 / first if first else 0
    return {key: round(value / len(orders), 3) for key, value in result.items()}


def gate(lexical_scores, similarity, wanted):
    """hybrid_search's relevance floor for one query: does the best relevant chunk pass, and how many pass."""
    lexical_top = max(lexical_scores, default=0.0)
    vector_top = max(similarity, default=None)
    passing = {i for i in range(len(lexical_scores))
               if (lexical_top > 0 and lexical_scores[i] >= LEXICAL_FLOOR * lexical_top)
               or (vector_top is not None and similarity[i] >= vector_top - VECTOR_MARGIN)}
    return bool(passing & wanted), len(passing)


def evaluate(embedder, dsn, questions):
    rows, chunks = load_chunks(dsn, questions['course_knowledge_ids'])
    asked = questions['questions']
    relevant = [{i for i, chunk in enumerate(chunks) if chunk['knowledge_id'] in q['relevant']} for q in asked]
    query_vectors = embedder.embed([q['text'] for q in asked])
    report = dict(materials=len(rows), chunks=len(chunks), characters=sum(len(c['content']) for c in chunks),
                  questions=len(asked), embed_model=embedder.model, rankers={}, gate={}, per_question={})
    for variant, text_of in (('text', lambda c: c['content']), ('title+text', titled)):
        texts = [text_of(chunk) for chunk in chunks]
        vectors = embedder.embed(texts)
        lexical = BM25(texts)
        lexical_scores = [lexical.scores(q['text']) for q in asked]
        similarity = [[cosine(qv, v) for v in vectors] for qv in query_vectors]
        orders = {'bm25': [ranking(scores, positive=True) for scores in lexical_scores],
                  'vector': [ranking(row) for row in similarity]}
        orders['hybrid_rrf'] = [fused_order(a, b) for a, b in zip(orders['vector'], orders['bm25'])]
        orders[f'hybrid_rrf_w{KEYWORD_WEIGHT}'] = [fused_order(a, b, weights=[1.0, KEYWORD_WEIGHT])
                                                   for a, b in zip(orders['vector'], orders['bm25'])]
        for name, found in orders.items():
            report['rankers'][f'{name} ({variant})'] = metrics(found, relevant)
        gated = [gate(scores, row, wanted) for scores, row, wanted in zip(lexical_scores, similarity, relevant)]
        report['gate'][variant] = dict(first_relevant_kept=round(sum(kept for kept, _ in gated) / len(gated), 3),
                                       median_passing=statistics.median(n for _, n in gated),
                                       max_passing=max(n for _, n in gated))
        for q, order in zip(asked, orders[f'hybrid_rrf_w{KEYWORD_WEIGHT}']):
            report['per_question'].setdefault(q['id'], {})[variant] = [chunks[i]['key'] for i in order[:3]]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--embedder', choices=['hash', 'qwen'], default='hash')
    parser.add_argument('--questions', default=str(QUESTIONS))
    parser.add_argument('--json', help='把结果另存为 JSON 文件')
    args = parser.parse_args()
    questions = json.loads(Path(args.questions).read_text(encoding='utf-8'))
    if args.embedder == 'qwen':
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        from embed import QwenEmbedder
        base = QwenEmbedder()
        if not base.configured():
            raise SystemExit('嵌入模型尚未配置，请先在系统设置或 .env 中填写。')
        embedder = CachedEmbedder(base)
        print(f'将调用真实嵌入模型 {base.model}；已缓存的文本不再调用。')
    else:
        from embed import HashingEmbedder
        embedder = HashingEmbedder(1024)
        print('使用离线哈希向量：只反映字面重合，不代表语义检索效果。')
    dsn = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
    report = evaluate(embedder, dsn, questions)
    print(f"{report['materials']} 份材料 · {report['chunks']} 个片段 / {report['characters']} 字符 · "
          f"{report['questions']} 个问题 · {report['embed_model']}")
    columns = list(next(iter(report['rankers'].values())))
    print('  ' + '方法'.ljust(26) + ''.join(c.rjust(11) for c in columns))
    for ranker, values in report['rankers'].items():
        print('  ' + ranker.ljust(26) + ''.join(f'{values[c]:.3f}'.rjust(11) for c in columns))
    for variant, values in report['gate'].items():
        print(f'  相关性下限（{variant}）：首个相关片段保留 {values["first_relevant_kept"]:.0%}，'
              f'通过下限的片段中位数 {values["median_passing"]}，最多 {values["max_passing"]}')
    if getattr(embedder, 'calls', None) is not None:
        print(f'本次新嵌入 {embedder.calls} 段文本。')
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print('已保存：' + args.json)


if __name__ == '__main__':
    main()
