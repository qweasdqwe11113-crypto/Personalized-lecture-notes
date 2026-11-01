"""Allowlisted, versioned mappings for the eight converted local PPTX masters."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'ppt-templates'
MAPPING_VERSION = 'classroom-masters-v1'
DEFAULT_TEMPLATE = 'simple-clear'
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


def template_record(identity):
    if not isinstance(identity, str) or identity not in PROFILES:
        raise ValueError('请选择有效的课件模板。')
    catalog = json.loads((ROOT / 'converted-pptx' / 'conversion.json').read_text(encoding='utf-8'))
    record = next(row for row in catalog['templates'] if row['id'] == identity)
    path = ROOT / 'converted-pptx' / (identity + '.pptx')
    payload = path.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    if sha != record['sha256']:
        raise ValueError('课件模板文件与转换清单指纹不一致，请先核对模板资产。')
    return dict(id=identity, name=record['name'], path=path, sha256=sha,
                width=record['slide_width_emu'], height=record['slide_height_emu'],
                profile=PROFILES[identity], mapping_version=MAPPING_VERSION)


def available_templates():
    return [dict(id=r['id'], name=r['name'], width=r['width'], height=r['height'],
                 preview_url=f'/api/presentation-templates/{r["id"]}/preview')
            for r in (template_record(identity) for identity in PROFILES)]
