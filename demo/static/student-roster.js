(() => {
  let selected = null, version = 0;
  window.selectedProfileStudentId = null;
  function note(message){$('student-roster-status').textContent=message;}
  function renderStates(data){
    $('states').replaceChildren(...data.states.filter(s=>s.state!=='unknown'||s.revision>0).map(s=>{
      const box=el('div');const top=el('div',undefined,'state-top');top.append(el('span',s.name),el('span',s.label,'badge '+s.state));
      box.append(top,el('p',s.evidence,'evidence'),el('p',s.strategy,'strategy'));return box;
    }));
    if(!$('states').children.length)$('states').append(el('p','尚未记录知识点诊断，可在维护画像中补充长期证据。','helper'));
  }
  async function loadStudent(id){
    const token=++version;selected=Number(id);window.selectedProfileStudentId=selected;
    $('edit-profile').disabled=true;
    $('roster-student-name').textContent='正在读取学生档案…';
    $('roster-student-subtitle').textContent='';$('roster-student-profile').textContent='';$('states').replaceChildren();
    try {
      const data=await api('/api/students/'+selected+'/profile');if(token!==version)return;
      $('roster-student-name').textContent=data.student.name;
      $('roster-student-subtitle').textContent=data.student.subtitle;
      $('roster-student-profile').textContent=data.student.profile;
      $('profile-view-note').textContent='该同学的长期诊断与教师记录；本次生成备注在讲义工作台填写。';
      renderStates(data);$('edit-profile').disabled=busy;
    } catch(e){if(token===version){$('roster-student-name').textContent='无法读取学生档案';note(e.message);}}
  }
  function route(){
    const hash=location.hash.slice(1);const match=/^profiles\/([1-9]\d*)$/.exec(hash);
    $('student-list-page').hidden=hash!=='profiles';
    $('student-create-page').hidden=hash!=='profiles/new';
    $('student-detail-page').hidden=!match;
    note('');version++;selected=null;window.selectedProfileStudentId=null;
    if(match)loadStudent(match[1]);
  }
  window.refreshStudentRoster=async ()=>{
    const d=await api('/api/students');
    $('student-roster-title').textContent='全部学生 · '+d.students.length+' 人';
    $('student-roster').replaceChildren(...d.students.map(s=>{
      const link=el('a',undefined,'student-card');link.href='#profiles/'+s.id;
      link.append(el('strong',s.name),el('span',s.subtitle,'helper'),el('p',s.profile,'evidence'),el('span','查看档案 →','student-card-action'));return link;
    }));
    if(!d.students.length)$('student-roster').append(el('p','还没有学生，点击“新建学生画像”添加第一位同学。','helper'));
    const match=/^#profiles\/([1-9]\d*)$/.exec(location.hash);
    if(match)await loadStudent(match[1]);
  };
  $('view-current-profile').onclick=()=>{if(selected)loadStudent(selected);};
  $('refresh-student-roster').onclick=()=>refreshStudentRoster().catch(e=>note(e.message));
  $('new-student-form').onsubmit=async event=>{
    event.preventDefault();const button=$('create-student');if(button.disabled)return;button.disabled=true;note('正在保存…');
    try {
      const d=await api('/api/students',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('new-student-name').value,subtitle:$('new-student-subtitle').value,profile:$('new-student-profile').value})});
      $('new-student-form').reset();location.hash='profiles/'+d.student.id;
      await Promise.all([refreshStudentRoster(),refreshWorkspaceCatalog()]);
    }catch(e){note(e.message);}finally{button.disabled=false;}
  };
  window.addEventListener('hashchange',route);
  route();refreshStudentRoster().catch(e=>note(e.message));
})();
