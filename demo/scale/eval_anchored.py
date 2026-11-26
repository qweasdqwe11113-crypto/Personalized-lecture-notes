"""The anchored material mode on a real course, without generating anything: lessons whose material is over
the budget, and the passages a learner's error adds from elsewhere in the course (never from later chapters).

    .venv/bin/python demo/scale/eval_anchored.py --embedder qwen

Course 2's library and material are read in a read-only transaction and copied into a disposable schema
(eval_point_suggest.real_library). Each scenario is a lesson of real points, a goal, and a learner note
written for it; the report says what the model would receive: which of the lesson's material is whole or cut,
whether every point keeps something, and which passages the note adds and from which chapter. Each learner
scenario is run a second time without the chapter boundary, to show what the boundary keeps out.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

SCALE = Path(__file__).resolve().parent
sys.path[:0] = [str(SCALE), str(SCALE.parent), str(SCALE.parent / 'tests')]

import retrieval  # noqa: E402
from eval_point_suggest import ROOT, CachedEmbedder, index, real_library, schema  # noqa: E402
from lecture_policy import MATERIAL_BUDGET  # noqa: E402

# (name, lesson points by name, goal, learner note). Notes name errors a teacher would record after marking.
SCENARIOS = [
    ('超出上限：第二章六种分布', ['泊松分布（Poisson）', '泊松定理（二项分布的泊松近似）', '均匀分布（Uniform）',
                         '指数分布（Exponential）', '正态分布（Normal）', '标准正态分布与标准化'],
     '比较泊松、均匀、指数和正态分布的定义、期望方差与适用情形，会用标准化查表计算正态概率', ''),
    ('超出上限：蒙特卡洛整章', ['蒙特卡洛', '蒙特卡洛方法的基本思想'],
     '理解蒙特卡洛方法用随机模拟估计概率和积分的思想，会写模拟步骤并估计误差', ''),
    ('学情来自前面的章节：假设检验', ['原假设与备择假设', '第I类错误与第II类错误', 'p值的定义与决策规则'],
     '能写出原假设与备择假设，解释两类错误和 p 值的含义并做出检验决策',
     '该生把 P(拒绝H0|H0为真) 和 P(H0为真|拒绝H0) 当成一回事，条件概率的方向弄反，还以为 p 值就是原假设成立的概率'),
    ('学情指向后面的章节：二项与泊松', ['二项分布', '泊松定理（二项分布的泊松近似）'],
     '能写出二项分布并计算概率，会在 n 大 p 小时用泊松分布近似二项分布',
     '该生用正态分布近似二项分布时忘了连续性修正，n 很大时不知道该用泊松近似还是正态近似'),
    ('学情落在本课资料里：期望与方差', ['数学期望的定义与计算', '方差与标准差', '期望与方差的性质'],
     '能计算离散和连续随机变量的期望与方差，并用期望与方差的性质简化计算',
     '该生把 Var(aX+b) 写成 a·Var(X)+b，也说不清 E(X²) 和 [E(X)]² 的区别'),
]


def describe(found, materials, library):
    """What one anchored_search gave, in names and chapters."""
    by_id = {point['id']: point for point in library}
    lesson = set(found['selected_knowledge_ids'])
    own = [item for item in found['items'] if item.get('anchor')]
    extra = [item for item in found['items'] if not item.get('anchor')]
    covered = {item['knowledge_id'] for item in own} if found['base']['kind'] == 'chunks' else lesson
    return dict(
        base_characters=found['base']['characters'], kind=found['base']['kind'],
        given_characters=sum(len(row['content']) for row in materials), materials=len(materials),
        excerpts=len(own), points_without_material=[by_id[k]['name'] for k in lesson - covered],
        supplements=[dict(key=item['key'], point=by_id[item['knowledge_id']]['name'],
                          chapter=by_id[item['knowledge_id']]['chapter'], heading=item['heading'], score=item['score'],
                          excerpt=item['content'][:80]) for item in extra],
        learner_in_lesson=[row['title'] for row in materials if row['id'] in set(found['learner_in_lesson'])],
        later_points=found['boundary']['later_points'], backend=found['backend'])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--embedder', choices=['hash', 'qwen'], default='hash')
    parser.add_argument('--course', type=int, default=2)
    parser.add_argument('--json', help='把结果另存为 JSON 文件')
    args = parser.parse_args()
    if args.embedder == 'qwen':
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        from embed import QwenEmbedder
        embedder = CachedEmbedder(QwenEmbedder())
    else:
        from embed import HashingEmbedder
        embedder = HashingEmbedder(1024)
    dsn = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
    report = dict(embedder=embedder.model, budget=MATERIAL_BUDGET, scenarios=[])
    with schema() as fixture:
        from test_courses_jobs import module
        library, _ = real_library(fixture, dsn, args.course)
        index(fixture, embedder, [library])
        ids = {point['name']: point['id'] for point in library}
        everything = [point['id'] for point in library]
        for name, points, goal, note in SCENARIOS:
            lesson = [ids[point] for point in points]
            plan = retrieval.plan_queries(goal, points, (), note, personal=bool(note))
            entry = dict(name=name, points=points, goal=goal, note=note)
            with fixture.fixture_db() as conn:
                rows = module.scoped_materials(conn, lesson, None)
                found, materials = retrieval.anchored_search(conn, embedder, plan, lesson_rows=rows, lesson_ids=lesson,
                                                             scope_ids=everything, budget=MATERIAL_BUDGET)
                entry['anchored'] = describe(found, materials, library)
                if note:
                    with patch.object(retrieval, 'later_points', lambda *a, **k: set()):
                        found, materials = retrieval.anchored_search(conn, embedder, plan, lesson_rows=rows, lesson_ids=lesson,
                                                                     scope_ids=everything, budget=MATERIAL_BUDGET)
                    entry['without_boundary'] = describe(found, materials, library)
            report['scenarios'].append(entry)
    for entry in report['scenarios']:
        a = entry['anchored']
        print(f"\n## {entry['name']}：{'、'.join(entry['points'])}")
        print(f"  本课资料 {a['base_characters']} 字 → {'全文' if a['kind'] == 'materials' else '节选'}；给模型 {a['given_characters']} 字"
              f"（上限 {report['budget']}），{a['materials']} 份" + (f"，节选 {a['excerpts']} 段" if a['kind'] == 'chunks' else '')
              + (f"；没有资料的知识点：{'、'.join(a['points_without_material'])}" if a['points_without_material'] else '；每个知识点都有资料'))
        if entry['note']:
            print(f"  学情：{entry['note']}")
            print(f"  后面的章节排除 {a['later_points']} 个知识点；命中本课资料：{'、'.join(a['learner_in_lesson']) or '无'}")
            for label, found in (('补充', a), ('（不设章节边界时）', entry['without_boundary'])):
                print(f"  {label}：" + ('；'.join(f"{s['point']}（{s['chapter'] or '未分章'}）" for s in found['supplements']) or '无'))
    if getattr(embedder, 'calls', None) is not None:
        print(f'\n本次新嵌入 {embedder.calls} 段文本。')
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'结果已保存到 {args.json}')


if __name__ == '__main__':
    main()
