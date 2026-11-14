/* Hash navigation preserves form inputs and background tasks across pages. */
(() => {
  const pages = {
    workbench: ['讲义工作台', '选择备课和讲义对象（全班或学生），补充本次备注，生成有针对性的讲义。'],
    quizzes: ['出题', '按课程目标与班级或学生水平生成练习题。'],
    grading: ['阅卷', '上传学生作答与参考答案，自动提取并逐题批改。'],
    profiles: ['班级与画像', '班级画像汇总班内学生的诊断；学生档案在各课程间共享。'],
    materials: ['课程', '每门课程有自己的备课、课程资料和班级。'],
    records: ['生成记录', '管理已保存讲义，查看后台任务与每次生成的完整记录。'],
    settings: ['系统设置', '管理本地工作空间的服务连接与模型配置。'],
  };
  const aliases = {'lecture-results': 'workbench', 'support-modules': 'records'};
  // Old bookmarks: the upload page now lives inside the course.
  const redirects = {uploads: 'materials/resources'};
  const subTitles = {courses: '全部课程', 'materials/resources': '课程资料', 'materials/knowledge': '知识点库', 'materials/classes': '班级', 'materials/new': '新建备课',
    'profiles/students': '学生名单', 'profiles/resources': '班级资料', 'profiles/new': '新建学生', 'profiles/import': '批量导入学生资料'};
  // The desktop release has no fixed-experiment tab.
  const recordTypes = ['lectures', 'quizzes', 'grading', 'experiments'].filter(key => $('record-tab-' + key));
  let active;
  function navigate(focus = false) {
    let hash = location.hash.slice(1);
    if (redirects[hash]) { hash = redirects[hash]; history.replaceState(null, '', '#' + hash); }
    const profileRoute = /^profiles\/(new|import|students|resources|[1-9]\d*)$/.test(hash);
    const materialRoute = hash === 'courses' || /^materials\/[\w-]+$/.test(hash);
    const recordsRoute = /^records\/(lectures|quizzes|grading|experiments)$/.test(hash);
    const page = recordsRoute ? 'records' : materialRoute ? 'materials' : profileRoute ? 'profiles' : pages[hash] ? hash : aliases[hash] || 'workbench';
    if (!pages[hash] && !profileRoute && !materialRoute && !recordsRoute) history.replaceState(null, '', '#' + page);
    if (active === hash) return;
    active = hash;
    for (const key of Object.keys(pages)) $('page-' + key).hidden = key !== page;
    for (const link of document.querySelectorAll('a[data-page]')) {
      if (link.dataset.page === page) { link.setAttribute('aria-current', 'page'); if (link.scrollIntoView && link.parentElement.scrollWidth > link.parentElement.clientWidth) link.scrollIntoView({block: 'nearest', inline: 'nearest'}); }
      else link.removeAttribute('aria-current');
    }
    const title = subTitles[hash] || (materialRoute ? '备课详情' : profileRoute ? '学生档案' : pages[page][0]);
    $('page-title').textContent = title;
    document.title = title + ' · 知页';
    document.body.dataset.page = page;
    const recordsTab = recordsRoute && recordTypes.includes(hash.split('/')[1]) ? hash.split('/')[1] : 'lectures';
    for (const key of recordTypes) {
      const selected = key === recordsTab;
      $('record-tab-' + key).setAttribute('aria-selected', String(selected));
      $('record-tab-' + key).tabIndex = selected ? 0 : -1;
      $('record-panel-' + key).hidden = !selected;
    }
    if (page === 'records' && recordsTab === 'quizzes') window.dispatchEvent(new Event('quiz-history-open'));
    if (page === 'records' && recordsTab === 'experiments') window.dispatchEvent(new Event('experiment-history-open'));
    if (page === 'records' && recordsTab === 'grading') window.dispatchEvent(new Event('grading-history-open'));
    window.scrollTo({top: 0});
    if (focus) (recordsRoute ? $('record-tab-' + recordsTab) : $('page-title')).focus({preventScroll: true});
  }
  window.addEventListener('hashchange', () => navigate(true));
  for (const key of recordTypes) {
    $('record-tab-' + key).addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const index = recordTypes.indexOf(key);
      const next = event.key === 'Home' ? recordTypes[0] : event.key === 'End' ? recordTypes[recordTypes.length - 1] : recordTypes[(index + (event.key === 'ArrowRight' ? 1 : -1) + recordTypes.length) % recordTypes.length];
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
