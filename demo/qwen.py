"""Qwen transport. Credentials and raw service errors never enter saved traces."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
import re
import threading
import time
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


_STREAM = threading.local()


@contextmanager
def stream_to(callback):
    """Deliver streamed text pieces of calls made by this thread to callback(piece).

    A thread-local listener keeps call_qwen's two-argument signature, so every caller
    and test double that knows nothing about streaming keeps working unchanged.
    """
    previous = getattr(_STREAM, 'callback', None)
    _STREAM.callback = callback
    try:
        yield
    finally:
        _STREAM.callback = previous


def stream_listener():
    """The current thread's listener, for test doubles that imitate a streaming service."""
    return getattr(_STREAM, 'callback', None)


def visible_text(text):
    """What may be shown while output is still arriving: reasoning blocks never are."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S | re.I)
    start = re.search(r'<think>', text, re.I)
    return (text[:start.start()] if start else text).lstrip()


def _read_stream(response):
    """Collect an OpenAI-style SSE stream into the same shape a plain reply has."""
    parts, usage, model, finish = [], None, None, None
    callback = getattr(_STREAM, 'callback', None)
    deadline = time.monotonic() + request_timeout()

    def partial():
        return ModelResult(clean_content(''.join(parts)), usage if isinstance(usage, dict) else {},
                           {'finish_reason': finish} if isinstance(finish, str) else {})
    try:
        for raw in response:
            if time.monotonic() > deadline:
                raise ModelError('timeout', 'Qwen 请求超时，请稍后重试。', partial=partial())
            line = raw.decode('utf-8').strip()
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                raise ModelError('invalid_json', '模型服务没有返回有效 JSON。', partial=partial()) from None
            if not isinstance(chunk, dict):
                continue
            if chunk.get('error'):
                raise ModelError('stream_error', '模型服务在输出过程中报告错误，请稍后重试。', partial=partial())
            if isinstance(chunk.get('usage'), dict):
                usage = chunk['usage']
            if isinstance(chunk.get('model'), str):
                model = chunk['model']
            for choice in chunk.get('choices') or []:
                if not isinstance(choice, dict):
                    continue
                piece = (choice.get('delta') or {}).get('content')
                if isinstance(piece, str) and piece:
                    parts.append(piece)
                    if callback:
                        callback(piece)
                if isinstance(choice.get('finish_reason'), str):
                    finish = choice['finish_reason']
    except (UnicodeDecodeError, AttributeError, TypeError):
        raise ModelError('invalid_protocol', '模型响应格式不符合 chat/completions 协议。', partial=partial()) from None
    except TimeoutError:
        raise ModelError('timeout', 'Qwen 请求超时，请稍后重试。', partial=partial()) from None
    except OSError:
        raise ModelError('connection_error', '模型连接中断，请稍后重试。', partial=partial()) from None
    return {'choices': [{'message': {'content': ''.join(parts)}, 'finish_reason': finish}],
            'usage': usage, 'model': model}


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
            # The frozen body decides the transport; old saved requests stay non-streaming.
            headers = getattr(response, 'headers', None)
            if 'text/event-stream' in ((headers.get('Content-Type') if headers else '') or ''):
                result = _read_stream(response)
            else:
                result = json.load(response)
    except ModelError:
        raise
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
