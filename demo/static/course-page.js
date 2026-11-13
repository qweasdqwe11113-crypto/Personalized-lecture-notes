/* 课程 page: all courses (#courses), and one course's home with tabs for lessons (#materials),
   course resources (#materials/resources), the knowledge library (#materials/knowledge, knowledge.js)
   and classes (#materials/classes). The lesson list and
   lesson editor stay in course-manager.js; uploads.js fills the resources tab. */
(() => {
  const scope = window.workspaceScope;
  const tabs = {lessons: ['course-tab-lessons', 'course-list-page', 'materials'], resources: ['course-tab-resources', 'course-panel-resources', 'materials/resources'], knowledge: ['course-tab-knowledge', 'course-panel-knowledge', 'materials/knowledge'], classes: ['course-tab-classes', 'course-panel-classes', 'materials/classes']};
  const status = (id, message, failed = false) => { $(id).textContent = message || ''; $(id).dataset.error = String(failed); };
  const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  function view() {
    const hash = location.hash.slice(1);
    if (hash === 'courses') return 'all';
    if (hash === 'materials') return 'lessons';
    if (hash === 'materials/resources') return 'resources';
    if (hash === 'materials/classes') return 'classes';
    if (hash === 'materials/knowledge') return 'knowledge';
    return /^materials\/[\w-]+$/.test(hash) ? 'editor' : null;
  }
  function route() {
    const v = view();
    if (!v) return;
    $('course-all-page').hidden = v !== 'all';
    $('course-home-page').hidden = !tabs[v];
    for (const [key, [tab, panel]] of Object.entries(tabs)) {
      const on = key === v;
      $(tab).setAttribute('aria-selected', String(on)); $(tab).tabIndex = on ? 0 : -1;
      if (key !== 'lessons') $(panel).hidden = !on;
    }
    if (v === 'all') drawAll();
    if (tabs[v]) { drawHeader(); if (v === 'classes') drawClasses(); }
  }
  function meta(c) {
    return `${c.lesson_keys.length} 份备课 · ${c.classes.length} 个班级 · ${c.student_count} 名学生`;
  }
  function drawAll() {
    const now = scope.courseId();
    $('course-cards').replaceChildren(...scope.courses.map(c => {
      const card = el('button', undefined, 'student-card course-card' + (c.id === now ? ' is-current' : '')); card.type = 'button';
      card.append(el('span', c.stage || '未填写学段', 'scope-kicker'), el('strong', c.name), el('span', meta(c), 'helper'));
      if (c.description) card.append(el('p', c.description, 'evidence'));
      card.append(el('span', c.id === now ? '当前课程 · 进入 →' : '进入课程 →', 'student-card-action'));
      card.onclick = () => { scope.set(c.id, undefined, 'courses'); location.hash = 'materials'; };
      return card;
    }));
    if (!scope.courses.length) $('course-cards').append(el('p', '还没有课程，点击「新建课程」开始。', 'helper'));
    $('course-unassigned').textContent = scope.unassigned ? `另有 ${scope.unassigned} 名学生尚未加入任何班级，可在班级的「学生」中添加。` : '';
  }
  function drawHeader() {
    const c = scope.course();
    if (!c) { $('course-home-name').textContent = '尚无课程'; $('course-home-meta').textContent = ''; return; }
    $('course-home-stage').textContent = c.stage || '课程';
    $('course-home-name').textContent = c.name;
    $('course-home-meta').textContent = meta(c);
    $('course-home-description').textContent = c.description || '';
    $('course-home-description').hidden = !c.description;
    if (!$('edit-course-form').hidden) return;
    $('edit-course-name').value = c.name; $('edit-course-stage').value = c.stage || ''; $('edit-course-description').value = c.description || '';
  }
  function drawClasses() {
    const c = scope.course();
    $('course-classes-title').textContent = c ? `班级 · ${c.classes.length} 个` : '班级';
    $('course-class-cards').replaceChildren(...(c?.classes || []).map(k => {
      const card = el('button', undefined, 'student-card' + (k.id === scope.classId() ? ' is-current' : '')); card.type = 'button';
      card.append(el('strong', k.name), el('span', `${k.student_ids.length} 名学生`, 'helper'), el('p', k.profile || '尚未填写班级特点。', 'evidence'), el('span', '查看班级画像 →', 'student-card-action'));
      card.onclick = () => { scope.set(c.id, k.id, 'courses'); location.hash = 'profiles'; };
      return card;
    }));
    if (c && !c.classes.length) $('course-class-cards').append(el('p', '这门课程还没有班级。新建班级后添加学生，即可查看班级画像、生成班级讲义。', 'helper'));
  }
  function toggle(button, form, open) {
    form.hidden = !open; button.setAttribute('aria-expanded', String(open));
    if (open) form.querySelector('input')?.focus();
  }
  async function afterChange() {
    await scope.refresh();
    if (typeof refreshWorkspaceCatalog === 'function') await refreshWorkspaceCatalog();
  }
  $('toggle-new-course').onclick = () => toggle($('toggle-new-course'), $('new-course-form'), $('new-course-form').hidden);
  $('new-course-form').onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector('button[type=submit]'); if (button.disabled) return; button.disabled = true;
    try {
      const d = await api('/api/courses', json('POST', {name: $('new-course-name').value, stage: $('new-course-stage').value, description: $('new-course-description').value}));
      await scope.refresh(); scope.set(d.course.id, undefined, 'courses');
      $('new-course-form').reset(); toggle($('toggle-new-course'), $('new-course-form'), false);
      location.hash = 'materials';
      status('course-home-status', '课程已创建。接下来可以新建备课、上传课程资料，或在「班级」中新建班级。');
    } catch (e) { status('course-all-status', e.message, true); }
    finally { button.disabled = false; }
  };
  $('edit-course').onclick = () => {
    const form = $('edit-course-form');
    if (!form.hidden) { form.hidden = true; return; }
    drawHeader(); form.hidden = false; $('edit-course-name').focus();
  };
  $('cancel-course-edit').onclick = () => { $('edit-course-form').hidden = true; status('course-home-status', ''); };
  $('edit-course-form').onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector('button[type=submit]'); if (button.disabled) return; button.disabled = true;
    try {
      await api('/api/courses/' + scope.courseId(), json('PUT', {name: $('edit-course-name').value, stage: $('edit-course-stage').value, description: $('edit-course-description').value}));
      $('edit-course-form').hidden = true;
      await afterChange();
      status('course-home-status', '课程信息已保存，本课程的备课同步使用新名称。');
    } catch (e) { status('course-home-status', e.message, true); }
    finally { button.disabled = false; }
  };
  $('toggle-new-class').onclick = () => toggle($('toggle-new-class'), $('new-class-form'), $('new-class-form').hidden);
  $('new-class-form').onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector('button[type=submit]'); if (button.disabled) return; button.disabled = true;
    try {
      const d = await api(`/api/courses/${scope.courseId()}/classes`, json('POST', {name: $('new-class-name').value, profile: $('new-class-profile').value}));
      $('new-class-form').reset(); toggle($('toggle-new-class'), $('new-class-form'), false);
      await scope.refresh(); scope.set(scope.courseId(), d.class.id, 'courses');
      status('course-classes-status', `已创建「${d.class.name}」。`);
      location.hash = 'profiles/students';
    } catch (e) { status('course-classes-status', e.message, true); }
    finally { button.disabled = false; }
  };
  // Arrow keys move between the course tabs, like the records tabs.
  const order = Object.values(tabs);
  order.forEach(([tab], index) => $(tab).addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? order.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + order.length) % order.length;
    location.hash = order[next][2]; $(order[next][0]).focus();
  }));
  window.addEventListener('hashchange', route);
  window.addEventListener('scope-changed', () => { status('course-home-status', ''); $('edit-course-form').hidden = true; route(); });
  route();
})();
