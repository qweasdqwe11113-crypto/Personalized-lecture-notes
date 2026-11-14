/* Lesson design review. The design is a finished run, not a lecture: the teacher reads it, changes
   times, focus or problems, or asks for another attempt; confirming starts the writing job. */
(() => {
let state = null, working = false;
const dialog = $('design-dialog');
const lines = value => value.split('\n').map(line => line.trim()).filter(Boolean);
function message(text, problems) {
  $('design-error').replaceChildren(document.createTextNode(text || ''), ...((problems || []).length ? [el('ul')] : []));
  for (const problem of problems || []) $('design-error').lastChild.append(el('li', problem));
  $('design-error').hidden = !text;
}
function lock(value) {
  working = value;
  for (const node of dialog.querySelectorAll('button, input, textarea')) node.disabled = value;
}
function total() {
  const sum = [...$('design-minutes').querySelectorAll('input')].reduce((n, input) => n + (Number(input.value) || 0), 0);
  const off = state.duration && sum !== state.duration;
  $('design-total').textContent = `合计 ${sum} 分钟` + (state.duration ? `，授课时长 ${state.duration} 分钟` + (off ? '，两者不一致' : '') : '');
  $('design-total').classList.toggle('design-total-off', Boolean(off));
}
function draw(data) {
  state = data;
  const design = data.design;
  $('design-note').textContent = `${data.topic} · 记录 #${data.id} · ${data.warning}`;
  $('design-preview').replaceChildren(renderMarkdown(data.preview));
  $('design-reminders').replaceChildren(...data.reminders.map(text => el('li', text)));
  $('design-reminders').hidden = !data.reminders.length;
  $('design-minutes').replaceChildren(...design.segments.map(segment => {
    const row = el('label', undefined, 'design-minutes-row'), input = el('input');
    input.type = 'number'; input.min = 1; input.max = 180; input.step = 1; input.value = segment.minutes;
    input.oninput = total;
    row.append(el('span', `${data.kinds[segment.kind]}：${segment.title}` + (segment.focus ? '（重点）' : '')), input);
    return row;
  }));
  $('design-key').value = design.focus.key_points.join('\n');
  $('design-hard').value = design.focus.difficulties.join('\n');
  $('design-skip').value = design.focus.not_today.join('\n');
  $('design-items').replaceChildren(...design.items.map(item => {
    const box = el('div', undefined, 'design-item'), stem = el('textarea'), answer = el('textarea');
    stem.rows = 3; stem.value = item.stem; stem.dataset.field = 'stem';
    answer.rows = 2; answer.value = item.answer; answer.dataset.field = 'answer';
    box.dataset.item = item.id;
    box.append(el('label', `${data.labels[item.id]} 题干`), stem, el('label', `${data.labels[item.id]} 答案`), answer);
    return box;
  }));
  total();
}
// The design as edited in the form; everything the form does not show is kept as it was.
function collect() {
  const design = structuredClone(state.design);
  [...$('design-minutes').querySelectorAll('input')].forEach((input, index) => { design.segments[index].minutes = Number(input.value); });
  design.focus.key_points = lines($('design-key').value);
  design.focus.difficulties = lines($('design-hard').value);
  design.focus.not_today = lines($('design-skip').value);
  for (const box of $('design-items').children) {
    const item = design.items.find(entry => entry.id === box.dataset.item);
    item.stem = box.querySelector('[data-field=stem]').value;
    item.answer = box.querySelector('[data-field=answer]').value;
  }
  return design;
}
const post = (path, body) => api(`/api/lesson-designs/${state.id}/${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
async function open(runId) {
  message(''); $('design-progress').textContent = ''; $('design-feedback').value = '';
  const data = await api('/api/lesson-designs/' + runId);
  draw(data);
  if (!dialog.open) dialog.showModal();
  dialog.querySelector('.dialog-body').scrollTop = 0;
}
async function act(label, work) {
  if (working || !state) return;
  message(''); lock(true); $('design-progress').textContent = label;
  try { await work(); }
  catch (e) { message(e.message, e.problems); $('design-progress').textContent = ''; }
  finally { lock(false); }
}
async function follow(job) {
  dialog.close();
  await refreshWorkspaceJobs().catch(() => {});
  await window.watchLectureJob(job.id);
}
$('design-check').onclick = () => act('正在检查修改…', async () => {
  draw(await post('check', {design: collect()}));
  $('design-progress').textContent = state.reminders.length ? '修改已应用，仍有提醒，见上方。' : '修改已应用，未发现结构问题；内容仍需教师判断。';
});
$('design-confirm').onclick = () => act('正在提交…', async () => follow(await post('expand', {design: collect()})));
$('design-revise').onclick = () => act('正在提交…', async () => {
  const note = $('design-feedback').value.trim();
  if (!note) throw new Error('请先填写修改意见。');
  await follow(await post('revise', {note, design: collect()}));
});
$('close-design').onclick = () => { if (!working) dialog.close(); };
dialog.addEventListener('cancel', event => { if (working) event.preventDefault(); });
// Designed lessons always use LaTeX and program-drawn figures; the review switch only applies to them.
function syncMode() {
  const designed = $('generation-mode').value === 'designed-v1';
  $('lecture-format').disabled = designed;
  if (designed) $('lecture-format').value = 'rich-v1';
  $('design-review').disabled = !designed;
}
$('generation-mode').addEventListener('change', syncMode);
window.addEventListener('lecture-version-changed', syncMode);
syncMode();
window.openDesignReview = open;
})();
