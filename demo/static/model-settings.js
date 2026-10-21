(() => {
 const channels=['llm','vlm','embedding'];
 const form=document.getElementById('model-settings-form');
 if(!form)return;
 const status=document.getElementById('model-settings-status');
 const failure=document.getElementById('model-settings-error');
 const save=document.getElementById('save-model-settings');
 const versions={llm:0,vlm:0,embedding:0};
 let testing=0,saving=false;
 const input=(name,field)=>document.getElementById(name+'-'+field);
 function channel(name){return {base_url:input(name,'base-url').value.trim(),model:input(name,'model').value.trim(),api_key:input(name,'api-key').value,clear_key:input(name,'clear-key').checked};}
 function render(data){
  for(const name of channels){
   const c=data.channels[name];
   input(name,'base-url').value=c.base_url;
   input(name,'model').value=c.model;
   input(name,'api-key').value='';
   input(name,'clear-key').checked=false;
   input(name,'api-key').placeholder=c.key_present?'已配置 · 留空保留原密钥':'未配置 · 可填写新密钥';
  }
 }
 async function request(url,payload){
  const response=await fetch(url,payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:undefined);
  let data;try{data=await response.json();}catch{throw new Error('服务未返回有效响应，请确认本地服务仍在运行。');}
  if(!response.ok)throw new Error(data.error||'操作失败，请重试。');
  return data;
 }
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
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(testing||saving)return;saving=true;failure.hidden=true;
  const payload={channels:Object.fromEntries(channels.map(name=>[name,channel(name)]))};
  const controls=Array.from(form.querySelectorAll('input,button'));
  const previous=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);
  status.textContent='正在保存配置…';
  try{const data=await request('/api/model-settings',payload);render(data);status.textContent=data.message;}
  catch(e){failure.textContent=e.message;failure.hidden=false;status.textContent='保存失败，修改尚未生效。';}
  finally{saving=false;controls.forEach((c,i)=>c.disabled=previous[i]);save.disabled=false;}
 });
 request('/api/model-settings').then(data=>{
  render(data);form.querySelectorAll('input,button').forEach(c=>c.disabled=false);
  status.textContent=data.restart_required?'检测到已保存配置与运行配置不同，请重启服务后生效。':'配置已加载。可以先测试连接，再保存修改。';
 }).catch(e=>{status.textContent='模型配置读取失败，请刷新页面重试。';failure.textContent=e.message;failure.hidden=false;});
})();
