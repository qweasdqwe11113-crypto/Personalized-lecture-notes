"use strict";
// Grading → profile evidence. Drafts are only text; each write is one teacher-confirmed evidence record.
(() => {
  let record = null, drafts = null, catalog = null, states = {}, token = 0;
  const option = (value, label) => { const node = el('option', label); node.value = value; return node; };
  const status = text => { $('grading-evidence-status').textContent = text || ''; };
  const localDate = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  function observedAt() {
    const value = $('grading-evidence-date').value;
    if (!value) return null;
    // A date alone means midday of that day; today's date never lands in the future.
    const midday = new Date(value + 'T12:00:00'), now = new Date();
    return (midday > now ? now : midday).toISOString();
  }
  async function ensureCatalog() {
    if (!catalog) catalog = await api('/api/course-tasks');
    return catalog;
  }
  function pointName(id) {
    return catalog.knowledge_points.find(p => p.id === id)?.name || '知识点 ' + id;
  }
  async function show(row) {
    record = row;
    const ready = row.status === 'completed' && row.result;
    $('grading-evidence').hidden = !ready;
    if (!ready) return;
    const current = ++token;
    status('正在整理证据草稿…');
    const [data] = await Promise.all([api(`/api/grading/${row.id}/evidence-drafts`), ensureCatalog()]);
    if (current !== token) return;
    drafts = data;
    const topicSelect = $('grading-evidence-topic'), studentSelect = $('grading-evidence-student');
    topicSelect.replaceChildren(...Object.entries(catalog.topics).map(([key, t]) =>
      option(key, (t.custom ? '' : '示例 · ') + [t.stage, t.course, t.title].filter(Boolean).join(' / '))));
    if (data.quiz && catalog.topics[data.quiz.topic_key]) topicSelect.value = data.quiz.topic_key;
    topicSelect.disabled = Boolean(data.quiz);
    const previous = studentSelect.value;
    studentSelect.replaceChildren(option('', '请选择学生'), ...catalog.students.map(s => option(s.id, `${s.name} · ${s.subtitle}`)));
    if (catalog.students.some(s => String(s.id) === previous)) studentSelect.value = previous;
    $('grading-evidence-date').value = data.observed_on || localDate(new Date());
    $('grading-evidence-date').max = localDate(new Date());
    await draw();
  }
  function card(item, sid) {
    const box = el('article', undefined, 'evidence-draft');
    const title = item.knowledge_id ? `知识点：${item.knowledge_name || pointName(item.knowledge_id)}` : '未对应题组知识点的题目';
    box.append(el('h3', title), el('p', `第 ${item.question_numbers.join('、')} 题 · AI 建议得分 ${item.score} / ${item.max_score}` +
      (item.pending ? ` · ${item.pending} 题待核对未计分` : ''), 'helper'));
    let pointSelect = null;
    if (!item.knowledge_id) {
      const topic = catalog.topics[$('grading-evidence-topic').value];
      pointSelect = el('select');
      pointSelect.append(option('', '请选择知识点'), ...(topic?.knowledge_ids || []).map(id => option(id, pointName(id))));
      const label = el('label', '对应知识点'); box.append(label, pointSelect);
    }
    const stateLine = el('p', '', 'helper');
    const describe = () => {
      const id = item.knowledge_id || Number(pointSelect.value);
      const s = states[id];
      stateLine.textContent = s ? `当前掌握状态：${s.label}（${s.evidence || '没有诊断记录'}）` : '选择知识点后显示当前状态。';
    };
    const task = el('textarea'); task.rows = 2; task.maxLength = 1200; task.value = item.task_content;
    const performance = el('textarea'); performance.rows = 5; performance.maxLength = 2000; performance.value = item.performance;
    const decision = el('select'); decision.append(option('retain', '保留原判断，只记录证据'), option('update', '更新掌握状态'));
    const state = el('select'); state.append(option('needs_support', '需要补充'), option('partial', '部分掌握'), option('mastered', '已掌握'), option('unknown', '未知'));
    state.disabled = true;
    decision.onchange = () => { state.disabled = decision.value !== 'update'; };
    const reason = el('input'); reason.maxLength = 600; reason.placeholder = '例如：单次测验，结合作业表现后再判断。';
    const row = el('div', undefined, 'evidence-draft-row');
    for (const [label, input] of [['判断', decision], ['新状态', state], ['确认理由', reason]]) {
      const wrap = el('div'); wrap.append(el('label', label), input); row.append(wrap);
    }
    const save = el('button', '确认记录', 'primary'); save.type = 'button';
    const result = el('span', '', 'helper');
    const actions = el('div', undefined, 'trace-actions'); actions.append(save, result);
    box.append(stateLine, el('label', '任务或观察内容'), task, el('label', '学生表现（可修改）'), performance, row, actions);
    if (pointSelect) pointSelect.onchange = async () => {
      const id = Number(pointSelect.value);
      if (id && !states[id]) await loadStates([id]).catch(e => status(e.message));
      describe();
    };
    describe();
    save.onclick = async () => {
      const id = item.knowledge_id || Number(pointSelect.value), current = states[id];
      if (!id || !current) { result.textContent = '请先选择知识点。'; return; }
      if (!$('grading-evidence-recorder').value.trim()) { result.textContent = '请填写录入者。'; return; }
      if (!observedAt()) { result.textContent = '请选择观察日期。'; return; }
      save.disabled = true; result.textContent = '正在记录…';
      try {
        await api(`/api/students/${sid}/evidence`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          knowledge_id: id, expected_revision: current.revision, source: $('grading-evidence-source').value,
          task_content: task.value, performance: performance.value, observed_at: observedAt(),
          recorder: $('grading-evidence-recorder').value, decision: decision.value,
          new_state: decision.value === 'update' ? state.value : null, reason: reason.value, confirmed: true})});
        // Later drafts for the same diagnosed point must build on the version this record created.
        for (const s of Object.values(states)) if (s.canonical_id === current.canonical_id) {
          s.revision += 1; if (decision.value === 'update') { s.state = state.value; s.label = state.selectedOptions[0].textContent; s.evidence = performance.value; }
        }
        box.classList.add('saved'); result.textContent = '已记入学生画像。';
        for (const input of box.querySelectorAll('textarea,select,input,button')) input.disabled = true;
      } catch (e) {
        result.textContent = e.status === 409 ? '该知识点的画像已被更新，请刷新草稿后重新核对。' : e.message;
        save.disabled = false;
      }
    };
    return box;
  }
  async function loadStates(ids) {
    const sid = $('grading-evidence-student').value;
    if (!sid || !ids.length) return;
    const data = await api(`/api/students/${sid}/knowledge-states?ids=${ids.join(',')}`);
    for (const s of data.states) states[s.id] = s;
  }
  async function draw() {
    const container = $('grading-evidence-drafts'), sid = $('grading-evidence-student').value, current = ++token;
    container.replaceChildren(); states = {};
    if (!drafts) return;
    if (!sid) { status('先选择这份作答对应的学生，再逐条核对证据草稿。'); return; }
    const items = [...drafts.groups, ...(drafts.unmapped ? [{...drafts.unmapped, knowledge_id: null}] : [])];
    await loadStates(drafts.groups.map(g => g.knowledge_id));
    if (current !== token) return;
    container.append(...items.map(item => card(item, sid)));
    status(drafts.quiz ? `已按题组「${drafts.quiz.title}」的知识点分组。${drafts.note}` : `未使用系统题组，请为题目选择对应知识点。${drafts.note}`);
  }
  window.addEventListener('grading-record-shown', event => {
    if (record && event.detail.id === record.id && event.detail.status === record.status && drafts) return;
    show(event.detail).catch(e => status(e.message));
  });
  $('grading-evidence-student').onchange = () => draw().catch(e => status(e.message));
  $('grading-evidence-topic').onchange = () => draw().catch(e => status(e.message));
})();
