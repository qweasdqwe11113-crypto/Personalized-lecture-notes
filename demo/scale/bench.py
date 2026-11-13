"""Throughput and quality baseline for lecture generation at class scale.

    .venv/bin/python demo/scale/bench.py                              # simulated model, 12 lectures
    .venv/bin/python demo/scale/bench.py --jobs 40 --latency 8 --concurrency 1 3 6
    .venv/bin/python demo/scale/bench.py --real --jobs 2 --concurrency 3   # the configured model (real calls, real cost)

It builds a disposable schema in the dedicated database (dropped afterwards, like the
test suite), loads the scale corpus and a synthetic class, and submits one batch of
lectures per concurrency level through the real queue, gate and storage code.

Simulated mode replaces only the model call with a reply of the requested length after
--latency seconds, so it measures the pipeline (queueing, parallel section writing,
checks, saving), not model quality. --real leaves the model call alone and additionally
reports length and program-check results; lectures are written to .runtime/bench/.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
import time
from threading import Lock
from unittest.mock import patch

DEMO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(DEMO), str(DEMO / 'tests')]


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))] if ordered else 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--jobs', type=int, default=12, help='每个并发档位生成的讲义数（默认 12）')
    parser.add_argument('--concurrency', type=int, nargs='+', default=[1, 3], help='要比较的模型并发数')
    parser.add_argument('--latency', type=float, default=1.0, help='模拟模型每次调用的秒数')
    parser.add_argument('--mode', choices=['sectioned-v1', 'single'], default='sectioned-v1')
    parser.add_argument('--course', choices=['probability', 'linear'], default='probability')
    parser.add_argument('--examples', type=int, default=8)
    parser.add_argument('--real', action='store_true', help='调用已配置的真实模型（产生费用）')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 60 or any(not 1 <= c <= 8 for c in args.concurrency):
        raise SystemExit('讲义数请填 1—60，并发数请填 1—8。')

    from test_courses_jobs import CourseJobTests, module
    from jobs import JobQueue
    from load import install
    import sectioned
    key = 'scale_probability_v1' if args.course == 'probability' else 'scale_linear_v1'
    calls, guard = {'count': 0, 'active': 0, 'peak': 0}, Lock()

    def simulated(prepared, attempted):
        attempted()
        with guard:
            calls['count'] += 1
            calls['active'] += 1
            calls['peak'] = max(calls['peak'], calls['active'])
        try:
            time.sleep(args.latency)
            payload = json.loads(prepared.body)
            user = json.loads(payload['messages'][1]['content'])
            ctx = user['context']
            cite = f" [M{ctx['materials'][0]['id']}]" if ctx['materials'] else ''
            marks = ''.join(f" [K{s['id']}]" for s in ctx.get('states', []))
            if 'target_section' in user:
                title, low = user['target_section'], user['section_quota']['low']
                return module.ModelResult(f"## {title}\n{'讲' * low}{cite}{marks if title == '核心讲解' else ''}\n",
                                          {'total_tokens': low * 2})
            if '+sectioned-v1' in prepared.prompt_version:
                return module.ModelResult(json.dumps({'title': '基准讲义', 'notation': '', 'sections': {
                    name: ['要点一', '要点二'] for name, _, _ in sectioned.SECTIONS}}, ensure_ascii=False), {'total_tokens': 200})
            low = sectioned.target_range(ctx)[0]
            body = ''.join(f"## {name}\n{'讲' * round(low * share + 10)}{cite}{marks if name == '核心讲解' else ''}\n\n"
                           for name, share, _ in sectioned.SECTIONS)
            return module.ModelResult('# 基准讲义\n\n' + body, {'total_tokens': low * 2})
        finally:
            with guard:
                calls['active'] -= 1

    CourseJobTests.setUpClass()
    fixture = CourseJobTests()
    results, output = [], None
    try:
        fixture.setUp()
        with fixture.fixture_db() as conn:
            loaded = install(conn, students=args.jobs, examples=args.examples)
            students = [row['id'] for row in conn.execute(
                "SELECT id FROM students WHERE subtitle LIKE %s ORDER BY id", ('合成·%',))]
        course = next(c for c in loaded['courses'] if c['key'] == key)
        # install() creates one class per course, in course order.
        offset = 0 if args.course == 'linear' else args.jobs
        students = students[offset:offset + args.jobs]
        print(f"语料：{course['title']} · {course['knowledge_points']} 个知识点 · {course['materials']} 份材料 · {course['characters']} 字符；"
              f"学生 {len(students)} 名；模型：{'真实（' + module.MODEL + '）' if args.real else f'模拟，每次调用 {args.latency} 秒'}")
        if args.real:
            output = DEMO.parent / '.runtime' / 'bench' / datetime.now().strftime('%Y%m%d-%H%M%S')
            output.mkdir(parents=True, exist_ok=True)
        payload = dict(topic=key, goal='', personalization_mode='profile', retrieval_mode='sql', generation_mode=args.mode,
                       lecture_format='rich-v1' if args.real else 'plain',
                       teaching_options={'duration': 45, 'detail': 'standard', 'length_policy': 'duration-v2'})
        for limit in args.concurrency:
            calls.update(count=0, active=0, peak=0)
            module.GENERATION_LOCK.set_limit(limit)
            queue = JobQueue(lambda: module.db(), module.execute_job, capacity=limit + 3, workers=limit,
                             bulk_capacity=args.jobs + 8)
            patches = [patch.object(module, 'TASKS', queue)] + ([] if args.real else [patch.object(module, 'call_qwen', simulated)])
            for item in patches:
                item.start()
            try:
                started = time.monotonic()
                response = fixture.client.post('/api/jobs/batch', json={**payload, 'student_ids': students})
                jobs = [job for job in response.get_json()['jobs'] if 'job_id' in job]
                rejected = [job for job in response.get_json()['jobs'] if 'error' in job]
                while not queue.wait_idle(5):
                    pass
                wall = time.monotonic() - started
                with fixture.fixture_db() as conn:
                    rows = conn.execute('''SELECT j.status, r.id AS run_id, r.lecture_id, r.usage, r.error_message, r.content,
                        r.error_code, r.response_metadata->'stages' AS stages,
                        extract(epoch FROM j.finished_at - j.created_at) AS total,
                        extract(epoch FROM j.finished_at - j.started_at) AS active,
                        (r.context->'input_budget'->>'context_characters')::int AS input_characters,
                        r.citation_check->'quality_report' AS report
                        FROM generation_jobs j JOIN generation_runs r ON r.id=j.run_id WHERE j.id=ANY(%s) ORDER BY r.id''',
                                        ([job['job_id'] for job in jobs],)).fetchall()
            finally:
                queue.close()
                for item in patches:
                    item.stop()
            done = [row for row in rows if row['status'] == 'succeeded']
            entry = dict(concurrency=limit, submitted=len(jobs), rejected=len(rejected), succeeded=len(done),
                         wall_seconds=round(wall, 1), lectures_per_minute=round(len(done) / wall * 60, 2) if wall else 0,
                         job_seconds_p50=round(percentile([float(r['active']) for r in done], .5), 1),
                         job_seconds_p95=round(percentile([float(r['active']) for r in done], .95), 1),
                         wait_plus_run_p95=round(percentile([float(r['total']) for r in done], .95), 1),
                         input_characters=round(statistics.mean(r['input_characters'] or 0 for r in rows)) if rows else 0,
                         failures=[r['error_message'] for r in rows if r['status'] != 'succeeded'][:5])
            if args.real:
                lengths, passed, warnings, tokens = [], 0, {}, []
                for row in done:
                    checks = {c['code']: c['status'] for c in row['report']['automatic']}
                    lengths.append(row['report']['metrics']['chinese_characters'])
                    passed += checks.get('length') == 'pass'
                    for code, status in checks.items():
                        if status == 'warning':
                            warnings[code] = warnings.get(code, 0) + 1
                    tokens.append((row['usage'] or {}).get('total_tokens', 0))
                    (output / f"c{limit}-run{row['run_id']}.md").write_text(row['content'], encoding='utf-8')
                for row in rows:  # keep what a failed run did write, and every stage record, for diagnosis
                    if row['status'] != 'succeeded' and row['content']:
                        (output / f"c{limit}-run{row['run_id']}-failed.md").write_text(row['content'], encoding='utf-8')
                entry['runs'] = [dict(run_id=row['run_id'], status=row['status'], error_code=row['error_code'],
                                      seconds=round(float(row['active'] or 0), 1),
                                      stages=[dict(stage=s['stage'], attempt=s['attempt'], characters=s['characters'],
                                                   seconds=s['seconds'], finish_reason=s['finish_reason'],
                                                   total_tokens=s['total_tokens'], problems=s['problems'])
                                              for s in row['stages'] or []]) for row in rows]
                entry.update(chinese_characters=lengths, length_pass=f'{passed}/{len(done)}', check_warnings=warnings,
                             total_tokens_mean=round(statistics.mean(tokens)) if tokens else 0)
            else:
                entry.update(model_calls=calls['count'], peak_parallel_calls=calls['peak'])
            results.append(entry)
            print(json.dumps(entry, ensure_ascii=False))
    finally:
        module.GENERATION_LOCK.set_limit(1)
        fixture.tearDown()
        CourseJobTests.tearDownClass()
    if output:
        (output / 'results.json').write_text(json.dumps(dict(arguments=vars(args), model=module.MODEL, results=results),
                                                        ensure_ascii=False, indent=1), encoding='utf-8')
        print('讲义与结果已保存到 ' + str(output))
    if len(results) > 1 and results[0]['wall_seconds']:
        base = results[0]
        for entry in results[1:]:
            print(f"并发 {entry['concurrency']} 相对并发 {base['concurrency']}：总用时 {base['wall_seconds']} 秒 → {entry['wall_seconds']} 秒，"
                  f"加速 {base['wall_seconds'] / entry['wall_seconds']:.2f} 倍")


if __name__ == '__main__':
    main()
