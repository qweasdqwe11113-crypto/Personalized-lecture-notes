"use strict";
(() => {
  const labels = {reading:'正在提取题目与作答…', grading:'正在逐题批改…', completed:'阅卷完成 · 建议评分需教师复核', failed:'阅卷失败'};
  let activeId, timer;
  const message = text => { $('grading-error').textContent = text || ''; $('grading-error').hidden = !text; };
  function render(row) {
    $('grading-status').textContent = labels[row.status] || row.status;
    $('grading-extraction').hidden = !row.extracted;
    $('grading-extracted').textContent = row.extracted || '';
    $('grading-reference-text').textContent = row.reference_text || '未另传参考答案';
    $('grading-download').hidden = row.status !== 'completed';
    $('grading-results').replaceChildren();
    if (row.status === 'failed') message(row.error);
    const result = row.result;
    if (!result) { $('grading-summary').textContent = row.filename + ' · ' + labels[row.status]; return; }
    $('grading-download').href = '/api/grading/' + row.id + '/download';
    $('grading-summary').textContent = `${row.filename} · 共 ${result.questions.length} 题 · 已评得分 ${result.score} / 全卷 ${result.max_score} 分 · 待核对 ${result.pending} 题。所有分数均为建议评分。`;
    for (const q of result.questions) {
      const card = el('article', undefined, 'grading-question' + (q.status === 'needs_review' ? ' grading-pending' : ''));
      card.append(el('h3', `第 ${q.number} 题 · ${q.score === null ? '待核对' : q.score + ' 分'} / ${q.max_score} 分`));
      for (const [title, text] of [['题目',q.stem],['学生作答',q.student_answer],['参考答案',q.reference_answer],['评分解析与错因',q.feedback]]) {
        const p = el('p'); p.append(el('strong', title + '：'), document.createTextNode(text)); card.append(p);
      }
      card.append(el('p', q.basis === 'provided' ? '答案依据：材料提供的参考答案（需核对对应关系）' : '答案依据：模型推导，请重点核对。', 'grading-basis'));
      $('grading-results').append(card);
    }
  }
  async function history() {
    const data = await api('/api/grading');
    $('grading-history').replaceChildren();
    if (!data.records.length) $('grading-history').append(el('p','还没有阅卷记录。','helper'));
    for (const row of data.records) {
      const button = el('button', `${row.filename} · ${labels[row.status]} · ${new Date(row.created_at).toLocaleString('zh-CN')}`);
      button.type = 'button'; button.onclick = () => open(row.id).catch(e => message(e.message));
      $('grading-history').append(button);
    }
  }
  async function poll(id) {
    const row = await api('/api/grading/' + id);
    if (activeId !== id) return;
    render(row);
    const pending = ['reading','grading'].includes(row.status);
    if (pending) timer = setTimeout(() => poll(id).catch(e => message(e.message)), 1800);
    else await history();
  }
  async function open(id) {
    clearTimeout(timer); activeId = id; message('');
    try { localStorage.setItem('grading-last-id',id); } catch (_) {}
    await poll(id);
  }
  $('grading-form').onsubmit = async event => {
    event.preventDefault(); message('');
    const paper = $('grading-paper').files[0], reference = $('grading-reference').files[0];
    if (!paper) return message('请选择学生作答材料。');
    if (paper.size + (reference?.size || 0) > 10 * 1024 * 1024) return message('上传文件总大小不能超过10 MB。');
    const body = new FormData(); body.append('paper',paper);
    if (reference) body.append('reference',reference);
    body.append('requirements',$('grading-requirements').value);
    $('grading-submit').disabled = true;
    try {
      const response = await fetch('/api/grading',{method:'POST',body});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '上传失败。');
      await open(data.id); await history();
    } catch(e) { message(e.message); }
    finally { $('grading-submit').disabled = false; }
  };
  $('grading-refresh').onclick = () => history().catch(e => message(e.message));
  history().catch(e => message(e.message));
  try { const id = localStorage.getItem('grading-last-id'); if (id) open(id).catch(e => message(e.message)); } catch (_) {}
})();
