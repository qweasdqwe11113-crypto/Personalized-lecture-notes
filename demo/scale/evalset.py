"""Fixed evaluation set for the default lesson-design pipeline (demo/scale/evalset.json).

    .venv/bin/python demo/scale/evalset.py                            # dry run of the dev split: request sizes only
    .venv/bin/python demo/scale/evalset.py --split holdout            # dry run of the held-out lessons
    .venv/bin/python demo/scale/evalset.py --cases L1 L2 P1 P2 --real # the configured model (real calls, real cost)
    .venv/bin/python demo/scale/evalset.py --cases L1 L3 P1 P3 --modes profile none --real   # with and without the profile
    .venv/bin/python demo/scale/evalset.py --cases L1 P1 --retrieval hybrid none --real      # how the material is supplied

Like bench.py it works in a disposable schema of the dedicated database, dropped afterwards. It
loads the scale corpus and its synthetic students, files the corpus under one course per subject
the way the service does on start (classes.ensure_course_structure), puts each course's students
into one class, and then goes through the page's own routes: each case is saved as a lesson with
its points ticked from the course library (/api/course-tasks), and submitted to /api/jobs with
generation_mode designed-v1 and no design review, so nothing waits for a teacher.

The sentences that mark the synthetic students for removal (「合成学生，用于规模测试……」) are taken
out in that schema, so the model reads the profiles the way it would read a teacher's notes.

Rules: tune prompts and checks on the dev split only. The holdout split is run before a release and
never tuned against; once it is, it stops being a holdout.

--retrieval chooses how the lesson gets its material: sql (the ticked points' material, the default),
hybrid (whole-course retrieval, retrieval.hybrid_search) or none (no material, as a control). A dry run
of hybrid ranks with offline hashing vectors; a real run embeds the corpus with the configured model.

Results go to .runtime/evalset/<time>/ under the data folder (ignored by git): each lesson, its design
and the material it was given, results.json and summary.md. With two modes or retrieval conditions,
blind/ holds each case's lessons as A, B, …, the key in a separate file and a scoring sheet for
teachers (the dimensions of quality_baseline.py --sheet).
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import random
import re
import statistics
import sys
import time
from unittest.mock import patch

SCALE = Path(__file__).resolve().parent
DEMO = SCALE.parent
sys.path[:0] = [str(SCALE), str(DEMO), str(DEMO / 'tests')]

import design as D  # noqa: E402
from edition import data_dir  # noqa: E402

SET = SCALE / 'evalset.json'
REAL = 'real'
DETAIL = 'standard'
# Measured with the configured model on 2026-11-17 (design_compare.py, three saved lessons): 57k–98k
# tokens for a 40–45 minute lesson. Used only for the estimate printed before a real run.
TOKENS_PER_45_MINUTES = 85000
CONFIRM_ABOVE = 600000
MODES = {'profile': '有画像', 'class': '班级画像', 'none': '无画像'}
RETRIEVALS = {'sql': '所选知识点全部材料', 'anchored': '本课知识点全部资料 + 整门课补充', 'hybrid': '整门课检索',
              'none': '不提供材料'}
RANKED = {'anchored', 'hybrid'}     # the conditions that rank chunks with the embedding model
# What a reminder is about, by the wording of design.validate and designed.inspect_prose; first match wins.
CATEGORIES = [
    (re.escape(D.ARITHMETIC), '算式复算'),
    (re.escape(D.TRIMMED), '超长截断'),
    (r'各环节合计', '时间合计'),
    (r'却只有 \d+ 分钟', '题量与时间'),
    (r'重点环节', '重点环节数'),
    (r'「布置作业」占了', '作业时间'),
    (r'一对一辅导', '一对一措辞'),
    (r'第 \d+ 条目标', '目标与检验题'),
    (r'同一组数据', '题目重复'),
    (r'plan_links|本次讲解安排', '讲解安排落位'),
    (r'的图|figures', '图示'),
    (r'关于数据或生成过程|正文写了「', '数据与过程话术'),
    (r'没写完', '句子不完整'),
    (r'英文缩写', '英文缩写'),
    (r'答案和步骤写', '前后数值不一致'),
    (r'道（授课设置）', '题量不符'),
    (r'learner 应为空', '无画像时猜学情'),
    (r'学生姓名', '学生姓名'),
    (r'两种写法', '符号写法'),
    (r'缺少小结', '缺少小结'),
    (r'一整段解答', '答案写成解答'),
    (r'留了空却没有写答案', '学案笔记'),
    (r'个汉字，超过', '环节过长'),
    (r'篇幅只有', '环节过短'),
    (r'重抄了提问', '重抄提问'),
    (r'重抄了', '重抄题干'),
    (r'不要复述学情', '复述学情或提到设计'),
    (r'正文提到「提问', '引用别的提问'),
    (r'表达需要改', '表达'),
    (r'无效或重复|之一|minutes 应为|不存在的|没有放进任何环节|重复使用|JSON|缺少 focus|标题|字符，请精简', '结构'),
]


def category(problem):
    return next((label for pattern, label in CATEGORIES if re.search(pattern, problem)), '其他')


def load_set():
    return json.loads(SET.read_text(encoding='utf-8'))


def pick(data, split='dev', ids=None):
    cases = data['cases']
    if ids:
        unknown = [identity for identity in ids if identity not in {case['id'] for case in cases}]
        if unknown:
            raise ValueError('评测集中没有这些课：' + '、'.join(unknown))
        return [case for case in cases if case['id'] in ids]
    return [case for case in cases if split == 'all' or case['split'] == split]


def estimate(cases, modes):
    return sum(round(TOKENS_PER_45_MINUTES * case['duration'] / 45) for case in cases) * len(modes)


def expect(response, status, what):
    if response.status_code != status:
        raise RuntimeError(f'{what}失败（{response.status_code}）：{(response.get_json(silent=True) or {}).get("error", "")}')
    return response.get_json()


def prepare(fixture, data, cases, students, subjects=True, quick=False):
    """Corpus, courses, one class per course, one saved lesson per case. Returns {case id: lesson}.
    subjects: give each course the subject evalset.json names, the way a teacher sets it on the course page.
    quick: make each lesson with the one-click flow (quick_lesson) instead of ticking the case's points."""
    from classes import ensure_course_structure, library_ids
    from corpus import load_course
    from load import MARK, install
    courses = {}
    with fixture.fixture_db() as conn:
        install(conn, students=students, examples=data['examples_per_point'], seed=data['seed'])
        ensure_course_structure(conn, presets=False)
        for name in sorted({case['course'] for case in cases}):
            corpus = load_course(name, 0)
            course_id = conn.execute('SELECT payload FROM course_tasks WHERE key=%s', (corpus['key'],)).fetchone()['payload']['course_id']
            shelf = library_ids(conn, course_id)
            names = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM knowledge_points WHERE id=ANY(%s)', (shelf,))}
            people = conn.execute('SELECT id,subtitle FROM students WHERE subtitle LIKE %s ORDER BY id',
                                  (f'{MARK}{corpus["stage"]} · %',)).fetchall()
            info = conn.execute('SELECT name,stage FROM courses WHERE id=%s', (course_id,)).fetchone()
            courses[name] = dict(id=course_id, chapter=corpus['chapter'], library=[(k, names[k]) for k in shelf],
                                 students=[(row['id'], row['subtitle'].rsplit(' · ', 1)[-1]) for row in people],
                                 name=info['name'], stage=info['stage'], subject=data['subjects'][name] if subjects else 'general')
        # The markers are there so load.py --remove finds these records; the model should read plain notes.
        conn.execute('''UPDATE students SET profile=regexp_replace(profile, '^合成学生，用于规模测试，不对应真实个人。', ''),
            subtitle=substr(subtitle, %s) WHERE subtitle LIKE %s''', (len(MARK) + 1, MARK + '%'))
    client = fixture.client
    for course in courses.values():
        expect(client.put(f'/api/courses/{course["id"]}', json=dict(name=course['name'], stage=course['stage'],
                                                                     subject=course['subject'])), 200, '设定课程学科')
        made = expect(client.post(f'/api/courses/{course["id"]}/classes', json={'name': '评测班'}), 201, '建立班级')
        course['class_id'] = made['class']['id']
        for sid, _ in course['students']:
            expect(client.post(f'/api/classes/{course["class_id"]}/students', json={'student_id': sid}), 201, '学生入班')
    lessons = {}
    for case in cases:
        course = courses[case['course']]
        ids = {name: kid for kid, name in course['library']}
        missing = [name for name in case['knowledge'] if name not in ids]
        if missing:
            raise RuntimeError(f'{case["id"]} 的知识点不在课程知识点库里：' + '、'.join(missing))
        # Ticked points in library order, the way the page sends them.
        ticked = [kid for kid, name in course['library'] if name in case['knowledge']]
        if quick:
            key, found = quick_lesson(client, course, case, ticked)
        else:
            key, found = expect(client.post('/api/course-tasks', json=dict(
                preparation_copy=True, course_id=course['id'], knowledge_ids=ticked, knowledge_names=[], chapter=course['chapter'],
                title=case['title'], goal=case['goal'], task_level=case['task_level'], lesson_plan='')), 201,
                f'保存备课 {case["id"]}')['key'], None
        lesson = dict(key=key, class_id=None, student_id=None, quick=found)
        if case['target']['kind'] == 'class':
            lesson.update(class_id=course['class_id'], target='全班')
        else:
            wanted = case['target']['level']
            sid, level = next((person for person in course['students'] if person[1] == wanted), course['students'][0])
            lesson.update(student_id=sid, target=f'学生（{level}）')
        lessons[case['id']] = lesson
    return lessons


def quick_lesson(client, course, case, answer):
    """The one-click flow (quick_start.py) for a case: its title and goal as the teacher's focus, every
    suggested point accepted as it is. The lesson keeps the case's title and goal, so only its points differ
    from the ticked lesson. Returns the lesson's key and how the suggestion compares with the case's points."""
    names, focus = dict(course['library']), f"{case['title']}：{case['goal']}"
    where = dict(course_id=course['id'], class_id=course['class_id'])
    found = expect(client.post('/api/quick-lesson/suggest', json=dict(where, text=focus)), 200,
                   f'查找知识点 {case["id"]}')['suggestion']
    if not found['suggested']:
        raise RuntimeError(f'{case["id"]} 没有勾选任何知识点：' + '；'.join(found['warnings']))
    saved = expect(client.post('/api/quick-lesson', json=dict(where, text=case['goal'], title=case['title'],
                                                               knowledge_ids=found['suggested'], suggested_ids=found['suggested'])),
                   201, f'一键备课 {case["id"]}')
    hit = set(found['suggested']) & set(answer)
    return saved['key'], dict(focus=focus, suggested=[names[k] for k in found['suggested']], answer=case['knowledge'],
                              precision=round(len(hit) / len(found['suggested']), 3), recall=round(len(hit) / len(answer), 3),
                              warnings=found['warnings'], backend=found['backend'], embed_model=found['embed_model'])


def job_payload(case, lesson, mode, retrieval='sql', note=''):
    """What the page submits (static/jobs.js generationPayload) for this case. note: the teacher's 备注 for
    one student (learner_note), which also goes into the 针对学情 query; a whole-class lesson has none."""
    options = dict(duration=case['duration'], lesson_type=case['lesson_type'], detail=DETAIL, examples=case['examples'],
                   exercises=case['exercises'], requirements='', target_characters=0, length_policy='duration-v2')
    payload = dict(topic=lesson['key'], goal='', retrieval_mode=retrieval, teaching_options=options, visual_analysis_ids=[],
                   generation_mode='designed-v1', lecture_format='rich-v1', design_review=False)
    if lesson['class_id'] is not None:
        payload.update(class_id=lesson['class_id'], personalization_mode='none' if mode == 'none' else 'class')
    else:
        payload.update(student_id=lesson['student_id'], personalization_mode=mode)
        if note:
            payload['learner_note'] = note
    return payload


def record(case, lesson, payload, row):
    meta = row['meta'] or {}
    stages = meta.get('stages') or []
    designs = [stage for stage in stages if stage['stage'] in ('design', 'design-patch')]
    retry = [stage for stage in designs if stage['attempt'] > 1]
    rewritten = sorted({stage['stage'] for stage in stages if stage not in designs and stage['attempt'] > 1})
    # A failed run's check is of whatever partial output it had; its usage and stages are kept (runs before F3 have none).
    succeeded = row['status'] == 'succeeded'
    report = (row['report'] or {}) if succeeded else {}
    usage = row['usage'] or {}
    materials = row.get('materials') or []
    return dict(
        case=case['id'], split=case['split'], course=case['course'], title=case['title'], target=lesson['target'],
        quick=lesson.get('quick'),
        mode=payload['personalization_mode'], retrieval=payload.get('retrieval_mode', 'sql'),   # the server's default
        materials=len(materials), material_characters=sum(len(m.get('content') or '') for m in materials),
        duration=case['duration'], examples=case['examples'], exercises=case['exercises'],
        status=row['status'], error=row['error_message'], run_id=row['run_id'], prompt_version=row['prompt_version'],
        tokens=usage.get('total_tokens'), calls=usage.get('model_calls'),
        seconds=meta.get('elapsed_seconds') or round(float(row['active'] or 0), 1),
        design_attempts=len(designs), design_problems=designs[0]['problems'] if designs else [],
        # The second design call: a whole new design ('design') or a partial correction ('design-patch', C4).
        design_retry=retry[0]['stage'] if retry else None,
        retry_seconds=sum(stage.get('seconds') or 0 for stage in retry) if retry else None,
        retry_tokens=sum(stage.get('total_tokens') or 0 for stage in retry) if retry else None,
        rewritten=rewritten,
        rewrite_problems={stage['stage']: stage['problems'] for stage in stages if stage['stage'] in rewritten and stage['attempt'] == 1},
        reminders=meta.get('design_reminders') or [],
        warnings=[check['label'] for check in report.get('automatic') or [] if check['status'] != 'pass'],
        citation_support={key: (report.get('citation_support') or {}).get(key) for key in ('checked', 'skipped_language', 'median')}
        | {'suspect': len((report.get('citation_support') or {}).get('suspect') or [])},
        chinese_characters=(report.get('metrics') or {}).get('chinese_characters'))


def tally(records, problems):
    """Category -> number of lessons in which it appears (each lesson counts once per category)."""
    counts = Counter()
    for entry in records:
        counts.update({category(problem) for problem in problems(entry)})
    return '、'.join(f'{name} {count}' for name, count in counts.most_common()) or '无'


def retried_how(entry):
    if entry['design_attempts'] <= 1:
        return '否'
    return '是（局部修正）' if entry.get('design_retry') == 'design-patch' else '是'


def retry_cost(done):
    """What the second design call cost, the part C4 set out to halve."""
    seconds = [entry['retry_seconds'] for entry in done if entry.get('retry_seconds') is not None]
    tokens = [entry['retry_tokens'] for entry in done if entry.get('retry_tokens')]
    if not seconds:
        return ''
    patched = sum(entry.get('design_retry') == 'design-patch' for entry in done)
    return (f'，其中局部修正 {patched} 节；重试那一次的耗时中位数 {round(statistics.median(seconds))} 秒'
            + (f'、tokens 中位数 {round(statistics.median(tokens))}' if tokens else ''))


def summary(records, model, simulated):
    head = ['课', '对象', '方式', '材料', '分钟', '状态', 'tokens', '秒', '调用', '设计重试', '重写环节', '最终提醒', '程序检查未通过', '汉字']
    lines = ['| ' + ' | '.join(head) + ' |', '| ' + ' | '.join(['---'] * 6 + ['---:'] * 3 + ['---'] * 4 + ['---:']) + ' |']
    for entry in records:
        reminders = Counter(category(problem) for problem in entry['reminders'])
        if entry['status'] == 'succeeded':
            cells = [entry['tokens'] or '', entry['seconds'] or '', entry['calls'] or '',
                     retried_how(entry), '、'.join(entry['rewritten']) or '无',
                     '、'.join(f'{name} {count}' for name, count in reminders.items()) or '无',
                     '、'.join(entry['warnings']) or '无', entry['chinese_characters'] or '']
        else:   # what the calls cost is known; the rest belongs to a lesson that was never finished
            cells = [entry['tokens'] or '—', entry['seconds'] or '', entry['calls'] or '—',
                     retried_how(entry) if entry['design_attempts'] else '—', '—', '—', '—', '—']
        lines.append('| ' + ' | '.join(str(value) for value in [
            entry['case'], entry['target'], MODES[entry['mode']],
            f"{RETRIEVALS[entry.get('retrieval', 'sql')]} · {entry.get('materials', '—')} 份", entry['duration'],
            '成功' if entry['status'] == 'succeeded' else '失败：' + (entry['error'] or entry['status'])[:60]] + cells) + ' |')
    done = [entry for entry in records if entry['status'] == 'succeeded']
    failed = len(records) - len(done)
    median = lambda key: round(statistics.median(entry[key] for entry in done)) if done else '—'
    retried = sum(entry['design_attempts'] > 1 for entry in done)
    unknown = sum(entry['tokens'] is None for entry in records if entry['status'] != 'succeeded')
    notes = ([f'含失败的 {failed - unknown} 节'] if failed - unknown else []) + (
        [f'{unknown} 节失败的课没有用量记录'] if unknown else [])
    lines += [
        '',
        f'模型：{"模拟" if simulated else model}；成功 {len(done)}/{len(records)}；'
        f'tokens 合计 {sum(entry["tokens"] or 0 for entry in records)}' + (f'（{"；".join(notes)}）' if notes else '') + '。',
        f'中位数（成功的课）：tokens {median("tokens")}，秒 {median("seconds")}，汉字 {median("chinese_characters")}。',
        f'课堂设计重试（成功的课）：{retried}/{len(done)}' + retry_cost(done) + '。',
        '第一次课堂设计的问题（按类别，数字是出现的课数）：' + tally(records, lambda entry: entry['design_problems']),
        '重写环节的原因（按类别）：' + tally(records, lambda entry: [p for ps in entry['rewrite_problems'].values() for p in ps]),
        '最终仍有的提醒（按类别）：' + tally(records, lambda entry: entry['reminders']),
        '程序检查未通过（按项目）：' + ('、'.join(f'{name} {count}' for name, count in
                                       Counter(w for entry in records for w in entry['warnings']).most_common()) or '无'),
        '',
        *quick_lines(records),
        '以上为程序统计，不能判断讲解是否正确、是否好用；教师评分用 blind/ 里的评分表或 quality_baseline.py --sheet。',
    ]
    return '\n'.join(lines) + '\n'


def quick_lines(records):
    found = {entry['case']: entry['quick'] for entry in records if entry.get('quick')}
    if not found:
        return []
    mean = lambda key: round(statistics.mean(item[key] for item in found.values()), 3)
    return [f'一键备课（自动接受建议）：勾选的知识点对照评测集标注，precision {mean("precision")}，recall {mean("recall")}；'
            + '；'.join(f'{case} 勾选 {"、".join(item["suggested"])}' for case, item in found.items()) + '。']


def variant(entry):
    """One generated version of a case: its mode, and its material condition when that is not sql."""
    retrieval = entry.get('retrieval', 'sql')
    return entry['mode'] if retrieval == 'sql' else f"{entry['mode']}-{retrieval}"


def blind(out, cases, records, modes):
    """Each case's lessons in the compared variants as A, B, ... in a random order; the key goes in its own file."""
    from quality_baseline import SHEET
    folder = out / 'blind'
    folder.mkdir()
    chooser, key = random.SystemRandom(), {}
    sheet = ['# 盲评评分表', '', '每节课有两份讲义（A、B），分别打分，不要打开 key.json。每项 0 分明显不符，1 分部分做到，2 分做到；'
             '「依据」处引一句原文。', '']
    for case in cases:
        found = {variant(entry): entry for entry in records if entry['case'] == case['id'] and entry['status'] == 'succeeded'}
        if len(found) < len(modes):
            continue
        order = list(found)
        chooser.shuffle(order)
        key[case['id']] = {}
        for letter, mode in zip('ABCD', order):
            (folder / f'{case["id"]}-{letter}.md').write_text((out / f'{case["id"]}-{mode}.md').read_text(encoding='utf-8'),
                                                            encoding='utf-8')
            key[case['id']][letter] = mode
        letters = 'ABCD'[:len(order)]
        sheet += [f'## {case["id"]} {case["title"]}（{case["duration"]} 分钟）', '',
                  '| 维度 | 看什么 | ' + ' | '.join(f'{letter} 评分' for letter in letters) + ' | 依据 |',
                  '| --- | --- | ' + ' | '.join('---' for _ in letters) + ' | --- |']
        sheet += [f'| {name} | {what} | ' + ' | '.join('' for _ in letters) + ' |  |' for name, what in SHEET] + ['']
    (folder / 'key.json').write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding='utf-8')
    (folder / '评分表.md').write_text('\n'.join(sheet), encoding='utf-8')
    return key


def say(text):
    print(text, flush=True)


def new_folder(base):
    """A run folder of its own, named by the time: two runs started in the same second get -2, -3 ...
    instead of sharing one, where the later results.json would replace the earlier."""
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    for number in range(1, 100):
        folder = base / (stamp if number == 1 else f'{stamp}-{number}')
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            continue
    raise SystemExit(f'{base} 里同一秒的结果目录太多，请稍后再运行。')


def run(cases, modes=('profile',), model=None, concurrency=3, students=None, out=None, log=say, subjects=True,
        retrievals=('sql',), quick=False, note=''):
    """model: None for a dry run (request sizes only), REAL for the configured model, or a callable that
    stands in for call_qwen(prepared, on_attempt). Returns the records, or the request sizes of a dry run."""
    from test_courses_jobs import CourseJobTests, module
    from jobs import JobQueue
    data = load_set()
    CourseJobTests.setUpClass()
    fixture = CourseJobTests()
    try:
        fixture.setUp()
        offline = None
        if model is None and (quick or RANKED & set(retrievals)):
            # A dry run never calls a service: retrieval and suggestions rank with offline hashing vectors.
            from embed import HashingEmbedder
            offline = patch.object(module, 'EMBEDDER', HashingEmbedder(1024))
            offline.start()
        lessons = prepare(fixture, data, cases, students or data['students_per_course'], subjects, quick)
        jobs = [(case, lessons[case['id']], job_payload(case, lessons[case['id']], mode, retrieval, note))
                for case in cases for mode in modes for retrieval in retrievals]
        if model is None:
            sizes = []
            for case, lesson, payload in jobs:
                _, _, _, context, prepared = module.build_job(payload)
                messages = json.loads(prepared.body)['messages']
                sizes.append(dict(case=case['id'], mode=payload['personalization_mode'], target=lesson['target'],
                                  retrieval=payload['retrieval_mode'],
                                  system=len(messages[0]['content']), user=len(messages[1]['content']),
                                  materials=len(context['materials']), prompt_version=prepared.prompt_version))
                log(f'{case["id"]} {lesson["target"]} · {MODES[payload["personalization_mode"]]} · '
                    f'{RETRIEVALS[payload["retrieval_mode"]]} · {case["duration"]} 分钟：'
                    f'设计请求 系统提示 {sizes[-1]["system"]} 字符，输入 {sizes[-1]["user"]} 字符，'
                    f'材料 {sizes[-1]["materials"]} 条，{prepared.prompt_version}')
            offline and offline.stop()
            return sizes
        if out:
            out = Path(out)
            out.mkdir(parents=True, exist_ok=True)
        else:
            out = new_folder(data_dir() / '.runtime' / 'evalset')
        module.GENERATION_LOCK.set_limit(concurrency)
        queue = JobQueue(lambda: module.db(), module.execute_job, capacity=len(jobs) + 3, workers=concurrency,
                         bulk_capacity=len(jobs) + 8)
        patches = [patch.object(module, 'TASKS', queue)] + ([] if model == REAL else [patch.object(module, 'call_qwen', model)])
        for item in patches:
            item.start()
        try:
            submitted = []
            for case, lesson, payload in jobs:
                job = expect(fixture.client.post('/api/jobs', json=payload), 202, f'提交 {case["id"]}')
                submitted.append((case, lesson, payload, job['id']))
            started, waits = time.monotonic(), 0
            while not queue.wait_idle(15):
                waits += 1
                if waits % 4 == 0:
                    finished = sum(queue.detail(job_id)['status'] in ('succeeded', 'failed', 'cancelled')
                                   for *_, job_id in submitted)
                    log(f'  已完成 {finished}/{len(submitted)}，用时 {round(time.monotonic() - started)} 秒')
            with fixture.fixture_db() as conn:
                rows = {row['job_id']: row for row in conn.execute('''SELECT j.id AS job_id, j.status, r.id AS run_id,
                    r.usage, r.response_metadata AS meta, r.content, r.error_message, r.prompt_version,
                    r.context->'materials' AS materials, r.retrieval,
                    r.citation_check->'quality_report' AS report, extract(epoch FROM j.finished_at - j.started_at) AS active
                    FROM generation_jobs j JOIN generation_runs r ON r.id=j.run_id WHERE j.id=ANY(%s)''',
                                                                  ([job_id for *_, job_id in submitted],))}
        finally:
            queue.close()
            for item in patches:
                item.stop()
        records = []
        for case, lesson, payload, job_id in submitted:
            row = rows[job_id]
            entry = record(case, lesson, payload, row)
            records.append(entry)
            name = f'{case["id"]}-{variant(entry)}'
            if row['content']:
                (out / (name + ('.md' if row['status'] == 'succeeded' else '-failed.md'))).write_text(row['content'], encoding='utf-8')
            meta = row['meta'] or {}
            if meta.get('design'):
                (out / f'{name}-design.json').write_text(json.dumps(meta['design'], ensure_ascii=False, indent=1), encoding='utf-8')
            # The material exactly as the model got it, and how it was picked.
            (out / f'{name}-materials.json').write_text(json.dumps(dict(
                retrieval={key: value for key, value in (row['retrieval'] or {}).items() if key != 'alternatives'},
                materials=row['materials'] or []), ensure_ascii=False, indent=1), encoding='utf-8')
            (out / f'{name}-stages.json').write_text(json.dumps(dict(stages=meta.get('stages') or [], reminders=entry['reminders']),
                                                                ensure_ascii=False, indent=1), encoding='utf-8')
        simulated = model != REAL
        (out / 'results.json').write_text(json.dumps(dict(
            evalset=data['version'], created=datetime.now().isoformat(timespec='seconds'),
            model='模拟' if simulated else module.MODEL, modes=list(modes), retrievals=list(retrievals), quick=quick, note=note,
            embed_model=getattr(module.EMBEDDER, 'model', None) if quick or RANKED & set(retrievals) else None,
            concurrency=concurrency, subjects=subjects,
            cases=cases, results=records), ensure_ascii=False, indent=1), encoding='utf-8')
        (out / 'summary.md').write_text(summary(records, module.MODEL, simulated), encoding='utf-8')
        if len(modes) * len(retrievals) > 1:
            blind(out, cases, records, [None] * (len(modes) * len(retrievals)))
        log(summary(records, module.MODEL, simulated))
        log(f'结果保存在 {out}（不进入 git）。')
        return records
    finally:
        module.GENERATION_LOCK.set_limit(1)
        fixture.tearDown()
        CourseJobTests.tearDownClass()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--split', choices=['dev', 'holdout', 'all'], default='dev', help='评测集分组（默认 dev）')
    parser.add_argument('--cases', nargs='+', metavar='编号', help='只运行这些课，例如 L1 P2（不看 --split）')
    parser.add_argument('--modes', nargs='+', choices=['profile', 'none'], default=['profile'],
                        help='profile：有画像（全班课用班级画像）；none：无画像。两个都写时每节课各生成一份，并输出盲评材料')
    parser.add_argument('--retrieval', nargs='+', choices=list(RETRIEVALS), default=['sql'],
                        help='材料取用：sql 所选知识点全部材料（默认）；anchored 本课知识点全部资料加整门课补充；'
                             'hybrid 整门课检索；none 不提供材料（对照）')
    parser.add_argument('--quick', action='store_true',
                        help='用一键备课建课：标题和目标作为重点，自动勾选建议的知识点（不用评测集标注的知识点）')
    parser.add_argument('--note', default='', help='教师备注（只用于为单个学生生成的课，进入「针对学情」查询），例如写一个前面知识点上的错误')
    parser.add_argument('--real', action='store_true', help='调用已配置的真实模型（产生费用）；不加时只统计请求大小')
    parser.add_argument('--concurrency', type=int, default=3, help='同时进行的模型调用数（默认 3，与服务默认相同）')
    parser.add_argument('--yes', action='store_true', help=f'预计用量超过 {CONFIRM_ABOVE} tokens 时仍然运行')
    parser.add_argument('--no-subject', action='store_true', help='课程不设学科（v1 的做法），用来和设了学科的结果对照')
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 8:
        parser.error('并发数请填 1—8。')
    try:
        cases = pick(load_set(), args.split, args.cases)
    except ValueError as exc:
        parser.error(str(exc))
    modes = list(dict.fromkeys(args.modes))
    retrievals = list(dict.fromkeys(args.retrieval))
    expected = estimate(cases, modes) * len(retrievals)
    print(f'{len(cases)} 节课 × {len(modes)} 种方式 × {len(retrievals)} 种材料取用：' + '、'.join(case['id'] for case in cases)
          + f'；按已测用量估计约 {expected // 10000} 万 tokens。')
    if any(case['split'] == 'holdout' for case in cases):
        print('包含 holdout 课：按规矩只在发版前运行，不对着它调提示词和检查。')
    if args.real:
        from dotenv import load_dotenv
        load_dotenv(data_dir() / '.env')  # the same file the service reads; its values are never printed
        from qwen import config_flags
        if not config_flags()['endpoint_format_ok']:
            parser.error('没有找到可用的模型配置。请用 ZHIYE_DATA_DIR 指向放有 .env 的数据目录。')
        if expected > CONFIRM_ABOVE and not args.yes:
            parser.error(f'预计用量超过 {CONFIRM_ABOVE} tokens。确认后加 --yes 再运行，或用 --cases 少选几节。')
    run(cases, modes, REAL if args.real else None, args.concurrency, subjects=not args.no_subject, retrievals=retrievals,
        quick=args.quick, note=args.note)
    if not args.real:
        print('\n这是预演，没有调用模型。加 --real 才会真实生成并产生用量。')


if __name__ == '__main__':
    main()
