"""Downloads of a lecture or one teacher revision as Markdown, plain text, HTML or Word."""
import html
from io import BytesIO
from urllib.parse import quote

from flask import Response, jsonify, request

import richdoc
from edition import release
from rendering import markdown_html, plain_formulas, visual_appendix
from reviews import STATUS_LABELS, lecture_row, version_row


def export_notice():
    """The closing line of a downloaded lecture. Only the demonstration data has made-up students."""
    return '说明：内容由模型生成，使用前请教师审核。' if release() else '说明：虚拟学生演示；模型生成内容需人工审核。'


def build_reference_text(row):
    sources = '\n'.join(
        f'- [M{m["id"]}] {m["source_title"]}: {m["source_url"]}\n  {m["section"]}'
        for m in row['context']['materials']
    )
    warning = row['citation_check'].get('review_warning', '')
    text = row.get('export_note', '') + row['content'] + '\n\n## 参考材料\n\n' + sources + visual_appendix(row['context'])
    text += '\n\n' + export_notice() + '\n'
    if warning:
        text += '\n审核提醒：' + warning + '\n'
    return text


def build_export_text(row):
    """用于 txt 下载：把 Markdown 标记去掉一部分，保持可读。"""
    content = build_reference_text({**row, 'content': richdoc.plain_text(row['content'])}
                                   if '```figure' in row['content'] else row)
    lines = []
    for line in plain_formulas(content).splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            stripped = stripped.lstrip('#').strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            stripped = '· ' + stripped[2:]
        lines.append(stripped)
    return '\n'.join(lines)


def build_export_html(row):
    """把模型返回的 Markdown 做保守转换；所有模型文本都先转义。"""
    body_parts = [markdown_html(row['content']), markdown_html(visual_appendix(row['context']))]

    sources_html = []
    for m in row['context']['materials']:
        sources_html.append(
            '<li>'
            f'<strong>[M{m["id"]}]</strong> '
            f'{html.escape(m["source_title"])}：'
            f'<a href="{html.escape(m["source_url"])}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(m["source_url"])}</a>'
            f'<br><span>{html.escape(m["section"])}</span>'
            '</li>'
        )

    warning = row['citation_check'].get('review_warning', '')
    warning_html = f'<p class="warning">审核提醒：{html.escape(warning)}</p>' if warning else ''
    student_name = html.escape(row['context']['student']['name'])
    topic_title = html.escape(row['context']['topic']['title'])

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{student_name} · {topic_title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Noto Sans SC", "Microsoft YaHei", sans-serif;
       max-width: 820px; margin: 40px auto; padding: 0 24px; line-height: 1.9; color: #162235; }}
h1 {{ font-size: 28px; }}
h2 {{ font-size: 20px; margin-top: 32px; border-bottom: 1px solid #e4e8ef; padding-bottom: 8px; }}
.citation {{ color: #2858dc; font-size: 13px; font-weight: 600; }}
.formula-block {{ white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.7; padding:12px; background:#f6f7fb; }}
.math {{ white-space: nowrap; }}
.math-display {{ white-space:pre-wrap; padding:12px 16px; background:#f6f8ff; border-left:3px solid #365bd6; }}
table {{ border-collapse:collapse; margin:14px 0; }} th,td {{ border:1px solid #d7dfe9; padding:8px 12px; text-align:left; }} th {{ background:#edf2ff; }}
figure {{ margin:18px 0; text-align:center; }} figure img {{ max-width:100%; height:auto; }} figcaption {{ font-size:12px; color:#68758a; }}
@media print {{ h1,h2,h3 {{ break-after:avoid-page; }} pre,li {{ break-inside:avoid-page; }} }}
.meta {{ color: #68758a; font-size: 13px; margin-bottom: 28px; }}
.warning {{ background: #f3f6fb; padding: 12px 15px; border-radius: 6px; color: #66738a; font-size: 13px; }}
a {{ color: #4268ba; }}
</style>
</head>
<body>
<h1>{topic_title}</h1>
<p class="meta">学生：{student_name} · 模型：{html.escape(row['model'])} · 已保存 #{row['id']}</p>
<p class="meta" style="white-space:pre-wrap">{html.escape(row.get('export_note', ''))}</p>
{''.join(body_parts)}
<h2>{html.escape(row.get('source_heading', '参考材料'))}</h2>
<ul>{''.join(sources_html)}</ul>
<p class="meta">{html.escape(export_notice())}</p>
{warning_html}
</body>
</html>'''


def build_export_docx(row):
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Noto Sans SC'
    style.font.size = Pt(11)

    if row.get('export_note'):
        doc.add_paragraph(row['export_note'])

    for kind, value in richdoc.blocks(row['content']):
        if kind == 'figure':
            richdoc.add_figure(doc, value)
            continue
        if kind == 'math':
            richdoc.add_display_math(doc, value)
            continue
        if kind == 'table':
            richdoc.add_table(doc, value)
            continue
        if kind == 'code':
            for text in value.splitlines():
                doc.add_paragraph(text)
            continue
        line = value.strip()
        if not line:
            continue
        if line.startswith('### '):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith('## '):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith('# '):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith('- ') or line.startswith('* '):
            richdoc.add_inline(doc.add_paragraph(style='List Bullet'), line[2:].strip())
        else:
            richdoc.add_inline(doc.add_paragraph(), line)

    doc.add_heading(row.get('source_heading', '参考材料'), level=2)
    for m in row['context']['materials']:
        doc.add_paragraph(
            f'[M{m["id"]}] {m["source_title"]}: {m["source_url"]} — {m["section"]}',
            style='List Bullet'
        )

    for line in visual_appendix(row['context']).splitlines():
        if line.startswith('### '):doc.add_heading(line[4:],level=3)
        elif line.startswith('## '):doc.add_heading(line[3:],level=2)
        elif line:doc.add_paragraph(line)
    doc.add_paragraph(export_notice())
    warning = row['citation_check'].get('review_warning', '')
    if warning:
        doc.add_paragraph('审核提醒：' + warning)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def export_response(row, markdown, note, filename):
    fmt = request.args.get('format', 'md').lower()
    if fmt not in {'md', 'txt', 'html', 'docx'}:
        return jsonify(error='不支持的下载格式。'), 400
    export_row = {**row, 'export_note': note}
    if fmt == 'md':
        content, mime = markdown + visual_appendix(row['context']), 'text/markdown; charset=utf-8'
    elif fmt == 'txt':
        content, mime = build_export_text(export_row), 'text/plain; charset=utf-8'
    elif fmt == 'html':
        content, mime = build_export_html(export_row), 'text/html; charset=utf-8'
    else:
        content = build_export_docx(export_row).getvalue()
        mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    response = Response(content, content_type=mime)
    name = filename + '.' + fmt
    response.headers['Content-Disposition'] = (
        f"attachment; filename=lecture-{row['id']}.{fmt}; filename*=UTF-8''{quote(name)}")
    return response


def register_exports(app, db):
    @app.get('/api/lectures/<int:lecture_id>/download')
    def download_lecture(lecture_id):
        with db() as conn:
            row = conn.execute('SELECT * FROM lectures WHERE id=%s',(lecture_id,)).fetchone()
        if not row:
            return jsonify(error='讲义不存在。'),404
        sources = '\n'.join(f'- [M{m["id"]}] {m["source_title"]}: {m["source_url"]}\n  {m["section"]}'
                            for m in row['context']['materials'])
        warning = row['citation_check'].get('review_warning','')
        content = '导出版本：模型原稿 #'+str(lecture_id)+'；审核状态：原稿未审核（教师修订另行导出）。\n\n'+row['content']+'\n\n## 参考材料\n\n'+sources+'\n\n'+export_notice()+'\n'
        if warning:
            content += '\n审核提醒：'+warning+'\n'
        note = '导出版本：模型原稿 #'+str(lecture_id)+'；审核状态：原稿未审核（教师修订另行导出）。\n\n'
        return export_response(row, content, note, f'{row["context"]["student"]["name"]}-讲义-{lecture_id}')


    @app.get('/api/lectures/<int:lecture_id>/revisions/<int:number>/download')
    def download_revision(lecture_id, number):
        if number == 0:
            return download_lecture(lecture_id)
        with db() as conn:
            lecture = lecture_row(conn, lecture_id)
            revision = version_row(conn, lecture, number)
        sources = '\n'.join(f'- [M{m["id"]}] {m["source_title"]}: {m["source_url"]}\n  {m["section"]}' for m in lecture['context']['materials'])
        duration = f'{revision["review_seconds"]} 秒' if revision['review_seconds'] else '未记录'
        note = (f'导出版本：教师修订稿，讲义 #{lecture_id} / R{number}\n'
                   f'保存时审核状态：{STATUS_LABELS[revision["status"]]}\n'
                   f'记录者：{revision["recorder"]}；保存时间：{revision["created_at"].isoformat()}\n'
                   f'修改理由：{revision["reason"]}\n审核/修改用时（记录者自报）：{duration}\n\n')
        content = (note + revision['content'] + '\n\n## 参考材料（原生成时快照）\n\n' + sources
                   + '\n\n说明：' + ('' if release() else '虚拟学生演示；')
                   + '审核状态为该版本保存时的教师记录，不是程序质量认证。\n')
        export_row = {**lecture, 'content': revision['content'],
                      'source_heading': '参考材料（原生成时快照）',
                      'citation_check': {'review_warning': '审核状态为该版本保存时的教师记录，不是程序质量认证。'}}
        return export_response(export_row, content, note, f'lecture-{lecture_id}-R{number}')
