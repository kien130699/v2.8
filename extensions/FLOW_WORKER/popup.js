const $=id=>document.getElementById(id);
const SERVER_DEFAULTS={imageModel:'Nano Banana 2',videoModel:'Veo 3.1 - Fast',imageConcurrency:9,videoConcurrency:4,videoDuration:'8s'};
const fields={
  pairs:$("pairs"),serverEnabled:$("serverEnabled"),serverUrl:$("serverUrl"),
  submitPolicy:$("submitPolicy"),autoDownloadVideo:$("autoDownloadVideo"),
  maxSubmitsPerMinute:$("maxSubmitsPerMinute"),submitGapMs:$("submitGapMs"),
  aspectRatio:$("aspectRatio"),imageOutputs:$("imageOutputs"),videoOutputs:$("videoOutputs"),
  imageTimeoutSec:$("imageTimeoutSec"),videoTimeoutSec:$("videoTimeoutSec")
};
const run=$("run"),serverBadge=$("serverBadge"),status=$("status"),progressFill=$("progressFill"),progressLabel=$("progressLabel"),progressPercent=$("progressPercent"),progressDetail=$("progressDetail"),queueStats=$("queueStats");
const defaultRuntime=()=>({running:false,logs:[],lastLevel:'info',progressPercent:0,progressLabel:'Chưa chạy',progressDetail:'1 tab · queue song song.',updatedAt:null,jobs:{},metrics:{imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,imageLimit:9,videoLimit:4,done:0,errors:0,total:0}});
function renderRuntime(input){
  const r={...defaultRuntime(),...(input||{})},logs=Array.isArray(r.logs)?r.logs:[];
  status.textContent=logs.length?logs.map(x=>`[${x.time}] ${x.text}`).join("\n"):'Sẵn sàng.';status.dataset.level=r.lastLevel||'info';status.scrollTop=status.scrollHeight;
  const pct=Math.max(0,Math.min(100,Number(r.progressPercent||0)));progressFill.style.width=`${pct}%`;progressPercent.textContent=`${Math.round(pct)}%`;progressLabel.textContent=r.progressLabel||'Chưa chạy';progressDetail.textContent=r.progressDetail||'';
  const m={imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,imageLimit:9,videoLimit:4,done:0,errors:0,total:0,...(r.metrics||{})};
  queueStats.textContent=`IMAGE ${m.imageInFlight}/${m.imageLimit} · VIDEO ${m.videoInFlight}/${m.videoLimit} · SUBMIT I:${m.submitImageQueued} V:${m.submitVideoQueued} · DONE ${m.done}/${m.total} · ERROR ${m.errors}`;
  run.disabled=Boolean(r.running);run.textContent=r.running?'ĐANG CHẠY 1 TAB...':'CHẠY 1 TAB / N JOB';
}
async function saveForm(){const o={};for(const [k,v] of Object.entries(fields))o[k]=v.type==='checkbox'?v.checked:v.value;await chrome.storage.local.set({flowPairAutoForm:o});if(kickServerFields.has(document.activeElement?.id))chrome.runtime.sendMessage({type:'FLOW_SERVER_RECONNECT'}).catch(()=>{});}
async function loadForm(){const {flowPairAutoForm:o}=await chrome.storage.local.get('flowPairAutoForm');if(!o)return;for(const [k,v] of Object.entries(o))if(fields[k]&&v!=null){if(fields[k].type==='checkbox')fields[k].checked=Boolean(v);else fields[k].value=v;}}
async function loadRuntime(){const {flowPairAutoRuntime}=await chrome.storage.local.get('flowPairAutoRuntime');renderRuntime(flowPairAutoRuntime);}
chrome.storage.onChanged.addListener((c,a)=>{if(a==='local'&&c.flowPairAutoRuntime)renderRuntime(c.flowPairAutoRuntime.newValue);});
chrome.storage.onChanged.addListener((c,a)=>{if(a==='local'&&c.flowPairAutoForm)loadForm().catch(()=>{});});
for(const el of Object.values(fields)){el.addEventListener('change',saveForm);if(el.tagName==='TEXTAREA'||el.tagName==='INPUT')el.addEventListener('input',saveForm);}
chrome.runtime.onMessage.addListener(m=>{if(m?.type==='FLOW_STATUS_SNAPSHOT'&&m.runtime)renderRuntime(m.runtime);});


const kickServerFields=new Set(['serverEnabled','serverUrl']);
function renderServerStatus(s){
  if(!serverBadge)return;const connected=Boolean(s?.connected);serverBadge.textContent=connected?'● Connected':'○ Disconnected';serverBadge.style.color=connected?'#137333':'#b3261e';serverBadge.title=s?.lastError||s?.url||'';
}
async function loadServerStatus(){const {flowPairAutoServerStatus}=await chrome.storage.local.get('flowPairAutoServerStatus');renderServerStatus(flowPairAutoServerStatus);}
chrome.storage.onChanged.addListener((c,a)=>{if(a==='local'&&c.flowPairAutoServerStatus)renderServerStatus(c.flowPairAutoServerStatus.newValue);});
chrome.runtime.onMessage.addListener(m=>{if(m?.type==='FLOW_SERVER_STATUS')renderServerStatus(m.status);});

const tabButtons=[...document.querySelectorAll('[data-tab-target]')];
const tabPanes=[...document.querySelectorAll('.tabPane')];
async function activateTab(id,persist=true){
  tabButtons.forEach(b=>b.classList.toggle('active',b.dataset.tabTarget===id));
  tabPanes.forEach(p=>p.classList.toggle('active',p.id===id));
  if(persist) await chrome.storage.local.set({flowPairAutoUiTab:id});
}
tabButtons.forEach(b=>b.addEventListener('click',()=>activateTab(b.dataset.tabTarget)));
run.addEventListener('click',async()=>{try{
  await saveForm();const [tab]=await chrome.tabs.query({active:true,currentWindow:true});if(!tab?.id)throw new Error('Không xác định được tab hiện tại.');
  renderRuntime({...defaultRuntime(),running:true,logs:[{time:new Date().toLocaleTimeString(),text:'Đang gửi queue xuống background...',level:'info'}],progressLabel:'Đang khởi động'});
  const response=await chrome.runtime.sendMessage({type:'FLOW_RUN_PAIRS',tabId:tab.id,pairs:fields.pairs.value,options:{
    imageModel:SERVER_DEFAULTS.imageModel,videoModel:SERVER_DEFAULTS.videoModel,
    imageConcurrency:SERVER_DEFAULTS.imageConcurrency,videoConcurrency:SERVER_DEFAULTS.videoConcurrency,
    submitPolicy:fields.submitPolicy.value,autoDownloadVideo:fields.autoDownloadVideo.checked,
    maxSubmitsPerMinute:Number(fields.maxSubmitsPerMinute.value),submitGapMs:Number(fields.submitGapMs.value),
    aspectRatio:fields.aspectRatio.value,imageOutputs:fields.imageOutputs.value,videoDuration:SERVER_DEFAULTS.videoDuration,videoOutputs:fields.videoOutputs.value,
    imageTimeoutSec:Number(fields.imageTimeoutSec.value||300),videoTimeoutSec:Number(fields.videoTimeoutSec.value||900)
  }});if(!response?.ok)throw new Error(response?.error||'Automation thất bại.');
}catch(error){const {flowPairAutoRuntime}=await chrome.storage.local.get('flowPairAutoRuntime');if(flowPairAutoRuntime){renderRuntime(flowPairAutoRuntime);return;}renderRuntime({...defaultRuntime(),lastLevel:'error',logs:[{time:new Date().toLocaleTimeString(),text:`❌ ${error?.message||error}`,level:'error'}]});}});

(async()=>{
  const vEl = $('extVersionBadge');
  if (vEl && chrome.runtime?.getManifest) vEl.textContent = 'v' + chrome.runtime.getManifest().version;
  await loadForm();
  await loadServerStatus();
  const {flowPairAutoUiTab}=await chrome.storage.local.get('flowPairAutoUiTab');
  if(flowPairAutoUiTab&&document.getElementById(flowPairAutoUiTab))await activateTab(flowPairAutoUiTab,false);
  await loadRuntime();
})();
