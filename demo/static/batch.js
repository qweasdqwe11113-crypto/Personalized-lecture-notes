/* Queue the current lesson for several students. Each student gets a separate frozen
   input, run and lecture; this window only submits and reports. */
(() => {
const labels = {queued: '排队中', running: '生成中', succeeded: '已保存', failed: '失败', interrupted: '进程中断', cancelled: '已取消'};
let jobs = [], timer = 0, session = 0;
const boxes = () => Array.from($('batch-students').querySelectorAll('input'));
const fail = message => { $('batch-error').textContent = message || ''; $('batch-error').hidden = !message; };
function count() {
  const chosen = boxes().filter(box => box.checked).length;
  $('batch-selected').textContent = `已选 ${chosen} 人`;
  $('batch-submit').disabled = !chosen || jobs.some(job => ['queued', 'running'].includes(job.status));
}
function drawStudents() {
  // Within the current class by default; a lesson without a course keeps every listed student.
  const topic = topics[$('topic').value], ids = topic?.student_ids, scope = window.workspaceScope;
  const cls = scope?.klass(), members = cls && topic?.course_id === scope.courseId() ? new Set(cls.student_ids) : null;
  const list = students.filter(student => (!ids || ids.includes(student.id)) && (!members || members.has(student.id)));
  $('batch-note').textContent = `${topic?.title || '当前备课'}${members ? ' · ' + cls.name : ''} · 可选 ${list.length} 名学生`;
  $('batch-students').replaceChildren(...list.map(student => {
    const label = el('label', undefined, 'batch-student'), box = el('input');
    box.type = 'checkbox'; box.value = student.id; box.checked = true; box.onchange = count;
    label.append(box, el('span', student.name), el('small', student.subtitle));
    return label;
  }));
  count();
}
function drawStatus() {
  const done = jobs.filter(job => job.status === 'succeeded').length, failed = jobs.filter(job => job.error || ['failed', 'interrupted', 'cancelled'].includes(job.status)).length;
  $('batch-status').replaceChildren(...jobs.map(job => {
    const row = el('div', undefined, 'batch-row batch-' + (job.error ? 'failed' : job.status));
    row.append(el('strong', job.name), el('span', job.error ? '未提交' : labels[job.status] || job.status, 'batch-state'));
    if (job.lecture_id) {
      const open = el('button', '打开讲义 #' + job.lecture_id, 'secondary'); open.type = 'button';
      open.onclick = async () => {
        if (busy) return;
        try { const row = await api('/api/lectures/' + job.lecture_id); $('batch-dialog').close(); restoreLectureSettings(row); showResult(row); }
        catch (e) { fail(e.message); }
      };
      row.append(open);
    } else if (job.error || job.error_message) row.append(el('span', job.error || job.error_message, 'batch-reason'));
    else if (job.elapsed_seconds) row.append(el('span', Number(job.elapsed_seconds).toFixed(0) + ' 秒', 'helper'));
    return row;
  }));
  if (jobs.length) $('batch-summary').textContent = `共 ${jobs.length} 份 · 已保存 ${done} · 未完成 ${jobs.length - done - failed} · 失败 ${failed}`;
  count();
}
async function poll(current) {
  timer = 0;
  if (current !== session) return;
  const pending = jobs.filter(job => job.job_id);
  if (!pending.length) return;
  try {
    const data = await api('/api/job-status?ids=' + pending.map(job => job.job_id).join(','));
    if (current !== session) return;
    const byId = new Map(data.jobs.map(job => [job.id, job]));
    jobs = jobs.map(job => job.job_id && byId.has(job.job_id) ? {...job, ...byId.get(job.job_id)} : job);
    drawStatus();
  } catch (e) { fail(e.message); }
  if (jobs.some(job => ['queued', 'running'].includes(job.status))) timer = setTimeout(() => poll(current), 2000);
  else { if (typeof refreshHistory === 'function') refreshHistory().catch(() => {}); if (typeof window.refreshWorkspaceJobs === 'function') window.refreshWorkspaceJobs().catch(() => {}); }
}
$('batch-open').addEventListener('click', () => { fail(''); if (!jobs.some(job => ['queued', 'running'].includes(job.status))) drawStudents(); });
$('batch-all').onclick = () => { boxes().forEach(box => { box.checked = true; }); count(); };
$('batch-none').onclick = () => { boxes().forEach(box => { box.checked = false; }); count(); };
$('batch-submit').onclick = async () => {
  const ids = boxes().filter(box => box.checked).map(box => Number(box.value));
  if (!ids.length) return;
  fail(''); $('batch-submit').disabled = true;
  const names = new Map(students.map(student => [student.id, student.name]));
  try {
    const data = await api('/api/jobs/batch', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...window.generationPayload(), student_ids: ids})});
    jobs = data.jobs.map(job => ({...job, name: names.get(job.student_id) || '学生 ' + job.student_id}));
    drawStatus();
    if (timer) clearTimeout(timer);
    poll(++session);
  } catch (e) { fail(e.message); count(); }
};
})();
