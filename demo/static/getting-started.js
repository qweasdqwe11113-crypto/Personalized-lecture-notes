/* "开始使用" on the workbench: what is still missing before the first lecture can be generated
   (model connection, a course, its knowledge library, a class with students, a lesson). Each step
   reads the live workspace, so the guide disappears by itself once everything is in place; a
   fresh desktop install sees it first, the demonstration setup never does. */
(() => {
  const scope = window.workspaceScope;
  const KEY = 'zhiye.gettingStarted.collapsed';
  let version = 0;
  const collapsed = () => { try { return localStorage.getItem(KEY) === '1'; } catch { return false; } };
  const remember = value => { try { localStorage.setItem(KEY, value ? '1' : '0'); } catch { /* private window */ } };

  async function facts() {
    const [health, listing] = await Promise.all([api('/api/health'), api('/api/courses')]);
    const course = listing.courses.find(c => c.id === scope.courseId()) || listing.courses[0] || null;
    const library = course ? (await api(`/api/courses/${course.id}/knowledge`)).points.filter(p => !p.archived) : [];
    return {health, courses: listing.courses, course, library};
  }

  function steps({health, courses, course, library}) {
    const classes = course?.classes || [];
    const seated = classes.find(c => c.student_ids.length);
    const needCourse = course ? '' : '需要先新建课程';
    return [
      {title: '连接语言模型', done: health.endpoint_format_ok && health.model_present,
       text: '讲义、出题、阅卷和资料识别都要用到语言模型。在系统设置中填写服务地址、模型名称和密钥，测试通过后保存，再重启服务。',
       action: ['前往系统设置', '#settings']},
      {title: '新建课程', done: courses.length > 0,
       text: '课程是备课、资料和班级的归属，例如「大学 · 高等数学」。可以建多门课程，在顶部随时切换。',
       action: ['新建课程', '#courses', 'new-course'], done_text: course ? `当前课程：${scope.courseLabel(course)}` : ''},
      {title: '建立知识点库', done: library.length > 0, blocked: needCourse,
       text: '为课程添加知识点：逐条添加、导入表格，或上传讲义和教材，由模型识别后确认入库。',
       action: ['添加知识点', '#materials/knowledge'], done_text: `已有 ${library.length} 个知识点`},
      {title: '添加班级和学生', done: Boolean(seated), blocked: needCourse,
       text: '新建班级后逐个添加学生，或下载模板批量导入名单与学习特点。讲义会参考学生的画像和掌握情况。',
       action: classes.length ? ['添加学生', '#profiles/import'] : ['新建班级', '#materials/classes'],
       done_text: seated ? `${classes.length} 个班级 · ${course.student_count} 名学生` : ''},
      {title: '新建备课', done: Boolean(course?.lesson_keys.length), blocked: needCourse,
       text: '选择本次要讲的知识点，写下教学目标；之后就能在这里为全班或某个学生生成讲义。',
       action: ['新建备课', '#materials/new'], done_text: course ? `已有 ${course.lesson_keys.length} 份备课` : ''},
    ];
  }

  function open([, hash, extra]) {
    location.hash = hash;
    // The course list keeps its "新建课程" form folded; unfold it once the page is showing.
    if (extra === 'new-course') setTimeout(() => {
      const form = $('new-course-form');
      if (form?.hidden) $('toggle-new-course')?.click();
      $('new-course-name')?.focus();
    }, 60);
  }

  function draw(list) {
    const done = list.filter(s => s.done).length, box = $('getting-started');
    box.hidden = done === list.length;
    if (box.hidden) return;
    const current = list.findIndex(s => !s.done && !s.blocked);
    $('getting-started-count').textContent = `已完成 ${done} / ${list.length}`;
    $('getting-started-fill').style.inlineSize = `${Math.round(done / list.length * 100)}%`;
    $('getting-started-steps').replaceChildren(...list.map((step, index) => {
      const item = el('li', undefined, 'start-step' + (step.done ? ' is-done' : index === current ? ' is-current' : step.blocked ? ' is-blocked' : ''));
      const mark = el('span', step.done ? '✓' : String(index + 1), 'start-mark'); mark.setAttribute('aria-hidden', 'true');
      const body = el('div', undefined, 'start-body');
      const title = el('h3', step.title);
      if (step.done) title.append(el('span', '已完成', 'start-tag'));
      body.append(title, el('p', step.done ? step.done_text || step.text : step.blocked || step.text));
      item.append(mark, body);
      if (!step.blocked || step.done) {
        const go = el('button', step.done ? '查看' : step.action[0], index === current ? 'primary' : 'secondary');
        go.type = 'button';
        go.setAttribute('aria-label', `${step.title}：${go.textContent}`);
        go.onclick = () => open(step.action);
        item.append(go);
      }
      return item;
    }));
    fold(collapsed());
  }

  function fold(value) {
    $('getting-started').classList.toggle('is-collapsed', value);
    $('getting-started-steps').hidden = value;
    $('getting-started-toggle').textContent = value ? '展开' : '收起';
    $('getting-started-toggle').setAttribute('aria-expanded', String(!value));
  }

  async function refresh() {
    if (document.body.dataset.page && document.body.dataset.page !== 'workbench') return;
    const token = ++version;
    try {
      const data = await facts();
      if (token === version) draw(steps(data));
    } catch {
      // The guide is optional: a failed read leaves the workbench as it is.
      if (token === version) $('getting-started').hidden = true;
    }
  }

  $('getting-started-toggle').onclick = () => { const value = !collapsed(); remember(value); fold(value); };
  window.addEventListener('scope-changed', refresh);
  window.addEventListener('hashchange', () => setTimeout(refresh));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  scope.ready.then(refresh, () => {});
})();
