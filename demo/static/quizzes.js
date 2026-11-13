(() => {
  const typeLabels={single_choice:'单项选择',fill_blank:'填空',short_answer:'简答',calculation:'计算',application:'综合应用'};
  const difficultyLabels={basic:'基础',medium:'中等',advanced:'拔高',mixed:'混合'};
  const levelLabels={foundation:'基础待巩固',standard:'常规水平',advanced:'学有余力'};
  const statusLabels={queued:'排队中',running:'生成中',succeeded:'已生成',failed:'失败',interrupted:'已中断'};
  let catalog={}, roster=[], pointNames=new Map(), displayed=null, teacher=false, watching=0, submitting=false, linked=null;
  const option=(value,label)=>{const node=el('option',label);node.value=value;return node;};
  function showError(message){$('quiz-error').textContent=message||'';$('quiz-error').hidden=!message;}
  function setStatus(message){$('quiz-status').textContent=message;}
  const versionLabel=n=>n?'R'+n:'原稿';
  function drawLink(){
    $('quiz-lecture-link').hidden=!linked;
    $('quiz-topic').disabled=Boolean(linked)||!Object.keys(catalog).length;$('quiz-student').disabled=Boolean(linked);
    if(linked)$('quiz-lecture-label').textContent=`配套讲义 #${linked.id} · ${versionLabel(linked.revision)} · ${linked.student||'无画像对照'}`;
  }
  const scope=window.workspaceScope;
  // The quiz target: '' (no profile), a student id, or 'class:<id>' for the class profile.
  const targetValue=row=>row.student_id!=null?String(row.student_id):row.class_id!=null?'class:'+row.class_id:'';
  function ensureOption(value,label){if(value&&!Array.from($('quiz-student').options).some(o=>o.value===value))$('quiz-student').append(option(value,label));}
  function linkLecture(link){
    linked=link;
    if(link&&catalog[link.topic]){$('quiz-topic').value=link.topic;selectTopic();}
    if(link){const v=targetValue(link);ensureOption(v,link.student);$('quiz-student').value=v;$('quiz-level').value=v?'profile':'standard';describeStudent();}
    drawLink();
  }
  function allocation(){
    const n=Number($('quiz-count').value),difficulty=$('quiz-difficulty').value;
    if(!Number.isInteger(n)||n<1||n>30){$('quiz-allocation').textContent='每次支持 1～30 题。';return;}
    if(difficulty!=='mixed'){$('quiz-allocation').textContent=`共 ${n} 题，${difficultyLabels[difficulty]}难度。`;return;}
    const weights=[5,3,2],counts=weights.map(w=>Math.floor(n*w/10));
    const order=[0,1,2].sort((a,b)=>(n*weights[b]%10)-(n*weights[a]%10));
    const remaining=n-counts.reduce((a,b)=>a+b,0);for(const i of order.slice(0,remaining))counts[i]++;
    $('quiz-allocation').textContent=`共 ${n} 题：基础 ${counts[0]} 题、中等 ${counts[1]} 题、拔高 ${counts[2]} 题。`;
  }
  function describeStudent(){
    const value=$('quiz-student').value,isClass=value.startsWith('class:'),student=roster.find(s=>String(s.id)===value);
    $('quiz-level').querySelector('option[value=profile]').disabled=!student&&!isClass;
    if(!student&&!isClass&&$('quiz-level').value==='profile')$('quiz-level').value='standard';
    $('quiz-profile-note').textContent=$('quiz-level').value!=='profile'?'按所选水平设定先修要求，不发送学生个人画像；题目难度另行选择。':
      isClass?'依据班级画像中各知识点的掌握分布安排分层题目；不发送学生姓名，班级资料只用于本班。':'依据所选学生的长期画像安排重点；未知状态不会自动当作薄弱点。';
  }
  function selectTopic(){
    const topic=catalog[$('quiz-topic').value];if(!topic)return;
    $('quiz-goal').textContent='教学目标：'+topic.goal;
    const selected=$('quiz-student').value,cls=scope?.klass(),inClass=cls&&topic.course_id===scope.courseId();
    const members=inClass?new Set(cls.student_ids):null;
    const people=roster.filter(s=>topic.student_ids.includes(s.id)&&(!members||members.has(s.id)));
    $('quiz-student').replaceChildren(option('','通用学生水平'),...(inClass&&cls.student_ids.length?[option('class:'+cls.id,`全班 · ${cls.name}（班级画像）`)]:[]),...people.map(s=>option(s.id,s.name+' · '+s.subtitle)));
    if(Array.from($('quiz-student').options).some(o=>o.value===selected))$('quiz-student').value=selected;
    $('quiz-knowledge').replaceChildren(...topic.knowledge_ids.map((id,index)=>{
      const label=el('label');const input=el('input');input.type='checkbox';input.value=id;input.name='quiz-point';
      label.append(input,document.createTextNode(topic.knowledge_names?.[index]||pointNames.get(id)||'知识点 '+id));return label;
    }));
    describeStudent();$('quiz-generate').disabled=submitting;
  }
  async function refreshCatalog(){
    const data=await api('/api/course-tasks'),selected=$('quiz-topic').value;
    catalog=data.topics;roster=data.students;pointNames=new Map(data.knowledge_points.map(k=>[k.id,k.name]));
    drawTopics(selected);
  }
  function drawTopics(selected){
    // Lessons of the current course; a linked lecture's lesson stays selectable.
    const course=scope?.courseId(),keep=linked?.topic;
    const own=Object.entries(catalog).filter(([key,t])=>course==null||t.course_id===course||key===keep).sort(([,a],[,b])=>Number(Boolean(b.custom))-Number(Boolean(a.custom)));
    $('quiz-topic').replaceChildren(...own.map(([key,t])=>option(key,(t.custom?'':'示例 · ')+(t.title||'未命名备课'))));
    if(!own.length)$('quiz-topic').append(option('','本课程还没有备课'));
    if(own.some(([key])=>key===selected))$('quiz-topic').value=selected;
    $('quiz-topic').disabled=!own.length;selectTopic();drawLink();
    $('quiz-generate').disabled=submitting||!own.length;
  }
  function restoreSettings(row){
    const lecture=row.context.lecture;
    const named=row.student_id!=null||row.class_id!=null;
    linked=lecture?{id:lecture.id,revision:lecture.revision,topic:row.topic_key,student_id:row.student_id,class_id:row.class_id??null,student:named?row.context.student.name:''}:null;
    if(catalog[row.topic_key]){if(!Array.from($('quiz-topic').options).some(o=>o.value===row.topic_key))$('quiz-topic').append(option(row.topic_key,catalog[row.topic_key].title));$('quiz-topic').value=row.topic_key;selectTopic();}
    const sid=targetValue(row);ensureOption(sid,named?row.context.student.name:'');
    $('quiz-student').value=sid;
    $('quiz-level').value=row.options.student_level;describeStudent();
    $('quiz-count').value=row.options.count;$('quiz-difficulty').value=row.options.difficulty;$('quiz-requirements').value=row.options.requirements;
    for(const input of document.querySelectorAll('input[name=quiz-type]'))input.checked=row.options.types.includes(input.value);
    for(const input of document.querySelectorAll('input[name=quiz-point]'))input.checked=row.options.knowledge_ids.includes(Number(input.value));
    allocation();drawLink();
  }
  function drawDownload(){
    $('quiz-export').hidden=displayed?.status!=='succeeded';
    if(displayed?.status==='succeeded'){
      $('quiz-download').textContent='下载'+(teacher?'教师版':'学生版');
      $('quiz-download').href=`/api/quizzes/${displayed.id}/download?version=${teacher?'teacher':'student'}&format=${$('quiz-format').value}`;
    }
  }
  function drawResult(){
    $('quiz-student-view').setAttribute('aria-pressed',String(!teacher));$('quiz-teacher-view').setAttribute('aria-pressed',String(teacher));
    drawDownload();$('quiz-questions').replaceChildren();
    if(!displayed)return;
    $('quiz-retry').hidden=!['failed','interrupted'].includes(displayed.status);
    $('quiz-preview-title').textContent=displayed.context.topic.title+' · 练习题';
    const d=displayed;
    $('quiz-preview-note').textContent=`${d.options.count} 题 · ${difficultyLabels[d.options.difficulty]}难度 · ${d.options.student_level==='profile'?'参考 '+d.context.student.name+' 的画像':levelLabels[d.options.student_level]} · ${statusLabels[d.status]}`+(d.status==='succeeded'?'。答案正确性、难度和引用支持仍需教师复核。':'');
    if(d.status!=='succeeded')return;
    for(const q of d.result.questions){
      const card=el('section',undefined,'quiz-question');
      card.append(el('h3',`第 ${q.number} 题 · ${typeLabels[q.type]} · ${difficultyLabels[q.difficulty]}`),el('p',q.stem,'quiz-text'));
      for(const [i,text] of q.options.entries())card.append(el('p','ABCD'[i]+'. '+text,'quiz-text'));
      if(!teacher)card.append(el('p','作答：____________________________','quiz-workspace'));
      else{
        const answer=el('div',undefined,'quiz-answer');
        for(const [label,text] of [['考查知识点',d.context.knowledge_names[String(q.knowledge_id)]],['参考答案',q.answer],['解析',q.analysis],['易错点',q.common_mistake],['材料依据',q.source_ids.join('、')||'自编题目，未标注直接材料依据，请复核。']])answer.append(el('h4',label),el('p',text));
        card.append(answer);
      }
      $('quiz-questions').append(card);
    }
  }
  async function refreshHistory(){
    const data=await api('/api/quizzes');
    $('quiz-history-error').hidden=true;
    $('quiz-history').replaceChildren(...data.quizzes.map(q=>{
      const b=el('button');b.type='button';b.setAttribute('aria-current',String(q.id===displayed?.id));
      b.append(el('strong',q.title),el('span',`${q.lecture_id?'配套讲义 #'+q.lecture_id+' · ':''}${statusLabels[q.status]} · ${q.options.count} 题 · ${difficultyLabels[q.options.difficulty]} · ${new Date(q.created_at).toLocaleString('zh-CN')}`,'helper'));
      b.onclick=()=>{openWorkspacePage('quizzes');watch(q.id).catch(e=>showError(e.message));};return b;
    }));
    if(!data.quizzes.length)$('quiz-history').append(el('p','还没有出题记录。','helper'));
  }
  async function watch(id){
    const token=++watching;teacher=false;showError('');localStorage.setItem('activeQuizJob',id);
    await catalogReady;let restored=false;
    while(token===watching){
      const row=await api('/api/quizzes/'+id);if(token!==watching)return;
      if(!restored){restoreSettings(row);restored=true;}
      displayed=row;drawResult();
      setStatus(statusLabels[row.status]+(row.status==='queued'?'，正在等待模型空闲。':row.status==='running'?'，可切换页面，完成后可在记录中查看。':''));
      if(row.confirmation_pending){showError('任务结束状态尚未确认，请检查本地服务后刷新记录。');break;}
      if(['succeeded','failed','interrupted'].includes(row.status)){
        if(row.error_message)showError(row.error_message);await refreshHistory();break;
      }
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }
  $('quiz-form').onsubmit=async event=>{
    event.preventDefault();if(submitting)return;
    const types=Array.from(document.querySelectorAll('input[name=quiz-type]:checked'),n=>n.value);
    if(!types.length){showError('请至少选择一种题型。');return;}
    submitting=true;$('quiz-generate').disabled=true;showError('');setStatus('正在固定出题设置并提交…');
    try{
      const who=$('quiz-student').value,target=who.startsWith('class:')?{class_id:Number(who.slice(6))}:{student_id:who?Number(who):null};
      const data={topic:$('quiz-topic').value,...target,student_level:$('quiz-level').value,count:Number($('quiz-count').value),difficulty:$('quiz-difficulty').value,types,knowledge_ids:Array.from(document.querySelectorAll('input[name=quiz-point]:checked'),n=>Number(n.value)),requirements:$('quiz-requirements').value,...(linked?{lecture_id:linked.id,lecture_revision:linked.revision}:{})};
      const row=await api('/api/quizzes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      await refreshHistory().catch(()=>{});watch(row.id).catch(e=>showError(e.message+' 任务已提交，可在出题记录中重新查看。'));
    }catch(e){showError(e.message);setStatus('提交未完成，请核对设置后重试。');}
    finally{submitting=false;$('quiz-generate').disabled=false;}
  };
  $('quiz-retry').onclick=async()=>{
    if(!displayed)return;const id=displayed.id;$('quiz-retry').disabled=true;showError('');
    try{const row=await api('/api/quizzes/'+id+'/retry',{method:'POST'});await watch(row.id);}catch(e){showError(e.message);}finally{$('quiz-retry').disabled=false;}
  };
  $('quiz-topic').onchange=selectTopic;
  $('quiz-lecture-clear').onclick=()=>{linked=null;drawLink();describeStudent();setStatus('已取消讲义关联，将按备课副本出题。');};
  const lectureButton=el('button','配套出题','secondary');lectureButton.id='open-lecture-quiz';lectureButton.type='button';lectureButton.disabled=true;
  $('lecture-prep-status').before(lectureButton);
  window.addEventListener('lecture-version-changed',()=>{lectureButton.disabled=!lastResult||busy;});
  lectureButton.onclick=async()=>{
    if(!lastResult||busy)return;
    const r=lastResult,profiled=r.context.personalization_mode!=='none';
    watching++;displayed=null;teacher=false;drawResult();showError('');$('quiz-preview-title').textContent='题组预览';
    await catalogReady.catch(()=>{});
    linkLecture({id:r.id,revision:r.revision_number||0,topic:r.topic_key,student_id:profiled?r.student_id:null,class_id:profiled?r.class_id??null:null,student:profiled?r.context.student.name:''});
    setStatus('已关联当前阅读的讲义版本，确认题量与题型后生成。');openWorkspacePage('quizzes');
  };
  $('quiz-student').onchange=()=>{if($('quiz-student').value)$('quiz-level').value='profile';describeStudent();};
  $('quiz-level').onchange=describeStudent;$('quiz-count').oninput=allocation;$('quiz-difficulty').onchange=allocation;
  $('quiz-student-view').onclick=()=>{teacher=false;drawResult();};$('quiz-teacher-view').onclick=()=>{teacher=true;drawResult();};$('quiz-format').onchange=drawDownload;
  function historyError(e){$('quiz-history-error').textContent=e.message;$('quiz-history-error').hidden=false;}
  $('quiz-refresh-catalog').onclick=()=>refreshCatalog().catch(e=>showError(e.message));$('quiz-refresh-history').onclick=()=>refreshHistory().catch(historyError);
  window.addEventListener('quiz-history-open',()=>refreshHistory().catch(historyError));
  window.addEventListener('scope-changed',event=>{if(!Object.keys(catalog).length)return;if(event.detail==='refresh')refreshCatalog().catch(()=>{});else drawTopics($('quiz-topic').value);});
  allocation();const catalogReady=(scope?scope.ready.catch(()=>{}):Promise.resolve()).then(refreshCatalog);catalogReady.catch(e=>showError(e.message));refreshHistory().catch(historyError);
  const last=localStorage.getItem('activeQuizJob');if(last)watch(last).catch(()=>localStorage.removeItem('activeQuizJob'));
})();
