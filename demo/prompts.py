"""Prepare one immutable request body for both persistence and model transport."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

PROMPT_VERSION = 'lecture-math-v3'
INPUT_VERSION = 'lecture-context-v4'
PROMPT_FILE = Path(__file__).with_name('prompts') / f'{PROMPT_VERSION}.md'


@dataclass(frozen=True)
class PreparedRequest:
    body: str
    sha256: str
    prompt_version: str = PROMPT_VERSION
    input_version: str = INPUT_VERSION


def prepare_request(context, goal, model):
    model_context = {**context, 'materials': [
        {**material, 'citation_id': f'M{material["id"]}'} for material in context['materials']
    ]}
    version = 'lecture-probability-v2' if context['topic'].get('course') == '大学概率论' else PROMPT_VERSION
    prompt_file = Path(__file__).with_name('prompts') / f'{version}.md'
    system = prompt_file.read_text(encoding='utf-8').strip()
    payload = {'model': model, 'messages': [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps({'goal': goal, 'context': model_context}, ensure_ascii=False)}
    ], 'temperature': 0.3, 'max_tokens': 2600, 'chat_template_kwargs': {'enable_thinking': False}}
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return PreparedRequest(body, hashlib.sha256(body.encode('utf-8')).hexdigest(), version,
                           'lecture-context-v4' if 'personalization_mode' in context else INPUT_VERSION)
