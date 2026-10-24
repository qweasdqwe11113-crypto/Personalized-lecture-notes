const teachingDefaults = {duration:45,lesson_type:'new',detail:'standard',examples:2,exercises:3,requirements:''};
const teachingFields = {duration:'lesson-duration',lesson_type:'lesson-type',detail:'lesson-detail',examples:'lesson-examples',exercises:'lesson-exercises',requirements:'teaching-requirements'};
function readTeachingOptions(){return Object.fromEntries(Object.entries(teachingFields).map(([key,id])=>[key,['duration','examples','exercises'].includes(key)?Number($(id).value):$(id).value]));}
function restoreTeachingOptions(options){const values={...teachingDefaults,...options};for(const [key,id] of Object.entries(teachingFields))$(id).value=values[key];}
function setTeachingBusy(value){for(const id of Object.values(teachingFields))$(id).disabled=value;document.querySelectorAll('[data-teaching-preset]').forEach(b=>b.disabled=value);$('adjust-section').disabled=value||!lastResult;}
for(const b of document.querySelectorAll('[data-teaching-preset]'))b.onclick=()=>{const field=$('teaching-requirements');if(!field.value.includes(b.dataset.teachingPreset))field.value=(field.value.trim()+'\n'+b.dataset.teachingPreset).trim().slice(0,2000);field.focus();};
