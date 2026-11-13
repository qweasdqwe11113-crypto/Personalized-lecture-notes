// Compare saved lectures and versions without generating, grading or rewriting.
(() => {
  const dialog = $('lecture-comparison-dialog');
  let rootId = null, options = null, result = null, session = 0;
  const statuses = {unreviewed:'原稿未审核',draft:'草稿',needs_revision:'待修订',reviewed:'教师已审核'};
  const labels = {same:'一致',different:'不同',unavailable:'未留存或未能核对'};
  const sectionLabels = {same:'文字一致',changed:'内容有差异',left_only:'仅左侧有此章节',right_only:'仅右侧有此章节'};
  const controls = ['lecture-comparison-left','lecture-comparison-other','lecture-comparison-right',
    'apply-lecture-comparison','reload-lecture-comparison'];
  function setLoading(value) { for (const id of controls) $(id).disabled = value || (!options && id!=='reload-lecture-comparison'); }
  function candidate(id) { return options.candidates.find(c => c.id === Number(id)); }
  function versionLabel(v) {
    return (v.revision_number ? `R${v.revision_number}` : '模型原稿') + ' · ' + statuses[v.status]
      + (v.recorder ? ' · ' + v.recorder : '');
  }
  function name(v) { return `#${v.id} ${v.name} · ${v.mode === 'none' ? '无画像' : '有画像'} · ${versionLabel(v)}`; }
  function versions(selectId, lecture, preferred=0) {
    $(selectId).replaceChildren(...lecture.versions.map(v => {
      const option=el('option',versionLabel(v));option.value=v.revision_number;return option;
    }));
    $(selectId).value=String(lecture.versions.some(v=>v.revision_number===Number(preferred))?preferred:0);
  }
  function showError(err) {
    $('lecture-comparison-error').textContent=err.message;
    $('lecture-comparison-error').hidden=false;
    $('lecture-comparison-status').textContent='读取失败，可刷新记录后重试。';
  }
  function side(title) { const box=el('section',undefined,'comparison-side');box.append(el('h4',title));return box; }
  function drawBasis(v, title) {
    const box=side(title+'：'+name(v));
    box.append(el('p',v.mode==='none'?'这份生成输入未使用学生画像。':v.profile||'旧记录未留存学习特点。','comparison-text'));
    if(v.mode!=='none') for(const state of v.states||[]) {
      box.append(el('p',`${state.name} · ${state.label||state.state}\n依据：${state.evidence??'未记录'}\n策略：${state.strategy??'未记录'}`,'comparison-text'));
    }
    box.append(el('p',`请求模型：${v.model}；本版本保存于 ${new Date(v.version_created_at).toLocaleString('zh-CN')}。`,'helper'));
    if(v.reason)box.append(el('p','修订理由：'+v.reason,'helper'));
    for(const material of v.materials||[]) {
      const record=el('details',undefined,'history-card');record.append(el('summary',`M${material.id} · ${material.title}`),el('p',material.content,'comparison-text'));box.append(record);
    }
    return box;
  }
  function drawSections() {
    const selected=$('lecture-comparison-section').value;
    const onlyChanged=$('lecture-comparison-differences').checked;
    const entries=result.sections.map((s,index)=>({section:s,index})).filter(({section:s,index})=>
      (selected==='all'||selected===String(index))&&(!onlyChanged||s.status!=='same'));
    $('lecture-comparison-summary').textContent=`共 ${result.summary.section_count} 段，${result.summary.different_sections} 段文字有差异；当前展示 ${entries.length} 段。`;
    $('lecture-comparison-sections').replaceChildren(...entries.map(({section:s})=>{
      const card=el('article',undefined,'comparison-card');
      const heading=el('div',undefined,'comparison-card-heading');
      heading.append(el('h3',s.title+(s.occurrence>1?`（第 ${s.occurrence} 次）`:'')),el('span',sectionLabels[s.status],'badge '+(s.status==='same'?'':'comparison-changed')));
      const pair=el('div',undefined,'comparison-pair');
      for(const [title,section,v] of [['左侧',s.left,result.left],['右侧',s.right,result.right]]) {
        const paper=side(title+' · '+v.name+' · '+(v.revision_number?`R${v.revision_number}`:'原稿'));
        if(section){
          paper.append(el('p',`正文第 ${section.start_line}—${section.end_line} 行`,'helper'));
          const text=el('div',undefined,'lecture lecture-comparison-markdown');text.append(renderMarkdown(section.text));paper.append(text);
        }else paper.append(el('p','此版本没有这一章节。','helper'));
        pair.append(paper);
      }
      card.append(heading,pair);
      if(s.changes.changed_blocks.length) {
        const detail=el('details',undefined,'comparison-evidence');
        detail.append(el('summary',`定位变动片段 · 左侧 ${s.changes.removed_lines} 行 / 右侧 ${s.changes.added_lines} 行`));
        for(const change of s.changes.changed_blocks) {
          const excerpts=el('div',undefined,'comparison-pair lecture-compare-excerpts');
          for(const [title,number,lines] of [['左侧',change.before_start,change.before],['右侧',change.after_start,change.after]]) {
            const box=side(lines.length?`${title}第 ${number} 行起`:`${title}无对应片段`);box.append(el('pre',lines.length?lines.join('\n'):'此处没有对应文本。','trace-text'));excerpts.append(box);
          }
          detail.append(excerpts);
        }
        card.append(detail);
      }
      return card;
    }));
    if(!entries.length)$('lecture-comparison-sections').append(el('p','当前筛选下没有有差异的章节；可以切换章节或取消勾选。','current-summary'));
  }
  function drawResult() {
    const warnings=[];
    if(result.controls.some(c=>c.status==='different'))warnings.push('存在不同的生成设置，不能把文字差异都归因于学生画像。');
    if(result.controls.some(c=>c.status==='unavailable'))warnings.push('部分历史设置未留存或未能核对，请结合实际来源复核。');
    if(result.kind==='revision')warnings.push('这是同一份讲义的保存版本对照，显示人工修订内容。');
    else if(result.left.revision_number||result.right.revision_number)warnings.push('比较包含人工修订，差异也可能来自教师修改。');
    if(result.section_order_changed)warnings.push('两侧章节的存在情况或顺序不同；以下按左侧顺序对齐，右侧新增章节另列。');
    warnings.push('对照仅显示实际文字与输入记录，不判断哪一份讲义更适合学生。');
    $('lecture-comparison-warning').textContent=warnings.join('\n');
    $('lecture-comparison-controls').replaceChildren(...result.controls.map(c=>el('span',c.label+'：'+labels[c.status],
      'badge '+(c.status==='different'?'comparison-changed':''))));
    $('lecture-comparison-basis').replaceChildren(drawBasis(result.left,'左侧'),drawBasis(result.right,'右侧'));
    const all=el('option','全部章节');all.value='all';
    $('lecture-comparison-section').replaceChildren(all,...result.sections.map((s,index)=>{
      const option=el('option',s.title+(s.occurrence>1?`（第 ${s.occurrence} 次）`:''));option.value=index;return option;
    }));
    drawSections();$('lecture-comparison-output').hidden=false;
    $('lecture-comparison-status').textContent=`左 ${name(result.left)} ↔ 右 ${name(result.right)}`;
  }
  async function loadPair() {
    const token=++session,id=rootId;
    result=null;$('lecture-comparison-output').hidden=true;$('lecture-comparison-error').hidden=true;
    $('lecture-comparison-status').textContent='正在读取所选的已保存版本…';setLoading(true);
    try {
      const params=new URLSearchParams({other_id:$('lecture-comparison-other').value,
        left_revision:$('lecture-comparison-left').value,right_revision:$('lecture-comparison-right').value});
      const data=await api(`/api/lectures/${id}/comparison?${params}`);
      if(token!==session||!dialog.open||id!==rootId)return;
      result=data;drawResult();
    }catch(err){if(token===session&&dialog.open)showError(err);}
    finally{if(token===session)setLoading(false);}
  }
  async function loadOptions(preserve=false) {
    const saved=preserve?{left:$('lecture-comparison-left').value,other:$('lecture-comparison-other').value,right:$('lecture-comparison-right').value}:null;
    const token=++session,id=rootId;
    result=null;options=null;$('lecture-comparison-output').hidden=true;$('lecture-comparison-error').hidden=true;
    $('lecture-comparison-status').textContent='正在查找实际任务与目标一致的讲义…';setLoading(true);
    try {
      const data=await api(`/api/lectures/${id}/comparison-options`);
      if(token!==session||!dialog.open||id!==rootId)return;
      options=data;
      const base=candidate(id),left=Number(saved?saved.left:lastResult?.revision_number||0);
      versions('lecture-comparison-left',base,left);
      $('lecture-comparison-goal').textContent=`${data.topic}\n共同教学目标：${data.goal}`;
      $('lecture-comparison-other').replaceChildren(...data.candidates.map(c=>{
        const option=el('option',`#${c.id} ${c.name} · ${c.mode==='none'?'无画像':'有画像'} · ${new Date(c.created_at).toLocaleDateString('zh-CN')}`);option.value=c.id;return option;
      }));
      const alternate=base.versions.find(v=>v.revision_number!==Number($('lecture-comparison-left').value));
      const preferred=saved&&candidate(saved.other)?candidate(saved.other):alternate?base:
        data.candidates.find(c=>c.student_id!==base.student_id)||data.candidates.find(c=>c.id!==id)||base;
      $('lecture-comparison-other').value=String(preferred.id);
      versions('lecture-comparison-right',preferred,saved?saved.right:preferred.id===id&&alternate?alternate.revision_number:0);
      $('lecture-comparison-options-note').textContent=`可选 ${data.candidates.length} 份同任务、同目标讲义；原稿和已有修订分别选择。`
        +(data.truncated?' 部分更早记录未在选择列表中展示。':'')
        +(data.candidates.length===1&&base.versions.length===1?' 当前只有一个已保存版本，左右内容相同。':'');
      await loadPair();
    }catch(err){if(token===session&&dialog.open)showError(err);}
    finally{if(token===session)setLoading(false);}
  }
  function changedSelection() {
    session++;result=null;$('lecture-comparison-output').hidden=true;
    $('lecture-comparison-status').textContent='选择已更改，点击“比较所选版本”查看。';
  }
  window.clearLectureComparison=()=>{session++;rootId=null;options=null;result=null;$('compare-lectures').disabled=true;if(dialog.open)dialog.close();};
  window.showLectureComparison=r=>{if(rootId!==r.id)window.clearLectureComparison();rootId=r.id;$('compare-lectures').disabled=busy;};
  window.setLectureComparisonBusy=value=>{$('compare-lectures').disabled=value||!rootId;};
  $('compare-lectures').onclick=()=>{if(busy||!rootId)return;$('lecture-comparison-differences').checked=false;dialog.showModal();loadOptions();};
  $('close-lecture-comparison').onclick=()=>{session++;dialog.close();};
  dialog.addEventListener('cancel',()=>{session++;});
  $('reload-lecture-comparison').onclick=()=>loadOptions(true);
  $('lecture-comparison-form').onsubmit=event=>{event.preventDefault();if(options)loadPair();};
  $('lecture-comparison-left').onchange=changedSelection;
  $('lecture-comparison-right').onchange=changedSelection;
  $('lecture-comparison-other').onchange=()=>{if(options)versions('lecture-comparison-right',candidate($('lecture-comparison-other').value));changedSelection();};
  $('lecture-comparison-section').onchange=()=>{if(result)drawSections();};
  $('lecture-comparison-differences').onchange=()=>{if(result)drawSections();};
})();
