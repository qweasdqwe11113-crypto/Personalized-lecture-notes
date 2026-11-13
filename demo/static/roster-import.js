// Batch import: the server re-plans and re-checks the same file at confirmation; nothing is inferred here.
(() => {
  const help={students:'每行一名学生。已有同名学生会跳过，不覆盖原档案；导入后可在学生档案中补充诊断证据。',
    evidence:'每行一条学习证据，写明来源、观察日期、判断与理由。知识点按名称对应本课程的知识点库（「课程 → 知识点库」中可查看全部名称），不必先建备课；全角半角括号、空格和大小写不影响对应。「更新状态」改变该知识点的掌握状态，「保留原判断」只记录证据；同一知识点的多行按文件顺序依次生效。未知不等于薄弱，请勿据单次表现下结论。'};
  const columns={students:[['line','行'],['姓名','姓名'],['年级或说明','年级或说明'],['学习特点','学习特点'],['result','处理']],
    evidence:[['line','行'],['学生姓名','学生'],['知识点','知识点'],['证据来源','来源'],['观察日期','观察日期'],['学生表现','学生表现'],['change','掌握状态'],['result','处理']]};
  let preview=null,token=0;
  const status=message=>{$('roster-import-status').textContent=message;};
  function reset(){preview=null;$('roster-import-result').hidden=true;$('roster-import-confirmed').checked=false;$('roster-import-submit').disabled=true;}
  function syncKind(){
    const kind=$('roster-import-kind').value;
    $('roster-import-recorder-field').hidden=kind!=='evidence';
    $('roster-import-help').textContent=help[kind];$('roster-import-template').href='/api/students/import/template?kind='+kind;reset();
  }
  // Imports go into the current class: names are matched within it and new students join it.
  // Evidence names points of the course knowledge library, so no lesson has to be chosen.
  const scope=window.workspaceScope;
  function form(extra={}){
    const data=new FormData();data.append('kind',$('roster-import-kind').value);
    if(scope?.classId()!=null)data.append('class_id',String(scope.classId()));
    if(scope?.courseId()!=null)data.append('course_id',String(scope.courseId()));
    data.append('file',$('roster-import-file').files[0]);for(const [k,v] of Object.entries(extra))data.append(k,v);return data;
  }
  function draw(data,kind){
    const s=data.summary;
    $('roster-import-summary').textContent=`预览结果 · 共 ${s.total} 行：可导入 ${s.ready} 行`+(s.skipped?`，跳过 ${s.skipped} 行`:'')+(s.errors?`，${s.errors} 行需修改`:'');
    const head=el('tr');for(const [,label] of columns[kind])head.append(el('th',label));$('roster-import-head').replaceChildren(head);
    $('roster-import-rows').replaceChildren(...data.rows.map(r=>{
      const tr=el('tr',undefined,r.errors.length?'error':'');
      for(const [key] of columns[kind]){
        let text=key==='line'?String(r.line):key==='change'?(r.errors.length?'—':`${r.previous_label} → ${r.confirmed_label}${r.decision==='retain'?'（保留原判断）':''}`):r.values[key];
        let cls=['学习特点','学生表现'].includes(key)?'wide':'';
        if(key==='result'){text=r.errors.length?r.errors.join('；'):r.action==='skip'?r.note:r.action==='create'?'新建档案':'记录证据';cls=r.errors.length?'row-error':r.action==='skip'?'row-skip':'row-ok';}
        tr.append(el('td',text,cls));
      }
      return tr;
    }));
    $('roster-import-result').hidden=false;$('roster-import-submit').textContent=`确认导入 ${s.ready} 行`;
    $('roster-import-submit').disabled=!$('roster-import-confirmed').checked||Boolean(s.errors)||!s.ready;
  }
  $('roster-import-kind').onchange=syncKind;$('roster-import-file').onchange=reset;
  $('roster-import-confirmed').onchange=()=>{$('roster-import-submit').disabled=!preview||!$('roster-import-confirmed').checked||Boolean(preview.summary.errors)||!preview.summary.ready;};
  $('roster-import-form').onsubmit=async event=>{
    event.preventDefault();if(!$('roster-import-file').files[0]){status('请选择 CSV 文件。');return;}
    const current=++token,kind=$('roster-import-kind').value;reset();status('正在检查文件…');
    try{
      const data=await api('/api/students/import/preview',{method:'POST',body:form()});if(current!==token)return;
      preview={...data,kind};draw(data,kind);
      status(data.summary.errors?'有行未通过检查：请按「处理」列修改文件后重新预览，整批不会部分导入。':'检查通过。请逐行核对，勾选确认后导入。');
    }catch(e){if(current===token)status(e.message);}
  };
  $('roster-import-submit').onclick=async()=>{
    if(!preview)return;const button=$('roster-import-submit');button.disabled=true;status('正在导入…');
    try{
      const data=await api('/api/students/import',{method:'POST',body:form({token:preview.token,confirmed:'true',recorder:$('roster-import-recorder').value})});
      status(preview.kind==='students'?`已新建 ${data.created_ids.length} 份学生档案，并加入${scope?.klass()?.name||'本班'}。`:`已记录 ${data.ready} 条学习证据，可在各学生档案中查看。`);
      // Clear the file but keep the kind (and recorder): the next file is often of the same kind.
      reset();$('roster-import-file').value='';
      if(typeof refreshWorkspaceCatalog==='function')await refreshWorkspaceCatalog();
      if(scope)await scope.refresh();await refreshStudentRoster();
    }catch(e){status(e.message+(e.status===409?'':' 本次未写入任何记录。'));button.disabled=false;}
  };
  // Another class or course invalidates a preview made for the previous one.
  window.addEventListener('scope-changed',event=>{if(event.detail==='refresh')return;reset();status('');});
  syncKind();
})();
