const state = { documents: [], busy: false };
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function simpleMarkdown(markdown = '') {
  return escapeHtml(markdown)
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>');
}

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  setTimeout(() => element.classList.remove('show'), 2400);
}

async function loadDocuments() {
  const data = await api('/api/documents');
  state.documents = data.documents;
  renderDocuments();
}

function renderDocuments() {
  $('#doc-count').textContent = state.documents.length;
  $('#stat-docs').textContent = state.documents.length;
  $('#stat-chunks').textContent = state.documents.reduce((sum, doc) => sum + (doc.chunks?.length || 0), 0);
  $('#stat-sources').textContent = new Set(state.documents.map(doc => doc.source)).size;
  $('#source-list').innerHTML = state.documents.slice(0, 4).map(doc => `
    <div class="source-card"><strong>${escapeHtml(doc.name)}</strong><span>${escapeHtml(doc.type)} · ${doc.chunks?.length || 0} 个片段</span><p>${escapeHtml((doc.content || '').slice(0, 62))}${doc.content?.length > 62 ? '…' : ''}</p></div>
  `).join('') || '<p class="muted">暂无知识资料</p>';
  $('#documents-table').innerHTML = `
    <div class="doc-row header"><span>资料名称</span><span>类型</span><span>来源</span><span>片段</span><span></span></div>
    ${state.documents.map(doc => `<div class="doc-row">
      <div class="doc-name"><strong>${escapeHtml(doc.name)}</strong><small>${new Date(doc.createdAt).toLocaleString('zh-CN')}</small></div>
      <span class="tag">${escapeHtml(doc.type)}</span><span title="${escapeHtml(doc.source)}">${escapeHtml(doc.source).slice(0, 28)}</span><span>${doc.chunks?.length || 0}</span>
      <button class="delete" data-delete="${doc.id}" title="删除">×</button>
    </div>`).join('')}
  `;
}

function addUserMessage(text) {
  $('#messages').insertAdjacentHTML('beforeend', `<div class="message user"><div class="bubble"><p>${escapeHtml(text)}</p></div></div>`);
  scrollMessages();
}

function addAssistantMessage(data) {
  const sources = (data.sources || []).slice(0, 4);
  $('#messages').insertAdjacentHTML('beforeend', `<div class="message assistant result">
    <div class="bot-icon">✦</div><div class="bubble"><div class="message-meta">${escapeHtml(data.title || '教学助理')}</div><p>${simpleMarkdown(data.markdown || '')}</p>
    ${sources.length ? `<div class="source-tags">${sources.map(source => `<span>↗ ${escapeHtml(source.documentName)}</span>`).join('')}</div>` : ''}</div></div>`);
  scrollMessages();
}

function scrollMessages() {
  const box = $('#messages');
  box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
}

async function sendMessage(preset) {
  if (state.busy) return;
  const prompt = $('#prompt');
  const message = (preset || prompt.value).trim();
  if (!message) return;
  prompt.value = '';
  addUserMessage(message);
  state.busy = true;
  $('#send').disabled = true;
  try {
    const data = await api('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message, profile: { level: $('#level').value, weakness: $('#weakness').value.trim() } })
    });
    addAssistantMessage(data);
  } catch (error) { toast(error.message); }
  finally { state.busy = false; $('#send').disabled = false; }
}

async function uploadFiles(files) {
  for (const file of files) {
    const isText = /text|json|csv|markdown/.test(file.type) || /\.(txt|md|csv|json)$/i.test(file.name);
    const content = isText ? await file.text() : '';
    await api('/api/documents', { method: 'POST', body: JSON.stringify({ name: file.name, type: isText ? '文本资料' : '多模态资料', size: file.size, content }) });
  }
  await loadDocuments();
  toast(`已入库 ${files.length} 份资料`);
}

$$('.nav-item').forEach(button => button.addEventListener('click', () => {
  $$('.nav-item').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  $$('.view').forEach(view => view.classList.remove('active'));
  $(`#${button.dataset.view}-view`).classList.add('active');
  $('#page-title').textContent = button.dataset.view === 'chat' ? '教学工作台' : '课程知识库';
}));

$$('[data-prompt]').forEach(button => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));
$('#send').addEventListener('click', () => sendMessage());
$('#prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage(); }
});
$('#new-chat').addEventListener('click', () => {
  $$('.message.user, .message.result').forEach(node => node.remove());
  toast('已开始新对话');
});
$('#kb-file').addEventListener('change', event => uploadFiles([...event.target.files]).catch(error => toast(error.message)));
$('#chat-file').addEventListener('change', async event => {
  const files = [...event.target.files];
  if (!files.length) return;
  $('#upload-chip').textContent = `正在上传：${files[0].name}`;
  $('#upload-chip').classList.remove('hidden');
  try { await uploadFiles(files); $('#upload-chip').textContent = `✓ 已加入知识库：${files[0].name}`; }
  catch (error) { toast(error.message); }
});
$('#documents-table').addEventListener('click', async event => {
  const id = event.target.dataset.delete;
  if (!id) return;
  await api(`/api/documents/${encodeURIComponent(id)}`, { method: 'DELETE' });
  await loadDocuments();
  toast('资料已删除');
});
$('#web-search').addEventListener('click', async () => {
  const button = $('#web-search');
  button.disabled = true;
  $('#web-status').textContent = '正在搜索、解析并写入知识库…';
  try {
    const data = await api('/api/web-search', { method: 'POST', body: JSON.stringify({ topic: $('#web-topic').value }) });
    $('#web-status').textContent = `${data.online ? '联网搜索完成' : '网络不可用，已使用演示资料'}，新增 ${data.imported} 条。`;
    await loadDocuments();
  } catch (error) { $('#web-status').textContent = error.message; }
  finally { button.disabled = false; }
});

loadDocuments().catch(error => toast(error.message));
