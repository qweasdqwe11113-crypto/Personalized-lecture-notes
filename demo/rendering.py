"""Conservative HTML export: Unicode formula blocks stay text, never executable HTML."""
import html
import re


def inline(text):
    escaped = html.escape(text)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    return re.sub(r'\[([MV]\d+)\]', r'<span class="citation">[\1]</span>', escaped)


def markdown_html(content):
    parts, list_type, fence, block = [], None, None, []

    def close_list():
        nonlocal list_type
        if list_type:
            parts.append(f'</{list_type}>')
            list_type = None

    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith(('```', '~~~')):
            token = line[:3]
            if fence == token:
                parts.append('<pre class="formula-block">' + html.escape('\n'.join(block)) + '</pre>')
                fence, block = None, []
            elif fence is None:
                close_list()
                fence = token
            else:
                block.append(raw)
            continue
        if fence:
            block.append(raw)
            continue
        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        item = re.match(r'^([-*]|\d+[.、])\s+(.+)$', line)
        if not line:
            close_list()
        elif heading:
            close_list()
            level = len(heading[1])
            parts.append(f'<h{level}>{inline(heading[2])}</h{level}>')
        elif item:
            kind = 'ol' if item[1][0].isdigit() else 'ul'
            if list_type != kind:
                close_list()
                list_type = kind
                parts.append(f'<{kind}>')
            value = f' value="{int(re.match(r"\d+", item[1])[0])}"' if kind == 'ol' else ''
            parts.append(f'<li{value}>{inline(item[2])}</li>')
        else:
            close_list()
            parts.append('<p>' + inline(line) + '</p>')
    close_list()
    if block:
        parts.append('<pre class="formula-block">' + html.escape('\n'.join(block)) + '</pre>')
    return ''.join(parts)


def visual_appendix(context):
    evidence = context.get('visual_evidence', [])
    if not evidence:
        return ''
    lines = ['\n\n## 图片解析依据（生成时快照，需核对）']
    for item in evidence:
        lines.extend(['', f'### [{item["citation_id"]}] {item.get("label", "图片")}',
                      item['content'],
                      f'识读模型：{item.get("model", "历史未记录")}；图片 SHA-256：{item.get("normalized_sha256", "历史未记录")}'])
    return '\n'.join(lines)
