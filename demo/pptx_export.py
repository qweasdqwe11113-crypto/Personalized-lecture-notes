"""Portable, editable Office Open XML slides; no desktop Office dependency.

Only text and speaker notes are authored here. All model text stays text (never
XML, paths, links or executable content). Geometry is a fixed 16:9 classroom layout.
"""
from io import BytesIO
import re
import unicodedata
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
FONT = 'Microsoft YaHei'


def safe(value):
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', str(value))
    return escape(value, {'"': '&quot;'})


def wrap(text, width=66):
    """Deterministic conservative wrapping, accounting for CJK glyph width."""
    lines, line, units = [], '', 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if char == '\n' or units + size > width:
            lines.append(line)
            line, units = '', 0
        if char != '\n':
            line += char
            units += size
    lines.append(line)
    return lines


def textbox(number, name, paragraphs, x, y, w, h, size=2400, color='18344C', bold=False):
    def emu(px):
        return int(px * 9525)
    body = []
    for text in paragraphs:
        runs = []
        for index, line in enumerate(text.split('\n')):
            if index:
                runs.append('<a:br/>')
            runs.append(f'<a:r><a:rPr lang="zh-CN" sz="{size}" b="{int(bold)}">'
                        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
                        f'<a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/>'
                        f'<a:cs typeface="{FONT}"/></a:rPr><a:t>{safe(line)}</a:t></a:r>')
        body.append('<a:p><a:pPr><a:lnSpc><a:spcPct val="110000"/></a:lnSpc>'
                    '<a:spcAft><a:spcPts val="800"/></a:spcAft><a:buNone/></a:pPr>'
                    + ''.join(runs) + f'<a:endParaRPr lang="zh-CN" sz="{size}"/></a:p>')
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{number}" name="{safe(name)}"/>'
            '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>'
            f'<a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(w)}" cy="{emu(h)}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>'
            '<a:ln><a:noFill/></a:ln></p:spPr><p:txBody>'
            '<a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t">'
            '<a:noAutofit/></a:bodyPr><a:lstStyle/>' + ''.join(body) + '</p:txBody></p:sp>')


def group():
    return ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def relationships(items):
    return XML + f'<Relationships xmlns="{REL}">' + ''.join(
        f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{safe(target)}"/>'
        for rid, kind, target in items) + '</Relationships>'


def source_notes(slide, context):
    records = {f'M{m["id"]}': f'{m["title"]}\n{m.get("source_title", "")}\n{m.get("source_url", "")}\n{m.get("section", "")}'
               for m in context.get('materials', [])}
    records.update({v['citation_id']: f'{v.get("label", "图片解析")}\n识读模型：{v.get("model", "未记录")}\n'
                    f'图片指纹：{v.get("normalized_sha256", "未记录")}\n{v["content"]}'
                    for v in context.get('visual_evidence', [])})
    return '\n\n'.join(f'[{key}] {records[key]}' for key in slide['sources'] if key in records) or '本页未标注资料来源，请教师核查。'


def build_pptx(deck, source, context):
    slides = deck['slides']
    parts, overrides = {}, []
    def put(path, content, kind=None):
        parts[path] = content
        if kind:
            overrides.append(f'<Override PartName="/{path}" ContentType="application/vnd.openxmlformats-officedocument.{kind}+xml"/>')

    put('_rels/.rels', relationships([('rId1', 'officeDocument', 'ppt/presentation.xml')]))
    ids = ''.join(f'<p:sldId id="{256+i}" r:id="rId{i+2}"/>' for i in range(len(slides)))
    put('ppt/presentation.xml', XML + f'<p:presentation xmlns:a="{A}" xmlns:r="{R}" xmlns:p="{P}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
        '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>', 'presentationml.presentation.main')
    put('ppt/_rels/presentation.xml.rels', relationships([
        ('rId1', 'slideMaster', 'slideMasters/slideMaster1.xml'),
        *[(f'rId{i+2}', 'slide', f'slides/slide{i+1}.xml') for i in range(len(slides))]]))
    put('ppt/slideMasters/slideMaster1.xml', XML + f'<p:sldMaster xmlns:a="{A}" xmlns:r="{R}" xmlns:p="{P}">'
        f'<p:cSld><p:spTree>{group()}</p:spTree></p:cSld>'
        '<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
        'bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        '<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>', 'presentationml.slideMaster')
    put('ppt/slideMasters/_rels/slideMaster1.xml.rels', relationships([
        ('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'), ('rId2', 'theme', '../theme/theme1.xml')]))
    put('ppt/slideLayouts/slideLayout1.xml', XML + f'<p:sldLayout xmlns:a="{A}" xmlns:r="{R}" xmlns:p="{P}" type="blank" preserve="1">'
        f'<p:cSld name="授课空白版式"><p:spTree>{group()}</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>', 'presentationml.slideLayout')
    put('ppt/slideLayouts/_rels/slideLayout1.xml.rels', relationships([('rId1', 'slideMaster', '../slideMasters/slideMaster1.xml')]))
    colors = dict(dk1='18344C', lt1='FFFFFF', dk2='123A50', lt2='F2F6F8', accent1='146B79', accent2='C16837',
                  accent3='365E91', accent4='6C7484', accent5='A27833', accent6='56856F', hlink='146B79', folHlink='6C7484')
    scheme = ''.join(f'<a:{k}><a:srgbClr val="{v}"/></a:{k}>' for k, v in colors.items())
    fonts = ''.join(f'<a:{k}><a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/><a:cs typeface="{FONT}"/></a:{k}>'
                    for k in ('majorFont', 'minorFont'))
    # Standard theme entries allow PowerPoint to edit objects with normal defaults.
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
    put('ppt/theme/theme1.xml', XML + f'<a:theme xmlns:a="{A}" name="课堂">'
        f'<a:themeElements><a:clrScheme name="课堂">{scheme}</a:clrScheme><a:fontScheme name="中文">{fonts}</a:fontScheme>'
        f'<a:fmtScheme name="简洁"><a:fillStyleLst>{fill*3}</a:fillStyleLst><a:lnStyleLst>{line*3}</a:lnStyleLst>'
        '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
        f'<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst>{fill*3}</a:bgFillStyleLst>'
        '</a:fmtScheme></a:themeElements></a:theme>', 'theme')

    version = f'R{source["base_revision"]}' if source['base_revision'] else '原稿'
    for i, slide in enumerate(slides, 1):
        cover = i == 1
        foreground = 'FFFFFF' if cover else '18344C'
        title = '\n'.join(wrap(slide['title'], 46 if cover else 64))
        shapes = textbox(2, '页标题', [title], 72, 90 if cover else 58, 1136, 160 if cover else 125,
                         4200 if cover else 3200, foreground, True)
        bullets = ['\n'.join(wrap(b, 66)) for b in slide['bullets']]
        shapes += textbox(3, '投屏要点', bullets, 76, 280 if cover else 196, 1128, 320 if cover else 365,
                          2400, foreground)
        if slide['question']:
            shapes += textbox(4, '课堂提问', ['\n'.join(wrap('课堂提问：' + slide['question'], 82))],
                              76, 577, 1128, 88, 2000, 'ADE2DF' if cover else '146B79', True)
        footer = f'讲义 #{source["lecture_id"]} · {version} · AI 改编稿，待教师核查     {i} / {len(slides)}'
        shapes += textbox(5, '页码与版本', [footer], 76, 684, 1128, 25, 1000, 'C0D1DA' if cover else '62788A')
        put(f'ppt/slides/slide{i}.xml', XML + f'<p:sld xmlns:a="{A}" xmlns:r="{R}" xmlns:p="{P}">'
            f'<p:cSld name="{safe(slide["title"])}"><p:bg><p:bgPr><a:solidFill><a:srgbClr val="'
            + ('123A50' if cover else 'FFFFFF') + '"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
            f'<p:spTree>{group()}{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>',
            'presentationml.slide')
        put(f'ppt/slides/_rels/slide{i}.xml.rels', relationships([
            ('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'), ('rId2', 'notesSlide', f'../notesSlides/notesSlide{i}.xml')]))
        notes = (slide['notes'] + '\n\n本页材料依据（仅编号有效性经过程序检查，支持关系需人工核对）：\n'
                 + source_notes(slide, context) + f'\n\n讲义 #{source["lecture_id"]} / {version}；源版本保存时状态：'
                 + source['source_status'] + '\n课件为独立 AI 改编稿，源版本审核状态不代表课件已审核。'
                 + f'\n源正文 SHA-256：{source["content_sha256"]}\n生成记录：{source.get("run_id", "见网页生成记录")}')
        note_shape = textbox(2, '教师讲稿', notes.splitlines(), 60, 40, 600, 840, 1200)
        # Body placeholder makes notes visible in PowerPoint's Notes pane.
        note_shape = note_shape.replace('<p:nvPr/>', '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr>', 1)
        put(f'ppt/notesSlides/notesSlide{i}.xml', XML + f'<p:notes xmlns:a="{A}" xmlns:r="{R}" xmlns:p="{P}">'
            f'<p:cSld><p:spTree>{group()}{note_shape}</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>', 'presentationml.notesSlide')
        put(f'ppt/notesSlides/_rels/notesSlide{i}.xml.rels', relationships([('rId1', 'slide', f'../slides/slide{i}.xml')]))
    put('[Content_Types].xml', XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>' + ''.join(overrides) + '</Types>')
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content.encode('utf-8'))
    return output.getvalue()
