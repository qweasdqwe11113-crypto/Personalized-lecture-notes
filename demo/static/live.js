/* Live preview of a running lecture job. Display only: polling in jobs.js stays the
   authority for completion, and the saved lecture replaces this text when it arrives. */
(() => {
const states = {waiting: '等待', writing: '写作中', repairing: '未通过检查，重写中', done: '完成'};
let source = null, parts = new Map(), order = [], timer = 0, shown = false;
const text = () => order.map(key => parts.get(key)?.text || '').filter(Boolean).join('\n\n');
function draw() {
  timer = 0;
  const content = text();
  $('live-text').replaceChildren(renderMarkdown(content));
  $('live-count').textContent = content ? `已写 ${(content.match(/[一-鿿]/g) || []).length} 个汉字` : '';
  const named = order.map(key => parts.get(key)).filter(part => part && part.heading);
  $('live-sections').replaceChildren(...named.map(part => {
    const chip = el('span', `${part.heading} · ${states[part.state] || part.state}`, 'live-chip live-' + part.state);
    return chip;
  }));
  $('live-sections').hidden = !named.length;
}
function stop() {
  if (source) { source.close(); source = null; }
  if (timer) { clearTimeout(timer); timer = 0; }
  parts = new Map(); order = []; shown = false;
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
