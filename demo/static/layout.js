/* Presentation only: bounded desktop columns; no model or database writes. */
(() => {
  const workspace = document.getElementById('workspace');
  const separator = document.getElementById('layout-resizer');
  const support = document.getElementById('support-modules');
  const aside = document.getElementById('settings-column');
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

  function apply(width, remember = false) {
    const [minimum, maximum] = limits();
    const actual = Math.round(Math.max(minimum, Math.min(maximum, width)));
    workspace.style.setProperty('--sidebar-width', `${actual}px`);
    separator.setAttribute('aria-valuemin', minimum);
    separator.setAttribute('aria-valuemax', Math.round(maximum));
    separator.setAttribute('aria-valuenow', actual);
    separator.setAttribute('aria-valuetext', `设置栏宽度 ${actual} 像素`);
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

  function placeModules() {
    const destination = desktop.matches ? aside : workspace;
    if (support.parentElement !== destination) {
      const focused = document.activeElement;
      const restoreFocus = support.contains(focused);
      destination.append(support);
      if (restoreFocus) focused.focus({preventScroll: true});
    }
  }

  function resize() {
    placeModules();
    if (drag) finish(null, false);
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
    const content = dialog.querySelector('#trace-content') || body;
    const resetScroll = () => {
      if (dialog.open && !content.hidden) {
        dialog.querySelector('.editor-top').scrollTop = 0;
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
  placeModules();
  apply(preferred);
})();
