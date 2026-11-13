const $ = id => document.getElementById(id);
let students = [], topics = {}, current = null, lastResult = null, busy = false, contextVersion = 0, modelConfigured = false, historicalSettingsValid = true;
const el = (tag, text, cls) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; };
function error(message){$('error').textContent=message||'';$('error').hidden=!message;}
function hasGenerationBasis(ctx){return historicalSettingsValid && Boolean(ctx && (ctx.materials.length || ctx.topic.preparation_copy));}
function restoreLectureSettings(r){
 const key=r.topic_key||r.context.topic.key;
 const matches=Object.keys(topics).filter(k=>topics[k].title===r.topic);
 const resolved=key&&topics[key]?key:!key&&matches.length===1?matches[0]:null;
 historicalSettingsValid=Boolean(resolved);
 const who=r.student_id==null&&r.class_id!=null?'class:'+r.class_id:r.student_id;
 if(resolved){followLectureScope(resolved,r);drawTopics(resolved);filterStudents(who);}
 else{$('topic').value='';error('历史讲义对应的课程无法唯一确定。请手动选择当前备课副本后再生成；历史正文与输入仍可阅读。');}
 $('personalization-mode').value=r.context.personalization_mode||'profile';$('retrieval-mode').value=r.context.retrieval?.mode||'sql';if(r.context.lecture_pipeline){$('generation-mode').value=r.context.generation_mode||'single';$('lecture-format').value=r.context.lecture_format||'plain';}$('goal').value=r.goal;
 drawContext(r.context,true);return historicalSettingsValid;
}
function drawPreparation(ctx){
 $('preparation-preview').hidden=!ctx.topic.preparation_copy;
 $('preparation-preview-goal').textContent='副本目标：'+ctx.topic.goal;
 $('preparation-preview-plan').textContent=ctx.topic.lesson_plan||'未填写教案正文，按教学目标组织讲义。';
}
// Course and class scope (context.js): the workbench lists the current course's lessons, and
// the lecture target is either the whole current class or one of its students.
const topicLabel=t=>t.custom?([t.chapter,t.title].filter(Boolean).join(' / ')||'未命名备课'):t.title;
function scopedTopicKeys(){const id=window.workspaceScope?.courseId();const keys=Object.keys(topics);return id==null?keys:keys.filter(k=>topics[k].course_id===id);}
function drawTopics(preferred){
 const keys=scopedTopicKeys(),key=preferred||$('topic').value;
 $('topic').replaceChildren(...(keys.length?keys.map(k=>{const o=el('option',(topics[k].custom?'':'示例 · ')+topicLabel(topics[k]));o.value=k;return o;}):[el('option','本课程还没有备课')]));
 if(keys.length)$('topic').value=keys.includes(key)?key:keys[0];
 return keys.length>0;
}
function lectureTarget(){const v=$('student').value;return v.startsWith('class:')?{class_id:Number(v.slice(6))}:{student_id:Number(v)};}
window.lectureTarget=lectureTarget;
function targetQuery(){const t=lectureTarget();return t.class_id!=null?'class_id='+t.class_id:'student_id='+t.student_id;}
function followLectureScope(key,r){
 // Opening a saved lecture from another course or class switches the shared scope to it, quietly.
 const scope=window.workspaceScope,course=topics[key]?.course_id;if(!scope||course==null)return;
 const cls=r.class_id!=null&&r.student_id==null?r.class_id:scope.classOf(r.student_id,course);
 if(course!==scope.courseId()||(cls!=null&&cls!==scope.classId()))scope.set(course,cls??null,'workbench');
}
async function refreshWorkspaceCatalog(preferredTopic, preferredStudent){
 const d=await api('/api/bootstrap');const key=preferredTopic||$('topic').value;const sid=preferredStudent||$('student').value;
 topics=d.topics;students=d.students;
 drawTopics(key);filterStudents(sid);drawHistory(d.history);window.dispatchEvent(new Event('workspace-context'));return d;
}
// Uploaded sources link to the stored file; a PDF segment opens at its first cited page.
function sourceHref(url){if(!url.startsWith('upload:'))return url;const [id,part]=url.slice(7).split('#');const page=/^p(\d+)-/.exec(part||'');return '/api/uploads/'+encodeURIComponent(id)+'/download'+(page?'?inline=1#page='+page[1]:'');}
async function api(url, options){const r=await fetch(url, options);let d;try{d=await r.json();}catch{throw new Error('服务未返回有效响应，请确认本地服务仍在运行。');}if(!r.ok){const err=new Error(d.error||'请求失败，请重试。');err.status=r.status;err.runId=d.generation_run_id;throw err;}return d;}
function drawContext(ctx, snapshot=false){
 current=ctx; if(typeof drawLecturePlan==='function')drawLecturePlan(ctx,snapshot); $('student-profile').textContent=ctx.student.profile; drawPreparation(ctx); if(snapshot){if(typeof restoreVisualSelection==='function')restoreVisualSelection(ctx.visual_evidence||[]);if(typeof restoreTeachingOptions==='function')restoreTeachingOptions({...ctx.teaching_options,requirements:[...new Set([ctx.teaching_options?.requirements,ctx.learner_note].filter(Boolean))].join('\n\n')});$('retrieval-note').textContent=ctx.retrieval?.mode==='vector'?'本讲义按目标检索材料片段；再次生成会检查并更新索引。':'直接读取当前知识点的全部关联材料。';}

 $('task-level').textContent=(ctx.topic.task_level||'当前任务')+'；课程任务与掌握状态分别设置。';
 window.dispatchEvent(new Event("workspace-context"));
 drawSources(ctx.materials);
}
function drawSources(materials){$('source-count').textContent=materials.length+' 条材料';$('sources').replaceChildren(...materials.map(m=>{const d=el('details',undefined,'source');const s=el('summary');s.append(el('span','M'+m.id),document.createTextNode(m.title));const a=el('a',m.source_title+' · 查看原始资料 ↗');a.href=sourceHref(m.source_url);a.target='_blank';a.rel='noopener noreferrer';d.append(s,el('p',m.content),a,el('p',m.section,'source-location'));if(m.chunk_count)d.append(el('p',`本次由向量检索取到 ${m.chunk_count} 个片段，最高相似度 ${Number(m.max_score).toFixed(3)}；相似度高不代表该片段支持对应表述。`,'source-location'));return d;}));}
function markResult(on){$('result-paper').classList[on?'add':'remove']('has-result');$('workflow-strip').classList[on?'add':'remove']('has-result');}
function clearResult(){lastResult=null;markResult(false);window.dispatchEvent(new Event('lecture-version-changed'));if($('result-plan'))$('result-plan').hidden=true;$('adjust-section').disabled=true;$('download').disabled=true;$('download-format').disabled=true;$('print-lecture').disabled=true;$('empty').hidden=false;$('lecture').hidden=true;$('lecture-meta').hidden=true;$('review-note').hidden=true;if (typeof window.clearTopicNav === 'function') window.clearTopicNav();$('result-label').textContent='讲义预览';if(typeof clearLectureTrace==='function')clearLectureTrace();if(typeof clearLectureReview==='function')clearLectureReview();if(typeof clearProfileComparison==='function')clearProfileComparison();if(typeof clearLectureComparison==='function')clearLectureComparison();}
async function loadContext(){historicalSettingsValid=true;const version=++contextVersion;current=null;error('');$('generate').disabled=true;clearResult();const vector=$('retrieval-mode').value==='vector';$('retrieval-note').textContent=vector?'正在检查并更新资料索引，首次准备可能需要一些时间…':'精确 SQL 直接读取材料，无需向量索引。';if(!topics[$('topic').value]||!$('student').value){$('student-profile').textContent='';$('retrieval-note').textContent=topics[$('topic').value]?'当前班级还没有可选学生，请在「班级与画像」中添加。':'当前课程还没有备课，请在「课程」中新建或复制示例。';return;}try{const c=await api(`/api/context?${targetQuery()}&topic=${$('topic').value}&personalization_mode=${$('personalization-mode').value}&retrieval_mode=${$('retrieval-mode').value}&goal=${encodeURIComponent($('goal').value)}`);if(version!==contextVersion)return;drawContext(c);$('generate').disabled=!modelConfigured||!hasGenerationBasis(c);if(!hasGenerationBasis(c))$('retrieval-note').textContent='当前备课尚无课程资料，请到「课程 → 课程资料」上传并收录后生成。';else if(vector)loadRetrievalStatus(version);}catch(e){if(version===contextVersion){error(e.message);$('retrieval-note').textContent=vector?'资料准备或检索失败：'+e.message:'材料读取失败：'+e.message;}}}
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
// Formulas go through KaTeX's DOM builder and figures arrive as server-drawn SVG images,
// so neither path can inject markup either.
function mathNode(tex, display){
 const node=el(display?'div':'span',tex,display?'math-display':'math-inline');
 node.dataset.tex=tex;
 node.setAttribute('aria-label','公式：'+tex);
 if(typeof katex!=='undefined'){
   try{
     node.textContent='';
     katex.render(tex,node,{displayMode:display,throwOnError:false,strict:'ignore',output:'html'});
   }catch{
     node.textContent=tex;
     node.className+=' math-failed';
   }
 }
 
 // 新增：将公式设为可放大（通常是块级公式需要放大，行内公式可选）
 if (display) {
   node.classList.add('zoomable');
   node.title = '点击放大公式';
   node.onclick = () => openLightbox(node.cloneNode(true));
 }
 
 return node;
}
function inlinePlain(node,text,lineNumber,interactive){const parts=text.split(/(\*\*[^*]+\*\*|\[[MVK]\d+\])/g);for(const p of parts){if(p.startsWith('**')&&p.endsWith('**'))node.append(el('strong',p.slice(2,-2)));else if(/^\[[MV]\d+\]$/.test(p)){if(!interactive){node.append(el('span',p,'citation'));continue;}const b=el('button',p,'citation');b.type='button';b.setAttribute('aria-label','查看来源 '+p);b.onclick=()=>{if(typeof openCitationSource==='function')openCitationSource(p.slice(1,-1),lineNumber);};node.append(b);}else if(/^\[K\d+\]$/.test(p)){const mark=el('span',p,'plan-marker');mark.dataset.planId=p.slice(2,-1);mark.title='讲解安排定位，需教师核对是否落实';node.append(mark);}else node.append(document.createTextNode(p));}}
function inline(node,text,lineNumber,interactive=false){
 if(!text.includes('$')){inlinePlain(node,text,lineNumber,interactive);return;}
 for(const p of text.split(/(\$\$[^$]+\$\$|(?<!\\)\$(?:\\.|[^$\\])+?\$)/g)){
  if(!p)continue;
  // A dollar sign next to a space is money or prose, not a formula delimiter.
  if(p.length>4&&p.startsWith('$$')&&p.endsWith('$$'))node.append(mathNode(p.slice(2,-2).trim(),false));
  else if(p.length>2&&p.startsWith('$')&&p.endsWith('$')&&!/^\$\s|\s\$$/.test(p))node.append(mathNode(p.slice(1,-1),false));
  else inlinePlain(node,p.replace(/\\\$/g,'$'),lineNumber,interactive);
 }
}
const figureCache=new Map();let figureQueue=[];
function drawFigure(node,result){
 node.replaceChildren();
 if(!result||!result.ok){
   node.append(el('p','这幅图未能绘制：'+(result?result.error:'绘图服务没有响应。'),'check-warning'));
   return;
 }
 const image=el('img');
 image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(result.svg);
 image.alt=result.title+'：'+result.alt;
 image.width=result.width;
 image.height=result.height;
 
 // 新增：给图片本身加上放大功能
 image.classList.add('zoomable');
 image.title = '点击放大查看图表';
 image.onclick = () => openLightbox(image.cloneNode(true));

 node.append(image,el('p',result.title+'（由程序按讲义中的数据绘制）','figure-caption'));
 for(const warning of result.warnings)node.append(el('p','图中数据需核对：'+warning,'check-warning'));
}
async function flushFigures(){
 const batch=figureQueue;figureQueue=[];if(!batch.length)return;
 const sources=[...new Set(batch.map(item=>item.source))].slice(0,12);
 try{const data=await api('/api/figures/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({figures:sources})});sources.forEach((source,i)=>figureCache.set(source,data.figures[i]));}catch{}
 for(const item of batch)drawFigure(item.node,figureCache.get(item.source));
}
function figureNode(source,complete=true){
 const node=el('div',undefined,'figure-block');
 if(!complete){node.append(el('p','图的数据正在写入…','helper'));return node;}
 if(figureCache.has(source)){drawFigure(node,figureCache.get(source));return node;}
 node.append(el('p','正在绘制图…','helper'));figureQueue.push({node,source});setTimeout(flushFigures,0);return node;
}
function tableNode(rows,interactive){
 const cells=r=>r.line.slice(1,-1).split('|').map(c=>c.trim());
 const wrap=el('div',undefined,'table-wrap'),table=el('table'),hasHead=rows.length>1&&cells(rows[1]).every(c=>/^:?-{2,}:?$/.test(c));
 rows.forEach((row,i)=>{if(hasHead&&i===1)return;const tr=el('tr');for(const value of cells(row)){const cell=el(hasHead&&i===0?'th':'td');inline(cell,value,row.index,interactive);tr.append(cell);}table.append(tr);});
 wrap.append(table);return wrap;
}
function renderMarkdown(text,interactive=false){
 const frag=document.createDocumentFragment();let list=null,fence=null,pre=null,figure=null,math=null,table=null;
 const flushTable=()=>{if(table){frag.append(tableNode(table,interactive));table=null;}};
 text.split('\n').forEach((raw,index)=>{const line=raw.trim();
  if(figure){if(line.startsWith('```')){frag.append(figureNode(figure.join('\n').trim()));figure=null;}else figure.push(raw);return;}
  if(math){if(line.endsWith('$$')){math.push(line.slice(0,-2));frag.append(mathNode(math.join('\n').trim(),true));math=null;}else math.push(raw);return;}
  if(!fence&&/^```figure\s*$/.test(line)){list=null;flushTable();figure=[];return;}
  if(/^(```|~~~)/.test(line)){const token=line.slice(0,3);if(fence===token){fence=null;pre=null;}else if(!fence){list=null;flushTable();fence=token;pre=el('pre',undefined,'formula-block');frag.append(pre);}else pre.append(document.createTextNode(raw+'\n'));return;}
  if(fence){pre.append(document.createTextNode(raw+'\n'));return;}
  if(!line){list=null;flushTable();return;}
  if(line.startsWith('$$')){list=null;flushTable();const rest=line.slice(2);if(rest.length>2&&rest.endsWith('$$'))frag.append(mathNode(rest.slice(0,-2).trim(),true));else math=[rest];return;}
  if(line.length>2&&line.startsWith('|')&&line.endsWith('|')){list=null;(table=table||[]).push({line,index});return;}
  flushTable();
  const heading=line.match(/^(#{1,3})\s+(.+)$/),item=line.match(/^([-*]|\d+[.、])\s+(.+)$/);
  if(heading){list=null;const h=el('h'+heading[1].length);h.dataset.sectionTitle=heading[2];inline(h,heading[2],index,interactive);frag.append(h);}
  else if(item){const tag=/\d/.test(item[1])?'ol':'ul';if(!list||list.tagName.toLowerCase()!==tag){list=el(tag);frag.append(list);}const li=el('li');if(tag==='ol')li.value=parseInt(item[1],10);inline(li,item[2],index,interactive);list.append(li);}
  else{list=null;const p=el('p');if(/^(?:P\(.+\)\s*=|[\wα-ωΑ-Ω]+\s*[=≈≤≥]|\\\[)/.test(line)){p.className='math-line';p.setAttribute('aria-label','公式：'+line);}inline(p,line,index,interactive);frag.append(p);}
 });
 // Text that is still arriving may end inside a block; show what is there without guessing the rest.
 flushTable();if(math)frag.append(el('p','$$'+math.join('\n'),'math-line'));if(figure)frag.append(figureNode('',false));
 return frag;
}
function revealResult(){const paper=$('result-paper');if(typeof paper.getBoundingClientRect!=='function'||!(window.innerWidth<1200))return;const top=paper.getBoundingClientRect().top;if(top>window.innerHeight*.6)paper.scrollIntoView({block:'start'});}
function showResult(r, refreshReview=true, navigate=true){if(navigate&&typeof openWorkspacePage==="function")openWorkspacePage("workbench");lastResult=r;markResult(true);window.dispatchEvent(new Event('lecture-version-changed'));if(typeof drawResultPlan==='function')drawResultPlan(r);$('adjust-section').disabled=busy;$('empty').hidden=true;$('loading').hidden=true;$('lecture').hidden=false;$('lecture-meta').hidden=false;$('review-note').hidden=false;$('lecture').replaceChildren(renderMarkdown(r.content,true));if (typeof window.annotateTopicNav === 'function') window.annotateTopicNav($('lecture'));if(typeof drawVisualEvidence==='function')drawVisualEvidence(r.context.visual_evidence);$('result-label').textContent=r.context.personalization_mode==='none'?'无画像对照讲义':r.context.student.name+'的讲义';$('lecture-meta').textContent=`${r.context.topic.title} · ${r.model} · ${Number(r.elapsed_seconds).toFixed(1)} 秒 · 已保存 #${r.id}`;$('review-note').textContent=(r.citation_check.review_warning ? r.citation_check.review_warning+' ' : '')+(r.citation_check.has_citations?'引用编号已核对。':'本次未检测到材料引用，请重点检查。')+' 内容正确性与引用支持关系仍需教师复核。';drawSources(r.context.materials);$('download').disabled=false;$('download-format').disabled=false;$('print-lecture').disabled=false;updateDownloadLabel();if(typeof showProfileComparison==='function')showProfileComparison(r);if(typeof showLectureComparison==='function')showLectureComparison(r);if(typeof showLectureTrace==='function')showLectureTrace(r);if(refreshReview&&typeof loadLectureReview==='function')loadLectureReview(r);if(navigate)revealResult();}
function drawHistory(history){$('history').replaceChildren();if(!history.length){$('history').append(el('p','暂无已保存讲义','helper'));return;}const open=h=>async()=>{if(busy)return;error('');const version=++contextVersion;try{const r=await api('/api/lectures/'+h.id);if(version!==contextVersion)return;restoreLectureSettings(r);showResult(r);$('generate').disabled=!modelConfigured||!hasGenerationBasis(current);}catch(e){if(version===contextVersion)error(e.message);}};
 for(const h of history){const b=el('button',`#${h.id} ${h.name} · ${h.topic}`);b.type='button';b.disabled=busy;if(h.created_at)b.append(el('span',new Date(h.created_at).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}),'history-date'));b.onclick=open(h);$('history').append(b);}
 $('empty-recent').hidden=!history.length;$('empty-recent-list').replaceChildren(...history.slice(0,3).map(h=>{const b=el('button',`#${h.id} ${h.name} · ${h.topic}`,'secondary');b.type='button';b.disabled=busy;b.onclick=open(h);return b;}));}
async function refreshHistory(){const d=await api('/api/bootstrap');drawHistory(d.history);if(typeof refreshRuns==='function')await refreshRuns();}
function setBusy(value){busy=value;window.dispatchEvent(new Event('lecture-version-changed'));if(typeof setTeachingBusy==='function')setTeachingBusy(value);if(typeof setLecturePlanBusy==='function')setLecturePlanBusy(value);document.querySelectorAll('#visual-evidence-options input').forEach(n=>n.disabled=value);for(const id of ['student','topic','goal','personalization-mode','retrieval-mode','generation-mode','lecture-format','batch-open'])$(id).disabled=value;$('generate').disabled=value||!modelConfigured||!hasGenerationBasis(current);$('edit-profile').disabled=value||!window.selectedProfileStudentId;if(typeof setReviewBusy==='function')setReviewBusy(value);if(typeof setProfileComparisonBusy==='function')setProfileComparisonBusy(value);if(typeof setLectureComparisonBusy==='function')setLectureComparisonBusy(value);for(const b of [...$('history').querySelectorAll('button'),...$('empty-recent-list').querySelectorAll('button')])b.disabled=value;$('generate').replaceChildren(document.createTextNode(value?'正在生成…':'生成个性化讲义'),el('span',value?'':'↗'));}
function filterStudents(preferred){
 const topic=topics[$('topic').value],ids=topic?.student_ids;
 const selected=String(preferred||$('student').value||'');
 const scope=window.workspaceScope,cls=scope?.klass(),inCourse=cls&&topic&&topic.course_id===scope.courseId();
 // In a class: the whole class first, then its students. Without one: every student of the lesson.
 const members=inCourse?new Set(cls.student_ids):null;
 const available=students.filter(s=>(!ids||ids.includes(s.id))&&(!members||members.has(s.id)||String(s.id)===selected));
 const options=available.map(s=>{const o=el('option',s.name+' · '+s.subtitle+(members&&!members.has(s.id)?'（不在当前班级）':''));o.value=s.id;return o;});
 if(inCourse&&cls.student_ids.length){const o=el('option',`全班 · ${cls.name}（${cls.student_ids.length} 人，按班级画像）`);o.value='class:'+cls.id;options.unshift(o);}
 $('student').replaceChildren(...options);
 if(options.some(o=>o.value===selected))$('student').value=selected;
 $('student-label').textContent=inCourse?'讲义对象 · 全班或学生':'选择学生';
}
$('student').onchange=()=>{if(typeof restoreVisualSelection==='function')restoreVisualSelection([]);loadContext();};
$('topic').onchange=()=>{if(typeof restoreVisualSelection==='function')restoreVisualSelection([]);filterStudents();$('goal').value=topics[$('topic').value].goal;loadContext();};
let scopePending=false;
function applyScope(event){
 // Never swap the lesson under a running generation; apply the newer scope once it finishes.
 if(event?.detail==='workbench'||!Object.keys(topics).length)return;
 if(busy){scopePending=true;return;}
 scopePending=false;const before=$('topic').value,who=$('student').value;
 if(!drawTopics()){filterStudents();loadContext();return;}
 filterStudents();const moved=$('topic').value!==before||$('student').value!==who;
 if($('topic').value!==before)$('goal').value=topics[$('topic').value].goal;
 // A plain data refresh keeps the open context unless the selection or the class itself changed.
 if(event?.detail==='refresh'&&!moved&&!who.startsWith('class:'))return;
 if(typeof restoreVisualSelection==='function')restoreVisualSelection([]);loadContext();
}
window.addEventListener('scope-changed',applyScope);
window.addEventListener('lecture-version-changed',()=>{if(scopePending&&!busy)applyScope();});
$('personalization-mode').onchange=()=>{loadContext();};
// Profile page shortcut: keep the current course when it includes the student, otherwise the first that does.
window.prepareLectureFor=target=>{
 // target: a student id, or 'class:<id>' for the whole current class.
 if(busy)return false;const keys=scopedTopicKeys(),isClass=String(target).startsWith('class:');
 const fits=k=>isClass||topics[k]?.student_ids?.includes(target);
 const key=keys.includes($('topic').value)&&fits($('topic').value)?$('topic').value:keys.find(fits);if(!key)return false;
 if(key!==$('topic').value){$('topic').value=key;$('goal').value=topics[key].goal;}
 filterStudents(String(target));if($('student').value!==String(target))return false;
 if(typeof restoreVisualSelection==='function')restoreVisualSelection([]);openWorkspacePage('workbench');loadContext();return true;};
$('retrieval-mode').onchange=loadContext;
function lectureDownloadUrl(format){return '/api/lectures/'+lastResult.id+(lastResult.revision_number?'/revisions/'+lastResult.revision_number:'')+'/download?format='+encodeURIComponent(format);}
function updateDownloadLabel(){if(!lastResult){$('download').textContent='下载原稿';return;}$('download').textContent=(lastResult.revision_number?'下载 R'+lastResult.revision_number:'下载原稿')+' '+$('download-format').selectedOptions[0].textContent;}
$('download-format').onchange=updateDownloadLabel;
$('download').onclick=()=>{if(!lastResult)return;const format=$('download-format').value;const a=el('a');a.href=lectureDownloadUrl(format);a.download='lecture-'+lastResult.id+(lastResult.revision_number?'-R'+lastResult.revision_number:'')+'.'+format;document.body.append(a);a.click();a.remove();};
$('print-lecture').onclick=()=>{if(lastResult)window.print();};
(async()=>{try{const d=await api('/api/bootstrap');topics=d.topics;students=d.students;modelConfigured=d.configured;
 // Wait for the course and class scope so the first lesson shown already belongs to it.
 if(window.workspaceScope)await window.workspaceScope.ready.catch(()=>{});
 const first=drawTopics(topics.linear&&scopedTopicKeys().includes('linear')?'linear':undefined);filterStudents();if(first)$('goal').value=topics[$('topic').value].goal;
 $('student').disabled=false;$('topic').disabled=false;drawHistory(d.history);await loadContext();if(!d.configured){error('请先在系统设置中配置语言模型地址和名称。');$('generate').disabled=true;}}catch(e){error(e.message);}})();

// ==================== 一键放大 + 滚轮缩放 + 拖拽 ====================
let lightboxState = {
  scale: 1,
  translateX: 0,
  translateY: 0,
  isDragging: false,
  startX: 0,
  startY: 0
};

function updateLightboxTransform() {
  const target = document.querySelector('.lightbox-scale-target');
  if (target) {
    lightboxState.scale = Math.max(0.2, Math.min(10, lightboxState.scale)); // 扩大了缩放上限
    target.style.transform = `translate(${lightboxState.translateX}px, ${lightboxState.translateY}px) scale(${lightboxState.scale})`;
  }
}

function openLightbox(contentNode) {
  const lightbox = document.getElementById('image-lightbox');
  const container = document.getElementById('lightbox-content');
  if (!lightbox || !container) return;

  // 重置状态
  lightboxState = { scale: 1, translateX: 0, translateY: 0, isDragging: false, startX: 0, startY: 0 };

  const scaleTarget = document.createElement('div');
  scaleTarget.className = 'lightbox-scale-target';
  scaleTarget.appendChild(contentNode);
  
  container.replaceChildren(scaleTarget);
  
  // 移除旧的 toolbar（如果有）
  const oldToolbar = lightbox.querySelector('.lightbox-toolbar');
  if (oldToolbar) oldToolbar.remove();

  // 底部工具栏提示
  const toolbar = document.createElement('div');
  toolbar.className = 'lightbox-toolbar';
  toolbar.textContent = '滚轮缩放 · 按住拖拽平移 · 按 Esc 关闭';
  lightbox.appendChild(toolbar); // 直接添加到 lightbox 层，固定位置

  lightbox.hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeLightbox() {
  const lightbox = document.getElementById('image-lightbox');
  const container = document.getElementById('lightbox-content');
  if (!lightbox || !container) return;
  
  lightbox.hidden = true;
  container.replaceChildren();
  
  const toolbar = lightbox.querySelector('.lightbox-toolbar');
  if (toolbar) toolbar.remove();
  
  document.body.style.overflow = '';
}

// 绑定事件部分保持不变...
document.addEventListener('DOMContentLoaded', () => {
  const lightbox = document.getElementById('image-lightbox');
  const closeBtn = document.getElementById('lightbox-close');
  const container = document.getElementById('lightbox-content');
  
  if (closeBtn) closeBtn.addEventListener('click', closeLightbox);
  if (lightbox) {
    lightbox.querySelector('.lightbox-overlay').addEventListener('click', closeLightbox);
  }

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && lightbox && !lightbox.hidden) {
      closeLightbox();
    }
  });

  if (container) {
    // 滚轮
    container.addEventListener('wheel', (e) => {
      if (lightbox.hidden) return;
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.15 : 0.15;
      lightboxState.scale += delta;
      updateLightboxTransform();
    }, { passive: false });

    // 拖拽
    container.addEventListener('pointerdown', (e) => {
      if (lightbox.hidden) return;
      // 避免点到关闭按钮时触发拖拽
      if (e.target.closest('.lightbox-close')) return;
      
      lightboxState.isDragging = true;
      lightboxState.startX = e.clientX - lightboxState.translateX;
      lightboxState.startY = e.clientY - lightboxState.translateY;
      container.setPointerCapture(e.pointerId);
    });

    container.addEventListener('pointermove', (e) => {
      if (!lightboxState.isDragging) return;
      lightboxState.translateX = e.clientX - lightboxState.startX;
      lightboxState.translateY = e.clientY - lightboxState.startY;
      updateLightboxTransform();
    });

    const stopDrag = (e) => {
      if (lightboxState.isDragging) {
        lightboxState.isDragging = false;
        if (container.hasPointerCapture(e.pointerId)) {
          container.releasePointerCapture(e.pointerId);
        }
      }
    };
    container.addEventListener('pointerup', stopDrag);
    container.addEventListener('pointercancel', stopDrag);
  }
});
