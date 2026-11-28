// 一键备课: the teacher writes what the lesson is about; retrieval ranks the course library's knowledge points
// for it (POST /api/quick-lesson/suggest); the teacher confirms, drops or adds points; the lesson is saved like
// one made on the course page (POST /api/quick-lesson) and opened in the workbench, where the default material
// mode gives those points' material whole and adds passages for the learner from the rest of the course.
const quick = {library: [], suggestion: null, chosen: new Set(), shown: [], scope: null, busy: false};
const QUICK_MAX = 20;

function quickHow(s) {
  return s.vector?.used ? `关键词与语义（${s.embed_model}）两种排序融合` : '只按关键词排序（' + (s.vector?.note || '没有可用的向量') + '）';
}

function quickSummary() {
  const n = quick.chosen.size;
  $('quick-summary').textContent = !quick.suggestion ? '先写下重点并查找知识点' :
    !n ? '至少选 1 个知识点' : `已选 ${n} 个知识点` + (n > QUICK_MAX ? `，最多 ${QUICK_MAX} 个` : '');
  $('quick-create').disabled = quick.busy || !quick.suggestion || !n || n > QUICK_MAX;
  $('quick-suggest').disabled = quick.busy || !quick.library.length;
}

function drawQuickAdd() {
  const shown = new Set(quick.shown), groups = new Map();
  for (const point of quick.library) {
    if (shown.has(point.id)) continue;
    const label = point.chapter || '未分章';
    if (!groups.has(label)) { const group = el('optgroup'); group.label = label; groups.set(label, group); }
    const option = el('option', point.name + (point.materials ? '' : '（暂无资料）'));
    option.value = String(point.id);
    groups.get(label).append(option);
  }
  $('quick-add-point').replaceChildren(...groups.values());
  $('quick-add-button').disabled = quick.busy || !groups.size;
  $('quick-add').hidden = !quick.suggestion;
}

function quickPointRow(id, found, byId) {
  const point = found[id], base = byId[id] || {}, row = el('div', undefined, 'quick-point');
  const head = el('label', undefined, 'quick-point-head'), box = el('input');
  box.type = 'checkbox'; box.value = String(id); box.checked = quick.chosen.has(id); box.disabled = quick.busy;
  box.onchange = () => { if (box.checked) quick.chosen.add(id); else quick.chosen.delete(id); quickSummary(); };
  const name = el('span', undefined, 'quick-point-name');
  name.append(el('strong', base.name || point?.name || `#${id}`));
  if (base.chapter) name.append(el('span', ' · ' + base.chapter, 'quick-point-chapter'));
  head.append(box, name);
  row.append(head);
  if (!point) { row.append(el('p', '教师添加', 'quick-point-meta')); return row; }
  const meta = [`相关度 ${Math.round(point.share * 100)}%`];
  if (point.suggested) meta.push('建议勾选');
  if (point.later) meta.push(`在「${quick.suggestion.chapter}」之后的章节，没有勾选`);
  row.append(el('p', meta.join(' · '), 'quick-point-meta'));
  if (point.evidence?.length) {
    const why = el('details', undefined, 'quick-point-evidence');
    why.append(el('summary', '为什么找到它'),
      ...point.evidence.map(e => el('p', `M${e.material_id}${e.heading ? ' · ' + e.heading : ''}：${e.excerpt}…`)));
    row.append(why);
  }
  return row;
}

function drawQuickPoints() {
  const s = quick.suggestion, byId = Object.fromEntries(quick.library.map(point => [point.id, point]));
  const found = Object.fromEntries((s?.points || []).map(point => [point.knowledge_id, point]));
  $('quick-warnings').replaceChildren(...(s?.warnings || []).map(text => el('p', text, 'check-warning')));
  $('quick-points').replaceChildren(...quick.shown.map(id => quickPointRow(id, found, byId)));
  drawQuickAdd();
  quickSummary();
}

function quickReset() {
  quick.suggestion = null; quick.chosen = new Set(); quick.shown = [];
  drawQuickPoints();
}

async function openQuickStart() {
  const scope = window.workspaceScope, courseId = scope?.courseId() ?? null, classId = scope?.classId() ?? null;
  $('quick-course').textContent = scope?.course()?.name || '当前课程';
  if (quick.scope?.course_id !== courseId || quick.scope?.class_id !== classId) quickReset();
  quick.scope = null; quick.library = [];
  quickSummary();
  if (courseId == null) { $('quick-status').textContent = '请先在页面顶部选择课程。'; return; }
  $('quick-status').textContent = '正在读取知识点库…';
  try {
    const query = new URLSearchParams({course_id: courseId});
    if (classId != null) query.set('class_id', classId);
    const d = await api('/api/quick-lesson/library?' + query);
    const chapter = $('quick-chapter').value, none = el('option', '不限定');
    none.value = '';
    $('quick-chapter').replaceChildren(none, ...d.chapters.map(name => { const o = el('option', name); o.value = name; return o; }));
    $('quick-chapter').value = d.chapters.includes(chapter) ? chapter : '';
    quick.library = d.points; quick.scope = {course_id: courseId, class_id: classId};
    $('quick-status').textContent = d.points.length ? `这门课的知识点库共 ${d.points.length} 个知识点。` :
      '这门课的知识点库还是空的：请先在「课程资料」上传资料并确认知识点。';
  } catch (e) { $('quick-status').textContent = '知识点库读取失败：' + e.message; }
  drawQuickPoints();
}

async function suggestQuickPoints(event) {
  event?.preventDefault();
  if (!quick.scope || quick.busy) return;
  const text = $('quick-focus').value.trim();
  if (text.length < 4) { $('quick-status').textContent = '请用 4—400 个字写出本课要讲的重点。'; return; }
  quick.busy = true; quickSummary();
  $('quick-status').textContent = '正在整门课的资料里查找…首次查找要为资料片段建立索引，可能需要一些时间。';
  try {
    const d = await api('/api/quick-lesson/suggest', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...quick.scope, text, chapter: $('quick-chapter').value || null})});
    const s = d.suggestion;
    quick.library = d.points; quick.suggestion = s;
    quick.chosen = new Set(s.suggested); quick.shown = s.points.map(point => point.knowledge_id);
    $('quick-status').textContent = `在 ${s.scope.materials} 份资料的 ${s.scope.chunks} 个片段中查找，${quickHow(s)}；` +
      (s.suggested.length ? `勾选了 ${s.suggested.length} 个最相关的知识点，请核对后创建。` : '没有可勾选的知识点，请从下方添加。');
  } catch (e) { $('quick-status').textContent = '查找失败：' + e.message; }
  finally { quick.busy = false; drawQuickPoints(); }
}

async function createQuickLesson() {
  if (!quick.scope || !quick.suggestion || !quick.chosen.size || quick.busy) return;
  quick.busy = true; drawQuickPoints();
  $('quick-status').textContent = '正在创建备课…';
  try {
    const d = await api('/api/quick-lesson', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...quick.scope, text: $('quick-focus').value.trim(), title: $('quick-title').value.trim(),
        chapter: $('quick-chapter').value || null, suggested_ids: quick.suggestion.suggested,
        knowledge_ids: quick.library.filter(point => quick.chosen.has(point.id)).map(point => point.id)})});
    $('quick-start-dialog').close();
    $('quick-focus').value = ''; $('quick-title').value = ''; quickReset();
    $('quick-status').textContent = '';
    await refreshWorkspaceCatalog(d.key);
    // The lesson's points give all their material; the rest of the course adds passages for the learner.
    $('retrieval-mode').value = 'anchored';
    if (typeof openWorkspacePage === 'function') openWorkspacePage('workbench');
    $('topic').dispatchEvent(new Event('change'));
  } catch (e) { $('quick-status').textContent = '创建失败：' + e.message; }
  finally { quick.busy = false; drawQuickPoints(); }
}

for (const button of document.querySelectorAll('[data-setting-dialog="quick-start-dialog"]')) button.addEventListener('click', openQuickStart);
$('quick-start-form').addEventListener('submit', suggestQuickPoints);
$('quick-create').onclick = createQuickLesson;
$('quick-add-button').onclick = () => {
  const id = Number($('quick-add-point').value);
  if (!id || quick.shown.includes(id)) return;
  quick.shown.push(id); quick.chosen.add(id);
  drawQuickPoints();
};
// The suggestion was found for the chapter chosen then; the teacher's own changes to the list are kept.
$('quick-chapter').addEventListener('change', () => {
  if (quick.suggestion) $('quick-status').textContent = '章节已改：点「查找知识点」按新的章节重新查找，或保留当前勾选直接创建。';
});
