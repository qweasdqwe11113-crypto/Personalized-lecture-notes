"""Allowlisted semantic design and versioned mappings for eight PPTX masters."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'ppt-templates'
MAPPING_VERSION = 'classroom-masters-v1'
DEFAULT_TEMPLATE = 'teaching-blue'
# Native master indexes, blank layout 6, and safe regions (fractions of canvas).
# Keep master artwork and original aspect ratio. Do not infer from model fields.
PROFILES = {
    'simple-clear': dict(masters=(0, 1), region=(0, 0, 1, 1)),
    'simple-dark': dict(masters=(0, 0), region=(0, 0, 1, 1), dark=True),
    'peter-river': dict(masters=(0, 1), region=(0, 0, 1, 1), dark=True),
    'vivid-blue': dict(masters=(0, 1), region=(0, 0, 1, .64)),
    'notebook': dict(masters=(0, 0), region=(0, .10, 1, .79)),
    'pencil': dict(masters=(1, 1), region=(0, .17, 1, .75)),
    'technical-office': dict(masters=(0, 0), region=(0, .26, 1, .52)),
    'cinema': dict(masters=(0, 0), region=(.04, .23, .92, .70)),
}

# New semantic mappings. Legacy profiles above remain frozen for old downloads.
SEMANTIC_PROFILES = {
    'simple-clear': dict(masters=(0, 1), region=(0, 0, 1, 1), skin='simple-clear',
        description='简洁浅色，保留原比例和浅色纹理',
        palette=dict(primary='#283D50', accent='#5C7C96', bg_sub='#F2F5F8', line='#D3DDE5')),
    'simple-dark': dict(masters=(0, 0), region=(0, 0, 1, 1), skin='simple-dark',
        description='简洁深色，使用浅色文字和深色内容区域',
        palette=dict(primary='#D3E8FF', accent='#91CCFF', text='#F4F7FB', muted='#CAD6E2',
                     hint='#ABBECE', bg_sub='#293845', line='#54697B', surface='#19232D', on_primary='#172738')),
    'peter-river': dict(masters=(0, 1), region=(0, 0, 1, 1), skin='peter-river',
        description='河流蓝，蓝色母版搭配深蓝书写区',
        palette=dict(primary='#D7EEFF', accent='#A7DEFF', text='#F5FAFF', muted='#D4E9F7',
                     hint='#B5D5EB', bg_sub='#20597F', line='#5B90B2', surface='#133D5C', on_primary='#133D5C')),
    'vivid-blue': dict(masters=(2, 2), region=(0, .045, 1, .91), skin='vivid-blue',
        description='明亮蓝，采用青蓝边框和橙色强调',
        palette=dict(primary='#086B8C', accent='#BC6900', text='#183443', muted='#466170',
                     hint='#647E8C', bg_sub='#EDF8FC', line='#C8E4EF')),
    'notebook': dict(masters=(0, 1), region=(0, .13, 1, .72), skin='notebook',
        description='笔记本，保留深蓝边框与横线纸张',
        palette=dict(primary='#08052F', accent='#67659C', text='#242337', muted='#55546B',
                     hint='#76758B', bg_sub='#F7F7FC', line='#D5D4E0')),
    'pencil': dict(masters=(1, 1), region=(0, .18, 1, .70), skin='pencil',
        description='铅笔，保留铅笔装饰并采用暖棕配色',
        palette=dict(primary='#6C482B', accent='#9C611E', text='#322C25', muted='#685B4D',
                     hint='#8B7865', bg_sub='#FCF7EF', line='#E3D6C5')),
    'technical-office': dict(masters=(0, 0), region=(0, 0, 1, 1), skin='technical-office', zones=True,
        description='技术办公，标题与正文分区避开铅笔和尺子',
        palette=dict(primary='#17556E', accent='#276995', text='#233943', muted='#4E6875',
                     hint='#6E8793', bg_sub='#EFF7FA', line='#C9DEE7')),
    'cinema': dict(masters=(0, 0), region=(.05, .24, .90, .68), skin='cinema',
        description='电影胶片，内容排在胶片边框内的白色区域',
        palette=dict(primary='#292929', accent='#85632A', text='#242424', muted='#565656',
                     hint='#777777', bg_sub='#F5F4F0', line='#DAD7D0')),
}


def template_record(identity, *, mapping_version=None):
    if identity == 'teaching-blue':
        path = ROOT / identity / 'theme.json'
        raw = path.read_bytes()
        theme = json.loads(raw)
        return dict(id=identity, name=theme['name'], path=path, sha256=hashlib.sha256(raw).hexdigest(),
                    width=1280 * 12700, height=720 * 12700, profile=dict(region=(0, 0, 1, 1)),
                    mapping_version='semantic-blue-v1', asset_kind='design', semantic=True, theme=theme,
                    description='蓝白教学，16:9 宽屏内容排版',
                    preview_path=ROOT / identity / 'preview.png')
    if not isinstance(identity, str) or identity not in PROFILES:
        raise ValueError('请选择有效的课件模板。')
    catalog = json.loads((ROOT / 'converted-pptx' / 'conversion.json').read_text(encoding='utf-8'))
    record = next(row for row in catalog['templates'] if row['id'] == identity)
    path = ROOT / 'converted-pptx' / (identity + '.pptx')
    payload = path.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    if sha != record['sha256']:
        raise ValueError('课件模板文件与转换清单指纹不一致，请先核对模板资产。')
    semantic = mapping_version != MAPPING_VERSION
    semantic_version = f'semantic-{identity}-v1'
    if semantic and mapping_version not in (None, semantic_version):
        raise ValueError('课件模板映射版本不受支持。')
    return dict(id=identity, name=record['name'] + ' · 内容自适应' if semantic else record['name'], path=path, sha256=sha,
                width=record['slide_width_emu'], height=record['slide_height_emu'],
                profile=SEMANTIC_PROFILES[identity] if semantic else PROFILES[identity],
                mapping_version=semantic_version if semantic else MAPPING_VERSION,
                asset_kind='pptx', semantic=semantic,
                description=SEMANTIC_PROFILES[identity]['description'] if semantic else '传统分栏母版',
                preview_path=ROOT / identity / 'semantic.preview.png' if semantic else ROOT / 'converted-pptx' / (identity + '.preview.png'))


def available_templates():
    return [dict(id=r['id'], name=r['name'], width=r['width'], height=r['height'], asset_kind=r['asset_kind'],
                 semantic=r['semantic'], description=r['description'],
                 preview_url=f'/api/presentation-templates/{r["id"]}/preview')
            for r in (template_record(identity) for identity in [DEFAULT_TEMPLATE, *PROFILES])]
