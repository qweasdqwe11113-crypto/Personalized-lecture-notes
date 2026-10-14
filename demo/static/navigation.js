/* Hash navigation preserves form inputs and background tasks across pages. */
(() => {
  const pages = {
    workbench: ['讲义工作台', '从学生的学习起点出发，让每一份讲义更有针对性。'],
    profiles: ['学生画像', '连接诊断证据与教学策略，持续了解学生的学习需要。'],
    materials: ['课程材料', '查看当前教学任务的知识点来源，为讲义提供可靠依据。'],
    records: ['生成记录', '管理已保存讲义，查看后台任务与每次生成的完整记录。'],
    settings: ['系统设置', '管理本地工作空间的服务连接与模型配置。'],
  };
  const aliases = {'lecture-results': 'workbench', 'support-modules': 'records'};
  let active;
  function navigate(focus = false) {
    const hash = location.hash.slice(1);
    const page = pages[hash] ? hash : aliases[hash] || 'workbench';
    if (!pages[hash]) history.replaceState(null, '', '#' + page);
    if (active === page) return;
    active = page;
    for (const key of Object.keys(pages)) $('page-' + key).hidden = key !== page;
    for (const link of document.querySelectorAll('[data-page]')) {
      if (link.dataset.page === page) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    $('page-title').textContent = pages[page][0];
    $('page-description').textContent = pages[page][1];
    $('breadcrumb-page').textContent = pages[page][0];
    document.title = pages[page][0] + ' · 知页';
    document.body.dataset.page = page;
    window.scrollTo({top: 0});
    if (focus) $('page-title').focus({preventScroll: true});
  }
  window.addEventListener('hashchange', () => navigate(true));
  window.openWorkspacePage = page => {
    if (pages[page] && active !== page) location.hash = page;
  };
  // Mirror the existing selectors, retaining their course filtering and requests.
  function syncSelector(sourceId, targetId) {
    const source = $(sourceId), target = $(targetId);
    const sync = () => {
      target.replaceChildren(...Array.from(source.options, option => option.cloneNode(true)));
      target.value = source.value;
      target.disabled = source.disabled;
    };
    new MutationObserver(sync).observe(source, {childList: true, attributes: true, attributeFilter: ['disabled']});
    source.addEventListener('change', sync);
    target.addEventListener('change', () => {
      source.value = target.value;
      source.dispatchEvent(new Event('change'));
    });
    sync();
    return sync;
  }
  const syncs = [syncSelector('topic', 'profile-topic-select'), syncSelector('student', 'profile-student-select'), syncSelector('topic', 'material-topic-select')];
  // Historical lectures update selectors programmatically without a change event.
  window.addEventListener('workspace-context', () => syncs.forEach(sync => sync()));
  $('view-current-profile').onclick = () => {
    if (busy || $('student').disabled || !$('student').value) return;
    $('personalization-mode').value = 'profile';
    loadContext();
  };
  navigate();
})();
