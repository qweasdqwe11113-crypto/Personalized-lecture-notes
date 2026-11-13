(() => {
  const tabs = [$('upload-file-tab'), $('upload-url-tab')];
  const panes = [$('upload-file-pane'), $('upload-url-pane')];
  let uploading = false, previewVersion = 0;
  function selectMode(index) {
    tabs.forEach((tab, i) => { tab.setAttribute('aria-selected', String(i === index)); tab.tabIndex = i === index ? 0 : -1; panes[i].hidden = i !== index; });
  }
  tabs.forEach((tab, index) => {
    tab.onclick = () => selectMode(index);
    tab.onkeydown = event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const target = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index;
        selectMode(target); tabs[target].focus();
      }
    };
  });
  function status(message, failed = false) {
    $('upload-status').hidden = !message;
    $('upload-status').textContent = message;
    $('upload-status').classList.toggle('upload-failed', failed);
  }
  function selectedFile() {
    const file = $('upload-file').files[0];
    $('upload-file-selection').textContent = file ? file.name + ' · ' + (file.size / 1024).toFixed(1) + ' KB' : '尚未选择文件';
    return file;
  }
  $('upload-file').onchange = () => { selectedFile(); status(''); };
  const dropzone = $('upload-dropzone');
  for (const name of ['dragenter', 'dragover']) dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.add('is-dragging'); });
  for (const name of ['dragleave', 'drop']) dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove('is-dragging'); });
  dropzone.addEventListener('drop', event => {
    if (uploading) return;
    if (event.dataTransfer.files.length !== 1) { status('请每次上传一个文件。', true); return; }
    $('upload-file').files = event.dataTransfer.files;
    selectedFile(); status('');
  });
  // Resources live in the course (#materials/resources) or the class (#profiles/resources); one
  // workspace moves between the two hosts. Without a scope (tests, old pages) every upload is listed.
  const scope = window.workspaceScope;
  let listVersion = 0;
  function mode() {
    const hash = (window.location?.hash || '').slice(1);
    return !scope ? 'all' : hash === 'materials/resources' ? 'course' : hash === 'profiles/resources' ? 'class' : null;
  }
  function place(m) {
    if (m === 'all') return true;
    const course = scope.course(), cls = scope.klass();
    if (!course || (m === 'class' && !cls)) return false;
    const host = $(m === 'course' ? 'course-resource-host' : 'class-resource-host');
    if ($('resource-workspace').parentElement !== host) host.append($('resource-workspace'));
    $('upload-panel-title').textContent = m === 'course' ? '上传课程资料' : '上传班级资料';
    $('upload-scope-note').textContent = m === 'course' ? `资料归入「${course.name}」，本课程所有班级共用。` : `资料只属于「${cls.name}」，收录后只在为该班或班内学生生成时使用。`;
    $('upload-scope-badge').textContent = m === 'course' ? '课程资料' : '班级专用';
    $('upload-library-note').textContent = m === 'course' ? '本课程及其班级的资料，班级专用的单独标注 · 最近 100 条' : '本班专用资料 · 本课程共用资料见「课程 → 课程资料」';
    return true;
  }
  function listUrl(m) {
    if (m === 'all') return '/api/uploads';
    const q = 'course_id=' + scope.courseId();
    return m === 'course' ? '/api/uploads?scope=course_all&' + q : '/api/uploads?scope=class&' + q + '&class_id=' + scope.classId();
  }
  const className = id => scope?.course()?.classes.find(k => k.id === id)?.name || '班级';
  function uploadRow(item, actions) {
    const row = el('div', undefined, 'upload-row');
    const icon = el('span', item.kind === 'url' ? '↗' : '▤', 'upload-row-icon'); icon.setAttribute('aria-hidden', 'true');
    const info = el('div', undefined, 'upload-row-info');
    const preview = el('button', item.title, 'upload-preview-button'); preview.type = 'button';
    preview.onclick = async () => {
      const version = ++previewVersion;
      if (typeof invalidateUploadImages === 'function') invalidateUploadImages();
      try {
        const data = await api('/api/uploads/' + item.id);
        if (version !== previewVersion) return;
        $('upload-preview-title').textContent = data.upload.title;
        $('upload-preview-meta').textContent = item.kind === 'url' ? item.source_url : item.filename;
        $('upload-preview-content').textContent = data.upload.content;
        $('upload-preview-images').replaceChildren();
        if (!$('upload-preview-dialog').open) $('upload-preview-dialog').showModal();
        await showUploadImages(item.id);
      } catch (e) { if (version === previewVersion) status(e.message, true); }
    };
    const where = item.class_id ? `${className(item.class_id)}专用 · ` : '';
    info.append(preview, el('p', `${where}${item.kind === 'url' ? '网址导入' : '本地上传'} · ${Number(item.characters).toLocaleString()} 字符 · ${new Date(item.created_at).toLocaleString('zh-CN')}`));
    for (const warning of item.warnings || []) info.append(el('p', warning, 'check-warning'));
    // Knowledge-point recognition status of course and class uploads (knowledge.js).
    const knowledge = scope && window.courseKnowledge?.uploadStatus(item);
    if (knowledge) info.append(knowledge);
    row.append(icon, info, ...actions(preview));
    return row;
  }
  function standardActions(item) {
    return preview => {
      const list = [];
      if (scope) { const attach = el('button', '收录到备课', 'secondary'); attach.type = 'button'; attach.onclick = () => openAttach(item); list.push(attach); }
      const pictures = el('button', '图片识读', 'secondary'); pictures.type = 'button'; pictures.onclick = () => preview.click();
      const download = el('a', '下载原文件', 'secondary'); download.href = '/api/uploads/' + item.id + '/download'; download.download = item.filename;
      return [...list, pictures, download];
    };
  }
  async function refreshUploads() {
    const m = mode(), version = ++listVersion;
    if (!m || !place(m)) return;
    try {
      if (m !== 'all' && window.courseKnowledge) await window.courseKnowledge.load().catch(() => {});
      const data = await api(listUrl(m));
      if (version !== listVersion) return;
      const list = $('upload-list'); list.replaceChildren();
      $('upload-library-title').textContent = m === 'class' ? `班级资料 · ${data.uploads.length} 份` : m === 'course' ? `课程资料 · ${data.uploads.length} 份` : '课程资料';
      if (!data.uploads.length) { const empty = el('div', undefined, 'upload-library-empty'); empty.append(el('span', '▤'), el('strong', m === 'class' ? '本班还没有专用资料' : '还没有上传资料'), el('p', m === 'class' ? '上传本班试卷、专用讲义等，收录后只用于本班。' : '从本地文件或网址开始，添加第一份教学资料。')); list.append(empty); }
      for (const item of data.uploads) list.append(uploadRow(item, standardActions(item)));
      if (m === 'course') await refreshUnassigned(version);
      else $('upload-unassigned-box').hidden = true;
    } catch (e) { if (version === listVersion) $('upload-list').replaceChildren(el('p', e.message, 'helper')); }
  }
  async function refreshUnassigned(version) {
    // Uploads made before courses existed: filing one under this course makes it listable here.
    const data = await api('/api/uploads?scope=unassigned');
    if (version !== listVersion) return;
    $('upload-unassigned-box').hidden = !data.uploads.length;
    $('upload-unassigned-title').textContent = `未归类的旧资料 · ${data.uploads.length} 份`;
    $('upload-unassigned-list').replaceChildren(...data.uploads.map(item => uploadRow(item, () => {
      const file = el('button', '归入本课程', 'secondary'); file.type = 'button';
      file.onclick = async () => {
        file.disabled = true;
        try { await api('/api/uploads/' + item.id + '/scope', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({course_id: scope.courseId(), class_id: null})}); status(`「${item.title}」已归入本课程。`); await refreshUploads(); }
        catch (e) { status(e.message, true); file.disabled = false; }
      };
      return [file];
    })));
  }
  // 收录到备课: pick one of this course's own lessons and a knowledge point.
  let attachItem = null, attachCatalog = {};
  async function openAttach(item) {
    attachItem = item; $('attach-dialog-status').textContent = '';
    $('attach-dialog-meta').textContent = `资料：${item.title}`;
    $('attach-dialog-scope').textContent = item.class_id ? `这是「${className(item.class_id)}」的专用资料，收录后只在为该班或班内学生生成时引用。` : '这是课程资料，收录后本课程所有班级生成时都可引用。';
    $('attach-dialog-submit').disabled = true;
    if (!$('attach-dialog').open) $('attach-dialog').showModal();
    try {
      const d = await api('/api/course-tasks'); if (attachItem !== item) return;
      const course = item.course_id ?? scope.courseId();
      attachCatalog = Object.fromEntries(Object.entries(d.topics).filter(([, t]) => t.custom && t.course_id === course));
      const names = new Map(d.knowledge_points.map(k => [k.id, k.name]));
      for (const t of Object.values(attachCatalog)) t.pointNames = t.knowledge_ids.map((id, i) => t.knowledge_names?.[i] || names.get(id) || '知识点 ' + id);
      const keys = Object.keys(attachCatalog), keep = $('attach-dialog-topic').value;
      $('attach-dialog-topic').replaceChildren(...keys.map(k => { const o = el('option', attachCatalog[k].title || '未命名备课'); o.value = k; return o; }));
      if (keys.includes(keep)) $('attach-dialog-topic').value = keep;
      if (!keys.length) $('attach-dialog-status').textContent = '本课程还没有自己的备课。请先在「课程 → 备课」新建备课，或复制示例。';
      drawAttachPoints();
    } catch (e) { $('attach-dialog-status').textContent = e.message; }
  }
  function drawAttachPoints() {
    const t = attachCatalog[$('attach-dialog-topic').value];
    $('attach-dialog-knowledge').replaceChildren(...(t?.knowledge_ids || []).map((id, i) => { const o = el('option', t.pointNames[i]); o.value = id; return o; }));
    $('attach-dialog-submit').disabled = !t || !t.knowledge_ids.length;
    if (t && !t.knowledge_ids.length) $('attach-dialog-status').textContent = '这份备课还没有知识点，请先在备课中填写。';
  }
  if (scope) {
    $('attach-dialog-topic').onchange = () => { $('attach-dialog-status').textContent = ''; drawAttachPoints(); };
    $('attach-dialog-form').onsubmit = async event => {
      event.preventDefault(); const button = $('attach-dialog-submit'); if (button.disabled || !attachItem) return;
      if (busy) { $('attach-dialog-status').textContent = '请等待当前生成结束。'; return; }
      button.disabled = true; const key = $('attach-dialog-topic').value;
      try {
        const d = await api('/api/course-tasks/' + encodeURIComponent(key) + '/materials', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({upload_id: attachItem.id, knowledge_id: Number($('attach-dialog-knowledge').value)})});
        $('attach-dialog-status').textContent = `已收录为 ${d.citation_id}，生成「${attachCatalog[key].title || '该备课'}」的讲义时可引用。`;
        if ($('topic').value === key && typeof loadContext === 'function') loadContext();
      } catch (e) { $('attach-dialog-status').textContent = e.message; }
      finally { button.disabled = false; }
    };
    window.addEventListener('hashchange', () => { if (mode()) refreshUploads(); });
    window.addEventListener('scope-changed', () => { if (mode()) refreshUploads(); });
    window.addEventListener('knowledge-changed', () => { if (mode() && mode() !== 'all') refreshUploads(); });
  }
  // Where a new upload is filed: the course, or the class on the class resources tab.
  function target() {
    const m = mode();
    if (!scope || !m || m === 'all' || scope.courseId() == null) return {};
    return m === 'class' && scope.classId() != null ? {course_id: scope.courseId(), class_id: scope.classId()} : {course_id: scope.courseId()};
  }
  async function submit(options) {
    if (uploading) return;
    uploading = true;
    panes.forEach(pane => pane.querySelectorAll('input,button').forEach(control => control.disabled = true));
    status('正在读取和解析资料，请稍候…');
    try {
      const data = await api('/api/uploads', options);
      const saved = `「${data.upload.title}」已保存，提取 ${data.upload.characters.toLocaleString()} 个字符、${data.upload.images || 0} 张图片。`;
      const warned = data.upload.warnings?.length ? ' 提醒：' + data.upload.warnings.join(' ') : '';
      // Course and class uploads with text are recognized right away; the teacher confirms later.
      if (data.upload.characters && data.upload.course_id != null && window.courseKnowledge) {
        await window.courseKnowledge.recognize(data.upload.id);
        status(saved + '正在自动识别知识点，完成后在资料行点「审核并确认」。' + warned);
      } else status(saved + '可点击「图片识读」继续解析。' + warned);
      await refreshUploads();
    } catch (e) { status(e.message, true); }
    finally { uploading = false; panes.forEach(pane => pane.querySelectorAll('input,button').forEach(control => control.disabled = false)); }
  }
  panes[0].onsubmit = event => {
    event.preventDefault(); const file = selectedFile();
    if (!file) { status('请选择要上传的资料文件。', true); return; }
    if (!/\.(txt|md|pdf|docx|png|jpe?g|webp|bmp|gif|tiff?)$/i.test(file.name) || file.size > 10 * 1024 * 1024 || file.size === 0) { status('请选择非空文档或图片，大小不超过 10 MB。', true); return; }
    const body = new FormData(); body.append('file', file); body.append('title', $('upload-file-title').value.trim());
    for (const [key, value] of Object.entries(target())) body.append(key, String(value));
    submit({method:'POST', body});
  };
  panes[1].onsubmit = event => { event.preventDefault(); submit({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:$('upload-url').value.trim(), title:$('upload-url-title').value.trim(), ...target()})}); };
  $('refresh-uploads').onclick = refreshUploads;
  $('close-upload-preview').onclick = () => $('upload-preview-dialog').close();
  $('upload-preview-dialog').addEventListener('close', () => {
    previewVersion++;
    if (typeof invalidateUploadImages === 'function') invalidateUploadImages();
  });
  if (scope) scope.ready.then(() => refreshUploads(), () => {}); else refreshUploads();
})();
