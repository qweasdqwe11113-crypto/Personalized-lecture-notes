/* Runs in <head> before the page draws, so a dark page never flashes light: picks the theme from the
   choice saved in 系统设置 → 外观, or from the system when the choice is 跟随系统 (the default),
   and follows the system when it changes. */
(() => {
  const KEY = 'zhiye-theme';
  const media = matchMedia('(prefers-color-scheme: dark)');
  const saved = () => { try { return localStorage.getItem(KEY) || 'auto'; } catch { return 'auto'; } };
  const apply = () => {
    const choice = saved();
    document.documentElement.dataset.theme = choice === 'dark' || (choice === 'auto' && media.matches) ? 'dark' : 'light';
  };
  apply();
  media.addEventListener('change', apply);
  window.zhiyeTheme = {
    get: saved,
    set(choice) {
      try { if (choice === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, choice); } catch { /* private mode: this window only */ }
      apply();
    },
  };
})();
