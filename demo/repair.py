"""One automatic second attempt when a structured reply fails the program's own checks.

The follow-up request is the frozen request plus the rejected reply and the concrete
problem, so it is fully determined by saved data. It is tried once; a second failure
is reported as a failure, never patched into something that looks valid.
"""
import hashlib
import json

from prompts import PreparedRequest

POLICY = 'retry-once-v1'
REPLY_LIMIT = 24000


def retry_request(prepared, rejected, problem):
    payload = json.loads(prepared.body)
    payload['messages'] = payload['messages'] + [
        {'role': 'assistant', 'content': (rejected or '')[:REPLY_LIMIT]},
        {'role': 'user', 'content': f'上一次输出没有通过程序检查：{problem} 请重新输出完整结果，严格使用要求的格式，'
                                    '不要解释、道歉或添加格式之外的文字。'}]
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return PreparedRequest(body, hashlib.sha256(body.encode('utf-8')).hexdigest(),
                           prepared.prompt_version + '+repair-v1', prepared.input_version)


def merged_usage(first, second, problem, retry):
    usage = dict(first or {})
    for key, value in (second or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(usage.get(key, 0), (int, float)):
            usage[key] = usage.get(key, 0) + value
    usage['repair'] = {'policy': POLICY, 'reason': problem, 'request_sha256': retry.sha256}
    return usage
