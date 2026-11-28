"""How well does a teacher's free-text focus find a lesson's knowledge points? (retrieval.suggest_points)

    .venv/bin/python demo/scale/eval_point_suggest.py                         # offline hashing vectors
    .venv/bin/python demo/scale/eval_point_suggest.py --embedder qwen         # the configured embedding model
    .venv/bin/python demo/scale/eval_point_suggest.py --embedder qwen --sweep # the rule's parameters, tuned on dev

Two sets, neither written for this feature:
- the evalset's 12 lessons (scale/evalset.json, written 2026-11-17): the focus is the lesson's title and goal,
  the answer its ticked knowledge points. The courses are the synthetic scale corpus, installed in a
  disposable schema as evalset.py installs them. dev tunes the rule; holdout only reports it.
- the real course 2 (大学概率论, 97 points): the 57 labelled questions of experiments/2026-11-26-rag. Its
  library and material are read in a read-only transaction and copied into a disposable schema; nothing is
  written to the course's own tables.
Per focus: precision and recall of the ticked points, whether every answer point was ticked (all), whether
every answer point is at least listed for the teacher (listed), and how many points were ticked. Real
embeddings are cached under .runtime/rag-eval/ (git-ignored), so a rerun calls the service only for new text.
"""
import argparse
from contextlib import contextmanager
import itertools
import json
import os
from pathlib import Path
import statistics
import sys
from unittest.mock import patch

SCALE = Path(__file__).resolve().parent
DEMO = SCALE.parent
ROOT = DEMO.parent
sys.path[:0] = [str(SCALE), str(DEMO), str(DEMO / 'tests')]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

import retrieval  # noqa: E402
from classes import LIBRARY  # noqa: E402
from eval_course_rag import QUESTIONS, CachedEmbedder  # noqa: E402

OFFSET = 1_000_000      # the real course's ids, moved clear of whatever the fixture schema holds
GRID = dict(share=(0.3, 0.4, 0.5, 0.6, 0.7), depth=(15, 30, 50), limit=(4, 6, 8))


def scores(ticked, listed, answer):
    ticked, answer = set(ticked), set(answer)
    hit = ticked & answer
    return dict(precision=len(hit) / len(ticked) if ticked else 0.0, recall=len(hit) / len(answer),
                all=answer <= ticked, listed=answer <= set(listed), ticked=len(ticked))


def summarize(rows):
    found = {key: round(statistics.mean(float(row[key]) for row in rows), 3)
             for key in ('precision', 'recall', 'all', 'listed', 'ticked')}
    p, r = found['precision'], found['recall']
    return dict(found, f1=round(2 * p * r / (p + r), 3) if p + r else 0.0, n=len(rows))


@contextmanager
def rule(share=None, depth=None, limit=None):
    """The suggestion rule's parameters for a while (retrieval.SUGGEST_*)."""
    values = dict(SUGGEST_SHARE=share, SUGGEST_DEPTH=depth, SUGGEST_MAX=limit)
    patches = [patch.object(retrieval, name, value) for name, value in values.items() if value is not None]
    for item in patches:
        item.start()
    try:
        yield
    finally:
        for item in patches:
            item.stop()


@contextmanager
def schema():
    """A disposable schema, the one the course-and-job tests use."""
    from test_courses_jobs import CourseJobTests
    CourseJobTests.setUpClass()
    fixture = CourseJobTests()
    try:
        fixture.setUp()
        yield fixture
    finally:
        fixture.tearDown()
        CourseJobTests.tearDownClass()


def evalset_libraries(fixture, data, cases):
    """The synthetic courses of the cases, installed as evalset.py installs them: {course name: library}."""
    from classes import ensure_course_structure, library_ids
    from corpus import load_course
    from load import install
    libraries = {}
    with fixture.fixture_db() as conn:
        install(conn, students=2, examples=data['examples_per_point'], seed=data['seed'])
        ensure_course_structure(conn, presets=False)
        for name in sorted({case['course'] for case in cases}):
            key = load_course(name, 0)['key']
            course_id = conn.execute('SELECT payload FROM course_tasks WHERE key=%s', (key,)).fetchone()['payload']['course_id']
            rows = conn.execute('''SELECT id, name, chapter, position FROM knowledge_points WHERE id = ANY(%s)
                ORDER BY position, id''', (library_ids(conn, course_id),)).fetchall()
            libraries[name] = [dict(row) for row in rows]
    return libraries


def real_library(fixture, dsn, course_id):
    """Course course_id's library and material, read only, copied into the fixture's schema under OFFSET."""
    with psycopg.connect(dsn, row_factory=dict_row, options='-c default_transaction_read_only=on') as conn:
        points = conn.execute(f'SELECT k.id, k.name, k.chapter, k.position {LIBRARY} ORDER BY k.position, k.id',
                              (course_id,)).fetchall()
        materials = conn.execute('''SELECT id, knowledge_id, title, content, source_title, source_url, section
            FROM materials WHERE knowledge_id = ANY(%s) AND class_id IS NULL ORDER BY id''',
                                 ([point['id'] for point in points],)).fetchall()
    with fixture.fixture_db() as conn:
        for point in points:
            conn.execute('INSERT INTO knowledge_points (id, name, chapter, position) VALUES (%s,%s,%s,%s)',
                         (point['id'] + OFFSET, point['name'], point['chapter'] or '', point['position']))
        for row in materials:
            conn.execute('''INSERT INTO materials (id, knowledge_id, title, content, source_title, source_url, section)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''', (row['id'] + OFFSET, row['knowledge_id'] + OFFSET, row['title'],
                                                  row['content'], row['source_title'], row['source_url'], row['section'] or ''))
    return [dict(point, id=point['id'] + OFFSET) for point in points], len(materials)


def index(fixture, embedder, libraries):
    if embedder is None:
        return
    with fixture.fixture_db() as conn:
        for library in libraries:
            retrieval.index_knowledge(conn, embedder, [point['id'] for point in library])


def run_focuses(fixture, embedder, focuses):
    """focuses: [(id, text, library, answer ids, chapter)] -> one row each."""
    rows = []
    with fixture.fixture_db() as conn:
        for identity, text, library, answer, chapter in focuses:
            found = retrieval.suggest_points(conn, embedder, text, library=library, chapter=chapter,
                                             vector_note=None if embedder else '离线评测，只按关键词。')
            listed = [point['knowledge_id'] for point in found['points']]
            names = {point['id']: point['name'] for point in library}
            rows.append(dict(id=identity, text=text, **scores(found['suggested'], listed, answer),
                             answer=[names[k] for k in answer], suggested=[names[k] for k in found['suggested']],
                             warnings=found['warnings'], backend=found['backend']))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--embedder', choices=['hash', 'qwen', 'none'], default='hash',
                        help='hash：离线哈希向量；qwen：已配置的嵌入模型（有缓存）；none：只按关键词')
    parser.add_argument('--course', type=int, default=2, help='真实课程的编号（默认 2，大学概率论）')
    parser.add_argument('--no-course', action='store_true', help='不评测真实课程，只评测 evalset')
    parser.add_argument('--chapter', action='store_true', help='evalset 的课给出本课所在章节（课程设定的章节）')
    parser.add_argument('--sweep', action='store_true', help=f'在 dev 上比较规则参数 {GRID}，再报告 holdout')
    parser.add_argument('--rule', nargs=3, type=float, metavar=('SHARE', 'DEPTH', 'LIMIT'),
                        help='用这组规则参数评测，例如 --rule 0.6 15 6（默认用 retrieval 里的 SUGGEST_*）')
    parser.add_argument('--json', help='把结果另存为 JSON 文件')
    args = parser.parse_args()
    if args.embedder == 'qwen':
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        from embed import QwenEmbedder
        base = QwenEmbedder()
        if not base.configured():
            raise SystemExit('嵌入模型尚未配置，请先在系统设置或 .env 中填写。')
        embedder = CachedEmbedder(base)
        print(f'将调用真实嵌入模型 {base.model}；已缓存的文本不再调用。')
    elif args.embedder == 'hash':
        from embed import HashingEmbedder
        embedder = HashingEmbedder(1024)
        print('使用离线哈希向量：只反映字面重合，不代表语义检索效果。')
    else:
        embedder = None
    data = json.loads((SCALE / 'evalset.json').read_text(encoding='utf-8'))
    chosen = rule(args.rule[0], int(args.rule[1]), int(args.rule[2])) if args.rule else rule()
    chosen.__enter__()
    report = dict(embedder=getattr(embedder, 'model', None), rule=dict(share=retrieval.SUGGEST_SHARE,
                  depth=retrieval.SUGGEST_DEPTH, limit=retrieval.SUGGEST_MAX), sets={})
    with schema() as fixture:
        libraries = evalset_libraries(fixture, data, data['cases'])
        index(fixture, embedder, libraries.values())
        focuses = {}
        for case in data['cases']:
            library = libraries[case['course']]
            ids = {point['name']: point['id'] for point in library}
            chapter = None
            if args.chapter:
                chapters = {point['chapter'] for point in library if point['name'] in case['knowledge'] and point['chapter']}
                chapter = min(chapters) if len(chapters) == 1 else None
            focuses.setdefault(case['split'], []).append(
                (case['id'], f"{case['title']}：{case['goal']}", library, [ids[name] for name in case['knowledge']], chapter))
        if args.sweep:
            trials = []
            for share, depth, limit in itertools.product(*GRID.values()):
                with rule(share, depth, limit):
                    trials.append(dict(share=share, depth=depth, limit=limit,
                                       dev=summarize(run_focuses(fixture, embedder, focuses['dev']))))
            trials.sort(key=lambda trial: (-trial['dev']['f1'], -trial['dev']['all'], abs(trial['share'] - 0.5),
                                           abs(trial['depth'] - 30), abs(trial['limit'] - 6)))
            best = trials[0]
            with rule(best['share'], best['depth'], best['limit']):
                best['holdout'] = summarize(run_focuses(fixture, embedder, focuses['holdout']))
            report['sweep'] = dict(best=best, top=trials[:8])
            print('dev 上最好的参数：' + json.dumps({k: best[k] for k in ('share', 'depth', 'limit')}) +
                  f"  dev {best['dev']}  holdout {best['holdout']}")
        for split in ('dev', 'holdout'):
            rows = run_focuses(fixture, embedder, focuses[split])
            report['sets'][f'evalset-{split}'] = dict(summary=summarize(rows), rows=rows)
    if not args.no_course:
        questions = json.loads(Path(QUESTIONS).read_text(encoding='utf-8'))['questions']
        dsn = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
        with schema() as fixture:
            library, materials = real_library(fixture, dsn, args.course)
            index(fixture, embedder, [library])
            rows = run_focuses(fixture, embedder, [(q['id'], q['text'], library, [k + OFFSET for k in q['relevant']], None)
                                                   for q in questions])
        report['sets'][f'course{args.course}-questions'] = dict(summary=summarize(rows), rows=rows,
                                                               library=len(library), materials=materials)
    print(f"规则：前 {report['rule']['depth']} 个相关片段投票，达到最高票 {report['rule']['share']:.0%} 的知识点勾选，"
          f"最多 {report['rule']['limit']} 个；嵌入：{report['embedder'] or '无（只按关键词）'}")
    print('  ' + '评测集'.ljust(22) + ''.join(c.rjust(10) for c in ('n', 'precision', 'recall', 'f1', 'all', 'listed', 'ticked')))
    for name, found in report['sets'].items():
        s = found['summary']
        print('  ' + name.ljust(22) + str(s['n']).rjust(10) + ''.join(f"{s[c]:.3f}".rjust(10)
                                                                     for c in ('precision', 'recall', 'f1', 'all', 'listed', 'ticked')))
    for name, found in report['sets'].items():
        if name.startswith('evalset'):
            for row in found['rows']:
                print(f"  {row['id']} 答案 {'、'.join(row['answer'])} | 勾选 {'、'.join(row['suggested']) or '无'}"
                      + (f" | 提醒 {len(row['warnings'])} 条" if row['warnings'] else ''))
    if getattr(embedder, 'calls', None) is not None:
        print(f'本次新嵌入 {embedder.calls} 段文本。')
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'结果已保存到 {args.json}')


if __name__ == '__main__':
    main()
