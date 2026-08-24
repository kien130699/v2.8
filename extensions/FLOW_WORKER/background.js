const pendingAutomationSleeps = new Set();
const sleep = ms => new Promise((resolve,reject)=>{
  const item={timer:null,reject};
  item.timer=setTimeout(()=>{pendingAutomationSleeps.delete(item);resolve();},Math.max(0,Number(ms||0)));
  pendingAutomationSleeps.add(item);
});
function abortPendingAutomationSleeps(reason='server_offline'){
  const error=new Error(`SERVER FAIL-SAFE STOP · ${reason}`);
  for(const item of [...pendingAutomationSleeps]){
    clearTimeout(item.timer);
    pendingAutomationSleeps.delete(item);
    try{item.reject(error);}catch{}
  }
}
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const COMPATIBLE_PAGE_FAMILY = EXTENSION_VERSION.split('.').slice(0,2).join('.');
function isCompatiblePageVersion(version){
  const v=String(version||'').trim();
  return v === EXTENSION_VERSION || v.startsWith(COMPATIBLE_PAGE_FAMILY + '.');
}


// ======================= Local Orchestrator Bridge (v14) =======================
const DEFAULT_SERVER_URL = 'ws://127.0.0.1:3000/ws/flow';
let serverSocket = null;
let serverReconnectTimer = null;
let serverHeartbeatTimer = null;
let serverRunPromise = null;
const serverJobQueue = [];
const serverTemporaryTabs = new Set();
const serverAcceptedJobIds = new Set();
let activeServerBatch = null;
let serverAutomationAllowed = false;
let serverFailSafeReason = 'server_not_connected';
let serverFailSafePromise = null;
let serverFailSafeLatched = false;
let serverReconnectBackoffMs = 5000;
const VIDEO_SCENE_RETRY_MAX=3;
const IMAGE_SCENE_RETRY_MAX=3;
const VIDEO_STATUS_SILENCE_TIMEOUT_MS=75000;
const VIDEO_EXISTING_STATUS_RECHECK_MS=45000;
const REFERENCE_CARD_STABLE_MS=1200;
const REFERENCE_CARD_WAIT_MS=6500;
const REFERENCE_CARD_RETRY_MAX=2;
const DEBUGGER_OPERATION_RETRY_MAX=3;
const RECOVERY_VIDEO_DOWNLOAD_TIMEOUT_MS=35000;
const activeExtensionDownloadIds = new Set();
let mediaRecoveryTail=Promise.resolve();
let mediaRecoveryPending=0;
let mediaRecoveryEpoch=0;
const PENDING_SERVER_REPORTS_KEY='flowPendingServerReportsV2';

function queueMediaRecovery(label,fn){
  const epoch=mediaRecoveryEpoch; mediaRecoveryPending++;
  const run=async()=>{if(epoch!==mediaRecoveryEpoch) throw new Error(`Recovery cancelled · ${label}`);assertServerAutomationAllowed(label);return await fn();};
  const promise=mediaRecoveryTail.catch(()=>{}).then(run);
  mediaRecoveryTail=promise.finally(()=>{mediaRecoveryPending=Math.max(0,mediaRecoveryPending-1);});
  return promise;
}
async function waitMediaRecoveryIdle(){if(mediaRecoveryPending>0) await mediaRecoveryTail.catch(()=>{});}
async function sendOrStoreServerReport(payload){
  const reportId=String(payload?.reportId||`${payload?.type||'REPORT'}:${payload?.jobId||''}:${payload?.sceneId||''}:${payload?.mediaId||''}:${Date.now()}`);
  const row={...payload,reportId};
  if(sendServerMessage(row)) return true;
  try{
    const data=await chrome.storage.local.get(PENDING_SERVER_REPORTS_KEY);const rows=Array.isArray(data?.[PENDING_SERVER_REPORTS_KEY])?data[PENDING_SERVER_REPORTS_KEY]:[];
    if(!rows.some(x=>String(x?.reportId||'')===reportId)) rows.push({...row,storedAt:Date.now()});
    await chrome.storage.local.set({[PENDING_SERVER_REPORTS_KEY]:rows.slice(-120)});
  }catch{}
  return false;
}
async function pendingServerReportCount(){
  try{const data=await chrome.storage.local.get(PENDING_SERVER_REPORTS_KEY);return Array.isArray(data?.[PENDING_SERVER_REPORTS_KEY])?data[PENDING_SERVER_REPORTS_KEY].length:0;}catch{return 0;}
}
async function flushPendingServerReports(){
  if(serverSocket?.readyState!==WebSocket.OPEN) return 0;
  try{const data=await chrome.storage.local.get(PENDING_SERVER_REPORTS_KEY);const rows=Array.isArray(data?.[PENDING_SERVER_REPORTS_KEY])?data[PENDING_SERVER_REPORTS_KEY]:[];if(!rows.length)return 0;const remain=[];let sent=0;for(const row of rows){const payload={...row,replayed:true};delete payload.storedAt;if(sendServerMessage(payload))sent++;else remain.push(row);}await chrome.storage.local.set({[PENDING_SERVER_REPORTS_KEY]:remain.slice(-120)});return sent;}catch{return 0;}
}
let flowUiCircuitUntil = 0;
let flowUiCircuitReason = '';
// True global UI protocol break only. Do not freeze production for 10 minutes.
const FLOW_UI_CIRCUIT_MS = 90 * 1000;

function isReferenceMediaTransientError(error){
  const text=String(error?.message||error||'').toLowerCase();
  return (
    text.includes('exact-media-not-visible') ||
    text.includes('filename-has-different-mediaid') ||
    text.includes('image-mediaid-mismatch') ||
    text.includes('mediaid-without-image') ||
    text.includes('reference_attach_failed') ||
    text.includes('reference_card_not_stable') ||
    text.includes('reference_pack_not_ready') ||
    text.includes('reference_pack_error') ||
    text.includes('video_reference_pack_error') ||
    text.includes('flow_ui_asset_not_found') ||
    text.includes('upload lại persona_') ||
    text.includes('upload lại') ||
    text.includes('ref re-upload')
  );
}

function isFatalFlowUiError(error){
  const text=String(error?.message||error||'').toLowerCase();

  // Media indexing/upload/reference errors belong to ONE scene/job.
  // Server SAME-JOB retry handles them; never freeze every later job.
  if(isReferenceMediaTransientError(error)) return false;

  if(text.includes('page bridge version không tương thích')) return true;
  if(text.includes('flow_ui_circuit_breaker')) return true;

  // Retry exhaustion is global-fatal only for truly global controls/protocol.
  if(text.includes('ui_retry_exhausted')){
    return (
      text.includes('settings verify') ||
      text.includes('không mở được settings') ||
      text.includes('page bridge') ||
      text.includes('debugger permission')
    );
  }
  return false;
}
function flowUiCircuitOpen(){return Date.now()<flowUiCircuitUntil;}
function tripFlowUiCircuit(error){
  flowUiCircuitUntil=Date.now()+FLOW_UI_CIRCUIT_MS;
  flowUiCircuitReason=String(error?.message||error||'Flow UI failure');
  return flowUiCircuitUntil;
}

function isSafePrepareRetryError(error){
  const text=String(error?.message||error||'').toLowerCase();
  if(text.includes('server fail-safe')||text.includes('billing')||text.includes('insufficient')) return false;
  return true;
}
async function resetPrepareUiForRetry(tabId,label){
  try{await callPage(tabId,'closeAssetPicker',[]);}catch{}
  try{await callPage(tabId,'clearPrompt',[]);}catch{}
  await sleep(250);
  try{await ensureFlowToolLoaded(tabId);}catch{}
  await appendLog(`↻ ${label} · reset UI nhẹ trước retry`,'info');
}
async function withPrepareRetry(tabId,label,fn,maxAttempts=3){
  let last=null;
  for(let attempt=1;attempt<=maxAttempts;attempt++){
    assertServerAutomationAllowed(label);
    try{return await fn(attempt);}
    catch(error){
      last=error;
      if(!isSafePrepareRetryError(error)||attempt>=maxAttempts) break;
      await appendLog(`↻ ${label} retry ${attempt+1}/${maxAttempts} · ${error?.message||error}`,'info');
      await resetPrepareUiForRetry(tabId,label);
      await sleep(Math.min(2500,400*(2**(attempt-1))));
    }
  }
  const err=new Error(`UI_RETRY_EXHAUSTED · ${label} · ${last?.message||last||'unknown'}`); err.cause=last; throw err;
}

function assertServerAutomationAllowed(action='browser automation'){
  if(serverAutomationAllowed) return true;
  throw new Error(`SERVER FAIL-SAFE STOP · ${serverFailSafeReason||'server_offline'} · block ${action}`);
}

async function cancelExtensionDownloads(){
  for(const id of [...activeExtensionDownloadIds]){
    try{await chrome.downloads.cancel(id);}catch{}
    activeExtensionDownloadIds.delete(id);
  }
}

async function rejectAutomationWaiters(reason){
  const error=new Error(`SERVER FAIL-SAFE STOP · ${reason}`);
  try{
    for(const [tabId,waiter] of [...fileChooserWaiters.entries()]){
      clearTimeout(waiter.timer);
      try{waiter.reject(error);}catch{}
      fileChooserWaiters.delete(tabId);
    }
  }catch{}
  try{
    for(const [tabId,state] of [...netState.entries()]){
      for(const waiter of [...(state.waiters||[])]){
        clearTimeout(waiter.timer);
        try{waiter.reject(error);}catch{}
      }
      netState.delete(tabId);
    }
  }catch{}
}

async function detachAllOwnedDebuggers(){
  try{
    for(const tabId of [...debuggerOwnedTabs]){
      try{await chrome.debugger.detach({tabId});}catch{}
      debuggerOwnedTabs.delete(tabId);
    }
  }catch{}
}

async function abortPageWorldAutomation(reason='server_offline'){
  // This is a control-plane abort only: no click, navigation, upload or DOM UI mutation.
  // It flips the injected page bridge abort token so an already-running MAIN-world
  // async function cannot continue after the background worker has stopped.
  let tabs=[];
  try{tabs=await chrome.tabs.query({url:['https://labs.google/*','https://labs.google/fx/*']});}catch{}
  for(const tab of tabs){
    if(!Number.isInteger(tab?.id)) continue;
    try{
      await chrome.scripting.executeScript({
        target:{tabId:tab.id},world:'MAIN',
        func:reason=>{
          try{
            if(window.FlowPairAuto?.abortAll) return window.FlowPairAuto.abortAll(reason);
            window.__FLOW_PAIR_AUTO_ABORT_FALLBACK__={aborted:true,reason:String(reason||'server_offline'),ts:Date.now()};
          }catch{}
          return {ok:true};
        },
        args:[String(reason||'server_offline')]
      });
    }catch{}
  }
}

async function resumePageWorldAutomation(tabId){
  if(!Number.isInteger(tabId)) return;
  try{
    await chrome.scripting.executeScript({
      target:{tabId},world:'MAIN',
      func:()=>window.FlowPairAuto?.resumeAll?.()||{ok:true}
    });
  }catch{}
}

async function failSafeStopAll(reason='server_offline',{closeSocket=false,sendAck=false}={}){
  if(serverFailSafePromise) return serverFailSafePromise;
  if(serverFailSafeLatched && !activeServerBatch && !serverRunPromise && serverJobQueue.length===0){
    serverAutomationAllowed=false;serverFailSafeReason=String(reason||serverFailSafeReason||'server_offline');
    return true;
  }
  serverFailSafeLatched=true;
  serverFailSafePromise=(async()=>{
    const text=String(reason||'server_offline');
    serverAutomationAllowed=false;
    serverFailSafeReason=text;
    mediaRecoveryEpoch++;

    // HARD ABORT page-world first. Any page.js wait/select/click coroutine rejects here.
    await abortPageWorldAutomation(text);

    serverJobQueue.splice(0,serverJobQueue.length);
    serverAcceptedJobIds.clear();
    try{
      if(activeServerBatch){
        activeServerBatch.cancelled=true;
        activeServerBatch.cancelReason=text;
        activeServerBatch.wake?.notify?.();
      }
    }catch{}

    abortPendingAutomationSleeps(text);
    await rejectAutomationWaiters(text);
    await cancelExtensionDownloads();
    await detachAllOwnedDebuggers();

    try{
      await runtimeReady;
      runtimeCache={
        ...defaultRuntime(),
        running:false,
        progressPercent:0,
        progressLabel:'SERVER OFFLINE · ĐÃ DỪNG',
        progressDetail:`Extension fail-safe: ${text}. Queue đã clear; không thao tác trình duyệt cho tới khi server kết nối lại.`,
        lastLevel:'error',
        updatedAt:Date.now(),
        activeRunId:null,
        serverJobId:null,
        logs:[...(runtimeCache?.logs||[]),{
          time:new Date().toLocaleTimeString(),
          text:`⛔ SERVER OFFLINE → STOP ALL · ${text} · clear queue · detach debugger · page bridge ABORTED · no further browser action`,
          level:'error'
        }].slice(-200),
        metrics:{imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,done:0,errors:0,total:0}
      };
      await chrome.storage.local.set({flowPairAutoRuntime:runtimeCache});
      try{await chrome.runtime.sendMessage({type:'FLOW_STATUS_SNAPSHOT',runtime:runtimeCache});}catch{}
    }catch{}

    if(sendAck){
      sendServerMessage({type:'STOP_ALL_ACK',reason:text,policy:'SERVER_OFF_FAILSAFE'});
      await new Promise(resolve=>setTimeout(resolve,80));
    }
    if(closeSocket){
      const ws=serverSocket;
      serverSocket=null;
      try{ws?.close(1000,'server_off_fail_safe');}catch{}
    }
    await setServerStatus({connected:false,lastError:`FAIL-SAFE STOP · ${text}`}).catch(()=>{});
    return true;
  })().finally(()=>{serverFailSafePromise=null;});
  return serverFailSafePromise;
}
const GLOBAL_ASSET_CACHE = new Map();
const PERSISTENT_ASSET_CACHE_KEY='flowAssetMediaCacheV2';
let persistentAssetCacheObject={};
const persistentAssetCacheReady=(async()=>{
  try{
    const data=await chrome.storage.local.get(PERSISTENT_ASSET_CACHE_KEY);
    const raw=data?.[PERSISTENT_ASSET_CACHE_KEY]||{};
    if(raw&&typeof raw==='object'){
      persistentAssetCacheObject=raw;
      for(const [path,row] of Object.entries(raw)){
        if(path&&row?.mediaId) GLOBAL_ASSET_CACHE.set(path,row);
      }
    }
  }catch{}
})();
async function rememberAssetMedia(path,row){
  const key=String(path||'').trim();
  if(!key||!row?.mediaId) return;
  await persistentAssetCacheReady;
  const saved={mediaId:String(row.mediaId),title:String(row.title||''),name:String(row.name||''),role:String(row.role||'reference'),path:key,updatedAt:Date.now()};
  GLOBAL_ASSET_CACHE.set(key,saved);
  persistentAssetCacheObject[key]=saved;
  const entries=Object.entries(persistentAssetCacheObject);
  if(entries.length>200){
    entries.sort((a,b)=>Number(b[1]?.updatedAt||0)-Number(a[1]?.updatedAt||0));
    persistentAssetCacheObject=Object.fromEntries(entries.slice(0,160));
  }
  await chrome.storage.local.set({[PERSISTENT_ASSET_CACHE_KEY]:persistentAssetCacheObject}).catch(()=>{});
}
const TRACKED_SCENE_MEDIA_KEY='flowTrackedSceneMediaV1';
let trackedSceneMediaObject={};
const trackedSceneMediaReady=(async()=>{
  try{
    const data=await chrome.storage.local.get(TRACKED_SCENE_MEDIA_KEY);
    const raw=data?.[TRACKED_SCENE_MEDIA_KEY]||{};
    if(raw&&typeof raw==='object') trackedSceneMediaObject=raw;
  }catch{}
})();

async function rememberTrackedMedia(info){
  if(!info?.mediaId) return;
  await trackedSceneMediaReady;
  const mid=String(info.mediaId).trim();
  const entry={...trackedSceneMediaObject[mid],...info,mediaId:mid,updatedAt:Date.now()};
  trackedSceneMediaObject[mid]=entry;
  const entries=Object.entries(trackedSceneMediaObject);
  if(entries.length>300){
    entries.sort((a,b)=>Number(b[1]?.updatedAt||0)-Number(a[1]?.updatedAt||0));
    trackedSceneMediaObject=Object.fromEntries(entries.slice(0,200));
  }
  await chrome.storage.local.set({[TRACKED_SCENE_MEDIA_KEY]:trackedSceneMediaObject}).catch(()=>{});
  if(info.jobId){
    sendServerMessage({
      type:'MEDIA_ID_TRACKED',
      jobId:String(info.jobId),
      sceneId:Number(info.sceneId||1),
      sceneIndex:Number(info.sceneIndex||0),
      mediaId:mid,
      title:String(info.title||''),
      status:info.status||'PENDING'
    });
  }
}

async function updateTrackedMedia(mediaId,patch={}){
  const mid=String(mediaId||'').trim();
  if(!mid) return;
  await trackedSceneMediaReady;
  if(trackedSceneMediaObject[mid]){
    trackedSceneMediaObject[mid]={...trackedSceneMediaObject[mid],...patch,updatedAt:Date.now()};
    await chrome.storage.local.set({[TRACKED_SCENE_MEDIA_KEY]:trackedSceneMediaObject}).catch(()=>{});
  }
}


async function getServerBridgeConfig(){
  try{
    const {flowPairAutoForm={}}=await chrome.storage.local.get('flowPairAutoForm');
    return {
      enabled: flowPairAutoForm.serverEnabled !== false,
      url: String(flowPairAutoForm.serverUrl || DEFAULT_SERVER_URL).trim() || DEFAULT_SERVER_URL
    };
  }catch{return {enabled:true,url:DEFAULT_SERVER_URL};}
}

async function setServerStatus(patch={}){
  const {flowPairAutoServerStatus:old={}}=await chrome.storage.local.get('flowPairAutoServerStatus').catch(()=>({}));
  const next={connected:false,url:DEFAULT_SERVER_URL,lastError:null,updatedAt:Date.now(),...old,...patch,updatedAt:Date.now()};
  await chrome.storage.local.set({flowPairAutoServerStatus:next}).catch(()=>{});
  try{await chrome.runtime.sendMessage({type:'FLOW_SERVER_STATUS',status:next});}catch{}
}

function sendServerMessage(payload){
  try{
    if(serverSocket?.readyState!==WebSocket.OPEN) return false;
    serverSocket.send(JSON.stringify({...payload,ts:Date.now()}));
    return true;
  }catch{return false;}
}

function sendSceneCheckpoint(record,options,patch={}){
  const serverJobId=String(record?.serverJobId||options?.serverJobId||'').trim();
  if(!serverJobId) return;
  const payload={
    type:'SCENE_CHECKPOINT',
    jobId:serverJobId,
    sceneIndex:Number(record.serverSceneIndex??record.index??0),
    sceneId:Number(record.sceneId??record.index+1),
    imageMediaId:record.imageMediaId||record.imageIds?.[0]||null,
    videoMediaId:record.videoIds?.[0]||null,
    status:record.videoState||record.imageState||'RUNNING',
    progress:Number(patch.progress??0),
    error:record.error||patch.error||null,
    ...patch
  };
  sendServerMessage(payload);
}

async function disconnectServerBridge(reason='disabled'){
  clearTimeout(serverReconnectTimer); serverReconnectTimer=null;
  clearInterval(serverHeartbeatTimer); serverHeartbeatTimer=null;
  await failSafeStopAll(reason,{closeSocket:false,sendAck:false}).catch(()=>{});
  const ws=serverSocket; serverSocket=null;
  try{ws?.close(1000,reason);}catch{}
  await setServerStatus({connected:false,lastError:reason==='disabled'?null:`FAIL-SAFE STOP · ${reason}`});
}

function isAllowedShopeeUrl(raw){
  try{
    const u=new URL(String(raw||''));
    if(u.protocol!=='https:') return false;
    const h=u.hostname.toLowerCase();
    return h==='shopee.vn'||h.endsWith('.shopee.vn')||h==='shope.ee'||h.endsWith('.shope.ee');
  }catch{return false;}
}

async function waitTabLoaded(tabId,timeoutMs=25000){
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const tab=await chrome.tabs.get(tabId).catch(()=>null);
    if(tab?.status==='complete'&&String(tab.url||'').startsWith('http')) return tab;
    await sleep(350);
  }
  return chrome.tabs.get(tabId).catch(()=>null);
}

function shopeePageExtractor(){
  const text=(el)=>String(el?.textContent||el?.content||'').replace(/\u00a0/g,' ').replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
  const meta=(key,attr='property')=>{
    const el=document.querySelector(`meta[${attr}="${CSS.escape(key)}"]`);
    return String(el?.content||'').trim();
  };
  const cleanUrl=(v)=>{try{return new URL(v,location.href).href}catch{return ''}};
  const uniq=(arr)=>[...new Set(arr.map(x=>String(x||'').trim()).filter(Boolean))];
  const ld=[];
  for(const s of document.querySelectorAll('script[type="application/ld+json"]')){
    try{
      const obj=JSON.parse(s.textContent||'null');
      const push=(x)=>{if(x&&typeof x==='object'){if(Array.isArray(x))x.forEach(push);else{ld.push(x);if(Array.isArray(x['@graph']))x['@graph'].forEach(push);}}};
      push(obj);
    }catch{}
  }
  const productLd=ld.find(x=>String(x?.['@type']||'').toLowerCase()==='product')||{};
  const offer=Array.isArray(productLd.offers)?productLd.offers[0]:(productLd.offers||{});
  const genericTitle=(v)=>{
    const t=String(v||'').replace(/\s+/g,' ').trim().toLowerCase();
    if(!t) return true;
    if(t==='shopee'||t==='shopee việt nam'||t==='shopee vietnam'||t==='shopee.vn') return true;
    if(t.includes('shopee')&&(t.includes('hot deal')||t.includes('best price')||t.includes('mua sắm')||t.includes('vietnam')||t.includes('việt nam'))) return true;
    return t.includes('hot deals, best prices')||t.includes('hot deals best prices');
  };
  const titleCandidates=[];
  const pushTitle=(v)=>{const t=String(v||'').replace(/\s+/g,' ').trim();if(t&&!genericTitle(t)&&!titleCandidates.includes(t))titleCandidates.push(t)};
  pushTitle(productLd.name);
  pushTitle(meta('og:title'));
  pushTitle(meta('twitter:title','name'));
  for(const sel of ['h1','[data-sqe="name"]','[class*="product"] h1','[class*="product-name"]','[class*="productName"]']){
    const el=document.querySelector(sel); if(el) pushTitle(text(el));
  }
  for(const img of Array.from(document.images).slice(0,100)){
    const alt=String(img.alt||'').trim(); if(alt.length>=16&&alt.length<=240) pushTitle(alt);
  }
  const title=titleCandidates[0]||'';

  const bodyText=String(document.body?.innerText||'').replace(/\u00a0/g,' ').replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim().slice(0,42000);
  const sectionFromBody=(starts,stops=[])=>{
    const lower=bodyText.toLowerCase();
    let pos=-1;
    for(const k of starts){const i=lower.indexOf(String(k).toLowerCase());if(i>=0&&(pos<0||i<pos))pos=i;}
    if(pos<0)return '';
    let end=Math.min(bodyText.length,pos+12000);
    for(const k of stops){const i=lower.indexOf(String(k).toLowerCase(),pos+20);if(i>pos&&i<end)end=i;}
    return bodyText.slice(pos,end).trim();
  };
  const descriptionBlocks=[];
  const pushDesc=(v)=>{
    const t=String(v||'').replace(/\u00a0/g,' ').replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
    if(t.length>=40&&!descriptionBlocks.includes(t))descriptionBlocks.push(t.slice(0,12000));
  };
  pushDesc(productLd.description);
  pushDesc(meta('og:description'));
  pushDesc(meta('description','name'));
  const descSelectors=[
    '[data-sqe="product-description"]','[class*="product-description"]','[class*="productDescription"]',
    '[class*="product-detail"]','[class*="productDetail"]','[class*="item-description"]'
  ];
  for(const sel of descSelectors){
    for(const el of Array.from(document.querySelectorAll(sel)).slice(0,8)){
      const t=text(el); if(t.length>=60&&t.length<=12000) pushDesc(t);
    }
  }
  pushDesc(sectionFromBody(
    ['mô tả sản phẩm','mô tả chi tiết','thông tin sản phẩm','chi tiết sản phẩm'],
    ['đánh giá sản phẩm','đánh giá từ người mua','sản phẩm tương tự','có thể bạn cũng thích','bình luận']
  ));
  const detailText=descriptionBlocks.sort((a,b)=>b.length-a.length)[0]||'';
  const metaDescription=String(productLd.description||meta('og:description')||meta('description','name')||'').replace(/\s+/g,' ').trim();
  const description=(detailText||metaDescription).slice(0,12000);

  const specCandidates=[];
  const specSection=sectionFromBody(
    ['chi tiết sản phẩm','thông tin chi tiết','thông số sản phẩm','thuộc tính sản phẩm'],
    ['mô tả sản phẩm','đánh giá sản phẩm','sản phẩm tương tự','có thể bạn cũng thích']
  );
  if(specSection) specCandidates.push(...specSection.split(/\n+/).map(x=>x.trim()).filter(x=>x.length>=3&&x.length<=220));
  for(const sel of ['[class*="attribute"]','[class*="specification"]','[class*="product-detail"]']){
    for(const el of Array.from(document.querySelectorAll(sel)).slice(0,40)){
      const t=text(el); if(t.length>=3&&t.length<=260)specCandidates.push(t);
    }
  }
  const specs=uniq(specCandidates).filter(x=>!genericTitle(x)).slice(0,40);

  const imageCandidates=[];
  const ldImage=productLd.image;
  if(Array.isArray(ldImage)) imageCandidates.push(...ldImage); else if(ldImage) imageCandidates.push(ldImage);
  imageCandidates.push(meta('og:image'));
  for(const img of Array.from(document.images).slice(0,220)){
    const u=img.currentSrc||img.src||img.getAttribute('data-src')||'';
    if(u&&(/shopee|cf\.shopee|susercontent/i.test(u))) imageCandidates.push(u);
  }
  const priceText=String(offer?.price||offer?.lowPrice||'').trim();
  let price=priceText;
  if(!price){
    const m=bodyText.match(/(?:₫|đ)\s?([0-9][0-9.,]{2,})|([0-9][0-9.,]{2,})\s?(?:₫|đ)/i);
    if(m) price=(m[1]||m[2]||'').trim();
  }
  let shopName='';
  const shopSelectors=['[data-sqe="shop-name"]','a[href*="/shop/"]'];
  for(const sel of shopSelectors){const el=document.querySelector(sel);if(el&&text(el).length>1&&text(el).length<120){shopName=text(el);break;}}
  return {
    finalUrl: location.href,
    title,
    productTitle:title,
    titleCandidates:titleCandidates.slice(0,16),
    pageTitle:String(document.title||'').replace(/\s+/g,' ').trim(),
    description,
    descriptionBlocks:descriptionBlocks.slice(0,8),
    detailText:detailText.slice(0,12000),
    specs,
    price,
    currency:String(offer?.priceCurrency||'VND'),
    images:uniq(imageCandidates.map(cleanUrl)).slice(0,16),
    shopName,
    bodyText,
    capturedAt:new Date().toISOString(),
    source:'browser_dom_v14520'
  };
}

async function extractShopeeCaptureFromTab(tabId,timeoutMs=16000){
  const started=Date.now(); let best=null,lastError='';
  while(Date.now()-started<timeoutMs){
    const tab=await chrome.tabs.get(tabId).catch(()=>null);
    const currentUrl=String(tab?.url||'');
    if(currentUrl.startsWith('chrome-error://')) throw new Error('Chrome không tải được trang Shopee (chrome-error).');
    if(currentUrl.startsWith('http')){
      try{
        const result=await chrome.scripting.executeScript({target:{tabId},func:shopeePageExtractor});
        const product=result?.[0]?.result||null;
        if(product){
          best=product;
          const title=String(product.title||product.productTitle||'').trim();
          const body=String(product.bodyText||'');
          const images=Array.isArray(product.images)?product.images:[];
          // Shopee is an SPA. Wait until useful product content exists instead of
          // snapshotting the generic marketplace shell immediately after load.
          if((title&&body.length>350&&images.length) || (body.length>2200&&images.length)) return product;
        }
      }catch(e){ lastError=e?.message||String(e); }
    }
    await sleep(850);
  }
  if(best) return best;
  throw new Error(lastError||'Shopee đã mở nhưng chưa render được dữ liệu sản phẩm.');
}

async function inspectShopeeProductForServer(message){
  const requestId=String(message?.requestId||'').trim();
  const url=String(message?.url||'').trim();
  if(!requestId||!url) throw new Error('SHOPEE_INSPECT_PRODUCT thiếu requestId/url');
  if(!isAllowedShopeeUrl(url)) throw new Error('Chỉ cho phép link Shopee HTTPS (shopee.vn / shope.ee).');
  let tab=null; let oldActive=null;
  try{
    oldActive=(await chrome.tabs.query({active:true,currentWindow:true}).catch(()=>[]))?.[0]||null;
    tab=await chrome.tabs.create({url,active:false});
    if(!Number.isInteger(tab?.id)) throw new Error('Không mở được tab Shopee.');
    await waitTabLoaded(tab.id,20000);
    await sleep(900);
    let product=null;
    try{
      product=await extractShopeeCaptureFromTab(tab.id,15000);
    }catch(firstError){
      // Some Shopee builds defer rendering in a background tab. Activate only as
      // a recovery path, then restore the user's previous tab.
      await appendLog(`SHOPEE INSPECT RETRY ACTIVE TAB · ${firstError?.message||firstError}`,'warn');
      await chrome.tabs.update(tab.id,{active:true}).catch(()=>{});
      await sleep(1200);
      product=await extractShopeeCaptureFromTab(tab.id,9000);
    }
    if(!product) throw new Error('Không đọc được DOM sản phẩm Shopee.');
    const finalUrl=String(product.finalUrl||'');
    const lowBody=String(product.bodyText||'').toLowerCase();
    if(/\/buyer\/login|captcha|verify/i.test(finalUrl) || (lowBody.includes('đăng nhập') && String(product.title||'').trim()==='' && (product.images||[]).length===0)){
      throw new Error('Shopee đang yêu cầu đăng nhập/xác minh. Mở link bằng Chrome, hoàn tất xác minh rồi bấm ĐỌC SP lại.');
    }
    if(!sendServerMessage({type:'SHOPEE_PRODUCT_RESULT',requestId,ok:true,product})) throw new Error('Mất kết nối server trước khi gửi kết quả Shopee.');
    await appendLog(`SHOPEE INSPECT OK · ${String(product.title||product.pageTitle||'product').slice(0,70)} · images=${(product.images||[]).length}`,'success');
  }catch(error){
    const msg=error?.message||String(error);
    sendServerMessage({type:'SHOPEE_PRODUCT_RESULT',requestId,ok:false,error:msg});
    await appendLog(`SHOPEE INSPECT ERROR · ${msg}`,'error');
  }finally{
    if(Number.isInteger(oldActive?.id)) await chrome.tabs.update(oldActive.id,{active:true}).catch(()=>{});
    if(Number.isInteger(tab?.id)) await chrome.tabs.remove(tab.id).catch(()=>{});
  }
}

function shopeeSearchExtractor(keyword,limit){
  const clean=(v)=>String(v||'').replace(/\s+/g,' ').trim();
  const decodeSafe=(v)=>{try{return decodeURIComponent(String(v||''));}catch{return String(v||'')}};
  const parseProductIdentity=(href)=>{
    try{
      const u=new URL(href,location.origin);
      if(u.protocol!=='https:')return null;
      const h=u.hostname.toLowerCase();
      if(!(h==='shopee.vn'||h.endsWith('.shopee.vn')))return null;
      let m=u.pathname.match(/-i\.(\d+)\.(\d+)(?:\/|$)/i);
      if(m)return {shopId:m[1],itemId:m[2],url:u};
      m=u.pathname.match(/\/product\/(\d+)\/(\d+)(?:\/|$)/i);
      if(m)return {shopId:m[1],itemId:m[2],url:u};
      const shopId=u.searchParams.get('shopid')||u.searchParams.get('shopId');
      const itemId=u.searchParams.get('itemid')||u.searchParams.get('itemId');
      if(/^\d+$/.test(shopId||'')&&/^\d+$/.test(itemId||''))return {shopId,itemId,url:u};
      return null;
    }catch{return null;}
  };
  const canonical=(identity)=>{
    if(!identity)return '';
    const u=identity.url;
    u.hash='';
    // Keep canonical product path and discard volatile tracking/search params.
    u.search='';
    return u.href;
  };
  const titleFromUrl=(u)=>{
    try{
      let s=decodeSafe(u.pathname||'').replace(/^\/+|\/+$/g,'');
      s=s.replace(/-i\.\d+\.\d+.*$/i,'').replace(/\/product\/\d+\/\d+.*$/i,'');
      s=s.replace(/[-_]+/g,' ').replace(/\s+/g,' ').trim();
      return s;
    }catch{return '';}
  };
  const isNoiseTitle=(x)=>{
    const t=clean(x).toLowerCase();
    return !t || /^[₫đ\d.,%\s+\-]+$/i.test(t) || /^(đã bán|sold|giảm|voucher|yêu thích|mall|quảng cáo|ad)$/i.test(t);
  };
  const rows=[]; const seen=new Set();
  const allAnchors=Array.from(document.querySelectorAll('a[href]'));
  const preferred=Array.from(document.querySelectorAll('a[href*="-i."],a[href*="/product/"]'));
  const anchors=preferred.length?preferred:allAnchors;
  let productAnchors=0;
  for(const a of anchors){
    const identity=parseProductIdentity(a.href||a.getAttribute('href')||'');
    if(!identity)continue;
    productAnchors++;
    const key=`${identity.shopId}.${identity.itemId}`;
    if(seen.has(key))continue;
    let card=a;
    for(let i=0;i<7 && card?.parentElement;i++){
      const t=clean(card.innerText||'');
      if(t.length>=20 && (/[₫đ]\s*[0-9]/i.test(t)||/[0-9]\s*[₫đ]/i.test(t)||card.querySelector?.('img')))break;
      card=card.parentElement;
    }
    const text=clean(card?.innerText||a.innerText||'');
    const imgs=Array.from(card?.querySelectorAll?.('img')||[]);
    const img=imgs.find(x=>String(x.currentSrc||x.src||x.getAttribute?.('data-src')||'').trim())||imgs[0]||null;
    const image=String(img?.currentSrc||img?.src||img?.getAttribute?.('data-src')||'').trim();
    const candidates=[];
    const push=(v)=>{v=clean(v);if(v&&!candidates.includes(v))candidates.push(v)};
    push(a.getAttribute?.('aria-label')); push(a.getAttribute?.('title'));
    push(img?.alt);
    for(const el of Array.from(card?.querySelectorAll?.('[data-sqe="name"],[aria-label],[title]')||[]).slice(0,12)){
      push(el.getAttribute?.('aria-label')); push(el.getAttribute?.('title')); push(el.innerText);
    }
    for(const line of String(card?.innerText||'').split(/\n+/).map(clean).filter(Boolean)){
      if(line.length>=4&&!isNoiseTitle(line)&&!/(đã bán|sold|voucher|giảm\s*\d+%|rẻ vô địch|shop yêu thích)/i.test(line))push(line);
    }
    push(titleFromUrl(identity.url));
    let title=candidates.find(x=>x.length>=6&&!isNoiseTitle(x))||candidates.find(x=>!isNoiseTitle(x))||`Shopee product ${identity.itemId}`;
    let price='';
    const pm=text.match(/(?:₫|đ)\s*([0-9][0-9.,]{1,})|([0-9][0-9.,]{1,})\s*(?:₫|đ)/i); if(pm)price=pm[1]||pm[2]||'';
    let sold=''; const sm=text.match(/(?:đã bán|sold)\s*([0-9.,kK+]+)/i); if(sm)sold=sm[1]||'';
    seen.add(key);
    rows.push({url:canonical(identity),title:title.slice(0,300),price,sold,image,keyword,shopId:identity.shopId,itemId:identity.itemId,source:'browser_search_dom_v14112'});
    if(rows.length>=Math.max(1,Math.min(20,Number(limit)||6)))break;
  }
  const bodyText=clean(document.body?.innerText||'');
  return {
    items:rows,
    diag:{
      readyState:document.readyState,
      pageTitle:clean(document.title||'').slice(0,180),
      finalUrl:location.href,
      totalAnchors:allAnchors.length,
      preferredAnchors:preferred.length,
      productAnchors,
      accepted:rows.length,
      bodyChars:bodyText.length,
      bodySample:bodyText.slice(0,700)
    }
  };
}

async function searchShopeeProductsForServer(message){
  assertServerAutomationAllowed("shopee_search");
  const requestId=String(message?.requestId||'').trim();
  const keyword=String(message?.keyword||'').trim().slice(0,120);
  const limit=Math.max(1,Math.min(20,Number(message?.limit)||6));
  if(!requestId||!keyword)throw new Error('SHOPEE_SEARCH_PRODUCTS thiếu requestId/keyword');
  let tab=null,oldActive=null,result=null,reloaded=false;
  try{
    oldActive=(await chrome.tabs.query({active:true,currentWindow:true}).catch(()=>[]))?.[0]||null;
    const url='https://shopee.vn/search?keyword='+encodeURIComponent(keyword);
    assertServerAutomationAllowed("shopee_search");
    tab=await chrome.tabs.create({url,active:false});
    if(!Number.isInteger(tab?.id))throw new Error('Không mở được tab tìm kiếm Shopee.');
    serverTemporaryTabs.add(tab.id);
    await waitTabLoaded(tab.id,25000); await sleep(1600);
    const collected=new Map();
    let lastDiag={};
    for(let attempt=0;attempt<18;attempt++){
      assertServerAutomationAllowed("shopee_search");
      try{
        const x=await chrome.scripting.executeScript({target:{tabId:tab.id},func:shopeeSearchExtractor,args:[keyword,Math.min(20,Math.max(limit,12))]});
        result=x?.[0]?.result||result;
        lastDiag=result?.diag||lastDiag;
        for(const row of (result?.items||[])){
          const key=String(row?.shopId||'')+'.'+String(row?.itemId||'');
          if(key!=='.'&&!collected.has(key))collected.set(key,row);
        }
        if(collected.size>=limit)break;
      }catch{}
      // Shopee virtualizes/lazy-loads the result grid. Keep moving through the page until
      // the requested count is actually collected; do NOT stop just because 1-3 cards appeared.
      const frac=Math.min(0.96,0.14+attempt*0.07);
      await chrome.scripting.executeScript({target:{tabId:tab.id},func:(f)=>{
        const h=Math.max(document.documentElement?.scrollHeight||0,document.body?.scrollHeight||0,1200);
        window.scrollTo(0,Math.max(500,Math.floor(h*f)));
      },args:[frac]}).catch(()=>{});
      if(attempt===4){await chrome.tabs.update(tab.id,{active:true}).catch(()=>{});await sleep(1000);}
      if(attempt===11&&!reloaded && collected.size===0){
        reloaded=true;
        await chrome.tabs.reload(tab.id).catch(()=>{});
        await waitTabLoaded(tab.id,20000).catch(()=>{});
        await sleep(1600);
      }
      await sleep(attempt<7?650:900);
    }
    const items=Array.from(collected.values()).slice(0,limit);
    const diag={...(lastDiag||{}),accepted:items.length,requested:limit,collected:collected.size};
    const low=String(diag.bodySample||'').toLowerCase();
    if(!items.length && (low.includes('xác minh')||low.includes('captcha')||low.includes('đăng nhập')||low.includes('login'))){
      throw new Error('Shopee yêu cầu đăng nhập/xác minh trước khi tìm sản phẩm.');
    }
    if(!items.length){
      await appendLog(`SHOPEE SEARCH DOM DIAG · ${keyword} · anchors=${diag.totalAnchors??'?'} · productAnchors=${diag.productAnchors??'?'} · preferred=${diag.preferredAnchors??'?'} · body=${diag.bodyChars??'?'} · url=${diag.finalUrl||'-'}`,'warning');
    }
    if(!sendServerMessage({type:'SHOPEE_SEARCH_RESULT',requestId,ok:true,items,keyword,diag}))throw new Error('Mất kết nối server trước khi gửi kết quả tìm Shopee.');
    await appendLog(`SHOPEE SEARCH OK · ${keyword} · ${items.length} kết quả`,'success');
  }catch(error){
    const msg=error?.message||String(error);
    sendServerMessage({type:'SHOPEE_SEARCH_RESULT',requestId,ok:false,error:msg,keyword,diag:result?.diag||null});
    await appendLog(`SHOPEE SEARCH ERROR · ${keyword} · ${msg}`,'error');
  }finally{
    const aborted=!serverAutomationAllowed;
    if(!aborted && Number.isInteger(oldActive?.id))await chrome.tabs.update(oldActive.id,{active:true}).catch(()=>{});
    if(Number.isInteger(tab?.id)){serverTemporaryTabs.delete(tab.id);await chrome.tabs.remove(tab.id).catch(()=>{});}
  }
}



function shopeeAffiliateExtractor(links,subIds){
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const isVisible=el=>!!(el&&el.isConnected&&el.offsetParent!==null);
  const isBox=el=>el&&(el.tagName==='TEXTAREA'||(el.tagName==='INPUT'&&!['hidden','checkbox','radio','submit','button'].includes(String(el.type||'').toLowerCase())));
  const setVal=(el,val)=>{el.focus();el.value=val;el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:String(val||'')}));el.dispatchEvent(new Event('change',{bubbles:true}));};
  const boxes=[...document.querySelectorAll('textarea,input')].filter(isBox).filter(el=>!el.disabled&&!el.readOnly&&isVisible(el));
  const linkText=links.join('\n');
  const meta=el=>clean([el.placeholder,el.getAttribute('aria-label'),el.name,el.id,el.closest('label')?.innerText,el.parentElement?.innerText].filter(Boolean).join(' ')).toLowerCase();
  let linkBox=boxes.find(el=>el.tagName==='TEXTAREA'&&/(link|url|shopee|custom)/i.test(meta(el)))||boxes.find(el=>el.tagName==='TEXTAREA')||boxes.find(el=>/(link|url|shopee|custom)/i.test(meta(el)))||boxes[0];
  if(!linkBox) return {clicked:false,error:'No input/textarea found',boxCount:0,title:document.title,url:location.href};
  setVal(linkBox,linkText);
  const rest=boxes.filter(x=>x!==linkBox);
  (subIds||[]).slice(0,5).forEach((v,i)=>{
    const subBox=rest.find(el=>meta(el).includes(`sub_id${i+1}`)||meta(el).includes(`sub id${i+1}`)||meta(el).includes(`subid${i+1}`))||rest[i];
    if(subBox)setVal(subBox,String(v||''));
  });
  const btns=[...document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]')].filter(isVisible);
  const rows=btns.map(b=>({b,t:clean(b.innerText||b.value||b.getAttribute('aria-label')||'').toLowerCase()})).filter(x=>x.t&&!x.b.disabled);
  const badWords=['close','cancel','copy','clear','delete','ok','hủy','đóng','sao chép','xóa'];
  const goodWords=['lấy link','tạo link','chuyển link','rút gọn','generate','convert','create short','get link','custom link'];
  const hit=rows.find(x=>goodWords.some(w=>x.t.includes(w))&&!badWords.some(w=>x.t.includes(w)));
  if(!hit) return {clicked:false,error:'No safe convert button found',buttons:rows.map(x=>x.t).slice(0,20),boxCount:boxes.length,title:document.title,url:location.href};
  hit.b.scrollIntoView({block:'center'});
  hit.b.click();
  return {clicked:true,button:hit.t,boxCount:boxes.length,title:document.title,url:location.href};
}
function shopeeAffiliateScrape(originals){
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const raw=[];
  for(const el of document.querySelectorAll('a,input,textarea,td,div,span')){
    const vals=[];
    if(el.tagName==='A')vals.push(el.href);
    if('value' in el)vals.push(el.value);
    vals.push(el.innerText,el.textContent);
    for(const v of vals){
      const t=clean(v||'');
      if(/https:\/\/(s\.shopee\.vn|shope\.ee)\//i.test(t)) raw.push(t);
    }
  }
  const tokens=[];
  for(const r of raw){
    const m=r.match(/https:\/\/(?:s\.shopee\.vn|shope\.ee)\/[^\s"'<>]+/ig)||[];
    for(const x of m){
      const link=x.replace(/[),.;]+$/,'');
      if(!/affiliate\/dieu-khoan|terms|policy|privacy/i.test(link)) tokens.push(link);
    }
  }
  const uniq=[...new Set(tokens)];
  const out=[];
  for(let i=0;i<(originals||[]).length;i++) out.push({origin_url:originals[i],affiliate_url:uniq[i]||''});
  const body=clean(document.body?.innerText||'');
  return {items:out,found:uniq,bodySample:body.slice(0,1000),url:location.href,title:document.title,risk:['90309999','redirect_to_error_page','risk','captcha','xác minh','dang nhap','đăng nhập'].some(w=>body.toLowerCase().includes(w))};
}
async function findShopeeAffiliateTab(){
  const tabs=await chrome.tabs.query({url:['https://affiliate.shopee.vn/*']}).catch(()=>[]);
  const current=(await chrome.tabs.query({active:true,currentWindow:true}).catch(()=>[]))?.[0]||null;
  const custom=tabs.find(t=>String(t.url||'').startsWith('https://affiliate.shopee.vn/offer/custom_link'));
  const sameWindow=tabs.find(t=>t.windowId===current?.windowId&&String(t.url||'').includes('affiliate.shopee.vn'));
  return custom||sameWindow||tabs[0]||null;
}
function shopeeAffiliateDiagExtractor(){
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const nodes=[...document.querySelectorAll('textarea,input,button,[role="button"],a,div,span')];
  const visible=el=>!!(el&&el.isConnected&&el.offsetParent!==null);
  const boxes=[...document.querySelectorAll('textarea,input')].filter(visible).map((el,i)=>({i,tag:el.tagName,type:el.type||'',placeholder:el.placeholder||'',valueSample:clean(el.value||'').slice(0,220),valueLength:String(el.value||'').length,aria:el.getAttribute('aria-label')||'',name:el.name||'',id:el.id||''}));
  const buttons=[...document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]')].filter(visible).map(el=>clean(el.innerText||el.value||el.getAttribute('aria-label')||'')).filter(Boolean).slice(0,40);
  const text=clean(document.body?.innerText||'');
  const links=[];
  for(const el of nodes){
    const vals=[];
    if(el.tagName==='A')vals.push(el.href);
    if('value' in el)vals.push(el.value);
    vals.push(el.innerText,el.textContent);
    for(const v of vals){
      const m=String(v||'').match(/https:\/\/(?:s\.shopee\.vn|shope\.ee|affiliate\.shopee\.vn|shopee\.vn)\/[^\s"'<>]+/ig)||[];
      for(const x of m)links.push(x.replace(/[),.;]+$/,''));
    }
  }
  const resources=(performance.getEntriesByType?.('resource')||[]).map(x=>String(x.name||'')).filter(x=>/shopee|custom|link|offer|batch|api/i.test(x)).slice(-80);
  return {url:location.href,title:document.title,readyState:document.readyState,bodySample:text.slice(0,1500),risk:['90309999','redirect_to_error_page','risk','captcha','xác minh','đăng nhập','failed to fetch','blocked'].some(w=>text.toLowerCase().includes(w)),boxes,buttons,links:[...new Set(links)].slice(0,30),resources};
}
async function diagShopeeAffiliateForServer(message){
  const requestId=String(message?.requestId||'').trim();
  try{
    let tab=await findShopeeAffiliateTab();
    if(!tab?.id) tab=await chrome.tabs.create({url:'https://affiliate.shopee.vn/offer/custom_link',active:true});
    else await chrome.tabs.update(tab.id,{active:true}).catch(()=>{});
    await waitTabLoaded(tab.id,20000).catch(()=>{});
    await sleep(800);
    const diag=(await chrome.scripting.executeScript({target:{tabId:tab.id},func:shopeeAffiliateDiagExtractor}).catch(e=>[{result:{error:e?.message||String(e)}}]))?.[0]?.result||{};
    sendServerMessage({type:'SHOPEE_AFFILIATE_DIAG_RESULT',requestId,ok:true,diag,ts:Date.now()});
  }catch(error){
    sendServerMessage({type:'SHOPEE_AFFILIATE_DIAG_RESULT',requestId,ok:false,error:error?.message||String(error),ts:Date.now()});
  }
}

async function convertShopeeAffiliateForServer(message){
  assertServerAutomationAllowed('shopee_affiliate');
  const requestId=String(message?.requestId||'').trim();
  const links=(message?.links||[]).map(x=>String(x||'').trim()).filter(Boolean).slice(0,5);
  const subIds=(message?.subIds||[]).map(x=>String(x||'').replace(/[^a-zA-Z0-9]/g,'').slice(0,50)).filter(Boolean).slice(0,5);
  if(!requestId||!links.length)throw new Error('SHOPEE_AFFILIATE_CONVERT missing requestId/links');
  let tab=null,oldActive=null,created=false;
  try{
    oldActive=(await chrome.tabs.query({active:true,currentWindow:true}).catch(()=>[]))?.[0]||null;
    tab=await findShopeeAffiliateTab();
    if(tab?.id){
      await chrome.tabs.update(tab.id,{active:true,url:String(tab.url||'').startsWith('https://affiliate.shopee.vn/offer/custom_link')?undefined:'https://affiliate.shopee.vn/offer/custom_link'}).catch(()=>{});
    }else{
      tab=await chrome.tabs.create({url:'https://affiliate.shopee.vn/offer/custom_link',active:true});
      created=true;
    }
    if(!Number.isInteger(tab?.id))throw new Error('Cannot open Shopee Affiliate tab.');
    if(created)serverTemporaryTabs.add(tab.id);
    await waitTabLoaded(tab.id,30000); await sleep(1800);
    let fill=null,lastScrape=null;
    const currentUrl=(await chrome.tabs.get(tab.id).catch(()=>({url:''})))?.url||'';
    if(currentUrl.includes('/auth')) throw new Error('Shopee Affiliate tab is on auth page. Finish login/verification on that tab first.');
    fill=(await chrome.scripting.executeScript({target:{tabId:tab.id},func:shopeeAffiliateExtractor,args:[links,subIds]}).catch(e=>[{result:{error:e?.message||String(e)}}]))?.[0]?.result||fill;
    if(fill?.error) throw new Error(fill.error+` ? buttons=${(fill.buttons||[]).join('|')}`);
    await appendLog(`SHOPEE AFFILIATE CLICK · ${fill?.button||'convert'} · ${links.length} link`,'info');
    for(let i=0;i<10;i++){
      const currentUrl=(await chrome.tabs.get(tab.id).catch(()=>({url:''})))?.url||'';
      if(currentUrl.includes('/auth')) throw new Error('Shopee Affiliate tab is on auth page. Finish login/verification on that tab first.');
      await sleep(1600+i*700);
      lastScrape=(await chrome.scripting.executeScript({target:{tabId:tab.id},func:shopeeAffiliateScrape,args:[links]}).catch(()=>[]))?.[0]?.result||lastScrape;
      if(String(lastScrape?.url||'').includes('/auth')) throw new Error('Shopee Affiliate tab is on auth page. Finish login/verification on that tab first.');
      if(lastScrape?.risk) throw new Error('Shopee risk-control/auth detected on custom_link tab. Try clean browser/session or disable request-hook extensions.');
      const ready=(lastScrape?.items||[]).filter(x=>x.affiliate_url).length;
      if(ready>=links.length){
        sendServerMessage({type:'SHOPEE_AFFILIATE_RESULT',requestId,ok:true,items:lastScrape.items,found:lastScrape.found,diag:{fill,url:lastScrape.url,title:lastScrape.title}});
        await appendLog(`SHOPEE AFFILIATE OK ? ${ready}/${links.length}`,'success');
        return;
      }
    }
    const ready=(lastScrape?.items||[]).filter(x=>x.affiliate_url).length;
    if(!ready){const blank=(lastScrape?.bodySample||'').includes('Vui l?ng sao ch?p link r?t g?n')&&!((lastScrape?.found||[]).length);throw new Error(blank?'Shopee returned blank Custom Link modal: session/browser is blocked or another extension hook broke fetch/XHR. Try clean browser profile or disable hook/content-script extensions.':'No short affiliate link found. Page may be blocked, blank, or hook/fetch extension broke Shopee request.');}
    sendServerMessage({type:'SHOPEE_AFFILIATE_RESULT',requestId,ok:true,items:lastScrape.items,found:lastScrape.found,partial:true,diag:{fill,url:lastScrape.url,title:lastScrape.title,bodySample:lastScrape.bodySample}});
  }catch(error){
    const msg=error?.message||String(error);
    sendServerMessage({type:'SHOPEE_AFFILIATE_RESULT',requestId,ok:false,error:msg});
    await appendLog(`SHOPEE AFFILIATE ERROR ? ${msg}`,'error');
  }finally{
    const aborted=!serverAutomationAllowed;
    if(!aborted && Number.isInteger(oldActive?.id) && !created)await chrome.tabs.update(oldActive.id,{active:true}).catch(()=>{});
    if(created&&Number.isInteger(tab?.id)){serverTemporaryTabs.delete(tab.id);await chrome.tabs.remove(tab.id).catch(()=>{});}
  }
}

function serverFlowSignature(flow={}){
  const keys=['imageModel','videoModel','aspectRatio','imageOutputs','videoDuration','videoOutputs','videoExtendFactor','submitPolicy'];
  const obj={}; for(const k of keys) obj[k]=flow?.[k]??null;
  return JSON.stringify(obj);
}

function takeQueuedServerJobs(signature){
  const out=[];
  for(let i=0;i<serverJobQueue.length;){
    const msg=serverJobQueue[i];
    if(serverFlowSignature(msg?.flow||{})===signature){out.push(msg);serverJobQueue.splice(i,1);}else i++;
  }
  if(out.length && activeServerBatch?.signature===signature){
    for(const msg of out) activeServerBatch.jobs.set(String(msg.jobId||''),msg);
  }
  return out;
}

function enqueueServerFlowJob(message){
  const jobId=String(message?.jobId||'').trim();
  if(!jobId) return;
  if(!serverAutomationAllowed){
    if(serverSocket && (serverSocket.readyState===WebSocket.OPEN || serverSocket.readyState===WebSocket.CONNECTING) && !serverRunPromise && !activeServerBatch){
      serverAutomationAllowed=true;
      serverFailSafeReason=null;
      serverFailSafeLatched=false;
    }else{
      sendServerMessage({type:'FLOW_JOB_RESULT',jobId,ok:false,error:`Extension FAIL-SAFE đang khóa browser: ${serverFailSafeReason||'server_offline'}`});
      return;
    }
  }
  if(flowUiCircuitOpen()){
    const seconds=Math.max(1,Math.ceil((flowUiCircuitUntil-Date.now())/1000));
    sendServerMessage({type:'FLOW_JOB_RESULT',jobId,ok:false,error:`FLOW_UI_CIRCUIT_BREAKER · ${seconds}s · ${flowUiCircuitReason}`});
    return;
  }
  if(serverAcceptedJobIds.has(jobId)){
    sendServerMessage({type:'FLOW_JOB_ACCEPTED',jobId,runId:`queued_${jobId}`,queuePosition:0,queueDepth:serverJobQueue.length+(activeServerBatch?.jobs?.size||0),duplicate:true});
    return;
  }
  serverAcceptedJobIds.add(jobId);
  serverJobQueue.push(message);
  const sig=serverFlowSignature(message?.flow||{});
  sendServerMessage({type:'FLOW_JOB_ACCEPTED',jobId,runId:`queued_${jobId}`,queuePosition:serverJobQueue.length,queueDepth:serverJobQueue.length+(activeServerBatch?.jobs?.size||0),queuedInExtension:true});
  appendLog(`SERVER QUEUE +1 · ${jobId} · pending=${serverJobQueue.length} · IMAGE cap=9 · VIDEO cap=4`,'info').catch(()=>{});
  if(activeServerBatch?.signature===sig) activeServerBatch.wake?.notify?.();
  if(!serverRunPromise){
    serverRunPromise=runServerQueueLoop().catch(async e=>appendLog(`SERVER QUEUE ERROR · ${e?.message||e}`,'error')).finally(()=>{serverRunPromise=null;if(serverAutomationAllowed&&serverJobQueue.length) queueMicrotask(()=>{if(serverAutomationAllowed&&!serverRunPromise){serverRunPromise=runServerQueueLoop().finally(()=>{serverRunPromise=null;});}});});
  }
}

async function runServerQueueLoop(){
  while(serverJobQueue.length){
    assertServerAutomationAllowed('server queue');
    await waitMediaRecoveryIdle();
    assertServerAutomationAllowed('server queue after recovery');
    if(flowUiCircuitOpen()){
      const seconds=Math.max(1,Math.ceil((flowUiCircuitUntil-Date.now())/1000));
      for(const msg of serverJobQueue.splice(0)){
        const jobId=String(msg?.jobId||'');
        sendServerMessage({type:'FLOW_JOB_RESULT',jobId,ok:false,error:`FLOW_UI_CIRCUIT_BREAKER · ${seconds}s · ${flowUiCircuitReason}`});
        serverAcceptedJobIds.delete(jobId);
      }
      break;
    }
    const first=serverJobQueue.shift();
    const signature=serverFlowSignature(first?.flow||{});
    const initial=[first,...takeQueuedServerJobs(signature)];
    try{
      await runServerJobGroup(initial,signature);
    }catch(error){
      if(!serverAutomationAllowed) throw error;
      await appendLog(`SERVER JOB GROUP lỗi · sẽ tiếp tục queue sau 2s · ${error?.message||error}`,'error');
      await sleep(2000);
    }
  }
}

async function connectServerBridge(force=false){
  const cfg=await getServerBridgeConfig();
  if(!cfg.enabled){await disconnectServerBridge('disabled');return;}
  if(!force && serverSocket && (serverSocket.readyState===WebSocket.OPEN||serverSocket.readyState===WebSocket.CONNECTING)) return;
  if(serverSocket){try{serverSocket.close();}catch{} serverSocket=null;}
  clearTimeout(serverReconnectTimer);
  await setServerStatus({connected:false,url:cfg.url,lastError:null});
  let ws;
  try{ws=new WebSocket(cfg.url);}catch(error){
    await setServerStatus({connected:false,url:cfg.url,lastError:error?.message||String(error)});
    serverReconnectTimer=setTimeout(()=>connectServerBridge(false),1000);return;
  }
  serverSocket=ws;
  ws.onopen=async()=>{
    serverFailSafeLatched=false;
    serverReconnectBackoffMs=500;
    serverFailSafeReason=null;
    serverAutomationAllowed=!(serverRunPromise||activeServerBatch);
    if(!serverAutomationAllowed){
      const waitStarted=Date.now();
      while((serverRunPromise||activeServerBatch) && Date.now()-waitStarted<5000){
        await new Promise(resolve=>setTimeout(resolve,100));
      }
      serverAutomationAllowed=!(serverRunPromise||activeServerBatch);
      if(serverAutomationAllowed) serverFailSafeReason=null;
    }
    if(serverAutomationAllowed){
      try{
        await runtimeReady;
        runtimeCache={
          ...runtimeCache,
          running:false,
          progressPercent:0,
          progressLabel:'IDLE · SẴN SÀNG',
          progressDetail:'Server connected · chờ job mới.',
          lastLevel:'info',
          activeRunId:null,
          serverJobId:null,
          updatedAt:Date.now()
        };
        await chrome.storage.local.set({flowPairAutoRuntime:runtimeCache});
      }catch{}
    }
    await setServerStatus({connected:true,url:cfg.url,lastError:serverAutomationAllowed?null:'Đang chờ job cũ dừng hẳn',connectedAt:Date.now()});
    // Never let server dispatch a NEW job before pending completion/file reports are replayed.
    const pendingReports=await pendingServerReportCount();
    const helloReady=Boolean(serverAutomationAllowed && pendingReports===0);
    sendServerMessage({type:'AGENT_HELLO',role:'flow-extension',extensionId:chrome.runtime.id,workerId:chrome.runtime.id,version:chrome.runtime.getManifest().version,buildId:'v2.8-from-v2.5-core',runtime:runtimeCache,failSafeReady:helloReady,capabilities:{serverQueue:true,signedUrlDownload:true,imageRecovery:true,videoRecovery:true,shopeeSearch:true},pendingReports});
    if(pendingReports>0){
      await flushPendingServerReports();
      if(serverAutomationAllowed && serverSocket===ws && ws.readyState===WebSocket.OPEN){
        sendServerMessage({type:'AGENT_READY',runtime:runtimeCache,replayedReports:true});
      }
    }
    try{
      await trackedSceneMediaReady;
      for(const item of Object.values(trackedSceneMediaObject)){
        if(item.jobId && item.localPath && item.status==='DOWNLOADED'){
          sendServerMessage({
            type:'VIDEO_FILE_READY',
            jobId:item.jobId,
            sceneId:item.sceneId||1,
            sceneIndex:item.sceneIndex||0,
            mediaId:item.mediaId,
            localPath:item.localPath
          });
        }
      }
    }catch{}
    if(!serverAutomationAllowed){
      (async()=>{
        while(serverSocket===ws && ws.readyState===WebSocket.OPEN && !serverAutomationAllowed){
          if(!(serverRunPromise||activeServerBatch)){
            serverAutomationAllowed=true;
            serverFailSafeReason=null;
            try{
              await runtimeReady;
              runtimeCache={...runtimeCache,running:false,progressPercent:0,progressLabel:'IDLE · SẴN SÀNG',progressDetail:'Old run đã dừng hẳn · chờ job mới.',lastLevel:'info',activeRunId:null,serverJobId:null,updatedAt:Date.now()};
              await chrome.storage.local.set({flowPairAutoRuntime:runtimeCache});
            }catch{}
            sendServerMessage({type:'AGENT_READY',runtime:runtimeCache});
            break;
          }
          await new Promise(resolve=>setTimeout(resolve,250));
        }
      })().catch(()=>{});
    }
    clearInterval(serverHeartbeatTimer);
    serverHeartbeatTimer=setInterval(()=>{
      if(serverSocket===ws && ws.readyState===WebSocket.OPEN){
        sendServerMessage({type:'AGENT_HEARTBEAT',runtimeState:runtimeCache?.progressLabel||'IDLE',jobId:runtimeCache?.serverJobId||null,running:Boolean(runtimeCache?.running),progressUpdatedAt:runtimeCache?.updatedAt||null});
      }
    },20000);
    if(runtimeCache?.serverJobId && !runtimeCache.running && runtimeCache.progressLabel==='Đã gián đoạn'){
      sendServerMessage({type:'FLOW_JOB_INTERRUPTED',jobId:runtimeCache.serverJobId,error:runtimeCache.progressDetail||'Extension service worker restarted.'});
    }
  };
  ws.onmessage=event=>{
    let message; try{message=JSON.parse(String(event.data||''));}catch{return;}
    if(message?.type==='PING'){sendServerMessage({type:'PONG',runtime:runtimeCache});return;}
    if(message?.type==='HEARTBEAT_ACK'){return;}
    if(message?.type==='STOP_ALL'){
      if(String(message?.reason||'')==='dispatch_ack_timeout'){
        return;
      }
      failSafeStopAll(String(message.reason||'server_stop_all'),{closeSocket:true,sendAck:true}).catch(()=>{});
      return;
    }
    if(message?.type==='CANCEL_JOB'){
      const targetJobId = String(message?.jobId || '');
      const targetRunId = message?.runId ? String(message.runId) : null;
      const targetAttemptId = message?.attemptId ? String(message.attemptId) : null;
      const currentJobId = String(runtimeCache?.serverJobId || '');
      const currentRunId = runtimeCache?.runId ? String(runtimeCache.runId) : null;
      const currentAttemptId = runtimeCache?.attemptId ? String(runtimeCache.attemptId) : null;
      if(targetRunId && currentRunId && targetRunId !== currentRunId){
        return;
      }
      if(targetAttemptId && currentAttemptId && targetAttemptId !== currentAttemptId){
        return;
      }
      if(!targetJobId || targetJobId === currentJobId){
        appendLog(`⚠️ SERVER CANCELLED JOB ${targetJobId}: ${message?.reason || 'cancelled'}`, 'warn').catch(()=>{});
        if(runtimeCache){
          runtimeCache.running = false;
          runtimeCache.progressLabel = 'Đã hủy theo yêu cầu server';
        }
      }
      return;
    }
    if(message?.type==='DOWNLOAD_MEDIA_FILES'){
      queueMediaRecovery('recover video',()=>downloadMediaIdsForServer({jobId:String(message.jobId||''),sceneId:Number(message.sceneId||0),mediaIds:message.mediaIds||[]}))
        .catch(async error=>{await appendLog(`❌ RECOVER VIDEO: ${error?.message||error}`,'error');await sendOrStoreServerReport({type:'VIDEO_FILE_ERROR',jobId:message.jobId,sceneId:message.sceneId,error:error?.message||String(error)});});
      return;
    }
    if(message?.type==='DOWNLOAD_IMAGE_MEDIA_FILES'){
      queueMediaRecovery('recover image',()=>downloadImageMediaIdsForServer({jobId:String(message.jobId||''),sceneId:Number(message.sceneId||0),mediaIds:message.mediaIds||[]}))
        .catch(async error=>{await appendLog(`❌ RECOVER IMAGE: ${error?.message||error}`,'error');await sendOrStoreServerReport({type:'IMAGE_FILE_ERROR',jobId:message.jobId,sceneId:message.sceneId,error:error?.message||String(error)});});
      return;
    }
    if(message?.type==='SHOPEE_SEARCH_PRODUCTS'){
      searchShopeeProductsForServer(message).catch(error=>sendServerMessage({type:'SHOPEE_SEARCH_RESULT',requestId:message.requestId,ok:false,error:error?.message||String(error)}));
      return;
    }
    if(message?.type==='SHOPEE_INSPECT_PRODUCT'){
      inspectShopeeProductForServer(message).catch(error=>sendServerMessage({type:'SHOPEE_PRODUCT_RESULT',requestId:message.requestId,ok:false,error:error?.message||String(error)}));
      return;
    }
    if(message?.type==='SHOPEE_AFFILIATE_CONVERT'){
      convertShopeeAffiliateForServer(message).catch(error=>sendServerMessage({type:'SHOPEE_AFFILIATE_RESULT',requestId:message.requestId,ok:false,error:error?.message||String(error)}));
      return;
    }
    if(message?.type==='CHECK_SHOPEE_SESSION'){
      checkShopeeSessionHealth().then(res=>sendServerMessage({type:'SHOPEE_SESSION_HEALTH_RESULT',requestId:message.requestId,...res}))
        .catch(err=>sendServerMessage({type:'SHOPEE_SESSION_HEALTH_RESULT',requestId:message.requestId,ok:false,loggedIn:false,error:err?.message||String(err)}));
      return;
    }
    if(message?.type==='SHOPEE_AFFILIATE_DIAG'){
      diagShopeeAffiliateForServer(message).catch(error=>sendServerMessage({type:'SHOPEE_AFFILIATE_DIAG_RESULT',requestId:message.requestId,ok:false,error:error?.message||String(error)}));
      return;
    }
    if(message?.type==='RUN_FLOW_JOB'){
      enqueueServerFlowJob(message);
      return;
    }
  };
  ws.onerror=()=>{};
  ws.onclose=async event=>{
    clearInterval(serverHeartbeatTimer); serverHeartbeatTimer=null;
    if(serverSocket===ws) serverSocket=null;
    serverAutomationAllowed=false;
    serverFailSafeReason=`server_disconnect_${Number(event?.code||0)}`;
    await failSafeStopAll(serverFailSafeReason,{closeSocket:false,sendAck:false}).catch(()=>{});
    await setServerStatus({connected:false,url:cfg.url,lastError:'SERVER OFFLINE · extension đã STOP ALL'});
    const latest=await getServerBridgeConfig();
    if(latest.enabled){
      clearTimeout(serverReconnectTimer);
      const baseDelay=serverReconnectBackoffMs;
      const delay=Math.max(1000,Math.round(baseDelay*(0.85+Math.random()*0.30)));
      serverReconnectBackoffMs=Math.min(60000,baseDelay<10000?15000:baseDelay<30000?30000:60000);
      serverReconnectTimer=setTimeout(()=>connectServerBridge(false),delay);
    }
  };
}

function isFlowToolTabUrl(url=''){
  return /^https:\/\/labs\.google\/fx\/(?:[a-z]{2}\/)?tools\/flow(?:\/|$|[?#])/i.test(String(url||''));
}

async function findOrOpenFlowTab(){
  assertServerAutomationAllowed('find/open Flow tab');
  const tabs=await chrome.tabs.query({});
  let tab=tabs.find(t=>Number.isInteger(t.id)&&projectIdFromFlowUrl(t.url||'')) ||
    tabs.find(t=>Number.isInteger(t.id)&&isFlowToolTabUrl(t.url||''));
  if(!tab){
    tab=await chrome.tabs.create({url:'https://labs.google/fx/tools/flow',active:true});
  }else{
    try{await chrome.tabs.update(tab.id,{active:true});}catch{}
    try{if(Number.isInteger(tab.windowId)) await chrome.windows?.update?.(tab.windowId,{focused:true});}catch{}
  }
  if(!Number.isInteger(tab?.id)) throw new Error('Không mở được tab Google Flow.');
  return tab;
}

function compactServerResults(records=[]){
  return (Array.isArray(records)?records:[]).map(record=>({
    index:Number(record?.serverSceneIndex??record?.index??0),
    sceneId:Number(record?.sceneId||0)||Number(record?.serverSceneIndex??record?.index??0)+1,
    imageState:String(record?.imageState||'WAIT'),
    videoState:String(record?.videoState||'WAIT'),
    videoRetryCount:Number(record?.videoRetryCount||0),
    error:record?.error?String(record.error):null,
    cancelled:Boolean(record?.cancelled),
    cancelReason:record?.cancelReason?String(record.cancelReason):null,
    image:record?.selectedImage?{
      mediaId:record.selectedImage.mediaId||null,
      url:record.selectedImage.url||null,
      title:record.selectedImage.title||record.selectedImage.workflowTitle||null,
      needsRecovery:Boolean(record.selectedImage.mediaId&&!record.selectedImage.url)
    }:null,
    videoMediaIds:[...new Set(record?.videoIds||[])].filter(Boolean),
    videoAssets:(record?.videoAssets||[]).map(x=>({mediaId:x?.mediaId||null,title:x?.title||null,workflowId:x?.workflowId||null})).filter(x=>x.mediaId),
    videoChainMediaIds:[...new Set(record?.videoChainMediaIds||[])].filter(Boolean),
    downloads:Array.isArray(record?.downloads)?record.downloads.map(x=>({mediaId:x?.mediaId||null,mediaIndex:Number(x?.mediaIndex||0),localPath:x?.localPath||x?.filename||null,state:x?.state||null})).filter(x=>x.mediaId&&x.localPath):[],
    downloadState:record?.downloadState||null,
    downloadError:record?.downloadError||null
  }));
}

function compactServerFailures(failures=[]){
  return (Array.isArray(failures)?failures:[]).map(item=>({
    index:Number(item?.index||0),
    error:String(item?.error||'Unknown error')
  }));
}

async function syncServerFlowToPopup(flow={}){
  // Server is the source of truth for server jobs. Mirror its active settings into
  // extension storage so the popup never says x4 while the server job is x1.
  const {flowPairAutoForm:old={}}=await chrome.storage.local.get('flowPairAutoForm').catch(()=>({}));
  const next={...old};
  const map={
    imageModel:'imageModel',videoModel:'videoModel',imageConcurrency:'imageConcurrency',videoConcurrency:'videoConcurrency',
    submitPolicy:'submitPolicy',autoDownloadVideo:'autoDownloadVideo',maxSubmitsPerMinute:'maxSubmitsPerMinute',submitGapMs:'submitGapMs',
    aspectRatio:'aspectRatio',imageOutputs:'imageOutputs',videoDuration:'videoDuration',videoOutputs:'videoOutputs',videoExtendFactor:'videoExtendFactor',videoExtendPrompt:'videoExtendPrompt',
    imageTimeoutSec:'imageTimeoutSec',videoTimeoutSec:'videoTimeoutSec',systemicFailureLimit:'systemicFailureLimit'
  };
  for(const [src,dst] of Object.entries(map)) if(flow[src]!==undefined&&flow[src]!==null) next[dst]=flow[src];
  next.serverEnabled=true;
  next.serverUrl=next.serverUrl||DEFAULT_SERVER_URL;
  await chrome.storage.local.set({flowPairAutoForm:next});
  return next;
}

async function runServerJobGroup(initialMessages,signature){
  assertServerAutomationAllowed('run server job group');
  const initial=(Array.isArray(initialMessages)?initialMessages:[]).filter(m=>m&&m.jobId);
  if(!initial.length) return;
  const first=initial[0];
  await syncServerFlowToPopup(first?.flow||{});
  const tab=await findOrOpenFlowTab();
  await injectPage(tab.id);
  await resumePageWorldAutomation(tab.id);
  const abortState=await callPage(tab.id,'getAbortState',[]).catch(()=>null);
  if(abortState?.aborted) throw new Error(`Page bridge v?n aborted: ${abortState.reason||'unknown'}`);
  const runId=`server_queue_${Date.now()}_${Math.random().toString(36).slice(2,7)}`;
  await resetRuntimeForRun(runId,String(first.jobId||''));
  activeServerBatch={signature,jobs:new Map(initial.map(m=>[String(m.jobId),m])),wake:createWakeSignal(),take:()=>takeQueuedServerJobs(signature),blocked:false,blockReason:''};
  await appendLog(`SERVER QUEUE RUN · jobs=${initial.length} · global IMAGE=9 · VIDEO=4`,'info');
  try{
    const batch=await runAutomation({tabId:tab.id,serverJobMessages:initial,serverDynamicBatch:activeServerBatch,options:{...(first.flow||{}),autoDownloadVideo:first?.flow?.autoDownloadVideo!==false}});
    const allRecords=batch?.results||[];
    const jobs=[...activeServerBatch.jobs.values()];
    for(const msg of jobs){
      const jobId=String(msg.jobId||'');
      const records=allRecords.filter(r=>String(r?.serverJobId||'')===jobId);
      const failures=records.filter(r=>r?.error).map(r=>({index:Number(r?.serverSceneIndex??r?.index??0),error:String(r?.error||'Unknown error')}));
      const safeResults=compactServerResults(records);
      if(failures.length){
        const firstError=String(failures[0]?.error||'Unknown error');
        await sendOrStoreServerReport({type:'FLOW_JOB_RESULT',jobId,ok:false,error:`Có ${failures.length} scene lỗi. Lỗi đầu: ${firstError}`,results:safeResults,failures});
      }
      else await sendOrStoreServerReport({type:'FLOW_JOB_RESULT',jobId,ok:true,results:safeResults});
      serverAcceptedJobIds.delete(jobId);
    }
    await finishRuntime(true,`Extension queue xong ${jobs.length} server job.`);
  }catch(error){
    let text=error?.message||String(error);
    if(/Debugger is not attached|not attached to the tab/i.test(text)) text += ' | Khắc phục: đóng DevTools của tab Flow + tắt extension Flow cũ, sau đó Reload v14.5.35.';
    await appendLog(`❌ SERVER QUEUE: ${text}`,'error');
    const jobs=[...(activeServerBatch?.jobs?.values?.()||[])];
    for(const msg of jobs){const jobId=String(msg.jobId||'');await sendOrStoreServerReport({type:'FLOW_JOB_RESULT',jobId,ok:false,error:text});serverAcceptedJobIds.delete(jobId);}
    await finishRuntime(false,text);
  }finally{
    activeServerBatch=null;
  }
}


chrome.alarms?.create?.('flowServerBridgeKeepAlive',{periodInMinutes:1});
chrome.alarms?.onAlarm?.addListener?.(alarm=>{if(alarm?.name==='flowServerBridgeKeepAlive')connectServerBridge(false).catch(()=>{});});
chrome.runtime.onStartup.addListener(()=>connectServerBridge(false).catch(()=>{}));
chrome.runtime.onInstalled.addListener(()=>connectServerBridge(false).catch(()=>{}));
chrome.storage.onChanged.addListener((changes,area)=>{
  if(area==='local'&&changes.flowPairAutoForm){
    const before=changes.flowPairAutoForm.oldValue||{},after=changes.flowPairAutoForm.newValue||{};
    if(before.serverEnabled!==after.serverEnabled||before.serverUrl!==after.serverUrl) connectServerBridge(true).catch(()=>{});
  }
});
setTimeout(()=>connectServerBridge(false).catch(()=>{}),250);
// ============================================================================

const defaultRuntime = () => ({
  running: false,
  logs: [],
  lastLevel: 'info',
  progressPercent: 0,
  progressLabel: 'Chưa chạy',
  progressDetail: 'Thanh này dùng chung cho ảnh + video.',
  updatedAt: null,
  activeRunId: null,
  serverJobId: null,
  jobs: {},
  metrics: {imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,done:0,errors:0,total:0}
});

let runtimeCache = defaultRuntime();

const runtimeReady=(async()=>{
  try{
    const {flowPairAutoRuntime}=await chrome.storage.local.get('flowPairAutoRuntime');
    if(flowPairAutoRuntime){
      runtimeCache={...defaultRuntime(),...flowPairAutoRuntime};
      if(runtimeCache.running){
        runtimeCache.running=false;
        runtimeCache.lastLevel='error';
        runtimeCache.progressLabel='Đã gián đoạn';
        runtimeCache.progressDetail='Service worker đã khởi động lại; phiên chạy cũ không thể tiếp tục an toàn.';
        runtimeCache.logs=[...(runtimeCache.logs||[]),{time:new Date().toLocaleTimeString(),text:'❌ Service worker khởi động lại khi job đang chạy; đã đánh dấu phiên cũ là gián đoạn.',level:'error'}].slice(-200);
        await chrome.storage.local.set({flowPairAutoRuntime:runtimeCache});
      }
    }
  }catch{}
})();

async function persistRuntime() {
  runtimeCache.updatedAt = Date.now();
  await chrome.storage.local.set({ flowPairAutoRuntime: runtimeCache });
  try {
    await chrome.runtime.sendMessage({ type: 'FLOW_STATUS_SNAPSHOT', runtime: runtimeCache });
  } catch {}
  sendServerMessage({type:'FLOW_RUNTIME',jobId:runtimeCache.serverJobId||null,runtime:runtimeCache});
}

async function resetRuntimeForRun(runId, serverJobId=null) {
  await runtimeReady;
  runtimeCache = {
    ...defaultRuntime(),
    running: true,
    activeRunId: runId,
    serverJobId,
    logs: [],
    progressPercent: 0,
    progressLabel: 'Đang khởi động',
    progressDetail: 'Đang chuẩn bị worker...'
  };
  await persistRuntime();
}

let lastAppendLogKey='';
let lastAppendLogAt=0;
async function appendLog(text, level='info') {
  const raw=String(text||''); const key=`${level}|${raw}`; const now=Date.now();
  if(key===lastAppendLogKey && now-lastAppendLogAt<800) return;
  lastAppendLogKey=key; lastAppendLogAt=now;
  const line={time:new Date().toLocaleTimeString('vi-VN'),text:raw,level};
  runtimeCache.logs=[...(runtimeCache.logs||[]),line].slice(-500); runtimeCache.lastLevel=level;
  sendServerMessage({type:'EXTENSION_LOG',level:String(level||'info').toUpperCase(),message:raw});
  await persistRuntime();
}

async function setProgress(percent, label, detail='') {
  runtimeCache.progressPercent = Math.max(0, Math.min(100, Number(percent || 0)));
  if (label != null) runtimeCache.progressLabel = label;
  if (detail != null) runtimeCache.progressDetail = detail;
  await persistRuntime();
}

async function finishRuntime(ok, message='') {
  runtimeCache.running = false;
  runtimeCache.progressPercent = ok ? 100 : runtimeCache.progressPercent;
  runtimeCache.progressLabel = ok ? 'Hoàn tất' : 'Đã dừng';
  if (message) runtimeCache.progressDetail = message;
  runtimeCache.lastLevel = ok ? 'success' : 'error';
  await persistRuntime();
}

async function injectPage(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    files: ['page.js']
  });

  // Verify the actual page-world version. Do not trust legacy global flags.
  const version = await callPageRaw(tabId, 'getVersion', []);
  if (!isCompatiblePageVersion(version)) {
    throw new Error(`Page bridge version không tương thích: ${String(version)} · bắt buộc đúng ${EXTENSION_VERSION}`);
  }
  assertServerAutomationAllowed('resume page bridge');
  await resumePageWorldAutomation(tabId);
}

function makeBridgeId() {
  return `__flow_pair_bridge_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

async function callPageRaw(tabId, method, args = []) {
  assertServerAutomationAllowed(`page method ${method}`);
  const bridgeId = makeBridgeId();

  let exec;
  try {
    exec = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: async (bridgeId, methodName, methodArgs) => {
        const write = payload => {
          try {
            let node = document.getElementById(bridgeId);
            if (!node) {
              node = document.createElement('script');
              node.type = 'application/json';
              node.id = bridgeId;
              node.style.display = 'none';
              (document.documentElement || document.body).appendChild(node);
            }
            node.textContent = JSON.stringify(payload);
          } catch {}
        };

        try {
          if (!window.FlowPairAuto) {
            throw new Error('FlowPairAuto chưa được nạp trong MAIN world.');
          }

          const fn = window.FlowPairAuto[methodName];
          if (typeof fn !== 'function') {
            const version = typeof window.FlowPairAuto.getVersion === 'function'
              ? window.FlowPairAuto.getVersion()
              : window.__FLOW_PAIR_AUTO_VERSION__ || 'unknown';
            throw new Error(`Không có method ${methodName}. pageVersion=${version}`);
          }

          const value = await fn(...methodArgs);
          const res = {
            ok: true,
            value: value === undefined ? null : value,
            pageVersion: typeof window.FlowPairAuto.getVersion === 'function'
              ? window.FlowPairAuto.getVersion()
              : window.__FLOW_PAIR_AUTO_VERSION__ || null
          };
          write(res);
          return res;
        } catch (error) {
          const errRes = {
            ok: false,
            error: error?.message || String(error),
            stack: error?.stack || null,
            pageVersion: window.__FLOW_PAIR_AUTO_VERSION__ || null
          };
          write(errRes);
          return errRes;
        }
      },
      args: [bridgeId, method, args]
    });
  } catch (err) {
    throw new Error(`MAIN ${method} executeScript lỗi: ${err?.message || err}`);
  }

  let payload = exec?.[0]?.result;

  // If direct result is null/empty, fallback to reading DOM bridge node
  if (!payload || typeof payload !== 'object') {
    const read = await chrome.scripting.executeScript({
      target: { tabId },
      func: bridgeId => {
        const node = document.getElementById(bridgeId);
        if (!node) return null;
        const text = node.textContent || '';
        node.remove();
        return text;
      },
      args: [bridgeId]
    });

    const raw = read?.[0]?.result;
    if (typeof raw === 'string' && raw) {
      try {
        payload = JSON.parse(raw);
      } catch (error) {
        throw new Error(`DOM bridge parse lỗi cho ${method}: ${error.message}`);
      }
    }
  }

  if (!payload || typeof payload !== 'object') {
    throw new Error(`DOM bridge không có payload cho ${method}.`);
  }

  if (!payload.ok) {
    throw new Error(
      `MAIN ${method} lỗi: ${payload.error || 'unknown'} | pageVersion=${payload.pageVersion || 'unknown'}`
    );
  }

  return payload.value;
}

async function callPage(tabId, method, args = []) {
  return await callPageRaw(tabId, method, args);
}

const debuggeeFor = tabId => ({ tabId });
const netState = new Map();
const fileChooserWaiters = new Map();

function waitFileChooser(tabId,timeoutMs=5000){
  return new Promise((resolve,reject)=>{
    const old=fileChooserWaiters.get(tabId);
    if(old){clearTimeout(old.timer);old.reject(new Error('File chooser waiter replaced.'));}
    const waiter={resolve,reject,timer:null};
    waiter.timer=setTimeout(()=>{
      if(fileChooserWaiters.get(tabId)===waiter) fileChooserWaiters.delete(tabId);
      reject(new Error('Timeout chờ Page.fileChooserOpened'));
    },timeoutMs);
    fileChooserWaiters.set(tabId,waiter);
  });
}

function getNetState(tabId) {
  if (!netState.has(tabId)) {
    netState.set(tabId, { tracked: new Map(), waiters: [], recent: [], seq: 0 });
  }
  return netState.get(tabId);
}


function mediaIdFromRedirectUrl(url=''){
  try{
    const u=new URL(String(url||''));
    if(!u.pathname.includes('media.getMediaUrlRedirect')) return null;
    return String(u.searchParams.get('name')||'').trim()||null;
  }catch{return null;}
}

function isVideoCdnUrl(url=''){
  try{
    const u=new URL(String(url||''));
    return u.protocol==='https:' && (u.hostname==='flow-content.google' || u.hostname.endsWith('.googleusercontent.com'));
  }catch{return false;}
}

function signedVideoUrlLooksUsable(url=''){
  return isVideoCdnUrl(url);
}

function capturedSignedVideoUrl(mediaId){
  const wanted=String(mediaId||'').trim();
  if(!wanted) return null;
  let best=null;
  for(const state of netState.values()){
    const hit=state?.signedVideoUrls?.get(wanted);
    if(hit?.url && (!best || Number(hit.at||0)>Number(best.at||0))) best=hit;
  }
  return best;
}

async function resolveSignedUrlViaFlowTab(mediaId){
  const wanted=String(mediaId||'').trim();
  if(!wanted) throw new Error('mediaId video r?ng');
  const tab=await findOrOpenFlowTab();
  const tabId=tab?.id;
  if(!Number.isInteger(tabId)) throw new Error('Kh?ng c? tab Flow ?? probe mediaId.');
  await injectPage(tabId);
  try{
    const perf=await callPage(tabId,'findSignedVideoResource',[wanted]);
    if(perf?.url && signedVideoUrlLooksUsable(perf.url)) return {url:perf.url,at:Date.now(),source:'flow_page_resource',method:'RESOURCE',status:200};
  }catch{}
  const ownedBefore=debuggerOwnedTabs.has(tabId);
  let attachedHere=false;
  try{
    if(!ownedBefore){
      await debuggerAttachOnce(tabId);
      attachedHere=true;
      getNetState(tabId);
      await chrome.debugger.sendCommand(debuggeeFor(tabId),'Network.enable',{maxTotalBufferSize:12000000,maxResourceBufferSize:6000000});
    }else{
      getNetState(tabId);
      await chrome.debugger.sendCommand(debuggeeFor(tabId),'Network.enable').catch(()=>{});
    }
    let probe=null;
    try{probe=await callPage(tabId,'probeVideoRedirect',[wanted]);}catch(error){probe={ok:false,error:error?.message||String(error)};}
    const until=Date.now()+9000;
    while(Date.now()<until){
      const hit=capturedSignedVideoUrl(wanted);
      if(hit?.url && signedVideoUrlLooksUsable(hit.url)) return {...hit,source:'cdp_media_probe'};
      await sleep(120);
    }
    try{
      const perf=await callPage(tabId,'findSignedVideoResource',[wanted]);
      if(perf?.url && signedVideoUrlLooksUsable(perf.url)) return {url:perf.url,at:Date.now(),source:'flow_page_resource_after_probe',method:'RESOURCE',status:200};
    }catch{}
    throw new Error(`Flow tab probe kh?ng b?t ???c CDN redirect cho mediaId=${wanted}${probe?.error?`: ${probe.error}`:''}`);
  }finally{
    if(attachedHere){
      await chrome.debugger.detach(debuggeeFor(tabId)).catch(()=>{});
      debuggerOwnedTabs.delete(tabId);
      netState.delete(tabId);
    }
  }
}

const VIDEO_SIGNED_URL_CACHE=new Map();

async function resolveVideoSignedUrl(mediaId,{force=false}={}){
  mediaId=String(mediaId||'').trim();
  if(!mediaId) throw new Error('mediaId video r?ng');
  const cached=VIDEO_SIGNED_URL_CACHE.get(mediaId);
  if(!force && cached?.url && Date.now()-Number(cached.at||0)<15*60*1000) return {...cached,source:'extension_cache'};
  const captured=capturedSignedVideoUrl(mediaId);
  if(!force && captured?.url && Date.now()-Number(captured.at||0)<15*60*1000){VIDEO_SIGNED_URL_CACHE.set(mediaId,captured);return {...captured,source:'cdp_redirect_capture'};}
  const redirectUrl=`https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${encodeURIComponent(mediaId)}`;
  let lastError=null;
  for(const spec of [{method:'HEAD',headers:{}},{method:'GET',headers:{Range:'bytes=0-0'}}]){
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(new Error('resolve signed URL timeout')),12000);
    try{
      const response=await fetch(redirectUrl,{method:spec.method,headers:spec.headers,credentials:'include',redirect:'follow',cache:'no-store',signal:controller.signal});
      const finalUrl=String(response.url||'').trim();
      const status=Number(response.status||0);
      try{await response.body?.cancel();}catch{}
      if((response.ok||status===206) && signedVideoUrlLooksUsable(finalUrl)){
        const hit={url:finalUrl,at:Date.now(),status,method:spec.method};
        VIDEO_SIGNED_URL_CACHE.set(mediaId,hit);
        return {...hit,source:'labs_redirect'};
      }
      lastError=new Error(`resolve ${spec.method} HTTP ${status} final=${finalUrl.slice(0,120)}`);
    }catch(error){lastError=error;}
    finally{clearTimeout(timer);}
  }
  try{
    const pageHit=await resolveSignedUrlViaFlowTab(mediaId);
    if(pageHit?.url){VIDEO_SIGNED_URL_CACHE.set(mediaId,pageHit);return pageHit;}
  }catch(error){lastError=error;}
  const capturedAfter=capturedSignedVideoUrl(mediaId);
  if(capturedAfter?.url){VIDEO_SIGNED_URL_CACHE.set(mediaId,capturedAfter);return {...capturedAfter,source:'cdp_redirect_capture_after_probe'};}
  throw new Error(`Kh?ng resolve ???c signed URL cho mediaId=${mediaId}: ${lastError?.message||lastError||'unknown'}`);
}

function classifyUrl(url='') {
  if (url.includes('uploadImage')) return 'IMAGE_UPLOAD';
  if (url.includes('/flowMedia:batchGenerateImages')) return 'IMAGE_CREATE';
  if (/\/v1\/flowWorkflows\//.test(url)) return 'FLOW_WORKFLOW';
  if (url.includes('/v1/video:batchAsyncGenerateVideo') && !url.includes('batchCheckAsyncVideoGenerationStatus')) return 'VIDEO_CREATE';
  if (url.includes('/v1/video:batchCheckAsyncVideoGenerationStatus')) return 'VIDEO_STATUS';
  return null;
}

function parseJsonSafe(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function decodeBody(body, base64Encoded) {
  if (!base64Encoded) return body || '';
  try { return atob(body || ''); } catch { return body || ''; }
}

function compactText(value) {
  return String(value ?? '').replace(/\s+/g,' ').trim();
}

const PROMPT_STRUCTURED_KEYS = new Set(['text', 'prompt', 'prompttext', 'userprompt', 'rawprompt', 'caption', 'description', 'query', 'inputprompt', 'title']);
function collectStructuredTexts(node, out=[]) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) {
    for (const item of node) collectStructuredTexts(item, out);
    return out;
  }
  for (const [key, value] of Object.entries(node)) {
    const lk = String(key || '').toLowerCase();
    if (PROMPT_STRUCTURED_KEYS.has(lk) && typeof value === 'string' && value.trim()) {
      out.push(compactText(value));
    } else if (lk !== 'token' && lk !== 'recaptchacontext' && lk !== 'clienttoken') {
      collectStructuredTexts(value, out);
    }
  }
  return out;
}

function workflowIdFromUrl(url='') {
  const m=String(url).match(/\/v1\/flowWorkflows\/([^/?#]+)/);
  return m?.[1] || null;
}

function projectIdFromFlowUrl(url='') {
  const m=String(url).match(/\/flow\/project\/([^/?#]+)/);
  return m?.[1] || null;
}

function isFlowProjectRootUrl(url='', projectId='') {
  try{
    const u=new URL(String(url||''));
    const wanted=String(projectId||'').trim();
    if(!wanted) return false;
    const path=u.pathname.replace(/\/+$/,'');
    return path===`/fx/tools/flow/project/${wanted}` || path===`/fx/vi/tools/flow/project/${wanted}` || /^\/fx\/[a-z]{2}\/tools\/flow\/project\/[^\/]+$/i.test(path) && path.endsWith(`/${wanted}`);
  }catch{return false;}
}

function isFlowProjectDetailUrl(url='', projectId='') {
  const currentProject=projectIdFromFlowUrl(url);
  if(!currentProject || (projectId && currentProject!==projectId)) return false;
  return !isFlowProjectRootUrl(url,currentProject);
}

async function normalizeProjectRoot(tabId,projectId,reason='normalize project view'){
  assertServerAutomationAllowed('normalize Flow project');
  let tab=await chrome.tabs.get(tabId);
  if(isFlowProjectRootUrl(tab.url||'',projectId)) return tab;
  const target=`https://labs.google/fx/vi/tools/flow/project/${encodeURIComponent(projectId)}`;
  await appendLog(`PROJECT VIEW RECOVERY → ${reason} · ${String(tab.url||'').includes('/edit/')?'đang xem media/edit':'route con'} → project root`,'info');
  await trustedEscape(tabId).catch(()=>{});
  await chrome.tabs.update(tabId,{url:target});
  tab=await waitTabState(tabId,t=>t.status==='complete'&&projectIdFromFlowUrl(t.url||'')===projectId&&isFlowProjectRootUrl(t.url||'',projectId),30000,'thoát media detail về Project');
  await sleep(650);
  await injectPage(tabId);
  return tab;
}

function projectIdFromImageApiUrl(url='') {
  const m=String(url).match(/\/v1\/projects\/([^/?#]+)\/flowMedia:batchGenerateImages/);
  return m?.[1] || null;
}

function parseRequestInfo(kind, url='', postData='') {
  const json=parseJsonSafe(postData);
  const empty={
    parsed:!!json,
    validGeneration:false,
    projectId:projectIdFromImageApiUrl(url),
    projectIds:[],
    batchId:null,
    workflowId:workflowIdFromUrl(url),
    primaryMediaId:null,
    updateMask:null,
    texts:[],
    mediaIds:[],
    referenceMediaIds:[],
    requestCount:0,
    modelKeys:[]
  };
  if(!json) return empty;

  const texts=[...new Set(collectStructuredTexts(json,[]))].slice(0,20);
  const projectIds=[...new Set([
    json?.clientContext?.projectId,
    json?.workflow?.projectId,
    ...(Array.isArray(json?.media)?json.media.map(x=>x?.projectId):[]),
    ...(Array.isArray(json?.requests)?json.requests.map(x=>x?.clientContext?.projectId):[])
  ].filter(Boolean))];
  const projectId=projectIds[0] || empty.projectId || null;
  const batchId=json?.mediaGenerationContext?.batchId || json?.workflow?.metadata?.batchId || null;

  if(kind==='IMAGE_CREATE') {
    const requests=Array.isArray(json?.requests)?json.requests:[];
    return {
      ...empty,parsed:true,projectId,projectIds,batchId,texts,
      validGeneration:requests.length>0,
      requestCount:requests.length,
      modelKeys:requests.map(r=>r?.imageModelName).filter(Boolean)
    };
  }

  if(kind==='VIDEO_CREATE') {
    const requests=Array.isArray(json?.requests)?json.requests:[];
    const referenceMediaIds=[];
    for(const r of requests){
      for(const ref of (Array.isArray(r?.referenceImages)?r.referenceImages:[])){
        if(ref?.mediaId) referenceMediaIds.push(ref.mediaId);
      }
    }
    return {
      ...empty,parsed:true,projectId,projectIds,batchId,texts,
      validGeneration:requests.length>0||texts.length>0,
      requestCount:requests.length,
      modelKeys:requests.map(r=>r?.videoModelKey).filter(Boolean),
      referenceMediaIds:[...new Set(referenceMediaIds)]
    };
  }

  if(kind==='VIDEO_STATUS') {
    const media=Array.isArray(json?.media)?json.media:[];
    const mediaIds=media.map(x=>x?.name).filter(Boolean);
    return {
      ...empty,parsed:true,projectId,projectIds,texts,
      validGeneration:mediaIds.length>0,
      requestCount:media.length,
      mediaIds:[...new Set(mediaIds)]
    };
  }

  if(kind==='FLOW_WORKFLOW') {
    return {
      ...empty,parsed:true,projectId,projectIds,batchId,texts,
      validGeneration:true,
      workflowId:workflowIdFromUrl(url) || json?.workflow?.name || null,
      primaryMediaId:json?.workflow?.metadata?.primaryMediaId || null,
      updateMask:json?.updateMask || null
    };
  }

  return {...empty,parsed:true,projectId,projectIds,batchId,texts};
}


function normalizedPrompt(value='') {
  return compactText(value).toLocaleLowerCase();
}

function requestHasExactPrompt(info, prompt) {
  const wanted = normalizedPrompt(prompt);
  if (!wanted) return true;
  const texts = Array.isArray(info?.texts) ? info.texts : [];
  if (!texts.length) return true; // If no text node was parsed, don't falsely block valid single-dispatcher POST
  // 1. Exact match
  if (texts.some(text => normalizedPrompt(text) === wanted)) return true;
  // 2. Fuzzy / Substring match (in case Flow stripped flags like --ar 9:16 or trimmed whitespace)
  const cleanWanted = wanted.replace(/--[a-z0-9_:\s-]+/gi, '').trim();
  const sample = (cleanWanted || wanted).slice(0, 30);
  if (texts.some(text => {
    const nt = normalizedPrompt(text);
    return nt && (nt.includes(sample) || cleanWanted.includes(nt.slice(0, 30)));
  })) return true;
  // 3. Fallback: single dispatcher serialize ensures this request belongs to our current step
  return true;
}

function generationRequestMatches(meta,{kind,marker,projectId,prompt,referenceMediaId}) {
  if(!meta) return false;
  if(meta.kind!==kind) return false;
  if(Number(meta.seq)<=Number(marker?.seq||0)) return false;
  if(meta.method!=='POST') return false;
  if(!meta.requestInfo?.validGeneration) return false;
  if(!eventProjectMatches(meta,projectId)) return false;
  if(referenceMediaId && !meta.requestInfo?.referenceMediaIds?.includes(referenceMediaId)) return false;
  if(!referenceMediaId && prompt && !requestHasExactPrompt(meta.requestInfo,prompt)) return false;
  return true;
}

async function waitGenerationRequestStart(tabId,{kind,marker,projectId,prompt,referenceMediaId=null,timeoutMs=8000,label='generation request'}) {
  const state=getNetState(tabId);
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const candidates=[];
    for(const [requestId,meta] of state.tracked.entries()) candidates.push({requestId,...meta});
    for(const event of state.recent) candidates.push(event);
    candidates.sort((a,b)=>Number(a.seq||0)-Number(b.seq||0));
    const hit=candidates.find(meta=>generationRequestMatches(meta,{kind,marker,projectId,prompt,referenceMediaId}));
    if(hit) return hit;
    await sleep(40);
  }
  throw new Error(`Không thấy ${label} sau trusted Create. prompt=${JSON.stringify(prompt)} ref=${referenceMediaId||'-'}`);
}

async function waitExactRequestFinished(tabId,requestId,timeoutMs,label) {
  return await waitNet(tabId,event=>{
    if(event.requestId!==requestId) return false;
    if(event.failed) return {error:new Error(`${label} lỗi Network: ${event.errorText||'unknown'}`)};
    if(!event.loadingFinished) return false;
    return {value:event};
  },timeoutMs,label);
}

function isQuotaLikeError(error) {
  const text=String(error?.message||error||'').toUpperCase();
  return text.includes('HTTP 429') || text.includes('RESOURCE_EXHAUSTED') || text.includes('QUOTA') || text.includes('RATE LIMIT') || text.includes('DAILY LIMIT') || text.includes('PUBLIC_ERROR_UNUSUAL_ACTIVITY') || text.includes('RECAPTCHA') || text.includes('HTTP 403');
}

function createSubmitLimiter({maxPerMinute=6,minGapMs=1000}={}) {
  const stamps=[];
  let lastSubmit=0;
  return {
    async waitTurn(label='Create') {
      while(true){
        const now=Date.now();
        while(stamps.length && now-stamps[0]>=60000) stamps.shift();
        let waitMs=Math.max(0,Number(minGapMs||0)-(now-lastSubmit));
        if(Number(maxPerMinute)>0 && stamps.length>=Number(maxPerMinute)){
          waitMs=Math.max(waitMs,60000-(now-stamps[0])+30);
        }
        if(waitMs<=0) break;
        if(waitMs>700) await appendLog(`Rate limiter: chờ ${(waitMs/1000).toFixed(1)}s trước ${label}`,'info');
        await sleep(waitMs);
      }
      const ts=Date.now();
      stamps.push(ts); lastSubmit=ts;
      return ts;
    },
    snapshot(){
      const now=Date.now();
      while(stamps.length && now-stamps[0]>=60000) stamps.shift();
      return {usedLastMinute:stamps.length,maxPerMinute,minGapMs};
    }
  };
}

async function patchJob(index,patch={}) {
  const jobs={...(runtimeCache.jobs||{})};
  jobs[index]={...(jobs[index]||{}),...patch};
  runtimeCache.jobs=jobs;
  let done=0,errors=0;
  for(const job of Object.values(jobs)){
    if(job?.done) done++;
    if(job?.error) errors++;
  }
  runtimeCache.metrics={...(runtimeCache.metrics||{}),done,errors,total:Object.keys(jobs).length};
  await persistRuntime();
}

async function setMetrics(patch={}) {
  runtimeCache.metrics={...(runtimeCache.metrics||{}),...patch};
  await persistRuntime();
}

function countFinishedJobs(total) {
  let done=0,errors=0;
  for(let i=0;i<total;i++){
    const j=runtimeCache.jobs?.[i]||{};
    if(j.done) done++;
    if(j.error) errors++;
  }
  return {done,errors};
}

function createNetworkMarker(tabId) {
  const state=getNetState(tabId);
  return {seq:state.seq,at:Date.now()};
}

function eventAfterMarker(event, marker) {
  return !!event && !!marker && Number(event.seq)>Number(marker.seq);
}

function normalizeWaitVerdict(verdict,event) {
  if(!verdict) return null;
  if(verdict?.error) return {error:verdict.error};
  return {value:verdict?.value ?? event};
}

function emitNetEvent(tabId,event) {
  const state=getNetState(tabId);
  state.recent.push(event);
  if(state.recent.length>160) state.recent.splice(0,state.recent.length-160);

  for(const waiter of [...state.waiters]){
    let verdict=false;
    try{verdict=waiter.predicate(event);}catch(error){
      clearTimeout(waiter.timer);
      state.waiters=state.waiters.filter(x=>x!==waiter);
      waiter.reject(error);
      continue;
    }
    const normalized=normalizeWaitVerdict(verdict,event);
    if(!normalized) continue;
    clearTimeout(waiter.timer);
    state.waiters=state.waiters.filter(x=>x!==waiter);
    if(normalized.error) waiter.reject(normalized.error instanceof Error?normalized.error:new Error(String(normalized.error)));
    else waiter.resolve(normalized.value);
  }
}

function waitNet(tabId,predicate,timeoutMs,label) {
  const state=getNetState(tabId);

  for(const event of state.recent){
    let verdict=false;
    try{verdict=predicate(event);}catch(error){return Promise.reject(error);}
    const normalized=normalizeWaitVerdict(verdict,event);
    if(normalized) return normalized.error?Promise.reject(normalized.error):Promise.resolve(normalized.value);
  }

  return new Promise((resolve,reject)=>{
    const waiter={predicate,resolve,reject,timer:null};
    waiter.timer=setTimeout(()=>{
      state.waiters=state.waiters.filter(x=>x!==waiter);
      reject(new Error(`Timeout Network: ${label}`));
    },timeoutMs);
    state.waiters.push(waiter);
  });
}

async function getResponseBodyReliable(tabId,requestId) {
  let lastError=null;
  for(const delay of [0,60,180,420,900]){
    if(delay) await sleep(delay);
    try{
      const response=await chrome.debugger.sendCommand(debuggeeFor(tabId),'Network.getResponseBody',{requestId});
      return {ok:true,body:response?.body||'',base64Encoded:Boolean(response?.base64Encoded),error:null};
    }catch(error){lastError=error;}
  }
  return {ok:false,body:'',base64Encoded:false,error:lastError?.message||String(lastError||'unknown')};
}

chrome.debugger.onEvent.addListener((source,method,params)=>{
  const tabId=source.tabId;
  if(!Number.isInteger(tabId)||!netState.has(tabId)) return;
  const state=getNetState(tabId);

  if(method==='Page.fileChooserOpened'){
    const waiter=fileChooserWaiters.get(tabId);
    if(waiter){
      clearTimeout(waiter.timer);fileChooserWaiters.delete(tabId);waiter.resolve(params||{});
    }
    return;
  }

  if(method==='Network.requestWillBeSent'){
    const kind=classifyUrl(params.request?.url||'');
    if(!kind) return;
    const seq=++state.seq;
    const url=params.request.url;
    const methodName=String(params.request?.method||'').toUpperCase();
    const postData=params.request?.postData||'';
    state.tracked.set(params.requestId,{
      kind,url,seq,method:methodName,postData,redirectMediaId:mediaIdFromRedirectUrl(url),
      requestInfo:parseRequestInfo(kind,url,postData),
      requestSeen:true,startTs:Date.now()
    });
    return;
  }

  if(method==='Network.responseReceived'){
    const responseUrl=String(params.response?.url||'');
    const entry=state.tracked.get(params.requestId);
    const redirectMediaId = entry?.redirectMediaId || mediaIdFromRedirectUrl(entry?.url||'');
    if(redirectMediaId && signedVideoUrlLooksUsable(responseUrl)){
      state.signedVideoUrls.set(redirectMediaId,{url:responseUrl,at:Date.now(),status:Number(params.response?.status||0),method:entry?.method||null});
    }
    const kind=classifyUrl(responseUrl);
    if(!kind) return;
    // STRICT: never manufacture an eligible generation event from response-only data.
    // We require requestWillBeSent so HTTP method + request body are known.
    if(!entry) return;
    state.tracked.set(params.requestId,{
      ...entry,status:params.response.status,responseSeen:true,mimeType:params.response.mimeType||null
    });
    return;
  }

  if(method==='Network.loadingFailed'){
    const meta=state.tracked.get(params.requestId);
    if(!meta) return;
    state.tracked.delete(params.requestId);
    emitNetEvent(tabId,{
      tabId,requestId:params.requestId,...meta,failed:true,loadingFinished:false,
      errorText:params.errorText||'Network.loadingFailed',body:'',json:null,bodyAvailable:false,bodyError:params.errorText||'Network.loadingFailed'
    });
    return;
  }

  if(method!=='Network.loadingFinished') return;
  const meta=state.tracked.get(params.requestId);
  if(!meta) return;
  state.tracked.delete(params.requestId);

  (async()=>{
    const response=await getResponseBodyReliable(tabId,params.requestId);
    const decoded=decodeBody(response.body,response.base64Encoded);
    const json=parseJsonSafe(decoded);
    emitNetEvent(tabId,{
      tabId,requestId:params.requestId,...meta,failed:false,loadingFinished:true,
      body:decoded,json,bodyAvailable:response.ok&&decoded.length>0,bodyError:response.ok?null:response.error
    });
  })();
});

const debuggerOwnedTabs = new Set();
const debuggerDetachReason = new Map();

function debuggerErrorText(error){
  return String(error?.message||error||'unknown');
}
function debuggerLooksDetached(error){
  return /not attached|Debugger is not attached|No target with given id|Detached/i.test(debuggerErrorText(error));
}
function debuggerLooksBusy(error){
  return /another debugger|already attached|target is already being debugged|Cannot attach to this target/i.test(debuggerErrorText(error));
}
async function debuggerAttachOnce(tabId){
  const d=debuggeeFor(tabId);
  try{
    await chrome.debugger.attach(d,'1.3');
    debuggerOwnedTabs.add(tabId);
    debuggerDetachReason.delete(tabId);
    return d;
  }catch(error){
    // A previous attach from THIS service-worker run can survive a very short race.
    // Verify ownership by trying one harmless command before declaring a conflict.
    if(debuggerOwnedTabs.has(tabId)){
      try{
        await chrome.debugger.sendCommand(d,'Runtime.enable');
        return d;
      }catch{}
    }
    if(debuggerLooksBusy(error)){
      throw new Error(`Không attach được Chrome Debugger vào tab Flow ${tabId}. Có debugger khác đang giữ tab (thường là DevTools hoặc một bản Flow Wardrobe Studio cũ). Hãy đóng DevTools của tab Flow và tắt extension Flow cũ rồi thử lại. Chi tiết: ${debuggerErrorText(error)}`);
    }
    throw error;
  }
}

chrome.debugger.onDetach.addListener((source,reason)=>{
  const tabId=source.tabId;
  if(!Number.isInteger(tabId)) return;
  debuggerOwnedTabs.delete(tabId);
  debuggerDetachReason.set(tabId,String(reason||'unknown'));
  const state=netState.get(tabId);
  if(state){
    for(const waiter of state.waiters){
      clearTimeout(waiter.timer);
      waiter.reject(new Error(`Debugger tab ${tabId} bị detach: ${reason}`));
    }
    netState.delete(tabId);
  }
  appendLog(`Chrome Debugger DETACHED · tab=${tabId} · reason=${reason}. Nếu đang mở DevTools trên tab Flow, hãy đóng DevTools.`, 'error').catch(()=>{});
});

async function attachWorkerDebugger(tabId){
  assertServerAutomationAllowed('attach debugger');
  const d=debuggeeFor(tabId);
  let lastError=null;
  // Chrome/Edge can report attach success and then briefly lose the session when
  // a tab is activating/reloading. Retry the whole attach+probe sequence.
  for(let attempt=1;attempt<=3;attempt++){
    try{
      if(debuggerOwnedTabs.has(tabId)){
        try{await chrome.debugger.detach(d);}catch{}
        debuggerOwnedTabs.delete(tabId);
        await sleep(120);
      }
      await debuggerAttachOnce(tabId);
      getNetState(tabId);
      // Probe first. This catches the exact "Debugger is not attached" race
      // before the automation starts touching Flow UI.
      await chrome.debugger.sendCommand(d,'Runtime.enable');
      await chrome.debugger.sendCommand(d,'Network.enable',{maxTotalBufferSize:12000000,maxResourceBufferSize:6000000});
      await chrome.debugger.sendCommand(d,'Page.enable').catch(()=>{});
      await chrome.debugger.sendCommand(d,'Page.setInterceptFileChooserDialog',{enabled:true}).catch(()=>{});
      await appendLog(`Chrome Debugger READY · tab=${tabId} · attempt=${attempt}`,'info');
      return d;
    }catch(error){
      lastError=error;
      debuggerOwnedTabs.delete(tabId);
      try{await chrome.debugger.detach(d);}catch{}
      const reason=debuggerDetachReason.get(tabId);
      const text=debuggerErrorText(error);
      if(debuggerLooksBusy(error)){
        throw error;
      }
      if(attempt<3 && (debuggerLooksDetached(error)||reason)){
        await appendLog(`Chrome Debugger attach/probe lỗi lần ${attempt}/3 · ${text}${reason?` · detach=${reason}`:''} → thử lại`,'info');
        await sleep(350*attempt);
        continue;
      }
      break;
    }
  }
  const reason=debuggerDetachReason.get(tabId);
  throw new Error(`Chrome Debugger không giữ được tab Flow ${tabId}${reason?` (detach=${reason})`:''}. Đóng DevTools trên tab Flow, tắt các bản Flow Wardrobe Studio cũ, rồi chạy lại. Lỗi cuối: ${debuggerErrorText(lastError)}`);
}

async function detachWorkerDebugger(tabId){
  const chooser=fileChooserWaiters.get(tabId);
  if(chooser){clearTimeout(chooser.timer);chooser.reject(new Error('Worker kết thúc.'));fileChooserWaiters.delete(tabId);}
  const state=netState.get(tabId);
  if(state){
    for(const waiter of state.waiters){
      clearTimeout(waiter.timer);
      waiter.reject(new Error('Worker kết thúc.'));
    }
    netState.delete(tabId);
  }
  await chrome.debugger.detach(debuggeeFor(tabId)).catch(()=>{});
}

async function withDebuggerOperationRetry(tabId,label,fn,maxAttempts=DEBUGGER_OPERATION_RETRY_MAX){
  let last=null;
  for(let attempt=1;attempt<=maxAttempts;attempt++){
    assertServerAutomationAllowed(label);
    try{
      if(!debuggerOwnedTabs.has(tabId)) await attachWorkerDebugger(tabId);
      return await fn(attempt);
    }catch(error){
      last=error;
      if(!debuggerLooksDetached(error) || attempt>=maxAttempts) throw error;
      await appendLog(`↻ DEBUGGER SELF-HEAL ${label} ${attempt}/${maxAttempts} · ${debuggerErrorText(error)} → attach lại`, 'info');
      debuggerOwnedTabs.delete(tabId);
      try{await chrome.debugger.detach(debuggeeFor(tabId));}catch{}
      await sleep(180*attempt);
      await attachWorkerDebugger(tabId);
    }
  }
  throw last||new Error(`Debugger self-heal fail · ${label}`);
}

async function trustedClickPoint(tabId,point){
  assertServerAutomationAllowed('trusted click');
  if(!point||!Number.isFinite(point.x)||!Number.isFinite(point.y)) throw new Error('Tọa độ click không hợp lệ.');
  return await withDebuggerOperationRetry(tabId,'trusted click',async()=>{
    const d=debuggeeFor(tabId);
    await chrome.debugger.sendCommand(d,'Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x,y:point.y});
    await chrome.debugger.sendCommand(d,'Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',buttons:1,clickCount:1});
    await sleep(65);
    await chrome.debugger.sendCommand(d,'Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',buttons:0,clickCount:1});
    return true;
  });
}

async function trustedCreateClick(tabId){
  const point=await callPage(tabId,'getCreatePoint');
  await trustedClickPoint(tabId,point);
}


// v14.5.6: hard-reset the Flow composer before every scene/stage. This prevents
// reference chips from the previous IMAGE/VIDEO and stale prompt text from being
// submitted together with the next job.
async function clearComposerBeforeCreate(tabId,tag=''){
  await callPage(tabId,'closeSettings',[]).catch(()=>{});
  const pickerOpen=await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false);
  if(pickerOpen) await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
  await callPage(tabId,'clearPrompt',[]).catch(error=>{throw new Error(`${tag} Clear prompt: ${error?.message||error}`);});

  let removed=0;
  // Fast purge pass
  const bulk=await callPage(tabId,'removeAllComposerMedia',[]).catch(()=>({removed:0}));
  removed += Number(bulk?.removed || 0);

  for(let i=0;i<60;i++){
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0}));
    if(!Number(state?.count||0)) break;
    const point=await callPage(tabId,'getComposerMediaRemovePoint',[]).catch(()=>null);
    if(point){
      await trustedClickPoint(tabId,point).catch(()=>{});
      removed++;
      await sleep(100);
    } else {
      await callPage(tabId,'removeComposerMediaFirst',[]).catch(()=>{});
      await sleep(100);
    }
  }
  const finalState=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0}));
  if(Number(finalState?.count||0)>0){
    // If still cannot clear via UI, do a soft reload of the flow project
    await reloadAndNormalizeFlow(tabId, `${tag} purge stubborn ${finalState.count} media chips`).catch(()=>{});
  }
  await callPage(tabId,'clearPrompt',[]);
  await appendLog(`TAB ${tag} COMPOSER CLEAN → refs=0 · prompt=empty${removed?` · removed=${removed}`:''}`,'info');
  return {ok:true,removed};
}

const FLOW_TOOL_URL='https://labs.google/fx/tools/flow';

async function waitTabState(tabId,predicate,timeoutMs=30000,label='tab state'){
  const started=Date.now();
  let last=null;
  while(Date.now()-started<timeoutMs){
    last=await chrome.tabs.get(tabId);
    try{if(predicate(last)) return last;}catch{}
    await sleep(200);
  }
  throw new Error(`Timeout chờ ${label}. url=${last?.url||'unknown'} status=${last?.status||'unknown'}`);
}

async function ensureFlowToolLoaded(tabId){
  assertServerAutomationAllowed('navigate Flow');
  let tab=await chrome.tabs.get(tabId);
  const url=String(tab.url||'');
  if(!isFlowToolTabUrl(url)){
    await appendLog(`Tab chưa ở Flow → mở ${FLOW_TOOL_URL}`,'info');
    await chrome.tabs.update(tabId,{url:FLOW_TOOL_URL});
    tab=await waitTabState(tabId,t=>isFlowToolTabUrl(t.url||'')&&t.status==='complete',30000,'Flow tải xong');
  }else if(tab.status!=='complete'){
    tab=await waitTabState(tabId,t=>t.status==='complete',30000,'Flow tải xong');
  }
  return tab;
}

async function ensureProjectAndAllMedia(tabId){
  let tab=await chrome.tabs.get(tabId);
  let projectId=projectIdFromFlowUrl(tab.url||'');
  if(projectId && !isFlowProjectRootUrl(tab.url||'',projectId)){
    tab=await normalizeProjectRoot(tabId,projectId,'pre-inject child route');
  }else{
    await injectPage(tabId);
    tab=await chrome.tabs.get(tabId);
    projectId=projectIdFromFlowUrl(tab.url||'');
  }

  if(!projectId){
    await appendLog('Không ở trong Project → tự tạo Project mới bằng UI Flow.','info');
    let createPoint=null,lastCreateError=null;
    for(let attempt=1;attempt<=12;attempt++){
      try{createPoint=await callPage(tabId,'getCreateProjectPoint',[]);break;}
      catch(error){lastCreateError=error;await sleep(500);}
    }
    if(!createPoint) throw lastCreateError||new Error('Không tìm thấy nút tạo Project.');
    await appendLog(`CREATE PROJECT → ${createPoint?.label||'Create Project'}`,'info');
    await trustedClickPoint(tabId,createPoint);
    tab=await waitTabState(tabId,t=>Boolean(projectIdFromFlowUrl(t.url||'')),30000,'Flow tạo Project');
    if(tab.status!=='complete') tab=await waitTabState(tabId,t=>t.status==='complete',30000,'Project tải xong');
    projectId=projectIdFromFlowUrl(tab.url||'');
    if(!projectId) throw new Error(`Flow đã chuyển trang nhưng vẫn không lấy được projectId: ${tab.url}`);
    await sleep(450);
    await injectPage(tabId);
    await appendLog(`PROJECT READY → ${projectId}`,'success');
  }else{
    await appendLog(`PROJECT READY → ${projectId}`,'success');
  }

  // v14.5.7: a user may have opened an image/video detail URL under /edit/<mediaId>.
  // That child route does NOT expose the Project-level All Media control. Always return to the
  // exact project root first; do not use startsWith(projectRoot) because /edit/... also matches.
  await normalizeProjectRoot(tabId,projectId,'job bắt đầu');

  // Normalize the project view before touching Settings/Prompt/Asset Picker.
  // Clicking All Media is intentionally idempotent: if already selected, Flow stays there.
  let allPoint=null,lastError=null;
  for(let attempt=1;attempt<=4;attempt++){
    try{
      allPoint=await callPage(tabId,'getAllMediaPoint',[]);
      break;
    }catch(error){lastError=error;await sleep(500);}
  }
  if(!allPoint){
    await appendLog(`ALL MEDIA RECOVERY → chưa thấy nút sau lần đầu · về project root và thử lại`,'info');
    await normalizeProjectRoot(tabId,projectId,'All Media missing').catch(()=>{});
    await chrome.tabs.reload(tabId).catch(()=>{});
    await waitTabState(tabId,t=>t.status==='complete',30000,'reload Project').catch(()=>{});
    await sleep(650);
    await injectPage(tabId);
    for(let attempt=1;attempt<=8;attempt++){
      try{allPoint=await callPage(tabId,'getAllMediaPoint',[]);break;}
      catch(error){lastError=error;await sleep(400);}
    }
  }
  if(!allPoint) throw lastError||new Error('Không tìm thấy All Media sau khi đã thoát media detail và reload Project.');
  await trustedClickPoint(tabId,allPoint);
  await sleep(450);
  await appendLog('VIEW READY → All Media','success');
  return projectId;
}


async function reloadAndNormalizeFlow(tabId, reason='UI recovery', expectedProjectId=null) {
  const beforeTab=await chrome.tabs.get(tabId).catch(()=>null);
  const targetProjectId=String(expectedProjectId||projectIdFromFlowUrl(beforeTab?.url||'')||'').trim()||null;
  await appendLog(`FLOW RECOVERY → ${reason} · F5 / về đúng Project / All Media`, 'info');
  await trustedEscape(tabId).catch(()=>{});
  await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
  await chrome.tabs.reload(tabId).catch(()=>{});
  await waitTabState(tabId,t=>t.status==='complete',30000,'Flow reload');
  await sleep(700);
  await injectPage(tabId);
  let tab=await chrome.tabs.get(tabId);
  let currentProjectId=projectIdFromFlowUrl(tab.url||'');
  if(targetProjectId && currentProjectId!==targetProjectId){
    const targetUrl=`${FLOW_TOOL_URL}/project/${encodeURIComponent(targetProjectId)}`;
    await appendLog(`FLOW RECOVERY → trỏ lại project cũ ${targetProjectId}`, 'info');
    await chrome.tabs.update(tabId,{url:targetUrl});
    tab=await waitTabState(tabId,t=>t.status==='complete'&&projectIdFromFlowUrl(t.url||'')===targetProjectId,30000,'mở lại project cũ');
    await sleep(650);
    await injectPage(tabId);
    currentProjectId=targetProjectId;
  }
  if(!currentProjectId){
    await ensureFlowToolLoaded(tabId);
    return await ensureProjectAndAllMedia(tabId);
  }
  let point=null;
  for(let i=0;i<8;i++){
    try{point=await callPage(tabId,'getAllMediaPoint',[]);break;}catch{await sleep(350);}
  }
  if(point){await trustedClickPoint(tabId,point).catch(()=>{});await sleep(450);}
  const current=projectIdFromFlowUrl((await chrome.tabs.get(tabId)).url||'');
  await appendLog(`FLOW RECOVERY READY → project=${current||'unknown'}`, 'success');
  return current;
}

async function ensureAssetPickerOpenTrusted(tabId){
  if(await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false)) return true;

  const failures=[];
  try{
    if(await callPage(tabId,'openAssetPicker',[])) return true;
    failures.push({fallback:'page-openAssetPicker-first',reason:'not-open'});
  }catch(error){
    failures.push({fallback:'page-openAssetPicker-first',error:error?.message||String(error)});
  }

  for(let attempt=1;attempt<=3;attempt++){
    try{
      const point=await callPage(tabId,'getAddMediaPoint',[]);
      await trustedClickPoint(tabId,point);
      if(await waitPageCondition(tabId,'isAssetPickerOpen',true,2500)) return true;
      failures.push({attempt,reason:'picker-not-open',point});
    }catch(error){
      failures.push({attempt,error:error?.message||String(error)});
    }
    try{
      if(await callPage(tabId,'openAssetPicker',[])) return true;
      failures.push({attempt,fallback:'page-openAssetPicker',reason:'not-open'});
    }catch(error){
      failures.push({attempt,fallback:'page-openAssetPicker',error:error?.message||String(error)});
    }
    await trustedEscape(tabId).catch(()=>{});
    await sleep(250);
  }
  throw new Error(`Asset Picker kh?ng m? ???c: ${JSON.stringify(failures)}`);
}

async function ensureImagesTabTrusted(tabId){
  await ensureAssetPickerOpenTrusted(tabId);
  if(await callPage(tabId,'isImagesTabSelected',[]).catch(()=>false)) return true;

  const failures=[];
  for(let attempt=1;attempt<=3;attempt++){
    try{
      const point=await callPage(tabId,'getImagesTabPoint',[]);
      if(point?.selected) return true;
      await trustedClickPoint(tabId,point);
      if(await waitPageCondition(tabId,'isImagesTabSelected',true,2500)) return true;
      failures.push({attempt,reason:'images-tab-not-selected',point});
    }catch(error){
      failures.push({attempt,error:error?.message||String(error)});
    }
    await sleep(200);
  }
  throw new Error(`Không chọn được tab Images bằng CDP trusted click: ${JSON.stringify(failures)}`);
}


// ======================== v14 Local image upload bridge ========================
function attrsToObject(attrs=[]){
  const out={};
  for(let i=0;i+1<attrs.length;i+=2) out[String(attrs[i]).toLowerCase()]=String(attrs[i+1]);
  return out;
}

async function listFileInputs(tabId){
  const d=debuggeeFor(tabId);
  await chrome.debugger.sendCommand(d,'DOM.enable').catch(()=>{});
  const doc=await chrome.debugger.sendCommand(d,'DOM.getDocument',{depth:-1,pierce:true});
  const rootId=doc?.root?.nodeId;
  if(!rootId) return [];
  const found=await chrome.debugger.sendCommand(d,'DOM.querySelectorAll',{nodeId:rootId,selector:'input[type="file"]'}).catch(()=>({nodeIds:[]}));
  const rows=[];
  for(const nodeId of (found?.nodeIds||[])){
    const detail=await chrome.debugger.sendCommand(d,'DOM.describeNode',{nodeId,depth:0,pierce:true}).catch(()=>null);
    const attrs=attrsToObject(detail?.node?.attributes||[]);
    rows.push({nodeId,attrs,nodeName:detail?.node?.nodeName||'INPUT'});
  }
  return rows;
}

function scoreImageFileInput(row){
  const a=row?.attrs||{};
  const accept=String(a.accept||'').toLowerCase();
  const name=String(a.name||'').toLowerCase();
  const id=String(a.id||'').toLowerCase();
  const aria=String(a['aria-label']||'').toLowerCase();
  let score=0;
  if(accept.includes('image/')) score+=100;
  if(accept.includes('.png')||accept.includes('.jpg')||accept.includes('.jpeg')||accept.includes('.webp')) score+=70;
  if(!accept) score+=10;
  if(/image|photo|media|asset|upload/.test(`${name} ${id} ${aria}`)) score+=30;
  if(accept.includes('video/') && !accept.includes('image/')) score-=120;
  return score;
}

async function findBestImageFileInput(tabId){
  const rows=await listFileInputs(tabId);
  return rows.map(row=>({...row,score:scoreImageFileInput(row)})).sort((a,b)=>b.score-a.score)[0]||null;
}

async function revealImageFileInput(tabId){
  let hit=await findBestImageFileInput(tabId);
  if(hit && hit.score>0) return hit;
  await ensureAssetPickerOpenTrusted(tabId);
  try{
    const point=await callPage(tabId,'getUploadImagePoint',[]);
    await appendLog(`UPLOAD UI → ${point?.label||'Upload Image'}`,'info');
    await trustedClickPoint(tabId,point);
  }catch(error){
    // Some Flow builds already keep a hidden input in the picker; retry the DOM scan
    // before declaring failure so minor menu differences do not break local upload.
    await appendLog(`UPLOAD UI fallback: ${error?.message||String(error)}`,'info');
  }
  for(let i=0;i<30;i++){
    hit=await findBestImageFileInput(tabId);
    if(hit && hit.score>0) return hit;
    await sleep(100);
  }
  throw new Error('Không tìm thấy input[type=file] nhận ảnh trong Flow sau khi mở Upload Image.');
}

async function _setImageFileInputsOnce(tabId,localPaths,allowRecovery=true){
  const paths=(Array.isArray(localPaths)?localPaths:[localPaths]).map(x=>String(x||'').trim()).filter(Boolean);
  if(!paths.length) throw new Error('Đường dẫn ảnh upload đang rỗng.');
  const d=debuggeeFor(tabId);
  let hit=await findBestImageFileInput(tabId);
  if(hit && hit.score>0){
    try{
      await chrome.debugger.sendCommand(d,'DOM.setFileInputFiles',{files:paths,nodeId:hit.nodeId});
      return {ok:true,paths,nodeId:hit.nodeId,accept:hit.attrs?.accept||'',mode:'existing-input',multiple:paths.length>1};
    }catch(error){
      if(paths.length===1) throw error;
      await appendLog(`UPLOAD MULTI existing-input lỗi → thử chooser: ${error?.message||String(error)}`,'info');
    }
  }

  try{
    await ensureAssetPickerOpenTrusted(tabId);
    await chrome.debugger.sendCommand(d,'Page.enable').catch(()=>{});
    await chrome.debugger.sendCommand(d,'Page.setInterceptFileChooserDialog',{enabled:true}).catch(()=>{});
    const chooserPromise=waitFileChooser(tabId,5000).catch(()=>null);
    const point=await callPage(tabId,'getUploadImagePoint',[]);
    await appendLog(`UPLOAD UI → ${point?.label||'Upload Image'} · ${paths.length} file`,'info');
    await trustedClickPoint(tabId,point);
    const chooser=await chooserPromise;
    if(chooser?.backendNodeId){
      await chrome.debugger.sendCommand(d,'DOM.setFileInputFiles',{files:paths,backendNodeId:chooser.backendNodeId});
      return {ok:true,paths,backendNodeId:chooser.backendNodeId,mode:'intercepted-chooser',multiple:paths.length>1};
    }
    for(let i=0;i<35;i++){
      hit=await findBestImageFileInput(tabId);
      if(hit && hit.score>0){
        await chrome.debugger.sendCommand(d,'DOM.setFileInputFiles',{files:paths,nodeId:hit.nodeId});
        return {ok:true,paths,nodeId:hit.nodeId,accept:hit.attrs?.accept||'',mode:'lazy-input',multiple:paths.length>1};
      }
      await sleep(100);
    }
  }catch(error){
    if(!allowRecovery) throw error;
    await appendLog(`UPLOAD UI không sẵn sàng → tự F5/recover: ${error?.message||String(error)}`,'info');
    await reloadAndNormalizeFlow(tabId,'không thấy chỗ upload ảnh');
    const retried=await _setImageFileInputsOnce(tabId,paths,false);
    return {...retried,recovered:true};
  }
  if(allowRecovery){
    await reloadAndNormalizeFlow(tabId,'không tìm thấy input/chooser upload ảnh');
    const retried=await _setImageFileInputsOnce(tabId,paths,false);
    return {...retried,recovered:true};
  }
  throw new Error('Không tìm thấy file input/chooser để upload ảnh vào Flow sau recovery.');
}

async function setImageFileInputs(tabId,localPaths,allowRecovery=true){
  return await withDebuggerOperationRetry(
    tabId,'upload file input',
    ()=>_setImageFileInputsOnce(tabId,localPaths,allowRecovery),
    DEBUGGER_OPERATION_RETRY_MAX
  );
}

async function setImageFileInput(tabId,localPath){
  return await setImageFileInputs(tabId,[localPath]);
}

function normalizePossibleMediaId(value){
  if(value==null) return null;
  if(typeof value==='object'){
    for(const key of ['mediaId','id','name']){
      const hit=normalizePossibleMediaId(value?.[key]);
      if(hit) return hit;
    }
    return null;
  }
  const text=String(value).trim();
  if(!text) return null;
  const m=text.match(/(?:media|flowMedia|assets?)\/([^/?#]+)$/i);
  return m?.[1]||text;
}

function uploadMediaIdFromResponse(json){
  if(!json || typeof json!=='object') return null;
  const direct=[
    json?.mediaId,
    json?.media?.mediaId,
    json?.image?.mediaId,
    json?.asset?.mediaId,
    json?.uploadedMedia?.mediaId,
    json?.result?.mediaId,
    json?.media?.name,
    json?.uploadedMedia?.name
  ];
  for(const value of direct){
    const id=normalizePossibleMediaId(value);
    if(id) return id;
  }
  const queue=[json];
  const seen=new Set();
  while(queue.length){
    const node=queue.shift();
    if(!node || typeof node!=='object' || seen.has(node)) continue;
    seen.add(node);
    for(const [key,value] of Object.entries(node)){
      const k=String(key).toLowerCase();
      if((k==='mediaid'||k==='media_id') && value){
        const id=normalizePossibleMediaId(value); if(id) return id;
      }
      if(value && typeof value==='object') queue.push(value);
    }
  }
  return null;
}

async function waitUploadImageAfterMarker(tabId,marker,timeoutMs=90000){
  return await waitNet(tabId,event=>{
    if(event?.kind!=='IMAGE_UPLOAD' || !eventAfterMarker(event,marker) || event?.method!=='POST') return false;
    if(event.failed) return {error:new Error(`Upload ảnh lỗi Network: ${event.errorText||'unknown'}`)};
    if(!event.loadingFinished) return false;
    const status=Number(event.status||0);
    if(status && (status<200||status>=300)) return {error:new Error(`Upload ảnh HTTP ${status}`)};
    return {value:{event,mediaId:uploadMediaIdFromResponse(event.json)}};
  },timeoutMs,'IMAGE UPLOAD');
}

async function findUploadedMediaByName(tabId,name,timeoutMs=5000,exactOnly=false){
  const q=String(name||'').trim();
  await ensureAssetPickerOpenTrusted(tabId);
  await ensureImagesTabTrusted(tabId);
  await callPage(tabId,'setAssetSearch',[q]);
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const items=await callPage(tabId,'listSearchedImages',[]).catch(()=>[]);
    const usable=(items||[]).filter(x=>x?.validImage && x?.mediaId && x?.srcMediaId===x?.mediaId);
    const exact=usable.find(x=>String(x?.title||'').trim()===q);
    const hit=exact||(!exactOnly?usable[0]:null);
    if(hit?.mediaId) return hit;
    await sleep(250);
  }
  return null;
}

async function validateReusableMedia(tabId,expectedMediaId,names=[],timeoutMs=4500){
  const wanted=String(expectedMediaId||'').trim();
  if(!wanted) return {valid:false,reason:'empty-mediaId'};
  await ensureAssetPickerOpenTrusted(tabId);
  await ensureImagesTabTrusted(tabId);
  const queries=[...new Set((Array.isArray(names)?names:[names]).map(x=>String(x||'').trim()).filter(Boolean))].slice(0,2);
  if(!queries.length) queries.push('');
  const phaseMs=Math.max(900,Math.floor(timeoutMs/queries.length));

  for(const query of queries){
    await callPage(tabId,'setAssetSearch',[query]);
    const until=Date.now()+phaseMs;
    while(Date.now()<until){
      const status=await callPage(tabId,'getAssetMediaStatus',[wanted]).catch(()=>null);
      if(status?.valid) return {...status,query};
      if(status?.found && (
        status.reason==='mediaId-without-image' ||
        status.reason==='image-mediaId-mismatch' ||
        status.reason==='broken-image' ||
        status.reason==='image-without-exact-mediaId'
      )){
        return {...status,query};
      }
      if(query){
        const items=await callPage(tabId,'listSearchedImages',[]).catch(()=>[]);
        const sameTitle=(items||[]).find(x=>String(x?.title||'').trim()===query && x?.validImage);
        if(sameTitle?.mediaId && String(sameTitle.mediaId)!==wanted){
          return {valid:false,found:true,reason:'filename-has-different-mediaId',wanted,srcMediaId:String(sameTitle.mediaId),query,title:sameTitle.title};
        }
      }
      await sleep(220);
    }
  }
  return {valid:false,found:false,reason:'exact-media-not-visible',wanted};
}

async function waitComposerRefIncrease(tabId,beforeCount,timeoutMs=8000){
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>null);
    if(Number(state?.count||0)>Number(beforeCount||0)) return state;
    await sleep(180);
  }
  return null;
}

function composerReferenceCandidate(state,wanted,beforeState){
  const items=Array.isArray(state?.items)?state.items:[]; const exactId=String(wanted||'').trim();
  if(exactId&&!exactId.startsWith('composer:')){const exact=items.find(x=>String(x?.mediaId||'')===exactId);if(exact)return exact;}
  const beforeIds=new Set((beforeState?.items||[]).map(x=>String(x?.mediaId||'')).filter(Boolean));
  const added=items.filter(x=>{const mid=String(x?.mediaId||'');return mid&&!beforeIds.has(mid);}); if(added.length)return added.at(-1);
  if(Number(state?.count||0)>Number(beforeState?.count||0)&&items.length)return items.at(-1); return null;
}

async function waitComposerReferenceReady(tabId,{mediaId=null,beforeState=null,timeoutMs=REFERENCE_CARD_WAIT_MS,stableMs=REFERENCE_CARD_STABLE_MS}={}){
  const baseline=beforeState||{count:0,items:[]}; const started=Date.now(); let stableSince=0,stableKey='',lastState=null;
  while(Date.now()-started<timeoutMs){
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>null); lastState=state;
    const item=composerReferenceCandidate(state,mediaId,baseline);
    if(item){
      if(item.hasError===true||item.error===true)return {ok:false,error:true,reason:'REFERENCE_CARD_ERROR',item,state};
      const key=`${item.index}:${String(item.mediaId||'')}`; if(key!==stableKey){stableKey=key;stableSince=Date.now();}
      if(Date.now()-stableSince>=stableMs)return {ok:true,error:false,item,state};
    }else{stableKey='';stableSince=0;}
    await sleep(120);
  }
  return {ok:false,error:false,reason:'REFERENCE_CARD_NOT_STABLE',state:lastState,item:composerReferenceCandidate(lastState,mediaId,baseline)};
}

async function removeComposerReferenceCard(tabId,item,tag=''){
  if(!item)return false; const before=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0,items:[]}));
  const selector={mediaId:String(item.mediaId||'')||undefined,index:Number.isInteger(item.index)?Number(item.index):undefined,errorOnly:Boolean(item.hasError||item.error)};
  const point=await callPage(tabId,'getComposerMediaRemovePointFor',[selector]).catch(()=>null);
  if(!point)throw new Error(`${tag} Không lấy được nút xóa reference media=${selector.mediaId||'-'} index=${selector.index??'-'}.`);
  await trustedClickPoint(tabId,point);
  const started=Date.now(); while(Date.now()-started<3000){
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:Number(before?.count||0),items:[]}));
    const sameId=selector.mediaId&&(state?.items||[]).some(x=>String(x?.mediaId||'')===selector.mediaId);
    if(Number(state?.count||0)<Number(before?.count||0)||(selector.mediaId&&!sameId))return true; await sleep(100);
  }
  throw new Error(`${tag} Xóa reference không thành công · media=${selector.mediaId||'-'}.`);
}

async function ensureComposerReferencePackReady(tabId,tag,expectedCount,timeoutMs=7000){
  const started=Date.now();let stableSince=0,last=null;
  while(Date.now()-started<timeoutMs){
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0,errorCount:0,items:[]})); last=state;
    const errors=(state?.items||[]).filter(x=>x?.hasError===true||x?.error===true); if(errors.length)return {ok:false,reason:'REFERENCE_PACK_ERROR',state,errors};
    if(Number(state?.count||0)>=Number(expectedCount||0)){if(!stableSince)stableSince=Date.now();if(Date.now()-stableSince>=REFERENCE_CARD_STABLE_MS)return {ok:true,state,errors:[]};}else stableSince=0;
    await sleep(120);
  }
  return {ok:false,reason:'REFERENCE_PACK_NOT_READY',state:last,errors:(last?.items||[]).filter(x=>x?.hasError===true||x?.error===true)};
}

async function cleanupComposerErrorCards(tabId,tag){
  let removed=0; for(let guard=0;guard<12;guard++){
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({items:[]})); const error=(state?.items||[]).find(x=>x?.hasError===true||x?.error===true); if(!error)break;
    await appendLog(`TAB ${tag} REF ERROR CARD → remove media=${error.mediaId||'-'} index=${error.index}`,'error'); await removeComposerReferenceCard(tabId,error,tag); removed++;
  } return removed;
}

async function tryReuseExistingLibraryAsset(tabId,input,tag){
  const names=[
    String(input?.fileName||'').trim(),
    String(input?.name||'').trim(),
    String(input?.path||'').trim().split(/[\\/]/).pop()||'',
    String(input?.title||'').trim()
  ].filter(Boolean);
  for(const q of [...new Set(names)].slice(0,2)){
    const found=await findUploadedMediaByName(tabId,q,2500,true).catch(()=>null);
    if(found?.mediaId&&found?.validImage){
      await appendLog(`TAB ${tag} REF existing-valid-image → ${found.mediaId} (${q})`,'info');
      return {mediaId:String(found.mediaId),title:String(found.title||q),name:q};
    }
  }
  return null;
}

async function uploadAndAttachLocalImage(tabId,input,options,tag){
  await persistentAssetCacheReady;
  const path=String(input?.path||'').trim();
  const name=String(input?.name||'').trim()||path.split(/[\\/]/).pop()||'image';
  const role=String(input?.role||'reference');
  const suppliedMediaId=String(input?.mediaId||input?.media_id||'').trim();
  const suppliedTitle=String(input?.title||name||'').trim()||name;
  const cache=options.assetCache instanceof Map?options.assetCache:GLOBAL_ASSET_CACHE;
  const persisted=path?(GLOBAL_ASSET_CACHE.get(path)||cache?.get(path)||null):null;

  const candidates=[];
  if(persisted?.mediaId) candidates.push({source:'path-cache',row:persisted});
  if(suppliedMediaId && !candidates.some(x=>String(x.row?.mediaId)===suppliedMediaId)){
    candidates.push({source:'server-mediaId',row:{mediaId:suppliedMediaId,title:suppliedTitle,name,role,path}});
  }

  for(const candidate of candidates){
    const expected=String(candidate.row.mediaId||'');
    const check=await validateReusableMedia(tabId,expected,[candidate.row.title||suppliedTitle,name],4500).catch(error=>({valid:false,reason:error?.message||String(error)}));
    if(check?.valid){
      await appendLog(`TAB ${tag} REF ${role} ${candidate.source} VALID → ${expected}`,'info');
      try{
        const attached=await trustedAttachIngredient(tabId,[candidate.row.title||suppliedTitle,name],expected,12000,tag);
        if(attached?.ok){
          const saved={mediaId:expected,title:attached.title||candidate.row.title||suppliedTitle,name,role,path};
          if(path){cache?.set(path,saved);await rememberAssetMedia(path,saved);}
          return {...saved,attached:true,cacheHit:true,cacheSource:candidate.source};
        }
      }catch(error){
        await appendLog(`TAB ${tag} REF ${candidate.source} attach fail → upload lại · ${error?.message||error}`,'info');
      }
    }else{
      const reconciledId=String(check?.srcMediaId||'').trim();
      if(
        reconciledId &&
        reconciledId!==expected &&
        ['filename-has-different-mediaId','image-mediaId-mismatch'].includes(String(check?.reason||''))
      ){
        await appendLog(`TAB ${tag} REF MEDIAID RECONCILE ${role} · stale=${expected} → actual=${reconciledId} · KHÔNG upload lại`,'info');
        try{
          const attached=await trustedAttachIngredient(
            tabId,
            [candidate.row.title||suppliedTitle,name],
            reconciledId,
            24000,
            tag
          );
          if(attached?.ok){
            const saved={mediaId:reconciledId,title:attached.title||candidate.row.title||suppliedTitle,name,role,path};
            if(path){cache?.set(path,saved);await rememberAssetMedia(path,saved);}
            await appendLog(`TAB ${tag} REF MEDIAID RECONCILE OK ${role} → ${reconciledId}`,'success');
            return {...saved,attached:true,cacheHit:true,cacheSource:`${candidate.source}:reconciled`,mediaIdReconciled:true};
          }
        }catch(error){
          await appendLog(`TAB ${tag} REF reconcile attach chưa được → mới fallback upload · ${error?.message||error}`,'info');
        }
      }

      await appendLog(`TAB ${tag} REF ${candidate.source} STALE → upload lại · mediaId=${expected} · reason=${check?.reason||'unknown'}${check?.srcMediaId?` · imageMediaId=${check.srcMediaId}`:''}`,'info');
    }
    if(path){
      cache?.delete(path);
      await forgetAssetMedia(path);
    }
    await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
  }

  if(!candidates.length){
    const reused=await tryReuseExistingLibraryAsset(tabId,input,tag).catch(()=>null);
    if(reused?.mediaId){
      try{
        const attached=await trustedAttachIngredient(tabId,[reused.title||suppliedTitle,name],reused.mediaId,12000,tag);
        if(attached?.ok){
          const saved={mediaId:reused.mediaId,title:attached.title||reused.title||suppliedTitle,name,role,path};
          if(path){cache?.set(path,saved);await rememberAssetMedia(path,saved);}
          return {...saved,attached:true,cacheHit:true,reusedByName:true};
        }
      }catch{}
      await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    }
  }

  if(!path) throw new Error(`${tag} REF ${role}: mediaId stale/missing nhưng không có file local để upload lại.`);

  let lastError=null;
  for(let uploadAttempt=1;uploadAttempt<=2;uploadAttempt++){
    await appendLog(`TAB ${tag} UPLOAD REF ${role}: ${name} · attempt ${uploadAttempt}/2`,'info');
    const beforeState=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0}));
    const beforeCount=Number(beforeState?.count||0);
    await ensureAssetPickerOpenTrusted(tabId);
    const marker=createNetworkMarker(tabId);
    const uploadAction=await setImageFileInput(tabId,path);

    let upload=null;
    try{upload=await waitUploadImageAfterMarker(tabId,marker,45000);}catch(error){
      lastError=error;
      await appendLog(`TAB ${tag} upload response chưa đọc được (${name}): ${error?.message||String(error)} → verify composer`,'info');
    }
    let mediaId=upload?.mediaId||null;

    const composerAttached=await waitComposerRefIncrease(tabId,beforeCount,7000);
    if(composerAttached){
      await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
      const health=await waitComposerReferenceReady(tabId,{mediaId:mediaId||null,beforeState,timeoutMs:REFERENCE_CARD_WAIT_MS,stableMs:REFERENCE_CARD_STABLE_MS});
      if(health?.ok){
        const actualId=String(health.item?.mediaId||mediaId||`composer:${Date.now()}:${name}`); const saved={mediaId:actualId,title:name,name,role,path};
        if(actualId&&!actualId.startsWith('composer:')){cache?.set(path,saved);await rememberAssetMedia(path,saved);}
        await appendLog(`TAB ${tag} REF RE-UPLOAD READY ${role} → ${actualId}`,'success'); return {...saved,attached:true,cacheHit:false,composerDirect:true,reuploaded:true,uiRecovered:Boolean(uploadAction?.recovered),referenceReady:true};
      }
      if(health?.item){await appendLog(`TAB ${tag} REF RE-UPLOAD INVALID · ${health.reason} · media=${health.item.mediaId||mediaId||'-'} → remove`,'error');await removeComposerReferenceCard(tabId,health.item,tag).catch(()=>{});}
      lastError=new Error(`${health?.reason||'REFERENCE_CARD_NOT_READY'} sau upload ${name}`);
    }

    if(mediaId){
      // Upload response is authoritative enough to TRY ATTACH first.
      // All Media indexing can lag tens of seconds behind the upload response.
      try{
        await appendLog(`TAB ${tag} REF UPLOAD MEDIAID → attach trực tiếp ${mediaId} · không chờ pre-validate picker`,'info');
        const attached=await trustedAttachIngredient(tabId,[name],mediaId,30000,tag);
        if(attached?.ok){
          const actualId=String(attached.mediaId||mediaId);
          const saved={mediaId:actualId,title:attached.title||name,name,role,path};
          cache?.set(path,saved);await rememberAssetMedia(path,saved);
          await appendLog(`TAB ${tag} REF RE-UPLOAD OK ${role} → ${actualId}`,'success');
          return {...saved,attached:true,cacheHit:false,reuploaded:true,uiRecovered:Boolean(uploadAction?.recovered),directUploadMediaId:true};
        }
      }catch(error){
        lastError=error;
        await appendLog(`TAB ${tag} REF mediaId ${mediaId} chưa index/attach được → reconcile filename trước khi upload lại · ${error?.message||error}`,'info');
      }

      // The upload may have produced a healthy composer card a little later.
      const lateHealth=await waitComposerReferenceReady(tabId,{
        mediaId:null,
        beforeState,
        timeoutMs:5000,
        stableMs:REFERENCE_CARD_STABLE_MS
      }).catch(()=>null);
      if(lateHealth?.ok){
        const actualId=String(lateHealth.item?.mediaId||mediaId||`composer:${Date.now()}:${name}`);
        const saved={mediaId:actualId,title:name,name,role,path};
        if(actualId&&!actualId.startsWith('composer:')){cache?.set(path,saved);await rememberAssetMedia(path,saved);}
        await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
        await appendLog(`TAB ${tag} REF LATE COMPOSER READY ${role} → ${actualId} · bỏ qua picker index`,'success');
        return {...saved,attached:true,cacheHit:false,reuploaded:true,composerLateReady:true,referenceReady:true};
      }

      // Flow sometimes exposes the same uploaded file under a different mediaId.
      const found=await findUploadedMediaByName(tabId,name,12000,true).catch(()=>null);
      if(found?.mediaId&&found?.validImage){
        const actualId=String(found.mediaId);
        await appendLog(`TAB ${tag} REF FILENAME RECONCILE ${role} · uploadId=${mediaId} → visibleId=${actualId}`,'info');
        try{
          const attached=await trustedAttachIngredient(tabId,[found.title||name,name],actualId,24000,tag);
          if(attached?.ok){
            const saved={mediaId:actualId,title:attached.title||found.title||name,name,role,path};
            cache?.set(path,saved);await rememberAssetMedia(path,saved);
            await appendLog(`TAB ${tag} REF FILENAME RECONCILE OK ${role} → ${actualId}`,'success');
            return {...saved,attached:true,cacheHit:false,reuploaded:true,mediaIdReconciled:true,uiRecovered:Boolean(uploadAction?.recovered)};
          }
        }catch(error){
          lastError=error;
        }
      }

      const check=await validateReusableMedia(tabId,mediaId,[name],2500).catch(error=>({valid:false,reason:error?.message||String(error)}));
      lastError=new Error(
        `upload media chưa attach được · uploadId=${mediaId} · reason=${check?.reason||lastError?.message||'index-delay'}`
      );
    }else{
      const found=await findUploadedMediaByName(tabId,name,5000,true).catch(()=>null);
      if(found?.mediaId&&found?.validImage){
        try{
          const attached=await trustedAttachIngredient(tabId,[name],found.mediaId,12000,tag);
          if(attached?.ok){
            const saved={mediaId:found.mediaId,title:attached.title||found.title||name,name,role,path};
            cache?.set(path,saved);await rememberAssetMedia(path,saved);
            await appendLog(`TAB ${tag} REF RE-UPLOAD OK ${role} → ${found.mediaId}`,'success');
            return {...saved,attached:true,cacheHit:false,reuploaded:true,uiRecovered:Boolean(uploadAction?.recovered)};
          }
        }catch(error){lastError=error;}
      }
    }

    await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    if(uploadAttempt<2){
      await appendLog(`TAB ${tag} REF chưa attach sau direct-ID + composer + filename reconcile → upload lại lần cuối.`,'info');
      await sleep(500);
    }
  }

  throw new Error(`${tag} Upload/attach ${name} thất bại sau 2 lần: ${lastError?.message||lastError||'media chưa usable'}`);
}

async function recentUploadEventsAfterMarker(tabId,marker,expected,timeoutMs=90000){
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const rows=getNetState(tabId).recent
      .filter(e=>e?.kind==='IMAGE_UPLOAD'&&e?.method==='POST'&&eventAfterMarker(e,marker)&&e?.loadingFinished&&!e?.failed)
      .sort((a,b)=>(a.seq||0)-(b.seq||0));
    const good=rows.filter(e=>{const st=Number(e.status||0);return !st||(st>=200&&st<300);});
    if(good.length>=expected) return good.slice(0,expected);
    await sleep(180);
  }
  return getNetState(tabId).recent.filter(e=>e?.kind==='IMAGE_UPLOAD'&&eventAfterMarker(e,marker)&&e?.loadingFinished).sort((a,b)=>(a.seq||0)-(b.seq||0));
}

async function findUploadedMediaByCandidates(tabId,input,timeoutMs=30000){
  const path=String(input?.path||'').trim();
  const base=path.split(/[\\/]/).pop()||'';
  const names=[base,String(input?.name||'').trim()].filter(Boolean);
  for(const name of [...new Set(names)]){
    const found=await findUploadedMediaByName(tabId,name,timeoutMs,true).catch(()=>null);
    if(found?.mediaId) return found;
  }
  return null;
}

function inputRefKey(input){
  const path=String(input?.path||'').trim();
  if(path) return `path:${path}`;
  const media=String(input?.mediaId||input?.media_id||'').trim();
  return `media:${media}:${String(input?.role||'reference')}`;
}

async function uploadAndAttachLocalImagesBatch(tabId,inputs,options,tag){
  const out=[];
  for(let i=0;i<inputs.length;i++){
    const attached=await uploadAndAttachLocalImage(tabId,inputs[i],options,tag); out.push(attached);
    await appendLog(`TAB ${tag} REF SEQUENTIAL READY ${i+1}/${inputs.length} · ${attached.role||'reference'}:${attached.mediaId||'-'}`,'success'); await sleep(220);
  }
  for(let round=1;round<=REFERENCE_CARD_RETRY_MAX;round++){
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({items:[]})); const errors=(state?.items||[]).filter(x=>x?.hasError===true||x?.error===true); if(!errors.length)break;
    await appendLog(`TAB ${tag} REFERENCE PACK repair ${round}/${REFERENCE_CARD_RETRY_MAX} · error=${errors.length}`,'error');
    for(const error of errors){
      let index=out.findIndex(x=>String(x?.mediaId||'')===String(error?.mediaId||'')); if(index<0&&Number.isInteger(error?.index)&&error.index<inputs.length)index=Number(error.index);
      if(index<0||!inputs[index])throw new Error(`REFERENCE_PACK_ERROR không map được card lỗi media=${error?.mediaId||'-'} index=${error?.index}`);
      await removeComposerReferenceCard(tabId,error,tag); await sleep(350); out[index]=await uploadAndAttachLocalImage(tabId,inputs[index],options,tag); await sleep(REFERENCE_CARD_STABLE_MS);
    }
  }
  const final=await ensureComposerReferencePackReady(tabId,tag,inputs.length,8000);
  if(!final?.ok){
    const actual=Number(final?.state?.validCount||final?.state?.count||0);
    if(actual>=1){
      await appendLog(`TAB ${tag} REFERENCE PACK PARTIAL OK ${actual}/${inputs.length} ? Flow layout ch? gi? ???c m?t s? ref`, 'info');
      return out;
    }
    const errors=(final?.errors||[]).map(x=>`${x.mediaId||'?'}@${x.index}`).join(',');
    if(final?.errors?.length)await cleanupComposerErrorCards(tabId,tag).catch(()=>{});
    throw new Error(`REFERENCE_PACK_NOT_READY ? expected=${inputs.length} actual=${final?.state?.count||0} errors=${errors||'-'}`);
  }
  await appendLog(`TAB ${tag} REFERENCE PACK READY ${final.state.validCount||final.state.count}/${inputs.length} · error=0`,'success'); return out;
}

async function ensureSceneImageInputs(tabId,record,options,stage='image'){
  const allInputs=Array.isArray(record?.pair?.inputImages)?record.pair.inputImages:[];
  const inputs=stage==='video'?allInputs.filter(x=>x?.videoReference!==false):allInputs;
  if(!inputs.length) return [];
  const tag=`[${record.index+1}]`;
  if(stage==='video'&&inputs.length!==allInputs.length){
    await appendLog(`TAB ${tag} VIDEO REF FILTER → ${inputs.length}/${allInputs.length} refs; image-only refs giữ trong scene image`,'info');
  }
  const attached=await uploadAndAttachLocalImagesBatch(tabId,inputs,options,tag);
  await patchJob(record.index,{inputRefs:attached.map(x=>({role:x.role,name:x.name,mediaId:x.mediaId,cacheHit:x.cacheHit,batchUpload:Boolean(x.batchUpload),referenceReady:true})),referencePackReady:true,referencePackCount:attached.length});
  return attached;
}
// ============================================================================

function assetSearchText(value){
  // Search exactly the title/prompt that Flow stored. Do not append, strip,
  // translate, or otherwise rewrite punctuation here.
  return String(value??'').trim();
}

function assetSearchCandidatesForRecord(record){
  // The IMAGE response's generatedImage.prompt is not guaranteed to be the same
  // string that Asset Picker indexes. Never trust text as the identity of an image.
  // mediaId is the identity; text is only an optional filter fallback.
  const values=[
    record?.selectedImage?.workflowTitle,
    record?.selectedImage?.title,
    record?.pair?.imagePrompt
  ].map(assetSearchText).filter(Boolean);
  return [...new Set(values)];
}

async function _trustedAttachIngredientOnce(tabId,searchCandidates,mediaId,timeoutMs=60000){
  const wanted=String(mediaId||'').trim();
  if(!wanted) throw new Error('Ingredient mediaId đang rỗng.');
  const candidates=[...new Set((Array.isArray(searchCandidates)?searchCandidates:[searchCandidates]).map(assetSearchText).filter(Boolean))];

  const before=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0,items:[]}));
  const beforeCount=Number(before?.count||0);

  const composerHasWanted=async()=>{
    const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0,items:[]}));
    const items=Array.isArray(state?.items)?state.items:[];
    const exact=items.some(x=>String(x?.mediaId||'')===wanted);
    return {state,exact,increased:Number(state?.count||0)>beforeCount};
  };

  const verifyCommitted=async(timeout=5000)=>{
    const started=Date.now();
    while(Date.now()-started<timeout){
      const v=await composerHasWanted();
      if(v.exact || v.increased){
        if(await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false)){
          await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
        }
        return v;
      }
      await sleep(120);
    }
    return null;
  };

  await ensureAssetPickerOpenTrusted(tabId);
  await ensureImagesTabTrusted(tabId);

  // Exact mediaId first. Search text is only fallback for virtualized/stale result lists.
  const phases=[{query:'',label:'NO_SEARCH',budgetMs:Math.min(15000,Math.max(5000,Math.floor(timeoutMs*0.25)))}];
  const remaining=Math.max(5000,timeoutMs-phases[0].budgetMs);
  const perCandidate=candidates.length?Math.max(5000,Math.floor(remaining/candidates.length)):remaining;
  for(const query of candidates) phases.push({query,label:'TEXT_FALLBACK',budgetMs:perCandidate});

  const started=Date.now(),tried=[];
  for(const phase of phases){
    if(Date.now()-started>=timeoutMs) break;
    const phaseEnd=Math.min(started+timeoutMs,Date.now()+phase.budgetMs);
    const query=phase.query;
    tried.push(query||'<no-search>');
    await callPage(tabId,'setAssetSearch',[query]);

    let lastRefresh=0,lastHardRefresh=Date.now();
    while(Date.now()<phaseEnd && Date.now()-started<timeoutMs){
      if(!(await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false))){
        await ensureAssetPickerOpenTrusted(tabId);
        await ensureImagesTabTrusted(tabId);
        await callPage(tabId,'setAssetSearch',[query]);
      }

      const point=await callPage(tabId,'getAssetOptionPoint',[wanted]).catch(()=>null);
      if(point?.mediaId===wanted){
        // Flow current UI is two-step: select asset, then press "Add to prompt".
        // aria-selected is NOT authoritative; the commit button's selectedCount is.
        const pageSelect=await callPage(tabId,'clickAssetOptionByMediaId',[wanted]).catch(error=>({ok:false,error:error?.message||String(error)}));
        if(!pageSelect?.ok) await trustedClickPoint(tabId,point);
        await sleep(320);

        // Some builds attach immediately.
        let committed=await verifyCommitted(900);
        if(committed){
          return {ok:true,mediaId:wanted,title:point.title||'',method:'asset-direct',searchUsed:query,searchMode:phase.label};
        }

        let state=await callPage(tabId,'getAssetPickerSelectionState',[wanted]).catch(()=>null);
        if(state?.open && state?.confirmPoint && (Number(state?.selectedCount||state?.confirmPoint?.selectedCount||0)>0 || state?.selected)){
          await trustedClickPoint(tabId,state.confirmPoint);
          committed=await verifyCommitted(5500);
          if(committed){
            return {ok:true,mediaId:wanted,title:point.title||'',method:'select+add-to-prompt',searchUsed:query,searchMode:phase.label};
          }
        }else{
          // React state can land a fraction later; read once more, but never click the asset twice.
          await sleep(350);
          state=await callPage(tabId,'getAssetPickerSelectionState',[wanted]).catch(()=>state);
          if(state?.open && state?.confirmPoint && Number(state?.selectedCount||state?.confirmPoint?.selectedCount||0)>0){
            await trustedClickPoint(tabId,state.confirmPoint);
            committed=await verifyCommitted(5500);
            if(committed){
              return {ok:true,mediaId:wanted,title:point.title||'',method:'select+delayed-add-to-prompt',searchUsed:query,searchMode:phase.label};
            }
          }
        }

        const commitErr=new Error(
          `FLOW_UI_ASSET_COMMIT_FAILED · mediaId=${wanted}`+
          ` · selected=${Boolean(state?.selected)}`+
          ` · selectedCount=${Number(state?.selectedCount||state?.confirmPoint?.selectedCount||0)}`+
          ` · confirm=${JSON.stringify(state?.confirmPoint||null)}`+
          ` · composerBefore=${beforeCount}`
        );
        throw commitErr;
      }

      if(Date.now()-lastRefresh>2500){
        lastRefresh=Date.now();
        await ensureImagesTabTrusted(tabId);
        await callPage(tabId,'setAssetSearch',[query]);
      }
      if(Date.now()-lastHardRefresh>8000){
        lastHardRefresh=Date.now();
        await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
        await sleep(220);
        await ensureAssetPickerOpenTrusted(tabId);
        await ensureImagesTabTrusted(tabId);
        await callPage(tabId,'setAssetSearch',[query]);
      }
      await sleep(350);
    }
  }

  throw new Error(`FLOW_UI_ASSET_NOT_FOUND · mediaId=${wanted} · ${Math.round(timeoutMs/1000)}s · tried=${tried.map(x=>JSON.stringify(x)).join(' → ')}`);
}

async function trustedAttachIngredient(tabId,searchCandidates,mediaId,timeoutMs=60000,tag=''){
  const wanted=String(mediaId||'').trim(); let lastError=null;
  for(let retry=0;retry<=REFERENCE_CARD_RETRY_MAX;retry++){
    const beforeState=await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0,items:[]}));
    try{
      const attached=await _trustedAttachIngredientOnce(tabId,searchCandidates,wanted,timeoutMs);
      const health=await waitComposerReferenceReady(tabId,{mediaId:wanted,beforeState,timeoutMs:REFERENCE_CARD_WAIT_MS,stableMs:REFERENCE_CARD_STABLE_MS});
      if(health?.ok){const actualId=String(health.item?.mediaId||wanted);await appendLog(`TAB ${tag} REF READY ${retry?`retry=${retry}/${REFERENCE_CARD_RETRY_MAX} · `:''}${actualId||'-'}`,'success');return {...attached,mediaId:actualId||wanted,referenceReady:true,referenceRetryCount:retry};}
      if(health?.item){await appendLog(`TAB ${tag} REF INVALID · ${health.reason} · media=${health.item.mediaId||wanted||'-'} · cleanup`,'error');await removeComposerReferenceCard(tabId,health.item,tag);}
      lastError=new Error(`${health?.reason||'REFERENCE_CARD_NOT_READY'} · mediaId=${health?.item?.mediaId||wanted||'-'}`);
    }catch(error){
      lastError=error; const state=await callPage(tabId,'getComposerMediaState',[]).catch(()=>null); const bad=(state?.items||[]).find(x=>x?.hasError===true||x?.error===true);
      if(bad){await appendLog(`TAB ${tag} REF attach sinh ERROR card · media=${bad.mediaId||wanted||'-'} · cleanup`,'error');await removeComposerReferenceCard(tabId,bad,tag).catch(()=>{});}
    }
    if(retry<REFERENCE_CARD_RETRY_MAX){await appendLog(`TAB ${tag} ♻️ REF RETRY ${retry+1}/${REFERENCE_CARD_RETRY_MAX} · add lại đúng reference`,'info');await sleep(450*(retry+1));}
  }
  throw new Error(`REFERENCE_ATTACH_FAILED sau ${REFERENCE_CARD_RETRY_MAX} retry · mediaId=${wanted||'-'} · ${lastError?.message||lastError||'unknown'}`);
}

async function trustedEscape(tabId){
  return await withDebuggerOperationRetry(tabId,'Escape key',async()=>{
    const d=debuggeeFor(tabId);
    await chrome.debugger.sendCommand(d,'Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
    await chrome.debugger.sendCommand(d,'Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
    return true;
  });
}

async function waitPageCondition(tabId,method,expected,timeout=3000){
  const started=Date.now();
  while(Date.now()-started<timeout){
    const value=await callPage(tabId,method,[]).catch(()=>null);
    if(Boolean(value)===Boolean(expected)) return true;
    await sleep(100);
  }
  return false;
}

async function recoverAgentModeForSettings(tabId){
  const state=await callPage(tabId,'getAgentModeState',[]).catch(()=>({found:false,pressed:false,point:null}));

  if(!state?.found){
    return {handled:false,reason:'agent-toggle-not-found'};
  }

  // HARD RULE: when Agent is OFF, never click it.
  if(state.pressed!==true){
    await appendLog('AGENT GUARD · Agent aria-pressed=false → KHÔNG CLICK · tiếp tục tìm Settings','info');
    return {handled:false,reason:'agent-off-do-not-click'};
  }

  if(!state.point){
    throw new Error('AGENT MODE đang ON nhưng không lấy được tọa độ để tắt.');
  }

  // Only when Agent is already ON are we allowed to click once to deactivate it.
  await appendLog('AGENT RECOVERY · Settings không thấy + Agent aria-pressed=true → tắt Agent 1 lần rồi retry Settings','info');
  await trustedClickPoint(tabId,state.point);

  const started=Date.now();
  while(Date.now()-started<3500){
    const next=await callPage(tabId,'getAgentModeState',[]).catch(()=>null);
    if(!next?.found || next?.pressed!==true){
      await appendLog('AGENT RECOVERY OK · Agent OFF · retry Settings','success');
      await sleep(180);
      return {handled:true,deactivated:true};
    }
    await sleep(120);
  }
  throw new Error('AGENT RECOVERY FAILED · Agent vẫn aria-pressed=true sau click tắt.');
}


async function ensureSettingsOpenTrusted(tabId){
  if(await callPage(tabId,'isSettingsOpen',[]).catch(()=>false)) return true;

  if(await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false)){
    await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    await trustedEscape(tabId).catch(()=>{});
    await sleep(180);
  }

  const failures=[];
  let agentRecoveryUsed=false;

  for(let attempt=1;attempt<=3;attempt++){
    try{
      const point=await callPage(tabId,'getSettingsTriggerPoint',[]);
      if(String(point?.text||'').trim().toLowerCase()==='agent'){
        throw new Error('AGENT_GUARD: getSettingsTriggerPoint trả Agent · BLOCK CLICK');
      }
      await trustedClickPoint(tabId,point);
      if(await waitPageCondition(tabId,'isSettingsOpen',true,2500)) return true;
      failures.push({attempt,reason:'trusted-click-no-open',point});
    }catch(error){
      const text=error?.message||String(error);
      failures.push({attempt,error:text});
      if(!agentRecoveryUsed && /settings|cài đặt|không tìm thấy nút/i.test(text)){
        try{
          const recovery=await recoverAgentModeForSettings(tabId);
          if(recovery?.handled){
            agentRecoveryUsed=true;
            continue;
          }
        }catch(agentError){
          failures.push({attempt,agentRecovery:agentError?.message||String(agentError)});
        }
      }
    }
    await trustedEscape(tabId).catch(()=>{});
    await sleep(180);
  }

  try{
    await callPage(tabId,'openSettings',[]);
    if(await waitPageCondition(tabId,'isSettingsOpen',true,2500)) return true;
  }catch(error){
    const text=error?.message||String(error);
    failures.push({fallback:'page-openSettings',error:text});
    if(!agentRecoveryUsed && /settings|cài đặt|không tìm thấy nút/i.test(text)){
      try{
        const recovery=await recoverAgentModeForSettings(tabId);
        if(recovery?.handled){
          agentRecoveryUsed=true;
          const point=await callPage(tabId,'getSettingsTriggerPoint',[]);
          if(String(point?.text||'').trim().toLowerCase()==='agent'){
            throw new Error('AGENT_GUARD: retry Settings vẫn trỏ Agent');
          }
          await trustedClickPoint(tabId,point);
          if(await waitPageCondition(tabId,'isSettingsOpen',true,2500)) return true;
        }
      }catch(agentError){
        failures.push({fallback:'agent-recovery',error:agentError?.message||String(agentError)});
      }
    }
  }

  throw new Error(`Không mở được Settings sau Agent-guard + trusted + page fallback: ${JSON.stringify(failures)}`);
}

async function ensureSettingsClosedTrusted(tabId){
  if(!(await callPage(tabId,'isSettingsOpen',[]).catch(()=>false))) return true;
  try{
    const point=await callPage(tabId,'getSettingsTriggerPoint',[]);
    await trustedClickPoint(tabId,point);
    if(await waitPageCondition(tabId,'isSettingsOpen',false,1800)) return true;
  }catch{}
  await trustedEscape(tabId).catch(()=>{});
  if(await waitPageCondition(tabId,'isSettingsOpen',false,1800)) return true;
  try{
    await callPage(tabId,'closeSettings',[]);
    if(await waitPageCondition(tabId,'isSettingsOpen',false,1800)) return true;
  }catch{}
  await appendLog('SETTINGS CLOSE WARN -> continue after close race.','warn').catch(()=>{});
  return true;
}

async function trustedSelectModel(tabId,kind,requestedModel){
  const requested=String(requestedModel||'').trim();
  if(!requested||requested.toUpperCase()==='NONE') return {ok:true,skipped:true};
  await ensureSettingsOpenTrusted(tabId);
  let trigger=await callPage(tabId,'getModelTriggerPoint',[kind,requested]);
  if(trigger?.alreadySelected) return {ok:true,changed:false,current:trigger.current};
  await trustedClickPoint(tabId,trigger);
  await sleep(250);
  const option=await callPage(tabId,'getModelOptionPoint',[kind,requested]);
  await trustedClickPoint(tabId,option);
  await sleep(350);
  await ensureSettingsOpenTrusted(tabId);
  trigger=await callPage(tabId,'getModelTriggerPoint',[kind,requested]);
  if(!trigger?.alreadySelected) throw new Error(`Model chưa đổi đúng sau trusted click. cần=${requested}, hiện=${trigger?.current||'unknown'}`);
  return {ok:true,changed:true,current:trigger.current};
}

async function configureStageSettings(tabId,options,maxAttempts=3){
  const modelKind=String(options?.modelKind||options?.type||'').toUpperCase();
  const model=String(options?.model||'').trim();
  const failures=[];
  for(let attempt=1;attempt<=maxAttempts;attempt++){
    try{
      await ensureSettingsOpenTrusted(tabId);
      await callPage(tabId,'applySettings',[options]);
      await ensureSettingsOpenTrusted(tabId);
      if(model&&model.toUpperCase()!=='NONE') await trustedSelectModel(tabId,modelKind,model);
      await ensureSettingsOpenTrusted(tabId);
      const verification=await callPage(tabId,'verifyStageSettings',[options]);
      if(verification?.ok){
        await ensureSettingsClosedTrusted(tabId);
        return {ok:true,attempt,verification};
      }
      failures.push({attempt,failed:verification?.failed||['unknown'],current:verification?.current||{}});
    }catch(error){
      const text=error?.message||String(error);
      failures.push({attempt,error:text});
      if(attempt<maxAttempts && /không mở được Settings|không tìm thấy nút Settings|page fallback|agent/i.test(text)){
        const agent=await callPage(tabId,'getAgentModeState',[]).catch(()=>null);
        if(agent?.pressed===true){
          await recoverAgentModeForSettings(tabId).catch(()=>{});
        }else{
          await reloadAndNormalizeFlow(tabId,`Settings không phản hồi · attempt ${attempt}`).catch(()=>{});
        }
      }
    }
    await ensureSettingsClosedTrusted(tabId).catch(async()=>{await trustedEscape(tabId).catch(()=>{});});
    await sleep(250);
  }
  throw new Error(`SETTINGS VERIFY FAILED sau ${maxAttempts} lần: ${JSON.stringify(failures)}`);
}

function uniqueByMediaId(items){
  const out=[];
  const byId=new Map();
  for(const item of items||[]){
    const id=String(item?.mediaId||'').trim();
    if(!id) continue;
    const existing=byId.get(id);
    if(!existing){
      const copy={...item,mediaId:id};
      byId.set(id,copy); out.push(copy); continue;
    }
    // Merge metadata from workflow/media variants instead of dropping later data.
    if(!existing.title&&item?.title) existing.title=item.title;
    if(!existing.workflowId&&item?.workflowId) existing.workflowId=item.workflowId;
    if(!existing.url&&item?.url) existing.url=item.url;
    if(item?.source==='workflow.primaryMediaId'&&item?.title) existing.workflowTitle=item.title;
  }
  return out;
}

function imageMediaFromResponse(json){
  const found=[];
  const media=Array.isArray(json?.media)?json.media:[];
  for(const item of media){
    const generated=item?.image?.generatedImage||{};
    const id=item?.name||generated?.mediaId||null;
    if(id) found.push({
      mediaId:id,
      workflowId:item?.workflowId||generated?.workflowId||null,
      title:generated?.prompt||item?.mediaMetadata?.mediaTitle||null,
      url:generated?.fifeUrl||null,
      source:'media'
    });
  }
  const workflows=Array.isArray(json?.workflows)?json.workflows:[];
  for(const wf of workflows){
    const id=wf?.metadata?.primaryMediaId||null;
    if(id) found.push({mediaId:id,workflowId:wf?.name||null,title:wf?.metadata?.displayName||null,url:null,source:'workflow.primaryMediaId'});
  }
  // Model/API variants may return explicit image arrays.
  for(const key of ['images','generatedImages','outputs']){
    const arr=Array.isArray(json?.[key])?json[key]:[];
    for(const item of arr){
      const id=item?.mediaId||item?.name||item?.primaryMediaId||null;
      if(id) found.push({mediaId:id,workflowId:item?.workflowId||null,title:item?.prompt||item?.displayName||null,url:item?.fifeUrl||item?.url||null,source:key});
    }
  }
  return uniqueByMediaId(found);
}

function workflowIdsFromResponse(json){
  const ids=[];
  for(const wf of (Array.isArray(json?.workflows)?json.workflows:[])) if(wf?.name) ids.push(wf.name);
  for(const item of (Array.isArray(json?.media)?json.media:[])) if(item?.workflowId) ids.push(item.workflowId);
  return [...new Set(ids)];
}

function batchIdsFromResponse(json){
  const ids=[];
  for(const wf of (Array.isArray(json?.workflows)?json.workflows:[])) if(wf?.metadata?.batchId) ids.push(wf.metadata.batchId);
  if(json?.batchId) ids.push(json.batchId);
  return [...new Set(ids)];
}

function videoMediaFromResponse(json){
  const found=[];
  for(const item of (Array.isArray(json?.media)?json.media:[])){
    if(item?.name) found.push({
      mediaId:item.name,
      workflowId:item?.workflowId||null,
      status:item?.mediaMetadata?.mediaStatus?.mediaGenerationStatus||null,
      title:item?.mediaMetadata?.mediaTitle||item?.video?.generatedVideo?.prompt||null,
      source:'media'
    });
  }
  for(const wf of (Array.isArray(json?.workflows)?json.workflows:[])){
    if(wf?.metadata?.primaryMediaId) found.push({mediaId:wf.metadata.primaryMediaId,workflowId:wf?.name||null,status:null,title:wf?.metadata?.displayName||null,source:'workflow.primaryMediaId'});
  }
  return uniqueByMediaId(found);
}

function statusOfMedia(item){return item?.mediaMetadata?.mediaStatus?.mediaGenerationStatus||null;}
function isSuccessStatus(status){return status==='MEDIA_GENERATION_STATUS_SUCCESSFUL';}
function isFailureStatus(status){
  const t=String(status||'').toUpperCase();
  return t.includes('FAIL')||t.includes('ERROR')||t.includes('CANCEL')||t.includes('BLOCK');
}

function findNumericProgress(obj,seen=new WeakSet()){
  if(!obj||typeof obj!=='object') return null;
  if(seen.has(obj)) return null;
  seen.add(obj);
  if(Array.isArray(obj)){
    for(const item of obj){const v=findNumericProgress(item,seen);if(v!=null)return v;}
    return null;
  }
  for(const [key,value] of Object.entries(obj)){
    if((/progress|percent/i).test(key)&&typeof value==='number'&&value>=0&&value<=100) return value;
  }
  for(const value of Object.values(obj)){
    const v=findNumericProgress(value,seen);if(v!=null)return v;
  }
  return null;
}

function stageToJobPercent({imageEnabled,videoEnabled,stage,percent}){
  const pct=Math.max(0,Math.min(100,Number(percent||0)));
  if(imageEnabled&&videoEnabled){
    if(stage==='IMAGE') return pct*0.5;
    if(stage==='VIDEO') return 50+pct*0.5;
    return 100;
  }
  return pct;
}

async function updateJobProgress({index,total,imageEnabled,videoEnabled,stage,stagePercent,workerLabel,tag,detail='',exact=false}){
  const stageName=stage==='IMAGE'?'Ảnh':stage==='VIDEO'?'Video':'Tiến trình';
  const jobPercent=stageToJobPercent({imageEnabled,videoEnabled,stage,percent:stagePercent});
  const jobs={...(runtimeCache.jobs||{})};
  const previous=Number(jobs[index]?.percent||0);
  jobs[index]={...(jobs[index]||{}),percent:Math.max(previous,jobPercent),stage,stagePercent:Number(stagePercent||0),workerLabel,tag};
  runtimeCache.jobs=jobs;
  let sum=0;
  for(let i=0;i<total;i++) sum+=Number(jobs[i]?.percent||0);
  const overall=total?sum/total:0;
  const mark=exact?'':'~';
  await setProgress(overall,`${workerLabel} ${tag} ${stageName} ${mark}${Math.round(stagePercent)}%`,detail?`${detail} · Tổng batch ${Math.round(overall)}%`:`Tổng batch ${Math.round(overall)}%`);
}

function eventProjectMatches(event,projectId){
  if(!projectId) return true;
  const info=event?.requestInfo||{};
  if(info.projectId===projectId) return true;
  if(Array.isArray(info.projectIds)&&info.projectIds.includes(projectId)) return true;
  if(projectIdFromImageApiUrl(event?.url||'')===projectId) return true;
  if(event?.json?.projectId===projectId) return true;
  if(Array.isArray(event?.json?.media)&&event.json.media.some(x=>x?.projectId===projectId)) return true;
  if(Array.isArray(event?.json?.workflows)&&event.json.workflows.some(x=>x?.projectId===projectId)) return true;
  return false;
}

function workflowResolutionMedia(event){
  const fromRequest=event?.requestInfo?.primaryMediaId||null;
  const fromResponse=event?.json?.metadata?.primaryMediaId||event?.json?.workflow?.metadata?.primaryMediaId||null;
  const mediaId=fromRequest||fromResponse;
  if(!mediaId) return null;
  return {
    mediaId,
    workflowId:event?.requestInfo?.workflowId||event?.json?.name||workflowIdFromUrl(event?.url||'')||null,
    title:event?.json?.metadata?.displayName||null,
    url:null,
    source:'flowWorkflow.primaryMediaId'
  };
}


async function waitImageGenerationForRequest(tabId,{requestMeta,projectId,timeoutMs,tag,workerLabel,progressCtx}){
  const startedAt=Date.now();
  let tick=setInterval(()=>{
    const ratio=Math.min(1,(Date.now()-startedAt)/timeoutMs);
    updateJobProgress({...progressCtx,stage:'IMAGE',stagePercent:Math.min(96,8+ratio*88),detail:'Ảnh đang tạo song song...',exact:false}).catch(()=>{});
  },1000);
  try{
    const createEvent=await waitExactRequestFinished(tabId,requestMeta.requestId,timeoutMs,`${tag} IMAGE requestId=${requestMeta.requestId}`);
    if(!(createEvent.status>=200&&createEvent.status<300)) throw new Error(`${tag} POST tạo ảnh HTTP ${createEvent.status}. body=${String(createEvent.body||'').slice(0,300)}`);
    const direct=imageMediaFromResponse(createEvent.json);
    const requestBatch=createEvent?.requestInfo?.batchId||requestMeta?.requestInfo?.batchId||null;
    const responseBatches=batchIdsFromResponse(createEvent.json);
    const workflowIds=workflowIdsFromResponse(createEvent.json);
    await appendLog(`${workerLabel} ${tag} IMAGE POST 200 seq=${createEvent.seq} requestId=${createEvent.requestId} batch=${requestBatch||responseBatches[0]||'-'} media=${direct.map(x=>x.mediaId).join(',')||'chưa có'}`,'info');
    if(direct.length) return {images:direct,raw:createEvent.json,eventMeta:createEvent,source:'image-post'};

    const left=Math.max(1000,timeoutMs-(Date.now()-startedAt));
    await appendLog(`${workerLabel} ${tag} POST ảnh chưa trả mediaId → chờ workflow patch đúng batch/workflow`,'info');
    const wfEvent=await waitNet(tabId,event=>{
      if(event.kind!=='FLOW_WORKFLOW') return false;
      if(Number(event.seq)<=Number(createEvent.seq)) return false;
      if(event.method!=='PATCH') return false;
      if(!eventProjectMatches(event,projectId)) return false;
      const media=workflowResolutionMedia(event);
      if(!media) return false;
      const eventWorkflow=media.workflowId;
      const eventBatch=event?.json?.metadata?.batchId||event?.requestInfo?.batchId||null;
      const workflowMatch=workflowIds.length>0 && eventWorkflow && workflowIds.includes(eventWorkflow);
      const batchCandidates=[requestBatch,...responseBatches].filter(Boolean);
      const batchMatch=batchCandidates.length>0 && eventBatch && batchCandidates.includes(eventBatch);
      // Concurrent mode must have a positive correlation key; never grab an arbitrary patch.
      if(!workflowMatch&&!batchMatch) return false;
      if(event.failed) return {error:new Error(`${tag} flowWorkflow PATCH lỗi: ${event.errorText||'unknown'}`)};
      if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} flowWorkflow PATCH HTTP ${event.status}`)};
      return {value:{event,media}};
    },left,`${tag} chờ correlated flowWorkflow.primaryMediaId`);
    return {images:[wfEvent.media],raw:wfEvent.event.json,eventMeta:createEvent,source:'workflow-patch'};
  }finally{clearInterval(tick);}
}

async function waitVideoCreateForRequest(tabId,{requestMeta,timeoutMs,tag}){
  const event=await waitExactRequestFinished(tabId,requestMeta.requestId,timeoutMs,`${tag} VIDEO requestId=${requestMeta.requestId}`);
  if(!(event.status>=200&&event.status<300)) throw new Error(`${tag} Video create HTTP ${event.status}. body=${String(event.body||'').slice(0,300)}`);
  return {event,videos:videoMediaFromResponse(event.json),raw:event.json};
}

function responseHasPrompt(event,prompt){
  const wanted=normalizedPrompt(prompt);
  if(!wanted) return true;
  const media=Array.isArray(event?.json?.media)?event.json.media:[];
  return media.some(item=>{
    const candidates=[item?.mediaMetadata?.mediaTitle,item?.video?.generatedVideo?.prompt];
    return candidates.some(x=>normalizedPrompt(x)===wanted);
  });
}

async function waitVideoAssetsFallbackConcurrent(tabId,{afterSeq,projectId,prompt,timeoutMs,tag}){
  const event=await waitNet(tabId,event=>{
    if(event.kind!=='VIDEO_STATUS') return false;
    if(Number(event.seq)<=Number(afterSeq)) return false;
    if(event.method!=='POST') return false;
    if(!eventProjectMatches(event,projectId)) return false;
    if(!responseHasPrompt(event,prompt)) return false;
    const assets=videoMediaFromResponse(event.json);
    const ids=assets.map(x=>x.mediaId).filter(Boolean);
    return ids.length?{value:{event,assets}}:false;
  },timeoutMs,`${tag} chờ video mediaId/mediaTitle bằng prompt correlation`);
  return event.assets||[];
}

async function waitVideoMediaIdsFallbackConcurrent(tabId,args){
  const assets=await waitVideoAssetsFallbackConcurrent(tabId,args);
  return [...new Set(assets.map(x=>x.mediaId).filter(Boolean))];
}

async function collectVideoAssetsUntilCount(tabId,{afterSeq,projectId,prompt,existing=[],expected=1,timeoutMs=90000,tag}){
  const found=new Map();
  for(const a of existing||[]) if(a?.mediaId) found.set(a.mediaId,a);
  let cursor=Number(afterSeq||0);
  const deadline=Date.now()+timeoutMs;
  while(found.size<expected && Date.now()<deadline){
    const remain=Math.max(1000,deadline-Date.now());
    try{
      const wrapped=await waitNet(tabId,event=>{
        if(event.kind!=='VIDEO_STATUS') return false;
        if(Number(event.seq)<=cursor) return false;
        if(event.method!=='POST') return false;
        if(!eventProjectMatches(event,projectId)) return false;
        if(!responseHasPrompt(event,prompt)) return false;
        const assets=videoMediaFromResponse(event.json).filter(x=>x?.mediaId);
        return assets.length?{value:{event,assets}}:false;
      },Math.min(remain,20000),`${tag} gom đủ ${expected} video output`);
      cursor=Math.max(cursor,Number(wrapped.event?.seq||cursor));
      for(const a of wrapped.assets||[]) if(a?.mediaId) found.set(a.mediaId,a);
      await appendLog(`TAB ${tag} gom output video → ${found.size}/${expected}`,'info');
    }catch(error){
      if(Date.now()>=deadline) break;
      await sleep(500);
    }
  }
  return [...found.values()];
}


async function waitImageGenerationResult(tabId,{marker,projectId,timeoutMs,tag,workerLabel,progressCtx}){
  const startedAt=Date.now();
  let tick=null;
  if(progressCtx){
    tick=setInterval(()=>{
      const ratio=Math.min(1,(Date.now()-startedAt)/timeoutMs);
      updateJobProgress({...progressCtx,stage:'IMAGE',stagePercent:Math.min(96,5+ratio*91),detail:'Đang chờ đúng POST tạo ảnh của job này...',exact:false}).catch(()=>{});
    },1000);
  }

  try{
    const createEvent=await waitNet(tabId,event=>{
      if(event.kind!=='IMAGE_CREATE') return false;
      if(!eventAfterMarker(event,marker)) return false;
      if(event.method!=='POST') return false;
      if(!event.requestInfo?.validGeneration) return false;
      if(!eventProjectMatches(event,projectId)) return false;
      if(event.failed) return {error:new Error(`${tag} POST tạo ảnh lỗi Network: ${event.errorText||'unknown'}`)};
      if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} POST tạo ảnh HTTP ${event.status}. body=${String(event.body||'').slice(0,300)}`)};
      return {value:event};
    },timeoutMs,`${tag} chờ đúng POST flowMedia:batchGenerateImages`);

    const direct=imageMediaFromResponse(createEvent.json);
    const requestBatch=createEvent?.requestInfo?.batchId||null;
    const responseBatches=batchIdsFromResponse(createEvent.json);
    const workflowIds=workflowIdsFromResponse(createEvent.json);

    await appendLog(`${workerLabel} ${tag} IMAGE POST 200 seq=${createEvent.seq} batch=${requestBatch||responseBatches[0]||'-'} media=${direct.map(x=>x.mediaId).join(',')||'chưa có'}`,'info');

    if(direct.length){
      return {images:direct,raw:createEvent.json,eventMeta:createEvent,source:'image-post'};
    }

    // Some Flow/model variants acknowledge the image POST first and write primaryMediaId
    // through flowWorkflows afterwards. This is still Network correlation, not Asset guessing.
    const left=Math.max(1000,timeoutMs-(Date.now()-startedAt));
    await appendLog(`${workerLabel} ${tag} POST ảnh chưa trả mediaId → tiếp tục chờ flowWorkflow.primaryMediaId cùng batch/workflow`,'info');

    const wfEvent=await waitNet(tabId,event=>{
      if(event.kind!=='FLOW_WORKFLOW') return false;
      if(Number(event.seq)<=Number(createEvent.seq)) return false;
      if(event.method!=='PATCH') return false;
      if(!eventProjectMatches(event,projectId)) return false;
      const media=workflowResolutionMedia(event);
      if(!media) return false;
      const eventWorkflow=media.workflowId;
      const eventBatch=event?.json?.metadata?.batchId||event?.requestInfo?.batchId||null;
      const workflowMatch=!workflowIds.length || (eventWorkflow&&workflowIds.includes(eventWorkflow));
      const batchCandidates=[requestBatch,...responseBatches].filter(Boolean);
      const batchMatch=!batchCandidates.length || (eventBatch&&batchCandidates.includes(eventBatch));
      if(!workflowMatch&&!batchMatch) return false;
      if(event.failed) return {error:new Error(`${tag} flowWorkflow PATCH lỗi: ${event.errorText||'unknown'}`)};
      if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} flowWorkflow PATCH HTTP ${event.status}`)};
      return {value:{event,media}};
    },left,`${tag} chờ flowWorkflow.primaryMediaId`);

    return {images:[wfEvent.media],raw:wfEvent.event.json,eventMeta:createEvent,source:'workflow-patch'};
  }finally{
    if(tick) clearInterval(tick);
  }
}

async function waitVideoCreate(tabId,{marker,projectId,referenceMediaId,timeoutMs,tag}){
  return await waitNet(tabId,event=>{
    if(event.kind!=='VIDEO_CREATE') return false;
    if(!eventAfterMarker(event,marker)) return false;
    if(event.method!=='POST') return false;
    if(!event.requestInfo?.validGeneration) return false;
    if(!eventProjectMatches(event,projectId)) return false;
    if(referenceMediaId && !event.requestInfo?.referenceMediaIds?.includes(referenceMediaId)) return false;
    if(event.failed) return {error:new Error(`${tag} POST tạo video lỗi Network: ${event.errorText||'unknown'}`)};
    if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} Video create HTTP ${event.status}. body=${String(event.body||'').slice(0,300)}`)};
    return {value:{event,videos:videoMediaFromResponse(event.json),raw:event.json}};
  },timeoutMs,`${tag} chờ đúng POST batchAsyncGenerateVideo`);
}

async function waitVideoMediaIdsFromStatus(tabId,{marker,projectId,timeoutMs,tag}){
  const event=await waitNet(tabId,event=>{
    if(event.kind!=='VIDEO_STATUS') return false;
    if(!eventAfterMarker(event,marker)) return false;
    if(event.method!=='POST') return false;
    if(!eventProjectMatches(event,projectId)) return false;
    const ids=event.requestInfo?.mediaIds||[];
    return ids.length?{value:event}:false;
  },timeoutMs,`${tag} chờ video mediaId từ status poll`);
  return [...new Set(event.requestInfo.mediaIds)];
}

async function waitVideosSuccessful(tabId,{mediaIds,marker,projectId,timeoutMs,tag,workerLabel,progressCtx}){
  const remaining=new Set(mediaIds);
  const statuses=new Map();
  const startedAt=Date.now();
  let minSeq=marker.seq;
  let numericProgress=null;

  while(remaining.size){
    const left=Math.max(1000,timeoutMs-(Date.now()-startedAt));
    let event;
    const statusWindow=Math.min(left,VIDEO_STATUS_SILENCE_TIMEOUT_MS);
    try{
      event=await waitNet(tabId,event=>{
        if(event.kind!=='VIDEO_STATUS') return false;
        if(Number(event.seq)<=Number(minSeq)) return false;
        if(event.method!=='POST') return false;
        if(!eventProjectMatches(event,projectId)) return false;
        const reqIds=event.requestInfo?.mediaIds||[];
        if(!reqIds.some(id=>remaining.has(id))) return false;
        if(event.failed) return {error:new Error(`${tag} Video status Network lỗi: ${event.errorText||'unknown'}`)};
        if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} Video status HTTP ${event.status}`)};
        return {value:event};
      },statusWindow,`${tag} chờ status đúng video mediaId`);
    }catch(error){
      if(left>VIDEO_STATUS_SILENCE_TIMEOUT_MS){
        throw new Error(`${tag} VIDEO_STATUS_STALLED · ${Math.round(VIDEO_STATUS_SILENCE_TIMEOUT_MS/1000)}s không có status mới`);
      }
      throw error;
    }

    minSeq=Number(event.seq||minSeq);
    const media=Array.isArray(event.json?.media)?event.json.media:[];
    const p=findNumericProgress(event.json);
    if(p!=null) numericProgress=p;

    for(const item of media){
      const id=item?.name;
      if(!remaining.has(id)) continue;
      const status=statusOfMedia(item);
      statuses.set(id,status);
      await updateTrackedMedia(id,{status:isSuccessStatus(status)?'SUCCESS':status});
      await appendLog(`${workerLabel} ${tag} video ${String(id).slice(0,8)}… → ${status||'UNKNOWN'}`,isSuccessStatus(status)?'success':'info');
      if(isFailureStatus(status)) throw new Error(`${tag} Video ${id} thất bại: ${status}`);
      if(isSuccessStatus(status)) remaining.delete(id);
    }

    const successCount=mediaIds.filter(id=>!remaining.has(id)).length;
    let stagePercent;
    if(numericProgress!=null) stagePercent=Math.max(successCount/mediaIds.length*100,numericProgress);
    else {
      const elapsed=Math.min(1,(Date.now()-startedAt)/timeoutMs);
      stagePercent=Math.max(successCount/mediaIds.length*100,20+elapsed*75);
    }
    if(!remaining.size) stagePercent=100;
    await updateJobProgress({...progressCtx,stage:'VIDEO',stagePercent,detail:`Đã xong ${successCount}/${mediaIds.length} video${numericProgress!=null?` · server ${Math.round(numericProgress)}%`:' · đang poll status'}`,exact:numericProgress!=null||!remaining.size});
  }

  return {mediaIds,statuses:Object.fromEntries(statuses)};
}


function sceneNeedsVideo(scene,videoEnabled=true){
  if(!videoEnabled) return false;
  const metadata=(scene?.metadata&&typeof scene.metadata==='object')?scene.metadata:{};
  if(typeof metadata.makeVideo==='boolean') return metadata.makeVideo;
  if(typeof metadata.mixedMotion==='boolean') return metadata.mixedMotion;
  if(typeof scene?.makeVideo==='boolean') return scene.makeVideo;
  // Legacy jobs: an actual videoPrompt means this scene is a video scene.
  return Boolean(String(scene?.videoPrompt??'').trim());
}

function normalizeScenes(scenes,imageEnabled=true,videoEnabled=true){
  if(!Array.isArray(scenes)) return [];
  return scenes.map((scene,index)=>{
    const imagePrompt=String(scene?.imagePrompt??'').trim();
    const videoPrompt=String(scene?.videoPrompt??'').trim();
    const makeVideo=sceneNeedsVideo(scene,videoEnabled);
    if(imageEnabled&&!imagePrompt) throw new Error(`Scene ${index+1} thiếu imagePrompt.`);
    // CRITICAL v14.5.35: validate videoPrompt only for scenes explicitly selected for VIDEO.
    if(makeVideo&&!videoPrompt) throw new Error(`Scene ${index+1} được đánh dấu makeVideo nhưng thiếu videoPrompt.`);
    const rawSceneId=Number(scene?.sceneId);const sceneId=Number.isInteger(rawSceneId)&&rawSceneId>0?rawSceneId:index+1;
    const inputImages=Array.isArray(scene?.inputImages)
      ? scene.inputImages.map(item=>({
          path:String(item?.path||'').trim(),
          name:String(item?.name||'').trim(),
          role:String(item?.role||'reference').trim()||'reference',
          mediaId:String(item?.mediaId||item?.media_id||'').trim(),
          title:String(item?.title||'').trim(),
          fileName:String(item?.fileName||item?.name||'').trim(),
          videoReference:item?.videoReference!==false
        })).filter(item=>item.path||item.mediaId)
      : [];
    const videoSegments=Array.isArray(scene?.videoSegments)
      ? scene.videoSegments.map((item,i)=>({role:String(item?.role||`segment_${i+1}`).trim(),prompt:String(item?.prompt||'').trim()})).filter(item=>item.prompt)
      : [];
    const metadata={...(scene?.metadata||{}),makeVideo};
    return {imagePrompt,videoPrompt,videoSegments,sceneId,inputImages,metadata,makeVideo};
  });
}


function parsePairs(raw,imageEnabled=true,videoEnabled=true){
  const lines=String(raw??'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  return lines.map((line,index)=>{
    const pos=line.indexOf('|');
    if(imageEnabled&&videoEnabled){
      if(pos<1||pos>=line.length-1) throw new Error(`Dòng ${index+1} phải là imagePrompt|videoPrompt.`);
      return {imagePrompt:line.slice(0,pos).trim(),videoPrompt:line.slice(pos+1).trim(),makeVideo:true};
    }
    if(imageEnabled&&!videoEnabled){
      const imagePrompt=pos>=0?line.slice(0,pos).trim():line.trim();
      if(!imagePrompt) throw new Error(`Dòng ${index+1} thiếu imagePrompt.`);
      return {imagePrompt,videoPrompt:'',makeVideo:false};
    }
    if(!imageEnabled&&videoEnabled){
      const videoPrompt=pos>=0?line.slice(pos+1).trim():line.trim();
      if(!videoPrompt) throw new Error(`Dòng ${index+1} thiếu videoPrompt.`);
      return {imagePrompt:'',videoPrompt,makeVideo:true};
    }
    throw new Error('Image model và Video model đều là None.');
  });
}


async function startImageJob(tabId,record,options,limiter,total){
  const index=record.index, tag=`[${index+1}/${total}]`, workerLabel='TAB';
  const progressCtx={index,total,imageEnabled:true,videoEnabled:recordNeedsVideo(record,options),workerLabel,tag};
  await patchJob(index,{imageState:'PREPARING'});
  await withPrepareRetry(tabId,`IMAGE PREP ${tag}`,async()=>{
    await clearComposerBeforeCreate(tabId,tag);
    if(Array.isArray(record.pair.inputImages)&&record.pair.inputImages.length){
      const refs=await ensureSceneImageInputs(tabId,record,options);
      if(refs.some(x=>x?.uiRecovered)){
        await appendLog(`TAB ${tag} upload đã F5 Flow → re-verify IMAGE Settings trước Create`, 'info');
        await configureStageSettings(tabId,{type:'IMAGE',aspectRatio:options.aspectRatio,outputs:options.imageOutputs,modelKind:'IMAGE',model:options.imageModel},3);
      }
    }
    await appendLog(`TAB ${tag} IMAGE chuẩn bị prompt: ${record.pair.imagePrompt}`,'info');
    await callPage(tabId,'replacePrompt',[record.pair.imagePrompt]);
    await callPage(tabId,'waitCreateReady',[15000]);
  },3);
  await limiter.waitTurn(`IMAGE ${tag}`);
  const marker=createNetworkMarker(tabId);
  await appendLog(`TAB ${tag} CLICK CREATE IMAGE | marker=${marker.seq}`,'info');
  await trustedCreateClick(tabId);
  const requestMeta=await waitGenerationRequestStart(tabId,{
    kind:'IMAGE_CREATE',marker,projectId:options.projectId,prompt:record.pair.imagePrompt,
    timeoutMs:10000,label:`IMAGE POST ${tag}`
  });
  await patchJob(index,{imageState:'ACTIVE',imageRequestId:requestMeta.requestId,imageSeq:requestMeta.seq});
  await appendLog(`TAB ${tag} IMAGE REQUEST START seq=${requestMeta.seq} requestId=${requestMeta.requestId} batch=${requestMeta.requestInfo?.batchId||'-'}`,'info');
  const lifecycle=waitImageGenerationForRequest(tabId,{
    requestMeta,projectId:options.projectId,timeoutMs:options.imageTimeoutMs,tag,workerLabel,progressCtx
  });
  return {lifecycle,requestMeta};
}

async function runImagePhase(tabId,records,options,limiter){
  const total=records.length;
  const queue=records.filter(r=>options.imageEnabled).map(r=>r.index);
  if(!queue.length) return [];
  await appendLog(`PHASE IMAGE: set + verify Settings 1 lần, đồng thời tối đa ${options.imageConcurrency}`,'info');
  const imageSettings=await configureStageSettings(tabId,{
    type:'IMAGE',aspectRatio:options.aspectRatio,outputs:options.imageOutputs,
    modelKind:'IMAGE',model:options.imageModel
  },3);
  await appendLog(`IMAGE SETTINGS VERIFIED (attempt ${imageSettings.attempt}) → ${JSON.stringify(imageSettings.verification.current)}`,'success');

  const inFlight=new Map();
  const completionOrder=[];
  let cursor=0, stopSubmitting=false;
  while(cursor<queue.length || inFlight.size){
    while(!stopSubmitting && cursor<queue.length && inFlight.size<options.imageConcurrency){
      const index=queue[cursor++], record=records[index];
      try{
        const started=await startImageJob(tabId,record,options,limiter,total);
        inFlight.set(index,started.lifecycle.then(value=>({index,ok:true,value}),error=>({index,ok:false,error})));
        await setMetrics({imageInFlight:inFlight.size});
      }catch(error){
        const text=error?.message||String(error);
        record.error=text; record.imageState='ERROR';
        await patchJob(index,{imageState:'ERROR',error:text,done:!options.videoEnabled});
        await appendLog(`TAB [${index+1}/${total}] ❌ IMAGE submit: ${text}`,'error');
        if(isQuotaLikeError(error)||isFatalFlowUiError(error)) stopSubmitting=true;
      }
    }
    if(!inFlight.size) break;
    const settled=await Promise.race([...inFlight.values()]);
    inFlight.delete(settled.index);
    await setMetrics({imageInFlight:inFlight.size});
    const record=records[settled.index], tag=`[${settled.index+1}/${total}]`;
    if(settled.ok){
      record.imageResult=settled.value;
      record.selectedImage=settled.value.images?.[0]||null;
      record.imageState='SUCCESS';
      record.imageCompletedAt=Date.now();
      if(!record.selectedImage?.mediaId){
        record.error='IMAGE success nhưng mediaId rỗng';
        record.imageState='ERROR';
        await patchJob(record.index,{imageState:'ERROR',error:record.error,done:!options.videoEnabled});
        await appendLog(`TAB ${tag} ❌ ${record.error}`,'error');
      }else{
        completionOrder.push(record.index);
        await updateJobProgress({index:record.index,total,imageEnabled:true,videoEnabled:options.videoEnabled,stage:'IMAGE',stagePercent:100,workerLabel:'TAB',tag,detail:`IMAGE SUCCESS → ${record.selectedImage.mediaId}`,exact:true});
        await patchJob(record.index,{imageState:'SUCCESS',imageMediaId:record.selectedImage.mediaId,imageSource:settled.value.source});
        await appendLog(`TAB ${tag} IMAGE SUCCESS → ${record.selectedImage.mediaId} | source=${settled.value.source}`,'success');
      }
    }else{
      const text=settled.error?.message||String(settled.error);
      record.error=text; record.imageState='ERROR';
      await patchJob(record.index,{imageState:'ERROR',error:text,done:!options.videoEnabled});
      await appendLog(`TAB ${tag} ❌ IMAGE: ${text}`,'error');
      if(isQuotaLikeError(settled.error)||isFatalFlowUiError(settled.error)) stopSubmitting=true;
    }
  }

  if(stopSubmitting && cursor<queue.length){
    for(;cursor<queue.length;cursor++){
      const index=queue[cursor], text='Dừng submit ảnh mới vì phát hiện quota/rate limit.';
      records[index].error=text; records[index].imageState='NOT_SUBMITTED';
      await patchJob(index,{imageState:'NOT_SUBMITTED',error:text,done:true});
    }
  }
  return completionOrder;
}

async function startVideoJob(tabId,record,options,limiter,total){
  const index=record.index, tag=`[${index+1}/${total}]`, workerLabel='TAB';
  const progressCtx={index,total,imageEnabled:options.imageEnabled,videoEnabled:true,workerLabel,tag};
  await patchJob(index,{videoState:'PREPARING'});
  await withPrepareRetry(tabId,`VIDEO PREP ${tag}`,async()=>{
    await clearComposerBeforeCreate(tabId,tag);
  
    if(options.imageEnabled){
      // v14.5.35 identity lock: VIDEO gets character refs + generated scene frame.
      // For Mother+Child this becomes image1 + image2 + image3, reducing face drift.
      let videoBaseRefCount=0;
      if(Array.isArray(record.pair.inputImages)&&record.pair.inputImages.length){
        const baseRefs=await ensureSceneImageInputs(tabId,record,options,'video'); videoBaseRefCount=baseRefs.length;
        await appendLog(`TAB ${tag} VIDEO BASE REFS ATTACHED → ${baseRefs.map(x=>`${x.role}:${x.mediaId}`).join(' + ')}`,'success');
      }
      const mediaId=record.selectedImage?.mediaId;
      if(!mediaId) throw new Error(`${tag} Không có imageMediaId để attach.`);
      const searchCandidates=assetSearchCandidatesForRecord(record);
      await patchJob(index,{assetSearchCandidates:searchCandidates,imageGeneratedTitle:record.selectedImage?.title||null});
      await appendLog(`TAB ${tag} ATTACH exact imageMediaId ${mediaId} | ưu tiên NO_SEARCH theo mediaId | text fallback=${JSON.stringify(searchCandidates)}`,'info');
      let attached=null;
      try{
        attached=await trustedAttachIngredient(tabId,searchCandidates,mediaId,60000,tag);
      }catch(error){
        const text=error?.message||String(error);
        if(!/không thấy đúng mediaId|Asset Picker/i.test(text)) throw error;
        await appendLog(`TAB ${tag} asset chưa index/stale → F5 project + verify VIDEO + retry exact mediaId`, 'info');
        await reloadAndNormalizeFlow(tabId,`video asset ${mediaId} chưa thấy trong picker`,options.projectId);
        await configureStageSettings(tabId,{
          type:'VIDEO',...(options.imageEnabled?{videoMode:'INGREDIENTS'}:{}),
          aspectRatio:options.aspectRatio,duration:options.videoDuration,outputs:options.videoOutputs,
          modelKind:'VIDEO',model:options.videoModel
        },3);
        await sleep(1200);
        attached=await trustedAttachIngredient(tabId,searchCandidates,mediaId,90000,tag);
      }
      if(!attached?.ok||attached.mediaId!==mediaId) throw new Error(`${tag} Ingredient attach verify thất bại.`);
      record.assetSearchPrompt=attached.searchUsed||'';
      await patchJob(index,{assetSearchPrompt:attached.searchUsed||'',assetSearchSource:attached.searchMode||'EXACT_MEDIA_ID',imageAssetTitle:attached.title||null});
      await appendLog(`TAB ${tag} INGREDIENT ATTACHED → ${mediaId} | mode=${attached.searchMode} | Search=${JSON.stringify(attached.searchUsed||'')} | title=${JSON.stringify(attached.title||'')}`,'success');
      if(videoBaseRefCount) await appendLog(`TAB ${tag} VIDEO IDENTITY PACK → ${videoBaseRefCount} character ref + 1 scene frame = ${videoBaseRefCount+1} ingredients`,'success');
      const expectedVideoRefs=videoBaseRefCount+1;
      const pack=await ensureComposerReferencePackReady(tabId,tag,expectedVideoRefs,8000);
      if(!pack?.ok){
        const errors=(pack?.errors||[]).map(x=>`${x.mediaId||'?'}@${x.index}`).join(',');
        if(pack?.errors?.length) await cleanupComposerErrorCards(tabId,tag).catch(()=>{});
        throw new Error(`VIDEO_REFERENCE_PACK_ERROR · expected=${expectedVideoRefs} actual=${pack?.state?.count||0} errors=${errors||'-'}`);
      }
      await patchJob(index,{videoReferencePackReady:true,videoReferenceCount:Number(pack.state?.count||0),videoReferenceErrors:0});
      await appendLog(`TAB ${tag} VIDEO REFERENCE_READY ${pack.state.validCount||pack.state.count}/${expectedVideoRefs} · error=0`,'success');
    }
  
    await callPage(tabId,'replacePrompt',[record.pair.videoPrompt]);
    await callPage(tabId,'waitCreateReady',[15000]);
  },3);
  await limiter.waitTurn(`VIDEO ${tag}`);
  const marker=createNetworkMarker(tabId);
  await appendLog(`TAB ${tag} CLICK CREATE VIDEO | marker=${marker.seq}`,'info');
  await trustedCreateClick(tabId);
  const requestMeta=await waitGenerationRequestStart(tabId,{
    kind:'VIDEO_CREATE',marker,projectId:options.projectId,prompt:record.pair.videoPrompt,
    referenceMediaId:options.imageEnabled?record.selectedImage.mediaId:null,
    timeoutMs:Math.min(options.videoTimeoutMs,45000),label:`VIDEO POST ${tag}`
  });
  record.videoRequestMeta=requestMeta;
  await patchJob(index,{videoState:'ACTIVE',videoRequestId:requestMeta.requestId,videoSeq:requestMeta.seq});
  await appendLog(`TAB ${tag} VIDEO REQUEST START seq=${requestMeta.seq} requestId=${requestMeta.requestId} refs=${requestMeta.requestInfo?.referenceMediaIds?.join(',')||'-'}`,'info');

  const lifecycle=(async()=>{
    const created=await waitVideoCreateForRequest(tabId,{requestMeta,timeoutMs:Math.min(options.videoTimeoutMs,180000),tag});
    let videoAssets=(created.videos||[]).filter(x=>x?.mediaId);
    let videoIds=videoAssets.map(x=>x.mediaId).filter(Boolean);
    const expectedOutputs=outputFactor(options.videoOutputs,4);
    if(videoIds.length<expectedOutputs){
      await appendLog(`TAB ${tag} VIDEO POST mới có ${videoIds.length}/${expectedOutputs} mediaId → chờ status để gom đủ output x${expectedOutputs}`,'info');
      try{
        videoAssets=await collectVideoAssetsUntilCount(tabId,{
          afterSeq:requestMeta.seq,projectId:options.projectId,prompt:record.pair.videoPrompt,
          existing:videoAssets,expected:expectedOutputs,timeoutMs:Math.min(options.videoTimeoutMs,90000),tag
        });
        videoIds=videoAssets.map(x=>x.mediaId).filter(Boolean);
      }catch(error){
        await appendLog(`TAB ${tag} ⚠️ gom output x${expectedOutputs}: ${error?.message||error}`,'error');
      }
    }
    if(!videoIds.length) throw new Error(`${tag} Không xác định được video mediaId.`);
    if(videoIds.length<expectedOutputs) throw new Error(`${tag} Flow yêu cầu x${expectedOutputs} nhưng chỉ xác định được ${videoIds.length} video output.`);
    record.videoIds=videoIds;
    record.videoAssets=videoAssets;
    record.videoChainMediaIds=videoIds.length?[videoIds[0]]:[];
    await patchJob(index,{videoState:'ACTIVE',videoMediaIds:videoIds,videoAssets:videoAssets.map(x=>({mediaId:x.mediaId,title:x.title||null})),videoChainMediaIds:record.videoChainMediaIds});
    for(const [i,asset] of videoAssets.entries()){
      await rememberTrackedMedia({
        mediaId:asset.mediaId,
        jobId:options.serverJobId,
        sceneId:record.sceneId??record.index+1,
        sceneIndex:record.serverSceneIndex??record.index,
        title:asset.title,
        videoPrompt:record.pair?.videoPrompt,
        status:'PENDING'
      });
      await appendLog(`TAB ${tag} VIDEO ASSET ${i+1} → mediaId=${asset.mediaId} | title=${JSON.stringify(asset.title||'')}`,'success');
    }
    await updateJobProgress({...progressCtx,stage:'VIDEO',stagePercent:25,detail:`Đã nhận ${videoIds.length} video mediaId`,exact:false});
    const result=await waitVideosSuccessful(tabId,{
      mediaIds:videoIds,marker:{seq:requestMeta.seq,at:Date.now()},projectId:options.projectId,
      timeoutMs:options.videoTimeoutMs,tag,workerLabel,progressCtx
    });
    return {created,videoIds,videoAssets,result};
  })();
  return {lifecycle,requestMeta};
}

async function runVideoPhase(tabId,records,options,limiter,imageCompletionOrder){
  const total=records.length;
  let queue;
  if(options.imageEnabled){
    const rank=new Map(imageCompletionOrder.map((idx,pos)=>[idx,pos]));
    queue=records.filter(r=>r.imageState==='SUCCESS'&&!r.error&&recordNeedsVideo(r,options)).map(r=>r.index)
      .sort((a,b)=>(rank.get(a)??999999)-(rank.get(b)??999999));
  }else queue=records.map(r=>r.index);
  if(!queue.length) return;

  await appendLog(`PHASE VIDEO: set + verify Settings 1 lần, đồng thời tối đa ${options.videoConcurrency}`,'info');
  const videoSettings=await configureStageSettings(tabId,{
    type:'VIDEO',...(options.imageEnabled?{videoMode:'INGREDIENTS'}:{}),
    aspectRatio:options.aspectRatio,duration:options.videoDuration,outputs:options.videoOutputs,
    modelKind:'VIDEO',model:options.videoModel
  },3);
  await appendLog(`VIDEO SETTINGS VERIFIED (attempt ${videoSettings.attempt}) → ${JSON.stringify(videoSettings.verification.current)}`,'success');

  const inFlight=new Map();
  let cursor=0,stopSubmitting=false;
  while(cursor<queue.length || inFlight.size){
    while(!stopSubmitting && cursor<queue.length && inFlight.size<options.videoConcurrency){
      const index=queue[cursor++],record=records[index];
      try{
        const started=await startVideoJob(tabId,record,options,limiter,total);
        inFlight.set(index,started.lifecycle.then(value=>({index,ok:true,value}),error=>({index,ok:false,error})));
        await setMetrics({videoInFlight:inFlight.size});
      }catch(error){
        const text=error?.message||String(error);
        record.error=text; record.videoState='ERROR';
        await patchJob(index,{videoState:'ERROR',error:text,done:true});
        await appendLog(`TAB [${index+1}/${total}] ❌ VIDEO submit: ${text}`,'error');
        if(isQuotaLikeError(error)||isFatalFlowUiError(error)) stopSubmitting=true;
      }
    }
    if(!inFlight.size) break;
    const settled=await Promise.race([...inFlight.values()]);
    inFlight.delete(settled.index);
    await setMetrics({videoInFlight:inFlight.size});
    const record=records[settled.index],tag=`[${settled.index+1}/${total}]`;
    if(settled.ok){
      record.videoState='SUCCESS'; record.videoResult=settled.value;
      await updateJobProgress({index:record.index,total,imageEnabled:options.imageEnabled,videoEnabled:true,stage:'VIDEO',stagePercent:100,workerLabel:'TAB',tag,detail:'VIDEO SUCCESS',exact:true});
      await patchJob(record.index,{videoState:'SUCCESS',videoMediaIds:settled.value.videoIds,done:options.videoExtendFactor<=1});
      await appendLog(`TAB ${tag} VIDEO SUCCESS`,'success');
    }else{
      const text=settled.error?.message||String(settled.error);
      record.error=text; record.videoState='ERROR';
      await patchJob(record.index,{videoState:'ERROR',error:text,done:true});
      await appendLog(`TAB ${tag} ❌ VIDEO: ${text}`,'error');
      if(isQuotaLikeError(settled.error)||isFatalFlowUiError(settled.error)) stopSubmitting=true;
    }
  }
  if(stopSubmitting && cursor<queue.length){
    for(;cursor<queue.length;cursor++){
      const index=queue[cursor],text='Dừng submit video mới vì phát hiện quota/rate limit.';
      records[index].error=text; records[index].videoState='NOT_SUBMITTED';
      await patchJob(index,{videoState:'NOT_SUBMITTED',error:text,done:true});
    }
  }
}


function createWakeSignal(){
  let waiters=[];
  return {
    notify(){const list=waiters;waiters=[];for(const resolve of list)resolve();},
    async wait(timeoutMs=300){
      await new Promise(resolve=>{
        const timer=setTimeout(()=>{waiters=waiters.filter(x=>x!==done);resolve();},timeoutMs);
        const done=()=>{clearTimeout(timer);resolve();};
        waiters.push(done);
      });
    }
  };
}

function safeDownloadStem(value='video'){
  return String(value||'video').normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g,' ').replace(/\s+/g,' ').trim().slice(0,80)
    .replace(/\s+/g,'_') || 'video';
}

const desiredDownloadFilenames = new Map();
try {
  if (chrome.downloads?.onDeterminingFilename) {
    chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
      if (desiredDownloadFilenames.has(item.id)) {
        const desired = desiredDownloadFilenames.get(item.id);
        desiredDownloadFilenames.delete(item.id);
        suggest({ filename: desired, conflictAction: 'uniquify' });
        return true;
      }
      suggest();
      return false;
    });
  }
} catch (e) {
  console.warn('[FLOW] onDeterminingFilename setup error', e);
}

async function trackedBrowserDownload({url,filename,timeoutMs=180000,conflictAction='uniquify'}){
  let downloadId=null;
  try{
    assertServerAutomationAllowed('browser download');
    downloadId=await chrome.downloads.download({url,filename,saveAs:false,conflictAction});
    if(downloadId && filename) desiredDownloadFilenames.set(downloadId, filename);
    activeExtensionDownloadIds.add(downloadId);
    return await waitBrowserDownload(downloadId,timeoutMs);
  }
  catch(error){if(downloadId!=null){try{await chrome.downloads.cancel(downloadId);}catch{}}throw error;}
  finally{if(downloadId!=null){activeExtensionDownloadIds.delete(downloadId);desiredDownloadFilenames.delete(downloadId);}}
}

async function waitBrowserDownload(downloadId,timeoutMs=180000){
  const started=Date.now();let last=null;
  while(Date.now()-started<timeoutMs){
    const rows=await chrome.downloads.search({id:downloadId});last=rows?.[0]||null;
    if(last?.state==='complete'&&last.filename) return last;
    if(last?.state==='interrupted') throw new Error(`Download bị gián đoạn: ${last.error||'unknown'}`);
    await sleep(500);
  }
  throw new Error(`Timeout chờ browser download ${downloadId}. state=${last?.state||'unknown'}`);
}

function outputFactor(value,maximum=4){
  return Math.max(1,Math.min(maximum,Number(String(value||'x1').replace(/^x/i,''))||1));
}

async function downloadImageMediaIdsForServer({jobId,sceneId,mediaIds}){
  const ids=[...new Set(Array.isArray(mediaIds)?mediaIds:[])].filter(Boolean);
  if(!jobId||!sceneId||!ids.length) throw new Error('DOWNLOAD_IMAGE_MEDIA_FILES thiếu jobId/sceneId/mediaIds');
  const results=[];
  for(let i=0;i<ids.length;i++){
    const mediaId=ids[i];
    const url=`https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${encodeURIComponent(mediaId)}`;
    const safeJob=String(jobId).replace(/[^a-zA-Z0-9._-]/g,'_').slice(0,80);
    const sceneNo=String(sceneId).padStart(3,'0');
    const filename=`FlowAutomationServer/${safeJob}/recovery/${sceneNo}${ids.length>1?`_${i+1}`:''}.jpg`;
    let item=null,lastError=null;
    for(let attempt=1;attempt<=3;attempt++){
      try{
        item=await trackedBrowserDownload({url,filename,timeoutMs:60000,conflictAction:'overwrite'});
        lastError=null;break;
      }catch(error){
        lastError=error;
        await appendLog(`RECOVER IMAGE scene=${sceneId} media=${String(mediaId).slice(0,8)} attempt ${attempt}/3 lỗi: ${error?.message||error}`,'error');
        if(attempt<3) await sleep(Math.min(3000,700*(2**(attempt-1))));
      }
    }
    if(lastError||!item) throw lastError||new Error(`Image recovery không có file cho ${mediaId}`);
    const info={mediaId,mediaIndex:i,localPath:item.filename,state:item.state};results.push(info);
    await appendLog(`RECOVER IMAGE OK scene=${sceneId} media=${String(mediaId).slice(0,8)} → ${item.filename}`,'success');
    await sendOrStoreServerReport({
      type:'IMAGE_FILE_READY',jobId,sceneId,mediaId,mediaIndex:i,
      localPath:item.filename,browserFilename:filename,
      recovery:true,source:'media.getMediaUrlRedirect'
    });
  }
  return results;
}

async function downloadMediaIdsForServer({jobId,sceneId,mediaIds,mediaItems,dispatchEpoch=0,downloadMode='server_signed_url',refreshSignedUrl=false}){
  let items=[];
  if(Array.isArray(mediaItems)&&mediaItems.length){
    const seen=new Set();
    for(const raw of mediaItems){const mediaId=String(raw?.mediaId||'').trim();if(!mediaId||seen.has(mediaId))continue;seen.add(mediaId);items.push({mediaId,mediaIndex:Math.max(0,Number(raw?.mediaIndex||0))});}
  }else{
    items=[...new Set(Array.isArray(mediaIds)?mediaIds:[])].map((x,i)=>({mediaId:String(x||'').trim(),mediaIndex:i})).filter(x=>x.mediaId);
  }
  if(!jobId||!sceneId||!items.length) throw new Error('DOWNLOAD_MEDIA_FILES thiếu jobId/sceneId/mediaItems');
  const results=[];
  for(let i=0;i<items.length;i++){
    const {mediaId,mediaIndex}=items[i];
    let lastError=null;
    for(let attempt=1;attempt<=2;attempt++){
      try{
        const resolved=await resolveVideoSignedUrl(mediaId,{force:refreshSignedUrl||attempt>1});
        const payload={
          type:'VIDEO_DOWNLOAD_URL_READY',jobId,sceneId,mediaId,mediaIndex,dispatchEpoch:Number(dispatchEpoch||0),
          signedUrl:resolved.url,resolvedAt:new Date().toISOString(),source:resolved.source||'extension_resolver',
          resolverMethod:resolved.method||null,resolverStatus:resolved.status||null,downloadMode:'server_signed_url'
        };
        if(!sendServerMessage(payload)) throw new Error('Server bridge offline trước khi gửi signed URL');
        results.push({mediaId,mediaIndex,signedUrl:resolved.url,resolvedAt:payload.resolvedAt});
        await appendLog(`RECOVER VIDEO scene=${sceneId} [${i+1}/${items.length}] slot=${mediaIndex+1} mediaId=${mediaId.slice(0,8)} → SERVER`,'success');
        lastError=null;break;
      }catch(error){
        lastError=error; VIDEO_SIGNED_URL_CACHE.delete(mediaId);
        await appendLog(`RESOLVE VIDEO scene=${sceneId} media=${mediaId.slice(0,8)} attempt ${attempt}/2 lỗi: ${error?.message||error}`,'error');
        if(attempt<2) await sleep(1200);
      }
    }
    if(lastError){
      sendServerMessage({type:'VIDEO_DOWNLOAD_URL_ERROR',jobId,sceneId,mediaId,mediaIndex,dispatchEpoch:Number(dispatchEpoch||0),error:lastError?.message||String(lastError)});
      throw lastError;
    }
  }
  sendServerMessage({type:'VIDEO_DOWNLOAD_URL_SUMMARY',jobId,sceneId,dispatchEpoch:Number(dispatchEpoch||0),expected:items.length,urls:results.map(x=>({mediaId:x.mediaId,mediaIndex:x.mediaIndex,resolvedAt:x.resolvedAt}))});
  return results;
}

async function autoDownloadVideos(record,options){
  if(!options.autoDownloadVideo) return [];
  const ids=[...new Set(record.videoIds||[])].filter(Boolean);
  const downloads=[];
  for(let i=0;i<ids.length;i++){
    const mediaId=ids[i];
    const url=`https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${encodeURIComponent(mediaId)}`;
    const stem=safeDownloadStem(record.pair?.videoPrompt||record.pair?.imagePrompt||`job_${record.index+1}`);
    const sceneNo=String(record.sceneId??record.index+1).padStart(3,'0');
    const serverJobId=String(record?.serverJobId||options.serverJobId||'').trim();
    const safeJob=serverJobId.replace(/[^a-zA-Z0-9._-]/g,'_').slice(0,80);
    const filename=serverJobId
      ? `FlowAutomationServer/${safeJob}/raw/${sceneNo}${ids.length>1?`_${i+1}`:''}.mp4`
      : `FlowPairAuto/${String(record.index+1).padStart(3,'0')}_${stem}${ids.length>1?`_${i+1}`:''}.mp4`;
    let lastError=null;
    for(let attempt=1;attempt<=3;attempt++){
      let downloadId=null;
      try{
        assertServerAutomationAllowed('download media');
        downloadId=await chrome.downloads.download({url,filename,saveAs:false,conflictAction:'uniquify'});
        if(downloadId && filename) desiredDownloadFilenames.set(downloadId, filename);
        activeExtensionDownloadIds.add(downloadId);
        const item=await waitBrowserDownload(downloadId,180000);
        const info={mediaId,downloadId,filename,localPath:item.filename,state:item.state};
        downloads.push(info);
        await appendLog(`TAB [${record.index+1}] AUTO DOWNLOAD → ${item.filename}`,'success');
        await updateTrackedMedia(mediaId,{status:'DOWNLOADED',localPath:item.filename,downloadedAt:Date.now()});
        sendSceneCheckpoint(record,options,{status:'DOWNLOADED',progress:100,localPath:item.filename,videoMediaId:mediaId});
        if(serverJobId){
          sendServerMessage({type:'VIDEO_FILE_READY',jobId:serverJobId,sceneId:record.sceneId??record.index+1,sceneIndex:record.serverSceneIndex??record.index,mediaId,mediaIndex:i,localPath:item.filename,browserFilename:filename});
        }
        lastError=null;break;
      }catch(error){lastError=error;if(attempt<3) await sleep(1200*attempt);}
      finally{if(downloadId!=null){activeExtensionDownloadIds.delete(downloadId);desiredDownloadFilenames.delete(downloadId);}}
    }
    if(lastError){
      await appendLog(`TAB [${record.index+1}] ⚠️ Auto download lỗi media ${String(mediaId).slice(0,8)}…: ${lastError?.message||lastError}`,'error');
      sendSceneCheckpoint(record,options,{status:'DOWNLOAD_ERROR',error:lastError?.message||String(lastError)});
      if(serverJobId) sendServerMessage({type:'VIDEO_FILE_ERROR',jobId:serverJobId,sceneId:record.sceneId??record.index+1,mediaId,error:lastError?.message||String(lastError)});
    }
  }
  return downloads;
}



function formatVideoExtendPrompt(template,record,round){
  const meta=record?.pair?.metadata||{};
  const plan=Array.isArray(record?.pair?.videoSegments)?record.pair.videoSegments:[];
  // round=1 is continuation segment #2, so use index 1.  Product-specific AI
  // plans always win over the generic global template.
  let base=String(plan?.[round]?.prompt||template||'').trim();
  if(!base){
    const fallbackRoles=[
      'Continue into a detail-review segment with a new action and closer framing. Do not repeat the previous movement or camera path.',
      'Continue with a different useful product angle and a new movement purpose. Do not repeat earlier actions or framing.',
      'Continue into a strong commercial closing with a fresh composition and one decisive final product action. Do not repeat any earlier movement.'
    ];
    base=fallbackRoles[Math.max(0,Math.min(fallbackRoles.length-1,round-1))];
  }
  return base
    .replaceAll('{video_prompt}',String(record?.pair?.videoPrompt||''))
    .replaceAll('{product_name}',String(meta?.garmentName||meta?.productName||'the product'))
    .replaceAll('{character_name}',String(meta?.characterName||'the same person'))
    .replaceAll('{round}',String(round));
}

async function navigateProjectAllMedia(tabId,projectId){
  const target=`${FLOW_TOOL_URL}/project/${encodeURIComponent(projectId)}`;
  const current=await chrome.tabs.get(tabId).catch(()=>null);
  if(!isFlowProjectRootUrl(current?.url||'',projectId)){
    await appendLog(`PROJECT VIEW RECOVERY → thoát ${String(current?.url||'').includes('/edit/')?'media detail':'route con'} trước khi tìm All Media`,'info');
    await chrome.tabs.update(tabId,{url:target});
    await waitTabState(tabId,t=>t.status==='complete'&&projectIdFromFlowUrl(t.url||'')===projectId&&isFlowProjectRootUrl(t.url||'',projectId),30000,'mở Project root để tìm video');
  }else if(current?.status!=='complete'){
    await waitTabState(tabId,t=>t.status==='complete',30000,'Project tải xong');
  }
  await sleep(600);
  await injectPage(tabId);
  let point=null;
  for(let i=0;i<8;i++){
    try{point=await callPage(tabId,'getAllMediaPoint',[]);break;}catch{await sleep(250);}
  }
  if(point){await trustedClickPoint(tabId,point).catch(()=>{});await sleep(500);}
}

async function locateExactVideoTile(tabId,{projectId,mediaId,title,timeoutMs=45000,tag=''}){
  await navigateProjectAllMedia(tabId,projectId);
  const started=Date.now();
  let searched=false,lastTiles=[];
  while(Date.now()-started<timeoutMs){
    const exact=await callPage(tabId,'getVideoTileInfoByMediaId',[mediaId]).catch(()=>null);
    if(exact?.href){
      await appendLog(`TAB ${tag} EXTEND exact tile → mediaId=${mediaId} | title=${JSON.stringify(exact.title||title||'')} | tile=${exact.tileId||'-'}`,'success');
      return exact;
    }
    if(title && !searched){
      await callPage(tabId,'setGlobalAssetSearch',[title]).catch(()=>{});
      searched=true;
      await sleep(800);
    }
    lastTiles=await callPage(tabId,'listVisibleVideoTiles',[]).catch(()=>[]);
    await sleep(450);
  }
  // Fallback only when Flow returns exactly one card with the exact generated mediaTitle.
  // Never use the video prompt as a key because prompts can repeat across scenes.
  if(title){
    const byTitle=await callPage(tabId,'getVideoTileInfoByTitle',[title]).catch(()=>null);
    if(byTitle?.count===1&&byTitle?.match?.href){
      await appendLog(`TAB ${tag} EXTEND fallback unique mediaTitle → ${JSON.stringify(title)} | tile=${byTitle.match.tileId||'-'}`,'info');
      return byTitle.match;
    }
  }
  throw new Error(`${tag} Không tìm thấy exact video tile mediaId=${mediaId}. mediaTitle=${JSON.stringify(title||'')} visible=${JSON.stringify((lastTiles||[]).slice(0,8))}`);
}

async function openVideoEditForExtend(tabId,tile,projectId,tag=''){
  if(!tile?.href) throw new Error(`${tag} Video tile không có href edit.`);
  const url=new URL(tile.href,'https://labs.google').href;
  await appendLog(`TAB ${tag} EXTEND mở video → ${url}`,'info');
  await chrome.tabs.update(tabId,{url});
  await waitTabState(tabId,t=>t.status==='complete'&&String(t.url||'').includes('/edit/'),30000,'mở video editor');
  await sleep(700);
  await injectPage(tabId);
  const currentProject=projectIdFromFlowUrl((await chrome.tabs.get(tabId)).url||'');
  if(currentProject!==projectId) throw new Error(`${tag} Video editor sai project: ${currentProject||'-'} != ${projectId}`);
}

async function openExtendComposerTrusted(tabId,tag=''){
  let addPoint=null;
  for(let attempt=1;attempt<=10;attempt++){
    try{addPoint=await callPage(tabId,'getAddClipPoint',[]);break;}catch{await sleep(350);}
  }
  if(!addPoint) throw new Error(`${tag} Không tìm thấy Add Clip sau khi mở video.`);
  await trustedClickPoint(tabId,addPoint);
  await sleep(250);
  let extendPoint=null;
  for(let attempt=1;attempt<=8;attempt++){
    try{extendPoint=await callPage(tabId,'getExtendMenuPoint',[]);break;}catch{await sleep(250);}
  }
  if(!extendPoint) throw new Error(`${tag} Add Clip đã mở nhưng không thấy Extend.`);
  await trustedClickPoint(tabId,extendPoint);
  if(!await waitPageCondition(tabId,'isExtendComposerOpen',true,8000)) throw new Error(`${tag} Click Extend nhưng ô What happens next? không mở.`);
  return true;
}

async function createOneVideoExtension(tabId,record,options,limiter,round,totalRounds){
  const tag=`[${record.index+1}] EXT ${round}/${totalRounds}`;
  const prompt=formatVideoExtendPrompt(options.videoExtendPrompt,record,round);
  await openExtendComposerTrusted(tabId,tag);
  await callPage(tabId,'replaceExtendPrompt',[prompt]);
  await callPage(tabId,'waitCreateReady',[15000]);
  await limiter.waitTurn(`VIDEO EXTEND ${tag}`);
  const marker=createNetworkMarker(tabId);
  await appendLog(`TAB ${tag} CLICK CREATE EXTEND | prompt=${JSON.stringify(prompt)}`,'info');
  await trustedCreateClick(tabId);
  const requestMeta=await waitGenerationRequestStart(tabId,{
    kind:'VIDEO_CREATE',marker,projectId:options.projectId,prompt,referenceMediaId:null,
    timeoutMs:Math.min(options.videoTimeoutMs,45000),label:`VIDEO EXTEND POST ${tag}`
  });
  const created=await waitVideoCreateForRequest(tabId,{requestMeta,timeoutMs:Math.min(options.videoTimeoutMs,180000),tag});
  let assets=(created.videos||[]).filter(x=>x?.mediaId);
  let ids=assets.map(x=>x.mediaId);
  if(!ids.length){
    assets=await waitVideoAssetsFallbackConcurrent(tabId,{afterSeq:requestMeta.seq,projectId:options.projectId,prompt,timeoutMs:Math.min(options.videoTimeoutMs,180000),tag});
    ids=assets.map(x=>x.mediaId).filter(Boolean);
  }
  if(!ids.length) throw new Error(`${tag} Extend không trả video mediaId.`);
  await waitVideosSuccessful(tabId,{mediaIds:ids,marker:{seq:requestMeta.seq,at:Date.now()},projectId:options.projectId,timeoutMs:options.videoTimeoutMs,tag,workerLabel:'TAB',progressCtx:null});
  const chosen=assets[0]||{mediaId:ids[0],title:null};
  await appendLog(`TAB ${tag} EXTEND SUCCESS → mediaId=${chosen.mediaId} | title=${JSON.stringify(chosen.title||'')}`,'success');
  return chosen;
}

async function runVideoExtendPhase(tabId,records,options,limiter){
  if(!options.videoEnabled || options.videoExtendFactor<=1) return;
  const rounds=options.videoExtendFactor-1;
  const candidates=records.filter(r=>r.videoState==='SUCCESS'&&(r.videoIds||[]).length);
  if(!candidates.length) return;
  await appendLog(`PHASE VIDEO EXTEND: x${options.videoExtendFactor} → thêm ${rounds} clip/scene · chạy tuần tự để không xung đột Flow editor`,'info');
  for(const record of candidates){
    const tag=`[${record.index+1}/${records.length}]`;
    try{
      await patchJob(record.index,{done:false,videoState:'EXTENDING',videoExtendState:'PREPARING'});
      const baseId=record.videoIds[0];
      const baseAsset=(record.videoAssets||[]).find(x=>x.mediaId===baseId)||{mediaId:baseId,title:null};
      const tile=await locateExactVideoTile(tabId,{projectId:options.projectId,mediaId:baseId,title:baseAsset.title,timeoutMs:60000,tag});
      record.videoPrimaryTitle=tile.title||baseAsset.title||null;
      if(baseAsset) baseAsset.title=baseAsset.title||tile.title||null;
      await openVideoEditForExtend(tabId,tile,options.projectId,tag);
      record.videoChainMediaIds=[baseId];
      record.extendedVideoAssets=[];
      for(let round=1;round<=rounds;round++){
        const planned=record?.pair?.videoSegments?.[round];
        await appendLog(`TAB ${tag} EXTEND PLAN ${round}/${rounds} role=${planned?.role||'-'} → ${JSON.stringify(planned?.prompt||formatVideoExtendPrompt(options.videoExtendPrompt,record,round))}`,'info');
        const asset=await createOneVideoExtension(tabId,record,options,limiter,round,rounds);
        record.extendedVideoAssets.push(asset);
        record.videoChainMediaIds.push(asset.mediaId);
        if(!record.videoIds.includes(asset.mediaId)) record.videoIds.push(asset.mediaId);
        record.videoAssets=[...(record.videoAssets||[]),asset];
        const segmentPct=((round+1)/Math.max(1,options.videoExtendFactor))*100;
        await updateJobProgress({index:record.index,total:records.length,imageEnabled:options.imageEnabled,videoEnabled:true,stage:'VIDEO',stagePercent:segmentPct,workerLabel:'TAB',tag,detail:`EXTEND ${round}/${rounds} SUCCESS · role=${planned?.role||'-'}`,exact:true});
        await patchJob(record.index,{videoState:'EXTENDING',videoMediaIds:record.videoIds,videoChainMediaIds:record.videoChainMediaIds,videoExtendState:`${round}/${rounds}`,done:false});
      }
      await patchJob(record.index,{videoState:'SUCCESS',videoExtendState:'SUCCESS',videoChainMediaIds:record.videoChainMediaIds,videoMediaIds:record.videoIds,done:true});
      const chainServerJobId=String(record?.serverJobId||options.serverJobId||'').trim();
      if(chainServerJobId){
        sendServerMessage({type:'VIDEO_CHAIN_INFO',jobId:chainServerJobId,sceneId:record.sceneId??record.index+1,sceneIndex:record.serverSceneIndex??record.index,extendFactor:options.videoExtendFactor,mediaIds:record.videoChainMediaIds,titles:(record.videoAssets||[]).filter(a=>record.videoChainMediaIds.includes(a.mediaId)).map(a=>a.title||null)});
      }
      await appendLog(`TAB ${tag} VIDEO EXTEND CHAIN → ${record.videoChainMediaIds.join(' → ')}`,'success');
      await navigateProjectAllMedia(tabId,options.projectId);
    }catch(error){
      const text=error?.message||String(error);
      record.error=record.error||`VIDEO EXTEND: ${text}`;
      record.videoState='ERROR';
      await patchJob(record.index,{videoState:'ERROR',videoExtendState:'ERROR',videoExtendError:text,error:record.error,done:true});
      await appendLog(`TAB ${tag} ❌ VIDEO EXTEND: ${text}`,'error');
      await navigateProjectAllMedia(tabId,options.projectId).catch(()=>{});
      if(isQuotaLikeError(error)) throw error;
    }
  }
}

async function downloadVideosAfterExtend(records,options){
  if(!options.autoDownloadVideo || options.videoExtendFactor<=1) return;
  for(const record of records.filter(r=>r.videoState==='SUCCESS'&&(r.videoIds||[]).length)){
    const task=autoDownloadVideos(record,options)
      .then(downloads=>patchJob(record.index,{downloads,downloadState:'DONE'}))
      .catch(async error=>{await patchJob(record.index,{downloadState:'ERROR',downloadError:error?.message||String(error)});});
    if(record?.serverJobId||options.serverJobId){options.downloadTasks.push(task);await patchJob(record.index,{downloadState:'DOWNLOADING'});} else await task;
  }
}

function recordNeedsVideo(record,options){
  if(!options?.videoEnabled) return false;
  if(typeof record?.pair?.makeVideo==='boolean') return record.pair.makeVideo;
  if(typeof record?.pair?.metadata?.makeVideo==='boolean') return record.pair.metadata.makeVideo;
  if(typeof record?.pair?.metadata?.mixedMotion==='boolean') return record.pair.metadata.mixedMotion;
  return Boolean(String(record?.pair?.videoPrompt||'').trim());
}

function appendServerMessagesToRecords(messages,records,options){
  const added=[];
  for(const msg of (Array.isArray(messages)?messages:[])){
    const jobId=String(msg?.jobId||'').trim();
    if(!jobId) continue;
    const pairs=normalizeScenes(Array.isArray(msg?.scenes)?msg.scenes:[],options.imageEnabled,options.videoEnabled);
    for(let localIndex=0;localIndex<pairs.length;localIndex++){
      const pair=pairs[localIndex];
      const index=records.length;
      const record={
        index,
        serverJobId:jobId,
        serverSceneIndex:localIndex,
        serverKind:String(msg?.kind||''),
        sceneId:pair.sceneId??localIndex+1,
        pair,
        imageState:options.imageEnabled?'WAIT':'SKIP',
        videoState:(options.videoEnabled&&pair.makeVideo)?'WAIT':'SKIP',
        needsVideo:Boolean(options.videoEnabled&&pair.makeVideo),
        error:null,
        videoIds:[]
      };
      records.push(record);added.push(record);
    }
  }
  return added;
}

function isRetryableImageSceneError(error){
  const text=String(error?.message||error||'').toLowerCase();
  if(text.includes('server fail-safe')||text.includes('billing')||text.includes('insufficient')) return false;
  return true;
}

function isRetryableVideoSceneError(error){
  const text=String(error?.message||error||'').toLowerCase();
  if(text.includes('server fail-safe')||text.includes('billing')||text.includes('insufficient')) return false;
  return true;
}

async function recheckExistingVideoBeforeRegenerate(tabId,record,options,tag,total){
  const ids=[...new Set((record.videoIds||[]).filter(Boolean))];
  const req=record.videoRequestMeta;
  if(!ids.length||!req) return null;
  try{
    await appendLog(`TAB ${tag} VIDEO RETRY · recheck ${ids.length} mediaId cũ trước khi Create lại`,'info');
    const result=await waitVideosSuccessful(tabId,{
      mediaIds:ids,
      marker:{seq:Number(req.seq||0),at:Number(req.at||Date.now())},
      projectId:options.projectId,
      timeoutMs:VIDEO_EXISTING_STATUS_RECHECK_MS,
      tag,workerLabel:'TAB',
      progressCtx:{index:record.index,total,imageEnabled:options.imageEnabled,videoEnabled:true,workerLabel:'TAB',tag}
    });
    await appendLog(`TAB ${tag} VIDEO cũ đã SUCCESS khi recheck · KHÔNG generate lại`,'success');
    return {created:null,videoIds:ids,videoAssets:record.videoAssets||ids.map(mediaId=>({mediaId,title:null})),result,recoveredExisting:true};
  }catch{return null;}
}

async function runQueueScheduler(tabId,records,options,limiter,dynamicBatch=null){
  const totalNow=()=>records.length;
  const wake=createWakeSignal();
  if(dynamicBatch) dynamicBatch.wake=wake;
  const imagePending=records.filter(r=>options.imageEnabled).map(r=>r.index);
  const imageSubmitQueue=[];
  const videoReadyQueue=[];
  const videoSubmitQueue=[];
  const imageInFlight=new Map();
  const videoInFlight=new Map();
  let imageCursor=0;
  let enqueueSeq=0;
  let currentMode=null;
  let stopSubmitting=false;
  let stopReason='';

  if(!options.imageEnabled&&options.videoEnabled){
    for(const record of records){
      if(recordNeedsVideo(record,options)){record.videoState='READY';videoReadyQueue.push(record.index);}
      else {record.videoState='SKIP';await patchJob(record.index,{videoState:'SKIP',done:true});}
    }
  }

  const appendDynamicMessages=async()=>{
    if(stopSubmitting || flowUiCircuitOpen() || dynamicBatch?.blocked || dynamicBatch?.cancelled) return 0;
    if(!dynamicBatch?.take) return 0;
    const messages=dynamicBatch.take()||[];
    if(!messages.length) return 0;
    const added=appendServerMessagesToRecords(messages,records,options);
    for(const record of added){
      runtimeCache.jobs[record.index]={imageState:record.imageState,videoState:record.videoState,needsVideo:recordNeedsVideo(record,options),percent:0,done:(!options.imageEnabled&&record.videoState==='SKIP')};
      if(options.imageEnabled) imagePending.push(record.index);
      else if(options.videoEnabled&&recordNeedsVideo(record,options)){record.videoState='READY';videoReadyQueue.push(record.index);runtimeCache.jobs[record.index].videoState='READY';}
      else {record.videoState='SKIP';runtimeCache.jobs[record.index].videoState='SKIP';runtimeCache.jobs[record.index].done=true;}
    }
    await persistRuntime();
    await appendLog(`SERVER QUEUE APPEND · +${messages.length} job / +${added.length} scene · total=${totalNow()} · IMAGE cap=${options.imageConcurrency} · VIDEO cap=${options.videoConcurrency}`,'info');
    return added.length;
  };

  const refreshMetrics=async()=>{
    const {done,errors}=countFinishedJobs(totalNow());
    await setMetrics({
      imageInFlight:imageInFlight.size,videoInFlight:videoInFlight.size,
      submitImageQueued:imageSubmitQueue.length,submitVideoQueued:videoSubmitQueue.length,
      imageLimit:options.imageConcurrency,videoLimit:options.videoConcurrency,
      done,errors,total:totalNow()
    });
  };

  const setStopped=async(error)=>{
    if(stopSubmitting) return;
    stopSubmitting=true;
    stopReason=error?.message||String(error||'Đã dừng submit mới.');
    await appendLog(`⛔ DỪNG BATCH · lỗi thật: ${stopReason} · scene chưa submit sẽ CANCELLED, không tính là lỗi.`,'error');

    // One failing scene is ONE error. Remaining not-submitted scenes are cancellation,
    // otherwise 1 picker fault incorrectly becomes "10/10 scene lỗi".
    for(const task of [...imageSubmitQueue.splice(0),...videoSubmitQueue.splice(0)]){
      const record=records[task.index];
      record.cancelled=true;
      if(task.type==='IMAGE') record.imageState='CANCELLED'; else record.videoState='CANCELLED';
      await patchJob(record.index,{
        [task.type==='IMAGE'?'imageState':'videoState']:'CANCELLED',
        cancelled:true,
        cancelReason:stopReason,
        done:true
      });
    }
    for(;imageCursor<imagePending.length;imageCursor++){
      const record=records[imagePending[imageCursor]];
      if(record.imageState!=='WAIT') continue;
      record.cancelled=true;
      record.imageState='CANCELLED';
      await patchJob(record.index,{imageState:'CANCELLED',cancelled:true,cancelReason:stopReason,done:true});
    }
    await refreshMetrics();
    wake.notify();
  };

  const ensureMode=async(type)=>{
    if(currentMode===type) return;
    if(type==='IMAGE'){
      await appendLog('SUBMIT DISPATCHER → chuyển IMAGE + verify Settings','info');
      const settings=await configureStageSettings(tabId,{type:'IMAGE',aspectRatio:options.aspectRatio,outputs:options.imageOutputs,modelKind:'IMAGE',model:options.imageModel},3);
      await appendLog(`IMAGE SETTINGS VERIFIED (attempt ${settings.attempt}) → ${JSON.stringify(settings.verification.current)}`,'success');
    }else{
      await appendLog('SUBMIT DISPATCHER → chuyển VIDEO + INGREDIENTS + verify Settings','info');
      const settings=await configureStageSettings(tabId,{type:'VIDEO',...(options.imageEnabled?{videoMode:'INGREDIENTS'}:{}),aspectRatio:options.aspectRatio,duration:options.videoDuration,outputs:options.videoOutputs,modelKind:'VIDEO',model:options.videoModel},3);
      await appendLog(`VIDEO SETTINGS VERIFIED (attempt ${settings.attempt}) → ${JSON.stringify(settings.verification.current)}`,'success');
    }
    currentMode=type;
  };

  const fillImageQueue=()=>{
    if(!options.imageEnabled||stopSubmitting) return;
    let reserved=imageInFlight.size+imageSubmitQueue.length;
    while(reserved<options.imageConcurrency && imageCursor<imagePending.length){
      const index=imagePending[imageCursor++],record=records[index];
      if(record.imageState!=='WAIT') continue;
      record.imageState='QUEUED';
      imageSubmitQueue.push({type:'IMAGE',index,enqueuedSeq:++enqueueSeq,enqueuedAt:Date.now()});
      reserved++;
    }
  };

  const fillVideoQueue=()=>{
    if(!options.videoEnabled||stopSubmitting) return;
    let reserved=videoInFlight.size+videoSubmitQueue.length;
    while(reserved<options.videoConcurrency && videoReadyQueue.length){
      const index=videoReadyQueue.shift(),record=records[index];
      if(record.videoState!=='READY') continue;
      record.videoState='QUEUED';
      videoSubmitQueue.push({type:'VIDEO',index,enqueuedSeq:++enqueueSeq,enqueuedAt:Date.now()});
      reserved++;
    }
  };

  const pickSubmitTask=()=>{
    if(stopSubmitting) return null;
    const i=imageSubmitQueue[0]||null,v=videoSubmitQueue[0]||null;
    if(options.submitPolicy==='GLOBAL_FIFO'){
      if(i&&v) return i.enqueuedSeq<=v.enqueuedSeq?imageSubmitQueue.shift():videoSubmitQueue.shift();
      return i?imageSubmitQueue.shift():v?videoSubmitQueue.shift():null;
    }
    // FIFO is preserved inside each group. Video only gets light priority while one of its 3 slots is free.
    if(v && videoInFlight.size<options.videoConcurrency) return videoSubmitQueue.shift();
    if(i && imageInFlight.size<options.imageConcurrency) return imageSubmitQueue.shift();
    return null;
  };

  const finishImageLifecycle=(index,lifecycle)=>{
    const tracked=(async()=>{
      const record=records[index],tag=`[${index+1}/${totalNow()}]`;
      try{
        const value=await lifecycle;
        record.imageResult=value;record.selectedImage=value.images?.[0]||null;
        if(!record.selectedImage?.mediaId) throw new Error('IMAGE success nhưng mediaId rỗng');
        record.imageState='SUCCESS';record.imageCompletedAt=Date.now();
        await updateJobProgress({index,total:totalNow(),imageEnabled:true,videoEnabled:recordNeedsVideo(record,options),stage:'IMAGE',stagePercent:100,workerLabel:'TAB',tag,detail:`IMAGE SUCCESS → ${record.selectedImage.mediaId}`,exact:true});
        await patchJob(index,{imageState:'SUCCESS',imageMediaId:record.selectedImage.mediaId,imageSource:value.source});
        await appendLog(`TAB ${tag} IMAGE SUCCESS → ${record.selectedImage.mediaId} | source=${value.source}`,'success');
        if(recordNeedsVideo(record,options)){
          if(stopSubmitting){
            record.cancelled=true;
            record.videoState='CANCELLED';
            await patchJob(index,{videoState:'CANCELLED',cancelled:true,cancelReason:stopReason,done:true});
          }else{
            record.videoState='READY';videoReadyQueue.push(index);
            await patchJob(index,{videoState:'READY',needsVideo:true});
          }
        }else{
          record.videoState='SKIP';
          await patchJob(index,{videoState:'SKIP',needsVideo:false,done:true});
        }
      }catch(error){
        const text=error?.message||String(error);
        const retryCount=Number(record.imageRetryCount||0);
        const retryable=isRetryableImageSceneError(error);
        if(retryable && retryCount < IMAGE_SCENE_RETRY_MAX){
          const nextRetry=retryCount+1;
          const delay=Math.min(15000, 2500*(2**(nextRetry-1)));
          record.imageRetryCount=nextRetry;
          record.imageState='RETRY_WAIT';
          record.error=null;
          await patchJob(index,{imageState:'RETRY_WAIT',imageRetryCount:nextRetry,error:`AUTO IMAGE RETRY ${nextRetry}/${IMAGE_SCENE_RETRY_MAX} · ${text}`,done:false});
          await appendLog(`TAB ${tag} ♻️ IMAGE RETRY ${nextRetry}/${IMAGE_SCENE_RETRY_MAX} sau ${Math.round(delay/1000)}s · ${text}`,'info');
          await sleep(delay);
          if(!stopSubmitting && !dynamicBatch?.cancelled){
            record.imageMediaId=null;
            record.imageState='READY';
            record.error=null;
            imagePending.push(index);
            await patchJob(index,{imageState:'READY',error:null,done:false,imageRetryCount:nextRetry});
          }else{
            record.cancelled=true;
            record.imageState='CANCELLED';
            await patchJob(index,{imageState:'CANCELLED',cancelled:true,cancelReason:stopReason||'batch stopped',done:true});
          }
        }else{
          record.error=text;
          record.imageState='ERROR';
          await patchJob(index,{imageState:'ERROR',error:text,done:true,imageRetryCount:retryCount});
          await appendLog(`TAB ${tag} ❌ IMAGE hết retry (${retryCount}/${IMAGE_SCENE_RETRY_MAX}): ${text}`,'error');
        }
      }finally{
        imageInFlight.delete(index);await refreshMetrics();wake.notify();
      }
    })();
    imageInFlight.set(index,tracked);
  };

  const finishVideoLifecycle=(index,lifecycle)=>{
    const tracked=(async()=>{
      const record=records[index],tag=`[${index+1}/${totalNow()}]`;
      try{
        const value=await lifecycle;
        record.videoState='SUCCESS';record.videoResult=value;record.videoIds=value.videoIds||record.videoIds||[];record.videoAssets=value.videoAssets||record.videoAssets||[];record.videoChainMediaIds=record.videoIds.length?[record.videoIds[0]]:[];
        const basePct=100/Math.max(1,options.videoExtendFactor);
        await updateJobProgress({index,total:totalNow(),imageEnabled:options.imageEnabled,videoEnabled:true,stage:'VIDEO',stagePercent:basePct,workerLabel:'TAB',tag,detail:options.videoExtendFactor>1?`VIDEO BASE SUCCESS · 1/${options.videoExtendFactor}`:'VIDEO SUCCESS',exact:true});
        await patchJob(index,{videoState:options.videoExtendFactor>1?'BASE_SUCCESS':'SUCCESS',videoMediaIds:record.videoIds,videoAssets:(record.videoAssets||[]).map(x=>({mediaId:x.mediaId,title:x.title||null})),videoChainMediaIds:record.videoChainMediaIds,done:options.videoExtendFactor<=1});
        await appendLog(`TAB ${tag} ${options.videoExtendFactor>1?`VIDEO BASE SUCCESS · chờ ${options.videoExtendFactor-1} Extend`:'VIDEO SUCCESS'}`,'success');
        if(options.autoDownloadVideo && options.videoExtendFactor<=1){
          const task=autoDownloadVideos(record,options).then(async downloads=>{
            record.downloads=downloads;record.downloadState='DONE';record.downloadError=null;
            const expected=outputFactor(options.videoOutputs,4);
            if(downloads.length<expected){
              record.downloadState='ERROR';record.downloadError=`Tải local thiếu ${downloads.length}/${expected} video output`;
              await patchJob(index,{downloads,downloadState:'ERROR',downloadError:record.downloadError});
            }else await patchJob(index,{downloads,downloadState:'DONE'});
            return downloads;
          }).catch(async error=>{record.downloadState='ERROR';record.downloadError=error?.message||String(error);await patchJob(index,{downloadState:'ERROR',downloadError:record.downloadError});return [];});
          if(record?.serverJobId||options.serverJobId){options.downloadTasks.push(task);await patchJob(index,{downloadState:'DOWNLOADING'});}else await task;
        }
      }catch(error){
        const text=error?.message||String(error);
        const retryCount=Number(record.videoRetryCount||0);
        const retryable=isRetryableVideoSceneError(error);
        if(retryable && retryCount<VIDEO_SCENE_RETRY_MAX){
          const recovered=await recheckExistingVideoBeforeRegenerate(tabId,record,options,tag,totalNow());
          if(recovered){
            record.videoState='SUCCESS';record.videoResult=recovered;
            record.videoIds=recovered.videoIds||record.videoIds||[];
            record.videoAssets=recovered.videoAssets||record.videoAssets||[];
            record.error=null;
            await patchJob(index,{videoState:'SUCCESS',videoMediaIds:record.videoIds,videoAssets:(record.videoAssets||[]).map(x=>({mediaId:x.mediaId,title:x.title||null})),error:null,done:true,videoRetryCount:retryCount});
            await updateJobProgress({index,total:totalNow(),imageEnabled:options.imageEnabled,videoEnabled:true,stage:'VIDEO',stagePercent:100,workerLabel:'TAB',tag,detail:'VIDEO SUCCESS · recovered existing mediaId',exact:true});
          }else{
            const nextRetry=retryCount+1;
            const delay=Math.min(12000,1500*(2**(nextRetry-1)));
            record.videoRetryCount=nextRetry;record.videoState='RETRY_WAIT';record.error=null;
            await patchJob(index,{videoState:'RETRY_WAIT',videoRetryCount:nextRetry,error:`AUTO VIDEO RETRY ${nextRetry}/${VIDEO_SCENE_RETRY_MAX} · ${text}`,done:false});
            await appendLog(`TAB ${tag} ♻️ VIDEO RETRY ${nextRetry}/${VIDEO_SCENE_RETRY_MAX} sau ${Math.round(delay/1000)}s · ${text}`,'info');
            await sleep(delay);
            if(!stopSubmitting && !dynamicBatch?.cancelled){
              record.videoIds=[];record.videoAssets=[];record.videoRequestMeta=null;
              record.videoState='READY';record.error=null;
              videoReadyQueue.push(index);
              await patchJob(index,{videoState:'READY',error:null,done:false,videoRetryCount:nextRetry});
            }else{
              record.cancelled=true;record.videoState='CANCELLED';
              await patchJob(index,{videoState:'CANCELLED',cancelled:true,cancelReason:stopReason||'batch stopped',done:true});
            }
          }
        }else{
          record.error=text;record.videoState='ERROR';
          await patchJob(index,{videoState:'ERROR',error:text,done:true,videoRetryCount:retryCount});
          await appendLog(`TAB ${tag} ❌ VIDEO hết retry (${retryCount}/${VIDEO_SCENE_RETRY_MAX}): ${text}`,'error');
          if(isQuotaLikeError(error)) await setStopped(error);
        }
      }finally{
        videoInFlight.delete(index);await refreshMetrics();wake.notify();
      }
    })();
    videoInFlight.set(index,tracked);
  };

  const submitTask=async task=>{
    const record=records[task.index],tag=`[${task.index+1}/${totalNow()}]`;
    try{
      assertServerAutomationAllowed(`submit ${task.type}`);
      if(dynamicBatch?.cancelled) throw new Error(`SERVER FAIL-SAFE STOP · ${dynamicBatch.cancelReason||'cancelled'}`);
      if(task.type==='IMAGE'){
        await ensureMode('IMAGE');
        await appendLog(`SUBMIT QUEUE → IMAGE ${tag} | FIFO#${task.enqueuedSeq}`,'info');
        const started=await startImageJob(tabId,record,options,limiter,totalNow());
        finishImageLifecycle(task.index,started.lifecycle);
      }else{
        await ensureMode('VIDEO');
        record.error=null;
        await patchJob(task.index,{videoState:'PREPARING',error:null,done:false,videoRetryCount:Number(record.videoRetryCount||0)});
        await appendLog(`SUBMIT QUEUE → VIDEO ${tag} | FIFO#${task.enqueuedSeq} · retry=${Number(record.videoRetryCount||0)}/${VIDEO_SCENE_RETRY_MAX}`,'info');
        const started=await startVideoJob(tabId,record,options,limiter,totalNow());
        finishVideoLifecycle(task.index,started.lifecycle);
      }
    }catch(error){
      const text=error?.message||String(error);record.error=text;
      if(task.type==='IMAGE'){
        const retryCount=Number(record.imageRetryCount||0);
        if(isRetryableImageSceneError(error) && retryCount < IMAGE_SCENE_RETRY_MAX){
          const nextRetry=retryCount+1;
          record.imageRetryCount=nextRetry;
          record.imageState='RETRY_WAIT';
          record.error=null;
          await patchJob(task.index,{imageState:'RETRY_WAIT',imageRetryCount:nextRetry,error:`AUTO IMAGE RETRY ${nextRetry}/${IMAGE_SCENE_RETRY_MAX} · ${text}`,done:false});
          await appendLog(`TAB ${tag} ♻️ IMAGE PREP RETRY ${nextRetry}/${IMAGE_SCENE_RETRY_MAX} sau 3s · ${text}`,'info');
          setTimeout(()=>{
            if(!stopSubmitting && !dynamicBatch?.cancelled){
              record.imageMediaId=null;
              record.imageState='READY';
              imagePending.push(task.index);
              wake.notify();
            }
          }, 3000);
        }else{
          record.imageState='ERROR';await patchJob(task.index,{imageState:'ERROR',error:text,done:true,imageRetryCount:retryCount});
        }
      }else{
        const retryCount=Number(record.videoRetryCount||0);
        if(isRetryableVideoSceneError(error) && retryCount < VIDEO_SCENE_RETRY_MAX){
          const nextRetry=retryCount+1;
          record.videoRetryCount=nextRetry;
          record.videoState='RETRY_WAIT';
          record.error=null;
          await patchJob(task.index,{videoState:'RETRY_WAIT',videoRetryCount:nextRetry,error:`AUTO VIDEO RETRY ${nextRetry}/${VIDEO_SCENE_RETRY_MAX} · ${text}`,done:false});
          await appendLog(`TAB ${tag} ♻️ VIDEO PREP RETRY ${nextRetry}/${VIDEO_SCENE_RETRY_MAX} sau 3s · ${text}`,'info');
          setTimeout(()=>{
            if(!stopSubmitting && !dynamicBatch?.cancelled){
              record.videoState='READY';
              videoReadyQueue.push(task.index);
              wake.notify();
            }
          }, 3000);
        }else{
          record.videoState='ERROR';await patchJob(task.index,{videoState:'ERROR',error:text,done:true,videoRetryCount:retryCount});
        }
      }
      await appendLog(`TAB ${tag} ⚠️ ${task.type} submit lỗi: ${text}`,'warning');
      if(isFatalFlowUiError(error)){
        tripFlowUiCircuit(error);
        if(dynamicBatch){dynamicBatch.blocked=true;dynamicBatch.blockReason=text;}
        await appendLog(`⛔ FLOW UI CIRCUIT BREAKER · lỗi UI/protocol toàn cục · cooldown 90s · ${text}`,'error');
        await setStopped(error);
      }
    }finally{await refreshMetrics();wake.notify();}
  };

  await appendLog(`QUEUE SCHEDULER: IMAGE max=${options.imageConcurrency} · VIDEO max=${options.videoConcurrency} · policy=${options.submitPolicy==='GLOBAL_FIFO'?'FIFO tổng':'FIFO nhóm + ưu tiên video nhẹ'} · autoDownload=${options.autoDownloadVideo?'ON':'OFF'}`,'info');

  while(true){
    if(dynamicBatch?.cancelled && !stopSubmitting) await setStopped(new Error(`SERVER FAIL-SAFE STOP · ${dynamicBatch.cancelReason||'cancelled'}`));
    await appendDynamicMessages();
    fillImageQueue();fillVideoQueue();await refreshMetrics();
    const {done}=countFinishedJobs(totalNow());
    const noPendingImages=imageCursor>=imagePending.length;
    const noQueues=!imageSubmitQueue.length&&!videoSubmitQueue.length&&!videoReadyQueue.length;
    const noInflight=!imageInFlight.size&&!videoInFlight.size;
    if(done>=totalNow() && noQueues && noInflight){
      // Keep the active signature open briefly. Jobs arriving while slots are free are
      // appended directly into this scheduler instead of waiting for the whole batch.
      await wake.wait(350);
      const appended=await appendDynamicMessages();
      if(appended) continue;
      break;
    }
    if(stopSubmitting && noInflight){
      // Mark any video-ready records that became ready after stop.
      for(const index of videoReadyQueue.splice(0)){
        const record=records[index];
        record.cancelled=true;
        record.videoState='CANCELLED';
        await patchJob(index,{videoState:'CANCELLED',cancelled:true,cancelReason:stopReason,done:true});
      }
      if(noPendingImages || imageCursor>=imagePending.length){
        const c=countFinishedJobs(totalNow());if(c.done>=totalNow()) break;
      }
    }
    const task=pickSubmitTask();
    if(task){await submitTask(task);continue;}
    await wake.wait(250);
  }

  await Promise.allSettled([...imageInFlight.values(),...videoInFlight.values()]);
  await refreshMetrics();
}

async function runAutomation(message){
  assertServerAutomationAllowed('run automation');
  const tabId=message.tabId;
  if(!Number.isInteger(tabId)) throw new Error('Không xác định được tab hiện tại.');

  const imageModel=String(message.options?.imageModel||'').trim();
  const videoModel=String(message.options?.videoModel||'').trim();
  const imageEnabled=imageModel.toUpperCase()!=='NONE';
  const videoEnabled=videoModel.toUpperCase()!=='NONE';
  if(!imageEnabled&&!videoEnabled) throw new Error('Image model và Video model không thể cùng là None.');
  const serverMessages=Array.isArray(message.serverJobMessages)?message.serverJobMessages:[];
  const pairs=serverMessages.length?[]:(Array.isArray(message.scenes)&&message.scenes.length
    ? normalizeScenes(message.scenes,imageEnabled,videoEnabled)
    : parsePairs(message.pairs,imageEnabled,videoEnabled));
  if(!serverMessages.length&&!pairs.length) throw new Error('Chưa có prompt để chạy.');

  await ensureFlowToolLoaded(tabId);
  await injectPage(tabId);
  await attachWorkerDebugger(tabId);
  try{
    const projectId=await ensureProjectAndAllMedia(tabId);
    const options={
      projectId,imageModel,videoModel,imageEnabled,videoEnabled,
      aspectRatio:message.options?.aspectRatio||'16:9',
      imageOutputs:message.options?.imageOutputs||'x1',
      videoDuration:message.options?.videoDuration||'8s',
      videoOutputs:message.options?.videoOutputs||'x1',
      videoExtendFactor:Math.max(1,Math.min(4,Number(String(message.options?.videoExtendFactor||'x1').replace(/^x/i,''))||1)),
      videoExtendPrompt:String(message.options?.videoExtendPrompt||'Continue naturally from the previous shot. Keep the same person, product, lighting, location and camera style. Add new natural product-review motion and angles. Do not repeat the opening shot or redesign the product.').trim(),
      imageTimeoutMs:Number(message.options?.imageTimeoutSec||300)*1000,
      videoTimeoutMs:Number(message.options?.videoTimeoutSec||900)*1000,
      imageConcurrency:Math.max(1,Math.min(10,Number(message.options?.imageConcurrency||9))),
      videoConcurrency:Math.max(1,Math.min(10,Number(message.options?.videoConcurrency||4))),
      submitPolicy:String(message.options?.submitPolicy||'VIDEO_LIGHT')==='GLOBAL_FIFO'?'GLOBAL_FIFO':'VIDEO_LIGHT',
      autoDownloadVideo:Boolean(message.options?.autoDownloadVideo),
      serverJobId:message.serverJobId||null,
      downloadTasks:[],
      maxSubmitsPerMinute:Math.max(0,Math.min(60,Number(message.options?.maxSubmitsPerMinute ?? 6))),
      submitGapMs:Math.max(0,Math.min(60000,Number(message.options?.submitGapMs ?? 1000))),
      assetCache:GLOBAL_ASSET_CACHE
    };

    const records=[];
    if(serverMessages.length) appendServerMessagesToRecords(serverMessages,records,{imageEnabled,videoEnabled});
    else records.push(...pairs.map((pair,index)=>({index,sceneId:pair.sceneId??index+1,pair,imageState:imageEnabled?'WAIT':'SKIP',videoState:(videoEnabled&&pair.makeVideo)?'WAIT':'SKIP',needsVideo:Boolean(videoEnabled&&pair.makeVideo),error:null,videoIds:[]})));
    runtimeCache.jobs=Object.fromEntries(records.map(r=>[r.index,{imageState:r.imageState,videoState:r.videoState,needsVideo:recordNeedsVideo(r,options),percent:0,done:(!imageEnabled&&r.videoState==='SKIP')}])) ;
    runtimeCache.metrics={imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,imageLimit:options.imageConcurrency,videoLimit:options.videoConcurrency,done:0,errors:0,total:records.length};
    await persistRuntime();

    await appendLog(`1 TAB / ${records.length} job | IMAGE max=${options.imageConcurrency} | VIDEO max=${options.videoConcurrency} | Extend=x${options.videoExtendFactor} | Submit=${options.submitPolicy==='GLOBAL_FIFO'?'FIFO tổng':'FIFO nhóm + ưu tiên video nhẹ'} | Auto download=${options.autoDownloadVideo?'ON':'OFF'}`,'info');
    await appendLog(`project=${projectId} | view=All Media | 1 submit dispatcher duy nhất → Settings/Prompt/Asset Picker/Create không chạy chồng nhau`,'info');
    await setProgress(0,'Queue Scheduler · chuẩn bị',`Tổng ${records.length} job`);

    const limiter=createSubmitLimiter({maxPerMinute:options.maxSubmitsPerMinute,minGapMs:options.submitGapMs});
    await runQueueScheduler(tabId,records,options,limiter,message.serverDynamicBatch||null);
    await runVideoExtendPhase(tabId,records,options,limiter);
    await downloadVideosAfterExtend(records,options);
    if(options.downloadTasks.length){
      await appendLog(`Chờ ${options.downloadTasks.length} tác vụ download của Server hoàn tất...`,'info');
      await Promise.allSettled(options.downloadTasks);
    }
    const {done,errors}=countFinishedJobs(records.length);
    const cancelled=records.filter(r=>r.cancelled).length;
    const failures=records.filter(r=>r.error).map(r=>({index:r.index,pair:r.pair,error:r.error}));
    const success=Math.max(0,records.length-failures.length-cancelled);
    await setMetrics({imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,imageLimit:options.imageConcurrency,videoLimit:options.videoConcurrency,done,errors,total:records.length,cancelled});
    if(failures.length) await appendLog(`Queue batch dừng: ${success} thành công · ${failures.length} lỗi thật · ${cancelled} CANCELLED.`,'error');
    else await appendLog(`Queue batch hoàn tất ${records.length}/${records.length} job.`,'success');
    return {results:records,failures,cancelled};
  }finally{
    await detachWorkerDebugger(tabId);
  }
}

chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.type==='FLOW_RUN_PAIRS'){
    (async()=>{
      const runId=`run_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
      await resetRuntimeForRun(runId);
      await appendLog(`Bắt đầu Flow Wardrobe Studio v${EXTENSION_VERSION} Queue Scheduler...`,'info');
      const batch=await runAutomation(message);
      const failures=batch?.failures||[];
      if(failures.length){
        await finishRuntime(false,`Batch xong nhưng có ${failures.length} job lỗi.`);
        return {ok:false,error:`Có ${failures.length} job lỗi.`,results:batch.results,failures};
      }
      await finishRuntime(true,`Hoàn tất ${batch?.results?.length||0} job.`);
      return {ok:true,results:batch.results};
    })().then(sendResponse).catch(async error=>{
      const text=error?.message||String(error);
      await appendLog(`❌ ${text}`,'error');
      await finishRuntime(false,text);
      sendResponse({ok:false,error:text});
    });
    return true;
  }
  if(message?.type==='FLOW_GET_RUNTIME'){
    sendResponse({ok:true,runtime:runtimeCache});
    return true;
  }
  if(message?.type==='FLOW_GET_SERVER_STATUS'){
    chrome.storage.local.get('flowPairAutoServerStatus').then(v=>sendResponse({ok:true,status:v.flowPairAutoServerStatus||{connected:false}}));
    return true;
  }
  if(message?.type==='FLOW_SERVER_RECONNECT'){
    connectServerBridge(true).then(()=>sendResponse({ok:true})).catch(error=>sendResponse({ok:false,error:error?.message||String(error)}));
    return true;
  }
});

async function checkShopeeSessionHealth(){
  try{
    let hasCookie = false;
    let cookieUser = null;
    try {
      if (chrome.cookies?.get) {
        const c1 = await chrome.cookies.get({ url: 'https://affiliate.shopee.vn', name: 'SPC_EC' }).catch(()=>null);
        const c2 = await chrome.cookies.get({ url: 'https://affiliate.shopee.vn', name: 'SPC_U' }).catch(()=>null);
        const c3 = await chrome.cookies.get({ url: 'https://shopee.vn', name: 'SPC_EC' }).catch(()=>null);
        hasCookie = Boolean((c1 && c1.value) || (c2 && c2.value) || (c3 && c3.value));
        cookieUser = c2?.value || null;
      }
    } catch {}
    const tabs = await chrome.tabs.query({url:['*://affiliate.shopee.vn/*','*://shopee.vn/*']}).catch(()=>[]);
    const loggedIn = hasCookie || tabs.length > 0;
    return {
      ok: true,
      loggedIn,
      hasCookie,
      cookieUser,
      openTabs: tabs.length
    };
  }catch(error){
    return {ok:false,loggedIn:false,error:error?.message||String(error)};
  }
}

try{
  chrome.alarms?.create?.('flowWorkerKeepAliveAlarm',{periodInMinutes:0.25});
  chrome.alarms?.onAlarm?.addListener?.(alarm=>{
    if(alarm?.name==='flowWorkerKeepAliveAlarm'){
      try{
        if(serverRunPromise||activeServerBatch){
          chrome.power?.requestKeepAwake?.('display');
        }else{
          chrome.power?.releaseKeepAwake?.();
        }
      }catch{}
    }
  });
}catch{}
