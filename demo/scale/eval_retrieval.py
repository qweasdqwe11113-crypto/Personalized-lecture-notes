"""Retrieval baseline on the scale corpus: does a teaching question find its knowledge point?

    .venv/bin/python demo/scale/eval_retrieval.py                    # offline, hashing vectors
    .venv/bin/python demo/scale/eval_retrieval.py --embedder qwen    # the configured embedding model (real calls)

For each course, every chunk of the whole chapter is ranked for each labelled question,
and a hit is a chunk from the question's own knowledge point. Three rankers are compared:
the embedding cosine the app uses, a character-bigram BM25, and their reciprocal-rank
fusion. Needs no database. The hashing embedder only measures shared characters, so its
"vector" row says nothing about semantic retrieval; use --embedder qwen for that.
"""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from corpus import COURSES, load_course  # noqa: E402
from retrieval import chunk_text, cosine  # noqa: E402


def grams(text):
    compact = ''.join(c.lower() for c in text if c.isalnum())
    return [compact[i:i + 2] for i in range(len(compact) - 1)] or [compact]


class BM25:
    def __init__(self, documents, k1=1.5, b=0.75):
        self.docs = [grams(d) for d in documents]
        self.k1, self.b = k1, b
        self.average = sum(map(len, self.docs)) / max(1, len(self.docs))
        self.frequency = {}
        for doc in self.docs:
            for term in set(doc):
                self.frequency[term] = self.frequency.get(term, 0) + 1

    def scores(self, query):
        result = []
        for doc in self.docs:
            counts, score = {}, 0.0
            for term in doc:
                counts[term] = counts.get(term, 0) + 1
            for term in set(grams(query)):
                if term not in counts:
                    continue
                idf = math.log(1 + (len(self.docs) - self.frequency[term] + 0.5) / (self.frequency[term] + 0.5))
                tf = counts[term]
                score += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * len(doc) / self.average))
            result.append(score)
        return result


def ranking(scores):
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def fuse(*rankings, k=60):
    totals = {}
    for order in rankings:
        for rank, index in enumerate(order, 1):
            totals[index] = totals.get(index, 0) + 1 / (k + rank)
    return sorted(totals, key=lambda i: (-totals[i], i))


def metrics(orders, relevant, cutoffs):
    result = {f'recall@{k}': 0 for k in cutoffs}
    result.update(mrr=0.0, precision_at_6=0.0)
    for order, wanted in zip(orders, relevant):
        first = next((rank for rank, index in enumerate(order, 1) if index in wanted), None)
        for k in cutoffs:
            result[f'recall@{k}'] += bool(first and first <= k)
        result['mrr'] += 1 / first if first else 0
        result['precision_at_6'] += sum(index in wanted for index in order[:6]) / 6
    return {key: round(value / len(orders), 3) for key, value in result.items()}


def evaluate(embedder, examples=8, seed=7, cutoffs=(1, 3, 6)):
    report = []
    for name in COURSES:
        course = load_course(name, examples, seed)
        chunks = [(index, chunk) for index, point in enumerate(course['points'])
                  for material in point['materials'] for chunk in chunk_text(material['content'])]
        texts = [chunk for _, chunk in chunks]
        queries = [(index, query) for index, point in enumerate(course['points']) for query in point['queries']]
        relevant = [{i for i, (point, _) in enumerate(chunks) if point == wanted} for wanted, _ in queries]
        vectors = embedder.embed(texts)
        query_vectors = embedder.embed([q for _, q in queries])
        lexical = BM25(texts)
        vector_orders = [ranking([cosine(qv, v) for v in vectors]) for qv in query_vectors]
        lexical_orders = [ranking(lexical.scores(q)) for _, q in queries]
        hybrid_orders = [fuse(a, b) for a, b in zip(vector_orders, lexical_orders)]
        report.append(dict(course=course['title'], chunks=len(chunks), queries=len(queries),
                           characters=sum(map(len, texts)), embed_model=embedder.model,
                           rankers={'vector': metrics(vector_orders, relevant, cutoffs),
                                    'bm25': metrics(lexical_orders, relevant, cutoffs),
                                    'hybrid_rrf': metrics(hybrid_orders, relevant, cutoffs)}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--embedder', choices=['hash', 'qwen'], default='hash')
    parser.add_argument('--examples', type=int, default=8)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--json', help='把结果另存为 JSON 文件')
    args = parser.parse_args()
    if args.embedder == 'qwen':
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / '.env')
        from embed import QwenEmbedder
        embedder = QwenEmbedder()
        if not embedder.configured():
            raise SystemExit('嵌入模型尚未配置，请先在系统设置或 .env 中填写。')
        print(f'将调用真实嵌入模型 {embedder.model}（约 400 段文本）。')
    else:
        from embed import HashingEmbedder
        embedder = HashingEmbedder(1024)
        print('使用离线哈希向量：只反映字面重合，不代表语义检索效果。')
    report = evaluate(embedder, args.examples, args.seed)
    for course in report:
        print(f"\n{course['course']} · {course['chunks']} 个片段 / {course['characters']} 字符 · {course['queries']} 个问题 · {course['embed_model']}")
        columns = list(next(iter(course['rankers'].values())))
        print('  ' + '方法'.ljust(12) + ''.join(c.rjust(16) for c in columns))
        for ranker, values in course['rankers'].items():
            print('  ' + ranker.ljust(12) + ''.join(f'{values[c]:.3f}'.rjust(16) for c in columns))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        print('\n已保存：' + args.json)


if __name__ == '__main__':
    main()
