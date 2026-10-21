(() => {
const labels={queued:'排队中',running:'生成中',succeeded:'已保存',failed:'失败',interrupted:'进程中断'};
let watchSession=0;
async function refreshJobs(){
 const data=await api('/api/jobs');
 $('jobs-list').replaceChildren(...data.jobs.map(job=>{
   const b=el('button',`${labels[job.status]} · ${job.name} · ${job.topic}`);
   b.type='button';b.onclick=()=>{if(busy)return;watchJob(job.id);};return b;
 }));
 if(!data.jobs.length)$('jobs-list').append(el('p','尚无后台任务。','helper'));
}
async function checkHealth(){
 try{
  const h=await api('/api/health');
  $('health-summary').textContent=`数据库：${h.database_ok?'可连接':'待检查'}；密钥：${h.key_present?'已配置':'缺失'}；服务地址格式：${h.endpoint_format_ok?'有效':'待检查'}。${h.note} 配置修改后请重启服务。`;
  modelConfigured=h.database_ok&&h.key_present&&h.endpoint_format_ok&&h.model_present;
  if(!busy)$('generate').disabled=!modelConfigured||!current;
 }catch(e){$('health-summary').textContent=e.message;}
}
async function watchJob(id){
 if(typeof openWorkspacePage==="function")openWorkspacePage("workbench");
 const session=++watchSession;
 setBusy(true);error('');clearResult();$('empty').hidden=true;$('loading').hidden=false;
 try{
  while(session===watchSession){
   const job=await api('/api/jobs/'+id);
   const wait=Math.max(0,Math.floor((Date.now()-new Date(job.created_at).getTime())/1000));
   $('progress').textContent=`${labels[job.status]} · 记录 #${job.run_id} · 已等待 ${wait} 秒`+(job.status==='queued'?` · 等待位置 ${job.queue_position}`:'');
   if(job.confirmation_pending)throw new Error('任务结束状态未能确认，请核查生成记录并重启本地服务。');
   if(job.status==='succeeded'){
     const r=await api('/api/lectures/'+job.lecture_id);
     const key=Object.keys(topics).find(key=>topics[key].title===r.topic);
     if(key)$('topic').value=key;filterStudents(r.student_id);
     $('personalization-mode').value=r.context.personalization_mode||'profile';$('retrieval-mode').value=(r.context.retrieval||{}).mode||'sql';$('goal').value=r.goal;
     drawContext(r.context,true);showResult(r);
     $('progress').textContent=`已保存讲义 #${r.id} · 模型用时 ${job.elapsed_seconds} 秒 · 用量 ${JSON.stringify(job.usage)}`;
     await refreshHistory();break;
   }
   if(['failed','interrupted'].includes(job.status)){
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
  const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({student_id:Number($('student').value),topic:$('topic').value,goal:$('goal').value,personalization_mode:$('personalization-mode').value,retrieval_mode:$('retrieval-mode').value,visual_analysis_ids:typeof selectedVisualAnalyses==='function'?selectedVisualAnalyses():[]})});
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
$('check-health').onclick=checkHealth;
$('refresh-jobs').onclick=()=>refreshJobs().catch(e=>error(e.message));
refreshJobs().catch(()=>{});checkHealth();
})();
