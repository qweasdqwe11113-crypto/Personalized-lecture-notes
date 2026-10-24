// Profile maintenance always reads the current database, even beside an old lecture.
let editorProfile = null, editorStudentId = null, editorBusy = false, editorSession = 0;
let lastRecorder = '';
const stateRules = {
  unknown: '没有充分或有效的诊断依据，不据此判断能力不足。',
  needs_support: '证据显示关键概念错误，或无法完成基础步骤。',
  partial: '能完成部分任务，但解释、准确性或迁移仍不足。',
  mastered: '有独立完成目标任务并解释依据的证据，必要时检查变式。'
};
function profileError(message) {
  $('profile-error').textContent = message || '';
  $('profile-error').hidden = !message;
}
function editorState() {
  return editorProfile?.states.find(s => s.id === Number($('evidence-knowledge').value));
}
function drawStateRule() {
  const retain = $('evidence-decision').value === 'retain';
  $('evidence-state').disabled = editorBusy || retain;
  $('state-rule').textContent = retain ? '新证据会保存；当前状态及其原有依据保持不变，待教师复核。' : stateRules[$('evidence-state').value];
}
function drawCurrentState(resetChoice = false) {
  const s = editorState();
  if (!s) return;
  $('evidence-current').textContent = `当前：${s.label} · 记录版本 ${s.revision}\n现有依据：${s.evidence}`;
  if (resetChoice) $('evidence-state').value = s.state;
  drawStateRule();
}
function localMinute() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function historyCard(summaryText, lines) {
  const card = el('details', undefined, 'history-card');
  card.append(el('summary', summaryText));
  for (const [label, text] of lines) card.append(el('p', `${label}：${text}`, 'evidence'));
  return card;
}
function drawEditor(data, resetProfile = true, resetEvidence = true) {
  const selected = $('evidence-knowledge').value;
  editorProfile = data;
  $('profile-editor-title').textContent = data.student.name + ' · 维护画像';
  $('profile-current').textContent = `当前说明：${data.student.subtitle} · 版本 ${data.student.profile_revision}\n当前学习特点：${data.student.profile}`;
  if (resetProfile) {
    $('profile-form').reset();
    $('profile-name').value = data.student.name;
    $('profile-subtitle').value = data.student.subtitle;
    $('profile-text').value = data.student.profile;
    $('profile-recorder').value = lastRecorder;
  }
  if (resetEvidence) {
    $('evidence-form').reset();
    $('evidence-time').value = localMinute();
    $('evidence-recorder').value = lastRecorder;
  }
  const chosenState = $('evidence-state').value;
  $('evidence-knowledge').replaceChildren(...data.states.map(s => {
    const option = el('option', s.name); option.value = s.id; return option;
  }));
  if (data.states.some(s => String(s.id) === selected)) $('evidence-knowledge').value = selected;
  $('evidence-state').replaceChildren(...Object.entries(data.state_labels).map(([key, label]) => {
    const option = el('option', label); option.value = key; return option;
  }));
  if (!resetEvidence && chosenState) $('evidence-state').value = chosenState;
  $('profile-confirmed').checked = false;
  $('evidence-confirmed').checked = false;
  drawCurrentState(resetEvidence);
  const history = data.evidence_history;
  $('evidence-history-count').textContent = `共 ${data.evidence_count} 条证据，展示最近 ${history.length} 条。观察时间与录入时间分别保留。`;
  $('evidence-history').replaceChildren(...history.map(e => {
    const decision = e.decision === 'baseline' ? '原有画像留存' : e.decision === 'retain' ? '保留原判断，待复核' : '教师确认';
    return historyCard(`${e.knowledge_name} · ${decision} · ${data.state_labels[e.previous_state]} → ${data.state_labels[e.confirmed_state]}`, [
      ['来源', data.source_labels[e.source]], ['任务', e.task_content], ['学生表现', e.performance],
      ['此前依据', e.previous_evidence], ['确认理由', e.reason], ['录入者', e.recorder],
      ['观察时间', e.observed_at ? new Date(e.observed_at).toLocaleString('zh-CN') : '原记录未提供'],
      ['录入时间', new Date(e.created_at).toLocaleString('zh-CN')], ['记录版本', `${e.previous_revision} → ${e.new_revision}`]
    ]);
  }));
  $('profile-history').replaceChildren(...data.profile_history.map(h => historyCard(
    `${h.recorder} · 版本 ${h.previous_snapshot.profile_revision} → ${h.new_snapshot.profile_revision} · ${new Date(h.created_at).toLocaleString('zh-CN')}`, [
      ['原姓名', h.previous_snapshot.name], ['新姓名', h.new_snapshot.name],
      ['原说明', h.previous_snapshot.subtitle], ['新说明', h.new_snapshot.subtitle],
      ['原学习特点', h.previous_snapshot.profile], ['新学习特点', h.new_snapshot.profile], ['修改理由', h.reason]
    ]
  )));
  if (!data.profile_history.length) $('profile-history').append(el('p', '尚无教师修改记录。', 'helper'));
  $('profile-editor-content').hidden = false;
}
function setEditorBusy(value) {
  editorBusy = value;
  for (const node of $('profile-dialog').querySelectorAll('input,textarea,select,button')) node.disabled = value;
  drawStateRule();
}
async function loadEditor(preserveDraft) {
  preserveDraft = preserveDraft && Boolean(editorProfile);
  profileError('');
  setEditorBusy(true);
  const session = editorSession;
  $('profile-status').textContent = '正在读取当前画像…';
  try {
    const data = await api(`/api/students/${editorStudentId}/profile`);
    if (session !== editorSession) return;
    drawEditor(data, !preserveDraft, !preserveDraft);
    $('profile-status').textContent = preserveDraft ? '已读取当前版本，输入已保留。请对照当前依据，重新确认后保存。' : '画像来自当前数据库；所有修改都需要教师确认。';
  } catch (err) { if (session === editorSession) profileError(err.message); }
  finally { if (session === editorSession) setEditorBusy(false); }
}
$('edit-profile').onclick = async () => {
  if (busy || !window.selectedProfileStudentId) return;
  editorSession++;
  editorStudentId = window.selectedProfileStudentId;
  editorProfile = null;
  $('profile-editor-content').hidden = true;
  $('profile-dialog').showModal();
  await loadEditor(false);
};
$('close-profile').onclick = () => { if (!editorBusy) { editorSession++; $('profile-dialog').close(); } };
$('profile-dialog').addEventListener('cancel', e => { if (editorBusy) e.preventDefault(); else editorSession++; });
$('reload-profile').onclick = () => loadEditor(true);
$('evidence-knowledge').onchange = () => { drawCurrentState(true); $('evidence-confirmed').checked = false; };
$('evidence-decision').onchange = () => { drawStateRule(); $('evidence-confirmed').checked = false; };
$('evidence-state').onchange = () => { drawStateRule(); $('evidence-confirmed').checked = false; };
for (const formId of ['profile-form', 'evidence-form']) {
  $(formId).addEventListener('input', event => {
    if (event.target.type !== 'checkbox') $(formId === 'profile-form' ? 'profile-confirmed' : 'evidence-confirmed').checked = false;
  });
}
async function refreshAfterProfileSave() {
  await refreshWorkspaceCatalog();
  if (window.refreshStudentRoster) await refreshStudentRoster(editorStudentId);
  await loadContext();
}
async function saveProfileForm(event, evidence) {
  event.preventDefault();
  if (editorBusy || !editorProfile) return;
  const fields = Object.fromEntries(new FormData(event.target));
  fields.confirmed = $(evidence ? 'evidence-confirmed' : 'profile-confirmed').checked;
  fields.expected_revision = evidence ? editorState().revision : editorProfile.student.profile_revision;
  if (evidence) {
    fields.knowledge_id = Number(fields.knowledge_id);
    fields.observed_at = new Date(fields.observed_at).toISOString();
  }
  profileError('');
  setEditorBusy(true);
  $('profile-status').textContent = '正在保存…';
  try {
    const data = await api(`/api/students/${editorStudentId}/${evidence ? 'evidence' : 'profile'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields)
    });
    lastRecorder = fields.recorder;
    drawEditor(data, !evidence, evidence);
    $('profile-status').textContent = evidence ? '证据和教师判断已一起保存，原记录仍在历史中。' : '学生说明已保存，原说明仍在修改历史中。';
    try { await refreshAfterProfileSave(); }
    catch { $('profile-status').textContent += ' 主页面暂未刷新，可关闭后重新读取。'; }
  } catch (err) {
    profileError(err.message);
    $('profile-status').textContent = err.status === 409 ? '输入尚未保存。使用“保留输入，重新读取”对照最新版本后，再次确认。' : '本次未保存，请核对输入后重试。';
  } finally { setEditorBusy(false); }
}
$('profile-form').onsubmit = e => saveProfileForm(e, false);
$('evidence-form').onsubmit = e => saveProfileForm(e, true);
