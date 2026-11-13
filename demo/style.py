"""Countable signs of padded or machine-sounding lesson text. Reminders for a teacher, not a verdict.

Every measure is a plain text match. It cannot tell whether a sentence is true, useful or well
taught, and a quoted wrong answer can trip a pattern. quality.py shows the counts as reminders
for lessons written from a lesson design; scale/quality_baseline.py prints them so that outputs
of different pipeline versions can be compared on the same terms.
"""
import re

VERSION = 'style-hints-v1'

# (code, label, pattern). Subject words that merely look similar are left alone on purpose:
# 先验概率 and 证据 are probability vocabulary, so only the record-keeping phrases are matched.
PATTERNS = [
    ('system_talk', '系统与诊断话术',
     r'诊断(?:数据|记录|信息|证据|状态|显示|结果)|未知状态|先验(?:证据|诊断)|掌握证据|学情证据'
     r'|收集[^。；，\n]{0,10}证据|画像(?:记录|字段|快照)|数据库'),
    ('disclaimer', '免责声明',
     r'暂无(?:足够|已确认)?|依据有限|仅(?:作为|供)[^。\n]{0,16}(?:依据|参考)|不(?:能)?直接(?:判定|宣称|判断)'
     r'|不预设|并非严格|不代表(?:全班|真实|已)'),
    ('narration', '第三人称旁白',
     r'(?:教师|老师)(?:首先|先|随后|接着|然后|再|带领|给出|讲解|列举|逐步|在黑板|用红笔|提醒|引导|可根据)'),
    ('filler', '套话',
     r'需要(?:特别)?(?:说明|注意|强调|指出)的是|值得(?:注意|一提)的是|综上所述|总而言之|总的来说|特别说明'
     r'|不难(?:看出|发现)|众所周知|始终以[^。\n]{0,20}为(?:首要)?原则|灵活(?:调整|压缩|安排)'
     r'|具有重要(?:意义|作用)|旨在|从而更好地|为后续[^。\n]{0,10}(?:打下|奠定)'),
    ('ordinal', '排比式分点',
     r'(?:^|[。；：\n])[ \t]*(?:第[一二三四五六七八九十][，,、]|首先[，,]|其次[，,]|再次[，,]|最后[，,])'),
    ('chinese_numeral', '汉字写的数量',
     r'[一二两三四五六七八九十百]+分钟|[二三四五六七八九十百][一二三四五六七八九十百]+(?:名|道|个|人|次|项|页|起)'),
]
_COMPILED = [(code, label, re.compile(pattern, re.M)) for code, label, pattern in PATTERNS]
_FENCE = re.compile(r'^[ \t]*```.*?^[ \t]*```[ \t]*$', re.M | re.S)


def prose(text):
    """Lesson text without fenced blocks (figure data, quoted material)."""
    return _FENCE.sub('', text or '')


def hits(text):
    """{code: [matched phrases]} for every pattern group; empty groups are included."""
    body = prose(text)
    return {code: [match[0].strip() for match in pattern.finditer(body)] for code, _, pattern in _COMPILED}


def _stream(text):
    """One comparable string: no whitespace, formula delimiters or list markup."""
    body = re.sub(r'^[ \t]*(?:>|[-*+]|#{1,6}|\d+[.、])[ \t]+', '', prose(text), flags=re.M)
    return re.sub(r'[\s$*`]+|\\[,;! ]', '', body)


def repeated(text, minimum=3, shortest=8, longest=40):
    """Longest phrases that occur at least `minimum` times, as [(phrase, count)].

    A defining formula restated in the goals, the explanation and the summary shows up here
    as well; the list is for a reader to judge, not an error count.
    """
    stream = _stream(text)
    found, covered = [], [False] * len(stream)
    for size in range(min(longest, len(stream)), shortest - 1, -1):
        seen = {}
        for start in range(len(stream) - size + 1):
            seen.setdefault(stream[start:start + size], []).append(start)
        for phrase, starts in seen.items():
            if len(starts) < minimum:
                continue
            # Count non-overlapping occurrences that a longer repeated phrase has not claimed.
            free, last = [], -size
            for start in starts:
                if start >= last + size and not any(covered[start:start + size]):
                    free.append(start)
                    last = start
            if len(free) >= minimum and re.search(r'[一-鿿]', phrase):
                found.append((phrase, len(free)))
                for start in free:
                    covered[start:start + size] = [True] * size
    return sorted(found, key=lambda item: (-item[1] * len(item[0]), item[0]))


def summary(text):
    """Counts per pattern group, repeated phrases and the share of the text they take up."""
    stream = _stream(text)
    groups = hits(text)
    phrases = repeated(text)
    return dict(version=VERSION, characters=len(stream), chinese=len(re.findall(r'[一-鿿]', stream)),
                counts={code: len(found) for code, found in groups.items()}, examples=groups,
                repeated=phrases[:8],
                repeated_share=round(sum(len(p) * n for p, n in phrases) / len(stream), 3) if stream else 0.0)


def reminder(text, threshold=3):
    """Short teacher-facing sentence, or '' when the counted signs stay under the threshold."""
    groups = hits(text)
    labels = {code: label for code, label, _ in PATTERNS}
    parts = [f'{labels[code]} {len(found)} 处（如「{found[0]}」）' for code, found in groups.items() if found]
    return '；'.join(parts) if sum(len(found) for found in groups.values()) >= threshold else ''
