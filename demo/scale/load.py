"""Load the scale corpus and a synthetic class into the demo database, or remove them again.

    .venv/bin/python demo/scale/load.py                 # two chapter-sized courses + 2 × 40 students
    .venv/bin/python demo/scale/load.py --students 12 --examples 4
    .venv/bin/python demo/scale/load.py --remove        # delete what this script created

Everything it creates is marked (course keys scale_*, material sources corpus:scale-v1/…,
student subtitles starting with 合成·), so removal never touches other data. Students with
saved lectures are kept on removal and listed, because lectures reference them.
All students are synthetic; states and evidence are sampled, not observed.
"""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import random
import sys

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from psycopg.types.json import Jsonb  # noqa: E402

from corpus import COURSES, VERSION, characters, load_course  # noqa: E402
from profiles import record_evidence  # noqa: E402

MARK = '合成·'
RECORDER = '合成数据脚本'
SURNAMES = '王李张刘陈杨黄赵吴周徐孙马朱胡郭何林高罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢'
GIVEN = ['子涵', '雨桐', '浩然', '欣怡', '宇轩', '诗涵', '梓萱', '一鸣', '思远', '佳怡', '明轩', '可馨', '俊杰', '晨曦', '嘉懿',
         '若曦', '博文', '语嫣', '天佑', '静怡', '睿哲', '梦瑶', '志强', '晓雯', '文博', '雅琪', '瑞霖', '心怡', '昊天', '婉清']
LEVELS = [('基础待巩固', 0.30, -0.9), ('常规水平', 0.45, 0.0), ('学有余力', 0.25, 0.9)]
TRAITS = ['需要每一步都有理由，先用具体数理解再抽象', '计算较快但容易跳步，检验意识弱', '愿意提问，口头表达清楚，书写不规范',
          '遇到文字题容易读不懂条件', '概念记得住，变式题迁移困难', '注意力集中时间短，适合分段练习',
          '基础扎实，喜欢追问为什么', '怕出错，不敢尝试新方法']
TEMPLATES = {
    'needs_support': ('「{point}」课堂练习 {n} 题', '{n} 题中做对 {k} 题；说不出步骤的依据，出错集中在本知识点的基本操作上。'),
    'partial': ('「{point}」作业 {n} 题（含 2 道变式）', '基础题全部正确，变式题做对 {k} 题；能说出部分理由，但条件变化后容易出错。'),
    'mastered': ('「{point}」小测 {n} 题', '{n} 题全部正确，并能口述每一步的依据；检验过程完整。'),
}


def install(conn, students=40, examples=8, seed=7):
    """Idempotent per course. Returns a summary; the caller owns the transaction."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    summary = dict(version=VERSION, courses=[], students=0, evidence=0)
    used = {row['name'] for row in conn.execute('SELECT name FROM students')}
    for name in COURSES:
        course = load_course(name, examples, seed)
        if conn.execute('SELECT 1 FROM course_tasks WHERE key=%s', (course['key'],)).fetchone():
            summary['courses'].append(dict(key=course['key'], skipped='已存在，未改动'))
            continue
        conn.execute('LOCK TABLE knowledge_points, materials, students, course_tasks IN EXCLUSIVE MODE')
        knowledge_ids = []
        for point in course['points']:
            kid = conn.execute('''INSERT INTO knowledge_points(id,name)
                VALUES ((SELECT COALESCE(max(id),0)+1 FROM knowledge_points),%s) RETURNING id''', (point['name'],)).fetchone()['id']
            knowledge_ids.append(kid)
            for material in point['materials']:
                conn.execute('''INSERT INTO materials (id,knowledge_id,title,content,source_title,source_url,section)
                    VALUES ((SELECT COALESCE(max(id),0)+1 FROM materials),%s,%s,%s,%s,%s,%s)''',
                             (kid, material['title'], material['content'], material['source_title'],
                              material['source_url'], material['section']))
        payload = {key: course[key] for key in ('stage', 'course', 'chapter', 'title', 'goal', 'task_level', 'lesson_plan')}
        payload.update(knowledge_names=[p['name'] for p in course['points']], knowledge_ids=knowledge_ids, student_ids=[],
                       preparation_copy=True, custom=True, length_range=[600, 1000], check_version='general-text-check-v1',
                       scale_corpus=VERSION)
        conn.execute('INSERT INTO course_tasks(key,payload) VALUES (%s,%s)', (course['key'], Jsonb(payload)))
        created = evidence = 0
        for _ in range(students):
            person = rng.choice(SURNAMES) + rng.choice(GIVEN)
            while person in used:
                person = rng.choice(SURNAMES) + rng.choice(GIVEN)
            used.add(person)
            level, _, ability = rng.choices(LEVELS, weights=[w for _, w, _ in LEVELS])[0]
            sid = conn.execute('''INSERT INTO students(id,name,subtitle,profile)
                VALUES ((SELECT COALESCE(max(id),0)+1 FROM students),%s,%s,%s) RETURNING id''',
                               (person, f'{MARK}{course["stage"]} · {level}',
                                f'合成学生，用于规模测试，不对应真实个人。{course["course"]}，{level}；{rng.choice(TRAITS)}。')).fetchone()['id']
            created += 1
            for position, (kid, point) in enumerate(zip(knowledge_ids, course['points'])):
                if rng.random() < 0.15:
                    continue  # no evidence: the profile keeps "unknown", as real rosters do
                # Later points of a chapter are harder; ability shifts the whole curve.
                score = ability - 1.2 * position / len(knowledge_ids) + rng.gauss(0.3, 0.7)
                state = 'mastered' if score > 0.45 else 'partial' if score > -0.45 else 'needs_support'
                task, performance = TEMPLATES[state]
                n = rng.choice([4, 5, 6])
                k = n if state == 'mastered' else rng.randint(1, 2) if state == 'partial' else rng.randint(0, max(1, n // 3))
                record_evidence(conn, sid, dict(
                    confirmed=True, expected_revision=0, knowledge_id=kid, decision='update', new_state=state,
                    source=rng.choice(['homework', 'quiz', 'observation']), recorder=RECORDER,
                    task_content=task.format(point=point['name'], n=n), performance=performance.format(n=n, k=k),
                    reason='合成数据：按抽样得到的表现设定状态，仅用于规模测试。',
                    observed_at=(now - timedelta(days=rng.randint(1, 75), hours=rng.randint(0, 9))).isoformat()))
                evidence += 1
        summary['courses'].append(dict(key=course['key'], title=course['title'], knowledge_points=len(knowledge_ids),
                                       materials=sum(len(p['materials']) for p in course['points']),
                                       characters=characters(course), students=created, evidence=evidence))
        summary['students'] += created
        summary['evidence'] += evidence
    return summary


def remove(conn):
    """Delete scale data. Anything a saved lecture, run or quiz still references is kept and reported."""
    kept = []
    tasks = conn.execute("SELECT key,payload FROM course_tasks WHERE payload->>'scale_corpus'=%s", (VERSION,)).fetchall()
    synthetic = [r['id'] for r in conn.execute('SELECT id FROM students WHERE subtitle LIKE %s AND profile LIKE %s',
                                               (MARK + '%', '合成学生，用于规模测试%'))]
    busy = {r['student_id'] for r in conn.execute('''SELECT student_id FROM lectures WHERE student_id=ANY(%s)
        UNION SELECT student_id FROM generation_runs WHERE student_id=ANY(%s)
        UNION SELECT student_id FROM quiz_jobs WHERE student_id=ANY(%s)''', (synthetic, synthetic, synthetic))}
    free = [sid for sid in synthetic if sid not in busy]
    if busy:
        kept.append(f'{len(busy)} 名合成学生已有讲义、生成记录或题组，未删除。')
    evidence = [r['id'] for r in conn.execute('SELECT id FROM learning_evidence WHERE student_id=ANY(%s)', (free,))]
    conn.execute('UPDATE student_knowledge SET evidence_id=NULL WHERE student_id=ANY(%s)', (free,))
    conn.execute('DELETE FROM knowledge_state_history WHERE evidence_id=ANY(%s)', (evidence,))
    conn.execute('DELETE FROM learning_evidence WHERE id=ANY(%s)', (evidence,))
    conn.execute('DELETE FROM student_knowledge WHERE student_id=ANY(%s)', (free,))
    conn.execute('DELETE FROM student_profile_history WHERE student_id=ANY(%s)', (free,))
    conn.execute('DELETE FROM students WHERE id=ANY(%s)', (free,))
    removed_tasks = 0
    for task in tasks:
        ids = task['payload']['knowledge_ids']
        if conn.execute('''SELECT 1 FROM student_knowledge WHERE knowledge_id=ANY(%s)
                UNION ALL SELECT 1 FROM learning_evidence WHERE knowledge_id=ANY(%s)
                UNION ALL SELECT 1 FROM knowledge_aliases WHERE canonical_id=ANY(%s) OR knowledge_id=ANY(%s)
                LIMIT 1''', (ids, ids, ids, ids)).fetchone():
            kept.append(f'备课副本 {task["key"]} 的知识点仍被学生画像或其他副本使用，未删除。')
            continue
        conn.execute('DELETE FROM material_chunks WHERE knowledge_id=ANY(%s)', (ids,))
        conn.execute('DELETE FROM materials WHERE knowledge_id=ANY(%s) AND source_url LIKE %s', (ids, f'corpus:{VERSION}/%'))
        conn.execute('DELETE FROM course_tasks WHERE key=%s', (task['key'],))
        if not conn.execute('SELECT 1 FROM materials WHERE knowledge_id=ANY(%s) LIMIT 1', (ids,)).fetchone():
            conn.execute('DELETE FROM knowledge_points WHERE id=ANY(%s)', (ids,))
        removed_tasks += 1
    return dict(removed_students=len(free), removed_courses=removed_tasks, kept=kept)


def main():
    import json
    import psycopg
    from dotenv import load_dotenv
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--students', type=int, default=40, help='每门课的合成学生数（默认 40）')
    parser.add_argument('--examples', type=int, default=8, help='每个知识点的计算例题数（默认 8，0 表示只装载讲解）')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--remove', action='store_true', help='删除本脚本创建的数据')
    args = parser.parse_args()
    if not 0 <= args.students <= 200 or not 0 <= args.examples <= 40:
        raise SystemExit('学生数请填 0—200，例题数请填 0—40。')
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    dsn = os.environ.get('DATABASE_URL', 'postgresql:///lecture_demo_prototype')
    if conninfo_to_dict(dsn).get('dbname') != 'lecture_demo_prototype':
        raise SystemExit('只允许写入专用数据库 lecture_demo_prototype。')
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        result = remove(conn) if args.remove else install(conn, args.students, args.examples, args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
