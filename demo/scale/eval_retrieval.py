"""Retrieval baseline on the scale corpus: does a teaching question find its knowledge point?

    .venv/bin/python demo/scale/eval_retrieval.py                    # offline, hashing vectors
    .venv/bin/python demo/scale/eval_retrieval.py --embedder qwen    # the configured embedding model (real calls)

For each course, every chunk of the whole chapter is ranked for each labelled question,
and a hit is a chunk from the question's own knowledge point. Three rankers are compared:
the embedding cosine, BM25 over the app's search terms (Chinese character pairs, whole Latin
words), and their reciprocal-rank fusion, all taken from retrieval.py. Needs no database. The hashing embedder only measures shared characters, so its
"vector" row says nothing about semantic retrieval; use --embedder qwen for that.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from corpus import COURSES, load_course  # noqa: E402
# The rankers the app itself uses (retrieval.py), so these numbers describe the shipped code.
from retrieval import BM25, KEYWORD_WEIGHT, chunk_text, cosine, fused_order, ranking  # noqa: E402


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
        # As in hybrid_search: BM25 lists only chunks sharing a term with the question.
        lexical_orders = [ranking(lexical.scores(q), positive=True) for _, q in queries]
        hybrid_orders = [fused_order(a, b) for a, b in zip(vector_orders, lexical_orders)]
        shipped_orders = [fused_order(a, b, weights=[1.0, KEYWORD_WEIGHT]) for a, b in zip(vector_orders, lexical_orders)]
        report.append(dict(course=course['title'], chunks=len(chunks), queries=len(queries),
                           characters=sum(map(len, texts)), embed_model=embedder.model,
                           rankers={'vector': metrics(vector_orders, relevant, cutoffs),
                                    'bm25': metrics(lexical_orders, relevant, cutoffs),
                                    'hybrid_rrf': metrics(hybrid_orders, relevant, cutoffs),
                                    f'hybrid_rrf_w{KEYWORD_WEIGHT}': metrics(shipped_orders, relevant, cutoffs)}))
    return report


def texts(examples=8, seed=7):
    """How many texts one evaluation embeds: every chunk of both chapters and every question."""
    total = 0
    for name in COURSES:
        course = load_course(name, examples, seed)
        total += sum(len(chunk_text(m['content'])) for point in course['points'] for m in point['materials'])
        total += sum(len(point['queries']) for point in course['points'])
    return total


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
        print(f'将调用真实嵌入模型 {embedder.model}（{texts(args.examples, args.seed)} 段文本）。')
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
