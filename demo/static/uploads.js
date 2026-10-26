(() => {
  const tabs = [$('upload-file-tab'), $('upload-url-tab')];
  const panes = [$('upload-file-pane'), $('upload-url-pane')];
  let uploading = false;
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
  async function refreshUploads() {
    try {
      const data = await api('/api/uploads');
      const list = $('upload-list'); list.replaceChildren();
      if (!data.uploads.length) { const empty = el('div', undefined, 'upload-library-empty'); empty.append(el('span', '▤'), el('strong', '还没有上传资料'), el('p', '从本地文件或网址开始，添加第一份教学资料。')); list.append(empty); return; }
      for (const item of data.uploads) {
        const row = el('div', undefined, 'upload-row');
        const icon = el('span', item.kind === 'url' ? '↗' : '▤', 'upload-row-icon'); icon.setAttribute('aria-hidden', 'true');
        const info = el('div', undefined, 'upload-row-info');
        const preview = el('button', item.title, 'upload-preview-button'); preview.type = 'button';
        preview.onclick = async () => {
          try { const data = await api('/api/uploads/' + item.id); $('upload-preview-title').textContent = data.upload.title; $('upload-preview-meta').textContent = item.kind === 'url' ? item.source_url : item.filename; $('upload-preview-content').textContent = data.upload.content; $('upload-preview-images').replaceChildren(); $('upload-preview-dialog').showModal(); await showUploadImages(item.id); } catch (e) { status(e.message, true); }
        };
        info.append(preview, el('p', `${item.kind === 'url' ? '网址导入' : '本地上传'} · ${item.characters.toLocaleString()} 字符 · ${new Date(item.created_at).toLocaleString('zh-CN')}`));
        for (const warning of item.warnings || []) info.append(el('p', warning, 'check-warning'));
        const download = el('a', '下载原文件', 'secondary'); download.href = '/api/uploads/' + item.id + '/download'; download.download = item.filename;
        const pictures = el('button', '图片识读', 'secondary'); pictures.type = 'button'; pictures.onclick = () => preview.click();
        row.append(icon, info, pictures, download); list.append(row);
      }
    } catch (e) { $('upload-list').replaceChildren(el('p', e.message, 'helper')); }
  }
  async function submit(options) {
    if (uploading) return;
    uploading = true;
    panes.forEach(pane => pane.querySelectorAll('input,button').forEach(control => control.disabled = true));
    status('正在读取和解析资料，请稍候…');
    try {
      const data = await api('/api/uploads', options);
      status(`「${data.upload.title}」已保存，提取 ${data.upload.characters.toLocaleString()} 个字符、${data.upload.images || 0} 张图片。可点击「图片识读」继续解析。` + (data.upload.warnings?.length ? ' 提醒：' + data.upload.warnings.join(' ') : ''));
      await refreshUploads();
    } catch (e) { status(e.message, true); }
    finally { uploading = false; panes.forEach(pane => pane.querySelectorAll('input,button').forEach(control => control.disabled = false)); }
  }
  panes[0].onsubmit = event => {
    event.preventDefault(); const file = selectedFile();
    if (!file) { status('请选择要上传的资料文件。', true); return; }
    if (!/\.(txt|md|pdf|docx|png|jpe?g|webp|bmp|gif|tiff?)$/i.test(file.name) || file.size > 10 * 1024 * 1024 || file.size === 0) { status('请选择非空文档或图片，大小不超过 10 MB。', true); return; }
    const body = new FormData(); body.append('file', file); body.append('title', $('upload-file-title').value.trim()); submit({method:'POST', body});
  };
  panes[1].onsubmit = event => { event.preventDefault(); submit({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:$('upload-url').value.trim(), title:$('upload-url-title').value.trim()})}); };
  $('refresh-uploads').onclick = refreshUploads;
  $('close-upload-preview').onclick = () => $('upload-preview-dialog').close();
  refreshUploads();
})();
