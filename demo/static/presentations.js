// Model content is always displayed as text, never HTML.
(() => {
const dialog = $('ppt-dialog');
const openButton = el('button', '生成授课 PPT', 'secondary');
openButton.id = 'open-ppt'; openButton.type = 'button'; openButton.disabled = true;
$('print-lecture').insertAdjacentElement('afterend', openButton);
const labels = {queued:'排队中', running:'生成中', succeeded:'课件已生成', failed:'失败', interrupted:'已中断', cancelled:'已取消'};
const kinds = {cover:'封面', concept:'概念讲解', example:'例题', question:'课堂练习', summary:'总结'};
let source = null, working = false, session = 0;
let templates = [];
function showTemplate() {
  const item = templates.find(t => t.id === $('ppt-template').value);
  const preview = $('ppt-template-preview');
  preview.hidden = !item;
  if(item) {
    preview.src = item.preview_url;
    preview.alt = item.name + '原版模板预览';
    $('ppt-template-hint').textContent = `原始比例 ${(item.width/item.height).toFixed(2)}:1。下方课件区域为内容预览，下载文件应用此模板。`;
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
function drawDeck(data) {
  $('ppt-source').textContent = sourceLabel(data.source);
  $('ppt-preview').replaceChildren(...data.deck.slides.map((slide, index) => {
    const item = el('section', undefined, 'ppt-page');
    const canvas = el('div', undefined, 'ppt-canvas' + (index === 0 ? ' ppt-cover' : ''));
    canvas.append(el('h3', slide.title));
    const body = el('div', undefined, 'ppt-body');
    slide.bullets.forEach(text => body.append(el('p', text)));
    canvas.append(body);
    if(slide.question) canvas.append(el('p', '课堂提问：' + slide.question, 'ppt-question'));
    canvas.append(el('p', `${index+1} / ${data.deck.slides.length} · ${kinds[slide.kind]} · AI 改编稿，待教师核查`, 'ppt-footer'));
    const notes = el('details', undefined, 'ppt-notes');
    notes.append(el('summary', '教师讲稿与来源'), el('p', slide.notes), el('p', '材料编号：' + (slide.sources.join('、') || '本页未标注，请核查'), 'helper'));
    item.append(canvas, notes); return item;
  }));
  $('ppt-download').hidden = false;
  $('ppt-download').href = `/api/presentations/${data.id}/download`;
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
      working = false; $('ppt-submit').disabled = !source;
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
dialog.addEventListener('close', () => { session++; working = false; });
window.addEventListener('lecture-version-changed', syncButton);
window.watchPresentationJob = watchPresentationJob;
syncButton();
})();
