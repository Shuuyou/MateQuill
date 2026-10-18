'use strict';
const workspaceStorageKey='scene-workspace-v1';
function saveCurrentWorkspace(){
  if(typeof sessionStorage==='undefined')return;
  try{
    const history=state.history.slice(0,10).map(entry=>({result:entry.result,brief:entry.brief,time:entry.time.toISOString()}));
    const record={intakeExplicit:state.intakeExplicit||{},intakeQuery:state.intakeQuery||'',intakeResolved:state.intakeResolved||null,draft:task(),history,runId:state.result?.run_id||'',expanded:document.body.classList.contains('sidebar-expanded')};
    sessionStorage.setItem(workspaceStorageKey,JSON.stringify(record));
  }catch{}
}
function restoreCurrentWorkspace(){
  if(typeof sessionStorage==='undefined')return;
  try{
    const raw=sessionStorage.getItem(workspaceStorageKey);
    if(!raw||raw.length>4000000)return;
    const saved=JSON.parse(raw);
    if(!saved||typeof saved!=='object')return;
    state.intakeExplicit=saved.intakeExplicit&&typeof saved.intakeExplicit==='object'?saved.intakeExplicit:{};
    state.intakeQuery=typeof saved.intakeQuery==='string'?saved.intakeQuery:'';
    state.intakeResolved=saved.intakeResolved||null;
    if(saved.draft&&typeof saved.draft==='object'){
      const brief=saved.draft;
      for(const id of ['product','audience','scene','stage','goal','requirements'])if(typeof brief[id]==='string')$('#'+id).value=brief[id];
      if(Object.hasOwn(labels,brief.platform))setPlatform(brief.platform);
      if(['local','live','demo'].includes(brief.mode)){state.preferredMode=brief.mode;state.mode=brief.mode;}
    }
    if(Array.isArray(saved.history))state.history=saved.history.slice(0,10).filter(entry=>entry?.result?.run_id&&entry?.brief&&typeof entry.brief==='object').map(entry=>({...entry,time:new Date(entry.time)})).filter(entry=>!Number.isNaN(entry.time.getTime()));
    if(saved.expanded){document.body.classList.add('sidebar-expanded');$('#sidebar-toggle')?.setAttribute('aria-expanded','true');$('#sidebar-toggle')?.setAttribute('aria-label','收起导航');}
    renderRecentTasks();
    const active=state.history.find(entry=>entry.result.run_id===saved.runId);
    if(active)render(active.result,active.brief);
  }catch{}
}
['product','audience','scene','stage','goal','requirements'].forEach(id=>{
  $('#'+id)?.addEventListener('input',saveCurrentWorkspace);
  $('#'+id)?.addEventListener('change',saveCurrentWorkspace);
});
$('#source-mode')?.addEventListener('change',saveCurrentWorkspace);
$$('[data-platform],[data-new-task],[data-sample]').forEach(button=>button.addEventListener('click',saveCurrentWorkspace));
$('#sidebar-toggle')?.addEventListener('click',saveCurrentWorkspace);
restoreCurrentWorkspace();
