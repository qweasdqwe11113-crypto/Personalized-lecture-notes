// Lesson package: a download of saved records for the version being read; nothing is regenerated.
(() => {
  const dialog=$('package-dialog');
  const button=el('button','导出授课包','secondary');button.id='open-package';button.type='button';button.disabled=true;
  $('lecture-prep-status').before(button);
  const versionLabel=n=>n?'R'+n:'原稿';
  const option=(value,label)=>{const node=el('option',label);node.value=value;return node;};
  let source=null,token=0;
  // A lesson written from a lesson design has a student handout and a teacher's book; older lectures keep the handbook.
  const formats=designed=>designed?[['zip','完整授课包（.zip）'],['handout','仅学生学案（Word，不含答案）'],['handout-key','学生学案含答案（Word）'],['docx','仅教师用书（Word）'],['md','仅教师用书（Markdown）'],['record','生成记录与核对（Word）']]
    :[['zip','完整授课包（.zip）'],['docx','仅教师授课手册（Word）'],['md','仅教师授课手册（Markdown）']];
  const contents=designed=>designed?[['学生学案','按课堂顺序排好目标、笔记填空、例题和练习，不含答案和学生信息，可直接打印；另附含答案版'],['教师用书','本课要点与课堂流程表、各环节详案、板书设计、答案速查和课后记录页'],['生成记录与核对','生成时的学生档案与讲解安排、材料来源、程序检查和授课前核对清单'],['配套练习、授课课件与清单','所选的练习与可编辑 PPTX，及每个文件的 SHA-256']]
    :[['教师授课手册','授课概览、学生档案与本次讲解安排、讲义正文、配套练习答案解析、材料来源、审核待办'],['学生练习','不含答案和学生信息，可直接打印'],['授课课件与清单','可编辑 PPTX，及每个文件的 SHA-256']];
  function describe(designed){
    const chosen=$('package-format').value;
    $('package-format').replaceChildren(...formats(designed).map(([value,label])=>option(value,label)));
    if([...$('package-format').options].some(o=>o.value===chosen))$('package-format').value=chosen;
    dialog.querySelector('.package-contents').replaceChildren(...contents(designed).map(([name,text])=>{const li=el('li');li.append(el('strong',name),document.createTextNode('：'+text));return li;}));
  }
  function link(){
    if(!source)return;
    const query=new URLSearchParams({revision:String(source.revision),format:$('package-format').value});
    if($('package-quiz').value)query.set('quiz',$('package-quiz').value);
    if($('package-deck').value)query.set('presentation',$('package-deck').value);
    $('package-download').href=`/api/lectures/${source.id}/package?${query}`;
    $('package-download').textContent=$('package-format').value==='zip'?'下载授课包':'下载'+$('package-format').selectedOptions[0].textContent.replace(/^仅/,'').replace(/（.*$/,'');
  }
  // Show what already exists for the lecture being read, so teachers see the next step without opening dialogs.
  let statusKey='',statusAt=0;
  async function prepStatus(force=false){
    if(!lastResult){$('lecture-prep-status').textContent='';statusKey='';return;}
    const key=lastResult.id+':'+(lastResult.revision_number||0);
    if(!force&&key===statusKey&&Date.now()-statusAt<4000)return;
    statusKey=key;statusAt=Date.now();
    try{
      const data=await api(`/api/lectures/${lastResult.id}/package-options`);
      if(!lastResult||key!==lastResult.id+':'+(lastResult.revision_number||0))return;
      const quizzes=data.quizzes.filter(q=>q.lecture_id===lastResult.id).length,decks=data.presentations.length;
      $('lecture-prep-status').textContent=quizzes||decks?`已有配套练习 ${quizzes} 份 · 课件 ${decks} 份`:'尚未生成配套练习和课件';
    }catch(_){$('lecture-prep-status').textContent='';}
  }
  window.addEventListener('lecture-version-changed',()=>{button.disabled=!lastResult||busy;prepStatus();});
  window.addEventListener('hashchange',()=>{if(location.hash==='#workbench')prepStatus(true);});
  button.onclick=async()=>{
    if(!lastResult||busy)return;
    const current=++token;source={id:lastResult.id,revision:lastResult.revision_number||0};
    $('package-source').textContent=`依据当前阅读的讲义 #${source.id} / ${versionLabel(source.revision)}。`;
    $('package-quiz').replaceChildren(option('','不包含练习'));$('package-deck').replaceChildren(option('','不包含课件'));
    describe(Boolean(lastResult.context?.lesson_design));
    $('package-status').textContent='正在读取可加入的练习与课件…';link();dialog.showModal();
    try{
      const data=await api(`/api/lectures/${source.id}/package-options`);if(current!==token)return;
      describe(data.designed);
      for(const q of data.quizzes)$('package-quiz').append(option(q.id,`${q.lecture_id?'配套本讲义 '+versionLabel(q.lecture_revision):'同课程练习'} · ${q.options.count} 题 · ${new Date(q.created_at).toLocaleString('zh-CN')}`));
      for(const d of data.presentations)$('package-deck').append(option(String(d.run_id),`#${d.run_id} · 依据${versionLabel(Number(d.base_revision))} · ${d.slide_count} 页 · ${d.template_name||'旧版版式'}`));
      // Preselect what was made for this exact version, then anything made for this lecture.
      const quiz=data.quizzes.find(q=>q.lecture_id===source.id&&q.lecture_revision===source.revision)||data.quizzes.find(q=>q.lecture_id===source.id);
      if(quiz)$('package-quiz').value=quiz.id;
      const deck=data.presentations.find(d=>Number(d.base_revision)===source.revision);
      if(deck)$('package-deck').value=String(deck.run_id);
      $('package-status').textContent=data.quizzes.length||data.presentations.length?'已预选与当前版本对应的最近练习和课件，可修改。':'暂无已生成的练习或课件：可先「配套出题」或「生成授课 PPT」，也可只导出讲义与学生档案。';
      link();
    }catch(e){if(current===token)$('package-status').textContent=e.message;}
  };
  for(const id of ['package-quiz','package-deck','package-format'])$(id).onchange=link;
  $('close-package').onclick=()=>dialog.close();
  $('package-new-quiz').onclick=()=>{dialog.close();$('open-lecture-quiz')?.click();};
})();
