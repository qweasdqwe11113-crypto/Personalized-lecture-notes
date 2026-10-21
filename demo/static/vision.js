"use strict";
window.selectedVisualAnalyses = () => Array.from(document.querySelectorAll('#visual-evidence-options input:checked'), n => n.value);
async function refreshVisualEvidence() {
  const container = $('visual-evidence-options');
  const selected = new Set(selectedVisualAnalyses());
  try {
    const data = await api('/api/vision/analyses');
    container.replaceChildren();
    if (!data.analyses.length) container.append(el('p', '尚无图片解析，请先到「上传资料」上传并识读。', 'helper'));
    for (const item of data.analyses) {
      const label = el('label', undefined, 'visual-option');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = item.id; checkbox.checked = selected.has(item.id); checkbox.disabled = busy;
      checkbox.onchange = () => { if (selectedVisualAnalyses().length > 4) { checkbox.checked = false; error('每份讲义最多选择 4 张图片解析。'); } };
      label.append(checkbox, document.createTextNode(`${item.label}${item.page_no ? ' · 第' + item.page_no + '页' : ''} · ${item.model} · ${new Date(item.created_at).toLocaleString('zh-CN')}`));
      const details = el('details'); details.append(el('summary', '预览识读结果（需核对）'), el('p', item.content, 'visual-text'));
      container.append(label, details);
    }
  } catch (e) { container.replaceChildren(el('p', e.message, 'helper')); }
}
async function showUploadImages(uploadId) {
  const container = $('upload-preview-images');
  container.replaceChildren(el('p', '正在读取插图…', 'helper'));
  const data = await api('/api/uploads/' + uploadId + '/images');
  container.replaceChildren();
  if (!data.images.length) container.append(el('p', '未提取到图片。旧资料需重新上传以提取插图。', 'helper'));
  for (const image of data.images) {
    const card = el('section', undefined, 'visual-card');
    const img = el('img'); img.src = '/api/vision/images/' + image.id; img.alt = image.label; img.loading = 'lazy';
    const button = el('button', '使用本地 VLM 解析图片', 'secondary'); button.type = 'button';
    const output = el('p', '识读后请核对数值、公式和单位。', 'visual-text');
    button.onclick = async () => {
      button.disabled = true; output.textContent = '本地视觉模型正在识读，请稍候…';
      try {
        const result = await api('/api/vision/images/' + image.id + '/analyse', {method: 'POST'});
        output.textContent = result.analysis.content + '\n\n解析已保存，可在讲义工作台选择；仍需教师核对。';
        await refreshVisualEvidence();
      } catch (e) { output.textContent = e.message; }
      finally { button.disabled = false; }
    };
    card.append(el('h3', image.label + (image.page_no ? ' · 第' + image.page_no + '页' : '')), img, button, output);
    container.append(card);
  }
}
function drawVisualEvidence(evidence) {
  if (!evidence || !evidence.length) return;
  const section = el('section', undefined, 'visual-evidence-result');
  section.append(el('h3', '本次使用的图片解析'), el('p', '原图由独立视觉模型识读，语言模型仅使用解析文字。公式、数值及引用支持关系需核对。', 'helper'));
  for (const item of evidence) {
    const details = el('details');
    details.append(el('summary', `[${item.citation_id}] ${item.label} · ${item.model}`));
    const img = el('img'); img.src = '/api/vision/images/' + item.image_id; img.alt = item.label; img.loading = 'lazy';
    details.append(img, el('p', item.content, 'visual-text'), el('p', 'SHA-256: ' + item.normalized_sha256, 'helper'));
    section.append(details);
  }
  $('lecture').append(section);
}
$('refresh-visual-evidence').onclick = refreshVisualEvidence;
refreshVisualEvidence();
