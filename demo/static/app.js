const $ = id => document.getElementById(id);
let students = [], topics = {}, current = null, lastResult = null, busy = false, contextVersion = 0, modelConfigured = false;
const el = (tag, text, cls) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; };
function error(message){$('error').textContent=message||'';$('error').hidden=!message;}
function hasGenerationBasis(ctx){return Boolean(ctx && (ctx.materials.length || ctx.topic.preparation_copy));}
function drawPreparation(ctx){
 $('preparation-preview').hidden=!ctx.topic.preparation_copy;
 $('preparation-preview-goal').textContent='副本目标：'+ctx.topic.goal;
 $('preparation-preview-plan').textContent=ctx.topic.lesson_plan||'未填写教案正文，按教学目标组织讲义。';
}
async function refreshWorkspaceCatalog(preferredTopic, preferredStudent){
 const d=await api('/api/bootstrap');const key=preferredTopic||$('topic').value;const sid=preferredStudent||Number($('student').value);
 topics=d.topics;students=d.students;
 $('topic').replaceChildren(...Object.entries(topics).map(([key,t])=>{const o=el('option',t.custom?[t.stage,t.course,t.chapter,t.title].filter(Boolean).join(' / '):t.title);o.value=key;return o;}));
 if(topics[key])$('topic').value=key;filterStudents(sid);drawHistory(d.history);window.dispatchEvent(new Event('workspace-context'));return d;
}
async function api(url, options){const r=await fetch(url, options);let d;try{d=await r.json();}catch{throw new Error('服务未返回有效响应，请确认本地服务仍在运行。');}if(!r.ok){const err=new Error(d.error||'请求失败，请重试。');err.status=r.status;err.runId=d.generation_run_id;throw err;}return d;}
function drawContext(ctx, snapshot=false){
 current=ctx; $('student-profile').textContent=ctx.student.profile; drawPreparation(ctx); if(snapshot)$('learner-note').value=ctx.learner_note||'';
 $('learner-note').disabled=busy||ctx.personalization_mode==='none';
 $('task-level').textContent=(ctx.topic.task_level||'当前任务')+'；课程任务与掌握状态分别设置。';
 window.dispatchEvent(new Event("workspace-context"));
 drawSources(ctx.materials);if(typeof drawScope==='function')drawScope(ctx.scope_check);
}
function drawSources(materials){$('source-count').textContent=materials.length+' 条材料';$('sources').replaceChildren(...materials.map(m=>{const d=el('details',undefined,'source');const s=el('summary');s.append(el('span','M'+m.id),document.createTextNode(m.title));const a=el('a',m.source_title+' · 查看原始资料 ↗');a.href=m.source_url.startsWith('upload:')?'/api/uploads/'+encodeURIComponent(m.source_url.slice(7))+'/download':m.source_url;a.target='_blank';a.rel='noopener noreferrer';d.append(s,el('p',m.content),a,el('p',m.section,'source-location'));if(m.chunk_count)d.append(el('p',`本次由向量检索取到 ${m.chunk_count} 个片段，最高相似度 ${Number(m.max_score).toFixed(3)}；相似度高不代表该片段支持对应表述。`,'source-location'));return d;}));}
function clearResult(){lastResult=null;$('download').disabled=true;$('download-format').disabled=true;$('print-lecture').disabled=true;$('empty').hidden=false;$('lecture').hidden=true;$('lecture-meta').hidden=true;$('review-note').hidden=true;$('result-label').textContent='讲义预览';if(typeof clearLectureTrace==='function')clearLectureTrace();if(typeof clearLectureReview==='function')clearLectureReview();if(typeof clearProfileComparison==='function')clearProfileComparison();if(typeof clearLectureComparison==='function')clearLectureComparison();}
async function loadContext(){const version=++contextVersion;current=null;error('');$('generate').disabled=true;clearResult();const vector=$('retrieval-mode').value==='vector';$('retrieval-note').textContent=vector?'正在检查并更新资料索引，首次准备可能需要一些时间…':'精确 SQL 直接读取材料，无需向量索引。';try{const c=await api(`/api/context?student_id=${$('student').value}&topic=${$('topic').value}&personalization_mode=${$('personalization-mode').value}&retrieval_mode=${$('retrieval-mode').value}&goal=${encodeURIComponent($('goal').value)}&learner_note=${encodeURIComponent($('learner-note').value)}`);if(version!==contextVersion)return;drawContext(c);$('generate').disabled=!modelConfigured||!hasGenerationBasis(c);if(!hasGenerationBasis(c))$('retrieval-note').textContent='当前任务尚无课程材料，请到「课程材料」关联已上传资料后生成。';else if(vector)loadRetrievalStatus(version);}catch(e){if(version===contextVersion){error(e.message);$('retrieval-note').textContent=vector?'资料准备或检索失败：'+e.message:'材料读取失败：'+e.message;}}}
async function loadRetrievalStatus(version){
 const note=$('retrieval-note');if(!note)return;
 note.textContent='正在读取索引状态…';
 try{
  const d=await api(`/api/retrieval/status?topic=${$('topic').value}`);
  if(version!==contextVersion)return;
  const ready=d.points.filter(p=>p.indexed).length;
  note.textContent=`资料索引已自动更新 · ${ready}/${d.points.length} 个知识点有片段 · 嵌入模型 ${d.embed_model}。后续仅更新新增或变化的材料。`;
  if(ready<d.points.length)note.textContent+=' 部分知识点暂无可索引的材料。';
 }catch(e){if(version===contextVersion)note.textContent='索引状态读取失败，请检查本机 PostgreSQL。';}
}
// Small Markdown renderer: all model text is inserted as text nodes, never raw HTML.
function inline(node,text){const parts=text.split(/(\*\*[^*]+\*\*|\[[MV]\d+\])/g);for(const p of parts){if(p.startsWith('**')&&p.endsWith('**'))node.append(el('strong',p.slice(2,-2)));else if(/^\[[MV]\d+\]$/.test(p))node.append(el('span',p,'citation'));else node.append(document.createTextNode(p));}}
function renderMarkdown(text){const frag=document.createDocumentFragment();let list=null;for(const raw of text.split('\n')){const line=raw.trim();if(!line){list=null;continue;}const heading=line.match(/^(#{1,3})\s+(.+)$/);const item=line.match(/^([-*]|\d+[.、])\s+(.+)$/);if(heading){list=null;const h=el('h'+heading[1].length);inline(h,heading[2]);frag.append(h);}else if(item){const tag=/\d/.test(item[1])?'ol':'ul';if(!list||list.tagName.toLowerCase()!==tag){list=el(tag);frag.append(list);}const li=el('li');if(tag==='ol')li.value=parseInt(item[1],10);inline(li,item[2]);list.append(li);}else{list=null;const p=el('p');if(/^P\(.+\)\s*=/.test(line)){p.className='math-line';p.setAttribute('aria-label','概率公式：'+line);}inline(p,line);frag.append(p);}}return frag;}
function showResult(r, refreshReview=true){if(typeof openWorkspacePage==="function")openWorkspacePage("workbench");lastResult=r;$('empty').hidden=true;$('loading').hidden=true;$('lecture').hidden=false;$('lecture-meta').hidden=false;$('review-note').hidden=false;$('lecture').replaceChildren(renderMarkdown(r.content));if(typeof drawVisualEvidence==='function')drawVisualEvidence(r.context.visual_evidence);$('result-label').textContent=r.context.personalization_mode==='none'?'无画像对照讲义':r.context.student.name+'的讲义';$('lecture-meta').textContent=`${r.context.topic.title} · ${r.model} · ${Number(r.elapsed_seconds).toFixed(1)} 秒 · 已保存 #${r.id}`;$('review-note').textContent=(r.citation_check.review_warning ? r.citation_check.review_warning+' ' : '')+(r.citation_check.has_citations?'引用编号已核对。':'本次未检测到材料引用，请重点检查。')+' 内容正确性与引用支持关系仍需教师复核。';drawSources(r.context.materials);$('download').disabled=false;$('download-format').disabled=false;$('print-lecture').disabled=false;updateDownloadLabel();if(typeof showProfileComparison==='function')showProfileComparison(r);if(typeof showLectureComparison==='function')showLectureComparison(r);if(typeof showLectureTrace==='function')showLectureTrace(r);if(refreshReview&&typeof loadLectureReview==='function')loadLectureReview(r);}
function drawHistory(history){$('history').replaceChildren();if(!history.length){$('history').append(el('p','暂无已保存讲义','helper'));return;}for(const h of history){const b=el('button',`#${h.id} ${h.name} · ${h.topic}`);b.type='button';b.disabled=busy;b.onclick=async()=>{if(busy)return;error('');const version=++contextVersion;try{const r=await api('/api/lectures/'+h.id);if(version!==contextVersion)return;const key=Object.keys(topics).find(k=>topics[k].title===r.topic);if(key)$('topic').value=key;filterStudents(r.student_id);$('personalization-mode').value=r.context.personalization_mode||'profile';$('retrieval-mode').value=(r.context.retrieval||{}).mode||'sql';$('goal').value=r.goal;drawContext(r.context,true);showResult(r);$('generate').disabled=!modelConfigured||!hasGenerationBasis(current);}catch(e){if(version===contextVersion)error(e.message);}};$('history').append(b);}}
async function refreshHistory(){const d=await api('/api/bootstrap');drawHistory(d.history);if(typeof refreshRuns==='function')await refreshRuns();}
function setBusy(value){busy=value;document.querySelectorAll('#visual-evidence-options input').forEach(n=>n.disabled=value);for(const id of ['student','topic','goal','personalization-mode','retrieval-mode','learner-note'])$(id).disabled=value;$('generate').disabled=value||!modelConfigured||!hasGenerationBasis(current);$('learner-note').disabled=value||$('personalization-mode').value==='none';$('edit-profile').disabled=value||!window.selectedProfileStudentId;if(typeof setReviewBusy==='function')setReviewBusy(value);if(typeof setProfileComparisonBusy==='function')setProfileComparisonBusy(value);if(typeof setLectureComparisonBusy==='function')setLectureComparisonBusy(value);for(const b of $('history').querySelectorAll('button'))b.disabled=value;$('generate').replaceChildren(document.createTextNode(value?'正在生成…':'生成个性化讲义'),el('span',value?'':'↗'));}
function filterStudents(preferred){
 const ids=topics[$('topic').value]?.student_ids;
 const selected=preferred||Number($('student').value);
 const available=students.filter(s=>!ids||ids.includes(s.id));
 $('student').replaceChildren(...available.map(s=>{const o=el('option',s.name+' · '+s.subtitle);o.value=s.id;return o;}));
 if(available.some(s=>s.id===selected))$('student').value=String(selected);
}
$('student').onchange=()=>{$('learner-note').value='';loadContext();};
$('topic').onchange=()=>{$('learner-note').value='';filterStudents();$('goal').value=topics[$('topic').value].goal;loadContext();};
$('personalization-mode').onchange=()=>{$('learner-note').disabled=$('personalization-mode').value==='none';loadContext();};
$('retrieval-mode').onchange=loadContext;
function lectureDownloadUrl(format){return '/api/lectures/'+lastResult.id+(lastResult.revision_number?'/revisions/'+lastResult.revision_number:'')+'/download?format='+encodeURIComponent(format);}
function updateDownloadLabel(){if(!lastResult){$('download').textContent='下载原稿';return;}$('download').textContent=(lastResult.revision_number?'下载 R'+lastResult.revision_number:'下载原稿')+' '+$('download-format').selectedOptions[0].textContent;}
$('download-format').onchange=updateDownloadLabel;
$('download').onclick=()=>{if(!lastResult)return;const format=$('download-format').value;const a=el('a');a.href=lectureDownloadUrl(format);a.download='lecture-'+lastResult.id+(lastResult.revision_number?'-R'+lastResult.revision_number:'')+'.'+format;document.body.append(a);a.click();a.remove();};
$('print-lecture').onclick=()=>{if(lastResult)window.print();};
(async()=>{try{const d=await api('/api/bootstrap');topics=d.topics;students=d.students;modelConfigured=d.configured;$('model-label').textContent=d.model;$('student').replaceChildren(...d.students.map(s=>{const o=el('option',s.name+' · '+s.subtitle);o.value=s.id;return o;}));$('topic').replaceChildren(...Object.entries(topics).map(([key,t])=>{const o=el('option',t.custom ? [t.stage,t.course,t.chapter,t.title].filter(Boolean).join(' / ') : t.title);o.value=key;return o;}));$('topic').value='linear';filterStudents();$('goal').value=topics.linear.goal;$('student').disabled=false;$('topic').disabled=false;drawHistory(d.history);await loadContext();if(!d.configured){error('请先在本地 .env 配置模型密钥。');$('generate').disabled=true;}}catch(e){error(e.message);}})();
