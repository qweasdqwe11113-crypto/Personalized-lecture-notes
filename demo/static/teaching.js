const teachingDefaults = {duration:45,lesson_type:'new',detail:'standard',examples:2,exercises:3,requirements:''};
const teachingFields = {duration:'lesson-duration',lesson_type:'lesson-type',detail:'lesson-detail',examples:'lesson-examples',exercises:'lesson-exercises',requirements:'teaching-requirements',target_characters:'lesson-target'};
function readTeachingOptions(){return {...Object.fromEntries(Object.entries(teachingFields).map(([key,id])=>[key,['duration','examples','exercises','target_characters'].includes(key)?Number($(id).value):$(id).value])),length_policy:'duration-v2'};}
function restoreTeachingOptions(options){const values={...teachingDefaults,...options};for(const [key,id] of Object.entries(teachingFields))$(id).value=values[key]??'';updateLengthNote();}
function setTeachingBusy(value){for(const id of Object.values(teachingFields))$(id).disabled=value;document.querySelectorAll('[data-teaching-preset]').forEach(b=>b.disabled=value);$('adjust-section').disabled=value||!lastResult;}
for(const b of document.querySelectorAll('[data-teaching-preset]'))b.onclick=()=>{const field=$('teaching-requirements');if(!field.value.includes(b.dataset.teachingPreset))field.value=(field.value.trim()+'\n'+b.dataset.teachingPreset).trim().slice(0,2000);field.focus();};

function updateLengthNote(){const options=readTeachingOptions();let low,high;const target=options.target_characters;if(target){low=Math.round(target*.8);high=Math.min(8000,Math.round(target*1.2));}else{const base=Math.min(6600,Math.max(500,options.duration*({brief:35,standard:100,detailed:145}[options.detail]||100)));low=Math.max(400,Math.round(base*.8));high=Math.min(8000,Math.round(base*1.2));}$('lesson-length-note').textContent=`本次建议约 ${low}—${high} 汉字，按时长与详略计算；建议时间不等于实际授课计时。`;}
for(const id of ['lesson-duration','lesson-detail','lesson-target'])$(id).addEventListener('input',updateLengthNote);
updateLengthNote();
