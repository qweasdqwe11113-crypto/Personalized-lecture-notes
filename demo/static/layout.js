/* Presentation only: bounded desktop columns; no model or database writes. */
(() => {
  const workspace = document.getElementById('workspace');
  const separator = document.getElementById('layout-resizer');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  const desktop = window.matchMedia('(min-width: 68.75rem)');
  const storageKey = 'lecture-demo-sidebar-width-v1';
  let preferred = 330;
  let drag = null;

  try {
    const saved = Number(localStorage.getItem(storageKey));
    if (Number.isFinite(saved) && saved >= 280 && saved <= 440) preferred = saved;
  } catch (_) { /* The interface also works when browser storage is unavailable. */ }

  function limits() {
    // Keep at least 540px for the result and 40px for gaps / separator.
    const maximum = Math.max(280, Math.min(440, workspace.clientWidth - 580));
    return [280, maximum];
  }

  // The collapse handle sits on the settings column's right edge, or at the left edge once
  // collapsed; its label and state follow. It is shown in the two-column layout only (CSS).
  function placeToggle(actualWidth) {
    if (!sidebarToggle) return;
    const collapsed = workspace.classList.contains('settings-collapsed');
    const label = collapsed ? '展开讲义设置' : '收起讲义设置';
    sidebarToggle.style.left = (collapsed ? 0 : actualWidth) + 'px';
    sidebarToggle.style.transform = 'none';
    sidebarToggle.textContent = collapsed ? '»' : '«';
    sidebarToggle.title = label;
    sidebarToggle.setAttribute('aria-label', label);
    sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
  }

  function apply(width, remember = false) {
    const [minimum, maximum] = limits();
    const actual = Math.round(Math.max(minimum, Math.min(maximum, width)));
    workspace.style.setProperty('--sidebar-width', `${actual}px`);
    separator.setAttribute('aria-valuemin', minimum);
    separator.setAttribute('aria-valuemax', Math.round(maximum));
    separator.setAttribute('aria-valuenow', actual);
    separator.setAttribute('aria-valuetext', `设置栏宽度 ${actual} 像素`);

    placeToggle(actual);

    if (remember) {
      preferred = actual;
      try { localStorage.setItem(storageKey, String(preferred)); } catch (_) { /* optional */ }
    }
  }

  function finish(event, remember = true) {
    if (!drag || (event && event.pointerId !== drag.id)) return;
    const pointerId = drag.id;
    drag = null;
    workspace.classList.remove('is-resizing');
    if (remember) apply(Number(separator.getAttribute('aria-valuenow')), true);
    if (separator.hasPointerCapture(pointerId)) separator.releasePointerCapture(pointerId);
  }

  separator.addEventListener('pointerdown', event => {
    if (!desktop.matches || event.button !== 0) return;
    if (workspace.classList.contains('settings-collapsed')) return;
    event.preventDefault();
    separator.focus({preventScroll: true});
    drag = {id: event.pointerId, x: event.clientX,
      width: Number(separator.getAttribute('aria-valuenow'))};
    separator.setPointerCapture(event.pointerId);
    workspace.classList.add('is-resizing');
  });
  separator.addEventListener('pointermove', event => {
    if (drag && drag.id === event.pointerId) apply(drag.width + event.clientX - drag.x);
  });
  separator.addEventListener('pointerup', event => finish(event));
  separator.addEventListener('pointercancel', event => finish(event, false));
  separator.addEventListener('lostpointercapture', () => finish(null, false));
  window.addEventListener('blur', () => finish(null, false));
  separator.addEventListener('dblclick', () => apply(330, true));
  separator.addEventListener('keydown', event => {
    if (!desktop.matches || event.ctrlKey || event.metaKey || event.altKey) return;
    if (workspace.classList.contains('settings-collapsed')) return;
    const [minimum, maximum] = limits();
    const current = Number(separator.getAttribute('aria-valuenow'));
    const step = event.shiftKey ? 32 : 16;
    const widths = {ArrowLeft: current - step, ArrowRight: current + step,
      Home: minimum, End: maximum};
    if (Object.prototype.hasOwnProperty.call(widths, event.key)) {
      event.preventDefault();
      apply(widths[event.key], true);
    }
  });

  // 折叠 / 展开：统一在这里处理，避免 app.js 与 layout.js 双重监听。
  sidebarToggle?.addEventListener('click', () => {
    workspace.classList.toggle('settings-collapsed');
    placeToggle(Number(separator.getAttribute('aria-valuenow')) || preferred);
  });

  function resize() {
    if (drag) finish(null, false);
    // The stacked layout has no handle, so the settings must not stay hidden there.
    if (!desktop.matches) workspace.classList.remove('settings-collapsed');
    apply(preferred);
  }
  let observedWidth = workspace.clientWidth;
  new ResizeObserver(() => {
    const width = workspace.clientWidth;
    if (width !== observedWidth) {
      observedWidth = width;
      resize();
    }
  }).observe(workspace);
  desktop.addEventListener('change', resize);

  for (const dialog of document.querySelectorAll('dialog')) {
    const body = dialog.querySelector('.dialog-body');
    if (!body) continue;  // e.g. the zoom view, which has no scrolling body
    const content = dialog.querySelector('#trace-content') || body;
    const resetScroll = () => {
      if (dialog.open && !content.hidden) {
        const header = dialog.querySelector('.editor-top, .lesson-dialog-header');
        if (header) header.scrollTop = 0;
        body.scrollTop = 0;
      }
    };
    // A hidden dialog has no scroll box. Reset only after opening / loading,
    // rather than on close; resizing an already open editor keeps its position.
    const observer = new MutationObserver(() => {
      resetScroll();
      requestAnimationFrame(resetScroll);
    });
    observer.observe(dialog, {attributes: true, attributeFilter: ['open']});
    observer.observe(content, {attributes: true, attributeFilter: ['hidden']});
  }

  apply(preferred);
})();
