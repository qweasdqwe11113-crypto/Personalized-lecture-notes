"use strict";
(() => {
  const labels = {reading:'正在提取题目与作答…', grading:'正在逐题批改…', completed:'阅卷完成 · 建议评分需教师复核', failed:'阅卷失败'};
  let activeId, timer;
  const message = text => { $('grading-error').textContent = text || ''; $('grading-error').hidden = !text; };
  const historyError = text => { $('grading-history-error').textContent = text || ''; $('grading-history-error').hidden = !text; };
  function render(row) {
    window.dispatchEvent(new CustomEvent('grading-record-shown', {detail: row}));
    $('grading-status').textContent = labels[row.status] || row.status;
    $('grading-status').dataset.status = row.status;
    $('grading-extraction').hidden = !row.extracted;
    $('grading-extracted').textContent = row.extracted || '';
    $('grading-reference-text').textContent = row.reference_text || '未另传参考答案';
    if (row.quiz_reference) $('grading-reference-text').textContent = `使用题组「${row.quiz_reference.title}」的教师版答案。\n\n` + (row.reference_text || '');
    $('grading-download').hidden = row.status !== 'completed';
    $('grading-results').replaceChildren();
    if (row.status === 'failed') message(row.error);
    const result = row.result;
    if (!result) { $('grading-summary').textContent = row.filename + ' · ' + labels[row.status]; return; }
    $('grading-download').href = '/api/grading/' + row.id + '/download';
    $('grading-summary').textContent = `${row.filename} · 共 ${result.questions.length} 题 · 已评得分 ${result.score} / 全卷 ${result.max_score} 分 · 待核对 ${result.pending} 题。所有分数均为建议评分。`;
    for (const q of result.questions) {
      const state = q.status === 'needs_review' ? 'pending' : q.score === q.max_score ? 'correct' : 'incorrect';
      const card = el('article', undefined, 'grading-question grading-' + state);
      const heading = el('h3', `第 ${q.number} 题`);
      heading.append(el('span', q.score === null ? `待核对 / ${q.max_score} 分` : `${q.score} / ${q.max_score} 分`, 'grading-score'));
      card.append(heading);
      for (const [title, text] of [['题目',q.stem],['学生作答',q.student_answer]]) {
        const p = el('p'); p.append(el('strong', title + '：'), document.createTextNode(text)); card.append(p);
      }
      const details = el('details'); details.open = state !== 'correct';
      details.append(el('summary', '参考答案与评分解析'));
      for (const [title, text] of [['参考答案',q.reference_answer],['评分解析与错因',q.feedback]]) {
        const p = el('p'); p.append(el('strong', title + '：'), document.createTextNode(text)); details.append(p);
      }
      details.append(el('p', q.basis === 'provided' ? '答案依据：材料提供的参考答案（需核对对应关系）' : '答案依据：模型推导，请重点核对。', 'grading-basis'));
      card.append(details);
      $('grading-results').append(card);
    }
  }
  async function history() {
    try {
      const data = await api('/api/grading');
      historyError('');
      $('grading-history').replaceChildren();
      if (!data.records.length) $('grading-history').append(el('p','还没有阅卷记录。','helper'));
      for (const row of data.records) {
        const button = el('button', `${row.filename} · ${labels[row.status]} · ${new Date(row.created_at).toLocaleString('zh-CN')}`);
        button.type = 'button'; button.onclick = () => {
          openWorkspacePage('grading');
          open(row.id).catch(e => message(e.message));
        };
        $('grading-history').append(button);
      }
    } catch(e) { historyError(e.message); }
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
    const mode = answerMode(), paper = $('grading-paper').files[0];
    const reference = mode === 'file' ? $('grading-reference').files[0] : null, quiz = mode === 'quiz' ? $('grading-quiz').value : '';
    if (!paper) return message('请选择学生作答材料。');
    if (mode === 'quiz' && !quiz) return message('请选择学生作答对应的题组，或改选其他答案来源。');
    if (mode === 'file' && !reference) return message('请选择参考答案文件；没有答案时可改选「由模型推导」。');
    if (paper.size + (reference?.size || 0) > 10 * 1024 * 1024) return message('上传文件总大小不能超过10 MB。');
    const body = new FormData(); body.append('paper',paper);
    if (reference) body.append('reference',reference);
    body.append('requirements',$('grading-requirements').value);
    if (quiz) body.append('quiz_id', quiz);
    $('grading-submit').disabled = true;
    try {
      const response = await fetch('/api/grading',{method:'POST',body});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '上传失败。');
      await open(data.id); await history();
    } catch(e) { message(e.message); }
    finally { $('grading-submit').disabled = false; }
  };
  function answerMode() { return document.querySelector('input[name=grading-answer]:checked')?.value || 'file'; }
  function syncAnswerMode() {
    $('grading-answer-quiz').hidden = answerMode() !== 'quiz';
    $('grading-answer-file').hidden = answerMode() !== 'file';
  }
  for (const input of document.querySelectorAll('input[name=grading-answer]')) input.onchange = syncAnswerMode;
  async function quizzes() {
    const select = $('grading-quiz'), selected = select.value;
    const data = await api('/api/quizzes');
    select.replaceChildren(el('option', '请选择题组'));select.firstChild.value = '';
    for (const q of data.quizzes.filter(q => q.status === 'succeeded')) {
      const option = el('option', `${q.title} · ${q.options.count} 题${q.lecture_id ? ' · 配套讲义 #' + q.lecture_id : ''} · ${new Date(q.created_at).toLocaleString('zh-CN')}`);
      option.value = q.id; select.append(option);
    }
    if (Array.from(select.options).some(o => o.value === selected)) select.value = selected;
    // With finished quizzes available, grading against them is the default: it keeps the point mapping.
    if (!quizzesLoaded && select.options.length > 1) document.querySelector('input[name=grading-answer][value=quiz]').checked = true;
    quizzesLoaded = true; syncAnswerMode();
  }
  let quizzesLoaded = false;
  window.addEventListener('hashchange', () => { if (location.hash === '#grading') quizzes().catch(() => {}); });
  quizzes().catch(() => {});
  $('grading-refresh').onclick = history;
  window.addEventListener('grading-history-open', history);
  history();
  try { const id = localStorage.getItem('grading-last-id'); if (id) open(id).catch(e => message(e.message)); } catch (_) {}
})();
