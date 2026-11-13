// Model content is always displayed as text, never HTML.
(() => {
const dialog = $('ppt-dialog');
const openButton = el('button', '生成授课 PPT', 'secondary');
openButton.id = 'open-ppt'; openButton.type = 'button'; openButton.disabled = true;
$('lecture-prep-status').before(openButton);
const labels = {queued:'排队中', running:'生成中', succeeded:'课件已生成', failed:'失败', interrupted:'已中断', cancelled:'已取消'};
const kinds = {cover:'封面', concept:'概念讲解', example:'例题', question:'课堂练习', summary:'总结'};
let source = null, working = false, session = 0;
let templates = [];
let currentDeck = null, editWorking = false, editToken = 0, pendingEdit = null;
const editStatus = text => { $('ppt-edit-status').textContent = text; };
function editBusy(value) {
  editWorking = value;
  for(const id of ['ppt-edit-page','ppt-edit-instruction','ppt-edit-generate']) $(id).disabled = value;
  $('ppt-submit').disabled = value || working || !source;
  document.querySelectorAll('.ppt-page-tools button').forEach(button => { button.disabled = value; });
}
function clearCandidate() {
  pendingEdit = null;
  $('ppt-edit-comparison').hidden = true; $('ppt-edit-decisions').hidden = true;
  $('ppt-edit-before').replaceChildren(); $('ppt-edit-after').replaceChildren();
}
function selectEditPage(page) {
  $('ppt-edit-page').value = String(page);
  document.querySelectorAll('#ppt-preview .ppt-page').forEach((node, index) => node.classList.toggle('is-edit-target', index+1 === page));
}
function showTemplate() {
  const item = templates.find(t => t.id === $('ppt-template').value);
  const preview = $('ppt-template-preview');
  preview.hidden = !item;
  if(item) {
    preview.src = item.preview_url;
    preview.alt = item.name + '样例预览';
    $('ppt-template-hint').textContent = item.semantic
      ? `${item.description}。按内容安排概念、推导、对比、表格和练习。最终排版请查看下载文件。`
      : `传统母版，原始比例 ${(item.width/item.height).toFixed(2)}:1。网页为内容预览，下载文件应用此母版。`;
  }
}
async function loadTemplates(selected) {
  if(!templates.length) {
    const data = await api('/api/presentation-templates');
    templates = data.templates;
    $('ppt-template').replaceChildren(...templates.map(item => {
      const option = el('option', item.name); option.value = item.id; return option;
    }));
  }
  if(selected && templates.some(t => t.id === selected)) $('ppt-template').value = selected;
  showTemplate();
}
$('ppt-template').onchange = showTemplate;
const version = number => number ? 'R' + number : '原稿';
function message(text) { $('ppt-status').textContent = text; }
function syncButton() { openButton.disabled = !lastResult || busy; }
function resetPreview() {
  $('ppt-preview').replaceChildren(); $('ppt-download').hidden = true; $('ppt-trace').hidden = true;
  editToken++; currentDeck = null; clearCandidate(); editBusy(false); $('ppt-edit-panel').hidden = true;
  $('ppt-edit-instruction').value = '';
}
function sourceLabel(item) {
  return `依据讲义 #${item.lecture_id} / ${version(item.base_revision)}；源版本保存时状态：${item.source_status}；模板：${item.template_name || '旧版简洁版式'}。`;
}
async function refreshList(lectureId, token) {
  const data = await api(`/api/lectures/${lectureId}/presentations`);
  if(token !== session) return;
  $('ppt-history').replaceChildren(...data.presentations.map(item => {
    const b = el('button', `#${item.run_id} · ${version(item.source.base_revision)} · ${item.source.slide_count} 页 · ${labels[item.status]}`, 'secondary');
    b.type = 'button'; b.onclick = () => watchPresentationJob(item.id); return b;
  }));
  if(!data.presentations.length) $('ppt-history').append(el('p', '这份讲义尚无课件记录。', 'helper'));
}
function drawVisual(visual) {
  const root = el('div', undefined, 'ppt-visual ppt-visual-' + visual.type);
  const paragraph = value => el('p', value);
  const list = items => { const node = el('div'); items.forEach(value => node.append(paragraph(value))); return node; };
  const section = (title, items) => { const node = el('div', undefined, 'ppt-visual-section'); node.append(el('h4', title), list(items)); return node; };
  switch(visual.type) {
    case 'cover': root.append(paragraph(visual.subtitle), list(visual.goals)); break;
    case 'definition':
      root.append(el('h4', visual.term), paragraph(visual.definition));
      if(visual.formula) root.append(el('p', visual.formula, 'ppt-formula'));
      root.append(list(visual.conditions)); break;
    case 'steps':
      root.append(el('p', visual.problem, 'ppt-problem'));
      visual.steps.forEach((step, i) => { const row = el('div', undefined, 'ppt-step'); row.append(el('strong', String(i+1)), paragraph(step.expression), el('span', step.reason)); root.append(row); });
      if(visual.check) root.append(paragraph('检验：' + visual.check)); break;
    case 'comparison': root.append(section(visual.left.title, visual.left.items), section(visual.right.title, visual.right.items)); break;
    case 'table': {
      const table = el('table'), head = el('tr'); visual.headers.forEach(value => head.append(el('th', value))); table.append(head);
      visual.rows.forEach(values => { const row = el('tr'); values.forEach(value => row.append(el('td', value))); table.append(row); }); root.append(table); break;
    }
    case 'process': visual.nodes.forEach((node, i) => root.append(section(`${i+1}. ${node.title}`, [node.detail]))); break;
    case 'metrics': visual.items.forEach(item => root.append(section(item.value + ' ' + item.unit, [item.label, item.context]))); break;
    case 'practice': root.append(el('p', visual.problem, 'ppt-problem'), list(visual.prompts), el('div', '作答区域', 'ppt-answer-space')); break;
    case 'recap': visual.items.forEach(item => root.append(section(item.title, [item.detail]))); break;
    default: root.append(el('p', visual.lead, 'ppt-lead'), list(visual.items));
  }
  return root;
}
function renderSlide(slide, index, total) {
    const item = el('section', undefined, 'ppt-page');
    const tools = el('div', undefined, 'ppt-page-tools');
    const choose = el('button', '修改这一页', 'secondary'); choose.type = 'button'; choose.disabled = editWorking;
    choose.onclick = () => {
      if(editWorking) return;
      selectEditPage(index+1);
      $('ppt-edit-instruction').focus();
    };
    tools.append(el('span', `第 ${index+1} 页`), choose);
    const canvas = el('div', undefined, 'ppt-canvas' + (slide.visual ? ' ppt-semantic' : index === 0 ? ' ppt-cover' : ''));
    applyPreviewTheme(canvas);
    canvas.append(el('h3', slide.title));
    const body = el('div', undefined, 'ppt-body');
    if(slide.visual) body.append(drawVisual(slide.visual));
    else slide.bullets.forEach(text => body.append(el('p', text)));
    canvas.append(body);
    if(slide.question && slide.question !== slide.visual?.problem) canvas.append(el('p', '课堂提问：' + slide.question, 'ppt-question'));
    canvas.append(el('p', `${index+1} / ${total} · ${kinds[slide.kind]} · AI 改编稿，待教师核查`, 'ppt-footer'));
    const notes = el('details', undefined, 'ppt-notes');
    notes.append(el('summary', '教师讲稿与来源'), el('p', slide.notes), el('p', '材料编号：' + (slide.sources.join('、') || '本页未标注，请核查'), 'helper'));
    item.append(tools, canvas, notes);
    return item;
}
function drawDeck(data, changedPage = null) {
  currentDeck = data; editToken++; clearCandidate(); editBusy(false);
  $('ppt-source').textContent = sourceLabel(data.source);
  if(changedPage && $('ppt-preview').children.length === data.deck.slides.length) {
    $('ppt-preview').children[changedPage-1].replaceWith(renderSlide(data.deck.slides[changedPage-1], changedPage-1, data.deck.slides.length));
  } else $('ppt-preview').replaceChildren(...data.deck.slides.map((slide, index) => renderSlide(slide, index, data.deck.slides.length)));
  $('ppt-edit-panel').hidden = false;
  $('ppt-edit-version').textContent = `当前课件 #${data.id}` + (data.edit ? ` · 由 #${data.edit.parent_run_id} 微调第 ${data.edit.page} 页` : '');
  $('ppt-edit-page').replaceChildren(...data.deck.slides.map((slide, index) => { const option = el('option', `第 ${index+1} 页 · ${slide.title}`); option.value = String(index+1); return option; }));
  selectEditPage(changedPage || 1); editStatus('选择一页，填写要求后生成修改稿。');
  $('ppt-download').hidden = false;
  $('ppt-download').href = `/api/presentations/${data.id}/download`;
  $('ppt-trace').hidden = false; $('ppt-trace').onclick = () => openTrace(data.id);
  message(`${data.deck.title} · ${data.deck.slides.length} 页。${data.warning}`);
}
async function watchPresentationJob(id) {
  const token = ++session;
  working = true; $('ppt-submit').disabled = true; resetPreview();
  if(!dialog.open) dialog.showModal();
  message('正在读取课件任务…');
  try {
    let job = await api('/api/jobs/' + id);
    if(!job.ppt_generation) throw new Error('该任务不是 PPT 课件。');
    const run = await api('/api/generation-runs/' + job.run_id);
    if(token !== session) return;
    const saved = run.context.ppt_export;
    await loadTemplates(saved.template_id);
    if(token !== session) return;
    source = {id:saved.lecture_id, revision_number:saved.base_revision};
    $('ppt-source').textContent = sourceLabel(saved);
    $('ppt-count').value = saved.slide_count; $('ppt-instruction').value = saved.instruction;
    $('ppt-trace').hidden = false; $('ppt-trace').onclick = () => openTrace(job.run_id);
    if(run.context.ppt_slide_edit && run.response_metadata?.edit_state !== 'accepted') {
      const parent = await api('/api/presentations/' + run.context.ppt_slide_edit.parent_run_id);
      if(token !== session) return;
      drawDeck(parent);
      selectEditPage(run.context.ppt_slide_edit.page);
      $('ppt-edit-instruction').value = run.context.ppt_slide_edit.instruction;
      await watchEditJob(id);
      return;
    }
    while(token === session) {
      message(`${labels[job.status]} · 输入记录 #${job.run_id}` + (job.status === 'queued' ? ` · 等待位置 ${job.queue_position}` : '') + '。关闭窗口后可从后台任务恢复。');
      if(job.confirmation_pending) throw new Error('任务结束状态尚未确认，请核查生成记录并重启本地服务。');
      if(job.status === 'succeeded') {
        const data = await api('/api/presentations/' + job.run_id);
        if(token === session) drawDeck(data);
        break;
      }
      if(['failed', 'interrupted', 'cancelled'].includes(job.status)) throw new Error(job.error_message || '课件未完成，可查看输入记录并按原输入重试。');
      await new Promise(resolve => setTimeout(resolve, 1000));
      if(token !== session) return;
      job = await api('/api/jobs/' + id);
    }
  } catch(e) { if(token === session) message(e.message + ' 已提交任务保存在“生成记录 → 后台任务”。'); }
  finally {
    if(token === session) {
      working = false; $('ppt-submit').disabled = !source || editWorking;
      if(source) refreshList(source.id, token).catch(e => message(e.message));
      refreshWorkspaceJobs().catch(() => {}); refreshRuns().catch(() => {});
    }
  }
}
openButton.onclick = async () => {
  if(!lastResult || busy) return;
  const token = ++session;
  source = {id:lastResult.id, revision_number:lastResult.revision_number || 0};
  working = false; $('ppt-submit').disabled = false; resetPreview();
  $('ppt-source').textContent = `依据当前阅读的讲义 #${source.id} / ${version(source.revision_number)}。`;
  message('先设置页数，再生成课件。网页显示内容预览；实际字体与分页请在 PowerPoint 中复核。');
  $('ppt-history').replaceChildren(); dialog.showModal();
  try { await loadTemplates(); if(token === session) await refreshList(source.id, token); } catch(e) { if(token === session) message(e.message); }
};
$('ppt-form').onsubmit = async e => {
  e.preventDefault(); if(working || !source) return;
  working = true; $('ppt-submit').disabled = true; resetPreview();
  const token = ++session, frozen = {...source};
  message('正在固定讲义版本并提交课件任务…');
  try {
    const job = await api(`/api/lectures/${frozen.id}/presentations`, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({base_revision:frozen.revision_number, slide_count:Number($('ppt-count').value), instruction:$('ppt-instruction').value, template_id:$('ppt-template').value})});
    if(token === session) await watchPresentationJob(job.id);
    else refreshWorkspaceJobs().catch(() => {});
  } catch(e) { if(token === session) { message(e.message); working = false; $('ppt-submit').disabled = false; } }
};
$('close-ppt').onclick = () => dialog.close();
dialog.addEventListener('close', () => { session++; editToken++; working = false; editWorking = false; });

function comparisonSlide(slide) {
  const node = el('div', undefined, 'ppt-comparison-slide');
  applyPreviewTheme(node);
  node.append(el('h5', slide.title));
  if(slide.visual) node.append(drawVisual(slide.visual));
  else slide.bullets.forEach(text => node.append(el('p', text)));
  if(slide.question && slide.question !== slide.visual?.problem) node.append(el('p', '课堂提问：' + slide.question));
  const notes = el('details'); notes.append(el('summary', '教师讲稿与来源'), el('p', slide.notes), el('p', slide.sources.join('、'))); node.append(notes);
  return node;
}
function applyPreviewTheme(node) {
  // Use the saved deck, so changing the new-task selector cannot recolor history.
  const saved = currentDeck?.source;
  if(saved?.template_id) node.dataset.template = saved.template_id;
}
async function watchEditJob(id) {
  const token = ++editToken, baseId = currentDeck?.id;
  editBusy(true); clearCandidate();
  try {
    while(token === editToken && dialog.open) {
      const job = await api('/api/jobs/' + id);
      if(token !== editToken || currentDeck?.id !== baseId) return;
      $('ppt-trace').hidden = false; $('ppt-trace').onclick = () => openTrace(job.run_id);
      if(job.confirmation_pending) throw new Error('任务状态待确认，请从生成记录恢复。');
      if(job.status === 'succeeded') {
        const candidate = await api('/api/presentation-edits/' + job.run_id);
        if(token !== editToken || currentDeck?.id !== baseId) return;
        if(candidate.state !== 'pending') { editStatus(candidate.state === 'discarded' ? '这次修改已放弃，原课件保持不变。' : '这次修改已采用，可从课件记录打开新版本。'); return; }
        if(candidate.parent_run_id !== baseId) throw new Error('修改稿与当前课件不一致，请重新打开。');
        pendingEdit = candidate;
        $('ppt-edit-before').replaceChildren(comparisonSlide(candidate.before));
        $('ppt-edit-after').replaceChildren(comparisonSlide(candidate.after));
        $('ppt-edit-comparison').hidden = false; $('ppt-edit-decisions').hidden = false;
        editStatus(`第 ${candidate.page} 页修改稿已就绪。采用后保存为新课件，原版保留。`);
        return;
      }
      if(['failed','interrupted','cancelled'].includes(job.status)) throw new Error(job.error_message || '本页修改未完成，原课件保持不变。');
      editStatus(`${labels[job.status]} · 仅处理选中页面。关闭后可从“生成记录 → 后台任务”恢复。`);
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  } catch(e) { if(token === editToken) editStatus(e.message); }
  finally { if(token === editToken) { editBusy(false); refreshWorkspaceJobs().catch(() => {}); refreshRuns().catch(() => {}); } }
}
$('ppt-edit-page').onchange = () => selectEditPage(Number($('ppt-edit-page').value));
$('ppt-edit-form').onsubmit = async event => {
  event.preventDefault(); if(!currentDeck || editWorking) return;
  const base = currentDeck, token = ++editToken;
  editBusy(true); clearCandidate(); editStatus('正在提交本页修改…');
  try {
    const job = await api(`/api/presentations/${base.id}/slide-edits`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({page:Number($('ppt-edit-page').value), instruction:$('ppt-edit-instruction').value, base_sha256:base.deck_sha256})});
    if(token === editToken && currentDeck?.id === base.id) await watchEditJob(job.id);
  } catch(e) { if(token === editToken) { editStatus(e.message); editBusy(false); } }
};
async function decideEdit(action) {
  if(!pendingEdit || editWorking) return;
  const candidate = pendingEdit, token = ++editToken;
  editBusy(true); $('ppt-edit-accept').disabled = true; $('ppt-edit-discard').disabled = true;
  try {
    await api(`/api/presentation-edits/${candidate.id}/${action}`, {method:'POST'});
    if(token !== editToken) return;
    if(action === 'accept') {
      const data = await api('/api/presentations/' + candidate.id);
      if(token !== editToken) return;
      drawDeck(data, candidate.page); $('ppt-edit-instruction').value = '';
      editStatus(`已保存为课件 #${data.id}，仅第 ${candidate.page} 页更新，原版 #${candidate.parent_run_id} 保留。`);
      refreshList(source.id, session).catch(() => {});
    } else { clearCandidate(); $('ppt-trace').onclick = () => openTrace(currentDeck.id); editStatus('已放弃这次修改，原课件保持不变。'); }
  } catch(e) { if(token === editToken) editStatus(e.message); }
  finally { $('ppt-edit-accept').disabled = false; $('ppt-edit-discard').disabled = false; if(token === editToken || currentDeck?.id === candidate.id) editBusy(false); }
}
$('ppt-edit-accept').onclick = () => decideEdit('accept');
$('ppt-edit-discard').onclick = () => decideEdit('discard');
window.addEventListener('lecture-version-changed', syncButton);
window.watchPresentationJob = watchPresentationJob;
syncButton();
})();
