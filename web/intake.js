'use strict';
const intakeFlow={allowSubmit:false,answers:{},request:0,controller:null,query:'',questions:[]};
state.intakeExplicit=state.intakeExplicit||{};
function setIntakePhase(phase){
  document.body.classList.toggle('is-intaking',phase==='understanding');
  document.body.classList.toggle('is-clarifying',phase==='clarify'||phase==='error');
  if(phase){document.body.classList.remove('has-result','is-running');for(const id of ['result','poster-workspace','loading','task-error']){const el=$('#'+id);if(el)el.hidden=true;}}
  $('#intake-panel').hidden=!phase;
  $('#clarification-form').hidden=phase!=='clarify';
  $('#intake-error').hidden=phase!=='error';
}
function intakeChoices(question){
  return (Array.isArray(question.options)?question.options:[]).map(option=>typeof option==='string'?{value:option,label:option}:option).filter(option=>option&&typeof option.value==='string');
}
function showIntakeQuestions(questions){
  intakeFlow.questions=questions;
  $('#intake-title').textContent=questions.length===1?'再确认一点，就可以开始。':'补充这几件事，就可以开始。';
  $('#intake-description').textContent='';
  $('#intake-questions').innerHTML=questions.map((question,index)=>{
    const field=String(question.field||'scene'),id='intake-answer-'+index,options=intakeChoices(question);
    return `<fieldset class="clarification-field"><legend>${esc(question.label||question.question||'还需要了解什么？')}</legend>${options.length?`<div class="clarification-choices">${options.map(option=>`<label><input type="radio" name="intake-${index}" value="${esc(option.value)}" ${intakeFlow.answers[field]===option.value?'checked':''}><span>${esc(option.label||option.value)}</span></label>`).join('')}</div>`:`<input id="${id}" data-intake-answer="${esc(field)}" type="text" aria-label="${esc(question.label||question.question||'补充信息')}" value="${esc(intakeFlow.answers[field]||'')}" maxlength="1000" autocomplete="off">`}</fieldset>`;
  }).join('');
  setIntakePhase('clarify');
  $('#intake-continue').textContent='继续';
  const first=$('#intake-questions input');if(first)first.focus();
}
function applyUnderstoodTask(task){
  for(const field of ['product','audience','stage','goal'])if(typeof task[field]==='string'&&task[field])$('#'+field).value=task[field];
  if(task.platform&&Object.hasOwn(labels,task.platform))setPlatform(task.platform);
  if(task.requirements&&typeof task.requirements==='string'&&!$('#requirements').value.trim())$('#requirements').value=task.requirements;
  state.intakeQuery=intakeFlow.query;
  state.intakeResolved={product:$('#product').value,audience:$('#audience').value,platform:state.platform};
}
async function understandTask(){
  const query=$('#scene').value.trim();
  if(!query){briefHint('说说这次想为顾客做什么。','scene');return}
  if(state.busy)return;
  if(query!==intakeFlow.query){intakeFlow.answers={};intakeFlow.query=query;}
  const token=++intakeFlow.request;
  intakeFlow.controller?.abort();intakeFlow.controller=new AbortController();
  state.busy=true;
  $('#intake-query').textContent=query;
  $('#intake-title').textContent='我在理解这次需要。';
  $('#intake-description').textContent='';
  $('#intake-error').hidden=true;
  setIntakePhase('understanding');
  try{
    const answers={...state.intakeExplicit,...intakeFlow.answers};
    if(state.intakeQuery===query&&state.intakeResolved)Object.assign(answers,state.intakeResolved,state.intakeExplicit,intakeFlow.answers);
    let resolved;
    try{resolved=await api('/api/intake',{method:'POST',body:JSON.stringify({query,mode:state.mode,answers}),signal:intakeFlow.controller.signal});}
    catch(error){if(![404,405].includes(error.status))throw error;resolved=await resolveCatalogBrief(query,answers);}
    if(token!==intakeFlow.request)return;
    if(resolved.status==='ready'&&resolved.task?.product&&resolved.task?.audience){
      applyUnderstoodTask(resolved.task);
      state.busy=false;setIntakePhase('');
      intakeFlow.allowSubmit=true;
      if(typeof saveCurrentWorkspace==='function')saveCurrentWorkspace();
      $('#brief-form').requestSubmit();
    }else if(resolved.questions?.length){
      showIntakeQuestions(resolved.questions);
    }else{
      throw new Error('请补充更具体的顾客需求后再试。');
    }
  }catch(error){
    if(token!==intakeFlow.request||error.name==='AbortError')return;
    setIntakePhase('error');$('#intake-title').textContent='这次没有理解完成。';
    $('#intake-error').textContent=error.message;
    const retry=document.createElement('button');retry.type='button';retry.className='text-button';retry.textContent='重新尝试';retry.addEventListener('click',understandTask);$('#intake-error').append(retry);
  }finally{
    if(token===intakeFlow.request&&!document.body.classList.contains('is-running'))state.busy=false;
  }
}
$('#brief-form').addEventListener('submit',event=>{
  if(intakeFlow.allowSubmit){intakeFlow.allowSubmit=false;return}
  event.preventDefault();event.stopImmediatePropagation();understandTask();
},true);
$('#clarification-form').addEventListener('submit',event=>{
  event.preventDefault();
  for(const [index,question]of intakeFlow.questions.entries()){
    const field=question.field||'scene',options=intakeChoices(question);
    const input=options.length?$(`#intake-questions input[name="intake-${index}"]:checked`):$(`[data-intake-answer="${field}"]`);
    if(!input?.value.trim()){
      const first=options.length?$(`#intake-questions input[name="intake-${index}"]`):input;first?.focus();
      $('#intake-description').textContent='请先回答上面的问题。';return;
    }
    intakeFlow.answers[field]=input.value.trim();
  }
  state.busy=false;understandTask();
});
$('#intake-edit').addEventListener('click',()=>{
  ++intakeFlow.request;intakeFlow.controller?.abort();state.busy=false;setIntakePhase('');$('#scene').focus();
});
for(const field of ['product','audience','stage','goal','requirements'])$('#'+field).addEventListener('input',()=>{
  if($('#'+field).value.trim())state.intakeExplicit[field]=$('#'+field).value.trim();else delete state.intakeExplicit[field];
});
$$('[data-platform]').forEach(button=>button.addEventListener('click',()=>{state.intakeExplicit.platform=button.dataset.platform}));
$$('[data-sample]').forEach(button=>button.addEventListener('click',()=>{
  state.intakeExplicit={product:$('#product').value,audience:$('#audience').value,platform:state.platform};
  if(typeof saveCurrentWorkspace==='function')saveCurrentWorkspace();
}));
$$('[data-new-task]').forEach(button=>button.addEventListener('click',()=>{
  ++intakeFlow.request;intakeFlow.controller?.abort();state.busy=false;intakeFlow.answers={};state.intakeExplicit={};state.intakeResolved=null;state.intakeQuery='';setIntakePhase('');
}));

async function resolveCatalogBrief(query,answers){
  const catalog=await api('/api/catalog?mode='+encodeURIComponent(state.mode));
  const rows=Array.isArray(catalog.items)?catalog.items:[];
  const norm=value=>String(value||'').toLowerCase().replace(/huawei|华为|[\s（）()·_—-]/g,'');
  const families=[...norm(query).matchAll(/matebook(?:fold|d14|d16|xpro|gt14|14s|16s|pro|14|16|e)/g)].map(x=>x[0]);
  let matches=rows.filter(row=>answers.product?[row.id,row.name].includes(answers.product):families.length?families.some(f=>norm(row.name).includes(f)):norm(row.name)&&norm(query).includes(norm(row.name)));
  if(!answers.product&&families.length<=1&&/鸿蒙|harmony/i.test(query))matches=matches.filter(row=>/鸿蒙|harmony/i.test(row.name+' '+row.system));
  if(!answers.product&&families.length<=1&&/windows/i.test(query))matches=matches.filter(row=>/windows/i.test(row.name+' '+row.system));
  const audience=answers.audience||query.match(/(?:给|面向|针对|为)([^，。！？,\n]{2,35}?)(?:写|做|推荐|生成|设计|制作)/)?.[1]||query.match(/(?:我是|客户是|顾客是)([^，。！？,\n]{2,35})/)?.[1]||'';
  const questions=[];
  if(matches.length!==1)questions.push({field:'product',label:'这次为哪一款电脑准备？',options:(matches.length?matches:rows).map(row=>({value:row.name,label:row.name}))});
  if(!audience)questions.push({field:'audience',label:'这次想和什么样的顾客沟通？',options:[]});
  return {status:questions.length?'clarify':'ready',questions,task:{product:matches[0]?.name||'',audience,scene:query,platform:answers.platform||(/朋友圈/.test(query)?'moments':/公众号/.test(query)?'wechat':/海报/.test(query)?'poster':'xiaohongshu'),stage:answers.stage||'consideration',goal:answers.goal||'consult',requirements:answers.requirements||query}};
}
