"""Small, deterministic lecture plans and explicit character budgets, not an evaluator."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import re

from profiles import STRATEGIES

MATERIAL_BUDGET = 16000
CONTEXT_BUDGET = 56000


class LectureInputError(ValueError):
    pass


def plan_for(context, overrides=None):
    if overrides is not None and (not isinstance(overrides, dict) or len(overrides) > 20):
        raise LectureInputError('讲解安排格式无效，请重新读取当前课程。')
    states = context.get('states', []) if context.get('personalization_mode') != 'none' else []
    allowed = {str(s['id']) for s in states}
    if overrides and set(overrides) - allowed:
        raise LectureInputError('讲解安排含不属于本次画像的知识点。无画像模式不发送个体安排。')
    result = []
    for state in states:
        action = (overrides or {}).get(str(state['id']), STRATEGIES[state['state']])
        if not isinstance(action, str) or not action.strip() or len(action.strip()) > 400:
            raise LectureInputError('每个知识点的讲解安排请填写 1—400 字。')
        evidence = state.get('recent_evidence', [])
        warnings = []
        dated = [e.get('observed_at') for e in evidence if e.get('observed_at')]
        if state['state'] == 'unknown':
            warnings.append('未诊断，不能当作薄弱。')
        elif not dated:
            warnings.append('依据未提供观察时间，请核对是否仍适用。')
        elif all((datetime.now(timezone.utc) - datetime.fromisoformat(d)).days > 90 for d in dated):
            warnings.append('所列证据均超过90天，请核对是否仍适用。')
        if any(e.get('decision') == 'retain' for e in evidence):
            warnings.append('存在保留原判断的证据，请核对差异；不自动更改掌握状态。')
        result.append(dict(knowledge_id=state['id'], name=state['name'], state=state['state'],
            evidence=state.get('evidence', ''), evidence_ids=[e['id'] for e in evidence],
            action=action.strip(), teacher_modified=action.strip()!=STRATEGIES[state['state']],
            target_section='核心讲解、例题与检验', warnings=warnings))
    return result


def _excerpts(material, query, quota):
    content = material['content']
    if len(content) <= quota:
        return material
    tokens = set(re.findall(r'[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}', query))
    # Long Chinese phrases also contribute short terms, without guessing semantics.
    tokens |= {s[i:i+2] for s in list(tokens) for i in range(0, len(s)-1, 2)}
    width = min(900, quota)
    windows = [(start, min(start + width, len(content))) for start in range(0, len(content), max(1,width-100))]
    windows.sort(key=lambda r: (-sum(content[r[0]:r[1]].count(t) for t in tokens), r[0]))
    chosen, used = [], 0
    for start, end in windows:
        if any(start < b and end > a for a,b in chosen):
            continue
        separator = 3 if chosen else 0
        end = min(end, start + quota - used - separator)
        if end > start:
            chosen.append((start,end)); used += end-start+separator
        if quota-used < 100:
            break
    chosen.sort()
    return {**material, 'content':'\n…\n'.join(content[a:b] for a,b in chosen),
        'selection':dict(method='goal-excerpts-v1', original_characters=len(content), ranges=chosen,
                         content_sha256=hashlib.sha256(content.encode()).hexdigest())}


def budget_context(context, goal):
    """Budget the actual saved model context; no alteration of stored source documents."""
    ctx = copy.deepcopy(context)
    notes = list(ctx.get('input_budget', {}).get('notes', []))
    materials = ctx['materials']
    previous = ctx.get('input_budget', {})
    original_material_ids = previous.get('original_material_ids', [m['id'] for m in materials])
    original_characters = previous.get('original_material_characters', sum(len(m['content']) for m in materials))
    if sum(len(m['content']) for m in materials) > MATERIAL_BUDGET:
        # Round-robin knowledge points before relevance ranking to preserve coverage.
        groups = {}
        for material in materials:
            groups.setdefault(material['knowledge_id'], []).append(material)
        selected = []
        while groups and len(selected) < 24:
            for kid in list(groups):
                selected.append(groups[kid].pop(0))
                if not groups[kid]: del groups[kid]
                if len(selected) == 24: break
        quota = max(200, (MATERIAL_BUDGET - len(selected)*6)//max(1,len(selected)))
        query = goal + ' ' + ' '.join(s['name'] for s in ctx.get('states',[]) if s['state']=='needs_support')
        ctx['materials'] = [_excerpts(m, query, quota) for m in selected]
        notes.append('材料超过字符预算，按知识点轮流保留并按目标选取原文片段；范围和原文指纹随输入保存。')
    plan = ctx['topic'].get('lesson_plan', '')
    if len(plan) > 8000:
        raise LectureInputError('教案正文超过本次生成的8000字符预算，请精简教案或拆分教学目标；原教案未修改。')
    for state in ctx.get('states', []):
        for evidence in state.get('recent_evidence', []):
            for key, limit in [('performance',600),('task_content',300),('reason',200),('previous_evidence',300)]:
                value = evidence.get(key, '')
                if isinstance(value,str) and len(value)>limit:
                    evidence[key] = value[:limit] + '…（输入节选，完整证据保留在学生档案）'
                    notes.append('较长证据仅发送节选，完整记录仍在学生档案。')
    ctx['input_budget'] = dict(
        version='lecture-budget-v1',
        material_characters=sum(len(m['content']) for m in ctx['materials']),
        original_material_ids=original_material_ids,
        original_material_characters=original_characters,
        omitted_material_ids=[i for i in original_material_ids if i not in {m['id'] for m in ctx['materials']}],
        limit=CONTEXT_BUDGET, notes=list(dict.fromkeys(notes)), context_characters=0,
        note='这是字符预算，不是精确token计数；仍受实际模型上下文限制。')
    for _ in range(3):
        size = len(json.dumps(dict(goal=goal, context=ctx), ensure_ascii=False, default=str))
        ctx['input_budget']['context_characters'] = size
    if size > CONTEXT_BUDGET:
        raise LectureInputError(f'本次输入约{size}字符，超过{CONTEXT_BUDGET}字符预算，请减少材料、教案或图片解析后重试。')
    return ctx


def finalize_context(context, goal, overrides=None):
    if context.get('lecture_workflow') != 'teacher-v2':
        return context
    context = {**context, 'personalization_plan':plan_for(context, overrides)}
    return budget_context(context, goal)
