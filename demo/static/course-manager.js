(() => {
  let catalog = {}, pointNames = new Map(), editing = null, saving = false, catalogLoaded = false, referenceVersion = 0;
  const option = (value, label) => {const n = el('option', label); n.value = value; return n;};
  function status(id, message, failed = false) {$(id).textContent = message; $(id).dataset.error = String(failed);}
  function drawList() {
    const cards = custom => Object.entries(catalog).filter(([,t])=>Boolean(t.custom)===custom).map(([key,t])=>{
      const link=el('a',undefined,'student-card');link.href='#materials/'+encodeURIComponent(key);
      link.append(el('strong',t.title),el('span',[t.stage,t.course,t.chapter].filter(Boolean).join(' · '),'helper'),el('p',t.goal,'evidence'),el('span','查看副本 →','student-card-action'));return link;
    });
    const copies=cards(true);$('course-list-title').textContent='我的备课副本 · '+copies.length+' 份';
    $('course-copy-list').replaceChildren(...copies);
    if(!copies.length)$('course-copy-list').append(el('p','还没有备课副本，点击“新建备课副本”开始备课。','helper'));
    $('course-example-list').replaceChildren(...cards(false));
  }
  async function drawReferences(key) {
    const token=++referenceVersion;$('course-reference-list').replaceChildren();
    status('course-reference-status','正在读取参考资料…');
    try {
      const d=await api('/api/course-tasks/'+encodeURIComponent(key)+'/materials');if(token!==referenceVersion)return;
      status('course-reference-status',d.materials.length+' 条参考资料');
      $('course-reference-list').replaceChildren(...d.materials.map(m=>{
        const box=el('details',undefined,'source');box.append(el('summary','M'+m.id+' · '+m.title),el('p',m.content));
        const link=el('a',m.source_title+' · 查看原始资料 ↗');link.href=m.source_url.startsWith('upload:')?'/api/uploads/'+encodeURIComponent(m.source_url.slice(7))+'/download':m.source_url;link.target='_blank';link.rel='noopener noreferrer';box.append(link);return box;
      }));
      if(!d.materials.length)$('course-reference-list').append(el('p','尚未添加参考资料。也可以直接根据教学目标和教案生成讲义。','helper'));
    }catch(e){if(token===referenceVersion)status('course-reference-status',e.message,true);}
  }
  function route() {
    const match=/^#materials\/([\w-]+)$/.exec(location.hash);const key=match?.[1];
    $('course-list-page').hidden=location.hash!=='#materials';$('course-editor-page').hidden=!match;
    $('course-reference-panel').hidden=true;referenceVersion++;
    status('course-manager-status','');status('course-material-status','');
    if(!match)return;
    if(key==='new'){fillTask();$('course-editor-heading').textContent='新建备课副本';return;}
    if(!catalogLoaded)return;
    if(!catalog[key]){fillTask();$('course-task-form').hidden=true;status('course-manager-status','找不到这份副本，请返回材料列表。',true);return;}
    $('course-task-form').hidden=false;fillTask(key);$('course-editor-heading').textContent=catalog[key].title;
    $('attach-topic').value=key;drawKnowledge();$('course-reference-panel').hidden=false;
    $('course-attach-details').hidden=!catalog[key].custom;drawReferences(key);
  }
  function fillTask(key = '') {
    $('course-task-form').hidden=false;
    editing = key ? {key, ...catalog[key]} : null;
    $('course-saved-actions').hidden=!editing;
    $('managed-course-task').value = key;
    const values = editing || {};
    for (const [id, field] of Object.entries({'course-stage':'stage', 'course-name':'course', 'course-chapter':'chapter', 'course-task-title':'title', 'course-task-level':'task_level', 'course-goal':'goal', 'course-lesson-plan':'lesson_plan'})) $(id).value = values[field] || '';
    $('course-knowledge').value = (values.knowledge_names || (values.knowledge_ids || []).map(k=>pointNames.get(k))).join('\n');
    const sample = Boolean(editing && !editing.custom);
    for (const input of $('course-task-form').querySelectorAll('input,textarea')) input.readOnly = sample;
    $('save-course-task').disabled = sample || saving;
    $('save-course-task').textContent = editing ? '保存副本修改' : '保存备课副本';
    $('duplicate-course-task').disabled = !editing || saving;
    $('use-course-task').disabled = !editing || saving;
    if(sample)status('course-manager-status','这是预设示例。点击「复制为新副本」后可独立编辑，并用于任意学生。');
  }
  function drawKnowledge() {
    const task = catalog[$('attach-topic').value];
    $('attach-knowledge').replaceChildren(...(task?.knowledge_ids || []).map((id,i)=>option(id,task.knowledge_names?.[i]||pointNames.get(id)||'知识点 '+id)));
  }
  async function refreshCatalog(preferred) {
    const d = await api('/api/course-tasks'); catalog = d.topics; pointNames = new Map(d.knowledge_points.map(k=>[k.id,k.name]));
    const attach = preferred || $('attach-topic').value;
    const label=t=>`${t.custom?'':'示例 · '}${t.stage?t.stage+' · ':''}${t.course} / ${t.title}`;
    $('managed-course-task').replaceChildren(option('', '新建备课副本'), ...Object.entries(catalog).map(([k,t])=>option(k,label(t))));
    $('attach-topic').replaceChildren(...Object.entries(catalog).map(([k,t])=>option(k,label(t))));
    if(catalog[attach])$('attach-topic').value=attach;
    catalogLoaded=true;drawList();
    if(preferred){location.hash='materials/'+preferred;}
    route();
    return d;
  }
  async function refreshUploads() {
    const d=await api('/api/uploads');const selected=$('attach-upload').value;
    $('attach-upload').replaceChildren(option('', '请选择已上传资料'),...d.uploads.map(u=>option(u.id,`${u.title} · ${u.characters} 字符`)));
    if(d.uploads.some(u=>u.id===selected))$('attach-upload').value=selected;
  }
  function freezeForm(value){
    saving=value;
    for(const id of ['managed-course-task','new-course-task','duplicate-course-task','use-course-task','save-course-task'])$(id).disabled=value;
    if(!value)fillTask(editing?.key||'');
  }
  $('new-course-task').onclick=()=>{if(!saving)location.hash='materials/new';};
  $('managed-course-task').onchange=()=>{status('course-manager-status','');fillTask($('managed-course-task').value);};
  $('course-task-form').addEventListener('input',()=>{$('use-course-task').disabled=true;$('duplicate-course-task').disabled=true;});
  $('attach-topic').onchange=drawKnowledge;
  $('refresh-course-uploads').onclick=async()=>{try{await refreshUploads();status('course-material-status','资料列表已更新。');}catch(e){status('course-material-status',e.message,true);}};
  $('course-task-form').onsubmit=async event=>{
    event.preventDefault();if(saving||busy){status('course-manager-status','请等待当前生成或保存结束。',true);return;}
    freezeForm(true);
    const body={preparation_copy:true,stage:$('course-stage').value,course:$('course-name').value,chapter:$('course-chapter').value,title:$('course-task-title').value,goal:$('course-goal').value,task_level:$('course-task-level').value,lesson_plan:$('course-lesson-plan').value,
      knowledge_names:$('course-knowledge').value.split('\n').map(x=>x.trim()).filter(Boolean)};
    if(editing)body.revision=editing.revision;
    let saved=false;
    try{
      const d=await api('/api/course-tasks'+(editing?'/'+editing.key:''),{method:editing?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      editing={key:d.key,...d.task};saved=true;await refreshCatalog(d.key);await refreshWorkspaceCatalog();
      status('course-manager-status',d.notice||'备课副本已保存，可继续关联参考资料，或选择此副本生成讲义。');
    }catch(e){status('course-manager-status',e.message,true);}
    finally{
      saving=false;$('managed-course-task').disabled=false;$('new-course-task').disabled=false;$('save-course-task').disabled=Boolean(editing&&!editing.custom);
      if(saved)fillTask(editing.key);
    }
  };
  $('duplicate-course-task').onclick=async()=>{
    if(!editing||saving||busy)return;freezeForm(true);
    try{const d=await api('/api/course-tasks/'+editing.key+'/copy',{method:'POST'});editing={key:d.key,...d.task};await refreshCatalog(d.key);await refreshWorkspaceCatalog();status('course-manager-status','已复制教案、目标和参考资料，可独立修改。');}
    catch(e){status('course-manager-status',e.message,true);}
    finally{freezeForm(false);}
  };
  $('use-course-task').onclick=async()=>{
    if(!editing||saving||busy)return;
    try{await refreshWorkspaceCatalog(editing.key);$('retrieval-mode').value='sql';$('topic').dispatchEvent(new Event('change'));openWorkspacePage('workbench');}
    catch(e){status('course-manager-status',e.message,true);}
  };
  $('course-material-form').onsubmit=async event=>{
    event.preventDefault();if(busy){status('course-material-status','请等待当前生成结束。',true);return;}
    const button=$('attach-course-material');if(button.disabled)return;button.disabled=true;
    try{
      const key=$('attach-topic').value;
      const d=await api('/api/course-tasks/'+key+'/materials',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upload_id:$('attach-upload').value,knowledge_id:Number($('attach-knowledge').value)})});
      status('course-material-status',`已收录为 ${d.citation_id}，可在讲义中引用。`);
      await drawReferences(key);
      if($('topic').value===key)await loadContext();
    }catch(e){status('course-material-status',e.message,true);}finally{button.disabled=false;}
  };
  window.addEventListener('hashchange',route);route();
  Promise.all([refreshCatalog(),refreshUploads()]).catch(e=>status('course-manager-status',e.message,true));
})();
