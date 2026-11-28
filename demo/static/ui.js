/* Small shared interface pieces: icons from the Lucide sprite, a toast for finished actions, a
   short "done" state on a button, skeleton rows while a list loads, and the sliding highlight
   under tab lists. Loaded before the other scripts; they call it as window.ui?.name(...), so they
   still run where this file is absent (the script tests load files one by one). */
(() => {
const SPRITE = '/static/vendor/lucide/icons.svg';
const SVG = 'http://www.w3.org/2000/svg';

function icon(name, extra = '') {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', `icon ${extra}`.trim());
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const use = document.createElementNS(SVG, 'use');
  use.setAttribute('href', `${SPRITE}#${name}`);
  svg.append(use);
  return svg;
}

// One toast at a time, in the top layer so it also shows above an open dialog.
let host = null, timer = 0;
function toast(message, {tone = 'ok', action = '', onAction = null, duration = 3600} = {}) {
  if (!host) {
    host = document.createElement('div');
    host.className = 'toast';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    host.popover = 'manual';
    document.body.append(host);
  }
  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = message;
  const parts = [icon(tone === 'ok' ? 'circle-check' : 'circle-alert', 'toast-icon'), text];
  if (action && onAction) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'toast-action'; button.textContent = action;
    button.onclick = () => { hide(); onAction(); };
    parts.push(button);
  }
  host.dataset.tone = tone;
  host.replaceChildren(...parts);
  // Shown again each time, so it moves above a dialog opened since the last one.
  if (host.matches(':popover-open')) host.hidePopover();
  host.showPopover();
  clearTimeout(timer);
  timer = setTimeout(hide, duration);
}
function hide() {
  clearTimeout(timer);
  if (host?.matches(':popover-open')) host.hidePopover();
}

// A finished action shows a tick and a short result on its own button, then the label returns.
function done(button, text, ms = 2000) {
  if (!button) return;
  const before = [...button.childNodes];
  const mark = icon('check', 'icon-pop');
  button.replaceChildren(mark, document.createTextNode(text));
  button.classList.add('is-done');
  setTimeout(() => {
    button.classList.remove('is-done');
    // Someone redrew the button meanwhile (a new job started): keep theirs.
    if (button.contains(mark)) button.replaceChildren(...before);
  }, ms);
}

// Grey bars in the shape of the content that is on its way; the text is for screen readers.
function skeleton(lines = 3, label = '正在读取…') {
  const box = document.createElement('div');
  box.className = 'skeleton';
  box.setAttribute('role', 'status');
  const text = document.createElement('span');
  text.className = 'visually-hidden';
  text.textContent = label;
  box.append(text);
  for (let i = 0; i < lines; i++) {
    const line = document.createElement('span');
    line.className = 'skeleton-line';
    box.append(line);
  }
  return box;
}

// Tab lists get one highlight that slides to the selected tab instead of jumping.
function tabIndicator(list) {
  if (list.querySelector(':scope > .tab-indicator')) return;
  const thumb = document.createElement('span');
  thumb.className = 'tab-indicator';
  thumb.setAttribute('aria-hidden', 'true');
  list.prepend(thumb);
  list.classList.add('has-indicator');
  let placed = false;
  const place = () => {
    const tab = list.querySelector(':scope > [role="tab"][aria-selected="true"]');
    if (!tab || !list.getClientRects().length) { placed = false; return; }
    // The first placement after being hidden jumps; later ones slide.
    thumb.classList.toggle('instant', !placed);
    thumb.style.setProperty('--x', `${tab.offsetLeft}px`);
    thumb.style.setProperty('--y', `${tab.offsetTop}px`);
    thumb.style.setProperty('--w', `${tab.offsetWidth}px`);
    thumb.style.setProperty('--h', `${tab.offsetHeight}px`);
    if (!placed) { thumb.getBoundingClientRect(); requestAnimationFrame(() => thumb.classList.remove('instant')); }
    placed = true;
  };
  new MutationObserver(place).observe(list, {subtree: true, attributes: true, attributeFilter: ['aria-selected']});
  new ResizeObserver(place).observe(list);
  document.fonts?.ready.then(place);
  place();
}

window.ui = {icon, toast, done, skeleton, tabIndicator};
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.record-tabs, .upload-tabs').forEach(tabIndicator);
});
})();
