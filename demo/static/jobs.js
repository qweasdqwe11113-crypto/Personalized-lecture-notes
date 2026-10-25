(() => {
const labels={queued:'排队中',running:'生成中',succeeded:'已保存',failed:'失败',interrupted:'进程中断',cancelled:'已取消'};
let watchSession=0;
async function refreshJobs(){
 const data=await api('/api/jobs');
 $('jobs-list').replaceChildren(...data.jobs.map(job=>{
   const b=el('button',`${job.section_rewrite?'章节调整 · ':''}${job.section_rewrite&&job.status==='succeeded'?'候选已生成':labels[job.status]} · ${job.name} · ${job.topic}`);
   b.type='button';b.onclick=()=>{if(busy)return;watchJob(job.id);};const row=el('div',undefined,'job-row');row.append(b);if(job.status==='queued'){const cancel=el('button','取消排队','secondary');cancel.type='button';cancel.onclick=async()=>{cancel.disabled=true;try{await api('/api/jobs/'+job.id+'/cancel',{method:'POST'});await refreshJobs();}catch(e){error(e.message);cancel.disabled=false;}};row.append(cancel);}return row;
 }));
 if(!data.jobs.length)$('jobs-list').append(el('p','尚无后台任务。','helper'));
}
async function checkHealth(){
 try{
  const h=await api('/api/health');
  $('health-summary').textContent=`数据库：${h.database_ok?'可连接':'待检查'}；密钥：${h.key_present?'已配置':'未配置（无鉴权服务可留空）'}；服务地址格式：${h.endpoint_format_ok?'有效':'待检查'}。${h.note} 单次模型请求等待上限 ${h.request_timeout_seconds} 秒。配置修改后请重启服务。`;
  modelConfigured=h.database_ok&&h.endpoint_format_ok&&h.model_present;
  if(!busy)$('generate').disabled=!modelConfigured||!hasGenerationBasis(current);
 }catch(e){$('health-summary').textContent=e.message;}
}
async function watchJob(id){
 if(typeof openWorkspacePage==="function")openWorkspacePage("workbench");
 const session=++watchSession;
 setBusy(true);error('');clearResult();$('empty').hidden=true;$('loading').hidden=false;
 try{
  while(session===watchSession){
   const job=await api('/api/jobs/'+id);
   if(job.section_rewrite){$('loading').hidden=true;setBusy(false);await window.watchSectionJob(id);break;}
   const wait=Math.max(0,Math.floor((Date.now()-new Date(job.created_at).getTime())/1000));
   $('progress').textContent=`${labels[job.status]} · 记录 #${job.run_id} · 已等待 ${wait} 秒`+(job.status==='queued'?` · 等待位置 ${job.queue_position}`:'');
   if(job.status==='running')$('progress').textContent+=' · 正在等待模型完整响应，尚无可报告的完成百分比';
   if(job.confirmation_pending)throw new Error('任务结束状态未能确认，请核查生成记录并重启本地服务。');
   if(job.status==='succeeded'){
     const r=await api('/api/lectures/'+job.lecture_id);
     restoreLectureSettings(r);showResult(r);
     $('progress').textContent=`已保存讲义 #${r.id} · 模型用时 ${job.elapsed_seconds} 秒 · 用量 ${JSON.stringify(job.usage)}`;
     await refreshHistory();break;
   }
   if(['failed','interrupted','cancelled'].includes(job.status)){
     error(job.error_message||'任务未完成，请核查生成记录。');$('empty').hidden=false;
     showFailedTrace(job.run_id);await refreshRuns();break;
   }
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
 }catch(e){error(e.message+' 已提交任务可在“后台任务”重新查看，不必重复提交。');$('empty').hidden=false;}
 finally{if(session===watchSession){$('loading').hidden=true;setBusy(false);refreshJobs().catch(()=>{});}}
}
$('generate-form').onsubmit=async e=>{
 e.preventDefault();if(busy||!current)return;
 setBusy(true);error('');$('progress').textContent='正在提交并固定输入…';
 try{
  const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({student_id:Number($('student').value),topic:$('topic').value,goal:$('goal').value,personalization_mode:$('personalization-mode').value,retrieval_mode:$('retrieval-mode').value,learner_note:$('learner-note').value,teaching_options:readTeachingOptions(),plan_overrides:typeof readLecturePlan==='function'?readLecturePlan():{},visual_analysis_ids:typeof selectedVisualAnalyses==='function'?selectedVisualAnalyses():[]})});
  await refreshJobs();await watchJob(job.id);
 }catch(e){error(e.message);setBusy(false);}
};
$('retry-run').onclick=async()=>{
 if(busy){traceError('当前页面正在查看另一个任务，请等待完成。');return;}
 const button=$('retry-run');button.disabled=true;
 try{
   const job=await api(`/api/generation-runs/${button.dataset.runId}/retry`,{method:'POST'});
   $('trace-dialog').close();await refreshJobs();await watchJob(job.id);
 }catch(e){traceError(e.message);}finally{button.disabled=false;}
};
window.refreshWorkspaceJobs=refreshJobs;
$('check-health').onclick=checkHealth;
$('refresh-jobs').onclick=()=>refreshJobs().catch(e=>error(e.message));
refreshJobs().catch(()=>{});checkHealth();
})();
