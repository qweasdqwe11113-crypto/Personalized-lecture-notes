/* 新手引导: a short tour of the workspace. The page is dimmed except for one area, and a card beside
   it says what that area is for (上一步 / 下一步 / 跳过 , or ← → and Esc). The tour only points;
   it stays on the workbench and changes nothing in the workspace.
   The desktop release opens it by itself once per data folder: the server records that it was
   finished or skipped (/api/onboarding), so a changed port or another browser does not bring it
   back. It can be opened again from the sidebar (新手引导) or the client's 帮助 menu. */
(() => {
  const nav = page => document.querySelector(`.app-nav a[data-page="${page}"]`);
  const desktopClient = () => Boolean(window.zhiyeDesktop);
  let settingsWereCollapsed = false;

  const STEPS = [
    {title: '欢迎使用知页', welcome: true,
     text: () => `花一分钟认识一下界面。可以随时跳过，之后在左侧「新手引导」${desktopClient() ? '或「帮助」菜单' : ''}里重新查看。`},
    {target: () => document.querySelector('.app-nav'), title: '功能都在左侧',
     text: '备课、出题、阅卷、管理学生，都从这里进入。'},
    {target: () => $('context-bar'), title: '当前课程与班级',
     text: '各页面都按这里选中的课程和班级显示内容，换课、换班都在这里切换。'},
    {target: () => nav('settings'), title: '先连接语言模型',
     text: '讲义、出题、阅卷都要用到模型。在这里填写服务地址、模型名称和密钥，测试通过后保存，再重启服务。'},
    {target: () => [nav('materials'), nav('profiles')], title: '准备课程和学生',
     text: '在「课程」中建立知识点库、上传教材讲义、新建班级和备课；在「班级与画像」中添加学生、记录学习情况。'},
    {target: () => $('settings-column'), before: expandSettings, title: '在这里生成讲义',
     text: '选好备课和讲义对象（全班或某名学生），设置时长和讲法，再点「生成个性化讲义」。结果都保存在「生成记录」里。'},
    {target: () => [nav('quizzes'), nav('grading')], title: '出题与阅卷',
     text: '按讲义出配套练习；上传学生作答自动阅卷，你确认后记入学生画像。'},
    // Only while the checklist is showing: it hides itself once every step is done.
    {target: () => $('getting-started'), optional: true, title: '从这里开始',
     text: '按顺序完成这几步，就能生成第一份讲义。全部完成后，清单会自动隐藏。'},
  ];

  const GAP = 14, MARGIN = 12, PAD = 6;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  /** Where the card goes beside the lit area: right, below, left or above, whichever fits first;
      over the area's lower right corner when none does (the checklist keeps that corner empty);
      centred when nothing is lit. Pure, for tests. */
  function place(target, card, view) {
    if (!target) return {side: 'center', left: (view.width - card.width) / 2, top: (view.height - card.height) / 2};
    const room = {
      right: target.right + GAP + card.width <= view.width - MARGIN,
      bottom: target.bottom + GAP + card.height <= view.height - MARGIN,
      left: target.left - GAP - card.width >= MARGIN,
      top: target.top - GAP - card.height >= MARGIN,
    };
    const side = ['right', 'bottom', 'left', 'top'].find(name => room[name]);
    const across = clamp(target.left + target.width / 2 - card.width / 2, MARGIN, view.width - card.width - MARGIN);
    if (!side) return {side: 'inside', left: clamp(target.right - card.width - GAP, MARGIN, view.width - card.width - MARGIN),
                       top: clamp(target.bottom - card.height - GAP, MARGIN, view.height - card.height - MARGIN)};
    if (side === 'right' || side === 'left') {
      const top = clamp(target.top + target.height / 2 - card.height / 2, MARGIN, view.height - card.height - MARGIN);
      return {side, top, left: side === 'right' ? target.right + GAP : target.left - GAP - card.width,
              arrow: clamp(target.top + target.height / 2 - top, 22, card.height - 22)};
    }
    return {side, left: across, top: side === 'bottom' ? target.bottom + GAP : target.top - GAP - card.height,
            arrow: clamp(target.left + target.width / 2 - across, 22, card.width - 22)};
  }

  function shown(node) {
    if (!node || node.hidden || !node.getClientRects().length) return false;
    const box = node.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }

  function targets(step) {
    if (!step.target) return [];
    return [].concat(step.target()).filter(shown);
  }

  /** The lit area: every target together, with a little room around it, kept inside the window. */
  function area(nodes) {
    if (!nodes.length) return null;
    const boxes = nodes.map(node => node.getBoundingClientRect());
    const left = Math.max(PAD, Math.min(...boxes.map(b => b.left)) - PAD);
    const top = Math.max(PAD, Math.min(...boxes.map(b => b.top)) - PAD);
    const right = Math.min(innerWidth - PAD, Math.max(...boxes.map(b => b.right)) + PAD);
    const bottom = Math.min(innerHeight - PAD, Math.max(...boxes.map(b => b.bottom)) + PAD);
    return {left, top, right, bottom, width: right - left, height: bottom - top};
  }

  function expandSettings() {
    const workspace = $('workspace'), toggle = $('sidebar-toggle');
    if (workspace?.classList.contains('settings-collapsed') && shown(toggle)) {
      settingsWereCollapsed = true;
      toggle.click();
    }
  }

  /** Bring a target that scrolls with the page into view; the sidebar is fixed and never needs it. */
  function reveal(nodes) {
    const node = nodes.find(n => !n.closest('.app-sidebar'));
    if (!node) return;
    const box = node.getBoundingClientRect();
    if (box.top >= 16 && box.bottom <= innerHeight - 16) return;
    const offset = box.height + 32 < innerHeight ? (innerHeight - box.height) / 2 : 24;
    window.scrollBy({top: box.top - offset, behavior: 'instant'});
  }

  let layer, hole, card, title, text, count, dots, prev, next, index = 0, frame = 0, opened = false;
  let returnFocus = null;

  function build() {
    layer = el('div', undefined, 'tour-layer');
    layer.hidden = true;
    hole = el('div', undefined, 'tour-hole');
    card = el('section', undefined, 'tour-card');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('aria-labelledby', 'tour-title');
    card.setAttribute('aria-describedby', 'tour-text');
    const head = el('div', undefined, 'tour-head');
    count = el('span', '', 'tour-count');
    const skip = el('button', '跳过引导', 'tour-skip');
    skip.type = 'button';
    skip.onclick = () => close('skipped');
    head.append(count, skip);
    const badge = el('span', '知', 'tour-badge');
    badge.setAttribute('aria-hidden', 'true');
    title = el('h2', '', 'tour-title'); title.id = 'tour-title';
    text = el('p', '', 'tour-text'); text.id = 'tour-text';
    text.setAttribute('aria-live', 'polite');
    const foot = el('div', undefined, 'tour-foot');
    dots = el('div', undefined, 'tour-dots');
    dots.setAttribute('aria-hidden', 'true');
    const actions = el('div', undefined, 'tour-actions');
    prev = el('button', '上一步', 'secondary'); prev.type = 'button'; prev.onclick = () => go(-1);
    next = el('button', '下一步', 'primary'); next.type = 'button'; next.onclick = () => go(1);
    actions.append(prev, next);
    foot.append(dots, actions);
    card.append(head, badge, title, text, foot);
    layer.append(hole, card);
    // A passive tour: the dimmed page does not respond, and wheel or touch scrolling would only
    // move the lit area away from what it describes.
    for (const name of ['wheel', 'touchmove']) layer.addEventListener(name, event => event.preventDefault(), {passive: false});
    document.body.append(layer);
  }

  /** The steps that can be shown now; the checklist step drops out once the checklist is hidden. */
  function visibleSteps() {
    return STEPS.filter(step => !step.optional || targets(step).length);
  }

  function render(focus = true) {
    const list = visibleSteps();
    index = clamp(index, 0, list.length - 1);
    const step = list[index];
    step.before?.();
    const nodes = targets(step);
    reveal(nodes);
    title.textContent = step.title;
    text.textContent = typeof step.text === 'function' ? step.text() : step.text;
    count.textContent = `${index + 1} / ${list.length}`;
    count.setAttribute('aria-label', `第 ${index + 1} 步，共 ${list.length} 步`);
    dots.replaceChildren(...list.map((_, i) => el('i', undefined, i === index ? 'is-current' : i < index ? 'is-past' : '')));
    prev.hidden = index === 0;
    next.textContent = index === list.length - 1 ? '完成' : index === 0 ? '开始了解' : '下一步';
    card.classList.toggle('is-welcome', Boolean(step.welcome));
    layer.dataset.step = String(STEPS.indexOf(step) + 1);
    position();
    if (focus) next.focus({preventScroll: true});
  }

  function position() {
    const list = visibleSteps();
    const step = list[clamp(index, 0, list.length - 1)];
    const lit = area(targets(step));
    hole.classList.toggle('is-empty', !lit);
    const spot = lit || {left: innerWidth / 2, top: innerHeight / 2, width: 0, height: 0};
    Object.assign(hole.style, {left: `${spot.left}px`, top: `${spot.top}px`, width: `${spot.width}px`, height: `${spot.height}px`});
    const size = card.getBoundingClientRect();
    const spotPlace = place(lit, {width: size.width, height: size.height}, {width: innerWidth, height: innerHeight});
    card.dataset.side = spotPlace.side;
    card.style.left = `${Math.round(spotPlace.left)}px`;
    card.style.top = `${Math.round(spotPlace.top)}px`;
    if (spotPlace.arrow !== undefined) card.style.setProperty('--tour-arrow', `${Math.round(spotPlace.arrow)}px`);
  }

  function schedule() {
    if (!opened || frame) return;
    frame = requestAnimationFrame(() => { frame = 0; position(); });
  }

  function go(delta) {
    const last = visibleSteps().length - 1;
    if (delta > 0 && index >= last) return close('finished');
    index = clamp(index + delta, 0, last);
    render();
  }

  function trapKeys(event) {
    if (!opened) return;
    if (event.key === 'Escape') { event.preventDefault(); close('skipped'); return; }
    if (event.key === 'ArrowRight') { event.preventDefault(); go(1); return; }
    if (event.key === 'ArrowLeft') { event.preventDefault(); if (index > 0) go(-1); return; }
    if (event.key !== 'Tab') return;
    const stops = Array.from(card.querySelectorAll('button')).filter(button => !button.hidden);
    const at = stops.indexOf(document.activeElement);
    event.preventDefault();
    stops[(at + (event.shiftKey ? -1 : 1) + stops.length) % stops.length].focus();
  }

  function background(value) {
    for (const node of document.querySelectorAll('body > .app-sidebar, body > main')) node.inert = value;
  }

  async function onWorkbench() {
    if (document.body.dataset.page === 'workbench') return;
    const shownPage = new Promise(resolve => {
      window.addEventListener('hashchange', resolve, {once: true});
      setTimeout(resolve, 400);
    });
    location.hash = 'workbench';
    await shownPage;
    await new Promise(resolve => requestAnimationFrame(resolve));
  }

  async function start() {
    if (opened) return;
    if (!layer) build();
    await onWorkbench();
    opened = true;
    index = 0;
    settingsWereCollapsed = false;
    returnFocus = document.activeElement;
    background(true);
    layer.hidden = false;
    requestAnimationFrame(() => layer.classList.add('is-open'));
    render();
    document.addEventListener('keydown', trapKeys, true);
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, true);
  }

  function close(outcome) {
    if (!opened) return;
    const step = Number(layer.dataset.step) || 1;
    opened = false;
    document.removeEventListener('keydown', trapKeys, true);
    window.removeEventListener('resize', schedule);
    window.removeEventListener('scroll', schedule, true);
    layer.classList.remove('is-open');
    layer.hidden = true;
    background(false);
    if (settingsWereCollapsed && !$('workspace')?.classList.contains('settings-collapsed')) $('sidebar-toggle')?.click();
    window.scrollTo({top: 0, behavior: 'instant'});
    (returnFocus && returnFocus !== document.body && document.contains(returnFocus) ? returnFocus : $('page-title'))?.focus?.({preventScroll: true});
    api('/api/onboarding/tour', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                 body: JSON.stringify({outcome, step})}).catch(() => { /* shown again next time */ });
  }

  async function openByItself() {
    let state;
    try { state = (await api('/api/onboarding')).tour; } catch { return; }
    if (state.auto && !document.hidden && document.body.dataset.page === 'workbench') start();
  }

  $('tour-open')?.addEventListener('click', () => start());
  window.zhiyeDesktop?.onShowTour?.(() => start());
  // After the course and class selectors and the checklist have had their first answer.
  (window.workspaceScope?.ready || Promise.resolve()).then(() => setTimeout(openByItself, 350), () => {});
  window.zhiyeTour = Object.freeze({start, place});
})();
