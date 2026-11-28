/* Live preview of a running lecture job. Display only: polling in jobs.js stays the
   authority for completion, and the saved lecture replaces this text when it arrives. */
(() => {
const states = {waiting: '等待', writing: '写作中', repairing: '未通过检查，重写中', done: '完成'};
let source = null, parts = new Map(), order = [], timer = 0, shown = false;
const text = () => order.map(key => parts.get(key)?.text || '').filter(Boolean).join('\n\n');
// Following stops once the teacher scrolls up from the end of the preview, and resumes at the end.
function following() {
  return $('live-preview').getBoundingClientRect().bottom <= window.innerHeight + 80;
}
function draw() {
  timer = 0;
  const follow = following();
  const content = text();
  $('live-text').replaceChildren(renderMarkdown(content));
  $('live-count').textContent = content ? `已写 ${(content.match(/[一-鿿]/g) || []).length} 个汉字` : '';
  const named = order.map(key => parts.get(key)).filter(part => part && part.heading);
  $('live-sections').replaceChildren(...named.map(part => {
    const chip = el('span', `${part.heading} · ${states[part.state] || part.state}`, 'live-chip live-' + part.state);
    return chip;
  }));
  $('live-sections').hidden = !named.length;
  // Written section by section: the generate button says how far it is and fills from the left.
  const total = order.length, finished = order.filter(key => parts.get(key)?.state === 'done').length;
  const button = $('generate'), label = button.lastChild;
  if (total > 1) {
    button.style.setProperty('--progress', String(finished / total));
    if (button.disabled && label?.nodeType === Node.TEXT_NODE) label.textContent = `已写 ${finished}/${total} 节`;
  }
  const overflow = $('live-preview').getBoundingClientRect().bottom - window.innerHeight + 24;
  if (follow && overflow > 0) window.scrollBy(0, overflow);
}
function stop() {
  if (source) { source.close(); source = null; }
  if (timer) { clearTimeout(timer); timer = 0; }
  parts = new Map(); order = []; shown = false;
  $('generate').style.removeProperty('--progress');
  $('live-preview').hidden = true; $('live-text').replaceChildren();
}
function start(id) {
  stop();
  if (typeof EventSource === 'undefined') return;
  const stream = source = new EventSource('/api/jobs/' + encodeURIComponent(id) + '/stream');
  stream.onmessage = event => {
    if (stream !== source) return;
    let data; try { data = JSON.parse(event.data); } catch { return; }
    order = data.order || order;
    for (const part of data.parts || []) parts.set(part.key, part);
    $('live-stage').textContent = data.stage || '模型写作中';
    if (!shown && (text() || order.length > 1)) { shown = true; $('loading').hidden = true; $('live-preview').hidden = false; }
    if (!timer) timer = setTimeout(draw, 220);
  };
  const end = () => { if (stream === source) { stream.close(); source = null; } };
  stream.addEventListener('done', end);
  stream.onerror = end;
}
window.liveLecture = {start, stop, active: () => shown};
})();
