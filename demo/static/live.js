/* Live preview of a running lecture job. Display only: polling in jobs.js stays the
   authority for completion, and the saved lecture replaces this text when it arrives.
   The step list above the paper says which stage the job is in; the thin bar under it, the
   generate button and the desktop taskbar advance by stage and by finished section, not by time. */
(() => {
const states = {waiting: '等待', writing: '写作中', repairing: '未通过检查，重写中', done: '完成'};
// Stage names differ by writing mode; the stream's stage label tells which mode a job is in.
const MODES = {
  'designed-v1': ['准备材料', '课堂设计', '分环节写作', '程序检查', '保存'],
  'sectioned-v1': ['准备材料', '规划大纲', '分章节写作', '程序检查', '保存'],
  single: ['准备材料', '模型写作', '程序检查', '保存'],
};
function stageOf(label) {
  if (/^(课堂设计|读取已确认的课堂设计)/.test(label)) return ['designed-v1', 1];
  if (label.startsWith('规划大纲')) return ['sectioned-v1', 1];
  if (label.startsWith('分环节写作')) return ['designed-v1', 2];
  if (label.startsWith('分章节写作')) return ['sectioned-v1', 2];
  if (label.startsWith('模型写作')) return ['single', 1];
  if (label.startsWith('程序检查')) return [null, -1];
  return [null, null];
}
// Redrawn several times a second: unchanged steps and chips are kept, so their entry and tick
// animations play once instead of on every update.
const put = (box, nodes) => (window.ui?.morph || ((b, n) => b.replaceChildren(...n)))(box, nodes);
let source = null, parts = new Map(), order = [], timer = 0, shown = false, mode = 'designed-v1', stage = 0;
const text = () => order.map(key => parts.get(key)?.text || '').filter(Boolean).join('\n\n');
// Following stops once the teacher scrolls up from the end of the preview, and resumes at the end.
function following() {
  return $('live-preview').getBoundingClientRect().bottom <= window.innerHeight + 80;
}
function drawSteps() {
  const names = MODES[mode] || MODES['designed-v1'];
  const total = order.length, finished = order.filter(key => parts.get(key)?.state === 'done').length;
  const writing = names.length - 3;
  // Within the writing stage the bar moves section by section.
  const within = stage === writing && total > 1 ? finished / total : 0;
  const fraction = Math.max(0.03, Math.min(1, (stage + within) / (names.length - 1)));
  const list = $('gen-steps');
  list.style.setProperty('--gen', String(fraction));
  put(list, names.map((name, i) => {
    const state = i < stage ? 'done' : i === stage ? 'current' : 'waiting';
    const item = el('li', undefined, 'gen-step gen-' + state);
    const mark = el('span', undefined, 'gen-mark'); mark.setAttribute('aria-hidden', 'true');
    const tick = state === 'done' && window.ui?.icon('check');
    if (tick) mark.append(tick);
    item.append(mark, el('span', name, 'gen-name'));
    if (state === 'current') item.setAttribute('aria-current', 'step');
    return item;
  }));
  window.zhiyeDesktop?.setProgress?.(fraction);
  const button = $('generate'), label = button.lastChild;
  if (total > 1) button.style.setProperty('--progress', String(within || (stage > writing ? 1 : 0)));
  if (button.disabled && label?.nodeType === Node.TEXT_NODE) {
    label.textContent = stage === writing && total > 1 ? `已写 ${finished}/${total} 节` : `${names[stage]}…`;
  }
}
function draw() {
  timer = 0;
  const follow = following();
  const content = text();
  $('live-text').replaceChildren(renderMarkdown(content));
  $('live-count').textContent = content ? `已写 ${(content.match(/[一-鿿]/g) || []).length} 个汉字` : '';
  const named = order.map(key => parts.get(key)).filter(part => part && part.heading);
  put($('live-sections'), named.map(part => {
    const chip = el('span', `${part.heading} · ${states[part.state] || part.state}`, 'live-chip live-' + part.state);
    const tick = part.state === 'done' && window.ui?.icon('check');
    if (tick) chip.prepend(tick);
    return chip;
  }));
  $('live-sections').hidden = !named.length;
  drawSteps();
  const overflow = $('live-preview').getBoundingClientRect().bottom - window.innerHeight + 24;
  if (follow && overflow > 0) window.scrollBy(0, overflow);
}
function stop() {
  if (source) { source.close(); source = null; }
  if (timer) { clearTimeout(timer); timer = 0; }
  parts = new Map(); order = []; shown = false; stage = 0;
  $('generate').style.removeProperty('--progress');
  $('gen-steps').hidden = true; $('gen-steps').replaceChildren();
  window.zhiyeDesktop?.setProgress?.(-1);
  $('live-preview').hidden = true; $('live-text').replaceChildren();
}
function start(id) {
  stop();
  // Until the stream names its stage, the choice on the page is the best guess of the mode.
  const chosen = $('generation-mode')?.value;
  mode = MODES[chosen] ? chosen : 'designed-v1';
  $('gen-steps').hidden = false; drawSteps();
  if (typeof EventSource === 'undefined') return;
  const stream = source = new EventSource('/api/jobs/' + encodeURIComponent(id) + '/stream');
  stream.onmessage = event => {
    if (stream !== source) return;
    let data; try { data = JSON.parse(event.data); } catch { return; }
    order = data.order || order;
    for (const part of data.parts || []) parts.set(part.key, part);
    $('live-stage').textContent = data.stage || '模型写作中';
    const [named, index] = stageOf(data.stage || '');
    if (named) mode = named;
    if (index !== null) stage = index < 0 ? MODES[mode].length - 2 : index;
    if (!shown && (text() || order.length > 1)) { shown = true; $('loading').hidden = true; $('live-preview').hidden = false; }
    if (!timer) timer = setTimeout(draw, 220);
  };
  const end = () => { if (stream === source) { stream.close(); source = null; } };
  stream.addEventListener('done', end);
  stream.onerror = end;
}
window.liveLecture = {start, stop, active: () => shown};
})();
