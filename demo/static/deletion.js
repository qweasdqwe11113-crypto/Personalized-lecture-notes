/* One confirmation for every delete: what will go, that it cannot be undone, and for a student the
   name typed out. run(typedName) does the delete; an error keeps the dialog open and shows why. */
(() => {
  let finish = null, run = null;
  const dialog = $('delete-dialog'), confirm = $('delete-dialog-confirm'), name = $('delete-dialog-name');
  const fail = message => { $('delete-dialog-error').textContent = message || ''; $('delete-dialog-error').hidden = !message; };
  function close(result) {
    if (dialog.open) dialog.close();
    const done = finish; finish = null; run = null;
    if (done) done(result);
  }
  window.confirmDeletion = ({title, lines, typed = '', action}) => new Promise(resolve => {
    if (finish) finish(null);
    finish = resolve; run = action;
    $('delete-dialog-title').textContent = title;
    $('delete-dialog-lines').replaceChildren(...lines.filter(Boolean).map(line => el('li', line)));
    $('delete-dialog-name-field').hidden = !typed;
    $('delete-dialog-name-label').textContent = typed ? `输入「${typed}」以确认` : '';
    name.value = ''; name.oninput = () => { confirm.disabled = name.value.trim() !== typed; };
    confirm.disabled = Boolean(typed); fail('');
    dialog.showModal();
    (typed ? name : $('delete-dialog-cancel')).focus();
  });
  confirm.onclick = async () => {
    if (!run || confirm.disabled) return;
    confirm.disabled = true; fail('');
    try { close(await run(name.value.trim())); }
    catch (e) { fail(e.message); confirm.disabled = false; }
  };
  $('delete-dialog-cancel').onclick = () => close(null);
  $('delete-dialog-close').onclick = () => close(null);
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(null); });
  // The lecture on screen: its versions, issues and generation records go; companion quizzes stay.
  window.addEventListener('lecture-version-changed', () => { $('delete-lecture').disabled = !lastResult || busy; });
  $('delete-lecture').onclick = async () => {
    const lecture = lastResult;
    if (!lecture || busy) return;
    try {
      const p = await api(`/api/lectures/${lecture.id}/delete-preview`);
      const done = await confirmDeletion({title: `删除讲义 #${lecture.id}`, lines: [
        `讲义「${p.title}」的原稿${p.revisions ? `和修订 ${p.revisions} 版` : ''}${p.issues ? `、问题记录 ${p.issues} 条` : ''}`,
        `生成记录 ${p.runs} 条（原稿、按章节调整和课件）`,
        p.quizzes_kept ? `配套题组 ${p.quizzes_kept} 份保留，标为「原讲义已删除」` : ''],
        action: () => postDelete(`/api/lectures/${lecture.id}/delete`)});
      if (!done) return;
      clearResult();
      if (typeof refreshHistory === 'function') refreshHistory().catch(() => {});
    } catch (e) { error(e.message); }
  };
  window.postDelete = (url, body) => api(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
})();
