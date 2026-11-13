/* Installable shell. The worker only keeps an offline copy of the page and its static
   files; API responses are never cached, so data always comes from the local service. */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
}
