"""Quick setup for the model settings: read a pasted configuration, find model services running on
this computer, and list the models a service offers.

Nothing here saves anything. The results fill the settings form; the teacher still tests and saves.
A key found in pasted text goes back only to the page that sent it, and is never logged.
"""
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
import json
import re
import shlex
import urllib.error
import urllib.request
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit


class ConfigImportError(ValueError):
    """Shown to the teacher as it is."""


# OpenAI-compatible addresses of common providers. The host decides the provider when a pasted
# address has no path; an environment variable prefix (DASHSCOPE_API_KEY …) when there is no address.
PROVIDERS = [
    dict(id='dashscope', name='阿里云百炼（通义千问）', base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
         hosts=('dashscope.aliyuncs.com',), env=('DASHSCOPE', 'ALIYUN', 'BAILIAN')),
    dict(id='deepseek', name='DeepSeek', base_url='https://api.deepseek.com/v1', hosts=('api.deepseek.com',), env=('DEEPSEEK',)),
    dict(id='moonshot', name='月之暗面 Kimi', base_url='https://api.moonshot.cn/v1', hosts=('api.moonshot.cn',),
         env=('MOONSHOT', 'KIMI')),
    dict(id='zhipu', name='智谱 GLM', base_url='https://open.bigmodel.cn/api/paas/v4', hosts=('open.bigmodel.cn',),
         env=('ZHIPUAI', 'ZHIPU', 'BIGMODEL', 'GLM')),
    dict(id='siliconflow', name='硅基流动', base_url='https://api.siliconflow.cn/v1',
         hosts=('api.siliconflow.cn', 'api.siliconflow.com'), env=('SILICONFLOW',)),
    dict(id='volcengine', name='火山方舟（豆包）', base_url='https://ark.cn-beijing.volces.com/api/v3',
         hosts=('ark.cn-beijing.volces.com',), env=('ARK', 'VOLCENGINE', 'DOUBAO')),
    dict(id='hunyuan', name='腾讯混元', base_url='https://api.hunyuan.cloud.tencent.com/v1',
         hosts=('api.hunyuan.cloud.tencent.com',), env=('HUNYUAN',)),
    dict(id='qianfan', name='百度千帆', base_url='https://qianfan.baidubce.com/v2', hosts=('qianfan.baidubce.com',),
         env=('QIANFAN',)),
    dict(id='minimax', name='MiniMax', base_url='https://api.minimaxi.com/v1', hosts=('api.minimaxi.com',), env=('MINIMAX',)),
    dict(id='openrouter', name='OpenRouter', base_url='https://openrouter.ai/api/v1', hosts=('openrouter.ai',),
         env=('OPENROUTER',)),
    dict(id='openai', name='OpenAI', base_url='https://api.openai.com/v1', hosts=('api.openai.com',), env=('OPENAI',)),
    dict(id='gemini', name='Google Gemini', base_url='https://generativelanguage.googleapis.com/v1beta/openai',
         hosts=('generativelanguage.googleapis.com',), env=('GEMINI',)),
]
BY_ID = {p['id']: p for p in PROVIDERS}

# Default ports of local model servers. The name is a guess until the server answers.
LOCAL = [(11434, 'Ollama'), (1234, 'LM Studio'), (8000, 'vLLM'), (8080, 'llama.cpp'), (30000, 'SGLang'),
         (9997, 'Xinference'), (1337, 'Jan'), (5000, 'text-generation-webui'), (5001, 'KoboldCpp'),
         (4000, 'LiteLLM'), (3000, 'One API / New API')]
LOCAL_PORTS = {port for port, _ in LOCAL}
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1', '0.0.0.0', 'host.docker.internal')
# What /v1/models says in owned_by, for servers that share a port with others.
OWNERS = {'vllm': 'vLLM', 'llamacpp': 'llama.cpp', 'sglang': 'SGLang', 'xinference': 'Xinference',
          'library': 'Ollama', 'organization_owner': 'LM Studio', 'jan': 'Jan', 'koboldcpp': 'KoboldCpp',
          'litellm': 'LiteLLM'}
ENDPOINTS = ('/chat/completions', '/completions', '/embeddings', '/models', '/responses', '/messages')
MAX_TEXT = 20000
MAX_BODY = 4 * 1024 * 1024
CHANNEL_NAMES = {'llm': '语言模型', 'vlm': '视觉模型', 'embedding': '嵌入模型'}


# ---------------------------------------------------------------- addresses

def provider_of(url):
    host = (urlsplit(url).hostname or '').lower()
    return next((p for p in PROVIDERS if host in p['hosts'] or any(host.endswith('.' + h) for h in p['hosts'])), None)


def is_local(url):
    return (urlsplit(url).hostname or '').lower() in LOCAL_HOSTS


def normalize_base_url(text):
    """The base address the settings want: scheme and host, the API path, no endpoint, query or account."""
    url = str(text or '').strip().strip('\'"<>()（）[]，,。;；')
    if not url:
        return ''
    if not re.match(r'^[a-z][a-z0-9+.-]*://', url, re.I):
        url = ('http://' if re.match(r'^(localhost|127\.|0\.0\.0\.0|\[::1\]|host\.docker\.internal)', url, re.I)
               else 'https://') + url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ''
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return ''
    if '.' not in parts.hostname and ':' not in parts.hostname and parts.hostname.lower() not in LOCAL_HOSTS:
        return ''                                         # base_url=BASE_URL in a code sample is not an address
    path = re.sub(r'/+', '/', parts.path).rstrip('/')
    for suffix in ENDPOINTS:
        if path.lower().endswith(suffix):
            path = path[:-len(suffix)].rstrip('/')
            break
    host = parts.hostname.lower()
    netloc = (f'[{host}]' if ':' in host else host) + (f':{port}' if port else '')
    provider = provider_of(url)
    if provider and provider['id'] == 'dashscope' and 'compatible-mode' not in path:
        path = urlsplit(provider['base_url']).path      # the native /api/v1 address is not OpenAI-compatible
    elif provider and not path:
        path = urlsplit(provider['base_url']).path
    elif host in LOCAL_HOSTS and port == 11434:
        path = '/v1'                                      # Ollama's own API sits beside /v1
    elif not path:
        path = '/v1'                                      # nearly every compatible server and relay
    return urlunsplit((parts.scheme.lower(), netloc, path, '', ''))


# ---------------------------------------------------------------- reading pasted text

URL_FIELDS = ('baseurl', 'base_url', 'apibase', 'api_base', 'apibaseurl', 'api_base_url', 'endpoint', 'url',
              'host', 'apiurl', 'api_url', 'baseuri', 'openai_base_url', 'openai_api_base', 'server')
KEY_FIELDS = ('apikey', 'api_key', 'key', 'token', 'apitoken', 'access_token', 'accesstoken', 'secret',
              'openai_api_key', 'authorization', 'bearer')
MODEL_FIELDS = ('model', 'modelname', 'model_name', 'modelid', 'model_id', 'defaultmodel', 'default_model', 'chatmodel')
_KEY_TOKEN = re.compile(r'(?<![A-Za-z0-9_-])(sk-[A-Za-z0-9_\-.]{16,}|[0-9a-f]{32}\.[A-Za-z0-9]{16}|'
                        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|AIza[0-9A-Za-z_\-]{30,})(?![A-Za-z0-9_-])')
_URL_TOKEN = re.compile(r'https?://[^\s"\'<>，。；）)\]]+|(?<![\w.@/-])(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?(?:/[^\s"\'<>，。；]*)?',
                        re.I)
_LABELS = {
    'base_url': r'(?:base[ _-]?url|api[ _-]?base(?:[ _-]?url)?|endpoint|接口地址|服务地址|请求地址|API ?地址|代理地址|中转地址|调用地址|Base ?URL)',
    'api_key': r'(?:api[ _-]?key|apikey|令牌|密钥|秘钥|key|token)',
    'model': r'(?:model(?:[ _-]?(?:name|id))?|模型(?:名称|名|ID)?)',
}


def _norm_field(name):
    return re.sub(r'[\s-]', '', str(name)).lower()


def _clean_key(value):
    value = str(value or '').strip().strip('\'"')
    return re.sub(r'^(?:Bearer|Basic)\s+', '', value, flags=re.I).strip()


def _from_dict(data):
    """Pick url, key and model out of one JSON object (any common spelling of the field names)."""
    found = {}
    for name, value in data.items():
        if not isinstance(value, (str, int)):
            continue
        field = _norm_field(name)
        if field in URL_FIELDS and isinstance(value, str) and ('.' in value or 'localhost' in value):
            found.setdefault('base_url', value)
        elif field in KEY_FIELDS and isinstance(value, str) and value.strip():
            found.setdefault('api_key', _clean_key(value))
        elif field in MODEL_FIELDS and isinstance(value, str) and value.strip():
            found.setdefault('model', value.strip())
        elif field in ('models', 'model_list') and isinstance(value, str):
            found.setdefault('model', value.split(',')[0].strip())
    return found


def _walk_json(data, depth=0):
    """The first object, depth first, that carries an address or a key, with its model if it has one."""
    if depth > 8:
        return {}
    if isinstance(data, dict):
        own = _from_dict(data)
        if 'base_url' in own or 'api_key' in own:
            models = data.get('models')
            if 'model' not in own and isinstance(models, list) and models:
                first = models[0]
                name = first if isinstance(first, str) else first.get('id') or first.get('name') \
                    if isinstance(first, dict) else None
                if isinstance(name, str) and name.strip():
                    own['model'] = name.strip()
            return own
        for value in data.values():
            found = _walk_json(value, depth + 1)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _walk_json(value, depth + 1)
            if found:
                return found
    return {}


def _json_in(text):
    for candidate in (text, *re.findall(r'\{.*\}', text, re.S)[:1]):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


def _b64json(value):
    value = unquote(value).strip()
    for decode in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(decode(value + '=' * (-len(value) % 4)).decode('utf-8'))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
    try:
        return json.loads(value)
    except ValueError:
        return None


def _deep_link(text):
    """One-click import links that relay services (new-api, one-api …) and clients hand out."""
    match = re.search(r'cherrystudio://\S+', text, re.I)
    if match:
        query = parse_qs(urlsplit(match.group(0)).query)
        data = _b64json((query.get('data') or [''])[0])
        if isinstance(data, dict):
            return dict(_walk_json(data), source='Cherry Studio 一键导入链接')
        raise ConfigImportError('Cherry Studio 链接里的配置读不出来，请重新复制完整链接。')
    match = re.search(r'ccswitch://\S+', text, re.I)
    if match:
        query = {k: v[0] for k, v in parse_qs(urlsplit(match.group(0)).query).items()}
        found = {k: v for k, v in dict(base_url=query.get('endpoint') or query.get('baseUrl') or query.get('base_url'),
                                       api_key=query.get('apiKey') or query.get('api_key') or query.get('key'),
                                       model=query.get('model')).items() if v}
        note = ['这条链接原本是给 Claude Code 用的，已按 OpenAI 兼容地址导入，请测试连接。'] \
            if (query.get('app') or '').lower() == 'claude' else []
        return dict(found, source='CC Switch 一键导入链接', notes=note)
    for match in re.finditer(r'\S*[?&#]settings=([^\s&#]+)', text):
        data = _json_in(unquote(match.group(1)))
        if isinstance(data, dict):
            vault = (data.get('keyVaults') or {}).get('openai') if isinstance(data.get('keyVaults'), dict) else None
            found = _walk_json(vault) if isinstance(vault, dict) else _walk_json(data)
            if found:
                return dict(found, source='LobeChat 导入链接' if vault else 'NextChat 导入链接')
    return None


def _curl(text):
    start = re.search(r'(?:^|\n)\s*(?:\$\s*)?curl\s', text)
    if not start:
        return None
    command = re.sub(r'\\\r?\n', ' ', text[start.start():]).replace('^\r\n', ' ').replace('^\n', ' ')
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    found, data = {}, None
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ''
        if word in ('-H', '--header'):
            name, _, value = following.partition(':')
            if name.strip().lower() in ('authorization', 'api-key', 'x-api-key'):
                found.setdefault('api_key', _clean_key(value))
        elif word in ('-d', '--data', '--data-raw', '--data-binary', '--json'):
            data = following
        elif re.match(r'https?://', word) and 'base_url' not in found:
            found['base_url'] = word
    payload = _json_in(data or '')
    if isinstance(payload, dict) and isinstance(payload.get('model'), str):
        found['model'] = payload['model']
    return dict(found, source='curl 命令') if found else None


_ENV_LINE = re.compile(r'^\s*(?:export\s+|set\s+|setx\s+|\$env:)?([A-Za-z][A-Za-z0-9_]*)\s*[=\s]\s*(.+?)\s*$')
_ENV_FIELDS = [('base_url', ('BASE_URL', 'API_BASE_URL', 'API_BASE', 'BASE', 'ENDPOINT', 'API_URL', 'URL', 'HOST')),
               ('api_key', ('API_KEY', 'KEY', 'TOKEN', 'API_TOKEN', 'ACCESS_TOKEN')),
               ('model', ('MODEL_NAME', 'MODEL_ID', 'CHAT_MODEL', 'MODEL'))]


def _env(text):
    """KEY=value lines (.env, export, set, PowerShell $env:), grouped by prefix: QWEN_BASE_URL → QWEN."""
    groups = {}
    for line in text.splitlines():
        match = _ENV_LINE.match(line)
        if not match or not match.group(1).isupper():
            continue
        name, value = match.group(1), match.group(2).strip().strip('\'"')
        for field, suffixes in _ENV_FIELDS:
            suffix = next((s for s in suffixes if name == s or name.endswith('_' + s)), None)
            if suffix:
                prefix = name[:-len(suffix)].rstrip('_')
                groups.setdefault(prefix, {}).setdefault(field, value)
                break
    groups = {k: v for k, v in groups.items() if v}
    if not groups:
        return None
    channels = {}
    for prefix, fields in groups.items():
        if 'base_url' in fields and not normalize_base_url(fields['base_url']):
            del fields['base_url']
        if 'base_url' not in fields:
            provider = next((p for p in PROVIDERS if any(prefix == e or prefix.startswith(e + '_') for e in p['env'])), None)
            if provider and 'api_key' in fields:
                fields['base_url'] = provider['base_url']
        if not fields:
            continue
        if 'EMBED' in prefix:
            channel = 'embedding'
        elif 'VLM' in prefix or 'VISION' in prefix or prefix.endswith('_VL'):
            channel = 'vlm'
        else:
            channel = 'llm'
        if channel in channels and len(channels[channel]) >= len(fields):
            continue
        channels[channel] = fields
    if not channels:
        return None
    if 'llm' not in channels:
        return None if len(channels) != 1 else dict(next(iter(channels.values())), source='环境变量')
    llm = channels.pop('llm')
    result = dict(llm, source='环境变量')
    if channels:
        result['channels'] = channels
    return result


def _labelled(text):
    """Lines like 「API 地址：…」「base_url = "…"」「apiKey: '…'」 in a provider's page or a code sample."""
    found = {}
    for field, label in _LABELS.items():
        pattern = re.compile(r'(?<![A-Za-z_])' + label + r'["\']?\s*[:：=]\s*["\']?([^\s"\'，,；;）)]+)', re.I)
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if field == 'base_url' and not normalize_base_url(value):
                continue
            if field == 'api_key':
                value = _clean_key(value)
                if len(value) < 8 or value.lower().startswith(('http', 'os.', 'process.', 'env')):
                    continue
            if field == 'model' and (value.lower().startswith('http') or len(value) > 200):
                continue
            found.setdefault(field, value)
            break
    return dict(found, source='粘贴的文字') if found else None


def parse_config(text, provider=''):
    """{base_url, api_key, model, source, provider, notes, channels?} from pasted text; raises ConfigImportError."""
    if not isinstance(text, str):
        raise ConfigImportError('请粘贴配置文字。')
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise ConfigImportError(f'粘贴的内容太长（超过 {MAX_TEXT} 字），只需要粘贴配置那一段。')
    preset = BY_ID.get(provider or '')
    if not text and not preset:
        raise ConfigImportError('请先粘贴配置，或选择一个服务商。')
    found = (_deep_link(text) or _curl(text) or (lambda data: dict(_walk_json(data), source='JSON 配置')
                                                  if isinstance(data, (dict, list)) and _walk_json(data) else None)(
        _json_in(text) if text[:1] in '{[' else None) or _env(text) or _labelled(text) or {})
    found = dict(found)
    notes = list(found.pop('notes', []))
    if 'base_url' not in found:
        urls = [u.group(0) for u in _URL_TOKEN.finditer(text) if normalize_base_url(u.group(0))]
        api_like = [u for u in urls if provider_of(normalize_base_url(u)) or is_local(normalize_base_url(u))
                    or re.search(r'api|/v\d|compatible', u, re.I)]
        if api_like or urls:
            found['base_url'] = (api_like or urls)[0]
    if 'api_key' not in found:
        key = _KEY_TOKEN.search(text)
        if key:
            found['api_key'] = key.group(1)
    if not found.get('source'):
        found['source'] = '粘贴的文字'
    raw_url = found.get('base_url', '')
    base_url = normalize_base_url(raw_url) if raw_url else ''
    if raw_url and not base_url:
        notes.append('粘贴的地址不是有效的 HTTP 地址，已忽略。')
    if not base_url and preset:
        base_url = preset['base_url']
    if not base_url and not found.get('api_key'):
        raise ConfigImportError('没有找到服务地址或密钥。请粘贴服务商给的地址和密钥，或一键导入链接、curl 命令、环境变量。')
    if not base_url:
        raise ConfigImportError('只找到了密钥，没有服务地址。请在上面选择服务商，或把服务地址一起粘贴进来。')
    known = provider_of(base_url)
    channels = {}
    for name, fields in (found.get('channels') or {}).items():
        url = normalize_base_url(fields.get('base_url', '')) or base_url
        channels[name] = dict(base_url=url, api_key=_clean_key(fields.get('api_key', '')), model=fields.get('model', ''))
    return dict(base_url=base_url, api_key=_clean_key(found.get('api_key', '')), model=found.get('model', '').strip(),
                source=found['source'], provider=known['name'] if known else ('本机服务' if is_local(base_url) else ''),
                local=is_local(base_url), notes=notes, channels=channels)


# ---------------------------------------------------------------- listing and sorting models

_EMBED = re.compile(r'embed|(?<![a-z])(?:bge|gte|e5|m3e|text2vec|mxbai|jina-emb|stella|acge|bce-emb|conan-emb)(?![a-z])', re.I)
_SKIP = re.compile(r'rerank|whisper|(?<![a-z])tts|speech|(?<![a-z])asr|paraformer|sensevoice|cosyvoice|audio|'
                   r'flux|stable-diffusion|sdxl|dall-e|wanx|(?<![a-z])t2[iv](?![a-z])|(?<![a-z])i2v|image-gen|'
                   r'qwen-image|seedream|seedance|moderation|realtime', re.I)
_VISION = re.compile(r'(?<![a-z])vl(?![a-z])|vision|llava|minicpm-?v|pixtral|internvl|moondream|qvq|glm-?4(?:\.\d+)?v|'
                     r'cogvlm|gemma-?3(?![-:]?(?:1b|270m))|gpt-4o|gpt-4\.1|gpt-5|gemini|mllama|omni|kimi-vl', re.I)
_SIZE = re.compile(r'([\d.]+)\s*([bm])', re.I)


def kind_of(model_id, hint=None):
    """'embedding', 'vision', 'chat' or 'other', from what the server said (hint) or else the name."""
    if hint in ('embedding', 'vision', 'chat', 'other'):
        return hint
    name = str(model_id)
    if _SKIP.search(name):
        return 'other'
    if _EMBED.search(name):
        return 'embedding'
    if _VISION.search(name):
        return 'vision'
    return 'chat'


def _size(model):
    match = _SIZE.search(str(model.get('size') or ''))
    if not match:
        match = re.search(r'[:\-_](\d+(?:\.\d+)?)([bm])(?![a-z])', model['id'], re.I)
    if not match:
        return 0.0
    value = float(match.group(1))
    return value / 1000 if match.group(2).lower() == 'm' else value


def _natural(text):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', text.lower())]


def suggest(models, current=None):
    """The model each channel would use: the one already set if the service has it, else the largest fit."""
    current = current or {}
    ids = {m['id'] for m in models}
    def pick(kinds, prefer=()):
        fits = [m for m in models if m['kind'] in kinds]
        for name in prefer:
            matches = [m for m in fits if name in m['id'].lower()]
            if matches:
                return max(matches, key=lambda m: _natural(m['id']))['id']
        # A cloud service lists dozens of models; guessing one of them is worse than asking.
        if not fits or (len(fits) > 5 and not any(_size(m) for m in fits)):
            return ''
        return max(fits, key=_size)['id']
    picks = {'llm': pick(('chat',)) or pick(('vision',)), 'vlm': pick(('vision',)),
             'embedding': pick(('embedding',), prefer=('bge-m3', 'text-embedding-v', 'multilingual', 'zh'))}
    for channel, model in current.items():
        if model and model in ids:
            kind = next(m['kind'] for m in models if m['id'] == model)
            if (channel, kind) in (('llm', 'chat'), ('llm', 'vision'), ('vlm', 'vision'), ('embedding', 'embedding')):
                picks[channel] = model
    return picks


def _opener(local):
    # A proxy from the environment must not catch requests to this computer.
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()


def fetch_json(url, api_key='', timeout=15.0, method='GET', payload=None):
    """(status, data); status 0 when nothing answered. Bodies over 4 MB or not JSON give data None."""
    headers = {'Accept': 'application/json', 'User-Agent': 'zhiye-model-settings'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    body = None
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _opener(is_local(url)).open(request, timeout=timeout) as response:
            raw = response.read(MAX_BODY + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None
    if len(raw) > MAX_BODY:
        return status, None
    try:
        return status, json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return status, None


def _model_ids(data):
    if isinstance(data, dict) and isinstance(data.get('data'), list):
        items = data['data']
    elif isinstance(data, dict) and isinstance(data.get('models'), list):
        items = data['models']
    else:
        return None
    result = []
    for item in items[:2000]:
        model_id = item.get('id') or item.get('name') or item.get('model') if isinstance(item, dict) else item
        if isinstance(model_id, str) and model_id.strip() and len(model_id) <= 300:
            result.append(dict(id=model_id.strip(), owner=item.get('owned_by') if isinstance(item, dict) else None,
                               hint=_hint(item) if isinstance(item, dict) else None))
    return result


def _hint(item):
    kind = str(item.get('type') or item.get('model_type') or '').lower()
    if kind in ('embeddings', 'embedding', 'text-embedding'):
        return 'embedding'
    if kind in ('vlm',):
        return 'vision'
    if kind in ('llm',):
        return 'chat'
    abilities = [str(a).lower() for a in item.get('capabilities') or [] if isinstance(a, str)]
    if 'embedding' in abilities:
        return 'embedding'
    if 'vision' in abilities:
        return 'vision'
    return None


def _ollama_details(root, models, timeout):
    """Ollama tells what each model can do (/api/show capabilities) and how large it is (/api/tags)."""
    status, tags = fetch_json(root + '/api/tags', timeout=timeout)
    if status != 200 or not isinstance(tags, dict):
        return False
    sizes = {t.get('name'): (t.get('details') or {}).get('parameter_size') for t in tags.get('models') or []
             if isinstance(t, dict)}
    def show(model):
        code, info = fetch_json(root + '/api/show', timeout=timeout, method='POST', payload={'model': model['id']})
        return _hint(info) if code == 200 and isinstance(info, dict) else None
    with ThreadPoolExecutor(max_workers=8) as pool:
        hints = list(pool.map(show, models[:40]))
    for model, hint in zip(models, hints + [None] * len(models)):
        model['hint'] = hint or model.get('hint')
        model['size'] = sizes.get(model['id']) or sizes.get(model['id'] + ':latest')
    return True


def describe(models):
    return [dict(id=m['id'], kind=kind_of(m['id'], m.get('hint')), **({'size': m['size']} if m.get('size') else {}))
            for m in models]


def list_models(base_url, api_key='', current=None, timeout=15.0):
    """The models behind base_url: {base_url (corrected if /v1 was missing or extra), models, suggested}."""
    base_url = normalize_base_url(base_url)
    if not base_url:
        raise ConfigImportError('请先填写有效的服务地址。')
    parts = urlsplit(base_url)
    candidates = [base_url, base_url[:-3] if parts.path.endswith('/v1') else base_url + '/v1']
    statuses = []
    for base in candidates:
        status, data = fetch_json(base + '/models', api_key, timeout)
        statuses.append(status)
        models = _model_ids(data) if status == 200 else None
        if models is not None:
            if is_local(base):
                root = urlunsplit((parts.scheme, parts.netloc, '', '', ''))
                _ollama_details(root, models, min(timeout, 5.0))
            described = describe(models)
            return dict(base_url=base, models=described, suggested=suggest(described, current))
        if status in (401, 403):
            break
    if 401 in statuses or 403 in statuses:
        raise ConfigImportError('服务拒绝了请求（HTTP 401/403）：密钥不对，或这个密钥没有读取模型列表的权限。')
    if all(s == 0 for s in statuses):
        raise ConfigImportError('无法连接这个地址。请确认服务已启动、地址和端口正确，并检查网络。')
    raise ConfigImportError('这个服务没有提供模型列表。请按服务商文档填写模型名称，再点「测试」。')


def detect_local(current=None, timeout=1.5):
    """Model servers answering on this computer's usual ports, with their models; never raises."""
    def look(entry):
        port, guess = entry
        root = f'http://127.0.0.1:{port}'
        status, data = fetch_json(root + '/v1/models', timeout=timeout)
        if status in (401, 403):
            name = guess if port in (11434, 1234) else 'OpenAI 兼容服务'
            return dict(name=name, port=port, base_url=root + '/v1', needs_key=True, models=[], suggested={})
        models = _model_ids(data) if status == 200 else None
        if models is None:
            return None
        name = guess
        if _ollama_details(root, models, timeout * 2):
            name = 'Ollama'
        else:
            code, extra = fetch_json(root + '/api/v0/models', timeout=timeout)     # LM Studio's richer list
            extra = _model_ids(extra) if code == 200 else None
            if extra:
                hints = {m['id']: m['hint'] for m in extra}
                for model in models:
                    model['hint'] = hints.get(model['id']) or model.get('hint')
                name = 'LM Studio'
            else:
                owners = {OWNERS.get(str(m.get('owner') or '').lower()) for m in models} - {None}
                name = owners.pop() if len(owners) == 1 else guess if port in (11434, 1234) else 'OpenAI 兼容服务'
        described = describe(models)
        return dict(name=name, port=port, base_url=root + '/v1', needs_key=False, models=described,
                    suggested=suggest(described, current))
    with ThreadPoolExecutor(max_workers=len(LOCAL)) as pool:
        return [found for found in pool.map(look, LOCAL) if found]


def public_providers():
    return [dict(id=p['id'], name=p['name'], base_url=p['base_url']) for p in PROVIDERS]
