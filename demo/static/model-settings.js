(() => {
 const channels=['llm','vlm','embedding'];
 const LABELS={llm:'语言模型',vlm:'视觉模型',embedding:'嵌入模型'};
 const KINDS={chat:'文字',vision:'视觉',embedding:'嵌入',other:'其他'};
 const FITS={llm:['chat','vision'],vlm:['vision'],embedding:['embedding']};
 const form=document.getElementById('model-settings-form');
 if(!form)return;
 const status=document.getElementById('model-settings-status');
 const failure=document.getElementById('model-settings-error');
 const save=document.getElementById('save-model-settings');
 const versions={llm:0,vlm:0,embedding:0};
 const keyPresent={llm:false,vlm:false,embedding:false};
 let testing=0,saving=false;
 // Inside the desktop client the saved settings can take effect at once: it restarts the service
 // and reloads this window. In a browser the note above tells how to restart.
 const desktop=window.zhiyeDesktop,restartRow=document.querySelector('.model-restart-row');
 const offerRestart=needed=>{if(restartRow)restartRow.hidden=!(needed&&desktop?.restartBackend);};
 document.getElementById('model-restart')?.addEventListener('click',async event=>{event.target.disabled=true;status.textContent='正在重启服务…';try{await desktop.restartBackend();}catch(e){event.target.disabled=false;failure.textContent=e.message||'重启失败。';failure.hidden=false;}});
 const input=(name,field)=>document.getElementById(name+'-'+field);
 const controls=()=>Array.from(form.querySelectorAll('input,button,select,textarea'));
 function channel(name){return {base_url:input(name,'base-url').value.trim(),model:input(name,'model').value.trim(),api_key:input(name,'api-key').value,clear_key:input(name,'clear-key').checked};}
 function render(data){
  for(const name of channels){
   const c=data.channels[name];
   input(name,'base-url').value=c.base_url;
   input(name,'model').value=c.model;
   input(name,'api-key').value='';
   input(name,'clear-key').checked=false;
   keyPresent[name]=Boolean(c.key_present);
   input(name,'api-key').placeholder=c.key_present?'已配置 · 留空保留原密钥':'未配置 · 可填写新密钥';
  }
 }
 async function request(url,payload){
  const response=await fetch(url,payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:undefined);
  let data;try{data=await response.json();}catch{throw new Error('服务未返回有效响应，请确认本地服务仍在运行。');}
  if(!response.ok)throw new Error(data.error||'操作失败，请重试。');
  return data;
 }
 // Fill one card as if the teacher had typed it, so the usual "changed, test again" state follows.
 function setChannel(name,values){
  if(values.base_url!==undefined)input(name,'base-url').value=values.base_url;
  if(values.model!==undefined)input(name,'model').value=values.model;
  if(values.api_key)input(name,'api-key').value=values.api_key;
  if('clear_key' in values||values.api_key)input(name,'clear-key').checked=Boolean(values.clear_key&&!values.api_key);
  input(name,'base-url').dispatchEvent(new Event('input'));
 }
 // The models a service offers, the ones that fit this card first, as choices under the model field.
 function fillModels(name,models){
  const list=document.getElementById(name+'-models');
  if(!list||!Array.isArray(models))return;
  const fits=FITS[name];
  const sorted=models.filter(m=>m.kind!=='other').sort((a,b)=>(fits.includes(b.kind)-fits.includes(a.kind))||(fits.indexOf(a.kind)-fits.indexOf(b.kind)));
  list.replaceChildren(...sorted.map(m=>{const option=document.createElement('option');option.value=m.id;option.label=KINDS[m.kind]+(m.size?` · ${m.size}`:'');return option;}));
 }
 const count=models=>{const n={chat:0,vision:0,embedding:0};for(const m of models||[])if(m.kind in n)n[m.kind]++;return n;};
 const testAll=names=>names.forEach(name=>{if(input(name,'model').value.trim())document.getElementById('test-'+name).click();});
 for(const name of channels){
  const button=document.getElementById('test-'+name);
  const result=document.getElementById(name+'-test-status');
  for(const field of ['base-url','model','api-key','clear-key'])input(name,field).addEventListener('input',()=>{
   versions[name]++;result.textContent='配置已修改，请重新测试。';delete result.dataset.state;
   status.textContent='有未保存的修改。测试成功后，点击“保存配置”并重启服务。';
  });
  button.addEventListener('click',async()=>{
   const version=++versions[name];button.disabled=true;testing++;save.disabled=true;result.dataset.state='pending';
   result.textContent=name==='vlm'?'正在发送测试图片并核对识别结果…':'正在实际调用模型，请稍候…';
   try{
    const data=await request('/api/model-settings/test',{channel:name,config:channel(name)});
    if(version!==versions[name])return;
    result.textContent=data.message;result.dataset.state=data.vision_verified===false?'warning':'success';
   }catch(e){if(version===versions[name]){result.textContent=e.message;result.dataset.state='error';}}
   finally{button.disabled=false;testing--;save.disabled=saving||testing>0;}
  });
  const lister=input(name,'list-models');
  lister?.addEventListener('click',async()=>{
   lister.disabled=true;result.dataset.state='pending';result.textContent='正在读取模型列表…';
   try{
    const data=await request('/api/model-settings/models',{channel:name,config:channel(name)});
    fillModels(name,data.models);
    const values={};
    if(data.base_url!==channel(name).base_url)values.base_url=data.base_url;
    if(!channel(name).model&&data.suggested?.[name])values.model=data.suggested[name];
    if(Object.keys(values).length)setChannel(name,values);
    const n=count(data.models),fit=FITS[name].reduce((sum,kind)=>sum+n[kind],0);
    result.textContent=`读到 ${data.models.length} 个模型，其中适合${LABELS[name]}的 ${fit} 个，可在「模型名称」里选择。`+(values.base_url?`服务地址已改为 ${data.base_url}。`:'');
    result.dataset.state=fit?'success':'warning';
   }catch(e){result.textContent=e.message;result.dataset.state='error';}
   finally{lister.disabled=false;}
  });
 }

 // ---------------------------------------------------------------- quick setup
 const detect=document.getElementById('model-detect'),detected=document.getElementById('model-detect-result');
 const provider=document.getElementById('model-provider'),pasted=document.getElementById('model-import-text');
 const importAll=document.getElementById('model-import-all'),importer=document.getElementById('model-import');
 const importStatus=document.getElementById('model-import-status');
 function describeFill(plan){return channels.filter(name=>plan[name]).map(name=>`${LABELS[name]} ${plan[name]}`).join('，');}
 function useService(service){
  const plan={};
  for(const name of channels){
   const model=service.suggested?.[name];
   if(!model)continue;
   plan[name]=model;
   setChannel(name,{base_url:service.base_url,model,clear_key:keyPresent[name]});
   fillModels(name,service.models);
  }
  const missing=channels.filter(name=>!plan[name]).map(name=>LABELS[name]);
  status.textContent=`已填入 ${service.name}（端口 ${service.port}）：${describeFill(plan)||'没有可用的模型'}。`+
   (missing.length?`没有找到${missing.join('、')}，这几项保持原样${missing.includes('视觉模型')?'（视觉模型可以不配置）':''}。`:'')+'正在测试连接，通过后点「保存配置」。';
  testAll(Object.keys(plan));
 }
 function renderServices(services){
  detected.replaceChildren();
  if(!services.length){detected.textContent='没有找到正在运行的本机模型服务。请先启动 Ollama、LM Studio 等，再点一次检测；服务在别的电脑上时，请在右侧粘贴它的地址。';return;}
  for(const service of services){
   const row=document.createElement('div');row.className='model-service';
   const text=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('span');
   title.textContent=`${service.name} · 端口 ${service.port}`;meta.className='helper';
   const n=count(service.models);
   meta.textContent=service.needs_key?'需要密钥：请在右侧粘贴地址和密钥':`${service.models.length} 个模型（文字 ${n.chat}、视觉 ${n.vision}、嵌入 ${n.embedding}）`;
   text.append(title,meta);
   const use=document.createElement('button');use.type='button';use.className='secondary';
   use.textContent=service.needs_key?'填入地址':'使用这个服务';
   use.addEventListener('click',()=>{
    if(service.needs_key){pasted.value=service.base_url;pasted.focus();importStatus.textContent='已填入地址，请在后面接着粘贴密钥，再点「识别并填入」。';return;}
    useService(service);
   });
   row.append(text,use);detected.append(row);
  }
 }
 detect.addEventListener('click',async()=>{
  detect.disabled=true;detected.textContent='正在检测本机常用端口（11434、1234、8000、8080 等）…';
  try{renderServices((await request('/api/model-settings/detect',{})).services);}
  catch(e){detected.textContent=e.message;}
  finally{detect.disabled=false;}
 });
 importer.addEventListener('click',async()=>{
  importer.disabled=true;importStatus.dataset.state='pending';importStatus.textContent='正在识别，并读取模型列表…';
  try{
   const data=await request('/api/model-settings/import',{text:pasted.value,provider:provider.value});
   const found=data.found,models=data.models,suggested=data.suggested||{},plan={};
   const noKey=found.local&&!found.api_key;
   plan.llm=found.model||suggested.llm||'';
   setChannel('llm',{base_url:found.base_url,model:plan.llm,api_key:found.api_key,clear_key:noKey&&keyPresent.llm});
   fillModels('llm',models);
   for(const name of ['vlm','embedding']){
    const own=found.channels?.[name];
    if(own){plan[name]=own.model;setChannel(name,{base_url:own.base_url,model:own.model,api_key:own.api_key,clear_key:found.local&&!own.api_key&&keyPresent[name]});fillModels(name,models);continue;}
    if(importAll.checked&&suggested[name]){plan[name]=suggested[name];setChannel(name,{base_url:found.base_url,model:suggested[name],api_key:found.api_key,clear_key:noKey&&keyPresent[name]});fillModels(name,models);}
   }
   pasted.value='';   // it held a key
   const parts=[`从${found.source}读到：${found.provider?found.provider+'，':''}地址 ${found.base_url}${found.api_key?'，密钥已填入（不显示）':''}。`];
   if(models)parts.push(`这个服务有 ${models.length} 个模型。`);
   const filled=describeFill(plan);
   if(filled)parts.push(`已填入：${filled}。`);
   if(!plan.llm)parts.push(models?'请在语言模型的「模型名称」里选择一个模型。':'请在语言模型里填写模型名称（按服务商文档）。');
   parts.push(...found.notes);
   importStatus.textContent=parts.join('');importStatus.dataset.state=plan.llm?'success':'warning';
   status.textContent='已按粘贴的配置填入，正在测试连接；通过后点「保存配置」。';
   testAll(Object.keys(plan).filter(name=>plan[name]));
  }catch(e){importStatus.textContent=e.message;importStatus.dataset.state='error';}
  finally{importer.disabled=false;}
 });

 form.addEventListener('submit',async event=>{
  event.preventDefault();if(testing||saving)return;saving=true;failure.hidden=true;
  const payload={channels:Object.fromEntries(channels.map(name=>[name,channel(name)]))};
  const all=controls();
  const previous=all.map(c=>c.disabled);all.forEach(c=>c.disabled=true);
  status.textContent='正在保存配置…';
  try{const data=await request('/api/model-settings',payload);render(data);status.textContent=desktop?.restartBackend?'已保存。点击「立即重启服务」后生效；正在运行的任务会中断。':data.message;offerRestart(data.restart_required);}
  catch(e){failure.textContent=e.message;failure.hidden=false;status.textContent='保存失败，修改尚未生效。';}
  finally{saving=false;all.forEach((c,i)=>c.disabled=previous[i]);save.disabled=false;}
 });
 request('/api/model-settings').then(data=>{
  render(data);
  for(const p of data.providers||[]){const option=document.createElement('option');option.value=p.id;option.textContent=`${p.name}（${p.base_url}）`;provider.append(option);}
  controls().forEach(c=>c.disabled=false);
  status.textContent=data.restart_required?'检测到已保存配置与运行配置不同，请重启服务后生效。':'配置已加载。可以用「快速配置」自动填写，或直接修改下面的卡片；先测试连接，再保存。';offerRestart(data.restart_required);
 }).catch(e=>{status.textContent='模型配置读取失败，请刷新页面重试。';failure.textContent=e.message;failure.hidden=false;});
})();
