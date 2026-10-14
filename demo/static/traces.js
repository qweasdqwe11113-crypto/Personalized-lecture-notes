// Every value below is displayed as text. Model/user input is never HTML.
let lectureTraceId = null, dialogTraceId = null, traceSession = 0;
const runLabels = {running: '未完成', succeeded: '讲义已保存', failed: '生成失败'};
function clearLectureTrace() {
  lectureTraceId = null;
  $('view-input').disabled = true;
  $('input-trace-note').hidden = true;
}
function showLectureTrace(result) {
  lectureTraceId = result.generation_run_id || null;
  $('view-input').disabled = !lectureTraceId;
  $('input-trace-note').hidden = false;
  $('input-trace-note').textContent = lectureTraceId
    ? `本次生成输入已留存 · 记录 #${lectureTraceId}`
    : '这份历史讲义没有保存完整 Prompt 与参数，无法精确还原当时的调用。学生与材料快照仍可查看。';
}
function showFailedTrace(runId) {
  if (!runId) return;
  lectureTraceId = runId;
  $('view-input').disabled = false;
  $('input-trace-note').hidden = false;
  $('input-trace-note').textContent = `本次未生成完整讲义，输入与状态可从记录 #${runId} 核查。`;
}
async function refreshRuns() {
  const data = await api('/api/generation-runs');
  $('generation-runs').replaceChildren(...data.runs.map(run => {
    const button = el('button', undefined, 'run-history-item');
    button.type = 'button';
    button.append(el('span', `#${run.id} ${run.name} · ${runLabels[run.status]}`, 'run-' + run.status),
                  el('span', run.topic, 'helper'));
    button.onclick = () => openTrace(run.id);
    return button;
  }));
  if (!data.runs.length) $('generation-runs').append(el('p', '尚无保存完整输入的新请求。既有讲义在“最近生成”查看。', 'helper'));
}
function traceError(message) {
  $('trace-dialog-error').textContent = message || '';
  $('trace-dialog-error').hidden = !message;
}
function drawTrace(run) {
  const payload = run.request_payload || {};
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  $('trace-title').textContent = `生成记录 #${run.id} · ${runLabels[run.status]}`;
  $('trace-status').textContent = run.status === 'running'
    ? '记录尚未结束，可能仍在处理或曾中断，请刷新核查。'
    : run.status === 'succeeded' ? `已关联讲义 #${run.lecture_id}，输入与输出一起留存。` : '此记录没有进入成功讲义历史。';
  traceError(!run.integrity_valid ? '输入校验值不匹配，请核查存储记录。' : run.error_message);
  $('trace-call-note').textContent = run.attempted_at
    ? `已尝试调用模型 · ${new Date(run.attempted_at).toLocaleString('zh-CN')}`
    : '输入已准备，尚未记录模型调用尝试。配置或资料检查失败时，输入仍可核查。';
  $('trace-metadata').replaceChildren();
  for (const [label, value] of [
    ['提示模板版本', run.prompt_version], ['输入结构版本', run.input_version],
    ['请求模型', payload.model || '未记录'], ['服务返回模型', run.response_metadata.response_model || '未记录'],
    ['记录时间', new Date(run.created_at).toLocaleString('zh-CN')],
    ['结束时间', run.finished_at ? new Date(run.finished_at).toLocaleString('zh-CN') : '尚未确认'],
    ['耗时', run.elapsed_seconds === null ? '未记录' : `${Number(run.elapsed_seconds).toFixed(2)} 秒`],
    ['输入 SHA-256', run.request_sha256], ['模型用量', JSON.stringify(run.usage)],
    ['失败分类', run.error_code ? `${run.error_stage} / ${run.error_code}` : '无已记录失败']
  ]) $('trace-metadata').append(el('dt', label), el('dd', value));
  const context = run.context;
  $('trace-context-summary').textContent = [
    `学生：${context.student.name}（虚拟数据）`, `主题：${run.topic}`,
    `本次目标：${run.goal}`, `主题默认目标：${context.topic.goal}`,
    `学习特点：${context.student.profile}`,
    '知识状态：' + context.states.map(s => `${s.name} / ${s.label} / 记录版本 ${s.revision ?? '未记录'}`).join('；'),
    '课程材料：' + context.materials.map(m => `M${m.id} ${m.title}`).join('；')
  ].join('\n\n');
  $('trace-system').textContent = messages.find(m => m.role === 'system')?.content || '记录中缺少系统消息。';
  $('trace-user').textContent = messages.find(m => m.role === 'user')?.content || '记录中缺少用户消息。';
  $('trace-parameters').textContent = JSON.stringify(Object.fromEntries(Object.entries(payload).filter(([key]) => key !== 'messages')), null, 2);
  $('trace-body').textContent = run.request_body;
  $('trace-output-section').hidden = !run.content;
  $('trace-output-title').textContent = run.status === 'succeeded' ? '输出正文（仍需教师审核）' : '部分输出（生成失败，不能当作完整讲义）';
  $('trace-output').textContent = run.content;
  $('download-input').hidden = !run.integrity_valid || !run.request_payload;
  $('download-input').href = `/api/generation-runs/${run.id}/input`;
  $('download-input').download = `generation-${run.id}-input.json`;
  $('retry-run').hidden = run.status !== 'failed' || !run.integrity_valid || !run.request_payload;
  $('retry-run').dataset.runId = run.id;
  $('trace-content').hidden = false;
}
async function openTrace(runId) {
  dialogTraceId = runId;
  const session = ++traceSession;
  traceError('');
  $('trace-content').hidden = true;
  $('download-input').hidden = true;
  $('retry-run').hidden = true;
  $('trace-title').textContent = `生成记录 #${runId}`;
  $('trace-status').textContent = '正在读取保存的输入…';
  if (!$('trace-dialog').open) $('trace-dialog').showModal();
  try {
    const run = await api(`/api/generation-runs/${runId}`);
    if (session === traceSession && $('trace-dialog').open) drawTrace(run);
  } catch (err) {
    if (session === traceSession) { traceError(err.message); $('trace-status').textContent = '记录暂时无法读取。'; }
  }
}
$('view-input').onclick = () => { if (lectureTraceId) openTrace(lectureTraceId); };
$('reload-trace').onclick = () => { if (dialogTraceId) openTrace(dialogTraceId); };
$('close-trace').onclick = () => { traceSession++; $('trace-dialog').close(); };
$('trace-dialog').addEventListener('cancel', () => { traceSession++; });
$('refresh-runs').onclick = () => refreshRuns().catch(err => error(err.message));
refreshRuns().catch(() => { $('generation-runs').replaceChildren(el('p', '记录暂时无法读取，请刷新。', 'helper')); });
