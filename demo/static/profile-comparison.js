// Read-only comparison: original input versus one consistent current DB snapshot.
(() => {
  const dialog = $('profile-comparison-dialog');
  const content = $('profile-comparison-content');
  let lectureId = null, session = 0, data = null;
  const fields = {state: '状态变化', evidence: '依据变化', strategy: '策略变化'};

  function date(value) {
    if (!value) return '未记录';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '未记录' : parsed.toLocaleString('zh-CN');
  }
  function paragraph(label, value) {
    return el('p', `${label}：${value ?? '旧记录未提供'}`, 'comparison-text');
  }
  function side(title) {
    const section = el('section', undefined, 'comparison-side');
    section.append(el('h4', title));
    return section;
  }
  function drawStudent() {
    const before = side('生成时'), after = side('当前');
    const historical = data.historical_student, currentStudent = data.current_student;
    if (historical) {
      before.append(paragraph('学生说明', historical.subtitle), paragraph('学习特点', historical.profile),
        paragraph('说明版本', historical.profile_revision));
    } else before.append(el('p', '本次无画像对照，未向模型提供学生说明或诊断。', 'helper'));
    after.append(paragraph('学生说明', currentStudent.subtitle), paragraph('学习特点', currentStudent.profile),
      paragraph('说明版本', currentStudent.profile_revision));
    $('profile-comparison-student').replaceChildren(before, after);
    const labels = {subtitle: '学生说明', profile: '学习特点'};
    $('profile-comparison-student-changes').textContent = data.profile_changes.length
      ? data.profile_changes.map(f => labels[f]).join('、') + '已改变。'
      : historical ? '已留存的学生说明与学习特点没有变化；缺失字段不作判断。' : '当时未使用画像，不能与当前画像判定相同或不同。';
  }
  function evidenceCard(evidence) {
    const card = el('details', undefined, 'history-card');
    const decision = evidence.decision === 'retain' ? '保留原判断，待复核'
      : evidence.decision === 'baseline' ? '原有画像留存' : '教师确认';
    card.append(el('summary', `${data.source_labels[evidence.source] || '证据'} · ${decision}`),
      paragraph('任务', evidence.task_content), paragraph('学生表现', evidence.performance),
      paragraph('确认理由', evidence.reason), paragraph('录入者', evidence.recorder),
      paragraph('观察时间', date(evidence.observed_at)), paragraph('录入时间', date(evidence.created_at)));
    if (evidence.previous_state && evidence.confirmed_state)
      card.append(paragraph('状态判断', `${data.state_labels[evidence.previous_state]} → ${data.state_labels[evidence.confirmed_state]}`));
    if (Number.isInteger(evidence.previous_revision) && Number.isInteger(evidence.new_revision))
      card.append(paragraph('记录版本', `${evidence.previous_revision} → ${evidence.new_revision}`));
    return card;
  }
  function stateSide(title, state, historical) {
    const box = side(title);
    if (!state) {
      box.append(el('p', historical ? data.personalization_mode === 'none'
        ? '无画像模式未向模型提供该知识点画像。' : '旧讲义未保存该知识点画像。'
        : '当前未找到该知识点。', 'helper'));
      return box;
    }
    box.append(el('span', state.label || data.state_labels[state.state] || '未记录', 'badge ' + (state.state || 'unknown')),
      paragraph('诊断依据', state.evidence), paragraph('讲解策略', state.strategy),
      paragraph('记录版本', state.revision));
    if (state.updated_at) box.append(paragraph('最近确认', date(state.updated_at)));
    const evidence = state.recent_evidence;
    if (Array.isArray(evidence)) {
      const recent = el('details', undefined, 'comparison-evidence');
      recent.append(el('summary', `当时留存的证据 · ${evidence.length} 条`));
      if (!historical) recent.querySelector('summary').textContent = `当前最近证据 · ${evidence.length} 条`;
      recent.append(...evidence.map(evidenceCard));
      if (!evidence.length) recent.append(el('p', '没有已留存的证据。', 'helper'));
      box.append(recent);
    } else box.append(el('p', '旧记录未留存独立证据明细；现有诊断摘要按原样展示。', 'helper'));
    return box;
  }
  function drawPoints() {
    const onlyChanges = $('profile-comparison-filter').value === 'changed';
    const points = data.points.filter(p => !onlyChanges || p.changed);
    const summary = data.summary;
    $('profile-comparison-summary').textContent = `共 ${data.points.length} 个知识点，${summary.changed_points} 个有已记录变化；`
      + `状态 ${summary.state_changes}、依据 ${summary.evidence_changes}、策略 ${summary.strategy_changes} 项。`
      + (summary.incomplete_points ? ` ${summary.incomplete_points} 个历史信息不完整，请在“全部知识点”中核对。` : '');
    const cards = points.map(point => {
      const card = el('article', undefined, 'comparison-card');
      const heading = el('div', undefined, 'comparison-card-heading');
      const badges = el('div', undefined, 'comparison-badges');
      heading.append(el('h4', point.name), badges);
      for (const field of point.changes) badges.append(el('span', fields[field], 'badge comparison-changed'));
      if (point.new_evidence_count) badges.append(el('span', `新增证据 ${point.new_evidence_count} 条`, 'badge comparison-changed'));
      if (point.missing_historical_fields.length) badges.append(el('span', data.personalization_mode === 'none'
        ? '当时未使用画像' : '历史信息不完整', 'badge'));
      if (!point.changed && !point.missing_historical_fields.length) badges.append(el('span', '未变化', 'badge'));
      const pair = el('div', undefined, 'comparison-pair');
      pair.append(stateSide('生成时', point.historical, true), stateSide('当前', point.current, false));
      card.append(heading, pair);
      if (point.new_evidence_count === null && point.historical)
        card.append(el('p', '旧记录未提供有效版本，无法精确统计生成后的新增证据；当前最近证据另列。', 'helper'));
      if (point.new_evidence_count) {
        const added = el('div', undefined, 'comparison-added');
        added.append(el('p', `生成后新增 ${point.new_evidence_count} 条证据，展示最近 ${point.new_evidence.length} 条。保留原判断也会留存证据，不自动改变讲解策略。`, 'helper'), ...point.new_evidence.map(evidenceCard));
        card.append(added);
      }
      return card;
    });
    $('profile-comparison-points').replaceChildren(...cards);
    if (!cards.length) $('profile-comparison-points').append(el('p', onlyChanges
      ? '没有可判定的已记录变化。历史缺失信息可切换“全部知识点”查看。' : '这份旧讲义没有留存知识点范围，无法补造历史对照。', 'current-summary'));
  }
  async function load() {
    const token = ++session, id = lectureId;
    data = null;
    $('reload-profile-comparison').disabled = true;
    $('profile-comparison-filter').disabled = true;
    content.hidden = true;
    $('profile-comparison-error').hidden = true;
    $('profile-comparison-status').textContent = '正在读取生成时快照与当前画像…';
    try {
      const result = await api(`/api/lectures/${id}/profile-comparison`);
      if (token !== session || !dialog.open || id !== lectureId) return;
      data = result;
      $('profile-comparison-title').textContent = `${data.current_student.name} · 生成时与当前画像`;
      $('profile-comparison-status').textContent = `讲义 #${id} · 生成于 ${date(data.generated_at)} · 当前读取于 ${date(data.compared_at)}`;
      $('profile-comparison-note').textContent = `${data.topic}\n共同目标：${data.goal}\n`
        + (data.personalization_mode === 'none' ? '无画像模式：生成时没有使用下列学生的画像，当前画像仅供教师查看。'
          : '左侧来自这份讲义保存的生成输入；右侧为本次读取的当前画像。刷新后可看到最新确认。');
      drawStudent(); drawPoints(); content.hidden = false;
    } catch (err) {
      if (token !== session || !dialog.open) return;
      $('profile-comparison-status').textContent = '本次读取失败，可刷新重试。';
      $('profile-comparison-error').textContent = err.message;
      $('profile-comparison-error').hidden = false;
    } finally {
      if (token === session) {
        $('reload-profile-comparison').disabled = false;
        $('profile-comparison-filter').disabled = !data;
      }
    }
  }
  window.clearProfileComparison = () => {
    session++; lectureId = null; data = null;
    $('compare-profile').disabled = true;
    if (dialog.open) dialog.close();
  };
  window.showProfileComparison = result => {
    if (lectureId !== result.id) window.clearProfileComparison();
    lectureId = result.id;
    $('compare-profile').disabled = busy;
  };
  window.setProfileComparisonBusy = value => { $('compare-profile').disabled = value || !lectureId; };
  $('compare-profile').onclick = () => {
    if (busy || !lectureId) return;
    data = null;
    $('profile-comparison-filter').value = 'all';
    $('profile-comparison-title').textContent = '生成时与当前画像';
    dialog.showModal(); load();
  };
  $('close-profile-comparison').onclick = () => { session++; dialog.close(); };
  dialog.addEventListener('cancel', () => { session++; });
  $('reload-profile-comparison').onclick = load;
  $('profile-comparison-filter').onchange = () => { if (data) drawPoints(); };
})();
