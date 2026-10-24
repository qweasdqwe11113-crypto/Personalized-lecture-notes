/* Hash navigation preserves form inputs and background tasks across pages. */
(() => {
  const pages = {
    workbench: ['讲义工作台', '选择备课副本和学生，补充本次备注，生成有针对性的讲义。'],
    quizzes: ['出题', '按课程目标与学生水平生成练习题。'],
    grading: ['阅卷', '上传学生作答与参考答案，自动提取并逐题批改。'],
    profiles: ['学生画像', '独立管理每位同学的信息、学习特点和长期薄弱点。'],
    materials: ['课程材料', '保存目标、教案和参考资料，建立可以反复使用的备课副本。'],
    uploads: ['上传资料', '汇集本地文档与网页内容，建立你的教学资料库。'],
    records: ['生成记录', '管理已保存讲义，查看后台任务与每次生成的完整记录。'],
    settings: ['系统设置', '管理本地工作空间的服务连接与模型配置。'],
  };
  const aliases = {'lecture-results': 'workbench', 'support-modules': 'records'};
  let active;
  function navigate(focus = false) {
    const hash = location.hash.slice(1);
    const profileRoute = /^profiles\/(new|[1-9]\d*)$/.test(hash);
    const materialRoute = /^materials\/[\w-]+$/.test(hash);
    const recordsRoute = /^records\/(lectures|quizzes)$/.test(hash);
    const page = recordsRoute ? 'records' : materialRoute ? 'materials' : profileRoute ? 'profiles' : pages[hash] ? hash : aliases[hash] || 'workbench';
    if (!pages[hash] && !profileRoute && !materialRoute && !recordsRoute) history.replaceState(null, '', '#' + page);
    if (active === hash) return;
    active = hash;
    for (const key of Object.keys(pages)) $('page-' + key).hidden = key !== page;
    for (const link of document.querySelectorAll('[data-page]')) {
      if (link.dataset.page === page) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    const title = materialRoute ? (hash === 'materials/new' ? '新建备课副本' : '备课副本详情') : profileRoute ? (hash === 'profiles/new' ? '新建学生画像' : '学生档案') : pages[page][0];
    $('page-title').textContent = title;
    document.title = title + ' · 知页';
    document.body.dataset.page = page;
    const recordsTab = hash === 'records/quizzes' ? 'quizzes' : 'lectures';
    for (const key of ['lectures', 'quizzes']) {
      const selected = key === recordsTab;
      $('record-tab-' + key).setAttribute('aria-selected', String(selected));
      $('record-tab-' + key).tabIndex = selected ? 0 : -1;
      $('record-panel-' + key).hidden = !selected;
    }
    if (page === 'records' && recordsTab === 'quizzes') window.dispatchEvent(new Event('quiz-history-open'));
    window.scrollTo({top: 0});
    if (focus) (recordsRoute ? $('record-tab-' + recordsTab) : $('page-title')).focus({preventScroll: true});
  }
  window.addEventListener('hashchange', () => navigate(true));
  for (const key of ['lectures', 'quizzes']) {
    $('record-tab-' + key).addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'lectures' : event.key === 'End' ? 'quizzes' : key === 'lectures' ? 'quizzes' : 'lectures';
      location.hash = 'records/' + next;
      $('record-tab-' + next).focus();
    });
  }
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
  const syncs = [syncSelector('topic', 'material-topic-select')];
  window.addEventListener('workspace-context', () => syncs.forEach(sync => sync()));
  navigate();
})();
