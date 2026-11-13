/* 知识点库 (#materials/knowledge): the course's points grouped by chapter, the recognition drafts of
   its uploads, and the dialog where the teacher renames, merges, deletes and adds points before
   confirming them into the library. uploads.js shows each upload's recognition status through
   window.courseKnowledge; the lesson editor (course-manager.js) reads the library from it too. */
(() => {
  const scope = window.workspaceScope;
  if (!scope) return;
  const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const pending = d => d.status === 'queued' || d.status === 'running';
  // Same rule as classes.name_key: full- / half-width forms, spaces and case do not make a new point.
  const nameKey = name => name.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
  let library = {courseId: null, points: [], drafts: []}, loadVersion = 0, poll = null, editingPoint = null;

  async function load(courseId = scope.courseId()) {
    if (courseId == null) return {points: [], drafts: []};
    const version = ++loadVersion;
    const d = await api(`/api/courses/${courseId}/knowledge`);
    if (version === loadVersion && courseId === scope.courseId()) {
      library = {courseId, points: d.points, drafts: d.drafts};
      schedule();
    }
    return d;
  }
  // While an upload is being recognized, check again every few seconds.
  function schedule() {
    clearTimeout(poll); poll = null;
    if (library.drafts.some(pending)) poll = setTimeout(() => refresh().catch(() => {}), 4000);
  }
  async function refresh() {
    await load();
    window.dispatchEvent(new Event('knowledge-changed'));
  }
  const onTab = () => location.hash === '#materials/knowledge';
  function say(message, failed = false) {
    const id = onTab() ? 'knowledge-page-status' : 'upload-status';
    $(id).hidden = false; $(id).textContent = message || ''; $(id).dataset.error = String(failed);
    $(id).classList?.toggle('upload-failed', failed && id === 'upload-status');
  }
  const button = (label, action, cls = 'secondary') => { const b = el('button', label, cls); b.type = 'button'; b.onclick = action; return b; };

  async function recognize(uploadId) {
    try {
      await api('/api/knowledge/recognize', json('POST', {upload_id: uploadId}));
      say('正在识别知识点：先按章节标题切分资料，再由模型细分。完成后点「审核并确认」。');
      await refresh();
    } catch (e) { say(e.message, true); }
  }

  // One line under each upload in the course or class resources.
  function uploadStatus(item) {
    if (!item.characters || item.course_id == null) return null;
    const box = el('div', undefined, 'upload-knowledge');
    const draft = library.drafts.find(d => d.upload_id === item.id);
    if (!draft) box.append(el('span', '尚未识别知识点', 'knowledge-badge'), button('识别知识点', () => recognize(item.id)));
    else if (pending(draft)) box.append(el('span', '正在识别知识点…', 'knowledge-badge running'));
    // A draft from the heading rules alone (model unavailable) can be run again once the model is set up.
    else if (draft.status === 'ready') box.append(el('span', `识别出 ${draft.count} 个知识点，待确认`, 'knowledge-badge ready'), button('审核并确认', () => review(item.id), 'primary'), button('重新识别', () => recognize(item.id)));
    else if (draft.status === 'confirmed') box.append(el('span', `已确认 ${draft.count} 个知识点`, 'knowledge-badge done'), button('查看识别结果', () => review(item.id)), button('重新识别', () => recognize(item.id)));
    else box.append(el('span', '识别未完成：' + (draft.error || '请重试'), 'knowledge-badge failed'), button('重新识别', () => recognize(item.id)));
    return box;
  }

  // Review dialog. Rows keep the draft's unit ranges; merging joins a row into the one above it.
  let review_ = null;
  const dialog = $('knowledge-dialog');
  function rangeText(units, first, last) {
    const unit = units[0]?.label.endsWith('页') ? '页' : '段';
    return first === last ? `第 ${first} ${unit}` : `第 ${first}—${last} ${unit}`;
  }
  function mainChapter(items) {
    const counts = new Map();
    for (const item of items) counts.set(item.chapter || '', (counts.get(item.chapter || '') || 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || '';
  }
  function drawItems() {
    const {items, units, shelf} = review_, max = units.length;
    $('knowledge-dialog-items').replaceChildren(...items.map((item, index) => {
      const row = el('li', undefined, 'knowledge-item');
      const name = el('input'); name.value = item.name; name.maxLength = 40; name.required = true;
      name.setAttribute('aria-label', `第 ${index + 1} 条知识点名称`);
      const known = el('span', '库中已有：合并资料', 'knowledge-badge done'); known.hidden = !shelf.has(nameKey(item.name));
      name.oninput = () => { item.name = name.value; known.hidden = !shelf.has(nameKey(name.value)); summary(); };
      const range = el('span', undefined, 'knowledge-range');
      const bound = (value, label, apply) => {
        const input = el('input'); input.type = 'number'; input.min = 1; input.max = max; input.value = value;
        input.setAttribute('aria-label', `第 ${index + 1} 条${label}`);
        input.onchange = () => { const n = Math.max(1, Math.min(max, Math.round(Number(input.value)) || 1)); apply(n); drawItems(); };
        return input;
      };
      const unit = units[0]?.label.endsWith('页') ? '页' : '段';
      range.append(el('span', '第'), bound(item.units[0], '起始' + unit, n => { item.units = [n, Math.max(n, item.units[1])]; }),
        el('span', '—'), bound(item.units[1], '结束' + unit, n => { item.units = [Math.min(n, item.units[0]), n]; }), el('span', unit));
      const first = units[item.units[0] - 1];
      range.title = first ? `${first.label}：${first.preview}` : '';
      const note = el('input'); note.value = item.summary || ''; note.maxLength = 200; note.placeholder = '一句话概括（选填）';
      note.setAttribute('aria-label', `第 ${index + 1} 条概括`); note.oninput = () => { item.summary = note.value; };
      const head = el('div', undefined, 'knowledge-item-head'); head.append(el('span', String(index + 1), 'knowledge-index'), name, known);
      const tail = el('div', undefined, 'knowledge-item-tail');
      const merge = button('并入上一条', () => {
        const above = items[index - 1];
        above.units = [Math.min(above.units[0], item.units[0]), Math.max(above.units[1], item.units[1])];
        above.summary = [above.summary, item.summary].filter(Boolean).join('；').slice(0, 200);
        items.splice(index, 1); drawItems();
      });
      merge.disabled = index === 0;
      const remove = button('删除', () => { items.splice(index, 1); drawItems(); });
      remove.setAttribute('aria-label', `删除第 ${index + 1} 条「${item.name}」`);
      tail.append(range, merge, remove);
      row.append(head, note, tail);
      return row;
    }));
    if (!items.length) $('knowledge-dialog-items').append(el('li', '没有保留的知识点。可点「新增一条」，或关闭窗口稍后再确认。', 'helper'));
    summary();
  }
  function summary() {
    const {items, shelf} = review_;
    const reused = items.filter(i => shelf.has(nameKey(i.name))).length;
    $('knowledge-dialog-summary').textContent = `共 ${items.length} 条：新增 ${items.length - reused}，合并到已有 ${reused}`;
    $('knowledge-dialog-submit').disabled = !items.length;
  }
  async function review(uploadId) {
    $('knowledge-dialog-status').textContent = '';
    try {
      const d = await api('/api/knowledge/drafts/' + uploadId);
      const items = d.draft.items.map(i => ({name: i.name, chapter: i.chapter || '', section: i.section || '', summary: i.summary || '', units: [...i.units]}));
      review_ = {uploadId, version: d.draft.version, units: d.units, items, shelf: new Set(d.library.map(nameKey)), chapter: mainChapter(items)};
      $('knowledge-dialog-title').textContent = (d.draft.status === 'confirmed' ? '知识点识别结果 · ' : '确认知识点 · ') + (d.upload.title || '资料');
      const method = d.draft.method === 'rules' ? '只按资料中的编号标题切分' : '按编号标题切分资料，再由模型细分';
      $('knowledge-dialog-meta').textContent = `${method} · 资料共 ${d.units.length} ${d.units[0]?.label.endsWith('页') ? '页' : '段'} · ${d.draft.status === 'confirmed' ? '已确认，可修改后再次确认' : '确认后才会加入知识点库'}`;
      $('knowledge-dialog-notice').hidden = !d.draft.notice; $('knowledge-dialog-notice').textContent = d.draft.notice || '';
      $('knowledge-dialog-chapter').value = review_.chapter;
      $('knowledge-dialog-submit').textContent = d.draft.status === 'confirmed' ? '再次确认' : '确认加入知识点库';
      drawItems();
      if (!dialog.open) dialog.showModal();
    } catch (e) { say(e.message, true); }
  }
  $('knowledge-dialog-add').onclick = () => {
    if (!review_) return;
    review_.items.push({name: '', chapter: review_.chapter, section: '', summary: '', units: [1, review_.units.length]});
    drawItems();
    const inputs = $('knowledge-dialog-items').querySelectorAll('.knowledge-item-head input');
    inputs[inputs.length - 1]?.focus();
  };
  $('knowledge-dialog-reset').onclick = () => { if (review_) review(review_.uploadId); };
  $('knowledge-dialog-close').onclick = () => dialog.close();
  $('knowledge-dialog-form').onsubmit = async event => {
    event.preventDefault();
    const submit = $('knowledge-dialog-submit'); if (submit.disabled || !review_) return;
    const chapter = $('knowledge-dialog-chapter').value.trim();
    const items = review_.items.map(i => ({name: i.name.trim(), section: i.section, summary: i.summary.trim(), units: i.units,
      chapter: (i.chapter || '') === review_.chapter ? chapter : i.chapter}));
    const names = items.map(i => i.name);
    if (names.some(n => !n)) { $('knowledge-dialog-status').textContent = '请为每条知识点填写名称，或删除空行。'; return; }
    const repeated = names.find((n, i) => names.indexOf(n) !== i);
    if (repeated) { $('knowledge-dialog-status').textContent = `「${repeated}」重复了，请合并或改名。`; return; }
    submit.disabled = true; $('knowledge-dialog-status').textContent = '正在加入知识点库…';
    try {
      const d = await api(`/api/knowledge/drafts/${review_.uploadId}/confirm`, json('POST', {items, version: review_.version}));
      dialog.close();
      say(`已加入知识点库：新增 ${d.created} 个，合并到已有 ${d.reused} 个。备课勾选这些知识点后，会自动引用对应的资料片段。`);
      await refresh();
    } catch (e) { $('knowledge-dialog-status').textContent = e.message; }
    finally { submit.disabled = false; }
  };

  // The library tab.
  function sourceLink(m) {
    const a = el('a', `${m.section || m.source_title} ↗`, 'knowledge-source');
    a.href = sourceHref(m.source_url); a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  }
  function pointRow(p) {
    const row = el('div', undefined, 'knowledge-point' + (p.archived ? ' is-archived' : ''));
    if (editingPoint === p.id) {
      const form = el('form', undefined, 'editor-form knowledge-point-edit');
      const field = (label, value, max, list) => {
        const wrap = el('label', label); const input = el('input'); input.value = value; input.maxLength = max;
        if (list) input.setAttribute('list', list);
        wrap.append(input); form.append(wrap); return input;
      };
      const name = field('名称', p.name, 40), chapter = field('章节', p.chapter, 100, 'knowledge-chapters'), note = field('概括', p.summary, 200);
      const actions = el('div', undefined, 'trace-actions');
      const save = el('button', '保存', 'primary'); save.type = 'submit';
      actions.append(save, button('取消', () => { editingPoint = null; drawLibrary(); }));
      form.append(actions);
      form.onsubmit = async event => {
        event.preventDefault(); save.disabled = true;
        try {
          await api('/api/knowledge/' + p.id, json('PUT', {name: name.value, chapter: chapter.value, summary: note.value}));
          editingPoint = null; say(name.value.trim() !== p.name ? '知识点已改名，使用它的备课同步显示新名称。' : '知识点已保存。');
          await refresh();
        } catch (e) { say(e.message, true); save.disabled = false; }
      };
      row.append(form);
      setTimeout(() => name.focus?.(), 0);
      return row;
    }
    const main = el('div', undefined, 'knowledge-point-main');
    main.append(el('strong', p.name));
    if (p.summary) main.append(el('p', p.summary, 'helper'));
    const facts = [p.sources.length ? `资料 ${p.sources.length} 段` : '尚无资料片段', p.lessons.length ? `${p.lessons.length} 份备课使用` : '未用于备课', p.diagnosed ? `${p.diagnosed} 名学生已诊断` : ''];
    main.append(el('p', facts.filter(Boolean).join(' · '), 'knowledge-point-meta'));
    if (p.sources.length) { const list = el('div', undefined, 'knowledge-sources'); list.append(...p.sources.map(sourceLink)); main.append(list); }
    const actions = el('div', undefined, 'knowledge-point-actions');
    actions.append(button('编辑', () => { editingPoint = p.id; drawLibrary(); }));
    actions.append(button(p.archived ? '恢复' : '归档', async () => {
      try {
        await api('/api/knowledge/' + p.id, json('PUT', {archived: !p.archived}));
        say(p.archived ? `「${p.name}」已恢复。` : `「${p.name}」已归档：不再出现在备课的勾选列表中，已使用它的备课和诊断记录不受影响。`);
        await refresh();
      } catch (e) { say(e.message, true); }
    }));
    row.append(main, actions);
    return row;
  }
  function drawLibrary() {
    const course = scope.course();
    if (!course || library.courseId !== course.id) return;
    const visible = library.points.filter(p => !p.archived), archived = library.points.filter(p => p.archived);
    $('knowledge-library-title').textContent = `知识点库 · ${visible.length} 个`;
    const chapters = new Map();
    for (const p of visible) { const key = p.chapter || '未分章节'; if (!chapters.has(key)) chapters.set(key, []); chapters.get(key).push(p); }
    $('knowledge-groups').replaceChildren(...[...chapters.entries()].map(([chapter, points]) => {
      const box = el('details', undefined, 'knowledge-chapter'); box.open = true;
      const head = el('summary'); head.append(el('strong', chapter), el('span', `${points.length} 个`, 'count'));
      box.append(head, ...points.map(pointRow));
      return box;
    }));
    if (!visible.length) $('knowledge-groups').append(el('p', '知识点库还是空的。在「课程资料」上传教材或讲义后会自动识别知识点，确认后出现在这里；也可以点「新增知识点」手动添加。', 'helper'));
    $('knowledge-archived-box').hidden = !archived.length;
    $('knowledge-archived-title').textContent = `已归档 · ${archived.length} 个`;
    $('knowledge-archived').replaceChildren(...archived.map(pointRow));
    $('knowledge-chapters').replaceChildren(...[...new Set(library.points.map(p => p.chapter).filter(Boolean))].map(c => { const o = el('option'); o.value = c; return o; }));
    const waiting = library.drafts.filter(d => d.status !== 'confirmed');
    $('knowledge-drafts').replaceChildren(...waiting.map(d => {
      const row = el('div', undefined, 'knowledge-draft');
      const text = pending(d) ? `「${d.title}」正在识别知识点…` : d.status === 'ready' ? `「${d.title}」识别出 ${d.count} 个知识点，待确认` : `「${d.title}」识别未完成：${d.error}`;
      row.append(el('span', text));
      if (d.status === 'ready') row.append(button('审核并确认', () => review(d.upload_id), 'primary'));
      if (d.status === 'failed') row.append(button('重新识别', () => recognize(d.upload_id)));
      return row;
    }));
  }
  $('toggle-new-point').onclick = () => {
    const open = $('new-point-form').hidden;
    $('new-point-form').hidden = !open; $('toggle-new-point').setAttribute('aria-expanded', String(open));
    if (open) $('new-point-name').focus();
  };
  $('new-point-form').onsubmit = async event => {
    event.preventDefault();
    const submit = event.target.querySelector('button[type=submit]'); if (submit.disabled) return; submit.disabled = true;
    try {
      const d = await api(`/api/courses/${scope.courseId()}/knowledge`, json('POST', {name: $('new-point-name').value, chapter: $('new-point-chapter').value, summary: $('new-point-summary').value}));
      $('new-point-form').reset(); $('new-point-form').hidden = true; $('toggle-new-point').setAttribute('aria-expanded', 'false');
      say(`已加入「${d.point.name}」。可在资料行用「收录到备课」补充资料，或直接在备课中勾选。`);
      await refresh();
    } catch (e) { say(e.message, true); }
    finally { submit.disabled = false; }
  };

  window.courseKnowledge = {
    load, refresh, uploadStatus, recognize, review,
    points: () => library.courseId === scope.courseId() ? library.points : [],
  };
  window.addEventListener('knowledge-changed', drawLibrary);
  window.addEventListener('hashchange', () => { if (onTab()) refresh().catch(e => say(e.message, true)); });
  window.addEventListener('scope-changed', event => {
    if (event.detail === 'refresh') return;
    editingPoint = null; library = {courseId: null, points: [], drafts: []};
    refresh().catch(() => {});
  });
  scope.ready.then(() => refresh()).catch(() => {});
})();
