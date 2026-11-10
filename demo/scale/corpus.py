"""Assemble the scale corpus: hand-written explanations plus computed worked examples."""
import json
from pathlib import Path

from examples import worked_examples

VERSION = 'scale-v1'
SOURCE_TITLE = '知页自编扩展语料（scale-v1，非教材原文）'
COURSES = ('linear_equations', 'conditional_probability')
DIRECTORY = Path(__file__).with_name('corpus')


def load_course(name, examples=8, seed=7):
    """One course with two materials per knowledge point; the same arguments give the same text."""
    data = json.loads((DIRECTORY / f'{name}.json').read_text(encoding='utf-8'))
    for index, point in enumerate(data['points'], 1):
        base = f'corpus:{VERSION}/{data["key"]}/{index:02d}'
        point['materials'] = [dict(title=point['name'] + '：概念与依据', content=point['content'], section=data['chapter'],
                                   source_title=SOURCE_TITLE, source_url=base + '-concept', role='concept')]
        if examples:
            point['materials'].append(dict(
                title=point['name'] + '：例题与检验', section=data['chapter'], source_title=SOURCE_TITLE,
                content=worked_examples(point['kind'], examples, f'{seed}:{index}'),
                source_url=base + '-examples', role='examples'))
    return data


def characters(course):
    return sum(len(m['content']) for p in course['points'] for m in p['materials'])
