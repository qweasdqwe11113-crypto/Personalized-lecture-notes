/* Teacher-visible plans and citations always read the saved lecture snapshot. */
function drawLecturePlan(ctx,snapshot=false){
 const box=$('lecture-plan');box.replaceChildren();
 for(const p of ctx.personalization_plan||[]){
  const item=el('div',undefined,'plan-item');item.append(el('strong',p.name),el('p','依据：'+p.evidence,'helper'));
  for(const warning of p.warnings||[])item.append(el('p',warning,'check-warning'));
  const label=el('label','本次怎么讲');const input=el('textarea');input.rows=2;input.maxLength=400;input.value=p.action;input.dataset.knowledgeId=p.knowledge_id;input.dataset.initialAction=p.action;input.dataset.teacherModified=String(p.teacher_modified);input.disabled=busy;label.append(input);item.append(label);box.append(item);
 }
 if(!box.children.length)box.append(el('p',ctx.personalization_mode==='none'?'无画像模式采用通用讲解，不发送个体安排。':'历史输入未保存讲解安排；不会补造。','helper'));
 $('input-budget-note').textContent=(snapshot?'当前显示生成时保存的安排。再次生成使用当前资料；精确重试请从失败记录操作。 ':'')+(ctx.input_budget?`本次材料约 ${ctx.input_budget.material_characters} 字符。`+' '+(ctx.input_budget.notes||[]).join(' ')+(ctx.input_budget.omitted_material_ids?.length?' 未纳入本次：'+ctx.input_budget.omitted_material_ids.map(i=>'M'+i).join('、')+'。':''):'');
}
function readLecturePlan(){return Object.fromEntries(Array.from($('lecture-plan').querySelectorAll('textarea')).filter(n=>n.value!==n.dataset.initialAction||n.dataset.teacherModified==='true').map(n=>[n.dataset.knowledgeId,n.value]));}
function setLecturePlanBusy(value){$('lecture-plan').querySelectorAll('textarea').forEach(n=>n.disabled=value);}
function drawResultPlan(row){
 const plans=row.context.personalization_plan||[];const box=$('result-plan-content');box.replaceChildren();$('result-plan').hidden=!plans.length;
 for(const p of plans){const item=el('div',undefined,'plan-item');item.append(el('strong',p.name),el('p','依据：'+p.evidence,'helper'),el('p','安排：'+p.action));
  const button=el('button','查看对应讲解','secondary');button.type='button';button.onclick=()=>{const mark=$('lecture').querySelector(`[data-plan-id="${p.knowledge_id}"]`);if(mark){mark.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});mark.tabIndex=-1;mark.focus({preventScroll:true});}else{button.textContent='本版本未标记对应位置，请人工核对。';}};item.append(button);box.append(item);
 }
 box.append(el('p','定位标记只说明正文关联了安排，是否落实与是否适合学生仍需教师复核。','helper'));
}
function openCitationSource(id,lineNumber){
 if(!lastResult)return;
 const ctx=lastResult.context;
 const material=id.startsWith('M')?ctx.materials.find(m=>'M'+m.id===id):null;
 const visual=id.startsWith('V')?(ctx.visual_evidence||[]).find(v=>v.citation_id===id):null;
 $('source-title').textContent='核查来源 ['+id+']';$('source-content').replaceChildren();
 $('source-review-actions').hidden=!material&&!visual;
 $('source-dialog').dataset.sourceId=id;$('source-dialog').dataset.sourceLine=Number.isInteger(lineNumber)?lineNumber:-1;
 $('source-status').textContent=`讲义 #${lastResult.id} / ${lastResult.revision_number?'R'+lastResult.revision_number:'原稿'} 的材料快照；编号有效不代表支持对应结论。`;
 if(material){$('source-content').append(el('h3',material.title),el('p',material.content,'source-excerpt'),el('p',material.source_title+' · '+material.section,'helper'));
  if(material.selection)$('source-content').append(el('p',`本次为原文节选；原文 ${material.selection.original_characters} 字符，原文位置（从0开始，不含末位）：${JSON.stringify(material.selection.ranges)}；SHA-256 ${material.selection.content_sha256}`,'helper'));
  const url=material.source_url.startsWith('upload:')?'/api/uploads/'+encodeURIComponent(material.source_url.slice(7))+'/download':material.source_url;
  if(/^https?:\/\//i.test(url)||url.startsWith('/api/uploads/')){const link=el('a','打开原始资料');link.href=url;link.target='_blank';link.rel='noopener noreferrer';$('source-content').append(link);}
 }else if(visual){const image=el('img');image.src='/api/vision/images/'+encodeURIComponent(visual.image_id);image.alt=visual.label;image.className='source-image';$('source-content').append(image,el('p',visual.content,'source-excerpt'),el('p','识读模型：'+visual.model+'；原图指纹：'+visual.normalized_sha256,'helper'));
 }else $('source-content').append(el('p','此编号不在本份讲义的来源快照中，请更正正文。','check-warning'));
 if(!$('source-dialog').open)$('source-dialog').showModal();
}
$('close-source').onclick=()=>$('source-dialog').close();
for(const b of document.querySelectorAll('[data-source-support]'))b.onclick=async()=>{const d=$('source-dialog');d.close();if(typeof prefillCitationReview==='function')await prefillCitationReview(d.dataset.sourceId,Number(d.dataset.sourceLine),b.dataset.sourceSupport);};
