'use strict';
let appearanceRecord=null,appearanceDraft=null,appearanceSaving=false,appearanceColor=null,appearanceSerial=0;
const appearanceOrder=['tribe','hair','face','height','bust','tail_or_ear_type','ear_muscle_tail_size','jaw','eyebrows','eyes','nose','mouth','skin_color','hair_color','highlight_color','right_eye_color','left_eye_color','facial_feature_color','lip_color_or_fur_pattern','face_paint','face_paint_color'];
function appearanceChangeStatus(){
 if(!appearanceRecord||!appearanceDraft)return;
 const same=(a,b)=>a&&b&&Object.keys(a).every(k=>a[k]===b[k]);
 const saved=same(appearanceDraft,appearanceRecord.parameters),updated=same(appearanceDraft,appearanceRecord.appearance?.rendered_parameters);
 $('#appearance-change-status').textContent=saved?(updated?'模型与预设已一致':'预设已保存，模型尚未更新'):(updated?'模型已更新，修改尚未保存':'修改尚未保存，模型尚未更新');
}
function appearanceFields(){
 const fields=[...appearanceRecord.fields].sort((a,b)=>appearanceOrder.indexOf(a.field)-appearanceOrder.indexOf(b.field));
 $('#appearance-fields').innerHTML=fields.map(option=>{
  const value=appearanceDraft[option.field]&option.mask,choices=[...option.choices];
  if(!choices.some(c=>c.value===value))choices.push({value,label:`${value}（当前预设）`});
  const selected=choices.find(c=>c.value===value);let control;
  if(option.kind==='slider')control=`<div class="appearance-slider"><input type="range" data-appearance-field="${option.field}" min="${choices[0].value}" max="${choices.at(-1).value}" value="${value}"><output>${value}</output></div>`;
  else if(option.kind==='color')control=`<button class="appearance-color secondary" data-appearance-color="${option.field}"><i class="swatch" style="background:${selected.color||'#5b6470'}"></i>颜色 ${value}<span>选择 ›</span></button>`;
  else control=`<div class="appearance-select">${selected.icon_id?`<img src="/api/icons/${selected.icon_id}.png" alt="" width="38" height="38">`:''}<select data-appearance-field="${option.field}">${choices.map(c=>`<option value="${c.value}" ${c.value===value?'selected':''}>${esc(c.label)}</option>`).join('')}</select></div>`;
  return `<label class="appearance-field"><span>${esc(option.label)}</span>${control}</label>`;
 }).join('');
 const toggles=[['highlight_flags',128,'启用挑染'],['mouth',128,'启用唇色'],['eyes',128,'瞳孔形态选项'],['face_paint',128,'镜像彩绘']].filter(([field])=>appearanceDraft.race!==7||field!=='mouth');
 $('#appearance-flags').innerHTML=`<div class="appearance-toggles">${toggles.map(([field,bit,label])=>`<label><input type="checkbox" data-appearance-bit="${field}" data-bit="${bit}" ${appearanceDraft[field]&bit?'checked':''}>${label}</label>`).join('')}</div><strong>面部特征</strong><div class="appearance-toggles">${Array.from({length:7},(_,i)=>`<label><input type="checkbox" data-appearance-bit="facial_features" data-bit="${1<<i}" ${appearanceDraft.facial_features&(1<<i)?'checked':''}>特征 ${i+1}</label>`).join('')}</div>`;
 appearanceChangeStatus();
}
async function openAppearance(id=boot.preset_id){
 if(!(ready||boot?.can_select_preset)||busy||appearanceSaving)return;const serial=++appearanceSerial;
 try{const record=await request('/api/appearance?id='+encodeURIComponent(id));if(serial!==appearanceSerial)return;
  appearanceRecord=record;appearanceDraft=clone(record.appearance?.is_preview?record.appearance.rendered_parameters:record.parameters);$('#appearance-location').textContent=`${record.file_name} · 软件预设库`;
  $('#appearance-location').title=record.directory+'\\'+record.file_name;$('#appearance-error').hidden=true;appearanceFields();$('#appearance-dialog').showModal();
 }catch(e){showError(e.message,e.detail||'')}
}
async function submitAppearance(preview){
 if(appearanceSaving||busy)return;appearanceSaving=true;statusBusy(true);$('#render-status').hidden=!preview;$('#appearance-fields').inert=true;$('#appearance-flags').inert=true;$('#appearance-save').disabled=true;$('#appearance-update').disabled=true;$('#appearance-update').textContent=preview?'正在准备模型…':'更新模型';$('#appearance-error').hidden=true;
 try{const changes=Object.fromEntries(Object.entries(appearanceDraft).filter(([field,value])=>value!==appearanceRecord.parameters[field]));
  const r=await request(preview?'/api/appearance/preview':'/api/appearance/save',{id:appearanceRecord.id,sha256:appearanceRecord.sha256,changes});
  boot=await request('/api/bootstrap');appearanceRecord.appearance=r.appearance;
  if(preview){ready=true;pending=false;state=clone(r.state);good=clone(state);await setPreview(r.image,r.notices);$('#appearance-dialog').close();toast(r.appearance.model_matches_file?'模型已更新':'模型已更新，预设尚未保存')}
  else{appearanceRecord.sha256=r.preset.sha256;appearanceRecord.parameters=clone(r.preset.parameters);toast('已保存到 '+r.preset.file_name)}
  updatePreset();updateOutfit();appearanceChangeStatus();$('#error-bar').hidden=true;
 }catch(e){$('#appearance-error').textContent=e.detail||e.message;$('#appearance-error').hidden=false}
 finally{appearanceSaving=false;statusBusy(false);$('#appearance-fields').inert=false;$('#appearance-flags').inert=false;$('#appearance-save').disabled=false;$('#appearance-update').disabled=false;$('#appearance-update').textContent='更新模型';if(pending)pumpRender()}
}
window.addEventListener('DOMContentLoaded',()=>{
 $('#appearance-open').onclick=()=>openAppearance();$('#appearance-save').onclick=()=>submitAppearance(false);$('#appearance-update').onclick=()=>submitAppearance(true);
 $('[data-appearance-close]').onclick=()=>{if(!appearanceSaving){++appearanceSerial;$('#appearance-dialog').close()}};
 $('#appearance-dialog').oncancel=e=>{if(appearanceSaving)e.preventDefault()};
 $('#appearance-fields').oninput=e=>{const control=e.target.closest('[data-appearance-field]');if(!control||appearanceSaving)return;const option=appearanceRecord.fields.find(x=>x.field===control.dataset.appearanceField);appearanceDraft[option.field]=(appearanceDraft[option.field]&~option.mask)|Number(control.value);if(option.kind==='slider')control.nextElementSibling.textContent=control.value;appearanceChangeStatus()};
 $('#appearance-fields').onchange=async e=>{
  const control=e.target.closest('[data-appearance-field]');if(!control||appearanceSaving)return;
  if(control.dataset.appearanceField==='tribe'){
   const serial=++appearanceSerial;$('#appearance-save').disabled=true;$('#appearance-update').disabled=true;
   try{const record=await request(`/api/appearance?id=${encodeURIComponent(appearanceRecord.id)}&tribe=${appearanceDraft.tribe}`);if(serial!==appearanceSerial)return;appearanceRecord.fields=record.fields;appearanceFields()}
   catch(error){$('#appearance-error').textContent=error.message;$('#appearance-error').hidden=false}
   finally{if(serial===appearanceSerial){$('#appearance-save').disabled=false;$('#appearance-update').disabled=false}}
  }else if(control.dataset.appearanceField==='hair'||control.dataset.appearanceField==='face_paint')appearanceFields();
 };
 $('#appearance-flags').onchange=e=>{const control=e.target.closest('[data-appearance-bit]');if(!control||appearanceSaving)return;const field=control.dataset.appearanceBit,bit=Number(control.dataset.bit);appearanceDraft[field]=control.checked?appearanceDraft[field]|bit:appearanceDraft[field]&~bit;appearanceChangeStatus()};
 $('#appearance-fields').onclick=e=>{const button=e.target.closest('[data-appearance-color]');if(!button||appearanceSaving)return;appearanceColor=appearanceRecord.fields.find(x=>x.field===button.dataset.appearanceColor);$('#appearance-color-title').textContent=appearanceColor.label;$('#appearance-color-grid').innerHTML=appearanceColor.choices.map(c=>`<button class="color-choice ${c.value===appearanceDraft[appearanceColor.field]?'selected':''}" data-appearance-color-value="${c.value}" style="background:${c.color}" title="颜色 ${c.value}" aria-label="颜色 ${c.value}"></button>`).join('');$('#appearance-color-dialog').showModal()};
 $('#appearance-color-grid').onclick=e=>{const b=e.target.closest('[data-appearance-color-value]');if(!b)return;appearanceDraft[appearanceColor.field]=Number(b.dataset.appearanceColorValue);$('#appearance-color-dialog').close();appearanceFields()};
 $('#appearance-color-close').onclick=()=>$('#appearance-color-dialog').close();
});
