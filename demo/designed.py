"""Lesson design first, then the teacher's notes one segment at a time.

The frozen request asks the model for a lesson design (design.py): focus, objectives, timed
segments and one bank of examples and exercises. The program checks it, asks once more with the
concrete problems if needed, and can stop there so the teacher confirms or edits the design.
Each teaching segment is then written by its own call, which is told not to restate problems;
the program inserts titles, questions, problems and answers from the design, so every later
document quotes the same text.

Every later request is derived from the frozen request, the frozen context and the design, and
its SHA-256 is recorded with the run. Nothing is silently patched: a design or segment that
still breaks structure or cites a missing source after one retry fails the run.
"""
import hashlib
import json
import re
from threading import Lock, Thread
import time

from flask import jsonify, request

import design as D
import style
from prompts import PreparedRequest, designed_system, prepare_request
from qwen import ModelError, ModelResult, stream_to, visible_text

MODE = 'designed-v1'
SEGMENT_LIMIT = 12000
LONG_RATIO, SHORT_RATIO = 1.5, 0.7
TRANSIENT = {'timeout', 'http_error', 'unreachable', 'connection_error', 'stream_error', 'invalid_json'}
INCOMPLETE = {'truncated', 'empty_output', 'unfinished_output'}


def freeze(context, stop=True):
    """Server-side settings saved with the run; none of this is model input."""
    return dict(version=MODE, design_version=D.VERSION, segment_system=designed_system(context, 'segment'),
                stop=bool(stop), confirmed=None, feedback=None)


def chinese(text):
    return len(re.findall(r'[一-鿿]', text or ''))


def _derive(frozen_body, messages, max_tokens, stream, version):
    payload = json.loads(frozen_body)
    body = {'model': payload['model'], 'messages': messages, 'temperature': payload.get('temperature', 0.3),
            'max_tokens': max_tokens}
    if 'chat_template_kwargs' in payload:
        body['chat_template_kwargs'] = payload['chat_template_kwargs']
    if stream:
        body.update(stream=True, stream_options={'include_usage': True})
    elif 'response_format' in payload:
        body['response_format'] = payload['response_format']
    text = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
    return PreparedRequest(text, hashlib.sha256(text.encode('utf-8')).hexdigest(), version, 'lesson-design-context-v1')


def repair_request(request_, previous, problems, version):
    payload = json.loads(request_.body)
    feedback = ('上一次输出没有通过程序检查：' + '；'.join(problems[:8])
                + '。请针对这些问题重新输出完整内容，仍然只输出要求的格式，不要解释或道歉。')
    messages = payload['messages'] + [{'role': 'assistant', 'content': (previous or '')[:D.LIMIT]},
                                      {'role': 'user', 'content': feedback}]
    return _derive(request_.body, messages, payload['max_tokens'], bool(payload.get('stream')), version)


def segment_request(frozen_body, context, design, segment, budget):
    """One segment's request: the same goal and learner facts, only this segment's material."""
    user = json.loads(json.loads(frozen_body)['messages'][1]['content'])
    view = user['context']
    bank = {item['id']: item for item in design['items']}
    cited = set(segment['sources']) | {name for ref in segment['items'] for name in bank[ref]['sources']}
    wanted = set(segment['knowledge_ids']) | {bank[ref]['knowledge_id'] for ref in segment['items']}
    materials = [m for m in view['materials'] if m.get('knowledge_id') in wanted or m['citation_id'] in cited]
    names = {**{entry['id']: f'第 {n} 环节' for n, entry in enumerate(design['segments'], 1)}, **D.labels(design)}
    message = {'goal': user['goal'], 'context': {**view, 'materials': materials or view['materials']},
               'design': design, 'names': names, 'target_segment': segment['id'], 'budget': budget}
    messages = [{'role': 'system', 'content': context['designed']['segment_system']},
                {'role': 'user', 'content': json.dumps(message, ensure_ascii=False, default=str)}]
    return _derive(frozen_body, messages, min(8000, round(budget['high'] * 2.2) + 700), True, 'lesson-segment-v1')


def inspect_prose(output, segment, budget, context, design):
    """Return (text, hard, soft). A repeated heading or wrapper is dropped; the rest is reported."""
    text = (output or '').strip()
    fenced = re.fullmatch(r'```(?:markdown|md)?[ \t]*\r?\n(.*)\r?\n```', text, re.S)
    if fenced:
        text = fenced[1].strip()
    lines = text.splitlines()
    while lines and (re.match(r'#{1,6}[ \t]', lines[0]) or not lines[0].strip()):
        lines = lines[1:]  # the assembled lesson has its own heading for this segment
    text = re.sub(r'[ \t]*\[K\d+\]', '', '\n'.join(lines)).strip()
    hard, soft = [], []
    if not text:
        hard.append('环节正文为空')
    if re.search(r'^#{1,2}[ \t]+\S', text, re.M):
        hard.append('不要写一级或二级标题，只写这个环节的正文')
    if len(text) > SEGMENT_LIMIT:
        hard.append(f'正文超过 {SEGMENT_LIMIT} 字符，请精简')
    allowed = D._allowed_sources(context)
    stray = sorted({f'{kind}{number}' for kind, number in re.findall(r'\[([MV])(\d+)\]', text)} - allowed)
    if stray:
        hard.append('引用了不存在的来源编号 ' + '、'.join(stray) + '；只能使用输入中提供的编号')
    count = chinese(text)
    if count > budget['high'] * LONG_RATIO:
        soft.append(f'篇幅有 {count} 个汉字，超过本环节上限 {budget["high"]}；删去重复和不属于本环节的内容')
    elif count < budget['low'] * SHORT_RATIO:
        soft.append(f'篇幅只有 {count} 个汉字，这是重点环节，至少写 {budget["low"]} 个汉字：补上推导的中间步骤、提问时机和纠错办法')
    squeezed = re.sub(r'\s+', '', text)
    bank = {item['id']: item for item in design['items']}
    copied = [D.labels(design)[ref] for ref in segment['items']
              if len(bank[ref]['stem']) >= 30 and re.sub(r'\s+', '', bank[ref]['stem'])[:30] in squeezed]
    if copied:
        soft.append('重抄了' + '、'.join(copied) + '的题干；题目由程序排版，这里只写怎么讲')
    hint = style.reminder(text)
    if hint:
        soft.append('表达需要改：' + hint + '。改成教师备课笔记的口吻，用短句和阿拉伯数字，不写旁白、免责声明和套话')
    return text, hard, soft


def _distance(text, soft, budget):
    count = chinese(text)
    return len(soft) * 1000 + max(budget['low'] - count, count - budget['high'], 0)


def settle_design(call_model, prepared, context, on_attempt, record):
    """Ask for the design, once more with the concrete problems if needed. Returns (design, soft)."""
    begin = time.monotonic()
    first = call_model(prepared, on_attempt)

    def read(content):
        try:
            return D.validate(D.parse(content), context)
        except D.DesignError as exc:
            return None, [str(exc)], []
    design, hard, soft = read(first.content)
    record('design', 1, prepared, first, time.monotonic() - begin, hard + soft)
    # A recomputed formula can be a false alarm; alone it is shown to the teacher, not retried.
    if not hard and all(D.ARITHMETIC in problem for problem in soft):
        return design, soft, first
    retry = repair_request(prepared, first.content, hard + soft, 'lesson-design-repair-v1')
    begin = time.monotonic()
    try:
        second = call_model(retry, lambda: None)
    except ModelError as exc:
        if exc.code not in TRANSIENT | INCOMPLETE or hard:
            raise
        record('design', 2, retry, exc.partial, time.monotonic() - begin, [str(exc)])
        return design, soft, first  # the first design is usable; its reminders stay visible
    again, hard2, soft2 = read(second.content)
    record('design', 2, retry, second, time.monotonic() - begin, hard2 + soft2)
    if not hard2 and (hard or len(soft2) <= len(soft)):
        return again, soft2, second
    if hard:
        raise ModelError('invalid_design', f'课堂设计重试一次后仍未通过检查：{(hard2 or hard)[0]}。未保存，请重试。',
                         'output', ModelResult(second.content))
    return design, soft, first


def generate(call_model, gate, prepared, context, goal, on_attempt, publish=None, announce=None, declare=None):
    """Run the staged generation. call_model(prepared, on_attempt) -> ModelResult.

    Returns the finished lesson, or (when the frozen settings say stop) the design alone with
    metadata['operation'] == 'lesson_design'. The caller holds one gate permit; extra segment
    writers take spare permits without blocking, so a gate of 1 means plain sequential calls.
    """
    publish = publish or (lambda key, text, state: None)
    announce = announce or (lambda label: None)
    declare = declare or (lambda parts: None)
    config = context['designed']
    guard, stages, usage = Lock(), [], {}
    started = time.monotonic()

    def record(stage, attempt, request_, output, seconds, problems):
        with guard:
            for key, value in (output.usage or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
            usage['model_calls'] = usage.get('model_calls', 0) + 1
            stages.append(dict(stage=stage, attempt=attempt, request_sha256=request_.sha256,
                               seconds=round(seconds, 2), characters=chinese(output.content),
                               finish_reason=output.metadata.get('finish_reason'),
                               total_tokens=(output.usage or {}).get('total_tokens'), problems=problems))

    def metadata(design, reminders, extra=None):
        with guard:
            return {'generation_mode': MODE, 'stages': list(stages), 'design': design, 'design_reminders': reminders,
                    'elapsed_seconds': round(time.monotonic() - started, 2), **(extra or {})}

    extra = {}
    if config.get('confirmed'):
        announce('读取已确认的课堂设计')
        design, hard, reminders = D.validate(config['confirmed']['design'], context)
        if hard:
            raise ModelError('invalid_design', '已确认的课堂设计未通过检查：' + hard[0], 'preparation')
        on_attempt()
    else:
        announce('课堂设计')
        publish('design', '', 'writing')
        design, reminders, reply = settle_design(call_model, prepared, context, on_attempt, record)
        if isinstance(reply.metadata.get('response_model'), str):
            extra['response_model'] = reply.metadata['response_model']
    publish('design', f'# {design["title"]}\n\n' + D.overview(design, context), 'done')
    if config.get('stop') and not config.get('confirmed'):
        return ModelResult(json.dumps(design, ensure_ascii=False), dict(usage),
                           metadata(design, reminders, {**extra, 'operation': 'lesson_design', 'finish_reason': 'stop'}))

    limits = D.budgets(design, context.get('teaching_options'))
    written = D.writers(design)
    declare([(segment['id'], segment['title']) for segment in design['segments']])
    for segment in design['segments']:
        if segment['id'] not in limits:
            publish(segment['id'], D.segment_block(design, segment), 'done')
    prose, failures = {}, []

    def attempt(segment, request_):
        buffer = []

        def piece(text):
            buffer.append(text)
            publish(segment['id'], D.segment_block(design, segment, visible_text(''.join(buffer))), 'writing')
        begin = time.monotonic()
        try:
            with stream_to(piece):
                output = call_model(request_, lambda: None)
            error = None
        except ModelError as exc:
            if exc.code not in TRANSIENT | INCOMPLETE:
                raise
            output, error = exc.partial, exc
        return output, error, time.monotonic() - begin

    def write(segment):
        budget = limits[segment['id']]
        request_ = segment_request(prepared.body, context, design, segment, budget)
        publish(segment['id'], '', 'writing')
        output, error, seconds = attempt(segment, request_)
        text, hard, soft = inspect_prose(output.content, segment, budget, context, design)
        if error is not None:
            hard = [str(error)] + ([] if error.code in TRANSIENT else hard)
        record(segment['id'], 1, request_, output, seconds, hard + soft)
        if hard or soft:
            publish(segment['id'], '', 'repairing')
            retry = (request_ if error is not None and error.code in TRANSIENT else
                     repair_request(request_, output.content, hard + soft, 'lesson-segment-repair-v1'))
            second, second_error, seconds = attempt(segment, retry)
            text2, hard2, soft2 = inspect_prose(second.content, segment, budget, context, design)
            if second_error is not None:
                hard2 = [str(second_error)] + hard2
            record(segment['id'], 2, retry, second, seconds, hard2 + soft2)
            if not hard2 and (hard or _distance(text2, soft2, budget) <= _distance(text, soft, budget)):
                text, hard, soft = text2, hard2, soft2
        if hard:
            raise ModelError('invalid_section', f'环节「{segment["title"]}」重写一次后仍未通过检查：{hard[0]}。未保存为完整讲义，请重试。',
                             'output', ModelResult(text))
        publish(segment['id'], D.segment_block(design, segment, text), 'done')
        return text

    def helper(segment):
        try:
            prose[segment['id']] = write(segment)
        except ModelError as exc:
            failures.append(exc)
        except Exception:
            failures.append(ModelError('unexpected_error', '环节写作出现异常，未保存为完整讲义，请重试。', 'output'))
        finally:
            gate.release()

    announce('分环节写作')
    pending, threads = list(written), []
    try:
        while pending and not failures:
            while gate is not None and len(pending) > 1 and gate.acquire(blocking=False):
                thread = Thread(target=helper, args=(pending.pop(),), daemon=True, name='segment-writer')
                threads.append(thread)
                thread.start()
            segment = pending.pop(0)
            try:
                prose[segment['id']] = write(segment)
            except ModelError as exc:
                failures.append(exc)
    finally:
        for thread in threads:  # helpers own their permits; never return while one is still writing
            thread.join()

    content = D.document(design, prose, context)
    with guard:
        stages.sort(key=lambda s: (s['stage'] != 'design', s['stage'], s['attempt']))
    if failures:
        error = failures[0]
        raise ModelError(error.code, str(error), error.stage, ModelResult(content, dict(usage), metadata(design, reminders, extra)))
    announce('程序检查')
    return ModelResult(content, dict(usage), metadata(design, reminders, {**extra, 'finish_reason': 'stop'}))


# ---------------------------------------------------------------- confirm, edit or revise a design

class DesignRequestError(ValueError):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.status, self.problems = status, problems or []


def register_designs(app, db, queue, model):
    from runs import run_detail

    def candidate(run_id):
        with db() as conn:
            run = run_detail(conn, run_id)
        if not run or (run['response_metadata'] or {}).get('operation') != 'lesson_design':
            raise DesignRequestError('课堂设计记录不存在。', 404)
        if run['status'] != 'succeeded' or not run['integrity_valid']:
            raise DesignRequestError('课堂设计尚未生成成功，或输入校验不一致。', 409)
        return run

    def view(run, design, reminders):
        context = run['context']
        return dict(id=run['id'], design=design, reminders=reminders, preview=D.document(design, {}, context),
                    duration=(context.get('teaching_options') or {}).get('duration'),
                    labels=D.labels(design), kinds=D.KINDS, goal=run['goal'], topic=run['topic'],
                    warning='课堂设计由模型生成，重点、时间和题目答案都需要教师核对。')

    @app.errorhandler(DesignRequestError)
    def design_error(exc):
        return jsonify(error=str(exc), problems=exc.problems), exc.status

    @app.get('/api/lesson-designs/<int:run_id>')
    def lesson_design(run_id):
        run = candidate(run_id)
        return jsonify(view(run, run['response_metadata']['design'], run['response_metadata'].get('design_reminders', [])))

    @app.post('/api/lesson-designs/<int:run_id>/check')
    def check_design(run_id):
        """Validate an edited design without starting anything, so the page can show reminders."""
        run, data = candidate(run_id), request.get_json(silent=True)
        design, hard, soft = D.validate((data or {}).get('design'), run['context'])
        if design is None or hard:
            raise DesignRequestError('修改后的课堂设计未通过检查，请按提示调整。', 400, hard)
        return jsonify(view(run, design, soft))

    @app.post('/api/lesson-designs/<int:run_id>/expand')
    def expand_design(run_id):
        """Write the lesson from the confirmed design. The model is not asked for a design again."""
        run, data = candidate(run_id), request.get_json(silent=True)
        if not isinstance(data, dict):
            raise DesignRequestError('请求格式必须是 JSON 对象。')
        original = run['response_metadata']['design']
        design, hard, _ = D.validate(data.get('design', original), run['context'])
        if design is None or hard:
            raise DesignRequestError('课堂设计未通过检查，不能开始写作。', 400, hard)
        settings = {**run['context']['designed'], 'stop': False, 'feedback': None,
                    'confirmed': dict(design=design, source_run_id=run_id, edited=design != original)}
        context = {**run['context'], 'designed': settings}
        prepared = PreparedRequest(run['request_body'], run['request_sha256'], run['prompt_version'], run['input_version'])
        return jsonify(queue().submit(run['student_id'], run['topic_key'], run['goal'], context, prepared, parent=run_id)), 202

    @app.post('/api/lesson-designs/<int:run_id>/revise')
    def revise_design(run_id):
        """A new design from the previous one and the teacher's written request."""
        run, data = candidate(run_id), request.get_json(silent=True)
        note = data.get('note') if isinstance(data, dict) else None
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 1500:
            raise DesignRequestError('请填写 1—1500 字的修改意见。')
        previous = data.get('design', run['response_metadata']['design'])
        design, hard, _ = D.validate(previous, run['context'])
        if design is None or hard:
            raise DesignRequestError('当前课堂设计未通过检查，请先按提示调整。', 400, hard)
        settings = {**run['context']['designed'], 'stop': True, 'confirmed': None,
                    'feedback': dict(design=design, note=note.strip(), source_run_id=run_id)}
        context = {**run['context'], 'designed': settings}
        prepared = prepare_request(context, run['goal'], model())
        return jsonify(queue().submit(run['student_id'], run['topic_key'], run['goal'], context, prepared, parent=run_id)), 202
