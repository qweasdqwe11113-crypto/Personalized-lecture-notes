(() => {
  const typeLabels={single_choice:'单项选择',fill_blank:'填空',short_answer:'简答',calculation:'计算',application:'综合应用'};
  const difficultyLabels={basic:'基础',medium:'中等',advanced:'拔高',mixed:'混合'};
  const levelLabels={foundation:'基础待巩固',standard:'常规水平',advanced:'学有余力'};
  const statusLabels={queued:'排队中',running:'生成中',succeeded:'已生成',failed:'失败',interrupted:'已中断'};
  let catalog={}, roster=[], pointNames=new Map(), displayed=null, teacher=false, watching=0, submitting=false;
  const option=(value,label)=>{const node=el('option',label);node.value=value;return node;};
  function showError(message){$('quiz-error').textContent=message||'';$('quiz-error').hidden=!message;}
  function setStatus(message){$('quiz-status').textContent=message;}
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
    const student=roster.find(s=>String(s.id)===$('quiz-student').value);
    $('quiz-level').querySelector('option[value=profile]').disabled=!student;
    if(!student&&$('quiz-level').value==='profile')$('quiz-level').value='standard';
    $('quiz-profile-note').textContent=$('quiz-level').value==='profile'?'依据所选学生的长期画像安排重点；未知状态不会自动当作薄弱点。':'按所选水平设定先修要求，不发送学生个人画像；题目难度另行选择。';
  }
  function selectTopic(){
    const topic=catalog[$('quiz-topic').value];if(!topic)return;
    $('quiz-goal').textContent='教学目标：'+topic.goal;
    const selected=$('quiz-student').value;
    $('quiz-student').replaceChildren(option('','通用学生水平'),...roster.filter(s=>topic.student_ids.includes(s.id)).map(s=>option(s.id,s.name+' · '+s.subtitle)));
    if(topic.student_ids.includes(Number(selected)))$('quiz-student').value=selected;
    $('quiz-knowledge').replaceChildren(...topic.knowledge_ids.map((id,index)=>{
      const label=el('label');const input=el('input');input.type='checkbox';input.value=id;input.name='quiz-point';
      label.append(input,document.createTextNode(topic.knowledge_names?.[index]||pointNames.get(id)||'知识点 '+id));return label;
    }));
    describeStudent();$('quiz-generate').disabled=submitting;
  }
  async function refreshCatalog(){
    const data=await api('/api/course-tasks'),selected=$('quiz-topic').value;
    catalog=data.topics;roster=data.students;pointNames=new Map(data.knowledge_points.map(k=>[k.id,k.name]));
    $('quiz-topic').replaceChildren(...Object.entries(catalog).sort(([,a],[,b])=>Number(Boolean(b.custom))-Number(Boolean(a.custom))).map(([key,t])=>option(key,(t.custom?'':'示例 · ')+[t.stage,t.course,t.title].filter(Boolean).join(' / '))));
    if(catalog[selected])$('quiz-topic').value=selected;
    $('quiz-topic').disabled=!Object.keys(catalog).length;selectTopic();
  }
  function restoreSettings(row){
    if(catalog[row.topic_key]){$('quiz-topic').value=row.topic_key;selectTopic();}
    const sid=row.student_id===null?'':String(row.student_id);
    if(Array.from($('quiz-student').options).some(o=>o.value===sid))$('quiz-student').value=sid;
    $('quiz-level').value=row.options.student_level;describeStudent();
    $('quiz-count').value=row.options.count;$('quiz-difficulty').value=row.options.difficulty;$('quiz-requirements').value=row.options.requirements;
    for(const input of document.querySelectorAll('input[name=quiz-type]'))input.checked=row.options.types.includes(input.value);
    for(const input of document.querySelectorAll('input[name=quiz-point]'))input.checked=row.options.knowledge_ids.includes(Number(input.value));
    allocation();
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
    $('quiz-history').replaceChildren(...data.quizzes.map(q=>{
      const b=el('button');b.type='button';b.setAttribute('aria-current',String(q.id===displayed?.id));
      b.append(el('strong',q.title),el('span',`${statusLabels[q.status]} · ${q.options.count} 题 · ${difficultyLabels[q.options.difficulty]} · ${new Date(q.created_at).toLocaleString('zh-CN')}`,'helper'));
      b.onclick=()=>watch(q.id).catch(e=>showError(e.message));return b;
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
      const data={topic:$('quiz-topic').value,student_id:$('quiz-student').value?Number($('quiz-student').value):null,student_level:$('quiz-level').value,count:Number($('quiz-count').value),difficulty:$('quiz-difficulty').value,types,knowledge_ids:Array.from(document.querySelectorAll('input[name=quiz-point]:checked'),n=>Number(n.value)),requirements:$('quiz-requirements').value};
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
  $('quiz-student').onchange=()=>{if($('quiz-student').value)$('quiz-level').value='profile';describeStudent();};
  $('quiz-level').onchange=describeStudent;$('quiz-count').oninput=allocation;$('quiz-difficulty').onchange=allocation;
  $('quiz-student-view').onclick=()=>{teacher=false;drawResult();};$('quiz-teacher-view').onclick=()=>{teacher=true;drawResult();};$('quiz-format').onchange=drawDownload;
  $('quiz-refresh-catalog').onclick=()=>refreshCatalog().catch(e=>showError(e.message));$('quiz-refresh-history').onclick=()=>refreshHistory().catch(e=>showError(e.message));
  allocation();const catalogReady=refreshCatalog();Promise.all([catalogReady,refreshHistory()]).catch(e=>showError(e.message));
  const last=localStorage.getItem('activeQuizJob');if(last)watch(last).catch(()=>localStorage.removeItem('activeQuizJob'));
})();
