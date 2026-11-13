/* Start-up screen: draws the state the main process publishes and offers fixes when a step fails. */
(() => {
  const desktop = window.zhiyeDesktop;
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };

  function drawSteps(steps) {
    $('steps').replaceChildren(...steps.map(step => {
      const item = el('li', undefined, 'step');
      item.dataset.state = step.state;
      const icon = el('span', undefined, 'step-icon'); icon.setAttribute('aria-hidden', 'true');
      const label = el('span', step.label, 'step-label');
      const status = {running: '进行中', done: '已完成', failed: '失败'}[step.state];
      if (status) label.setAttribute('aria-label', `${step.label}：${status}`);
      item.append(icon, label, el('span', step.state === 'failed' ? '' : step.detail || '', 'step-detail'));
      return item;
    }));
    const done = steps.filter(s => s.state === 'done').length;
    const running = steps.some(s => s.state === 'running') ? 0.5 : 0;
    $('progress-fill').style.inlineSize = steps.length ? `${Math.round((done + running) / steps.length * 100)}%` : '35%';
  }

  function draw(state) {
    if (!state) return;
    document.body.dataset.phase = state.phase;
    $('lead').textContent = state.lead || '';
    drawSteps(state.steps || []);
    const failed = state.phase === 'failed' && state.error;
    $('error').hidden = !failed;
    if (failed) {
      $('error-title').textContent = state.error.title;
      $('error-message').textContent = state.error.message;
      $('error-hint').textContent = state.error.hint || '';
      $('error-log').textContent = (state.error.log || []).join('\n') || '暂无日志。';
      $('retry').textContent = state.error.retryLabel || '重试';
      $('retry').disabled = false;
    }
    const info = state.info || {};
    document.title = info.productName || '知页';
    $('product').textContent = info.productName === '知页测试版' ? '知页' : info.productName || '知页';
    $('edition').hidden = info.edition !== 'demo';
    $('edition').textContent = info.editionLabel || '';
    $('version').textContent = info.version ? `版本 ${info.version}` : '';
    $('data-dir').textContent = info.dataDir ? `数据目录 ${info.dataDir}` : '';
    $('data-dir').title = info.dataDir || '';
  }

  const act = (id, work) => $(id).addEventListener('click', async () => {
    $(id).disabled = true;
    try { await work(); } finally { $(id).disabled = false; }
  });
  act('retry', () => desktop.retry());
  act('open-logs', () => desktop.openLogs());
  act('open-data', () => desktop.openDataDir());
  act('choose-data', () => desktop.chooseDataDir());

  if (!desktop) {
    draw({phase: 'failed', lead: '无法连接客户端', steps: [], error: {title: '页面没有在知页客户端中打开', message: '请通过知页程序启动。'}});
    return;
  }
  desktop.onState(draw);
  desktop.state().then(draw);
})();
