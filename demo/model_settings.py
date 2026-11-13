"""Local model settings. Persist atomically; apply on the next server start."""
import base64
import io
import json
import math
import os
from pathlib import Path
import tempfile
from threading import Lock
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key
from flask import jsonify, request
from PIL import Image, ImageDraw
from qwen import clean_content
from model_runtime import request_timeout

CHANNELS = {'llm': 'QWEN', 'vlm': 'VLM', 'embedding': 'QWEN_EMBEDDING'}
SUFFIXES = {'base_url': 'BASE_URL', 'model': 'MODEL', 'api_key': 'API_KEY'}
SAVE_LOCK = Lock()


class SettingsError(ValueError):
    pass


def read_channels(values):
    channels = {}
    for name, prefix in CHANNELS.items():
        channels[name] = {field: str(values.get(prefix + '_' + suffix) or '')
                          for field, suffix in SUFFIXES.items()}
    for field in SUFFIXES:
        channels['embedding'][field] = channels['embedding'][field] or channels['llm'][field]
    return channels


def public_channels(channels):
    return {name: {'base_url': c['base_url'], 'model': c['model'],
                   'key_present': bool(c['api_key'])} for name, c in channels.items()}


def validate_channel(name, data, old):
    if name not in CHANNELS or not isinstance(data, dict):
        raise SettingsError('请选择有效的模型类型。')
    result = {}
    for field in SUFFIXES:
        value = data.get(field, '')
        if not isinstance(value, str) or len(value) > 2048 or any(c in value for c in '\r\n\x00'):
            raise SettingsError('配置字段格式不正确，请使用单行文本。')
        result[field] = value.strip()
    if data.get('clear_key') is True:
        result['api_key'] = ''
    elif not result['api_key']:
        result['api_key'] = old.get('api_key', '')
    result['base_url'] = result['base_url'].rstrip('/')
    if name == 'vlm' and not result['base_url'] and not result['model']:
        return result
    try:
        url = urlsplit(result['base_url'])
        valid = (url.scheme in ('http', 'https') and url.hostname and url.port != 0
                 and not any([url.username, url.password, url.query, url.fragment]))
    except ValueError:
        valid = False
    if not valid:
        raise SettingsError('服务地址须为 HTTP 或 HTTPS 地址，不能包含账号、密钥、查询参数或片段。')
    if not result['model']:
        raise SettingsError('请填写准确的模型名称。')
    return result


def persist(path, channels):
    """Keep unrelated variables and comments; never expose write failures or secrets."""
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.model-settings-', dir=path.parent)
    os.close(fd)
    try:
        os.chmod(temp, 0o600)
        Path(temp).write_text(path.read_text(encoding='utf-8-sig') if path.exists() else '', encoding='utf-8')
        for name, channel in channels.items():
            for field, suffix in SUFFIXES.items():
                set_key(temp, CHANNELS[name] + '_' + suffix, channel[field], quote_mode='always')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def probe(name, channel):
    if not channel['base_url'] or not channel['model']:
        raise SettingsError('请先填写服务地址和模型名称。')
    if name == 'embedding':
        payload = {'model': channel['model'], 'input': ['A short embedding connection test.']}
        endpoint = '/embeddings'
    else:
        content = 'Reply briefly: what is 2 + 3?'
        if name == 'vlm':
            image = Image.new('RGB', (400, 250), 'white')
            draw = ImageDraw.Draw(image)
            draw.polygon([(30, 210), (130, 30), (230, 210)], fill='red')
            draw.text((275, 100), '739', fill='black', font_size=38)
            stream = io.BytesIO()
            image.save(stream, format='PNG')
            content = [
                {'type': 'text', 'text': 'Describe the shape, its color and the number. Be brief.'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                    base64.b64encode(stream.getvalue()).decode('ascii')}}]
        payload = {'model': channel['model'], 'messages': [{'role': 'user', 'content': content}],
                   'max_tokens': 1024, 'temperature': 0, 'stream': False,
                   'chat_template_kwargs': {'enable_thinking': False}}
        endpoint = '/chat/completions'
    headers = {'Content-Type': 'application/json'}
    if channel['api_key']:
        headers['Authorization'] = 'Bearer ' + channel['api_key']
    req = urllib.request.Request(channel['base_url'] + endpoint,
                                 data=json.dumps(payload).encode('utf-8'), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=request_timeout() if name=='llm' else 120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise SettingsError(f'服务返回 HTTP {status}，请检查地址、模型名称和访问权限。') from None
    except (urllib.error.URLError, OSError):
        raise SettingsError('无法连接服务或请求超时，请确认服务已启动并检查网络。') from None
    except (ValueError, UnicodeError):
        raise SettingsError('服务未返回有效 JSON，请检查接口地址。') from None
    try:
        if name == 'embedding':
            vectors = [item['embedding'] for item in result['data']]
            if len(vectors) != 1 or not vectors[0] or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in vectors[0]):
                raise ValueError
            return {'message': f'向量接口可用，返回 {len(vectors[0])} 维向量。', 'dimensions': len(vectors[0])}
        choice = result['choices'][0]
        text = choice['message']['content']
        if not isinstance(text, str) or not text.strip():
            raise ValueError
        if choice.get('finish_reason') == 'length':
            raise SettingsError('已连接，但测试输出达到长度上限；请检查模型的思考模式与输出设置。')
        text = clean_content(text)
        if not text:
            raise SettingsError('服务已连接，但只返回思考内容，未完成测试回答。')
        if name == 'vlm':
            correct = all(word in text.lower() for word in ('red', 'triangle', '739'))
            return {'message': '图片识别测试通过：红色三角形、739。' if correct else
                    '服务已接受图片并返回文本，但未通过预设图片内容核对，请确认模型具备视觉能力。',
                    'vision_verified': correct}
        return {'message': '语言模型接口可用，已成功返回文本。'}
    except SettingsError:
        raise
    except (KeyError, IndexError, TypeError, ValueError):
        raise SettingsError('服务响应不符合预期协议，请确认使用 OpenAI 兼容接口。') from None


def register_model_settings(app, env_path):
    app.config['MODEL_ENV_FILE'] = str(env_path)

    def saved():
        values = dict(os.environ)
        values.update(dotenv_values(app.config['MODEL_ENV_FILE']))
        return read_channels(values)

    @app.before_request
    def protect_model_settings():
        if not request.path.startswith('/api/model-settings'):
            return
        try:
            hostname = urlsplit('http://' + request.host).hostname
        except ValueError:
            hostname = None
        if (hostname not in ('localhost', '127.0.0.1', '::1') or
                request.headers.get('Sec-Fetch-Site') == 'cross-site'):
            return jsonify(error='请从本地工作空间访问模型设置。'), 403

    @app.get('/api/model-settings')
    def get_settings():
        try:
            channels = saved()
            return jsonify(channels=public_channels(channels), restart_required=channels != read_channels(os.environ))
        except OSError:
            return jsonify(error='无法读取本地模型配置文件。'), 500

    @app.post('/api/model-settings')
    def save_settings():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('channels'), dict):
            return jsonify(error='请提交完整的模型配置。'), 400
        try:
            with SAVE_LOCK:
                old = saved()
                channels = {name: validate_channel(name, data['channels'].get(name), old[name]) for name in CHANNELS}
                persist(app.config['MODEL_ENV_FILE'], channels)
            return jsonify(channels=public_channels(channels), restart_required=True,
                           message='已保存。请重启本地服务后生效；正在运行的任务继续使用原配置。')
        except SettingsError as exc:
            return jsonify(error=str(exc)), 400
        except OSError:
            return jsonify(error='保存失败，请检查本地配置文件是否可写。'), 500

    @app.post('/api/model-settings/test')
    def test_settings():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='请提交模型测试配置。'), 400
        try:
            name = data.get('channel')
            if not isinstance(name, str) or name not in CHANNELS:
                raise SettingsError('请选择有效的模型类型。')
            channel = validate_channel(name, data.get('config'), saved()[name])
            return jsonify(ok=True, **probe(name, channel))
        except SettingsError as exc:
            return jsonify(error=str(exc)), 400
        except OSError:
            return jsonify(error='无法读取本地配置。'), 500
