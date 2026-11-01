"""Lecture-to-slide requests, strict output checks and recoverable queued exports."""
import hashlib
import json
import os
import re
import time
from pathlib import Path

from flask import Response, jsonify, request, send_file
from psycopg.types.json import Jsonb

from prompts import PreparedRequest
from qwen import ModelError
from reviews import ReviewError, STATUS_LABELS, lecture_row, version_row
from runs import mark_attempt, run_detail
from pptx_export import build_pptx as build_legacy_pptx
from pptkit_export import build_pptx as build_kit_pptx, EXPORT_VERSION
from ppt_templates import (DEFAULT_TEMPLATE, MAPPING_VERSION, ROOT as TEMPLATE_ROOT,
                           available_templates, template_record)

MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
PROMPT_VERSION = 'lecture-ppt-v2'
PROMPT = (Path(__file__).with_name('prompts') / f'{PROMPT_VERSION}.md').read_text(encoding='utf-8').strip()


def fingerprint(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_pptx(deck, source, context):
    version = source.get('export_version', 'pptx-v1')
    if version == 'pptx-v1':
        return build_legacy_pptx(deck, source, context)
    if version == EXPORT_VERSION:
        return build_kit_pptx(deck, source, context)
    raise ReviewError('课件渲染版本不受支持，请核对生成记录。', 409)


def prepare_slides(context, model):
    edit = context['ppt_export']
    sources = [{'id': f'M{m["id"]}', 'title': m['title'], 'content': m['content']}
               for m in context.get('materials', [])]
    sources += [{'id': v['citation_id'], 'title': v.get('label', ''), 'content': v['content']}
                for v in context.get('visual_evidence', [])]
    user = dict(lecture=edit['base_content'], goal=edit['goal'], slide_count=edit['slide_count'],
                instruction=edit['instruction'], sources=sources,
                teaching_options=context.get('teaching_options', {}))
    user_text = json.dumps(user, ensure_ascii=False)
    if len(user_text) > 56000:
        raise ReviewError('课件输入超过 56000 字符，请先精简讲义或关联材料。')
    body = json.dumps(dict(model=model, messages=[dict(role='system', content=PROMPT),
                      dict(role='user', content=user_text)], temperature=0.3, max_tokens=16000,
                      chat_template_kwargs={'enable_thinking': False}), ensure_ascii=False, separators=(',', ':'))
    return PreparedRequest(body, fingerprint(body), PROMPT_VERSION, 'lecture-ppt-context-v1')


def parse_deck(content, context):
    def invalid(message):
        raise ModelError('invalid_ppt', '课件输出未通过检查：' + message + '。请按原输入重试。', 'output')

    if not isinstance(content, str) or len(content) > 90000:
        invalid('输出长度异常')
    text = content.strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    try:
        deck = json.loads(text)
    except (ValueError, TypeError):
        invalid('模型未返回有效 JSON')

    def field(value, maximum, minimum=1):
        return (isinstance(value, str) and minimum <= len(value.strip()) <= maximum
                and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]', value))

    if not isinstance(deck, dict) or not field(deck.get('title'), 60):
        invalid('缺少有效课程标题')
    slides = deck.get('slides')
    if not isinstance(slides, list) or len(slides) != context['ppt_export']['slide_count']:
        invalid('实际页数与设置不一致')
    allowed = {f'M{m["id"]}' for m in context.get('materials', [])}
    allowed |= {v['citation_id'] for v in context.get('visual_evidence', [])}
    result = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            invalid(f'第{index+1}页结构错误')
        if not field(slide.get('title'), 34) or slide.get('kind') not in {'cover', 'concept', 'example', 'question', 'summary'}:
            invalid(f'第{index+1}页标题或类型错误')
        bullets = slide.get('bullets')
        if (not isinstance(bullets, list) or not 1 <= len(bullets) <= 4
                or any(not field(b, 64) for b in bullets) or sum(map(len, bullets)) > 180):
            invalid(f'第{index+1}页投屏要点过长或缺失')
        if not field(slide.get('question'), 70, 0) or not field(slide.get('notes'), 1600, 40):
            invalid(f'第{index+1}页提问或讲稿长度不符合要求')
        if any(re.search(r'[\r\n\t]', s) for s in [slide['title'], *bullets, slide['question']]):
            invalid(f'第{index+1}页投屏文字包含换行或制表符')
        if index == 0 and (len(bullets) > 2 or sum(map(len, bullets)) > 80 or slide['question']):
            invalid('封面内容过多')
        sources = slide.get('sources')
        if (not isinstance(sources, list) or len(sources) > 30
                or any(not isinstance(s, str) or s not in allowed for s in sources)):
            invalid(f'第{index+1}页引用了不存在的来源')
        cited = set(re.findall(r'\[([MV]\d+)\]', '\n'.join([slide['title'], *bullets, slide['question'], slide['notes']])))
        if cited - allowed:
            invalid(f'第{index+1}页正文包含无效来源编号')
        result.append(dict(kind=slide['kind'], title=slide['title'].strip(),
                           bullets=[b.strip() for b in bullets], question=slide['question'].strip(),
                           notes=slide['notes'].strip(), sources=sorted(set(sources) | cited)))
    if result[0]['kind'] != 'cover' or result[-1]['kind'] != 'summary':
        invalid('缺少封面或末页总结')
    return dict(title=deck['title'].strip(), slides=result)


def execute_presentation(db, call_model, job_id, run_id, context, prepared):
    start = time.monotonic()
    def attempted():
        with db() as conn:
            mark_attempt(conn, run_id)
    output = call_model(prepared, attempted)
    try:
        deck = parse_deck(output.content, context)
        # Exercise the actual exporter before recording a usable result.
        build_pptx(deck, context['ppt_export'], context)
    except ModelError as exc:
        exc.partial = output
        raise
    with db() as conn:
        row = conn.execute('''UPDATE generation_runs SET status='succeeded',finished_at=now(),
            elapsed_seconds=%s,content=%s,usage=%s,response_metadata=%s,lecture_id=%s
            WHERE id=%s AND status='running' RETURNING id''',
            (round(time.monotonic()-start, 2), json.dumps(deck, ensure_ascii=False), Jsonb(output.usage),
             Jsonb({**output.metadata, 'operation': 'ppt_export',
                    'export_version': context['ppt_export'].get('export_version', 'pptx-v1')}),
             context['ppt_export']['lecture_id'], run_id)).fetchone()
        if not row:
            raise RuntimeError('PPT run is no longer running.')
        conn.execute("UPDATE generation_jobs SET status='succeeded',finished_at=now() WHERE id=%s", (job_id,))


def register_presentations(app, db, queue):
    @app.get('/api/presentation-templates')
    def presentation_templates():
        return jsonify(templates=available_templates(), default=DEFAULT_TEMPLATE)

    @app.get('/api/presentation-templates/<identity>/preview')
    def template_preview(identity):
        try:
            template_record(identity)
        except ValueError as exc:
            raise ReviewError(str(exc), 404) from exc
        return send_file(TEMPLATE_ROOT / 'converted-pptx' / (identity + '.preview.png'), mimetype='image/png')

    @app.post('/api/lectures/<int:lecture_id>/presentations')
    def create_presentation(lecture_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ReviewError('请求必须为 JSON 对象。')
        number, count = data.get('base_revision'), data.get('slide_count', 12)
        instruction = data.get('instruction', '')
        try:
            template = template_record(data.get('template_id', DEFAULT_TEMPLATE))
        except ValueError as exc:
            raise ReviewError(str(exc)) from exc
        if type(number) is not int or number < 0:
            raise ReviewError('请选择有效的讲义版本。')
        if type(count) is not int or not 6 <= count <= 20:
            raise ReviewError('课件页数应为 6—20 页（包含封面与总结）。')
        if not isinstance(instruction, str) or len(instruction) > 1000:
            raise ReviewError('课件要求请控制在 1000 字以内。')
        with db() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            lecture = lecture_row(conn, lecture_id)
            version = version_row(conn, lecture, number)
        if not version['content'].strip() or len(version['content']) > 20000:
            raise ReviewError('讲义正文为空或超过 20000 字符，请先保存精简的修订稿。')
        edit = dict(lecture_id=lecture_id, base_revision=number, base_content=version['content'],
                    content_sha256=fingerprint(version['content']), slide_count=count,
                    instruction=instruction.strip(), goal=lecture['goal'],
                    source_status=STATUS_LABELS.get(version['status'], '原稿未审核'),
                    export_version=EXPORT_VERSION, template_id=template['id'], template_name=template['name'],
                    template_sha256=template['sha256'], template_mapping_version=MAPPING_VERSION)
        context = {**lecture['context'], 'ppt_export': edit}
        prepared = prepare_slides(context, os.environ.get('QWEN_MODEL', 'qwen3.8-27b'))
        return jsonify(queue().submit(lecture['student_id'], 'ppt_export', lecture['goal'], context, prepared)), 202

    @app.get('/api/lectures/<int:lecture_id>/presentations')
    def list_presentations(lecture_id):
        with db() as conn:
            lecture_row(conn, lecture_id)
            rows = conn.execute('''SELECT j.id,j.status,j.run_id,r.context->'ppt_export' AS source,
                r.created_at,r.error_message FROM generation_jobs j JOIN generation_runs r ON r.id=j.run_id
                WHERE r.context->'ppt_export'->>'lecture_id'=%s ORDER BY r.id DESC LIMIT 30''',
                (str(lecture_id),)).fetchall()
        # The list needs provenance, not a second copy of the entire lecture.
        for row in rows:
            row['source'].pop('base_content', None)
        return jsonify(presentations=rows)

    def completed(run_id):
        with db() as conn:
            run = run_detail(conn, run_id)
        if not run or not run['context'].get('ppt_export'):
            raise ReviewError('课件记录不存在。', 404)
        if run['status'] != 'succeeded' or not run['integrity_valid']:
            raise ReviewError('课件尚未成功生成或输入校验不一致。', 409)
        return run, parse_deck(run['content'], run['context'])

    @app.get('/api/presentations/<int:run_id>')
    def presentation_detail(run_id):
        run, deck = completed(run_id)
        source = {k: v for k, v in run['context']['ppt_export'].items() if k != 'base_content'}
        return jsonify(id=run_id, deck=deck, source=source,
                       warning='课件为 AI 改编稿，尚未经独立教师审核；请核对公式、答案与来源。')

    @app.get('/api/presentations/<int:run_id>/download')
    def download_presentation(run_id):
        run, deck = completed(run_id)
        source = run['context']['ppt_export']
        payload = build_pptx(deck, {**source, 'run_id': run_id}, run['context'])
        response = Response(payload, mimetype=MIME)
        response.headers['Content-Disposition'] = (
            f'attachment; filename=lecture-{source["lecture_id"]}-R{source["base_revision"]}-ppt-{run_id}.pptx')
        return response
