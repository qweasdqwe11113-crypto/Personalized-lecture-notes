"""Qwen transport. Credentials and raw service errors never enter saved traces."""
from dataclasses import dataclass, field
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from model_runtime import request_timeout


@dataclass
class ModelResult:
    content: str
    usage: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


class ModelError(ValueError):
    def __init__(self, code, message, stage='model', partial=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.partial = partial or ModelResult('')


def config_flags():
    try:
        parsed = urlsplit(os.environ.get('QWEN_BASE_URL', ''))
        valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                 and not any([parsed.username, parsed.password, parsed.query, parsed.fragment]))
    except ValueError:
        valid = False
    return {'key_present': bool(os.environ.get('QWEN_API_KEY')),
            'endpoint_format_ok': bool(valid), 'model_present': bool(os.environ.get('QWEN_MODEL', 'qwen3.8-27b').strip())}


def clean_content(content):
    if not isinstance(content, str):
        return ''
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.S | re.I).strip()
    # An unfinished reasoning block is not a lecture or a reviewable partial draft.
    return '' if re.search(r'</?think>', content, re.I) else content


def call_qwen(prepared, on_attempt):
    key = os.environ.get('QWEN_API_KEY', '')
    base = os.environ.get('QWEN_BASE_URL', '').rstrip('/')
    try:
        parsed = urlsplit(base)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError
        url = base + '/chat/completions'
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        req = urllib.request.Request(url, data=prepared.body.encode('utf-8'), headers=headers)
    except ValueError:
        raise ModelError('invalid_endpoint', '请检查本地 QWEN_BASE_URL 的服务地址格式。', 'configuration') from None
    on_attempt()  # Commit the attempt before network I/O; never recompute the body.
    try:
        with urllib.request.urlopen(req, timeout=request_timeout()) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ModelError('http_error', f'模型服务返回 HTTP {code}，请检查服务配置或稍后重试。',
                         partial=ModelResult('', metadata={'http_status': code})) from None
    except urllib.error.URLError as exc:
        timeout = isinstance(exc.reason, TimeoutError)
        raise ModelError('timeout' if timeout else 'unreachable',
                         'Qwen 请求超时，请稍后重试。' if timeout else '无法连接 Qwen，请确认课题组内网连接后重试。') from None
    except TimeoutError:
        raise ModelError('timeout', 'Qwen 请求超时，请稍后重试。') from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ModelError('invalid_json', '模型服务没有返回有效 JSON。') from None
    except OSError:
        raise ModelError('connection_error', '模型连接中断，请稍后重试。') from None
    try:
        choice = result['choices'][0]
        raw_content = choice['message'].get('content')
        usage = result.get('usage')
        metadata = {k: v for k, v in {
            'response_model': result.get('model'), 'finish_reason': choice.get('finish_reason')
        }.items() if isinstance(v, str)}
        output = ModelResult(clean_content(raw_content), usage if isinstance(usage, dict) else {}, metadata)
        if choice.get('finish_reason') == 'length':
            raise ModelError('truncated', '模型输出达到长度上限，未保存为完整讲义，请缩短教学目标后重试。',
                             'output', output)
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise ModelError('empty_output', '模型没有返回讲义正文，请重试。', 'output', output)
        if not output.content:
            raise ModelError('unfinished_output', '模型未完成正文输出，请重试。', 'output', output)
        return output
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ModelError('invalid_protocol', '模型响应格式不符合 chat/completions 协议。') from None
