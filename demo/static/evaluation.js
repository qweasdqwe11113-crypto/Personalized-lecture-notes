(() => {
 let active=null,timer=null,version=0;
 const labels={prepared:'未运行',dispatching:'提交中',queued:'等待或生成中',succeeded:'已生成',failed:'失败',interrupted:'中断',running:'正在执行',completed:'本轮已结束'};
 async function history(){const d=await api('/api/lecture-experiments');$('experiment-history').replaceChildren(...d.experiments.map(e=>{const b=el('button',`${new Date(e.created_at).toLocaleString('zh-CN')} · ${labels[e.status]}`);b.type='button';b.onclick=()=>open(e.id).catch(showError);return b;}));}
 function showError(e){$('experiment-status').textContent=e.message;}
 async function open(id){
  clearTimeout(timer);active=id;const token=++version;
  const e=await api('/api/lecture-experiments/'+id);if(token!==version)return;
  $('experiment-detail').replaceChildren();const s=e.summary;
  $('experiment-status').textContent=`${labels[e.status]} · 成功 ${s.completed}/${s.total}，失败或中断 ${s.failed}；保存为教师已审核 ${s.teacher_reviewed} 份。已记录 ${s.timing_records} 份非零审核用时，合计 ${s.recorded_review_seconds} 秒；未记录的不估算。`;
  $('run-experiment').hidden=e.status==='running'||!e.cells.some(c=>c.status==='prepared');
  $('experiment-detail').append(el('p',e.manifest.design+' '+e.manifest.note,'helper'));
  for(const c of e.cells){const card=el('div',undefined,'plan-item');card.append(el('strong',`第${c.cell}格 · 学生${c.student_id} · ${c.mode==='profile'?'有画像':'无画像'} · 重复${c.repeat} · ${labels[c.status]}`));
   if(c.error||c.error_message)card.append(el('p',c.error||c.error_message,'check-warning'));
   if(c.lecture_id){const button=el('button','打开原稿并复核','secondary');button.type='button';button.onclick=async()=>{try{if(busy)throw new Error('请先等待当前页面任务完成。');const r=await api('/api/lectures/'+c.lecture_id);restoreLectureSettings(r);showResult(r);}catch(err){showError(err);}};card.append(button);}
   if(c.run_id){const trace=el('button','核对实际输入','secondary');trace.type='button';trace.onclick=()=>openTrace(c.run_id);card.append(trace);}
   $('experiment-detail').append(card);
  }
  if(e.status==='running')timer=setTimeout(()=>open(id).catch(showError),2000);
 }
 $('prepare-experiment').onclick=async()=>{const b=$('prepare-experiment');b.disabled=true;try{const e=await api('/api/lecture-experiments',{method:'POST'});await history();await open(e.id);}catch(e){showError(e);}finally{b.disabled=false;}};
 $('run-experiment').onclick=async()=>{if(!active)return;$('run-experiment').disabled=true;try{await api('/api/lecture-experiments/'+active+'/run',{method:'POST'});await open(active);await history();}catch(e){showError(e);}finally{$('run-experiment').disabled=false;}};
 window.addEventListener('experiment-history-open',()=>history().catch(showError));
})();
