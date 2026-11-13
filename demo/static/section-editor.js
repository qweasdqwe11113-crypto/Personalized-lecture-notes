(() => {
let lecture=null, bundle=null, proposal=null, working=false, session=0;
const dialog=$('section-editor');
const labels={queued:'排队中',running:'正在生成本节',succeeded:'候选已生成',failed:'生成失败',interrupted:'任务已中断',cancelled:'已取消'};
function message(text){$('section-error').textContent=text||'';$('section-error').hidden=!text;}
function lock(value){working=value;for(const node of $('section-form').elements)node.disabled=value;$('close-section-editor').disabled=value;$('accept-section').disabled=value||!proposal||proposal.status!=='succeeded'||!!proposal.response_metadata.saved_revision;}
function clearProposal(){proposal=null;$('accept-section').disabled=true;$('section-trace').hidden=true;$('section-after').replaceChildren(el('p','生成后在这里预览。','helper'));$('section-progress').textContent='';}
function drawBefore(){const section=bundle?.sections[Number($('section-select').value)];$('section-before').replaceChildren(...(section?[renderMarkdown(section.content)]:[el('p','此版本没有可调整的二级章节。','helper')]));}
async function openEditor(){
 if(busy||!lastResult)return;
 const token=++session;lecture={...lastResult};bundle=null;clearProposal();message('');
 $('section-form').reset();$('section-select').replaceChildren();$('section-before').replaceChildren();
 $('section-base-note').textContent=`讲义 #${lecture.id} · 基于 ${lecture.revision_number?'R'+lecture.revision_number:'模型原稿'}`;
 dialog.showModal();lock(true);
 try{
  const data=await api(`/api/lectures/${lecture.id}/sections?base_revision=${lecture.revision_number||0}`);
  if(token!==session)return;bundle=data;
  $('section-select').replaceChildren(...data.sections.map(s=>{const o=el('option',s.title);o.value=s.index;return o;}));drawBefore();
  if(!data.sections.length)message('此版本没有二级章节，可使用“编辑与审核”手动调整。');
 }catch(e){message(e.message);}finally{lock(false);$('generate-section').disabled=!bundle?.sections.length;}
}
function drawProposal(run){
 proposal=run;const edit=run.context.section_edit;
 $('section-base-note').textContent=`讲义 #${edit.lecture_id} · 基于 ${edit.base_revision?'R'+edit.base_revision:'模型原稿'} · ${edit.title}`;
 $('section-before').replaceChildren(renderMarkdown(edit.base_content.slice(edit.start,edit.end)));
 $('section-after').replaceChildren(renderMarkdown(run.content||'未取得候选正文。'));
 $('section-trace').hidden=false;$('section-trace').onclick=()=>openTrace(run.id);
 const saved=run.response_metadata.saved_revision;
 $('section-progress').textContent=run.status!=='succeeded'?'本次修改未完成；讲义未改动。':saved?`已另存 R${saved} 草稿；本次候选已采用。`:'候选已生成，请对照原文核对；采用后另存草稿。';
 if(run.status!=='succeeded')message(run.error_message||'本次候选未完成，未改动讲义。');
}
async function watchSectionJob(id){
 const token=++session;message('');clearProposal();if(!dialog.open)dialog.showModal();
 lock(true);setBusy(true);
 try{
  let job;
  while(token===session){
   job=await api('/api/jobs/'+id);
   $('section-progress').textContent=`${labels[job.status]} · 输入记录 #${job.run_id}`+(job.status==='queued'?` · 等待位置 ${job.queue_position}`:'');
   if(job.confirmation_pending)throw new Error('任务状态尚未确认，请从后台任务核查后恢复。');
   if(['succeeded','failed','interrupted','cancelled'].includes(job.status))break;
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
  if(token!==session)return;
  const run=await api('/api/generation-runs/'+job.run_id);drawProposal(run);
  lecture=await api('/api/lectures/'+run.context.section_edit.lecture_id);
  // Restore the exact base and target even when reopened from the background list.
  const edit=run.context.section_edit;
  bundle=await api(`/api/lectures/${edit.lecture_id}/sections?base_revision=${edit.base_revision}`);
  $('section-select').replaceChildren(...bundle.sections.map(s=>{const o=el('option',s.title);o.value=s.index;return o;}));
  $('section-select').value=String(edit.index);$('section-action').value=edit.action;
  $('section-instruction').value=edit.instruction;$('section-recorder').value=edit.recorder;
 }catch(e){message(e.message+' 已提交任务可从“生成记录 → 后台任务”恢复，不必重复提交。');}
 finally{setBusy(false);lock(false);$('generate-section').disabled=!bundle;refreshWorkspaceJobs().catch(()=>{});refreshRuns().catch(()=>{});}
}
$('section-select').onchange=()=>{clearProposal();drawBefore();};
$('adjust-section').onclick=openEditor;
$('close-section-editor').onclick=()=>{if(!working){session++;dialog.close();}};
dialog.addEventListener('cancel',e=>{if(working)e.preventDefault();else session++;});
$('section-form').onsubmit=async e=>{
 e.preventDefault();if(working||!bundle||!lecture)return;message('');clearProposal();
 const section=bundle.sections[Number($('section-select').value)];if(!section)return;
 const data={base_revision:bundle.base_revision,expected_revision:bundle.expected_revision,
  section_index:section.index,section_sha256:section.sha256,action:$('section-action').value,
  instruction:$('section-instruction').value,recorder:$('section-recorder').value,confirmed:true};
 lock(true);
 try{
  const job=await api(`/api/lectures/${lecture.id}/section-rewrites`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  await watchSectionJob(job.id);
 }catch(err){message(err.message+(err.status===409?' 请关闭并重新打开章节调整。':''));lock(false);}
};
$('accept-section').onclick=async()=>{
 if(working||!proposal||proposal.status!=='succeeded')return;message('');lock(true);
 try{
  const data=await api(`/api/section-rewrites/${proposal.id}/accept`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true})});
  if(typeof openWorkspacePage==='function')openWorkspacePage('workbench');
  restoreLectureSettings(lecture);
  displaySectionRevision(lecture,data);dialog.close();proposal=null;await refreshHistory();
 }catch(e){message(e.message+(e.status===409?' 候选保留在生成输入记录中；请基于最新版本重新调整，避免覆盖其他修改。':''));}
 finally{lock(false);}
};
window.watchSectionJob=watchSectionJob;
})();
