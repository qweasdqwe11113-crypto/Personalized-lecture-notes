(() => {
  let catalog = {}, pointNames = new Map(), editing = null, saving = false, catalogLoaded = false, referenceVersion = 0;
  // Knowledge picker: ticked point ids, the lesson's own names, and the course library (knowledge.js).
  let picked = [], own = new Map(), shelf = [], shelfVersion = 0, pickerInputs = [];
  const option = (value, label) => {const n = el('option', label); n.value = value; return n;};
  function status(id, message, failed = false) {$(id).textContent = message; $(id).dataset.error = String(failed);}
  const scope = window.workspaceScope;
  // Lessons belong to one course; the list shows the current course only.
  const inCourse = t => !scope?.course() || t.course_id === scope.courseId();
  function drawList() {
    const cards = custom => Object.entries(catalog).filter(([,t])=>Boolean(t.custom)===custom&&inCourse(t)).map(([key,t])=>{
      const link=el('a',undefined,'student-card');link.href='#materials/'+encodeURIComponent(key);
      link.append(el('strong',t.title||'未命名备课'),el('span',[t.chapter,t.task_level].filter(Boolean).join(' · '),'helper'),el('p',t.goal,'evidence'),el('span',custom?'编辑备课 →':'查看示例 →','student-card-action'));return link;
    });
    const copies=cards(true),examples=cards(false);
    $('course-list-title').textContent=`${scope?.course()?.name||'我的'}备课 · ${copies.length} 份`;
    $('course-copy-list').replaceChildren(...copies);
    if(!copies.length)$('course-copy-list').append(el('p',examples.length?'还没有备课。可新建备课，或打开下方示例复制成自己的备课。':'还没有备课，点击「新建备课」开始。','helper'));
    $('course-example-list').replaceChildren(...examples);$('course-example-panel').hidden=!examples.length;
  }
  function courseText(t){
    const c=scope?.courses.find(x=>x.id===(t?t.course_id:scope.courseId()));
    $('course-editor-course').textContent=c?'所属课程：'+scope.courseLabel(c):t&&(t.stage||t.course)?'所属课程：'+[t.stage,t.course].filter(Boolean).join(' · '):'';
  }
  async function drawReferences(key) {
    const token=++referenceVersion;$('course-reference-list').replaceChildren();
    status('course-reference-status','正在读取参考资料…');
    try {
      const d=await api('/api/course-tasks/'+encodeURIComponent(key)+'/materials');if(token!==referenceVersion)return;
      status('course-reference-status',d.materials.length+' 条参考资料');
      $('course-reference-list').replaceChildren(...d.materials.map(m=>{
        const box=el('details',undefined,'source');box.append(el('summary','M'+m.id+' · '+m.title),el('p',m.content));
        const link=el('a',m.source_title+' · 查看原始资料 ↗');link.href=sourceHref(m.source_url);link.target='_blank';link.rel='noopener noreferrer';box.append(link);return box;
      }));
      if(!d.materials.length)$('course-reference-list').append(el('p','尚未添加参考资料。也可以直接根据教学目标和教案生成讲义。','helper'));
    }catch(e){if(token===referenceVersion)status('course-reference-status',e.message,true);}
  }
  function route() {
    const found=/^#materials\/([\w-]+)$/.exec(location.hash);const match=found&&!['resources','classes','knowledge'].includes(found[1])?found:null;const key=match?.[1];
    $('course-list-page').hidden=location.hash!=='#materials';$('course-editor-page').hidden=!match;
    $('course-reference-panel').hidden=true;referenceVersion++;
    status('course-manager-status','');status('course-material-status','');
    if(!match)return;
    if(key==='new'){fillTask();$('course-editor-heading').textContent='新建备课';return;}
    if(!catalogLoaded)return;
    if(!catalog[key]){fillTask();$('course-task-form').hidden=true;status('course-manager-status','找不到这份备课，请返回备课列表。',true);return;}
    $('course-task-form').hidden=false;fillTask(key);$('course-editor-heading').textContent=catalog[key].title||'未命名备课';
    refreshUploads().catch(e=>status('course-material-status',e.message,true));
    $('attach-topic').value=key;drawKnowledge();$('course-reference-panel').hidden=false;
    $('course-attach-details').hidden=!catalog[key].custom;drawReferences(key);
  }
  function fillTask(key = '') {
    $('course-task-form').hidden=false;
    editing = key ? {key, ...catalog[key]} : null;
    $('course-saved-actions').hidden=!editing;
    $('managed-course-task').value = key;
    const values = editing || {};
    courseText(editing);
    for (const [id, field] of Object.entries({'course-chapter':'chapter', 'course-task-title':'title', 'course-task-level':'task_level', 'course-goal':'goal', 'course-lesson-plan':'lesson_plan'})) $(id).value = values[field] || '';
    picked = [...(values.knowledge_ids || [])];
    own = new Map(picked.map((id, i) => [id, values.knowledge_names?.[i] || pointNames.get(id) || '知识点 ' + id]));
    $('course-knowledge').value = ''; $('course-knowledge-filter').value = '';
    const sample = Boolean(editing && !editing.custom);
    for (const input of $('course-task-form').querySelectorAll('input,textarea')) input.readOnly = sample;
    $('save-course-task').disabled = sample || saving;
    $('save-course-task').textContent = editing ? '保存备课修改' : '保存备课';
    $('duplicate-course-task').disabled = !editing || saving;
    $('use-course-task').disabled = !editing || saving;
    if(sample)status('course-manager-status','这是预设示例。点击「复制为新备课」后可独立编辑，用于本课程的班级和学生。');
    drawPicker(); loadShelf();
  }
  async function loadShelf() {
    const course=editing?.course_id??scope?.courseId(),token=++shelfVersion;
    if(course==null||!window.courseKnowledge){shelf=[];drawPicker();return;}
    try{const d=await window.courseKnowledge.load(course);if(token!==shelfVersion)return;shelf=d.points.filter(p=>!p.archived);drawPicker();}
    catch(e){if(token===shelfVersion)status('course-manager-status',e.message,true);}
  }
  // Library points grouped by chapter, then the lesson's own points that are not in the library.
  function drawPicker() {
    const sample=Boolean(editing&&!editing.custom),query=$('course-knowledge-filter').value.trim(),inShelf=new Set(shelf.map(p=>p.id));
    const groups=new Map();
    for(const p of shelf){const key=p.chapter||'未分章节';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(p);}
    const extra=picked.filter(id=>!inShelf.has(id)).map(id=>({id,name:own.get(id),summary:'不在知识点库中（例如示例或复制的备课自带的知识点），保留在本备课中。'}));
    if(extra.length)groups.set('本备课已有',extra);
    pickerInputs=[];
    $('course-knowledge-groups').replaceChildren(...[...groups.entries()].map(([chapter,points])=>{
      const box=el('details',undefined,'knowledge-picker-group'),count=el('span','','count'),head=el('summary');
      box.open=Boolean(query)||groups.size<=2||points.some(p=>picked.includes(p.id));head.append(el('span',chapter),count);box.append(head);
      let shown=0;
      for(const p of points){
        const label=el('label',undefined,'knowledge-pick'),input=el('input');input.type='checkbox';input.checked=picked.includes(p.id);input.dataset.id=String(p.id);
        input.onchange=()=>{picked=input.checked?[...picked,p.id]:picked.filter(id=>id!==p.id);syncPicker();};
        label.append(input,el('span',p.name));if(p.summary)label.title=p.summary;
        label.hidden=Boolean(query)&&!p.name.includes(query)&&!(p.summary||'').includes(query);if(!label.hidden)shown++;
        pickerInputs.push({input,count,points,sample});box.append(label);
      }
      box.hidden=!shown;return box;
    }));
    if(!groups.size)$('course-knowledge-groups').append(el('p',scope?.course()?'知识点库还是空的：在「课程资料」上传教材或讲义并确认识别结果后，可在这里勾选；也可以在下方直接新增。':'没有课程知识点库，请在下方逐行填写知识点。','helper'));
    syncPicker();
  }
  function syncPicker() {
    const full=picked.length>=20;
    for(const {input,count,points,sample} of pickerInputs){
      input.disabled=sample||(!input.checked&&full);
      const chosen=points.filter(p=>picked.includes(p.id)).length;count.textContent=chosen?`已选 ${chosen} / ${points.length}`:`${points.length} 个`;
    }
    $('course-knowledge-count').textContent=`已选 ${picked.length} / 20`;
  }
  function drawKnowledge() {
    const task = catalog[$('attach-topic').value];
    $('attach-knowledge').replaceChildren(...(task?.knowledge_ids || []).map((id,i)=>option(id,task.knowledge_names?.[i]||pointNames.get(id)||'知识点 '+id)));
  }
  async function refreshCatalog(preferred) {
    const d = await api('/api/course-tasks'); catalog = d.topics; pointNames = new Map(d.knowledge_points.map(k=>[k.id,k.name]));
    const attach = preferred || $('attach-topic').value;
    const label=t=>`${t.custom?'':'示例 · '}${[t.stage,t.course,t.title].filter(Boolean).join(' / ')||'未命名备课'}`;
    $('managed-course-task').replaceChildren(option('', '新建备课'), ...Object.entries(catalog).map(([k,t])=>option(k,label(t))));
    $('attach-topic').replaceChildren(...Object.entries(catalog).map(([k,t])=>option(k,label(t))));
    if(catalog[attach])$('attach-topic').value=attach;
    catalogLoaded=true;drawList();
    if(preferred){location.hash='materials/'+preferred;}
    route();
    return d;
  }
  async function refreshUploads() {
    // The lesson's course resources and its classes' resources; class ones are marked.
    const course=editing?.course_id??scope?.courseId();if(course==null)return;
    const d=await api('/api/uploads?scope=course_all&course_id='+course);const selected=$('attach-upload').value;
    const classes=new Map((scope?.courses.find(c=>c.id===course)?.classes||[]).map(k=>[k.id,k.name]));
    $('attach-upload').replaceChildren(option('', d.uploads.length?'请选择课程或班级资料':'本课程还没有资料，请先在「课程资料」上传'),...d.uploads.map(u=>option(u.id,`${u.class_id?'['+(classes.get(u.class_id)||'班级')+'专用] ':''}${u.title} · ${u.characters} 字符`)));
    if(d.uploads.some(u=>u.id===selected))$('attach-upload').value=selected;
  }
  window.refreshCourseReferenceUploads=()=>refreshUploads().catch(()=>{});
  function freezeForm(value){
    saving=value;
    for(const id of ['managed-course-task','new-course-task','duplicate-course-task','use-course-task','save-course-task'])$(id).disabled=value;
    if(!value)fillTask(editing?.key||'');
  }
  $('new-course-task').onclick=()=>{if(!saving)location.hash='materials/new';};
  $('managed-course-task').onchange=()=>{status('course-manager-status','');fillTask($('managed-course-task').value);};
  $('course-task-form').addEventListener('input',()=>{$('use-course-task').disabled=true;$('duplicate-course-task').disabled=true;});
  $('attach-topic').onchange=drawKnowledge;
  $('course-knowledge-filter').oninput=drawPicker;
  $('refresh-course-uploads').onclick=async()=>{try{await refreshUploads();status('course-material-status','资料列表已更新。');}catch(e){status('course-material-status',e.message,true);}};
  $('course-task-form').onsubmit=async event=>{
    event.preventDefault();if(saving||busy){status('course-manager-status','请等待当前生成或保存结束。',true);return;}
    freezeForm(true);
    const course=editing?editing.course_id??scope?.courseId():scope?.courseId();
    // Ticked points in library order; new lines join the library (same name reuses the library point).
    const order=new Map(shelf.map((p,i)=>[p.id,i])),ids=[...picked].sort((a,b)=>(order.get(a)??1e9)-(order.get(b)??1e9));
    const lines=$('course-knowledge').value.split('\n').map(x=>x.trim()).filter(Boolean);
    const body={preparation_copy:true,...(course!=null?{course_id:course,knowledge_ids:ids,knowledge_names:lines}:{knowledge_names:[...ids.map(id=>own.get(id)),...lines]}),
      chapter:$('course-chapter').value,title:$('course-task-title').value,goal:$('course-goal').value,task_level:$('course-task-level').value,lesson_plan:$('course-lesson-plan').value};
    if(editing)body.revision=editing.revision;
    let saved=false;
    try{
      const d=await api('/api/course-tasks'+(editing?'/'+editing.key:''),{method:editing?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      editing={key:d.key,...d.task};saved=true;await refreshCatalog(d.key);await refreshWorkspaceCatalog();
      if(scope)await scope.refresh();
      if(lines.length)window.courseKnowledge?.refresh().catch(()=>{});
      status('course-manager-status',d.notice||'备课已保存，可继续收录参考资料，或用此备课生成讲义。');
    }catch(e){status('course-manager-status',e.message,true);}
    finally{
      saving=false;$('managed-course-task').disabled=false;$('new-course-task').disabled=false;$('save-course-task').disabled=Boolean(editing&&!editing.custom);
      if(saved)fillTask(editing.key);
    }
  };
  $('duplicate-course-task').onclick=async()=>{
    if(!editing||saving||busy)return;freezeForm(true);
    try{const d=await api('/api/course-tasks/'+editing.key+'/copy',{method:'POST'});editing={key:d.key,...d.task};await refreshCatalog(d.key);await refreshWorkspaceCatalog();if(scope)await scope.refresh();status('course-manager-status','已复制教案、目标和参考资料，可独立修改。');}
    catch(e){status('course-manager-status',e.message,true);}
    finally{freezeForm(false);}
  };
  $('use-course-task').onclick=async()=>{
    if(!editing||saving||busy)return;
    try{if(scope&&editing.course_id!=null&&editing.course_id!==scope.courseId())scope.set(editing.course_id,undefined,'courses');await refreshWorkspaceCatalog(editing.key);$('retrieval-mode').value='sql';$('topic').dispatchEvent(new Event('change'));openWorkspacePage('workbench');}
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
  window.addEventListener('scope-changed',event=>{
    if(!catalogLoaded)return;drawList();
    // Switching course while a lesson of another course is open returns to the new course's list.
    const open=/^#materials\/([\w-]+)$/.exec(location.hash)?.[1];
    if(event.detail!=='refresh'&&open&&catalog[open]&&catalog[open].course_id!==scope.courseId())location.hash='materials';
    else if(open==='new'){courseText(null);loadShelf();}
  });
  window.refreshCourseCatalog=()=>refreshCatalog();
  (scope?scope.ready.catch(()=>{}):Promise.resolve()).then(()=>refreshCatalog()).catch(e=>status('course-manager-status',e.message,true));
})();
