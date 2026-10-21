"""Embedding clients. The default channel is the research-group endpoint used for generation."""
import hashlib
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class EmbeddingError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize(vector):
    total = sum(value * value for value in vector) ** 0.5
    return [value / total for value in vector] if total else list(vector)


class QwenEmbedder:
    """OpenAI-compatible embedding endpoint, defaulting to the generation channel.

    Credentials and raw service errors stay out of every saved trace, exactly as in qwen.py.
    Whether this endpoint offers /embeddings is verified by the first real call, never assumed.
    """

    def __init__(self, model=None, base=None, key=None, timeout=120):
        self.model = (model or os.environ.get('QWEN_EMBEDDING_MODEL')
                      or os.environ.get('QWEN_MODEL', '')).strip()
        self.base = (base if base is not None else
                     os.environ.get('QWEN_EMBEDDING_BASE_URL') or os.environ.get('QWEN_BASE_URL', '')).rstrip('/')
        self._key = (key if key is not None else
                     os.environ.get('QWEN_EMBEDDING_API_KEY') or os.environ.get('QWEN_API_KEY', ''))
        self.timeout = timeout

    def configured(self):
        """Same standard as config_flags: presence only, never a live request."""
        try:
            parsed = urlsplit(self.base)
            valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                     and not any([parsed.username, parsed.password, parsed.query, parsed.fragment]))
        except ValueError:
            valid = False
        return bool(self._key) and valid and bool(self.model)

    def embed(self, texts):
        texts = list(texts)
        if not texts:
            return []
        if not self._key:
            raise EmbeddingError('missing_key', '请在本地 .env 中配置 QWEN_API_KEY。')
        try:
            parsed = urlsplit(self.base)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                    parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError
            url = self.base + '/embeddings'
        except ValueError:
            raise EmbeddingError('invalid_endpoint', '请检查本地 QWEN_BASE_URL 的服务地址格式。') from None
        payload = json.dumps({'model': self.model, 'input': texts}, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(url, data=payload, headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise EmbeddingError('http_error', f'嵌入服务返回 HTTP {code}，请检查配置或服务是否支持嵌入接口。') from None
        except urllib.error.URLError as exc:
            timeout_hit = isinstance(exc.reason, TimeoutError)
            raise EmbeddingError('timeout' if timeout_hit else 'unreachable',
                                 '嵌入请求超时，请减少资料后重试。' if timeout_hit else
                                 '无法连接嵌入服务，请确认课题组内网连接后重试。') from None
        except TimeoutError:
            raise EmbeddingError('timeout', '嵌入请求超时，请减少资料后重试。') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise EmbeddingError('invalid_json', '嵌入服务没有返回有效 JSON。') from None
        except OSError:
            raise EmbeddingError('connection_error', '嵌入连接中断，请稍后重试。') from None
        return self._vectors(result, len(texts))

    def _vectors(self, result, expected):
        data = result.get('data')
        # Two spellings exist in the wild: OpenAI's {"data":[{"embedding":[...]}]}
        # and Ollama's {"embeddings":[[...]]}. Accept either, count must still match.
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            vectors = [item.get('embedding') for item in data]
        else:
            vectors = result.get('embeddings')
        if not isinstance(vectors, list) or len(vectors) != expected:
            raise EmbeddingError('invalid_protocol', '嵌入服务返回的向量数量与输入不匹配。')
        try:
            return [[float(value) for value in vector] for vector in vectors]
        except (TypeError, ValueError):
            raise EmbeddingError('invalid_protocol', '嵌入服务返回的向量格式不正确。') from None


class OllamaEmbedder:
    """Local bge-m3 through Ollama /api/embed."""

    def __init__(self, model=None, base=None, timeout=120):
        self.model = model or os.environ.get('EMBEDDING_MODEL', 'bge-m3')
        self.base = (base or os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')).rstrip('/')
        self.timeout = timeout

    def embed(self, texts):
        texts = list(texts)
        if not texts:
            return []
        payload = json.dumps({'model': self.model, 'input': texts}, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.base + '/api/embed', data=payload,
                                        headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise EmbeddingError('http_error', f'本地 embedding 服务返回 HTTP {code}。') from None
        except urllib.error.URLError as exc:
            timeout_hit = isinstance(exc.reason, TimeoutError)
            raise EmbeddingError('timeout' if timeout_hit else 'unreachable',
                                 '本地 embedding 请求超时，请减少资料后重试。' if timeout_hit else
                                 '无法连接本机 Ollama，请确认本地服务已启动。') from None
        except TimeoutError:
            raise EmbeddingError('timeout', '本地 embedding 请求超时，请减少资料后重试。') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise EmbeddingError('invalid_json', '本地 embedding 服务没有返回有效 JSON。') from None
        except OSError:
            raise EmbeddingError('connection_error', '本地 embedding 连接中断。') from None
        vectors = result.get('embeddings')
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError('invalid_protocol', '本地 embedding 服务返回的向量数量不匹配。')
        try:
            return [[float(value) for value in vector] for vector in vectors]
        except (TypeError, ValueError):
            raise EmbeddingError('invalid_protocol', '本地 embedding 服务返回的向量格式不正确。') from None


class HashingEmbedder:
    """Deterministic offline vectors so tests need no Ollama.

    Shared character n-grams raise the score; these vectors carry no semantics and
    must never be used to argue that retrieval found the right passage.
    """

    def __init__(self, dimensions=1024):
        self.dimensions = dimensions
        self.model = f'hash-{dimensions}'

    def embed(self, texts):
        return [self._vector(text) for text in texts]

    def _vector(self, text):
        vector = [0.0] * self.dimensions
        for gram in self._grams(text):
            digest = hashlib.blake2b(gram.encode('utf-8'), digest_size=4).digest()
            index = int.from_bytes(digest[:2], 'big') % self.dimensions
            vector[index] += 1.0 if digest[2] & 1 else -1.0
        return normalize(vector)

    @staticmethod
    def _grams(text):
        compact = ''.join(char.lower() for char in text if char.isalnum())
        if len(compact) < 2:
            return {compact} if compact else {''}
        return {compact[start:start + 2] for start in range(len(compact) - 1)}
