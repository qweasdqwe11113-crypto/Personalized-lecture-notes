/* 班级与画像 page for the current class: class profile (#profiles), students (#profiles/students),
   class resources (#profiles/resources). Student pages (#profiles/new|import|<id>) sit beside it;
   student-roster.js and roster-import.js fill them, this file decides what is visible. */
(() => {
  const scope = window.workspaceScope;
  const order = ['needs_support', 'partial', 'mastered', 'unknown'];
  const labels = {needs_support: '需要补充', partial: '部分掌握', mastered: '已掌握', unknown: '未诊断'};
  const tabs = {profile: ['class-tab-profile', 'class-panel-profile', 'profiles'], students: ['class-tab-students', 'class-panel-students', 'profiles/students'], resources: ['class-tab-resources', 'class-panel-resources', 'profiles/resources']};
  let version = 0, loaded = null, recorder = '', scoped = false;
  const status = (message, failed = false) => { $('class-edit-status').textContent = message || ''; $('class-edit-status').dataset.error = String(failed); };
  function view() {
    const hash = location.hash.slice(1);
    if (hash === 'profiles') return 'profile';
    const m = /^profiles\/(students|resources|new|import|[1-9]\d*)$/.exec(hash);
    return !m ? null : /^\d/.test(m[1]) ? 'detail' : m[1];
  }
  function route() {
    const v = view();
    if (!v) return;
    const cls = scope.klass(), needsClass = v !== 'detail';
    // Until the course list has loaded, an empty list does not yet mean "no course".
    $('class-empty').hidden = !needsClass || Boolean(cls) || !scoped;
    $('class-home-page').hidden = !tabs[v] || !cls;
    $('student-create-page').hidden = v !== 'new' || !cls;
    $('student-import-page').hidden = v !== 'import' || !cls;
    $('student-detail-page').hidden = v !== 'detail';
    for (const [key, [tab, panel]] of Object.entries(tabs)) {
      const on = key === v;
      $(tab).setAttribute('aria-selected', String(on)); $(tab).tabIndex = on ? 0 : -1; $(panel).hidden = !on;
    }
    if (!cls) {
      const c = scope.course();
      $('class-empty-title').textContent = c ? `「${c.name}」还没有班级` : '还没有课程';
      $('class-empty-text').textContent = c ? '先在课程中新建班级，再添加学生；班级画像由班内学生的诊断汇总而成。'
        : '班级和学生都属于某门课程。先新建一门课程，再在课程里建班级、添加学生。';
      $('class-empty-action').textContent = c ? '前往新建班级' : '前往新建课程';
      $('class-empty-action').href = c ? '#materials/classes' : '#courses';
      return;
    }
    $('student-create-heading').textContent = `新建学生 · 加入${cls.name}`;
    $('student-import-heading').textContent = `批量导入 · ${cls.name}`;
    drawHeader(loaded?.class?.id === cls.id ? loaded.class : cls);
    if (v === 'profile') load();
  }
  function drawHeader(k) {
    const c = scope.course(), count = scope.klass()?.student_ids.length ?? 0;
    $('class-home-course').textContent = c ? scope.courseLabel(c) : '课程';
    $('class-home-name').textContent = k.name;
    $('class-home-meta').textContent = `${count} 名学生` + (k.profile_revision ? ` · 班级画像第 ${k.profile_revision} 版` : '');
    $('class-home-profile').textContent = k.profile || '尚未填写班级特点。班级讲义会参考这里的整体学情与课堂特点。';
    $('class-start-lecture').disabled = !count || busy;
  }
  async function load() {
    const cls = scope.klass(); if (!cls) return;
    const token = ++version, topic = $('class-profile-topic').value;
    $('class-profile-points').replaceChildren(el('p', '正在汇总班内诊断…', 'helper'));
    try {
      const d = await api(`/api/classes/${cls.id}/profile` + (topic ? '?topic=' + encodeURIComponent(topic) : ''));
      if (token !== version) return;
      draw(d);
    } catch (e) {
      if (token !== version) return;
      // A lesson removed or moved since the filter was drawn: fall back to the whole course.
      if (topic) { $('class-profile-topic').value = ''; return load(); }
      $('class-profile-points').replaceChildren(el('p', e.message, 'helper'));
    }
  }
  function bar(counts, total) {
    const box = el('div', undefined, 'class-bar'); box.setAttribute('role', 'img');
    box.setAttribute('aria-label', order.filter(s => counts[s]).map(s => `${labels[s]} ${counts[s]} 人`).join('，') || '暂无学生');
    for (const s of order) if (counts[s]) { const seg = el('span', String(counts[s]), 'class-bar-seg ' + s); seg.style.flexGrow = String(counts[s]); seg.title = `${labels[s]} ${counts[s]} / ${total}`; box.append(seg); }
    return box;
  }
  const studentLink = s => { const a = el('a', s.name); a.href = '#profiles/' + s.id; return a; };
  function draw(d) {
    loaded = d;
    const topic = $('class-profile-topic').value;
    $('class-profile-topic').replaceChildren(...[['', '本课程全部备课'], ...d.lessons.map(l => [l.key, l.title])].map(([v, t]) => { const o = el('option', t); o.value = v; return o; }));
    if (d.lessons.some(l => l.key === topic)) $('class-profile-topic').value = topic;
    drawHeader(d.class);
    const total = d.students.length;
    $('class-profile-summary').textContent = !total ? '班级还没有学生。' : !d.points.length ? `班内 ${total} 名学生；本课程还没有含知识点的备课。` :
      `班内 ${total} 名学生 · ${d.points.length} 个知识点 · 已有诊断 ${d.coverage.diagnosed} / ${d.coverage.cells} 项`;
    $('class-profile-points').replaceChildren(...d.points.map(p => {
      const row = el('details', undefined, 'class-point');
      const head = el('summary'); const title = el('div', undefined, 'class-point-title');
      title.append(el('strong', p.name), el('span', p.label, 'badge ' + p.state));
      head.append(title, bar(p.counts, total), el('span', p.summary, 'helper class-point-summary'));
      const groups = el('div', undefined, 'class-point-groups');
      for (const s of order) if (p.students[s].length) {
        const line = el('p'); line.append(el('span', labels[s] + '：', 'legend ' + s));
        p.students[s].forEach((st, i) => { if (i) line.append('、'); line.append(studentLink(st)); });
        groups.append(line);
      }
      row.append(head, groups); return row;
    }));
    if (!d.points.length) $('class-profile-points').append(el('p', total ? '在「课程 → 备课」中为本课程新建含知识点的备课后，这里会显示班级分布。' : '先在「学生」中添加学生。', 'helper'));
    $('class-profile-rule').textContent = d.rule;
    $('class-focus-list').replaceChildren(...d.focus_students.map(s => {
      const item = el('div', undefined, 'class-focus-item'); item.append(studentLink(s), el('span', s.points.join('、'), 'helper')); return item;
    }));
    if (!d.focus_students.length) $('class-focus-list').append(el('p', total ? '目前没有学生在这些知识点上被标记为「需要补充」。' : '暂无。', 'helper'));
    $('class-history').replaceChildren(...d.history.map(h => {
      const item = el('div', undefined, 'class-history-item');
      item.append(el('strong', `${new Date(h.created_at).toLocaleString('zh-CN')} · ${h.recorder}`), el('p', '理由：' + h.reason, 'helper'));
      if (h.previous_snapshot.name !== h.new_snapshot.name) item.append(el('p', `名称：${h.previous_snapshot.name} → ${h.new_snapshot.name}`));
      if (h.previous_snapshot.profile !== h.new_snapshot.profile) item.append(el('p', `班级特点：${h.previous_snapshot.profile || '（空）'} → ${h.new_snapshot.profile || '（空）'}`, 'evidence'));
      return item;
    }));
    if (!d.history.length) $('class-history').append(el('p', '尚无修改记录。', 'helper'));
  }
  $('class-profile-topic').onchange = load;
  $('edit-class').onclick = () => {
    const form = $('edit-class-form');
    if (!form.hidden) { form.hidden = true; return; }
    const k = loaded?.class?.id === scope.classId() ? loaded.class : scope.klass(); if (!k) return;
    $('edit-class-name').value = k.name; $('edit-class-profile').value = k.profile || '';
    $('edit-class-recorder').value = recorder; $('edit-class-reason').value = '';
    form.hidden = false; status(''); $('edit-class-profile').focus();
  };
  $('cancel-class-edit').onclick = () => { $('edit-class-form').hidden = true; status(''); };
  $('edit-class-form').onsubmit = async event => {
    event.preventDefault();
    const button = $('save-class'), k = loaded?.class?.id === scope.classId() ? loaded.class : scope.klass();
    if (button.disabled || !k) return; button.disabled = true;
    try {
      const d = await api('/api/classes/' + k.id, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        name: $('edit-class-name').value, profile: $('edit-class-profile').value, recorder: $('edit-class-recorder').value,
        reason: $('edit-class-reason').value, expected_revision: k.profile_revision})});
      recorder = $('edit-class-recorder').value;
      $('edit-class-form').hidden = true; draw(d);
      await scope.refresh();
      status('班级画像已保存，原内容保留在修改历史中。');
    } catch (e) { status(e.status === 409 ? e.message + ' 可取消后重新打开编辑。' : e.message, true); if (e.status === 409) loaded = null; }
    finally { button.disabled = false; }
  };
  $('class-start-lecture').onclick = () => {
    const k = scope.klass(); if (!k) return;
    if (!window.prepareLectureFor('class:' + k.id)) status(busy ? '讲义正在生成，请完成后再切换。' : '本课程还没有备课，请先在「课程 → 备课」新建或复制示例。', true);
  };
  const keys = Object.values(tabs);
  keys.forEach(([tab], index) => $(tab).addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? keys.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + keys.length) % keys.length;
    location.hash = keys[next][2]; $(keys[next][0]).focus();
  }));
  window.addEventListener('lecture-version-changed', () => { if (scope.klass()) $('class-start-lecture').disabled = busy || !scope.klass().student_ids.length; });
  window.addEventListener('hashchange', route);
  window.addEventListener('scope-changed', event => {
    scoped = true;
    if (event.detail !== 'refresh') { $('edit-class-form').hidden = true; status(''); $('class-profile-topic').value = ''; }
    route();
  });
  window.refreshClassProfile = () => { if (view() === 'profile') load(); };
  route();
})();
