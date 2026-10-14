(() => {
const categoryLabels = {math:'数学运算或条件', terminology:'术语', citation_support:'引用支持', scope:'目标或范围', structure:'结构或篇幅', other:'其他'};
const supportLabels = {not_applicable:'不适用', unreviewed:'尚未核查', supported:'支持', unsupported:'不支持', unclear:'依据不足或不确定'};
const statusLabels = {unreviewed:'原稿未审核', draft:'草稿', needs_revision:'待修订', reviewed:'教师已审核'};
let reviewLecture = null, lectureReviewData = null, selectedRevision = 0, reviewSession = 0;
let editorSession = 0, editorBase = 0, editorData = null, editorSaving = false, scopeSession = 0, scopeTimer;

function drawScope(scope) {
  if (!scope) return;
  const panel=$('scope-check').parentElement;
  panel.querySelector('summary').textContent='目标范围与材料覆盖'+(scope.warnings.length?` · ${scope.warnings.length} 项提醒`:'');
  if(scope.warnings.length)panel.open=true;
  $('scope-check').replaceChildren(el('p','共同默认目标：'+scope.default_goal,'helper'),el('p','本次目标：'+scope.goal,'helper'));
  for (const point of scope.coverage) $('scope-check').append(el('p',point.scope+' → '+(point.has_material?point.material_ids.map(id=>'M'+id).join('、'):'缺少关联材料'),'helper'));
  for (const warning of scope.warnings) $('scope-check').append(el('p',warning,'check-warning'));
  $('scope-check').append(el('p',scope.note,'helper'));
}
async function refreshScope() {
  const session = ++scopeSession;
  const sid=$('student').value, topic=$('topic').value, goal=$('goal').value;
  if (!sid || !topic) return;
  try {
    const ctx=await api('/api/context?'+new URLSearchParams({student_id:sid,topic,goal}));
    if (session===scopeSession && sid===$('student').value && topic===$('topic').value && goal===$('goal').value) drawScope(ctx.scope_check);
  } catch (err) { if(session===scopeSession)$('scope-check').replaceChildren(el('p',err.message,'check-warning')); }
}
$('goal').addEventListener('input',()=>{clearTimeout(scopeTimer);scopeSession++;$('scope-check').replaceChildren(el('p','正在核查本次目标…','helper'));scopeTimer=setTimeout(refreshScope,300);});

function drawQuality(report, origin, teacherRecord=null) {
  $('quality-panel').hidden=!report;
  if (!report) return;
  const warnings=report.automatic.filter(c=>c.status==='warning').length;
  $('quality-version').textContent=report.version;
  $('quality-summary').textContent=`${origin || '当前文本检查'} · 程序提示 ${warnings} 项；`+(teacherRecord?.status==='reviewed'?'该版本保存了教师审核判断，程序结果不替代该判断。':'数学、来源支持与教学调整仍待教师复核。');
  $('automatic-checks').replaceChildren(...report.automatic.map(c=>{
    const box=el('div',undefined,'check-item '+(c.status==='warning'?'check-warning':''));
    box.append(el('strong',(c.status==='pass'?'已检查 · ':'需查看 · ')+c.label),el('p',c.detail,'helper'));return box;
  }));
  $('teacher-checks-title').textContent=teacherRecord?'教师复核记录（保存时）':'待教师复核';
  $('teacher-checks').replaceChildren(...report.teacher_review.map(c=>el('p',(teacherRecord?.checklist[c.code]?'保存时已确认 · ':'待复核 · ')+c.label,'helper')));
  $('citation-checks').replaceChildren();
  for (const c of report.citation_locations) {
    const box=el('div',undefined,'history-card');box.append(el('strong',`正文第 ${c.line} 行`),el('p',c.excerpt,'helper'));
    for(const id of new Set(c.material_ids)) {
      const m=reviewLecture?.context.materials.find(m=>String(m.id)===id);
      box.append(el('p',m?`M${id} · ${m.title}\n${m.content}`:`M${id} 不在材料快照中。`,'current-summary'));
    }
    $('citation-checks').append(box);
  }
  if(!report.citation_locations.length)$('citation-checks').append(el('p','未检测到引用标记。','helper'));
}
function issueCards(issues, resolutions, container) {
  container.replaceChildren();
  for(const issue of issues){
    const card=el('details',undefined,'history-card');
    const source=issue.target_revision===0?'原稿':`R${issue.target_revision}`;
    card.append(el('summary',`问题 #${issue.id} · ${categoryLabels[issue.category]} · ${source} 第 ${issue.start_line}—${issue.end_line} 行`),el('p',issue.excerpt,'current-summary'),el('p',issue.detail,'helper'));
    if(issue.material_id)card.append(el('p',`M${issue.material_id} 引用判断：${supportLabels[issue.support]}（记录者判断）`,'helper'));
    const handled=resolutions?.find(r=>r.issue_id===issue.id);
    card.append(el('p',handled?'此版本的处理说明：'+handled.note:'此版本没有记录该问题的处理说明。','helper'),el('p',`${issue.source==='feedback'?'回复稿问题核对':'教师记录'} · ${issue.recorder} · ${new Date(issue.created_at).toLocaleString('zh-CN')}`,'helper'));
    container.append(card);
  }
  if(!issues.length)container.append(el('p','尚无具体问题记录；不表示讲义已通过审核。','helper'));
}
function clearLectureReview(){
  reviewSession++;scopeSession++;reviewLecture=null;lectureReviewData=null;selectedRevision=0;
  $('edit-lecture').disabled=true;$('version-controls').hidden=true;$('quality-panel').hidden=true;
  updateDownloadLabel();
}
function setReviewBusy(value){$('edit-lecture').disabled=value||!lectureReviewData;$('lecture-version').disabled=value;}
function currentReviewVersion(){return selectedRevision===0?lectureReviewData?.original:lectureReviewData?.revisions.find(r=>r.revision_number===selectedRevision);}
function drawVersionControls(){
  const original=el('option','模型原稿 · 未审核');original.value='0';
  $('lecture-version').replaceChildren(original,...lectureReviewData.revisions.map(r=>{const o=el('option',`R${r.revision_number} · ${statusLabels[r.status]} · ${r.recorder}`);o.value=r.revision_number;return o;}));
  $('lecture-version').value=String(selectedRevision);$('version-controls').hidden=false;
  const version=currentReviewVersion();
  const pending=lectureReviewData.issues.filter(i=>!version?.resolutions?.some(r=>r.issue_id===i.id)).length;
  $('version-status').textContent=`${selectedRevision===0?'模型原稿':`教师修订 R${selectedRevision}`} · ${statusLabels[version?.status] || '未审核'}（保存时记录） · ${pending} 条问题未在此版本说明处理；修订不改写生成输入。`;
  issueCards(lectureReviewData.issues,version?.resolutions,$('lecture-issues'));
}
async function loadLectureReview(result){
  const session=++reviewSession;reviewLecture=result;selectedRevision=0;lectureReviewData=null;
  $('edit-lecture').disabled=true;$('version-controls').hidden=true;$('lecture-issues').replaceChildren(el('p','正在读取问题与版本…','helper'));
  drawQuality(result.quality_report || result.citation_check.quality_report,result.quality_report_origin || '生成时留存');
  drawScope((result.quality_report || result.citation_check.quality_report)?.scope);
  try{
    const data=await api(`/api/lectures/${result.id}/review`);
    if(session!==reviewSession)return;
    lectureReviewData=data;drawVersionControls();$('edit-lecture').disabled=busy;
  }catch(err){if(session===reviewSession)$('lecture-issues').replaceChildren(el('p',err.message+' 请重新打开讲义。','check-warning'));}
}
function showReviewVersion(number){
  selectedRevision=number;const version=currentReviewVersion();if(!version)return;
  const r={...reviewLecture,content:version.content,revision_number:number,review_status:version.status};
  if(number){r.quality_report=version.quality_report;r.quality_report_origin='修订保存时检查';r.citation_check={has_citations:!!version.quality_report.citation_locations.length,quality_report:version.quality_report};}
  showResult(r,false);drawQuality(r.quality_report||r.citation_check.quality_report,r.quality_report_origin||'生成时留存',number?version:null);drawVersionControls();
  updateDownloadLabel();
  if(number){$('lecture-meta').textContent=`教师修订 R${number} · ${statusLabels[version.status]}（保存时记录） · ${version.recorder} · ${new Date(version.created_at).toLocaleString('zh-CN')}`;$('review-note').textContent='审核状态和问题处理是记录者在该版本保存时的判断；程序检查不能证明内容正确，新增问题另行显示。';const trace=$('input-trace-note');trace.textContent+=' 当前正文为教师修订，生成输入对应模型原稿。';}
}
$('lecture-version').onchange=()=>showReviewVersion(Number($('lecture-version').value));

function editorError(message){$('lecture-editor-error').textContent=message||'';$('lecture-editor-error').hidden=!message;}
function setEditorBusy(value){editorSaving=value;for(const id of ['save-revision','save-issue','close-lecture-editor','reload-lecture-review'])$(id).disabled=value;}
function drawEditorHistory(){
  $('revision-history').replaceChildren();
  for(const r of editorData.revisions){
    const card=el('details',undefined,'history-card');card.append(el('summary',`R${r.revision_number} · ${statusLabels[r.status]} · 基于 ${r.base_revision?`R${r.base_revision}`:'原稿'}`),el('p',`${r.recorder} · ${new Date(r.created_at).toLocaleString('zh-CN')} · 自报用时 ${r.review_seconds===0?'未记录':r.review_seconds+' 秒'}`,'helper'),el('p','理由：'+r.reason,'helper'),el('p',`新增 ${r.changes.added_lines} 行，移除或替换 ${r.changes.removed_lines} 行。`,'helper'));
    for(const c of r.changes.changed_blocks)card.append(el('pre',`原第 ${c.before_start} 行起：\n${c.before.join('\n') || '（空）'}\n\n修订第 ${c.after_start} 行起：\n${c.after.join('\n') || '（空）'}`,'trace-text'));
    const checked=Object.entries(editorData.review_items).filter(([key])=>r.checklist[key]).map(([,label])=>label);
    card.append(el('p','保存时确认：'+(checked.join('；')||'尚未确认检查项'),'helper'));
    for(const resolution of r.resolutions)card.append(el('p',`问题 #${resolution.issue_id} 处理说明：${resolution.note}`,'helper'));
    $('revision-history').append(card);
  }
  if(!editorData.revisions.length)$('revision-history').append(el('p','尚无修订。原稿另行保留。','helper'));
}
function drawResolutions(preserve=false){
  const previous=new Map();
  if(preserve)for(const box of $('revision-resolutions').querySelectorAll('.resolution-row'))previous.set(Number(box.dataset.issueId),{checked:box.querySelector('input').checked,note:box.querySelector('textarea').value});
  const base=editorBase===0?editorData.original:editorData.revisions.find(r=>r.revision_number===editorBase);
  $('revision-resolutions').replaceChildren();
  for(const issue of editorData.issues){
    const old=previous.get(issue.id), saved=base.resolutions?.find(r=>r.issue_id===issue.id);
    const box=el('div',undefined,'resolution-row history-card');box.dataset.issueId=issue.id;
    const label=el('label',undefined,'check-row');const checkbox=el('input');checkbox.type='checkbox';checkbox.checked=old?old.checked:false;
    label.append(checkbox,document.createTextNode(`已处理问题 #${issue.id} · ${categoryLabels[issue.category]}（对应 ${issue.target_revision?`R${issue.target_revision}`:'原稿'}）`));
    const note=el('textarea');note.rows=2;note.maxLength=800;note.value=old?old.note:saved?.note||'';note.setAttribute('aria-label',`问题 ${issue.id} 的处理理由`);
    box.append(label,el('p',issue.excerpt+'\n'+issue.detail,'helper'),note);$('revision-resolutions').append(box);
  }
  if(!editorData.issues.length)$('revision-resolutions').append(el('p','尚无具体问题记录。','helper'));
}
function drawEditor(preserve=false){
  const base=editorBase===0?editorData.original:editorData.revisions.find(r=>r.revision_number===editorBase);
  const newest=editorData.revisions[0]?.revision_number||0;
  $('lecture-editor-title').textContent=`编辑与审核 · 讲义 #${reviewLecture.id}`;
  $('lecture-editor-base').textContent=`本次基于 ${editorBase?`R${editorBase}`:'模型原稿'}；当前最新修订 ${newest?`R${newest}`:'尚无'}。另存新版本，保留原文与历史。`;
  $('issue-base-note').textContent=`问题位置对应已保存的 ${editorBase?`R${editorBase}`:'原稿'}；未保存的正文修改不能作为问题锚点。`;
  if(!preserve){
    $('revision-content').value=base.content;$('revision-status').value='draft';$('revision-recorder').value='';$('revision-reason').value='';$('revision-seconds').value='0';
    $('revision-checklist').replaceChildren(...Object.entries(editorData.review_items).map(([key,text])=>{const label=el('label',undefined,'check-row');const input=el('input');input.type='checkbox';input.dataset.checkKey=key;input.checked=false;label.append(input,document.createTextNode(text));return label;}));
    $('issue-form').reset();$('issue-material').replaceChildren(...reviewLecture.context.materials.map(m=>{const o=el('option',`M${m.id} · ${m.title}`);o.value=m.id;return o;}));
  }
  $('revision-confirmed').checked=false;$('issue-confirmed').checked=false;drawResolutions(preserve);drawEditorHistory();drawIssueMaterial();$('lecture-editor-content').hidden=false;
}
function drawIssueMaterial(){
  $('issue-source-fields').hidden=$('issue-category').value!=='citation_support';
  const m=reviewLecture?.context.materials.find(m=>m.id===Number($('issue-material').value));
  $('issue-material-content').textContent=m?m.content+'\n来源：'+m.source_title+' · '+m.section:'没有对应材料。';
}
$('issue-category').onchange=drawIssueMaterial;$('issue-material').onchange=drawIssueMaterial;
$('revision-content').addEventListener('input',()=>{for(const c of $('revision-checklist').querySelectorAll('input'))c.checked=false;$('revision-confirmed').checked=false;});
async function openLectureEditor(){
  if(!reviewLecture || busy)return;
  const session=++editorSession;editorBase=selectedRevision;editorData=null;editorError('');$('lecture-editor-content').hidden=true;$('lecture-editor-status').textContent='正在读取审核与问题记录…';$('lecture-editor').showModal();
  try{const data=await api(`/api/lectures/${reviewLecture.id}/review`);if(session!==editorSession||!$('lecture-editor').open)return;editorData=data;drawEditor();$('lecture-editor-status').textContent='填写修改理由与用时后另存。记录者为本地手填，尚无身份认证。';}catch(err){if(session===editorSession)editorError(err.message);}
}
$('edit-lecture').onclick=openLectureEditor;
$('close-lecture-editor').onclick=()=>{if(!editorSaving){editorSession++;$('lecture-editor').close();}};
$('lecture-editor').addEventListener('cancel',e=>{if(editorSaving)e.preventDefault();else editorSession++;});
$('reload-lecture-review').onclick=async()=>{if(!reviewLecture||editorSaving)return;setEditorBusy(true);editorError('');try{editorData=await api(`/api/lectures/${reviewLecture.id}/review`);drawEditor(true);$('lecture-editor-status').textContent='已重新读取，正文和填写内容保留；请核对新增记录，并重新确认。';}catch(err){editorError(err.message);}finally{setEditorBusy(false);}};
$('revision-form').onsubmit=async e=>{
  e.preventDefault();if(editorSaving||!editorData)return;editorError('');setEditorBusy(true);
  const checklist=Object.fromEntries([...$('revision-checklist').querySelectorAll('input')].map(c=>[c.dataset.checkKey,c.checked]));
  const resolutions=[...$('revision-resolutions').querySelectorAll('.resolution-row')].filter(c=>c.querySelector('input').checked).map(c=>({issue_id:Number(c.dataset.issueId),note:c.querySelector('textarea').value}));
  try{
    const data=await api(`/api/lectures/${reviewLecture.id}/revisions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:editorData.expected_revision,base_revision:editorBase,content:$('revision-content').value,status:$('revision-status').value,recorder:$('revision-recorder').value,reason:$('revision-reason').value,review_seconds:Number($('revision-seconds').value),checklist,resolutions,confirmed:$('revision-confirmed').checked})});
    lectureReviewData=data;editorData=data;editorBase=data.saved_revision;showReviewVersion(editorBase);drawEditor(true);$('lecture-editor-status').textContent=`已另存 R${editorBase}，原稿未改动；检查与修改位置已留存。`;
  }catch(err){editorError(err.message);$('revision-confirmed').checked=false;}finally{setEditorBusy(false);}
};
$('issue-form').onsubmit=async e=>{
  e.preventDefault();if(editorSaving||!editorData)return;editorError('');setEditorBusy(true);
  const citation=$('issue-category').value==='citation_support';
  try{
    const data=await api(`/api/lectures/${reviewLecture.id}/issues`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:editorData.expected_revision,target_revision:editorBase,category:$('issue-category').value,excerpt:$('issue-excerpt').value,detail:$('issue-detail').value,material_id:citation?Number($('issue-material').value):null,support:citation?$('issue-support').value:'not_applicable',recorder:$('issue-recorder').value,confirmed:$('issue-confirmed').checked})});
    lectureReviewData=data;editorData=data;drawVersionControls();drawEditor(true);$('lecture-editor-status').textContent='问题已保存，原文位置与引用判断已留存；正文输入保持不变。';
  }catch(err){editorError(err.message);$('issue-confirmed').checked=false;}finally{setEditorBusy(false);}
};

Object.assign(window, {drawScope, loadLectureReview, clearLectureReview, setReviewBusy});
})();
