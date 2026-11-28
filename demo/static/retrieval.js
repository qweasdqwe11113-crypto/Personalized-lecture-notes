// 课程资料依据: the passages retrieval picked for the next lecture, and the teacher's changes to them.
// Only a changed selection is sent (retrieval_chunk_ids); otherwise the server repeats the automatic pick.
// Each kept chunk travels as key@hash (the start of its content hash), so text edited since is refused.
// The anchored mode gives the lesson's own material whole (read-only here) and lets the teacher change
// only the passages retrieved from the rest of the course, or the excerpts of a lesson too long to give whole.
let retrievalState = null;
const RETRIEVAL_ROLES = [['explain', '讲解依据'], ['practice', '例题练习'], ['learner', '针对学情'], ['teacher', '教师保留']];
const CHUNK_KINDS = {definition: '定义', theorem: '定理', example: '例题', exercise: '练习', text: '正文'};
const EDITABLE_MODES = ['hybrid', 'anchored'];

function retrievalHow(r) {
  return r.vector?.used ? `关键词与语义（${r.embed_model}）两种排序融合` : '只按关键词排序（' + (r.vector?.note || '没有可用的向量') + '）';
}

function anchoredOwn(r) {
  const base = r.base || {}, n = (base.materials || []).length;
  return base.kind === 'chunks' ? `本课 ${n} 份资料共 ${base.characters} 字，超过 ${base.budget} 字的上限，按本课目标选取节选`
    : `本课 ${n} 份资料全文提供（${base.characters ?? 0} 字）`;
}

function anchoredMore(r) {
  if (!r.queries?.some(q => q.role === 'learner')) return '没有学情可检索，不做整门课补充';
  const later = r.boundary?.later_points ? `，不含后面章节的 ${r.boundary.later_points} 个知识点` : '';
  return `按学情从整门课补充 ${(r.items || []).filter(item => !item.anchor).length} 段（${retrievalHow(r)}${later}）`;
}

function retrievalModeNote(ctx, snapshot) {
  const r = ctx?.retrieval || {};
  if (r.mode === 'anchored')
    return (snapshot ? '本讲义生成时：' : '本次：') + anchoredOwn(r) + '；' + anchoredMore(r) + `。在「课程资料依据」里查看${snapshot ? '' : '和调整'}。`;
  if (r.mode === 'hybrid')
    return (snapshot ? '本讲义生成时' : '本次') + `从整门课 ${r.scope?.materials ?? 0} 份资料中检索，${retrievalHow(r)}。在「课程资料依据」里查看${snapshot ? '' : '和调整'}取到的片段。`;
  if (r.mode === 'vector') return snapshot ? '本讲义按目标检索材料片段；再次生成会检查并更新索引。' : '按目标从所选知识点的资料中选取片段。';
  if (r.mode === 'none') return '本讲义生成时没有提供课程资料（对照条件）；再次生成请选择材料取用方式。';
  return snapshot ? '本讲义直接读取了所选知识点的全部关联材料。' : '精确 SQL 直接读取所选知识点的全部关联材料，无需索引。';
}

function retrievalSelection() {
  const s = retrievalState;
  if (!s || s.snapshot || !EDITABLE_MODES.includes(s.mode)) return null;
  const same = s.checked.size === s.automatic.size && [...s.checked].every(key => s.automatic.has(key));
  return same ? null : s.order.filter(key => s.checked.has(key)).map(key => s.ids[key]);
}

function anchoredProblem(s) {
  // What the server would refuse: nothing left, or more than the budget (whole material plus checked passages).
  const own = [...s.checked].filter(key => s.anchors.has(key)).length;
  if (s.cut && !s.checked.size) return '至少保留一段资料，否则无法生成。';
  if (s.cut && !own) return '没有勾选本课资料的节选，讲义将只依据整门课的补充片段。';
  const size = (s.cut ? 0 : s.baseCharacters) + [...s.checked].reduce((sum, key) => sum + (s.sizes[key] || 0), 0);
  return size > s.budget ? `所选资料约 ${size} 字，超过 ${s.budget} 字的上限，生成时会被拒绝，请少选几段。` : '';
}

function retrievalSummary() {
  const s = retrievalState, summary = $('sources-summary');
  if (!summary || !s) return;
  const prefix = s.snapshot ? '生成时 · ' : '';
  if (EDITABLE_MODES.includes(s.mode)) {
    const changed = retrievalSelection() !== null;
    $('sources-reset').hidden = !changed;
    let warning = '';
    if (s.mode === 'anchored') {
      const own = [...s.checked].filter(key => s.anchors.has(key)).length, extra = s.checked.size - own;
      summary.textContent = `${prefix}${s.cut ? `本课资料节选 ${own} 段` : `本课资料全文 ${s.base} 份`} + 整门课补充 ${extra} 段${changed ? ' · 已调整' : ''}`;
      warning = s.snapshot ? '' : anchoredProblem(s);
    } else {
      summary.textContent = `${prefix}整门课检索 · 已选 ${s.checked.size} 段${changed ? ' · 已调整' : ''}`;
      warning = !s.snapshot && s.checked.size === 0 ? '至少保留一段资料，否则无法生成。' : '';
    }
    $('sources-note').textContent = warning || s.note;
    $('sources-note').classList.toggle('check-warning', Boolean(warning));
  } else if (s.mode === 'none') summary.textContent = prefix + '未提供课程资料（对照）';
  else summary.textContent = `${prefix}${s.mode === 'vector' ? '所选知识点内的片段' : '所选知识点的全部材料'} · ${s.count} 份`;
}

function retrievalMeta(item) {
  const parts = [(item.kind || 'text').split(' ').map(kind => CHUNK_KINDS[kind] || kind).join('、')];
  if (item.heading) parts.push(item.heading);
  if (item.also?.length) parts.push('也用作' + item.also.map(role => Object.fromEntries(RETRIEVAL_ROLES)[role] || role).join('、'));
  if (item.coverage) parts.push('所选知识点的保底片段');
  if (item.rank_bm25) parts.push('关键词第 ' + item.rank_bm25);
  if (item.rank_vector) parts.push('语义第 ' + item.rank_vector);
  if (item.score != null) parts.push('融合分 ' + Number(item.score).toFixed(4));
  parts.push(item.selected_point ? '本课知识点' : '本课以外的知识点');
  return parts.join(' · ');
}

function chunkRow(item, editable) {
  const s = retrievalState, row = el('div', undefined, 'retrieval-pick');
  const head = el('label', undefined, 'retrieval-pick-head'), box = el('input');
  box.type = 'checkbox'; box.value = item.key; box.checked = s.checked.has(item.key); box.disabled = !editable || busy;
  box.onchange = () => { if (box.checked) s.checked.add(item.key); else s.checked.delete(item.key); retrievalSummary(); };
  const title = el('span', undefined, 'retrieval-pick-title');
  title.append(el('strong', 'M' + item.material_id), document.createTextNode(' ' + item.title));
  head.append(box, title);
  const text = el('details', undefined, 'retrieval-pick-text');
  text.append(el('summary', `查看片段 · ${item.char_length} 字 · ${item.source_title}`), el('p', item.content));
  row.append(head, el('p', retrievalMeta(item), 'retrieval-pick-meta'), text);
  return row;
}

function retrievalGroup(title, rows, hint) {
  const section = el('section', undefined, 'retrieval-group');
  section.append(el('h3', title));
  if (hint) section.append(el('p', hint, 'helper'));
  section.append(...rows);
  return section;
}

function retrievalQueries(r) {
  const queries = el('details', undefined, 'retrieval-queries');
  queries.append(el('summary', '本次检索用的查询'), ...(r.queries || []).map(q => el('p', `${q.label}：${q.text}`)));
  return queries;
}

function drawAnchored(ctx, r, snapshot, alternatives) {
  const base = r.base || {}, items = r.items || [], cut = base.kind === 'chunks', scope = r.scope || {};
  const learner = r.queries?.some(q => q.role === 'learner');
  $('sources-intro').textContent = snapshot ? '生成这份讲义时用的资料（只读）。再次生成会按当前设置重新读取和检索。' :
    (cut ? `本课 ${(base.materials || []).length} 份资料共 ${base.characters} 字，超过 ${base.budget} 字的上限：按本课目标和学情从中选取节选，可以勾选调整。`
      : `本课知识点的 ${(base.materials || []).length} 份资料全文提供给模型，不需要勾选。`) +
    (learner ? `另按学情从整门课 ${scope.materials ?? 0} 份资料中检索补充片段` +
      (r.boundary?.later_points ? `（不含后面章节的 ${r.boundary.later_points} 个知识点）` : '') + '；取消勾选的片段不提供给模型。'
      : '当前没有学情可检索，不做整门课补充。');
  const groups = [];
  if (!cut) {
    const byId = Object.fromEntries((ctx.materials || []).map(m => [m.id, m])), marked = new Set(r.learner_in_lesson || []);
    groups.push(retrievalGroup('本课知识点的全部资料', (base.materials || []).map(m => {
      const d = el('details', undefined, 'retrieval-pick-text');
      d.append(el('summary', `M${m.id} ${m.title} · ${m.characters} 字${marked.has(m.id) ? ' · 与学情有关' : ''}`), el('p', byId[m.id]?.content || ''));
      return d;
    }), '原文完整提供；「与学情有关」表示针对学情的检索命中了这份资料。'));
  }
  const own = items.filter(item => item.anchor), extra = items.filter(item => !item.anchor);
  if (own.length) groups.push(retrievalGroup('本课知识点（节选）', own.map(item => chunkRow(item, !snapshot)),
    '与学情有关的片段和每个知识点最相关的一段在前，其余按本课目标排序，取到上限为止。'));
  if (extra.length) groups.push(retrievalGroup('针对学情 · 整门课补充', extra.map(item => chunkRow(item, !snapshot)),
    '可能属于本课以外的知识点，例如前面学过的内容。'));
  if (alternatives.length) groups.push(retrievalGroup('备选片段', alternatives.map(item => chunkRow(item, true)),
    '排在已选片段之后的片段，默认不提供给模型。'));
  if (r.removed?.length) groups.push(el('p', '教师在生成前移除了：' + r.removed.join('、'), 'helper'));
  if (r.queries?.length) groups.push(retrievalQueries(r));
  return groups;
}

function drawRetrieval(ctx, snapshot = false) {
  const box = $('retrieval-picks');
  if (!box) return;
  const r = ctx.retrieval || {}, mode = r.mode || 'sql', items = r.items || [];
  const alternatives = snapshot ? [] : (r.alternatives || []), all = [...items, ...alternatives];
  retrievalState = {snapshot, mode, count: (ctx.materials || []).length,
    // The panel's intro already says what the anchored mode gave; its note only says how passages were ranked.
    note: mode === 'hybrid' || (mode === 'anchored' && r.ranked) ? '排序方式：' + retrievalHow(r) + '。'
      : mode === 'anchored' ? '' : retrievalModeNote(ctx, snapshot),
    automatic: new Set(items.map(item => item.key)), checked: new Set(items.map(item => item.key)),
    order: all.map(item => item.key),
    ids: Object.fromEntries(all.map(item => [item.key, `${item.key}@${(item.content_sha256 || '').slice(0, 16)}`])),
    // The anchored mode's budget check on the page: the whole material plus the checked passages.
    cut: r.base?.kind === 'chunks', base: (r.base?.materials || []).length, baseCharacters: r.base?.characters || 0,
    budget: r.base?.budget || Infinity, anchors: new Set(all.filter(item => item.anchor).map(item => item.key)),
    sizes: Object.fromEntries(all.map(item => [item.key, item.char_length || 0]))};
  $('sources-reset').hidden = true;
  $('sources-note').textContent = retrievalState.note;
  if (mode === 'anchored') {
    box.replaceChildren(...drawAnchored(ctx, r, snapshot, alternatives));
    retrievalSummary();
    return;
  }
  if (mode !== 'hybrid') {
    $('sources-intro').textContent = mode === 'none' ? '生成时没有提供课程资料。' :
      mode === 'vector' ? '按目标从所选知识点的资料中取出的片段，原样提供给模型。' : '所选知识点的全部关联材料，原样提供给模型。';
    box.replaceChildren(...(ctx.materials || []).map(m => {
      const d = el('details', undefined, 'retrieval-pick-text');
      d.append(el('summary', `M${m.id} ${m.title} · ${m.content.length} 字`), el('p', m.content));
      return d;
    }));
    retrievalSummary();
    return;
  }
  const scope = r.scope || {};
  $('sources-intro').textContent = snapshot ? '生成这份讲义时取到的资料片段（只读）。再次生成会按当前设置重新检索。' :
    `从这门课 ${scope.materials ?? 0} 份资料的 ${scope.chunks ?? 0} 个片段中，按本课目标、所选知识点` +
    (r.queries?.some(q => q.role === 'learner') ? '和学情' : '') + '检索。取消勾选的片段不提供给模型，也可以勾选备选片段；更换学生、目标或备课后会重新检索。';
  const groups = RETRIEVAL_ROLES.map(([role, label]) => [label, items.filter(item => item.role === role)])
    .filter(([, rows]) => rows.length)
    .map(([label, rows]) => retrievalGroup(label, rows.map(item => chunkRow(item, !snapshot))));
  if (alternatives.length) groups.push(retrievalGroup('备选片段', alternatives.map(item => chunkRow(item, true)),
    '各查询排在已选片段之后的片段，默认不提供给模型。'));
  if (r.removed?.length) groups.push(el('p', '教师在生成前移除了：' + r.removed.join('、'), 'helper'));
  box.replaceChildren(...groups, retrievalQueries(r));
  retrievalSummary();
}

function resetRetrievalSelection() {
  const s = retrievalState;
  if (!s || s.snapshot) return;
  s.checked = new Set(s.automatic);
  for (const box of document.querySelectorAll('#retrieval-picks input')) box.checked = s.checked.has(box.value);
  retrievalSummary();
}

function setRetrievalBusy(value) {
  for (const box of document.querySelectorAll('#retrieval-picks input')) box.disabled = value || Boolean(retrievalState?.snapshot);
}

$('sources-reset')?.addEventListener('click', resetRetrievalSelection);
// app.js may have drawn a context before this script ran.
if (typeof current !== 'undefined' && current) drawRetrieval(current, false);
