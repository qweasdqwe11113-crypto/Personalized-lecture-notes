"""Do the cited sentences say what the cited material says? A model judgment per cited line, beside
the lexical attribution check of quality.py.

    .venv/bin/python demo/scale/judge_citations.py <evalset folder> [...]                    # dry run: counts only
    .venv/bin/python demo/scale/judge_citations.py <folder> [...] --real --json out.json    # the configured model
    .venv/bin/python demo/scale/judge_citations.py <folder> --cases L1 P1                    # only these lessons

A folder is one evalset run (.runtime/evalset/<time>/). Each lesson's material is read from its
<name>-materials.json (runs made with --retrieval). A folder made before that, such as the sql
baseline of 2026-11-18, has none; its material is rebuilt the way that run built it (same evalset,
corpus and seed in a disposable schema, retrieval_mode sql), and every id the lesson cites must be
found in the rebuilt material before anything is judged.

One request per lesson (claims in batches of BATCH) holds only the material the batch cites. The
judge is the same model family that wrote the lessons, so it may be lenient with its own kind of
writing, and two passes over the same lines agreed on about 86% of them. The verdicts are a screen;
docs/scale-data.md says which ones were checked against the material by hand, and by whom.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SCALE = Path(__file__).resolve().parent
sys.path[:0] = [str(SCALE), str(SCALE.parent), str(SCALE.parent / 'tests')]

import figures  # noqa: E402
from quality import MIN_TERMS, citation_support  # noqa: E402
from retrieval import terms  # noqa: E402

BATCH = 15
VERDICTS = ('supported', 'partial', 'unsupported')
SYSTEM = '''你是严格的引用核对员。每条 claim 是讲义里带 [M编号] 的一行，cites 是它引用的材料编号；materials 给出这些材料的原文（检索得到的可能是节选）。
逐条判断所引材料是否支持这一行的内容，只依据给出的材料原文，不用自己的知识补全：
- supported：这一行的定义、公式、条件、数据或题目在所引材料中有明确依据；允许改写，允许由材料直接算出的一步结果。
- partial：一部分有依据，另有材料里没有的内容（额外的结论、数据、条件或扩展）。
- unsupported：所引材料里找不到依据、与材料矛盾，或引错了材料。
教学组织语句（提问、板书安排、时间分配）本身不需要材料支持，只看其中的数学内容是否有依据；没有数学内容的，判 supported。
只输出 JSON：{"judgments":[{"id":"C1","verdict":"supported|partial|unsupported","reason":"不超过40字"}]}'''


def claims_of(content):
    """Cited lines with enough words to judge, each with the line before it for context."""
    prose = figures.FENCE.sub(lambda match: '\n' * match[0].count('\n'), content)
    lines, found, previous = prose.splitlines(), [], ''
    for number, line in enumerate(lines, 1):
        text = line.strip()
        cites = list(dict.fromkeys(re.findall(r'\[M(\d+)\]', text)))
        if cites and len(set(terms(re.sub(r'\[[MVK]\d+\]', ' ', text)))) >= MIN_TERMS:
            found.append(dict(id=f'C{len(found) + 1}', line=number, text=text[:500], before=previous[:200],
                              cites=[f'M{c}' for c in cites]))
        if text:
            previous = text
    return found


def lessons_in(folder):
    """(name, lecture text, material file or None) of every finished lesson in an evalset folder."""
    for path in sorted(folder.glob('*.md')):
        if path.name.endswith('-failed.md') or path.parent.name == 'blind' or not re.match(r'[A-Z]\d+-', path.name):
            continue
        name = path.stem
        materials = folder / f'{name}-materials.json'
        yield name, path.read_text(encoding='utf-8'), materials if materials.exists() else None


def rebuild_sql(folder):
    """{lesson name: materials} for a run made before materials were saved: the sql material, rebuilt."""
    import evalset
    from test_courses_jobs import CourseJobTests, module
    results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    cases = [case for case in results['cases']]
    data = evalset.load_set()
    CourseJobTests.setUpClass()
    fixture = CourseJobTests()
    rebuilt = {}
    try:
        fixture.setUp()
        lessons = evalset.prepare(fixture, data, cases, data['students_per_course'], results.get('subjects', True))
        for entry in results['results']:
            case = next(case for case in cases if case['id'] == entry['case'])
            mode = 'profile' if entry['mode'] == 'class' else entry['mode']
            payload = evalset.job_payload(case, lessons[case['id']], mode, 'sql')
            _, _, _, context, _ = module.build_job(payload)
            rebuilt[f'{entry["case"]}-{entry["mode"]}'] = context['materials']
    finally:
        fixture.tearDown()
        CourseJobTests.tearDownClass()
    return rebuilt


def materials_for(folder):
    names = {name: path for name, _, path in lessons_in(folder)}
    if all(path is not None for path in names.values()):
        return {name: json.loads(path.read_text(encoding='utf-8'))['materials'] for name, path in names.items()}
    return rebuild_sql(folder)


def request(model, claims, materials):
    from prompts import PreparedRequest
    wanted = {cite for claim in claims for cite in claim['cites']}
    given = [dict(citation_id=f'M{m["id"]}', content=m['content']) for m in materials if f'M{m["id"]}' in wanted]
    body = json.dumps(dict(model=model, temperature=0, max_tokens=3000, response_format={'type': 'json_object'},
                           chat_template_kwargs={'enable_thinking': False},
                           messages=[{'role': 'system', 'content': SYSTEM},
                                     {'role': 'user', 'content': json.dumps(dict(materials=given, claims=[
                                         {k: claim[k] for k in ('id', 'before', 'text', 'cites')} for claim in claims]),
                                         ensure_ascii=False)}]), ensure_ascii=False, separators=(',', ':'))
    return PreparedRequest(body, hashlib.sha256(body.encode('utf-8')).hexdigest(), 'citation-judge-v1')


def judge(model, claims, materials, log):
    """{claim id: verdict} and the tokens used. A batch the model answered only in part is asked again
    once for the claims it left out; an answer cut off at max_tokens is asked again in two halves;
    whatever is still missing stays unjudged (counted, never guessed)."""
    from qwen import ModelError, call_qwen
    verdicts, usage = {}, 0

    def ask(wanted):
        """Judge wanted; False when the answer ran past max_tokens (its tokens still count)."""
        nonlocal usage
        try:
            reply = call_qwen(request(model, wanted, materials), lambda: None)
        except ModelError as exc:
            if exc.code != 'truncated':
                raise
            usage += (exc.partial.usage or {}).get('total_tokens') or 0
            return False
        usage += (reply.usage or {}).get('total_tokens') or 0
        try:
            found = json.loads(reply.content)['judgments']
        except (ValueError, KeyError, TypeError):
            found = []
        asked = {claim['id'] for claim in wanted}
        for item in found if isinstance(found, list) else []:
            if isinstance(item, dict) and item.get('id') in asked and item.get('verdict') in VERDICTS:
                verdicts[item['id']] = dict(verdict=item['verdict'], reason=str(item.get('reason', ''))[:120])
        return True

    for start in range(0, len(claims), BATCH):
        batch = claims[start:start + BATCH]
        for attempt in (1, 2):
            wanted = [claim for claim in batch if claim['id'] not in verdicts]
            if not wanted:
                break
            if not ask(wanted):
                log(f'  第 {start // BATCH + 1} 批第 {attempt} 次：回答超过长度上限，分两半再问。')
                half = (len(wanted) + 1) // 2
                for part in (wanted[:half], wanted[half:]):
                    if part:
                        ask(part)
            left = [claim['id'] for claim in wanted if claim['id'] not in verdicts]
            if left:
                log(f'  第 {start // BATCH + 1} 批第 {attempt} 次：{len(left)} 条没有给出判断'
                    + ('，再问一次。' if attempt == 1 else '，记为未判断。'))
    return verdicts, usage


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('folders', nargs='+', help='evalset 结果目录')
    parser.add_argument('--real', action='store_true', help='调用已配置的模型做判断（产生费用）；不加时只统计')
    parser.add_argument('--json', help='把逐条判断另存为 JSON 文件')
    parser.add_argument('--cases', nargs='+', help='只看这些课（如 L1 P1）；默认全部')
    args = parser.parse_args()
    model = None
    if args.real:
        from dotenv import load_dotenv
        from edition import data_dir
        load_dotenv(data_dir() / '.env')   # the file the service reads; its values are never printed
        import os
        model = os.environ.get('QWEN_MODEL')
    report, total = [], 0
    for folder in map(Path, args.folders):
        results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
        retrievals = results.get('retrievals') or ['sql']
        materials = materials_for(folder)
        for name, content, _ in lessons_in(folder):
            if args.cases and name.split('-')[0] not in args.cases:
                continue
            given = materials[name]
            cited = {f'M{i}' for i in re.findall(r'\[M(\d+)\]', content)}
            known = {f'M{m["id"]}' for m in given}
            claims = claims_of(content)
            lexical = citation_support(content, {'materials': given})
            entry = dict(folder=folder.name, lesson=name, retrieval=retrievals[0] if len(retrievals) == 1 else None,
                         materials=len(given), cited_ids=sorted(cited), unknown_ids=sorted(cited - known),
                         claims=len(claims), lexical_suspect=len(lexical['suspect']),
                         lexical_cross_language=lexical['skipped_language'])
            if entry['unknown_ids'] and not (folder / f'{name}-materials.json').exists():
                raise SystemExit(f'{folder.name}/{name}：重建的材料里没有 ' + '、'.join(entry['unknown_ids']) + '，不能判断。')
            print(f'{folder.name}/{name}：材料 {len(given)} 份，带引用的行 {len(claims)}，'
                  f'不存在的编号 {len(entry["unknown_ids"])}，字面疑似引错 {entry["lexical_suspect"]}')
            if args.real and claims:
                answerable = [claim for claim in claims if set(claim['cites']) <= known]
                verdicts, usage = judge(model, answerable, given, print)
                total += usage
                for claim in claims:
                    claim.update(verdicts.get(claim['id']) or (dict(verdict='unsupported', reason='引用了不存在的材料编号')
                                                                if not set(claim['cites']) <= known else {}))
                counts = {verdict: sum(claim.get('verdict') == verdict for claim in claims) for verdict in VERDICTS}
                entry.update(counts, judged=sum(counts.values()), unjudged=len(claims) - sum(counts.values()),
                             tokens=usage, details=claims)
                print('  ' + '，'.join(f'{verdict} {count}' for verdict, count in counts.items())
                      + f'，未判断 {entry["unjudged"]}；{usage} tokens')
            report.append(entry)
    if args.real:
        print(f'判断合计 {total} tokens。')
    if args.json:
        Path(args.json).write_text(json.dumps(dict(model=model, judge='citation-judge-v1', lessons=report),
                                              ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print('已保存：' + args.json)


if __name__ == '__main__':
    main()
