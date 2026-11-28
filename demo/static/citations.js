/* Hovering or tabbing to a citation such as [M1] in the lecture shows a short card with the source:
   its title, the start of the excerpt and where it comes from. Clicking still opens the full source
   check (lecture-workflow.js). The card reads the saved lecture's source snapshot (lastResult). */
(() => {
let card = null, current = null, showTimer = 0, hideTimer = 0;

function sourceOf(id) {
  const ctx = typeof lastResult !== 'undefined' && lastResult ? lastResult.context || {} : {};
  if (id.startsWith('M')) {
    const m = (ctx.materials || []).find(item => 'M' + item.id === id);
    if (m) return {title: m.title, text: m.content, origin: [m.source_title, m.section].filter(Boolean).join(' · ')};
  } else if (id.startsWith('V')) {
    const v = (ctx.visual_evidence || []).find(item => item.citation_id === id);
    if (v) return {title: v.label, text: v.content, origin: '插图识读'};
  }
  return null;
}

function build(button) {
  const id = button.textContent.replace(/[[\]]/g, '');
  const source = sourceOf(id);
  const head = el('div', undefined, 'cite-card-head');
  head.append(el('span', id, 'cite-card-id'), el('strong', source ? source.title || '来源' : '不在来源快照中'));
  const parts = [head];
  if (source) {
    const text = String(source.text || '').replace(/\s+/g, ' ').trim();
    parts.push(el('p', text.length > 140 ? text.slice(0, 140) + '…' : text, 'cite-card-text'));
    if (source.origin) parts.push(el('p', source.origin, 'cite-card-origin'));
    parts.push(el('p', '点击查看全文并核查是否支持这句话', 'cite-card-hint'));
  } else parts.push(el('p', '这份讲义的材料里没有这个编号，请更正正文。', 'cite-card-text'));
  card.replaceChildren(...parts);
}

function show(button) {
  clearTimeout(hideTimer);
  if (current === button && card?.matches(':popover-open')) return;
  if (!card) {
    card = el('div', undefined, 'cite-card');
    card.id = 'cite-card'; card.popover = 'manual'; card.setAttribute('role', 'tooltip');
    card.addEventListener('pointerenter', () => clearTimeout(hideTimer));
    card.addEventListener('pointerleave', () => hide());
    document.body.append(card);
  }
  release();
  current = button;
  // The card is placed against this button with CSS anchor positioning (theme.css).
  button.style.setProperty('anchor-name', '--cite');
  button.setAttribute('aria-describedby', 'cite-card');
  build(button);
  if (!card.matches(':popover-open')) card.showPopover();
}
function release() {
  if (!current) return;
  current.style.removeProperty('anchor-name');
  current.removeAttribute('aria-describedby');
  current = null;
}
function hide(now = false) {
  clearTimeout(showTimer); clearTimeout(hideTimer);
  // The anchor stays on the button until the next card is shown, so the card fades out in place.
  const close = () => { if (card?.matches(':popover-open')) card.hidePopover(); current?.removeAttribute('aria-describedby'); };
  if (now) close(); else hideTimer = setTimeout(close, 150);
}

const citation = target => target?.closest?.('#lecture button.citation');
document.addEventListener('pointerover', event => {
  const button = citation(event.target);
  if (!button || event.pointerType !== 'mouse') return;
  clearTimeout(showTimer); clearTimeout(hideTimer);
  showTimer = setTimeout(() => show(button), 250);
});
document.addEventListener('pointerout', event => {
  if (citation(event.target) && !citation(event.relatedTarget)) hide();
});
document.addEventListener('focusin', event => {
  const button = citation(event.target);
  if (button && button.matches(':focus-visible')) show(button);
});
document.addEventListener('focusout', event => { if (citation(event.target)) hide(); });
document.addEventListener('click', event => { if (citation(event.target)) hide(true); });
document.addEventListener('keydown', event => { if (event.key === 'Escape' && card?.matches(':popover-open')) hide(true); });
window.addEventListener('scroll', () => { if (card?.matches(':popover-open') && !card.matches(':hover')) hide(true); }, {passive: true});
})();
