"""Outline first, then one model call per section; code assembles and checks the result.

One long reply kept coming back at a third of the requested length. Here the frozen
request asks only for an outline; each of the five sections is then written by its own
call with its own length quota, in parallel when the model gate has spare permits. A
section that breaks the structure, cites a missing source or is far too short is asked
for once more with the concrete problems; nothing is silently patched.

Every later request is derived from the frozen request, the frozen context and the
outline text, so a saved run records enough to rebuild each one (its SHA-256 is kept).
"""
import hashlib
import json
from pathlib import Path
import re
from threading import Lock, Thread
import time

from prompts import PreparedRequest
from qwen import ModelError, ModelResult, stream_to, visible_text
from teaching import length_range

MODE = 'sectioned-v1'
OUTLINE_MARK = '\n【分章节生成 sectioned-v1 · 大纲阶段'
SECTIONS = [
    ('学习目标', 0.05, '给出课堂结束时可检查的目标。'),
    ('本次学习安排', 0.10, '简要说明诊断依据与讲解调整，给出各环节建议分钟数；这是教学建议，不是实际计时。'),
    ('核心讲解', 0.45, '按顺序给出教师讲解、关键板书步骤和课堂提问；提问附预期回答、典型错误与追问引导。'),
    ('例题与检验', 0.30, '提供指定数量的例题与课堂练习，保留必要推导、适用条件、检验及参考答案；区分课内必做与选做。'),
    ('易错点与小结', 0.10, '给出易错点处理和简短的课堂达成检查。'),
]
SHORT_RATIO = 0.7          # below this share of the lower bound a section is asked for again
LONG_RATIO = 1.5           # above this multiple of the upper bound as well: small sections ran to three times their quota
SOFT = ('篇幅', '缺少讲解安排定位')  # worth one rewrite, never a reason to fail the lecture
SECTION_LIMIT = 14000
TRANSIENT = {'timeout', 'http_error', 'unreachable', 'connection_error', 'stream_error', 'invalid_json'}
INCOMPLETE = {'truncated', 'empty_output', 'unfinished_output'}


def target_range(context):
    """Same range the length check uses, so quotas and the check cannot disagree."""
    expanded = context.get('lecture_length_policy') == 'expanded-v1'
    if context.get('teaching_options'):
        return length_range(context['teaching_options'], expanded)
    if expanded:
        return 4000, 8000
    return tuple(context['topic'].get('length_range', [600, 900]))


def freeze(context):
    """Everything the later calls need beyond the frozen request, saved with the run."""
    low, high = target_range(context)
    return dict(version=MODE,
                section_prompt=(Path(__file__).with_name('prompts') / 'lecture-sectioned-section-v1.md')
                .read_text(encoding='utf-8').strip(),
                quotas=[dict(title=title, brief=brief, low=max(40, round(low * share)), high=max(80, round(high * share)))
                        for title, share, brief in SECTIONS])


def chinese(text):
    return len(re.findall(r'[一-鿿]', text or ''))


def parse_outline(content):
    text = (content or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError('输出不是有效的 JSON 对象') from None
    if not isinstance(data, dict) or not isinstance(data.get('sections'), dict):
        raise ValueError('缺少 sections 对象')
    title = data.get('title')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('缺少 title')
    sections = {}
    for name, _, _ in SECTIONS:
        points = data['sections'].get(name)
        if not isinstance(points, list) or not points or any(not isinstance(p, str) or not p.strip() for p in points):
            raise ValueError(f'章节「{name}」缺少要点')
        sections[name] = [p.strip()[:160] for p in points[:10]]
    notation = data.get('notation', '')
    return dict(title=re.sub(r'\s+', ' ', title.strip().lstrip('#').strip())[:60],
                notation=notation.strip()[:400] if isinstance(notation, str) else '', sections=sections)


def _derived(frozen_body, messages, max_tokens, stream, version):
    payload = json.loads(frozen_body)
    body = {'model': payload['model'], 'messages': messages, 'temperature': payload.get('temperature', 0.3),
            'max_tokens': max_tokens}
    if 'chat_template_kwargs' in payload:
        body['chat_template_kwargs'] = payload['chat_template_kwargs']
    if stream:
        body['stream'] = True
        body['stream_options'] = {'include_usage': True}
    elif 'response_format' in payload:
        body['response_format'] = payload['response_format']
    text = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
    return PreparedRequest(text, hashlib.sha256(text.encode('utf-8')).hexdigest(), version, 'lecture-sectioned-context-v1')


def section_request(frozen_body, context, outline, quota):
    payload = json.loads(frozen_body)
    system = payload['messages'][0]['content']
    cut = system.rfind(OUTLINE_MARK)
    if cut < 0 or not isinstance(context.get('sectioned'), dict):
        raise ModelError('invalid_request', '冻结输入不是分章节生成请求，不能继续。', 'preparation')
    user = json.loads(payload['messages'][1]['content'])
    user.update(target_section=quota['title'], section_brief=quota['brief'],
                section_quota={'low': quota['low'], 'high': quota['high']})
    if outline:
        user['outline'] = outline
    if quota['title'] == '核心讲解' and context.get('personalization_plan'):
        user['required_plan_markers'] = [f"K{item['knowledge_id']}" for item in context['personalization_plan']]
    rich = context.get('lecture_format') == 'rich-v1'
    messages = [{'role': 'system', 'content': system[:cut] + '\n' + context['sectioned']['section_prompt']},
                {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}]
    return _derived(frozen_body, messages, min(16000, round(quota['high'] * (2.2 if rich else 1.7)) + 900),
                    True, 'lecture-sectioned-section-v1')


def repair_request(request, previous, problems, version):
    payload = json.loads(request.body)
    feedback = ('上一次输出没有通过程序检查：' + '；'.join(problems)
                + '。请针对这些问题重新输出完整内容，仍然只输出要求的格式，不要解释或道歉。')
    messages = payload['messages'] + [{'role': 'assistant', 'content': previous[:SECTION_LIMIT]},
                                      {'role': 'user', 'content': feedback}]
    return _derived(request.body, messages, payload['max_tokens'], bool(payload.get('stream')), version)


def inspect_section(output, quota, context):
    """Return (text, problems). Tolerates harmless wrapping; reports everything else."""
    from sections import split_sections
    title, text = quota['title'], (output or '').strip()
    fenced = re.fullmatch(r'```(?:markdown|md)?[ \t]*\r?\n(.*)\r?\n```', text, re.S)
    if fenced:
        text = fenced[1].strip()
    lines = text.splitlines()
    if lines and re.fullmatch(r'#[ \t]+\S.*', lines[0]):
        lines = lines[1:]  # a repeated lecture title is dropped, the assembled lecture has its own
        while lines and not lines[0].strip():
            lines = lines[1:]
    heading = re.compile(r'#{1,3}[ \t]*(?:[一二三四五\d]+[、.．)）][ \t]*)?' + re.escape(title) + r'[ \t]*[:：]?[ \t]*')
    if lines and heading.fullmatch(lines[0].strip()):
        lines[0] = '## ' + title
    elif lines and not any(re.match(r'#{1,2}[ \t]+\S', line) for line in lines):
        lines.insert(0, '## ' + title)
        lines.insert(1, '')
    text = '\n'.join(lines).strip()
    problems = []
    parts = split_sections(text + '\n')
    if len(parts) != 1 or parts[0]['start'] != 0 or parts[0]['title'] != title:
        problems.append(f'只能输出「## {title}」这一个二级章节，不能包含其他二级章节')
    if re.search(r'^#[ \t]+\S', text, re.M):
        problems.append('不要输出一级标题')
    if not text.split('\n', 1)[-1].strip() or '\n' not in text:
        problems.append('章节正文为空')
    if len(text) > SECTION_LIMIT:
        problems.append(f'章节超过 {SECTION_LIMIT} 字符，请精简')
    materials = {str(m['id']) for m in context.get('materials', [])}
    visuals = {item['citation_id'] for item in context.get('visual_evidence', [])}
    missing = sorted(set(re.findall(r'\[M(\d+)\]', text)) - materials)
    missing_visual = sorted(set(re.findall(r'\[(V\d+)\]', text)) - visuals)
    if missing or missing_visual:
        problems.append('引用了不存在的来源编号 ' + '、'.join(['M' + m for m in missing] + missing_visual)
                        + '；只能使用输入中提供的编号')
    if title != '核心讲解':
        text = re.sub(r'[ \t]*\[K\d+\]', '', text)  # plan markers belong to one section by contract
    count = chinese(text)
    if count < quota['low'] * SHORT_RATIO:
        problems.append(f'篇幅只有 {count} 个汉字，低于本章节下限 {quota["low"]}；请补足推导步骤、提问与预期回答、检验过程')
    elif count > quota['high'] * LONG_RATIO:
        problems.append(f'篇幅有 {count} 个汉字，超过本章节上限 {quota["high"]}；请删去属于其他章节的展开和重复内容，压缩到 {quota["low"]}—{quota["high"]} 个汉字')
    if title == '核心讲解':
        planned = [str(item['knowledge_id']) for item in context.get('personalization_plan', [])]
        absent = [k for k in planned if k not in set(re.findall(r'\[K(\d+)\]', text))]
        if absent:
            problems.append('缺少讲解安排定位 ' + '、'.join('K' + k for k in absent) + '；每条讲解安排在首次落实处标注一次')
    return text, problems


def _only_soft(problems):
    return all(problem.startswith(SOFT) for problem in problems)


def _distance(text, problems, quota):
    """How far a structurally valid candidate is from its quota and plan markers; smaller is better."""
    count = chinese(text)
    return max(quota['low'] - count, count - quota['high'], 0) + 300 * sum(p.startswith('缺少讲解安排定位') for p in problems)


def generate(call_model, gate, prepared, context, goal, on_attempt, publish=None, announce=None):
    """Run the staged generation. call_model(prepared, on_attempt) -> ModelResult.

    The caller already holds one gate permit; extra section writers take spare permits
    without blocking, so a gate of 1 degrades to plain sequential calls.
    """
    publish = publish or (lambda key, text, state: None)
    announce = announce or (lambda label: None)
    guard, stages, usage = Lock(), [], {}
    started = time.monotonic()

    def record(stage, attempt, request, output, seconds, problems, model=None):
        with guard:
            for key, value in (output.usage or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
            usage['model_calls'] = usage.get('model_calls', 0) + 1
            stages.append(dict(stage=stage, attempt=attempt, request_sha256=request.sha256,
                               seconds=round(seconds, 2), characters=chinese(output.content),
                               finish_reason=output.metadata.get('finish_reason'),
                               total_tokens=(output.usage or {}).get('total_tokens'), problems=problems))

    def metadata(outline, extra=None):
        with guard:
            return {'generation_mode': MODE, 'stages': sorted(stages, key=lambda s: (s['stage'] != 'outline', s['stage'], s['attempt'])),
                    'outline': outline, 'elapsed_seconds': round(time.monotonic() - started, 2), **(extra or {})}

    # Stage 1: the frozen request itself.
    announce('规划大纲')
    outline, response_model = None, None
    begin = time.monotonic()
    try:
        first = call_model(prepared, on_attempt)
    except ModelError as cut:
        if cut.code not in INCOMPLETE:
            raise
        # A cut-off or empty outline is unusable but not fatal: the sections have fixed briefs.
        first = None
        record('outline', 1, prepared, cut.partial, time.monotonic() - begin, [str(cut)])
    response_model = first.metadata.get('response_model') if first else None
    try:
        if first is None:
            raise LookupError
        outline = parse_outline(first.content)
        record('outline', 1, prepared, first, time.monotonic() - begin, [])
    except LookupError:
        pass
    except ValueError as exc:
        record('outline', 1, prepared, first, time.monotonic() - begin, [str(exc)])
        retry = repair_request(prepared, first.content, [str(exc)], 'lecture-sectioned-outline-repair-v1')
        begin = time.monotonic()
        try:
            second = call_model(retry, lambda: None)
            try:
                outline = parse_outline(second.content)
                record('outline', 2, retry, second, time.monotonic() - begin, [])
            except ValueError as again:
                record('outline', 2, retry, second, time.monotonic() - begin, [str(again)])
        except ModelError as failed:
            record('outline', 2, retry, failed.partial, time.monotonic() - begin, [str(failed)])
    # Without a usable outline the sections are still written from their fixed briefs.

    quotas = context['sectioned']['quotas']
    results, failures = {}, []

    def attempt(title, request, number):
        buffer = []

        def piece(text):
            buffer.append(text)
            publish(title, visible_text(''.join(buffer)), 'writing')
        begin = time.monotonic()
        try:
            with stream_to(piece):
                output = call_model(request, lambda: None)
            error = None
        except ModelError as exc:
            if exc.code not in TRANSIENT | INCOMPLETE:
                raise
            output, error = exc.partial, exc
        return output, error, time.monotonic() - begin

    def write(quota):
        title = quota['title']
        request = section_request(prepared.body, context, outline, quota)
        publish(title, '', 'writing')
        output, error, seconds = attempt(title, request, 1)
        text, problems = inspect_section(output.content, quota, context)
        if error is not None:
            problems = [str(error)] + ([] if error.code in TRANSIENT else problems)
        record(title, 1, request, output, seconds, problems)
        if not problems:
            publish(title, text, 'done')
            return text
        publish(title, '', 'repairing')
        if error is not None and error.code in TRANSIENT:
            retry = request  # nothing usable came back: send the same derived request once more
        else:
            retry = repair_request(request, output.content, problems, 'lecture-sectioned-section-repair-v1')
        second, second_error, seconds = attempt(title, retry, 2)
        second_text, second_problems = inspect_section(second.content, quota, context)
        if second_error is not None:
            second_problems = [str(second_error)] + second_problems
        record(title, 2, retry, second, seconds, second_problems)
        first_usable = error is None and _only_soft(problems)
        if _only_soft(second_problems) and (not first_usable or _distance(second_text, second_problems, quota)
                                            <= _distance(text, problems, quota)):
            text, problems = second_text, second_problems
        if not _only_soft(problems):  # a transport error or a structural problem survived both attempts
            raise ModelError('invalid_section', f'章节「{title}」重写一次后仍未通过检查：{problems[0]}。未保存为完整讲义，请重试。',
                             'output', ModelResult(text))
        publish(title, text, 'done')
        return text

    def helper(quota):
        try:
            results[quota['title']] = write(quota)
        except ModelError as exc:
            failures.append(exc)
        except Exception:
            failures.append(ModelError('unexpected_error', '章节写作出现异常，未保存为完整讲义，请重试。', 'output'))
        finally:
            gate.release()

    announce('分章节写作')
    pending, threads = list(quotas), []
    try:
        while pending and not failures:
            while gate is not None and len(pending) > 1 and gate.acquire(blocking=False):
                thread = Thread(target=helper, args=(pending.pop(),), daemon=True, name='section-writer')
                threads.append(thread)
                thread.start()
            quota = pending.pop(0)
            try:
                results[quota['title']] = write(quota)
            except ModelError as exc:
                failures.append(exc)
    finally:
        for thread in threads:  # helpers own their permits; never return while one is still writing
            thread.join()

    title = (outline or {}).get('title') or context['topic']['title']
    body = '\n\n'.join(results[q['title']].strip() for q in quotas if q['title'] in results)
    content = f'# {title}\n\n{body}\n' if body else ''
    extra = {'response_model': response_model} if isinstance(response_model, str) else {}
    if failures:
        error = failures[0]
        raise ModelError(error.code, str(error), error.stage, ModelResult(content, dict(usage), metadata(outline, extra)))
    announce('程序检查')
    return ModelResult(content, dict(usage), metadata(outline, {**extra, 'finish_reason': 'stop'}))
