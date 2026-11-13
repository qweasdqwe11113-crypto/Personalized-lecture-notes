/* Students of the current class (#profiles/students), creating one inside the class
   (#profiles/new) and a student's record (#profiles/<id>), where the name, subtitle and learning
   traits are edited in place with a recorded reason. Records are shared across courses; the
   knowledge states shown follow the current course. */
(() => {
  const scope = window.workspaceScope;
  let selected = null, version = 0, armed = null, record = null, pendingEdit = null, recorder = '';
  // The revision the edit form started from, so a concurrent change is refused instead of overwritten.
  let editing = null;
  window.selectedProfileStudentId = null;
  const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  function note(message){$('student-roster-status').textContent=message||'';}
  function editStatus(message,failed=false){$('student-edit-status').textContent=message||'';$('student-edit-status').dataset.error=String(failed);}
  const historyFields=[['name','姓名'],['subtitle','学生说明'],['profile','学习特点']];
  function drawHistory(list){
    $('student-history-title').textContent=list.length?`修改历史 · ${list.length===20?'最近 20':list.length} 次`:'修改历史';
    $('student-history').replaceChildren(...list.map(h=>{
      const item=el('div',undefined,'class-history-item');
      item.append(el('strong',`${new Date(h.created_at).toLocaleString('zh-CN')} · ${h.recorder}`),el('p','理由：'+h.reason,'helper'));
      for(const [key,label] of historyFields)if(h.previous_snapshot[key]!==h.new_snapshot[key])
        item.append(el('p',`${label}：${h.previous_snapshot[key]||'（空）'} → ${h.new_snapshot[key]||'（空）'}`,'evidence'));
      return item;
    }));
    if(!list.length)$('student-history').append(el('p','尚无修改记录。','helper'));
  }
  function closeEdit(){editing=null;$('edit-student-form').hidden=true;$('edit-student').setAttribute('aria-expanded','false');}
  function openEdit(){
    if(!record)return;
    if(busy){editStatus('讲义正在生成，完成后再编辑学生画像。',true);return;}
    const s=record.student;editing={id:s.id,revision:s.profile_revision};
    $('edit-student-name').value=s.name;$('edit-student-subtitle').value=s.subtitle;$('edit-student-profile').value=s.profile;
    $('edit-student-recorder').value=recorder;$('edit-student-reason').value='';
    $('edit-student-form').hidden=false;$('edit-student').setAttribute('aria-expanded','true');editStatus('');
    $('edit-student-profile').focus();
  }
  function drawStudent(data){
    record=data;
    $('roster-student-name').textContent=data.student.name;
    $('roster-student-subtitle').textContent=data.student.subtitle;
    $('roster-student-profile').textContent=data.student.profile;
    $('roster-student-classes').textContent=data.classes?.length?'所在班级：'+data.classes.map(c=>`${c.course_name} · ${c.class_name}`).join('；'):'尚未加入任何班级。';
    $('student-states-title').textContent=(scope?.course()?scope.course().name+' · ':'')+'知识点掌握情况';
    $('profile-view-note').textContent='该同学在本课程知识点上的长期诊断与教师记录；本次生成备注在讲义工作台填写。';
    drawHistory(data.profile_history||[]);
    renderStates(data);$('edit-profile').disabled=busy;$('edit-student').disabled=busy;
  }
  const courseQuery = () => scope?.courseId() != null ? '?course_id=' + scope.courseId() : '';
  function renderStates(data){
    $('states').replaceChildren(...data.states.filter(s=>s.state!=='unknown'||s.revision>0).map(s=>{
      const box=el('div');const top=el('div',undefined,'state-top');top.append(el('span',s.name),el('span',s.label,'badge '+s.state));
      box.append(top,el('p',s.evidence,'evidence'),el('p',s.strategy,'strategy'));return box;
    }));
    if(!$('states').children.length)$('states').append(el('p','本课程的知识点尚未记录诊断，可点击「记录诊断证据」补充长期证据。','helper'));
  }
  async function loadStudent(id){
    // Re-reading the same student keeps an open edit form and its draft.
    const token=++version,next=Number(id);if(next!==selected)closeEdit();
    selected=next;window.selectedProfileStudentId=selected;record=null;
    $('edit-profile').disabled=true;$('edit-student').disabled=true;
    $('roster-student-name').textContent='正在读取学生档案…';
    $('roster-student-subtitle').textContent='';$('roster-student-profile').textContent='';$('roster-student-classes').textContent='';$('states').replaceChildren();
    try {
      const data=await api('/api/students/'+selected+'/profile'+courseQuery());if(token!==version)return;
      drawStudent(data);
      if(pendingEdit===selected){pendingEdit=null;openEdit();}
    } catch(e){if(token===version){$('roster-student-name').textContent='无法读取学生档案';note(e.message);}}
  }
  const className = id => scope?.course()?.classes.find(k => k.id === id)?.name;
  function drawRoster(){
    const cls=scope?.klass();if(!cls)return;
    const members=new Set(cls.student_ids),list=students.filter(s=>members.has(s.id));
    $('student-roster-title').textContent=`${cls.name} · ${list.length} 名学生`;
    $('student-roster').replaceChildren(...list.map(s=>{
      const card=el('div',undefined,'student-card roster-card');
      const link=el('a',undefined,'roster-card-main');link.href='#profiles/'+s.id;
      link.append(el('strong',s.name),el('span',s.subtitle,'helper'),el('p',s.profile,'evidence'),el('span','查看档案 →','student-card-action'));
      const actions=el('div',undefined,'roster-card-actions');
      const edit=el('button','编辑','secondary');edit.type='button';edit.setAttribute('aria-label',`编辑「${s.name}」的学生画像`);
      edit.onclick=()=>{pendingEdit=s.id;location.hash='profiles/'+s.id;};
      const remove=el('button',armed===s.id?'确认移出本班':'移出班级','secondary');remove.type='button';
      remove.onclick=()=>leave(s,remove);
      actions.append(edit,remove);card.append(link,actions);return card;
    }));
    if(!list.length)$('student-roster').append(el('p','班级还没有学生。可新建学生、批量导入，或添加其他课程中已有的学生。','helper'));
    drawExisting();
  }
  function drawExisting(){
    const cls=scope?.klass();if(!cls)return;
    const members=new Set(cls.student_ids),others=students.filter(s=>!members.has(s.id)),keep=$('add-existing-student').value;
    $('add-existing-student').replaceChildren(...others.map(s=>{
      const elsewhere=className(scope.classOf(s.id));
      const o=el('option',`${s.name} · ${s.subtitle}${elsewhere?`（已在${elsewhere}）`:''}`);o.value=s.id;return o;
    }));
    if(others.some(s=>String(s.id)===keep))$('add-existing-student').value=keep;
    if(!others.length)$('add-existing-student').append(el('option','所有学生都已在本班'));
    $('add-existing-submit').disabled=!others.length;
  }
  async function changed(){
    // Membership feeds the scope (class lists) and the workbench (lesson student lists).
    if(typeof refreshWorkspaceCatalog==='function')await refreshWorkspaceCatalog();
    if(scope)await scope.refresh();
    drawRoster();
  }
  async function leave(s,button){
    if(armed!==s.id){armed=s.id;drawRoster();note(`再次点击确认将「${s.name}」移出本班；学生档案、诊断和已生成讲义都会保留。`);return;}
    button.disabled=true;
    try{await api(`/api/classes/${scope.classId()}/students/${s.id}/remove`,{method:'POST'});armed=null;await changed();note(`已将「${s.name}」移出本班。`);}
    catch(e){note(e.message);button.disabled=false;}
  }
  function route(){
    const hash=location.hash.slice(1);const match=/^profiles\/([1-9]\d*)$/.exec(hash);
    note('');editStatus('');version++;selected=null;window.selectedProfileStudentId=null;armed=null;
    if(!match){pendingEdit=null;closeEdit();}
    if(hash==='profiles/students')drawRoster();
    if(match)loadStudent(match[1]);
  }
  async function afterEdit(id){
    if(typeof refreshWorkspaceCatalog==='function')await refreshWorkspaceCatalog();
    // The workbench shows this student's traits beside the lecture settings.
    if($('student').value===String(id)&&!busy&&typeof loadContext==='function')await loadContext();
  }
  $('edit-student').onclick=()=>editing?closeEdit():openEdit();
  $('cancel-student-edit').onclick=()=>{closeEdit();editStatus('');};
  $('edit-student-form').onsubmit=async event=>{
    event.preventDefault();
    const button=$('save-student');if(button.disabled||!editing)return;
    if(busy){editStatus('讲义正在生成，完成后再保存。',true);return;}
    button.disabled=true;const id=editing.id;
    try{
      const d=await api(`/api/students/${id}/profile${courseQuery()}`,json({
        name:$('edit-student-name').value,subtitle:$('edit-student-subtitle').value,profile:$('edit-student-profile').value,
        recorder:$('edit-student-recorder').value,reason:$('edit-student-reason').value,confirmed:true,expected_revision:editing.revision}));
      recorder=$('edit-student-recorder').value;closeEdit();
      if(selected===id)drawStudent(d);
      editStatus('学生画像已保存，原内容保留在修改历史中。');
      try{await afterEdit(id);}catch{editStatus('学生画像已保存；名单暂未刷新，可点击「读取最新档案」。');}
    }catch(e){editStatus(e.status===409?e.message+' 可取消后重新打开编辑。':e.message,true);}
    finally{button.disabled=false;}
  };
  window.addEventListener('lecture-version-changed',()=>{$('edit-student').disabled=busy||!record;});
  window.refreshStudentRoster=async ()=>{
    drawRoster();
    const match=/^#profiles\/([1-9]\d*)$/.exec(location.hash);
    if(match)await loadStudent(match[1]);
  };
  $('view-current-profile').onclick=()=>{if(selected)loadStudent(selected);};
  $('profile-start-lecture').onclick=()=>{
    if(!selected)return;
    // A student outside the current course's classes is still allowed when a lesson lists them.
    if(!window.prepareLectureFor(selected))note(busy?'讲义正在生成，请完成后再切换学生。':'当前课程没有包含这名学生的备课。请先把学生加入本课程的班级，或在「课程」中新建备课。');
  };
  $('refresh-student-roster').onclick=()=>changed().then(()=>note('名单已更新。')).catch(e=>note(e.message));
  $('toggle-add-existing').onclick=()=>{const open=$('add-existing-form').hidden;$('add-existing-form').hidden=!open;$('toggle-add-existing').setAttribute('aria-expanded',String(open));if(open){drawExisting();$('add-existing-student').focus();}};
  $('add-existing-form').onsubmit=async event=>{
    event.preventDefault();const button=$('add-existing-submit');if(button.disabled)return;
    const sid=Number($('add-existing-student').value);if(!sid)return;button.disabled=true;
    try{
      await api(`/api/classes/${scope.classId()}/students`,json({student_id:sid,move:$('add-existing-move').checked}));
      $('add-existing-move').checked=false;await changed();note('已加入本班。');
    }catch(e){note(e.message);}finally{button.disabled=false;drawExisting();}
  };
  $('new-student-form').onsubmit=async event=>{
    event.preventDefault();const button=$('create-student');if(button.disabled||!scope?.klass())return;button.disabled=true;note('正在保存…');
    try {
      const d=await api(`/api/classes/${scope.classId()}/students`,json({name:$('new-student-name').value,subtitle:$('new-student-subtitle').value,profile:$('new-student-profile').value}));
      $('new-student-form').reset();await changed();location.hash='profiles/'+d.student.id;
    }catch(e){note(e.message);}finally{button.disabled=false;}
  };
  window.addEventListener('hashchange',route);
  window.addEventListener('scope-changed',event=>{
    if(event.detail!=='refresh'){armed=null;note('');$('add-existing-form').hidden=true;$('toggle-add-existing').setAttribute('aria-expanded','false');}
    const hash=location.hash.slice(1);
    if(hash==='profiles/students')drawRoster();
    // The record's knowledge states follow the current course.
    else if(event.detail!=='refresh'&&/^profiles\/[1-9]\d*$/.test(hash)&&selected)loadStudent(selected);
  });
  route();
})();
