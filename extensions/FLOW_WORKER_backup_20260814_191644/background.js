const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const COMPATIBLE_PAGE_FAMILY = EXTENSION_VERSION.split('.').slice(0,2).join('.');
function isCompatiblePageVersion(version){
  const v=String(version||'').trim();
  return v === EXTENSION_VERSION || v.startsWith(COMPATIBLE_PAGE_FAMILY + '.');
}


// ======================= Local Orchestrator Bridge (v14) =======================
let serverSocket = null;
let serverReconnectTimer = null;
let serverHeartbeatTimer = null;
let serverRunPromise = null;
const serverJobQueue = [];
const serverAcceptedJobIds = new Set();
let activeServerBatch = null;
// v14.6.5: serialize DOWNLOAD_MEDIA_FILES. Recovery messages used to run concurrently
// for scene 1/2/3/4, causing interleaved resolver storms and making retry state opaque.
let serverVideoRecoveryChain = Promise.resolve();
// v14.6.0 fail-closed server control. A server-driven browser run is allowed
// only while the control websocket is alive. If the server stops/crashes, abort
// the active scheduler, clear queued server jobs, detach debugger and close only
// temporary Shopee tabs opened by the server.
let serverControlledRun = {active:false,aborted:false,reason:'',tabId:null};
let serverFailClosed = true; // no server session = no browser automation
const serverTemporaryTabs = new Set();

function serverControlError(){
  return new Error(serverControlledRun.reason || 'Parenting server mÃƒÂ¡Ã‚ÂºÃ‚Â¥t kÃƒÂ¡Ã‚ÂºÃ‚Â¿t nÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi; extension Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng thao tÃƒÆ’Ã‚Â¡c browser.');
}
function assertServerControlAlive(){
  if(serverFailClosed) throw serverControlError();
  if(serverControlledRun.active && (serverControlledRun.aborted || serverSocket?.readyState!==WebSocket.OPEN)) throw serverControlError();
  return true;
}
async function abortServerControlledWork(reason='Parenting server mÃƒÂ¡Ã‚ÂºÃ‚Â¥t kÃƒÂ¡Ã‚ÂºÃ‚Â¿t nÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi'){
  const text=String(reason||'Parenting server mÃƒÂ¡Ã‚ÂºÃ‚Â¥t kÃƒÂ¡Ã‚ÂºÃ‚Â¿t nÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi');
  serverFailClosed=true;
  const hadWork=serverControlledRun.active || !!activeServerBatch || serverJobQueue.length>0 || serverTemporaryTabs.size>0;
  if(serverControlledRun.active){serverControlledRun.aborted=true;serverControlledRun.reason=text;}
  if(activeServerBatch){activeServerBatch.aborted=true;activeServerBatch.abortReason=text;activeServerBatch.wake?.notify?.();}
  serverJobQueue.splice(0,serverJobQueue.length);
  serverAcceptedJobIds.clear();
  for(const tabId of [...serverTemporaryTabs]){
    serverTemporaryTabs.delete(tabId);
    try{await chrome.tabs.remove(tabId);}catch{}
  }
  const tabId=serverControlledRun.tabId;
  if(Number.isInteger(tabId)){try{await detachWorkerDebugger(tabId);}catch{}}
  if(hadWork){
    try{
      runtimeCache.running=false;
      runtimeCache.lastLevel='error';
      runtimeCache.progressLabel='Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng theo server';
      runtimeCache.progressDetail=text;
      runtimeCache.logs=[...(runtimeCache.logs||[]),{time:new Date().toLocaleTimeString(),text:`ÃƒÂ¢Ã¢â‚¬ÂºÃ¢â‚¬Â ${text} Ãƒâ€šÃ‚Â· Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng mÃƒÂ¡Ã‚Â»Ã‚Âi thao tÃƒÆ’Ã‚Â¡c browser cÃƒÂ¡Ã‚Â»Ã‚Â§a server`,level:'error'}].slice(-200);
      await persistRuntime();
    }catch{}
  }
}
const GLOBAL_ASSET_CACHE = new Map(); // persist Flow mediaId by local path across server jobs
// v14.6.0: Flow may hide old library assets while the server still stores their mediaId.
// Never spend 60s probing the same stale id on every scene. Once an exact id fails,
// mark it stale for this extension session; if a stable local file exists, upload it again.
const STALE_SERVER_MEDIA_IDS = new Set();
const SERVER_MEDIA_REPLACEMENTS = new Map(); // old mediaId -> newly uploaded mediaId

function referenceSuppliedMediaId(input){
  return String(input?.mediaId||input?.media_id||'').trim();
}
function referenceEffectiveMediaId(input){
  const oldId=referenceSuppliedMediaId(input);
  if(!oldId) return '';
  return String(SERVER_MEDIA_REPLACEMENTS.get(oldId)||oldId);
}

function referenceKnownMediaIds(input,options={}){
  const ids=[];
  const supplied=referenceSuppliedMediaId(input);
  const effective=referenceEffectiveMediaId(input);
  if(effective) ids.push(effective);
  if(supplied && supplied!==effective) ids.push(supplied);
  const path=String(input?.path||'').trim();
  if(path){
    const cache=options?.assetCache instanceof Map?options.assetCache:null;
    const hit=cache?.get(path)||GLOBAL_ASSET_CACHE.get(path)||null;
    if(hit?.mediaId && !String(hit.mediaId).startsWith('composer:')) ids.push(String(hit.mediaId));
  }
  return [...new Set(ids.filter(Boolean))];
}
async function getComposerMediaStateSafe(tabId){
  return await callPage(tabId,'getComposerMediaState',[]).catch(()=>({count:0,mediaIds:[],items:[]}));
}
function composerHasMediaIdState(state,id){
  const wanted=String(id||'').trim();
  if(!wanted) return false;
  return (state?.mediaIds||[]).includes(wanted) || (state?.items||[]).some(x=>String(x?.mediaId||'')===wanted);
}
async function composerHasMediaId(tabId,id){
  return composerHasMediaIdState(await getComposerMediaStateSafe(tabId),id);
}
function markReferenceMediaReplaced(input,oldId,newId){
  oldId=String(oldId||'').trim(); newId=String(newId||'').trim();
  if(!oldId||!newId||oldId===newId||newId.startsWith('composer:')) return;
  STALE_SERVER_MEDIA_IDS.add(oldId);
  SERVER_MEDIA_REPLACEMENTS.set(oldId,newId);
  sendServerMessage({
    type:'REFERENCE_MEDIA_REPLACED',
    role:String(input?.role||'reference'),
    characterId:String(input?.characterId||''),
    oldMediaId:oldId,newMediaId:newId,
    fileName:String(input?.fileName||input?.name||'').trim(),
    name:String(input?.name||'').trim(),
    title:String(input?.title||'').trim(),
    path:String(input?.path||'').trim()
  });
}
const DEFAULT_SERVER_URL = 'ws://127.0.0.1:3000/ws/flow';
const V28_BUILD_ID = '2.8.5.4';

async function getServerBridgeConfig(){
  // V2.8 is hard-isolated to port 3000. Do not inspect/migrate/connect 8786/8787/8897;
  // those ports belong to other Edge/extension stacks on this machine.
  try{
    const {flowPairAutoForm={}}=await chrome.storage.local.get('flowPairAutoForm');
    if(String(flowPairAutoForm.serverUrl||'')!==DEFAULT_SERVER_URL){
      const next={...flowPairAutoForm,serverEnabled:flowPairAutoForm.serverEnabled!==false,serverUrl:DEFAULT_SERVER_URL};
      await chrome.storage.local.set({flowPairAutoForm:next}).catch(()=>{});
    }
    return {enabled: flowPairAutoForm.serverEnabled !== false, url: DEFAULT_SERVER_URL};
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

function interruptServerJobsBeforeStop(reason='Parenting server Ãƒâ€žÃ¢â‚¬Ëœang dÃƒÂ¡Ã‚Â»Ã‚Â«ng'){
  const ids=new Set();
  for(const m of serverJobQueue) if(m?.jobId) ids.add(String(m.jobId));
  for(const m of (activeServerBatch?.jobs?.values?.()||[])) if(m?.jobId) ids.add(String(m.jobId));
  if(runtimeCache?.serverJobId) ids.add(String(runtimeCache.serverJobId));
  for(const jobId of ids){
    sendServerMessage({type:'FLOW_JOB_INTERRUPTED',jobId,error:String(reason),retryable:true,controlledStop:true});
  }
  return ids.size;
}

function stopServerHeartbeat(){
  if(serverHeartbeatTimer){clearInterval(serverHeartbeatTimer);serverHeartbeatTimer=null;}
}
function startServerHeartbeat(){
  stopServerHeartbeat();
  serverHeartbeatTimer=setInterval(()=>{
    if(serverSocket?.readyState===WebSocket.OPEN){
      sendServerMessage({type:'AGENT_HEARTBEAT',role:'flow-extension',extensionId:chrome.runtime.id,version:EXTENSION_VERSION,runtime:runtimeCache,serverQueueDepth:serverJobQueue.length,activeServerBatch:!!activeServerBatch});
    }else{
      connectServerBridge(false).catch(()=>{});
    }
  },20000);
}

async function disconnectServerBridge(reason='disabled'){
  clearTimeout(serverReconnectTimer); serverReconnectTimer=null;
  stopServerHeartbeat();
  await abortServerControlledWork(reason==='disabled'?'Server bridge bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ tÃƒÂ¡Ã‚ÂºÃ‚Â¯t':reason);
  const ws=serverSocket; serverSocket=null;
  try{ws?.close(1000,reason);}catch{}
  await setServerStatus({connected:false,lastError:reason==='disabled'?null:reason});
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
    if(t==='shopee'||t==='shopee viÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡t nam'||t==='shopee vietnam'||t==='shopee.vn') return true;
    if(t.includes('shopee')&&(t.includes('hot deal')||t.includes('best price')||t.includes('mua sÃƒÂ¡Ã‚ÂºÃ‚Â¯m')||t.includes('vietnam')||t.includes('viÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡t nam'))) return true;
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
    ['mÃƒÆ’Ã‚Â´ tÃƒÂ¡Ã‚ÂºÃ‚Â£ sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m','mÃƒÆ’Ã‚Â´ tÃƒÂ¡Ã‚ÂºÃ‚Â£ chi tiÃƒÂ¡Ã‚ÂºÃ‚Â¿t','thÃƒÆ’Ã‚Â´ng tin sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m','chi tiÃƒÂ¡Ã‚ÂºÃ‚Â¿t sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m'],
    ['Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â¡nh giÃƒÆ’Ã‚Â¡ sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m','Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â¡nh giÃƒÆ’Ã‚Â¡ tÃƒÂ¡Ã‚Â»Ã‚Â« ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âi mua','sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m tÃƒâ€ Ã‚Â°Ãƒâ€ Ã‚Â¡ng tÃƒÂ¡Ã‚Â»Ã‚Â±','cÃƒÆ’Ã‚Â³ thÃƒÂ¡Ã‚Â»Ã†â€™ bÃƒÂ¡Ã‚ÂºÃ‚Â¡n cÃƒâ€¦Ã‚Â©ng thÃƒÆ’Ã‚Â­ch','bÃƒÆ’Ã‚Â¬nh luÃƒÂ¡Ã‚ÂºÃ‚Â­n']
  ));
  const detailText=descriptionBlocks.sort((a,b)=>b.length-a.length)[0]||'';
  const metaDescription=String(productLd.description||meta('og:description')||meta('description','name')||'').replace(/\s+/g,' ').trim();
  const description=(detailText||metaDescription).slice(0,12000);

  const specCandidates=[];
  const specSection=sectionFromBody(
    ['chi tiÃƒÂ¡Ã‚ÂºÃ‚Â¿t sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m','thÃƒÆ’Ã‚Â´ng tin chi tiÃƒÂ¡Ã‚ÂºÃ‚Â¿t','thÃƒÆ’Ã‚Â´ng sÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœ sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m','thuÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢c tÃƒÆ’Ã‚Â­nh sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m'],
    ['mÃƒÆ’Ã‚Â´ tÃƒÂ¡Ã‚ÂºÃ‚Â£ sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m','Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â¡nh giÃƒÆ’Ã‚Â¡ sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m','sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m tÃƒâ€ Ã‚Â°Ãƒâ€ Ã‚Â¡ng tÃƒÂ¡Ã‚Â»Ã‚Â±','cÃƒÆ’Ã‚Â³ thÃƒÂ¡Ã‚Â»Ã†â€™ bÃƒÂ¡Ã‚ÂºÃ‚Â¡n cÃƒâ€¦Ã‚Â©ng thÃƒÆ’Ã‚Â­ch']
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
    const m=bodyText.match(/(?:ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â«|Ãƒâ€žÃ¢â‚¬Ëœ)\s?([0-9][0-9.,]{2,})|([0-9][0-9.,]{2,})\s?(?:ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â«|Ãƒâ€žÃ¢â‚¬Ëœ)/i);
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
    if(currentUrl.startsWith('chrome-error://')) throw new Error('Chrome khÃƒÆ’Ã‚Â´ng tÃƒÂ¡Ã‚ÂºÃ‚Â£i Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c trang Shopee (chrome-error).');
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
  throw new Error(lastError||'Shopee Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ mÃƒÂ¡Ã‚Â»Ã…Â¸ nhÃƒâ€ Ã‚Â°ng chÃƒâ€ Ã‚Â°a render Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c dÃƒÂ¡Ã‚Â»Ã‚Â¯ liÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m.');
}

async function inspectShopeeProductForServer(message){
  assertServerControlAlive();
  const requestId=String(message?.requestId||'').trim();
  const url=String(message?.url||'').trim();
  if(!requestId||!url) throw new Error('SHOPEE_INSPECT_PRODUCT thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u requestId/url');
  if(!isAllowedShopeeUrl(url)) throw new Error('ChÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° cho phÃƒÆ’Ã‚Â©p link Shopee HTTPS (shopee.vn / shope.ee).');
  let tab=null; let oldActive=null;
  try{
    oldActive=(await chrome.tabs.query({active:true,currentWindow:true}).catch(()=>[]))?.[0]||null;
    assertServerControlAlive();
    tab=await chrome.tabs.create({url,active:false});
    if(!Number.isInteger(tab?.id)) throw new Error('KhÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tab Shopee.');
    serverTemporaryTabs.add(tab.id);
    await waitTabLoaded(tab.id,20000);
    assertServerControlAlive();
    await sleep(900);
    assertServerControlAlive();
    let product=null;
    try{
      product=await extractShopeeCaptureFromTab(tab.id,15000);
    }catch(firstError){
      // Some Shopee builds defer rendering in a background tab. Activate only as
      // a recovery path, then restore the user's previous tab.
      await appendLog(`SHOPEE INSPECT RETRY ACTIVE TAB Ãƒâ€šÃ‚Â· ${firstError?.message||firstError}`,'warn');
      await chrome.tabs.update(tab.id,{active:true}).catch(()=>{});
      await sleep(1200);
      product=await extractShopeeCaptureFromTab(tab.id,9000);
    }
    if(!product) throw new Error('KhÃƒÆ’Ã‚Â´ng Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã‚Âc Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c DOM sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m Shopee.');
    const finalUrl=String(product.finalUrl||'');
    const lowBody=String(product.bodyText||'').toLowerCase();
    if(/\/buyer\/login|captcha|verify/i.test(finalUrl) || (lowBody.includes('Ãƒâ€žÃ¢â‚¬ËœÃƒâ€žÃ†â€™ng nhÃƒÂ¡Ã‚ÂºÃ‚Â­p') && String(product.title||'').trim()==='' && (product.images||[]).length===0)){
      throw new Error('Shopee Ãƒâ€žÃ¢â‚¬Ëœang yÃƒÆ’Ã‚Âªu cÃƒÂ¡Ã‚ÂºÃ‚Â§u Ãƒâ€žÃ¢â‚¬ËœÃƒâ€žÃ†â€™ng nhÃƒÂ¡Ã‚ÂºÃ‚Â­p/xÃƒÆ’Ã‚Â¡c minh. MÃƒÂ¡Ã‚Â»Ã…Â¸ link bÃƒÂ¡Ã‚ÂºÃ‚Â±ng Chrome, hoÃƒÆ’Ã‚Â n tÃƒÂ¡Ã‚ÂºÃ‚Â¥t xÃƒÆ’Ã‚Â¡c minh rÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i bÃƒÂ¡Ã‚ÂºÃ‚Â¥m Ãƒâ€žÃ‚ÂÃƒÂ¡Ã‚Â»Ã…â€™C SP lÃƒÂ¡Ã‚ÂºÃ‚Â¡i.');
    }
    if(!sendServerMessage({type:'SHOPEE_PRODUCT_RESULT',requestId,ok:true,product})) throw new Error('MÃƒÂ¡Ã‚ÂºÃ‚Â¥t kÃƒÂ¡Ã‚ÂºÃ‚Â¿t nÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi server trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc khi gÃƒÂ¡Ã‚Â»Ã‚Â­i kÃƒÂ¡Ã‚ÂºÃ‚Â¿t quÃƒÂ¡Ã‚ÂºÃ‚Â£ Shopee.');
    await appendLog(`SHOPEE INSPECT OK Ãƒâ€šÃ‚Â· ${String(product.title||product.pageTitle||'product').slice(0,70)} Ãƒâ€šÃ‚Â· images=${(product.images||[]).length}`,'success');
  }catch(error){
    const msg=error?.message||String(error);
    sendServerMessage({type:'SHOPEE_PRODUCT_RESULT',requestId,ok:false,error:msg});
    await appendLog(`SHOPEE INSPECT ERROR Ãƒâ€šÃ‚Â· ${msg}`,'error');
  }finally{
    const aborted=serverFailClosed||(serverControlledRun.active&&serverControlledRun.aborted);
    if(!aborted && Number.isInteger(oldActive?.id)) await chrome.tabs.update(oldActive.id,{active:true}).catch(()=>{});
    if(Number.isInteger(tab?.id)){serverTemporaryTabs.delete(tab.id);await chrome.tabs.remove(tab.id).catch(()=>{});}
  }
}


function shopeeSearchExtractor(keyword,limit){
  const clean=(v)=>String(v||'').replace(/\s+/g,' ').trim();
  const normalizeUrl=(href)=>{
    try{
      const u=new URL(href,location.origin);
      if(u.protocol!=='https:')return '';
      const h=u.hostname.toLowerCase();
      if(!(h==='shopee.vn'||h.endsWith('.shopee.vn')))return '';
      if(!(/\/product\/\d+\/\d+/.test(u.pathname)||/-i\.\d+\.\d+/.test(u.pathname)))return '';
      u.hash='';
      return u.href;
    }catch{return '';}
  };
  const rows=[]; const seen=new Set();
  const anchors=Array.from(document.querySelectorAll('a[href]'));
  for(const a of anchors){
    const url=normalizeUrl(a.href||a.getAttribute('href')||'');
    if(!url||seen.has(url))continue;
    let card=a;
    for(let i=0;i<5 && card?.parentElement;i++){
      const t=clean(card.innerText||'');
      if(t.length>=30 && (card.querySelector?.('img')||card.querySelectorAll?.('span').length>1))break;
      card=card.parentElement;
    }
    const text=clean(card?.innerText||a.innerText||'');
    const img=card?.querySelector?.('img');
    const image=String(img?.currentSrc||img?.src||img?.getAttribute?.('data-src')||'').trim();
    let title='';
    const alt=clean(img?.alt||'');
    if(alt.length>=8) title=alt;
    if(!title){
      const lines=String(card?.innerText||'').split(/\n+/).map(clean).filter(Boolean);
      title=lines.find(x=>x.length>=12&&!/^[ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â«Ãƒâ€žÃ¢â‚¬Ëœ\d.,%\s+-]+$/i.test(x)&&!/Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ bÃƒÆ’Ã‚Â¡n|sold|giÃƒÂ¡Ã‚ÂºÃ‚Â£m|voucher/i.test(x))||'';
    }
    let price=''; const pm=text.match(/(?:ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â«|Ãƒâ€žÃ¢â‚¬Ëœ)\s*([0-9][0-9.,]{2,})|([0-9][0-9.,]{2,})\s*(?:ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â«|Ãƒâ€žÃ¢â‚¬Ëœ)/i); if(pm)price=pm[1]||pm[2]||'';
    let sold=''; const sm=text.match(/(?:Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ bÃƒÆ’Ã‚Â¡n|sold)\s*([0-9.,kK+]+)/i); if(sm)sold=sm[1]||'';
    if(title.length<8)continue;
    seen.add(url); rows.push({url,title:title.slice(0,300),price,sold,image,keyword,source:'browser_search_dom_v14522'});
    if(rows.length>=Math.max(1,Math.min(20,Number(limit)||6)))break;
  }
  return {items:rows,bodyText:clean(document.body?.innerText||'').slice(0,3000),finalUrl:location.href};
}

async function searchShopeeProductsForServer(message){
  assertServerControlAlive();
  const requestId=String(message?.requestId||'').trim();
  const keyword=String(message?.keyword||'').trim().slice(0,120);
  const limit=Math.max(1,Math.min(20,Number(message?.limit)||6));
  if(!requestId||!keyword)throw new Error('SHOPEE_SEARCH_PRODUCTS thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u requestId/keyword');
  let tab=null,oldActive=null;
  try{
    oldActive=(await chrome.tabs.query({active:true,currentWindow:true}).catch(()=>[]))?.[0]||null;
    const url='https://shopee.vn/search?keyword='+encodeURIComponent(keyword);
    assertServerControlAlive();
    tab=await chrome.tabs.create({url,active:false});
    if(!Number.isInteger(tab?.id))throw new Error('KhÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tab tÃƒÆ’Ã‚Â¬m kiÃƒÂ¡Ã‚ÂºÃ‚Â¿m Shopee.');
    serverTemporaryTabs.add(tab.id);
    await waitTabLoaded(tab.id,20000); await sleep(1400);
    let result=null;
    for(let attempt=0;attempt<12;attempt++){
      assertServerControlAlive();
      try{
        const x=await chrome.scripting.executeScript({target:{tabId:tab.id},func:shopeeSearchExtractor,args:[keyword,limit]});
        result=x?.[0]?.result||null;
        if((result?.items||[]).length)break;
      }catch{}
      if(attempt===5){await chrome.tabs.update(tab.id,{active:true}).catch(()=>{});await sleep(1000);}
      await sleep(850);
    }
    const items=result?.items||[];
    const low=String(result?.bodyText||'').toLowerCase();
    if(!items.length && (low.includes('xÃƒÆ’Ã‚Â¡c minh')||low.includes('captcha')||low.includes('Ãƒâ€žÃ¢â‚¬ËœÃƒâ€žÃ†â€™ng nhÃƒÂ¡Ã‚ÂºÃ‚Â­p'))){
      throw new Error('Shopee yÃƒÆ’Ã‚Âªu cÃƒÂ¡Ã‚ÂºÃ‚Â§u Ãƒâ€žÃ¢â‚¬ËœÃƒâ€žÃ†â€™ng nhÃƒÂ¡Ã‚ÂºÃ‚Â­p/xÃƒÆ’Ã‚Â¡c minh trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc khi tÃƒÆ’Ã‚Â¬m sÃƒÂ¡Ã‚ÂºÃ‚Â£n phÃƒÂ¡Ã‚ÂºÃ‚Â©m.');
    }
    if(!sendServerMessage({type:'SHOPEE_SEARCH_RESULT',requestId,ok:true,items,keyword}))throw new Error('MÃƒÂ¡Ã‚ÂºÃ‚Â¥t kÃƒÂ¡Ã‚ÂºÃ‚Â¿t nÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi server trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc khi gÃƒÂ¡Ã‚Â»Ã‚Â­i kÃƒÂ¡Ã‚ÂºÃ‚Â¿t quÃƒÂ¡Ã‚ÂºÃ‚Â£ tÃƒÆ’Ã‚Â¬m Shopee.');
    await appendLog(`SHOPEE SEARCH OK Ãƒâ€šÃ‚Â· ${keyword} Ãƒâ€šÃ‚Â· ${items.length} kÃƒÂ¡Ã‚ÂºÃ‚Â¿t quÃƒÂ¡Ã‚ÂºÃ‚Â£`,'success');
  }catch(error){
    const msg=error?.message||String(error);
    sendServerMessage({type:'SHOPEE_SEARCH_RESULT',requestId,ok:false,error:msg,keyword});
    await appendLog(`SHOPEE SEARCH ERROR Ãƒâ€šÃ‚Â· ${keyword} Ãƒâ€šÃ‚Â· ${msg}`,'error');
  }finally{
    const aborted=serverFailClosed||(serverControlledRun.active&&serverControlledRun.aborted);
    if(!aborted && Number.isInteger(oldActive?.id))await chrome.tabs.update(oldActive.id,{active:true}).catch(()=>{});
    if(Number.isInteger(tab?.id)){serverTemporaryTabs.delete(tab.id);await chrome.tabs.remove(tab.id).catch(()=>{});}
  }
}

function serverFlowSignature(flow={}){
  const keys=['imageModel','videoModel','imageConcurrency','videoConcurrency','aspectRatio','imageOutputs','videoDuration','videoOutputs','videoExtendFactor','videoExtendPrompt','submitPolicy','maxSubmitsPerMinute','submitGapMs','imageTimeoutSec','videoTimeoutSec','systemicFailureLimit','autoDownloadVideo','clearComposerBeforeRun','clearPromptBeforeRun','clearImagesBeforeRun'];
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
  if(serverAcceptedJobIds.has(jobId)){
    sendServerMessage({type:'FLOW_JOB_ACCEPTED',jobId,runId:`queued_${jobId}`,queuePosition:0,queueDepth:serverJobQueue.length+(activeServerBatch?.jobs?.size||0),duplicate:true});
    return;
  }
  serverAcceptedJobIds.add(jobId);
  serverJobQueue.push(message);
  const sig=serverFlowSignature(message?.flow||{});
  sendServerMessage({type:'FLOW_JOB_ACCEPTED',jobId,runId:`queued_${jobId}`,queuePosition:serverJobQueue.length,queueDepth:serverJobQueue.length+(activeServerBatch?.jobs?.size||0),queuedInExtension:true});
  appendLog(`SERVER QUEUE +1 Ãƒâ€šÃ‚Â· ${jobId} Ãƒâ€šÃ‚Â· pending=${serverJobQueue.length} Ãƒâ€šÃ‚Â· IMAGE cap=${Number(message?.flow?.imageConcurrency||1)} Ãƒâ€šÃ‚Â· VIDEO cap=${Number(message?.flow?.videoConcurrency||1)}`,'info').catch(()=>{});
  if(activeServerBatch?.signature===sig) activeServerBatch.wake?.notify?.();
  if(!serverRunPromise){
    serverRunPromise=runServerQueueLoop().catch(async e=>appendLog(`SERVER QUEUE ERROR Ãƒâ€šÃ‚Â· ${e?.message||e}`,'error')).finally(()=>{serverRunPromise=null;if(serverJobQueue.length) queueMicrotask(()=>{if(!serverRunPromise){serverRunPromise=runServerQueueLoop().finally(()=>{serverRunPromise=null;});}});});
  }
}

async function runServerQueueLoop(){
  while(serverJobQueue.length){
    const first=serverJobQueue.shift();
    const signature=serverFlowSignature(first?.flow||{});
    const initial=[first,...takeQueuedServerJobs(signature)];
    await runServerJobGroup(initial,signature);
  }
}

async function connectServerBridge(force=false){
  const cfg=await getServerBridgeConfig();
  if(!cfg.enabled){await disconnectServerBridge('disabled');return;}
  if(!force && serverSocket && (serverSocket.readyState===WebSocket.OPEN||serverSocket.readyState===WebSocket.CONNECTING)) return;
  if(serverSocket){
    const oldSocket=serverSocket;
    serverSocket=null;
    try{oldSocket.close(1000,'client reconnect');}catch{}
    // Give FastAPI time to process the old close before opening a replacement.
    // Without this grace period the same MV3 extension can race itself and the
    // server sees two healthy sockets with one extensionId.
    if(force) await new Promise(resolve=>setTimeout(resolve,350));
  }
  clearTimeout(serverReconnectTimer);
  await setServerStatus({connected:false,url:cfg.url,lastError:null});
  let ws;
  try{ws=new WebSocket(cfg.url);}catch(error){
    await setServerStatus({connected:false,url:cfg.url,lastError:error?.message||String(error)});
    serverReconnectTimer=setTimeout(()=>connectServerBridge(false),3000);return;
  }
  serverSocket=ws;
  ws.onopen=async()=>{
    serverFailClosed=false;
    startServerHeartbeat();
    await setServerStatus({connected:true,url:cfg.url,lastError:null,connectedAt:Date.now()});
    try{await appendLog(`ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â€ Server bridge ONLINE Ãƒâ€šÃ‚Â· ${cfg.url} Ãƒâ€šÃ‚Â· worker=${chrome.runtime.getManifest().version} Ãƒâ€šÃ‚Â· build=${V28_BUILD_ID}`,'info');}catch{}
    sendServerMessage({type:'AGENT_HELLO',role:'flow-extension',extensionId:chrome.runtime.id,workerId:chrome.runtime.id,version:chrome.runtime.getManifest().version,buildId:V28_BUILD_ID,failSafeReady:true,capabilities:{serverQueue:true,signedUrlDownload:true,imageRecovery:true,videoRecovery:true},runtime:{...(runtimeCache||{}),running:Boolean(runtimeCache?.running),progressLabel:runtimeCache?.progressLabel||'IDLE'}});
    sendServerMessage({type:'AGENT_READY',failSafeReady:true,runtime:{...(runtimeCache||{}),running:Boolean(runtimeCache?.running),progressLabel:runtimeCache?.progressLabel||'IDLE'}});
    if(runtimeCache?.serverJobId && !runtimeCache.running && runtimeCache.progressLabel==='Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ giÃƒÆ’Ã‚Â¡n Ãƒâ€žÃ¢â‚¬ËœoÃƒÂ¡Ã‚ÂºÃ‚Â¡n'){
      sendServerMessage({type:'FLOW_JOB_INTERRUPTED',jobId:runtimeCache.serverJobId,error:runtimeCache.progressDetail||'Extension service worker restarted.'});
    }
  };
  ws.onmessage=event=>{
    let message; try{message=JSON.parse(String(event.data||''));}catch{return;}
    if(message?.type==='SERVER_SHUTDOWN'){
      const reason=message?.reason||'Parenting server Ãƒâ€žÃ¢â‚¬Ëœang dÃƒÂ¡Ã‚Â»Ã‚Â«ng';
      interruptServerJobsBeforeStop(reason);
      abortServerControlledWork(reason).finally(()=>{try{ws.close(1000,'server shutdown');}catch{}});
      return;
    }
    if(message?.type==='PING'){sendServerMessage({type:'PONG'});return;}
    if(message?.type==='DOWNLOAD_MEDIA_FILES'){
      const req={
        jobId:String(message.jobId||''),sceneId:Number(message.sceneId||0),mediaIds:message.mediaIds||[],
        downloadMode:String(message.downloadMode||'server_signed_url'),refreshSignedUrl:Boolean(message.refreshSignedUrl)
      };
      serverVideoRecoveryChain=serverVideoRecoveryChain.catch(()=>{}).then(async()=>{
        await appendLog(`DOWNLOAD RECOVERY START Ãƒâ€šÃ‚Â· scene=${req.sceneId} Ãƒâ€šÃ‚Â· media=${req.mediaIds.map(x=>String(x).slice(0,8)).join(',')}`,'info');
        return await downloadMediaIdsForServer(req);
      }).catch(async error=>{
        // Per-media VIDEO_DOWNLOAD_URL_ERROR is emitted inside downloadMediaIdsForServer.
        await appendLog(`ÃƒÂ¢Ã‚ÂÃ…â€™ RESOLVE VIDEO URL: ${error?.message||error}`,'error');
      });
      return;
    }
    if(message?.type==='DOWNLOAD_IMAGE_MEDIA_FILES'){
      downloadImageMediaIdsForServer({jobId:String(message.jobId||''),sceneId:Number(message.sceneId||0),mediaIds:message.mediaIds||[]})
        .catch(async error=>{await appendLog(`ÃƒÂ¢Ã‚ÂÃ…â€™ RECOVER IMAGE: ${error?.message||error}`,'error');sendServerMessage({type:'IMAGE_FILE_ERROR',jobId:message.jobId,sceneId:message.sceneId,error:error?.message||String(error)});});
      return;
    }
    if(message?.type==='SHOPEE_INSPECT_PRODUCT'){
      inspectShopeeProductForServer(message).catch(error=>sendServerMessage({type:'SHOPEE_PRODUCT_RESULT',requestId:message.requestId,ok:false,error:error?.message||String(error)}));
      return;
    }
    if(message?.type==='SHOPEE_SEARCH_PRODUCTS'){
      searchShopeeProductsForServer(message).catch(error=>sendServerMessage({type:'SHOPEE_SEARCH_RESULT',requestId:message.requestId,ok:false,error:error?.message||String(error)}));
      return;
    }
    if(message?.type==='RUN_FLOW_JOB'){
      enqueueServerFlowJob(message);
      return;
    }
  };
  ws.onerror=()=>{};
  ws.onclose=async event=>{
    const wasCurrent=serverSocket===ws;
    if(!wasCurrent) return; // stale socket from a forced reconnect must not kill the new session
    stopServerHeartbeat();
    serverSocket=null;
    const closeCode=Number(event?.code||0);
    const closeReason=String(event?.reason||'').trim();
    try{await appendLog(`ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ…â€™ Server bridge CLOSED Ãƒâ€šÃ‚Â· code=${closeCode||'?'} Ãƒâ€šÃ‚Â· clean=${event?.wasClean?'yes':'no'}${closeReason?` Ãƒâ€šÃ‚Â· ${closeReason}`:''}`,'warning');}catch{}
    await abortServerControlledWork('V2.8 server mÃƒÂ¡Ã‚ÂºÃ‚Â¥t kÃƒÂ¡Ã‚ÂºÃ‚Â¿t nÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi; extension fail-closed vÃƒÆ’Ã‚Â  dÃƒÂ¡Ã‚Â»Ã‚Â«ng browser automation.');
    await setServerStatus({connected:false,url:cfg.url,lastError:closeCode===4009?(closeReason||'Duplicate Flow worker'):'MÃƒÂ¡Ã‚ÂºÃ‚Â¥t kÃƒÂ¡Ã‚ÂºÃ‚Â¿t nÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi server'});
    if(closeCode===4009){
      try{await appendLog(`ÃƒÂ¢Ã¢â‚¬ÂºÃ¢â‚¬Â Duplicate FLOW_WORKER dÃƒÂ¡Ã‚Â»Ã‚Â«ng reconnect Ãƒâ€šÃ‚Â· ${closeReason||'4009'}`,'warning');}catch{}
      return;
    }
    const latest=await getServerBridgeConfig();
    if(latest.enabled){clearTimeout(serverReconnectTimer);serverReconnectTimer=setTimeout(()=>connectServerBridge(false),3000);}
  };
}

async function findOrOpenFlowTab(){
  const tabs=await chrome.tabs.query({});
  let tab=tabs.find(t=>Number.isInteger(t.id)&&isFlowToolUrl(t.url||''));
  if(!tab){
    tab=await chrome.tabs.create({url:'https://labs.google/fx/tools/flow',active:true});
  }else{
    try{await chrome.tabs.update(tab.id,{active:true});}catch{}
    try{if(Number.isInteger(tab.windowId)) await chrome.windows?.update?.(tab.windowId,{focused:true});}catch{}
  }
  if(!Number.isInteger(tab?.id)) throw new Error('KhÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tab Google Flow.');
  return tab;
}

function compactServerResults(records=[]){
  return (Array.isArray(records)?records:[]).map(record=>({
    index:Number(record?.serverSceneIndex??record?.index??0),
    sceneId:Number(record?.sceneId||0)||Number(record?.serverSceneIndex??record?.index??0)+1,
    imageState:String(record?.imageState||'WAIT'),
    videoState:String(record?.videoState||'WAIT'),
    error:record?.error?String(record.error):null,
    image:record?.selectedImage?{
      mediaId:record.selectedImage.mediaId||null,
      url:record.selectedImage.url||null,
      title:record.selectedImage.title||record.selectedImage.workflowTitle||null
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
  // v14.6.1: NEVER mutate serverEnabled/serverUrl while mirroring a RUN_FLOW_JOB.
  // Those keys are connection-control settings. Writing defaults here fires the
  // storage listener and can force-close the live websocket immediately after
  // FLOW_JOB_ACCEPTED. Preserve exactly what the user already has stored.
  if(Object.prototype.hasOwnProperty.call(old,'serverEnabled')) next.serverEnabled=old.serverEnabled;
  else delete next.serverEnabled;
  if(Object.prototype.hasOwnProperty.call(old,'serverUrl')) next.serverUrl=old.serverUrl;
  else delete next.serverUrl;
  await chrome.storage.local.set({flowPairAutoForm:next});
  return next;
}

async function runServerJobGroup(initialMessages,signature){
  const initial=(Array.isArray(initialMessages)?initialMessages:[]).filter(m=>m&&m.jobId);
  if(!initial.length) return;
  const first=initial[0];
  serverControlledRun={active:true,aborted:false,reason:'',tabId:null};
  activeServerBatch={signature,jobs:new Map(initial.map(m=>[String(m.jobId),m])),wake:createWakeSignal(),take:()=>takeQueuedServerJobs(signature),aborted:false,abortReason:''};
  assertServerControlAlive();
  await syncServerFlowToPopup(first?.flow||{});
  assertServerControlAlive();
  const tab=await findOrOpenFlowTab();
  serverControlledRun.tabId=tab.id;
  assertServerControlAlive();
  const runId=`server_queue_${Date.now()}_${Math.random().toString(36).slice(2,7)}`;
  await resetRuntimeForRun(runId,String(first.jobId||''));
  await appendLog(`SERVER QUEUE RUN Ãƒâ€šÃ‚Â· jobs=${initial.length} Ãƒâ€šÃ‚Â· global IMAGE=${Number(first?.flow?.imageConcurrency||1)} Ãƒâ€šÃ‚Â· VIDEO=${Number(first?.flow?.videoConcurrency||1)}`,'info');
  try{
    const parentingGroup=initial.some(m=>String(m?.kind||'').startsWith('parenting_'));
    const batch=await runAutomation({
      tabId:tab.id,serverJobMessages:initial,serverDynamicBatch:activeServerBatch,
      options:{
        ...(first.flow||{}),imageConcurrency:Math.max(1,Number(first?.flow?.imageConcurrency||1)),videoConcurrency:Math.max(1,Number(first?.flow?.videoConcurrency||1)),
        // Parenting recovery is artifact-driven: download each successful Veo clip
        // immediately so 7 successful clips remain usable even if clip #8 later fails.
        autoDownloadVideo:parentingGroup ? true : first?.flow?.autoDownloadVideo!==false
      }
    });
    const allRecords=batch?.results||[];
    const jobs=[...activeServerBatch.jobs.values()];
    for(const msg of jobs){
      const jobId=String(msg.jobId||'');
      const records=allRecords.filter(r=>String(r?.serverJobId||'')===jobId);
      const failures=records.filter(r=>r?.error).map(r=>({index:Number(r?.serverSceneIndex??r?.index??0),error:String(r?.error||'Unknown error')}));
      const safeResults=compactServerResults(records);
      assertServerControlAlive();
      if(failures.length) sendServerMessage({type:'FLOW_JOB_RESULT',jobId,ok:false,error:`CÃƒÆ’Ã‚Â³ ${failures.length} scene lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i.`,results:safeResults,failures});
      else sendServerMessage({type:'FLOW_JOB_RESULT',jobId,ok:true,results:safeResults});
      serverAcceptedJobIds.delete(jobId);
    }
    await finishRuntime(true,`Extension queue xong ${jobs.length} server job.`);
  }catch(error){
    let text=error?.message||String(error);
    const controlledAbort=serverFailClosed || !!activeServerBatch?.aborted || !!serverControlledRun?.aborted || serverSocket?.readyState!==WebSocket.OPEN;
    if(/Debugger is not attached|not attached to the tab/i.test(text)) text += ' | KhÃƒÂ¡Ã‚ÂºÃ‚Â¯c phÃƒÂ¡Ã‚Â»Ã‚Â¥c: Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng DevTools cÃƒÂ¡Ã‚Â»Ã‚Â§a tab Flow + tÃƒÂ¡Ã‚ÂºÃ‚Â¯t extension Flow cÃƒâ€¦Ã‚Â©, sau Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ Reload v14.7.1.';
    await appendLog(`${controlledAbort?'ÃƒÂ¢Ã¢â‚¬ÂºÃ¢â‚¬Â':'ÃƒÂ¢Ã‚ÂÃ…â€™'} SERVER QUEUE: ${text}`,controlledAbort?'warning':'error');
    const jobs=[...(activeServerBatch?.jobs?.values?.()||[])];
    for(const msg of jobs){
      const jobId=String(msg.jobId||'');
      if(controlledAbort){
        sendServerMessage({type:'FLOW_JOB_INTERRUPTED',jobId,error:text,retryable:true,controlledStop:true});
      }else{
        sendServerMessage({type:'FLOW_JOB_RESULT',jobId,ok:false,error:text});
      }
      serverAcceptedJobIds.delete(jobId);
    }
    await finishRuntime(false,text);
  }finally{
    activeServerBatch=null;
    serverControlledRun={active:false,aborted:false,reason:'',tabId:null};
  }
}


chrome.alarms?.create?.('flowServerBridgeKeepAlive',{periodInMinutes:0.5});
chrome.alarms?.onAlarm?.addListener?.(alarm=>{if(alarm?.name==='flowServerBridgeKeepAlive')connectServerBridge(false).catch(()=>{});});
chrome.runtime.onStartup.addListener(()=>connectServerBridge(false).catch(()=>{}));
chrome.runtime.onInstalled.addListener(()=>{getServerBridgeConfig().then(()=>connectServerBridge(true)).catch(()=>connectServerBridge(true).catch(()=>{}));});
chrome.storage.onChanged.addListener((changes,area)=>{
  if(area==='local'&&changes.flowPairAutoForm){
    const before=changes.flowPairAutoForm.oldValue||{},after=changes.flowPairAutoForm.newValue||{};
    // v14.6.1: compare EFFECTIVE connection settings, not raw stored values.
    // undefined and the default URL/ON state mean the same thing and must not
    // tear down a healthy websocket during an internal settings mirror.
    const beforeEnabled=before.serverEnabled!==false;
    const afterEnabled=after.serverEnabled!==false;
    const beforeUrl=String(before.serverUrl||DEFAULT_SERVER_URL).trim()||DEFAULT_SERVER_URL;
    const afterUrl=String(after.serverUrl||DEFAULT_SERVER_URL).trim()||DEFAULT_SERVER_URL;
    if(beforeEnabled!==afterEnabled||beforeUrl!==afterUrl) connectServerBridge(true).catch(()=>{});
  }
});
setTimeout(()=>connectServerBridge(false).catch(()=>{}),250);
// ============================================================================

const defaultRuntime = () => ({
  running: false,
  logs: [],
  lastLevel: 'info',
  progressPercent: 0,
  progressLabel: 'ChÃƒâ€ Ã‚Â°a chÃƒÂ¡Ã‚ÂºÃ‚Â¡y',
  progressDetail: 'Thanh nÃƒÆ’Ã‚Â y dÃƒÆ’Ã‚Â¹ng chung cho ÃƒÂ¡Ã‚ÂºÃ‚Â£nh + video.',
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
        runtimeCache.progressLabel='Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ giÃƒÆ’Ã‚Â¡n Ãƒâ€žÃ¢â‚¬ËœoÃƒÂ¡Ã‚ÂºÃ‚Â¡n';
        runtimeCache.progressDetail='Service worker Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ khÃƒÂ¡Ã‚Â»Ã…Â¸i Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ng lÃƒÂ¡Ã‚ÂºÃ‚Â¡i; phiÃƒÆ’Ã‚Âªn chÃƒÂ¡Ã‚ÂºÃ‚Â¡y cÃƒâ€¦Ã‚Â© khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚Â»Ã†â€™ tiÃƒÂ¡Ã‚ÂºÃ‚Â¿p tÃƒÂ¡Ã‚Â»Ã‚Â¥c an toÃƒÆ’Ã‚Â n.';
        runtimeCache.logs=[...(runtimeCache.logs||[]),{time:new Date().toLocaleTimeString(),text:'ÃƒÂ¢Ã‚ÂÃ…â€™ Service worker khÃƒÂ¡Ã‚Â»Ã…Â¸i Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ng lÃƒÂ¡Ã‚ÂºÃ‚Â¡i khi job Ãƒâ€žÃ¢â‚¬Ëœang chÃƒÂ¡Ã‚ÂºÃ‚Â¡y; Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â¡nh dÃƒÂ¡Ã‚ÂºÃ‚Â¥u phiÃƒÆ’Ã‚Âªn cÃƒâ€¦Ã‚Â© lÃƒÆ’Ã‚Â  giÃƒÆ’Ã‚Â¡n Ãƒâ€žÃ¢â‚¬ËœoÃƒÂ¡Ã‚ÂºÃ‚Â¡n.',level:'error'}].slice(-200);
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
    progressLabel: 'Ãƒâ€žÃ‚Âang khÃƒÂ¡Ã‚Â»Ã…Â¸i Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ng',
    progressDetail: 'Ãƒâ€žÃ‚Âang chuÃƒÂ¡Ã‚ÂºÃ‚Â©n bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ worker...'
  };
  await persistRuntime();
}

async function appendLog(text, level='info') {
  const line = { time: new Date().toLocaleTimeString(), text, level };
  runtimeCache.logs = [...(runtimeCache.logs || []), line].slice(-200);
  runtimeCache.lastLevel = level;
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
  runtimeCache.progressLabel = ok ? 'HoÃƒÆ’Ã‚Â n tÃƒÂ¡Ã‚ÂºÃ‚Â¥t' : 'Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng';
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
    throw new Error(`Page bridge version khÃƒÆ’Ã‚Â´ng tÃƒâ€ Ã‚Â°Ãƒâ€ Ã‚Â¡ng thÃƒÆ’Ã‚Â­ch: ${String(version)} (cÃƒÂ¡Ã‚ÂºÃ‚Â§n family ${COMPATIBLE_PAGE_FAMILY}.x; worker ${EXTENSION_VERSION})`);
  }
  if (version !== EXTENSION_VERSION) {
    await appendLog(`Page bridge ${String(version)} khÃƒÆ’Ã‚Â¡c patch worker ${EXTENSION_VERSION} nhÃƒâ€ Ã‚Â°ng cÃƒÆ’Ã‚Â¹ng family ${COMPATIBLE_PAGE_FAMILY}.x ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ tiÃƒÂ¡Ã‚ÂºÃ‚Â¿p tÃƒÂ¡Ã‚Â»Ã‚Â¥c`, 'info');
  }
}

function makeBridgeId() {
  return `__flow_pair_bridge_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

async function callPageRaw(tabId, method, args = []) {
  assertServerControlAlive();
  const bridgeId = makeBridgeId();

  // Execute in MAIN world, but write the result into shared DOM instead of
  // relying on InjectionResult.result. Edge sometimes returned null when
  // page-world async code touched React/Radix state.
  await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: async (bridgeId, methodName, methodArgs) => {
      const write = payload => {
        let node = document.getElementById(bridgeId);
        if (!node) {
          node = document.createElement('script');
          node.type = 'application/json';
          node.id = bridgeId;
          node.style.display = 'none';
          (document.documentElement || document.body).appendChild(node);
        }
        node.textContent = JSON.stringify(payload);
      };

      try {
        if (!window.FlowPairAuto) {
          throw new Error('FlowPairAuto chÃƒâ€ Ã‚Â°a Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c nÃƒÂ¡Ã‚ÂºÃ‚Â¡p trong MAIN world.');
        }

        const fn = window.FlowPairAuto[methodName];
        if (typeof fn !== 'function') {
          const version = typeof window.FlowPairAuto.getVersion === 'function'
            ? window.FlowPairAuto.getVersion()
            : window.__FLOW_PAIR_AUTO_VERSION__ || 'unknown';
          throw new Error(`KhÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ method ${methodName}. pageVersion=${version}`);
        }

        const value = await fn(...methodArgs);
        write({
          ok: true,
          value: value === undefined ? null : value,
          pageVersion: typeof window.FlowPairAuto.getVersion === 'function'
            ? window.FlowPairAuto.getVersion()
            : window.__FLOW_PAIR_AUTO_VERSION__ || null
        });
      } catch (error) {
        write({
          ok: false,
          error: error?.message || String(error),
          stack: error?.stack || null,
          pageVersion: window.__FLOW_PAIR_AUTO_VERSION__ || null
        });
      }

      // Return value is intentionally irrelevant.
      return true;
    },
    args: [bridgeId, method, args]
  });

  assertServerControlAlive();
  // Read shared DOM from extension world. This is stable even if MAIN-world
  // executeScript result itself is null.
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
  if (typeof raw !== 'string' || !raw) {
    throw new Error(`DOM bridge khÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ payload cho ${method}. raw=${String(raw)}`);
  }

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (error) {
    throw new Error(`DOM bridge parse lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i cho ${method}: ${error.message}`);
  }

  if (!payload?.ok) {
    throw new Error(
      `MAIN ${method} lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i: ${payload?.error || 'unknown'} | pageVersion=${payload?.pageVersion || 'unknown'}`
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
      reject(new Error('Timeout chÃƒÂ¡Ã‚Â»Ã‚Â Page.fileChooserOpened'));
    },timeoutMs);
    fileChooserWaiters.set(tabId,waiter);
  });
}

function getNetState(tabId) {
  if (!netState.has(tabId)) {
    netState.set(tabId, { tracked: new Map(), waiters: [], recent: [], seq: 0, redirectMedia: new Map(), signedVideoUrls: new Map() });
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
  if(!wanted) throw new Error('mediaId video rÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ng');
  let tabId=Number.isInteger(serverControlledRun?.tabId)?serverControlledRun.tabId:null;
  if(!Number.isInteger(tabId)){
    const tabs=await chrome.tabs.query({});
    const tab=tabs.find(t=>Number.isInteger(t.id)&&isFlowToolUrl(t.url||''));
    tabId=tab?.id;
  }
  if(!Number.isInteger(tabId)) throw new Error('KhÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ tab Flow Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ probe mediaId.');
  await injectPage(tabId);

  // DOM/performance can already know the final CDN URL without making another request.
  try{
    const perf=await callPage(tabId,'findSignedVideoResource',[wanted]);
    if(perf?.url && signedVideoUrlLooksUsable(perf.url)){
      return {url:perf.url,at:Date.now(),source:'flow_page_resource',method:'RESOURCE',status:200};
    }
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

    // A hidden <video preload=metadata> request is same-origin to labs.google and follows
    // the redirect naturally. CDP sees the redirect target even when fetch() is blocked
    // by CORS in the extension/page context. No browser download API and no Save popup.
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
    throw new Error(`Flow tab probe khÃƒÆ’Ã‚Â´ng bÃƒÂ¡Ã‚ÂºÃ‚Â¯t Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c CDN redirect cho mediaId=${wanted}${probe?.error?`: ${probe.error}`:''}`);
  }finally{
    if(attachedHere){
      await chrome.debugger.detach(debuggeeFor(tabId)).catch(()=>{});
      debuggerOwnedTabs.delete(tabId);
      netState.delete(tabId);
    }
  }
}

function classifyUrl(url='') {
  if (url.includes('media.getMediaUrlRedirect')) return 'MEDIA_REDIRECT';
  if (isVideoCdnUrl(url)) return 'VIDEO_CDN';
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

function collectStructuredTexts(node, out=[]) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) {
    for (const item of node) collectStructuredTexts(item,out);
    return out;
  }
  for (const [key,value] of Object.entries(node)) {
    if (key === 'text' && typeof value === 'string' && value.trim()) out.push(compactText(value));
    else if (key !== 'token' && key !== 'recaptchaContext') collectStructuredTexts(value,out);
  }
  return out;
}

function workflowIdFromUrl(url='') {
  const m=String(url).match(/\/v1\/flowWorkflows\/([^/?#]+)/);
  return m?.[1] || null;
}

function flowUrlInfo(url='') {
  try{
    const u=new URL(String(url||''));
    if(u.origin!=='https://labs.google') return {isFlow:false,baseUrl:null,projectId:null};
    // Google Flow can insert a locale segment: /fx/vi/tools/flow/project/<id>,
    // /fx/en/tools/flow/project/<id>, ... . Never use startsWith('/fx/tools/flow').
    const baseMatch=u.pathname.match(/^(.*\/tools\/flow)(?:\/|$)/i);
    if(!baseMatch) return {isFlow:false,baseUrl:null,projectId:null};
    const projectMatch=u.pathname.match(/\/tools\/flow\/project\/([^/?#]+)/i);
    return {
      isFlow:true,
      baseUrl:`${u.origin}${baseMatch[1].replace(/\/+$/,'')}`,
      projectId:projectMatch?.[1]||null
    };
  }catch{return {isFlow:false,baseUrl:null,projectId:null};}
}

function isFlowToolUrl(url='') { return Boolean(flowUrlInfo(url).isFlow); }
function flowToolBaseFromUrl(url='') { return flowUrlInfo(url).baseUrl || 'https://labs.google/fx/tools/flow'; }
function flowProjectUrl(projectId,urlHint='') {
  return `${flowToolBaseFromUrl(urlHint)}/project/${encodeURIComponent(String(projectId||'').trim())}`;
}
function projectIdFromFlowUrl(url='') { return flowUrlInfo(url).projectId; }

function isFlowProjectRootUrl(url='', projectId='') {
  try{
    const u=new URL(String(url||''));
    const wanted=String(projectId||'').trim();
    if(!wanted || !isFlowToolUrl(url)) return false;
    const path=u.pathname.replace(/\/+$/,'');
    const m=path.match(/\/tools\/flow\/project\/([^/]+)$/i);
    if(!m) return false;
    let got=m[1]; try{got=decodeURIComponent(got);}catch{}
    return got===wanted;
  }catch{return false;}
}

function isFlowProjectDetailUrl(url='', projectId='') {
  const currentProject=projectIdFromFlowUrl(url);
  if(!currentProject || (projectId && currentProject!==projectId)) return false;
  return !isFlowProjectRootUrl(url,currentProject);
}

async function normalizeProjectRoot(tabId,projectId,reason='normalize project view'){
  let tab=await chrome.tabs.get(tabId);
  if(isFlowProjectRootUrl(tab.url||'',projectId)) return tab;
  const target=flowProjectUrl(projectId,tab.url||'');
  await appendLog(`PROJECT VIEW RECOVERY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${reason} Ãƒâ€šÃ‚Â· ${String(tab.url||'').includes('/edit/')?'Ãƒâ€žÃ¢â‚¬Ëœang xem media/edit':'route con'} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ project root`,'info');
  await trustedEscape(tabId).catch(()=>{});
  await chrome.tabs.update(tabId,{url:target});
  tab=await waitTabState(tabId,t=>t.status==='complete'&&projectIdFromFlowUrl(t.url||'')===projectId&&isFlowProjectRootUrl(t.url||'',projectId),30000,'thoÃƒÆ’Ã‚Â¡t media detail vÃƒÂ¡Ã‚Â»Ã‚Â Project');
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
  const wanted=normalizedPrompt(prompt);
  if(!wanted) return true;
  const texts=Array.isArray(info?.texts)?info.texts:[];
  return texts.some(text=>normalizedPrompt(text)===wanted);
}

function generationRequestMatches(meta,{kind,marker,projectId,prompt,referenceMediaId}) {
  if(!meta) return false;
  if(meta.kind!==kind) return false;
  if(Number(meta.seq)<=Number(marker?.seq||0)) return false;
  if(meta.method!=='POST') return false;
  if(!meta.requestInfo?.validGeneration) return false;
  if(!eventProjectMatches(meta,projectId)) return false;
  if(referenceMediaId && !meta.requestInfo?.referenceMediaIds?.includes(referenceMediaId)) return false;
  // Reference-to-video: imageMediaId is the unique correlation key. Flow may normalize/translate
  // prompt text before the request is emitted, so prompt mismatch must not create a false negative.
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
  throw new Error(`KhÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y ${label} sau trusted Create. prompt=${JSON.stringify(prompt)} ref=${referenceMediaId||'-'}`);
}

async function waitExactRequestFinished(tabId,requestId,timeoutMs,label) {
  return await waitNet(tabId,event=>{
    if(event.requestId!==requestId) return false;
    if(event.failed) return {error:new Error(`${label} lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i Network: ${event.errorText||'unknown'}`)};
    if(!event.loadingFinished) return false;
    return {value:event};
  },timeoutMs,label);
}

function isQuotaLikeError(error) {
  const text=String(error?.message||error||'').toUpperCase();
  return text.includes('HTTP 429') || text.includes('RESOURCE_EXHAUSTED') || text.includes('QUOTA') || text.includes('RATE LIMIT') || text.includes('DAILY LIMIT');
}

function isTransientFlowUiError(error){
  const t=String(error?.message||error||'').toLowerCase();
  if(!t) return true;
  if(isQuotaLikeError(error)) return false;
  const permanent=['content policy','blocked by policy','invalid prompt','unsupported model','model not supported','permission denied'];
  if(permanent.some(x=>t.includes(x))) return false;
  const transient=[
    'timeout','network','settings','picker','asset','mediaid','debugger','not attached',
    'khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y video post','khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y image post','khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng media','khÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c',
    'khÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y nÃƒÆ’Ã‚Âºt','khÃƒÆ’Ã‚Â´ng phÃƒÂ¡Ã‚ÂºÃ‚Â£n hÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i','click clear reference','request start','connection'
  ];
  return transient.some(x=>t.includes(x));
}

function isSystemicFlowUiError(error){
  const t=String(error?.message||error||'').toLowerCase();
  return [
    'settings verify failed','khÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y nÃƒÆ’Ã‚Âºt cÃƒÆ’Ã‚Â i Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â·t','khÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c cÃƒÆ’Ã‚Â i Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â·t',
    'picker-not-open','bÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ chÃƒÂ¡Ã‚Â»Ã‚Ân tÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡p khÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸','debugger is not attached','not attached to the tab',
    'khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y video post','khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y image post','trusted tÃƒÂ¡Ã‚ÂºÃ‚Â¡o','khÃƒÆ’Ã‚Â´ng phÃƒÂ¡Ã‚ÂºÃ‚Â£n hÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i'
  ].some(x=>t.includes(x));
}

function sendSceneCheckpoint(record,stage,extra={}){
  const jobId=String(record?.serverJobId||'').trim();
  if(!jobId) return;
  sendServerMessage({
    type:'SCENE_CHECKPOINT',
    jobId,
    sceneId:Number(record?.sceneId||0),
    dispatchEpoch:Number(record?.serverDispatchEpoch||0),
    stage:String(stage||''),
    imageMediaId:String(record?.selectedImage?.mediaId||''),
    videoMediaIds:[...(record?.videoIds||[])],
    imageState:String(record?.imageState||''),
    videoState:String(record?.videoState||''),
    error:String(record?.error||''),
    ...extra
  });
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
        if(waitMs>700) await appendLog(`Rate limiter: chÃƒÂ¡Ã‚Â»Ã‚Â ${(waitMs/1000).toFixed(1)}s trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc ${label}`,'info');
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
    const url=params.request?.url||'';
    const kind=classifyUrl(url);
    if(!kind) return;
    if(kind==='MEDIA_REDIRECT'){
      const mid=mediaIdFromRedirectUrl(url);
      if(mid) state.redirectMedia.set(params.requestId,{mediaId:mid,at:Date.now()});
      return;
    }
    if(kind==='VIDEO_CDN'){
      const prior=state.redirectMedia.get(params.requestId);
      const redirectedMid=mediaIdFromRedirectUrl(params.redirectResponse?.url||'');
      const mid=String(prior?.mediaId||redirectedMid||'').trim();
      if(mid){
        state.signedVideoUrls.set(mid,{url:String(url),at:Date.now(),source:'cdp_redirect_capture'});
        if(state.signedVideoUrls.size>120){
          const oldest=[...state.signedVideoUrls.entries()].sort((a,b)=>Number(a[1]?.at||0)-Number(b[1]?.at||0)).slice(0,20);
          for(const [key] of oldest) state.signedVideoUrls.delete(key);
        }
      }
      state.redirectMedia.delete(params.requestId);
      return;
    }
    const seq=++state.seq;
    const methodName=String(params.request?.method||'').toUpperCase();
    const postData=params.request?.postData||'';
    state.tracked.set(params.requestId,{
      kind,url,seq,method:methodName,postData,
      requestInfo:parseRequestInfo(kind,url,postData),
      requestSeen:true,startTs:Date.now()
    });
    return;
  }

  if(method==='Network.responseReceived'){
    const kind=classifyUrl(params.response?.url||'');
    if(!kind) return;
    const entry=state.tracked.get(params.requestId);
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
      throw new Error(`KhÃƒÆ’Ã‚Â´ng attach Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c Chrome Debugger vÃƒÆ’Ã‚Â o tab Flow ${tabId}. CÃƒÆ’Ã‚Â³ debugger khÃƒÆ’Ã‚Â¡c Ãƒâ€žÃ¢â‚¬Ëœang giÃƒÂ¡Ã‚Â»Ã‚Â¯ tab (thÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âng lÃƒÆ’Ã‚Â  DevTools hoÃƒÂ¡Ã‚ÂºÃ‚Â·c mÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢t bÃƒÂ¡Ã‚ÂºÃ‚Â£n Flow Wardrobe Studio cÃƒâ€¦Ã‚Â©). HÃƒÆ’Ã‚Â£y Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng DevTools cÃƒÂ¡Ã‚Â»Ã‚Â§a tab Flow vÃƒÆ’Ã‚Â  tÃƒÂ¡Ã‚ÂºÃ‚Â¯t extension Flow cÃƒâ€¦Ã‚Â© rÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i thÃƒÂ¡Ã‚Â»Ã‚Â­ lÃƒÂ¡Ã‚ÂºÃ‚Â¡i. Chi tiÃƒÂ¡Ã‚ÂºÃ‚Â¿t: ${debuggerErrorText(error)}`);
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
      waiter.reject(new Error(`Debugger tab ${tabId} bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ detach: ${reason}`));
    }
    netState.delete(tabId);
  }
  appendLog(`Chrome Debugger DETACHED Ãƒâ€šÃ‚Â· tab=${tabId} Ãƒâ€šÃ‚Â· reason=${reason}. NÃƒÂ¡Ã‚ÂºÃ‚Â¿u Ãƒâ€žÃ¢â‚¬Ëœang mÃƒÂ¡Ã‚Â»Ã…Â¸ DevTools trÃƒÆ’Ã‚Âªn tab Flow, hÃƒÆ’Ã‚Â£y Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng DevTools.`, 'error').catch(()=>{});
});

async function attachWorkerDebugger(tabId){
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
      await appendLog(`Chrome Debugger READY Ãƒâ€šÃ‚Â· tab=${tabId} Ãƒâ€šÃ‚Â· attempt=${attempt}`,'info');
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
        await appendLog(`Chrome Debugger attach/probe lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i lÃƒÂ¡Ã‚ÂºÃ‚Â§n ${attempt}/3 Ãƒâ€šÃ‚Â· ${text}${reason?` Ãƒâ€šÃ‚Â· detach=${reason}`:''} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ thÃƒÂ¡Ã‚Â»Ã‚Â­ lÃƒÂ¡Ã‚ÂºÃ‚Â¡i`,'info');
        await sleep(350*attempt);
        continue;
      }
      break;
    }
  }
  const reason=debuggerDetachReason.get(tabId);
  throw new Error(`Chrome Debugger khÃƒÆ’Ã‚Â´ng giÃƒÂ¡Ã‚Â»Ã‚Â¯ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tab Flow ${tabId}${reason?` (detach=${reason})`:''}. Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â³ng DevTools trÃƒÆ’Ã‚Âªn tab Flow, tÃƒÂ¡Ã‚ÂºÃ‚Â¯t cÃƒÆ’Ã‚Â¡c bÃƒÂ¡Ã‚ÂºÃ‚Â£n Flow Wardrobe Studio cÃƒâ€¦Ã‚Â©, rÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i chÃƒÂ¡Ã‚ÂºÃ‚Â¡y lÃƒÂ¡Ã‚ÂºÃ‚Â¡i. LÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i cuÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi: ${debuggerErrorText(lastError)}`);
}

async function detachWorkerDebugger(tabId){
  const chooser=fileChooserWaiters.get(tabId);
  if(chooser){clearTimeout(chooser.timer);chooser.reject(new Error('Worker kÃƒÂ¡Ã‚ÂºÃ‚Â¿t thÃƒÆ’Ã‚Âºc.'));fileChooserWaiters.delete(tabId);}
  const state=netState.get(tabId);
  if(state){
    for(const waiter of state.waiters){
      clearTimeout(waiter.timer);
      waiter.reject(new Error('Worker kÃƒÂ¡Ã‚ÂºÃ‚Â¿t thÃƒÆ’Ã‚Âºc.'));
    }
    netState.delete(tabId);
  }
  await chrome.debugger.detach(debuggeeFor(tabId)).catch(()=>{});
}

async function trustedClickPoint(tabId,point){
  if(!point||!Number.isFinite(point.x)||!Number.isFinite(point.y)) throw new Error('TÃƒÂ¡Ã‚Â»Ã‚Âa Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ click khÃƒÆ’Ã‚Â´ng hÃƒÂ¡Ã‚Â»Ã‚Â£p lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡.');
  assertServerControlAlive();
  const d=debuggeeFor(tabId);
  await chrome.debugger.sendCommand(d,'Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x,y:point.y});
  assertServerControlAlive();
  let pressed=false;
  try{
    await chrome.debugger.sendCommand(d,'Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',buttons:1,clickCount:1});
    pressed=true;
    await sleep(65);
    assertServerControlAlive();
  }finally{
    if(pressed) await chrome.debugger.sendCommand(d,'Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',buttons:0,clickCount:1}).catch(()=>{});
  }
}

async function trustedCreateClick(tabId){
  const point=await callPage(tabId,'getCreatePoint');
  await trustedClickPoint(tabId,point);
}


// v14.6.0: clear stale prompt/media but KEEP exact reference chips that the next
// stage still needs. The chip DOM itself carries mediaId in media.getMediaUrlRedirect?name=.
// This prevents mother/child refs from being removed and re-uploaded on every scene.
async function clearComposerBeforeCreate(tabId,tag='',keepMediaIds=[]){
  await callPage(tabId,'closeSettings',[]).catch(()=>{});
  const pickerOpen=await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false);
  if(pickerOpen) await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
  await callPage(tabId,'clearPrompt',[]).catch(error=>{throw new Error(`${tag} Clear prompt: ${error?.message||error}`);});

  const keep=new Set((keepMediaIds||[]).map(x=>String(x||'').trim()).filter(Boolean));
  let removed=0;
  for(let i=0;i<16;i++){
    const state=await getComposerMediaStateSafe(tabId);
    if(!Number(state?.count||0)) break;
    const unwanted=(state?.items||[]).find(x=>!x?.mediaId || !keep.has(String(x.mediaId)));
    if(!unwanted) break;
    const point=await callPage(tabId,'getComposerMediaRemovePoint',[String(unwanted?.mediaId||'')]).catch(()=>null);
    if(!point) throw new Error(`${tag} CÃƒÆ’Ã‚Â³ ${state.count} reference nhÃƒâ€ Ã‚Â°ng khÃƒÆ’Ã‚Â´ng lÃƒÂ¡Ã‚ÂºÃ‚Â¥y Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c nÃƒÆ’Ã‚Âºt clear cho mediaId=${unwanted?.mediaId||'unknown'}.`);
    await trustedClickPoint(tabId,point);
    removed++;
    const before=Number(state.count||0);
    const started=Date.now();
    let after=before;
    while(Date.now()-started<2500){
      await sleep(100);
      const next=await getComposerMediaStateSafe(tabId);
      after=Number(next?.count||0);
      if(after<before) break;
    }
    if(after>=before) throw new Error(`${tag} Click clear reference nhÃƒâ€ Ã‚Â°ng sÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœ ÃƒÂ¡Ã‚ÂºÃ‚Â£nh khÃƒÆ’Ã‚Â´ng giÃƒÂ¡Ã‚ÂºÃ‚Â£m (${before}ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢${after}).`);
  }
  const finalState=await getComposerMediaStateSafe(tabId);
  const wrong=(finalState?.items||[]).filter(x=>!x?.mediaId || !keep.has(String(x.mediaId)));
  if(wrong.length) throw new Error(`${tag} Composer cÃƒÆ’Ã‚Â²n ${wrong.length} reference khÃƒÆ’Ã‚Â´ng thuÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢c keep-set sau khi clear.`);
  await callPage(tabId,'clearPrompt',[]);
  const kept=(finalState?.mediaIds||[]).filter(id=>keep.has(String(id)));
  await appendLog(`TAB ${tag} COMPOSER CLEAN ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ refs=${Number(finalState?.count||0)} giÃƒÂ¡Ã‚Â»Ã‚Â¯=${kept.length} Ãƒâ€šÃ‚Â· prompt=empty${removed?` Ãƒâ€šÃ‚Â· removed=${removed}`:''}`,'info');
  return {ok:true,removed,keptMediaIds:kept};
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
  throw new Error(`Timeout chÃƒÂ¡Ã‚Â»Ã‚Â ${label}. url=${last?.url||'unknown'} status=${last?.status||'unknown'}`);
}

async function ensureFlowToolLoaded(tabId){
  let tab=await chrome.tabs.get(tabId);
  const url=String(tab.url||'');
  if(!isFlowToolUrl(url)){
    await appendLog(`Tab chÃƒâ€ Ã‚Â°a ÃƒÂ¡Ã‚Â»Ã…Â¸ Flow ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ mÃƒÂ¡Ã‚Â»Ã…Â¸ ${FLOW_TOOL_URL}`,'info');
    await chrome.tabs.update(tabId,{url:FLOW_TOOL_URL});
    tab=await waitTabState(tabId,t=>isFlowToolUrl(t.url||'')&&t.status==='complete',30000,'Flow tÃƒÂ¡Ã‚ÂºÃ‚Â£i xong');
  }else if(tab.status!=='complete'){
    tab=await waitTabState(tabId,t=>t.status==='complete',30000,'Flow tÃƒÂ¡Ã‚ÂºÃ‚Â£i xong');
  }
  return tab;
}

async function ensureProjectAndAllMedia(tabId){
  await injectPage(tabId);
  let tab=await chrome.tabs.get(tabId);
  let projectId=projectIdFromFlowUrl(tab.url||'');

  if(!projectId){
    await appendLog('KhÃƒÆ’Ã‚Â´ng ÃƒÂ¡Ã‚Â»Ã…Â¸ trong Project ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ tÃƒÂ¡Ã‚Â»Ã‚Â± tÃƒÂ¡Ã‚ÂºÃ‚Â¡o Project mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi bÃƒÂ¡Ã‚ÂºÃ‚Â±ng UI Flow.','info');
    let createPoint=null,lastCreateError=null;
    for(let attempt=1;attempt<=12;attempt++){
      try{createPoint=await callPage(tabId,'getCreateProjectPoint',[]);break;}
      catch(error){lastCreateError=error;await sleep(500);}
    }
    if(!createPoint) throw lastCreateError||new Error('KhÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y nÃƒÆ’Ã‚Âºt tÃƒÂ¡Ã‚ÂºÃ‚Â¡o Project.');
    await appendLog(`CREATE PROJECT ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${createPoint?.label||'Create Project'}`,'info');
    await trustedClickPoint(tabId,createPoint);
    tab=await waitTabState(tabId,t=>Boolean(projectIdFromFlowUrl(t.url||'')),30000,'Flow tÃƒÂ¡Ã‚ÂºÃ‚Â¡o Project');
    if(tab.status!=='complete') tab=await waitTabState(tabId,t=>t.status==='complete',30000,'Project tÃƒÂ¡Ã‚ÂºÃ‚Â£i xong');
    projectId=projectIdFromFlowUrl(tab.url||'');
    if(!projectId) throw new Error(`Flow Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ chuyÃƒÂ¡Ã‚Â»Ã†â€™n trang nhÃƒâ€ Ã‚Â°ng vÃƒÂ¡Ã‚ÂºÃ‚Â«n khÃƒÆ’Ã‚Â´ng lÃƒÂ¡Ã‚ÂºÃ‚Â¥y Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c projectId: ${tab.url}`);
    await sleep(450);
    await injectPage(tabId);
    await appendLog(`PROJECT READY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${projectId}`,'success');
  }else{
    await appendLog(`PROJECT READY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${projectId}`,'success');
  }

  // v14.5.7: a user may have opened an image/video detail URL under /edit/<mediaId>.
  // That child route does NOT expose the Project-level All Media control. Always return to the
  // exact project root first; do not use startsWith(projectRoot) because /edit/... also matches.
  await normalizeProjectRoot(tabId,projectId,'job bÃƒÂ¡Ã‚ÂºÃ‚Â¯t Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â§u');

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
    await appendLog(`ALL MEDIA RECOVERY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ chÃƒâ€ Ã‚Â°a thÃƒÂ¡Ã‚ÂºÃ‚Â¥y nÃƒÆ’Ã‚Âºt sau lÃƒÂ¡Ã‚ÂºÃ‚Â§n Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â§u Ãƒâ€šÃ‚Â· vÃƒÂ¡Ã‚Â»Ã‚Â project root vÃƒÆ’Ã‚Â  thÃƒÂ¡Ã‚Â»Ã‚Â­ lÃƒÂ¡Ã‚ÂºÃ‚Â¡i`,'info');
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
  if(!allPoint) throw lastError||new Error('KhÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y All Media sau khi Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ thoÃƒÆ’Ã‚Â¡t media detail vÃƒÆ’Ã‚Â  reload Project.');
  await trustedClickPoint(tabId,allPoint);
  await sleep(450);
  await appendLog('VIEW READY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ All Media','success');
  return projectId;
}


async function reloadAndNormalizeFlow(tabId, reason='UI recovery', expectedProjectId=null) {
  const beforeTab=await chrome.tabs.get(tabId).catch(()=>null);
  const targetProjectId=String(expectedProjectId||projectIdFromFlowUrl(beforeTab?.url||'')||'').trim()||null;
  await appendLog(`FLOW RECOVERY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${reason} Ãƒâ€šÃ‚Â· F5 / vÃƒÂ¡Ã‚Â»Ã‚Â Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng Project / All Media`, 'info');
  await trustedEscape(tabId).catch(()=>{});
  await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
  await chrome.tabs.reload(tabId).catch(()=>{});
  await waitTabState(tabId,t=>t.status==='complete',30000,'Flow reload');
  await sleep(700);
  await injectPage(tabId);
  let tab=await chrome.tabs.get(tabId);
  let currentProjectId=projectIdFromFlowUrl(tab.url||'');
  if(targetProjectId && currentProjectId!==targetProjectId){
    const targetUrl=flowProjectUrl(targetProjectId,tab.url||beforeTab?.url||'');
    await appendLog(`FLOW RECOVERY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ trÃƒÂ¡Ã‚Â»Ã‚Â lÃƒÂ¡Ã‚ÂºÃ‚Â¡i project cÃƒâ€¦Ã‚Â© ${targetProjectId}`, 'info');
    await chrome.tabs.update(tabId,{url:targetUrl});
    tab=await waitTabState(tabId,t=>t.status==='complete'&&projectIdFromFlowUrl(t.url||'')===targetProjectId,30000,'mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚ÂºÃ‚Â¡i project cÃƒâ€¦Ã‚Â©');
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
  await appendLog(`FLOW RECOVERY READY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ project=${current||'unknown'}`, 'success');
  return current;
}

async function ensureAssetPickerOpenTrusted(tabId){
  if(await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false)) return true;

  const failures=[];
  for(let attempt=1;attempt<=3;attempt++){
    try{
      const point=await callPage(tabId,'getAddMediaPoint',[]);
      await trustedClickPoint(tabId,point);
      if(await waitPageCondition(tabId,'isAssetPickerOpen',true,3500)) return true;
      failures.push({attempt,reason:'picker-not-open',point});
    }catch(error){
      failures.push({attempt,error:error?.message||String(error)});
    }
    // Clear any half-open Radix layer, then retry with fresh coordinates.
    await trustedEscape(tabId).catch(()=>{});
    await sleep(250);
  }
  throw new Error(`Asset Picker khÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ bÃƒÂ¡Ã‚ÂºÃ‚Â±ng CDP trusted click: ${JSON.stringify(failures)}`);
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
  throw new Error(`KhÃƒÆ’Ã‚Â´ng chÃƒÂ¡Ã‚Â»Ã‚Ân Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tab Images bÃƒÂ¡Ã‚ÂºÃ‚Â±ng CDP trusted click: ${JSON.stringify(failures)}`);
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
    await appendLog(`UPLOAD UI ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${point?.label||'Upload Image'}`,'info');
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
  throw new Error('KhÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y input[type=file] nhÃƒÂ¡Ã‚ÂºÃ‚Â­n ÃƒÂ¡Ã‚ÂºÃ‚Â£nh trong Flow sau khi mÃƒÂ¡Ã‚Â»Ã…Â¸ Upload Image.');
}

async function setImageFileInputs(tabId,localPaths,allowRecovery=true){
  const paths=(Array.isArray(localPaths)?localPaths:[localPaths]).map(x=>String(x||'').trim()).filter(Boolean);
  if(!paths.length) throw new Error('Ãƒâ€žÃ‚ÂÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âng dÃƒÂ¡Ã‚ÂºÃ‚Â«n ÃƒÂ¡Ã‚ÂºÃ‚Â£nh upload Ãƒâ€žÃ¢â‚¬Ëœang rÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ng.');
  const d=debuggeeFor(tabId);
  let hit=await findBestImageFileInput(tabId);
  if(hit && hit.score>0){
    try{
      await chrome.debugger.sendCommand(d,'DOM.setFileInputFiles',{files:paths,nodeId:hit.nodeId});
      return {ok:true,paths,nodeId:hit.nodeId,accept:hit.attrs?.accept||'',mode:'existing-input',multiple:paths.length>1};
    }catch(error){
      if(paths.length===1) throw error;
      await appendLog(`UPLOAD MULTI existing-input lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ thÃƒÂ¡Ã‚Â»Ã‚Â­ chooser: ${error?.message||String(error)}`,'info');
    }
  }

  try{
    await ensureAssetPickerOpenTrusted(tabId);
    await chrome.debugger.sendCommand(d,'Page.enable').catch(()=>{});
    await chrome.debugger.sendCommand(d,'Page.setInterceptFileChooserDialog',{enabled:true}).catch(()=>{});
    const chooserPromise=waitFileChooser(tabId,5000).catch(()=>null);
    const point=await callPage(tabId,'getUploadImagePoint',[]);
    await appendLog(`UPLOAD UI ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${point?.label||'Upload Image'} Ãƒâ€šÃ‚Â· ${paths.length} file`,'info');
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
    await appendLog(`UPLOAD UI khÃƒÆ’Ã‚Â´ng sÃƒÂ¡Ã‚ÂºÃ‚Âµn sÃƒÆ’Ã‚Â ng ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ tÃƒÂ¡Ã‚Â»Ã‚Â± F5/recover: ${error?.message||String(error)}`,'info');
    await reloadAndNormalizeFlow(tabId,'khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y chÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ upload ÃƒÂ¡Ã‚ÂºÃ‚Â£nh');
    const retried=await setImageFileInputs(tabId,paths,false);
    return {...retried,recovered:true};
  }
  if(allowRecovery){
    await reloadAndNormalizeFlow(tabId,'khÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y input/chooser upload ÃƒÂ¡Ã‚ÂºÃ‚Â£nh');
    const retried=await setImageFileInputs(tabId,paths,false);
    return {...retried,recovered:true};
  }
  throw new Error('KhÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y file input/chooser Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ upload ÃƒÂ¡Ã‚ÂºÃ‚Â£nh vÃƒÆ’Ã‚Â o Flow sau recovery.');
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
    if(event.failed) return {error:new Error(`Upload ÃƒÂ¡Ã‚ÂºÃ‚Â£nh lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i Network: ${event.errorText||'unknown'}`)};
    if(!event.loadingFinished) return false;
    const status=Number(event.status||0);
    if(status && (status<200||status>=300)) return {error:new Error(`Upload ÃƒÂ¡Ã‚ÂºÃ‚Â£nh HTTP ${status}`)};
    return {value:{event,mediaId:uploadMediaIdFromResponse(event.json)}};
  },timeoutMs,'IMAGE UPLOAD');
}

async function findUploadedMediaByName(tabId,name,timeoutMs=25000,exactOnly=false){
  const q=String(name||'').trim();
  await ensureAssetPickerOpenTrusted(tabId);
  await ensureImagesTabTrusted(tabId);
  await callPage(tabId,'setAssetSearch',[q]);
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const items=await callPage(tabId,'listSearchedImages',[]).catch(()=>[]);
    const exact=(items||[]).find(x=>String(x?.title||'').trim()===q && x?.mediaId);
    const hit=exact||(!exactOnly?(items||[]).find(x=>x?.mediaId):null);
    if(hit?.mediaId) return hit;
    await sleep(350);
  }
  return null;
}

async function findUploadedMediaByNameExcluding(tabId,name,excludeIds,timeoutMs=12000){
  const q=String(name||'').trim();
  const excluded=new Set([...(excludeIds||[])].map(x=>String(x||'').trim()).filter(Boolean));
  await ensureAssetPickerOpenTrusted(tabId);
  await ensureImagesTabTrusted(tabId);
  await callPage(tabId,'setAssetSearch',[q]);
  const started=Date.now();
  while(Date.now()-started<timeoutMs){
    const items=await callPage(tabId,'listSearchedImages',[]).catch(()=>[]);
    const hit=(items||[]).find(x=>x?.mediaId && !excluded.has(String(x.mediaId)) && String(x?.title||'').trim()===q)
      ||(items||[]).find(x=>x?.mediaId && !excluded.has(String(x.mediaId)));
    if(hit?.mediaId) return hit;
    await sleep(300);
  }
  return null;
}

async function snapshotUploadedMediaIdsByName(tabId,name){
  const q=String(name||'').trim();
  if(!q) return new Set();
  try{
    await ensureAssetPickerOpenTrusted(tabId);
    await ensureImagesTabTrusted(tabId);
    await callPage(tabId,'setAssetSearch',[q]);
    await sleep(650);
    const items=await callPage(tabId,'listSearchedImages',[]).catch(()=>[]);
    return new Set((items||[]).map(x=>String(x?.mediaId||'').trim()).filter(Boolean));
  }catch{return new Set();}
}

async function attachFreshUploadedMedia(tabId,{name,mediaId,beforeIds,originalMediaId,projectId,tag,role}){
  let wanted=String(mediaId||'').trim();
  const excluded=new Set([...(beforeIds||[]),originalMediaId,...STALE_SERVER_MEDIA_IDS].map(x=>String(x||'').trim()).filter(Boolean));
  if(wanted){
    try{
      const exact=await trustedAttachIngredient(tabId,[name],wanted,45000);
      if(exact?.ok) return {...exact,mediaId:wanted,correlation:'upload-response-id'};
    }catch(error){
      await appendLog(`TAB ${tag} REF ${role} upload mediaId=${wanted} chÃƒâ€ Ã‚Â°a index sau 45s ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ tÃƒÆ’Ã‚Â¬m asset mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi theo filename, KHÃƒÆ’Ã¢â‚¬ÂNG upload lÃƒÂ¡Ã‚ÂºÃ‚Â¡i`, 'info');
    }
  }

  const fresh=await findUploadedMediaByNameExcluding(tabId,name,excluded,15000).catch(()=>null);
  if(fresh?.mediaId){
    const freshId=String(fresh.mediaId);
    const attached=await trustedAttachIngredient(tabId,[fresh.title||name,name],freshId,15000).catch(()=>null);
    if(attached?.ok){
      return {...attached,mediaId:freshId,title:attached.title||fresh.title||name,correlation:'fresh-filename-new-id'};
    }
  }

  // Asset Picker indexing can lag behind upload. Reload the SAME project, including
  // locale-aware /fx/vi/... routes, then retry exact identity once. Never re-upload here.
  if(projectId){
    await reloadAndNormalizeFlow(tabId,`upload ${name} chÃƒÂ¡Ã‚Â»Ã‚Â Asset Picker index`,projectId).catch(()=>{});
    if(wanted){
      const exactAfterReload=await trustedAttachIngredient(tabId,[name],wanted,30000).catch(()=>null);
      if(exactAfterReload?.ok) return {...exactAfterReload,mediaId:wanted,correlation:'upload-response-id-after-project-reload'};
    }
    const freshAfterReload=await findUploadedMediaByNameExcluding(tabId,name,excluded,10000).catch(()=>null);
    if(freshAfterReload?.mediaId){
      const freshId=String(freshAfterReload.mediaId);
      const attached=await trustedAttachIngredient(tabId,[freshAfterReload.title||name,name],freshId,15000).catch(()=>null);
      if(attached?.ok) return {...attached,mediaId:freshId,title:attached.title||freshAfterReload.title||name,correlation:'fresh-filename-after-project-reload'};
    }
  }
  throw new Error(`FLOW_ASSET_INDEX_DELAY Ãƒâ€šÃ‚Â· Upload ${name} Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ thÃƒÆ’Ã‚Â nh cÃƒÆ’Ã‚Â´ng${wanted?` Ãƒâ€šÃ‚Â· mediaId=${wanted}`:''} nhÃƒâ€ Ã‚Â°ng Asset Picker chÃƒâ€ Ã‚Â°a Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“ng bÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ asset mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi. KhÃƒÆ’Ã‚Â´ng upload lÃƒÂ¡Ã‚ÂºÃ‚Â¡i Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ trÃƒÆ’Ã‚Â¡nh duplicate; hÃƒÆ’Ã‚Â£y giÃƒÂ¡Ã‚Â»Ã‚Â¯ project mÃƒÂ¡Ã‚Â»Ã…Â¸ vÃƒÆ’Ã‚Â  retry job.`);
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

async function tryReuseExistingLibraryAsset(tabId,input,tag){
  const names=[
    String(input?.fileName||'').trim(),
    String(input?.name||'').trim(),
    String(input?.title||'').trim(),
    String(input?.path||'').trim().split(/[\\/]/).pop()||''
  ].filter(Boolean);
  for(const q of [...new Set(names)]){
    const found=await findUploadedMediaByName(tabId,q,5000,true).catch(()=>null);
    if(found?.mediaId){
      await appendLog(`TAB ${tag} REF existing-file-name hit ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${found.mediaId} (${q})`,'info');
      return {mediaId:String(found.mediaId),title:String(found.title||q),name:q};
    }
    const base=q.replace(/\.[^.]+$/,'');
    if(base&&base!==q){
      const baseHit=await findUploadedMediaByName(tabId,base,3000,true).catch(()=>null);
      if(baseHit?.mediaId){
        await appendLog(`TAB ${tag} REF existing-base-name hit ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${baseHit.mediaId} (${base})`,'info');
        return {mediaId:String(baseHit.mediaId),title:String(baseHit.title||base),name:q};
      }
    }
  }
  return null;
}

async function uploadAndAttachLocalImage(tabId,input,options,tag){
  assertServerControlAlive();
  const path=String(input?.path||'').trim();
  const name=String(input?.name||'').trim()||path.split(/[\\/]/).pop()||'image';
  const role=String(input?.role||'reference');
  const originalMediaId=referenceSuppliedMediaId(input);
  const replacementId=originalMediaId?String(SERVER_MEDIA_REPLACEMENTS.get(originalMediaId)||''):'';
  let suppliedMediaId=replacementId || originalMediaId;
  const suppliedTitle=String(input?.title||name||'').trim()||name;
  const cache=options.assetCache instanceof Map?options.assetCache:GLOBAL_ASSET_CACHE;
  let forceUpload=false;

  // v14.6.0: selected reference chips are authoritative. Check all known identities
  // for this input before touching Asset Picker. This is the DOM supplied by Flow:
  // img src=/fx/api/trpc/media.getMediaUrlRedirect?name=<mediaId>.
  const composerStateAtStart=await getComposerMediaStateSafe(tabId);
  for(const knownId of referenceKnownMediaIds(input,options)){
    if(composerHasMediaIdState(composerStateAtStart,knownId)){
      const saved={mediaId:knownId,title:suppliedTitle,name,role,path};
      if(path){GLOBAL_ASSET_CACHE.set(path,saved);cache?.set(path,saved);}
      if(originalMediaId&&knownId!==originalMediaId) markReferenceMediaReplaced(input,originalMediaId,knownId);
      await appendLog(`TAB ${tag} REF ${role} COMPOSER Ãƒâ€žÃ‚ÂÃƒÆ’Ã†â€™ CÃƒÆ’Ã¢â‚¬Å“ ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${knownId} Ãƒâ€šÃ‚Â· bÃƒÂ¡Ã‚Â»Ã‚Â picker/upload`,'success');
      return {...saved,attached:true,cacheHit:true,composerAlreadyAttached:true};
    }
  }

  // If this exact server id already failed earlier, do not search it again. Use the
  // replacement id if one was learned; otherwise go straight to the stable local file.
  if(originalMediaId && STALE_SERVER_MEDIA_IDS.has(originalMediaId) && !replacementId){
    forceUpload=true;
    suppliedMediaId='';
    const cachedOld=path?(cache?.get(path)||GLOBAL_ASSET_CACHE.get(path)):null;
    if(cachedOld?.mediaId===originalMediaId){cache?.delete(path);GLOBAL_ASSET_CACHE.delete(path);}
    await appendLog(`TAB ${tag} REF ${role} stale-mediaId Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ nhÃƒÂ¡Ã‚Â»Ã¢â‚¬Âº ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ bÃƒÂ¡Ã‚Â»Ã‚Â tÃƒÆ’Ã‚Â¬m ${originalMediaId} Ãƒâ€šÃ‚Â· upload local ngay`,'info');
  }

  // Exact mediaId is identity. Give it a short probe only. If it is absent, do NOT
  // accept another image with the same filename but a different mediaId; upload local.
  if(suppliedMediaId && !forceUpload){
    const sourceLabel=replacementId?'replacement-mediaId':'server-mediaId';
    await appendLog(`TAB ${tag} REF ${role} ${sourceLabel} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${suppliedMediaId} Ãƒâ€šÃ‚Â· exact probe tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi Ãƒâ€žÃ¢â‚¬Ëœa 10s`,'info');
    try{
      const attached=await trustedAttachIngredient(tabId,[suppliedTitle,name],suppliedMediaId,10000);
      if(!attached?.ok||attached.mediaId!==suppliedMediaId) throw new Error('attach exact mediaId verify=false');
      const saved={mediaId:suppliedMediaId,title:attached.title||suppliedTitle,name,role,path};
      if(path){GLOBAL_ASSET_CACHE.set(path,saved);cache?.set(path,saved);}
      return {...saved,attached:true,cacheHit:true,serverMediaId:!replacementId,replacementMediaId:Boolean(replacementId)};
    }catch(error){
      if(originalMediaId){
        STALE_SERVER_MEDIA_IDS.add(originalMediaId);
        if(replacementId) SERVER_MEDIA_REPLACEMENTS.delete(originalMediaId);
      }
      if(path){
        const cachedOld=cache?.get(path)||GLOBAL_ASSET_CACHE.get(path);
        if(cachedOld?.mediaId===suppliedMediaId||cachedOld?.mediaId===originalMediaId){cache?.delete(path);GLOBAL_ASSET_CACHE.delete(path);}
      }
      if(!path) throw new Error(`${tag} REF ${role} mediaId=${suppliedMediaId} khÃƒÆ’Ã‚Â´ng attach Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c vÃƒÆ’Ã‚Â  server khÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ file local Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ upload lÃƒÂ¡Ã‚ÂºÃ‚Â¡i: ${error?.message||error}`);
      forceUpload=true;
      await appendLog(`TAB ${tag} REF ${role} exact mediaId KHÃƒÆ’Ã¢â‚¬ÂNG CÃƒÆ’Ã¢â‚¬â„¢N THÃƒÂ¡Ã‚ÂºÃ‚Â¤Y ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ upload lÃƒÂ¡Ã‚ÂºÃ‚Â¡i file local (${name}). KhÃƒÆ’Ã‚Â´ng reuse ÃƒÂ¡Ã‚ÂºÃ‚Â£nh cÃƒÆ’Ã‚Â¹ng tÃƒÆ’Ã‚Âªn khÃƒÆ’Ã‚Â¡c mediaId.`,'info');
      await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    }
  }

  // A replacement learned earlier in this process wins over stale server metadata.
  let cached=path?(cache?.get(path)||GLOBAL_ASSET_CACHE.get(path)||null):null;
  if(cached?.mediaId && !STALE_SERVER_MEDIA_IDS.has(String(cached.mediaId))){
    await appendLog(`TAB ${tag} REF ${role} global-cache ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${cached.mediaId}`,'info');
    try{
      const attached=await trustedAttachIngredient(tabId,[cached.title||name,name],cached.mediaId,10000);
      if(!attached?.ok) throw new Error('attach cache trÃƒÂ¡Ã‚ÂºÃ‚Â£ vÃƒÂ¡Ã‚Â»Ã‚Â false');
      GLOBAL_ASSET_CACHE.set(path,cached);cache?.set(path,cached);
      if(originalMediaId&&cached.mediaId!==originalMediaId) markReferenceMediaReplaced(input,originalMediaId,cached.mediaId);
      return {...cached,attached:true,cacheHit:true,globalCache:true};
    }catch(error){
      await appendLog(`TAB ${tag} REF cache stale (${role}) ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ upload lÃƒÂ¡Ã‚ÂºÃ‚Â¡i: ${error?.message||error}`,'info');
      STALE_SERVER_MEDIA_IDS.add(String(cached.mediaId));
      cache?.delete(path);GLOBAL_ASSET_CACHE.delete(path);
      await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
      forceUpload=true;
    }
  }

  // Filename reuse is allowed only when the server did NOT provide an identity id.
  // With a supplied id, same-name/different-id is explicitly treated as a mismatch.
  if(!originalMediaId && !forceUpload && !cached?.mediaId){
    const reused=await tryReuseExistingLibraryAsset(tabId,input,tag).catch(()=>null);
    if(reused?.mediaId){
      const attached=await trustedAttachIngredient(tabId,[reused.title||suppliedTitle,name],reused.mediaId,10000);
      if(attached?.ok){
        const saved={mediaId:reused.mediaId,title:attached.title||reused.title||suppliedTitle,name,role,path};
        if(path){GLOBAL_ASSET_CACHE.set(path,saved);cache?.set(path,saved);}
        return {...saved,attached:true,cacheHit:true,reusedByName:true};
      }
    }
  }

  if(!path) throw new Error(`${tag} REF ${role} khÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ file local Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ upload.`);
  assertServerControlAlive();
  await appendLog(`TAB ${tag} UPLOAD REF ${role}: ${name}${originalMediaId?' Ãƒâ€šÃ‚Â· thay mediaId stale '+originalMediaId:''}`,'info');
  const beforeState=await getComposerMediaStateSafe(tabId);
  const beforeCount=Number(beforeState?.count||0);
  const beforeComposerIds=new Set((beforeState?.mediaIds||[]).map(String));
  await ensureAssetPickerOpenTrusted(tabId);
  assertServerControlAlive();
  // Snapshot exact-name IDs BEFORE upload. If Flow later exposes a different mediaId
  // than the upload response, only an ID not present in this snapshot may be adopted.
  const beforeLibraryIds=await snapshotUploadedMediaIdsByName(tabId,name);
  await callPage(tabId,'setAssetSearch',['']).catch(()=>{});
  const marker=createNetworkMarker(tabId);
  const uploadAction=await setImageFileInput(tabId,path);

  let upload=null;
  try{upload=await waitUploadImageAfterMarker(tabId,marker,90000);}catch(error){
    await appendLog(`TAB ${tag} upload response chÃƒâ€ Ã‚Â°a Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã‚Âc Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c (${name}): ${error?.message||String(error)} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ kiÃƒÂ¡Ã‚Â»Ã†â€™m composer/picker`,'info');
  }
  assertServerControlAlive();
  let mediaId=upload?.mediaId||null;
  let title=name;
  if(mediaId && originalMediaId && String(mediaId)===originalMediaId){
    // A stale response must never resurrect the same hidden identity after re-upload.
    mediaId=null;
  }
  const composerAttached=await waitComposerRefIncrease(tabId,beforeCount,8000);
  if(composerAttached){
    await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    // The composer DOM is more trustworthy than upload-response timing. Extract the
    // newly attached mediaId directly from media.getMediaUrlRedirect?name=<uuid>.
    const afterState=await getComposerMediaStateSafe(tabId);
    const newComposerIds=(afterState?.mediaIds||[]).map(String).filter(id=>!beforeComposerIds.has(id));
    if(newComposerIds.length) mediaId=newComposerIds.at(-1);
    if(!mediaId){
      const found=originalMediaId?await findUploadedMediaByNameExcluding(tabId,name,[originalMediaId,...STALE_SERVER_MEDIA_IDS],8000).catch(()=>null):await findUploadedMediaByName(tabId,name,8000).catch(()=>null);
      mediaId=found?.mediaId||null;title=found?.title||title;
    }
    const saved={mediaId:mediaId||`composer:${Date.now()}:${name}`,title,name,role,path};
    if(mediaId&&!String(mediaId).startsWith('composer:')){GLOBAL_ASSET_CACHE.set(path,saved);cache?.set(path,saved);if(originalMediaId)markReferenceMediaReplaced(input,originalMediaId,mediaId);}
    await appendLog(`TAB ${tag} REF ATTACHED ${role} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ composer-dom${mediaId?` Ãƒâ€šÃ‚Â· ${mediaId}`:''} (${name})`,'success');
    return {...saved,attached:true,cacheHit:false,composerDirect:true,uiRecovered:Boolean(uploadAction?.recovered),reuploadedBecauseStale:Boolean(originalMediaId)};
  }

  let pickerOpen=await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false);
  if(!mediaId){
    if(!pickerOpen) await ensureAssetPickerOpenTrusted(tabId);
    const found=originalMediaId?await findUploadedMediaByNameExcluding(tabId,name,[originalMediaId,...STALE_SERVER_MEDIA_IDS],20000):await findUploadedMediaByName(tabId,name,20000);
    mediaId=found?.mediaId||null;title=found?.title||title;
    pickerOpen=await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false);
  }
  if(!mediaId) throw new Error(`${tag} Upload ${name} xong nhÃƒâ€ Ã‚Â°ng khÃƒÆ’Ã‚Â´ng xÃƒÆ’Ã‚Â¡c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c mediaId vÃƒÆ’Ã‚Â  composer chÃƒâ€ Ã‚Â°a attach.`);
  if(!pickerOpen) await ensureAssetPickerOpenTrusted(tabId);
  const attached=await attachFreshUploadedMedia(tabId,{
    name,mediaId,beforeIds:beforeLibraryIds,originalMediaId,projectId:options?.projectId,tag,role
  });
  if(!attached?.ok) throw new Error(`${tag} Upload ${name} nhÃƒâ€ Ã‚Â°ng attach asset mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi thÃƒÂ¡Ã‚ÂºÃ‚Â¥t bÃƒÂ¡Ã‚ÂºÃ‚Â¡i.`);
  mediaId=String(attached.mediaId||mediaId);
  title=attached.title||title;
  const saved={mediaId,title,name,role,path};
  GLOBAL_ASSET_CACHE.set(path,saved);cache?.set(path,saved);
  if(originalMediaId) markReferenceMediaReplaced(input,originalMediaId,mediaId);
  await appendLog(`TAB ${tag} REF ATTACHED ${role} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${mediaId} (${name})${originalMediaId?' Ãƒâ€šÃ‚Â· mediaId Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ refresh':''}`,'success');
  return {...saved,attached:true,cacheHit:false,uiRecovered:Boolean(uploadAction?.recovered),reuploadedBecauseStale:Boolean(originalMediaId)};
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
  const cache=options.assetCache instanceof Map?options.assetCache:null;
  const cached=[],missing=[];
  const results=new Map();

  // Server mediaIds require strict identity semantics. Resolve them one by one through
  // uploadAndAttachLocalImage so stale ids immediately fall back to the stable local file.
  // This avoids the old batch path waiting 60s and then failing without upload fallback.
  for(const input of inputs){
    assertServerControlAlive();
    const path=String(input?.path||'').trim();
    const supplied=referenceSuppliedMediaId(input);
    if(supplied){
      const row=await uploadAndAttachLocalImage(tabId,input,options,tag);
      results.set(inputRefKey(input),row);
      continue;
    }
    const hit=path?(cache?.get(path)||GLOBAL_ASSET_CACHE.get(path)||null):null;
    if(hit?.mediaId) cached.push({input,hit}); else missing.push(input);
  }

  // Local cache rows without server identity are still attached normally.
  for(const {input,hit} of cached){
    const name=String(input?.name||hit.name||'image');
    await appendLog(`TAB ${tag} REF ${input?.role||'reference'} cache ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${hit.mediaId}`,'info');
    try{
      const attached=await trustedAttachIngredient(tabId,[hit.title||name,name],hit.mediaId,10000);
      if(!attached?.ok) throw new Error(`${tag} KhÃƒÆ’Ã‚Â´ng attach lÃƒÂ¡Ã‚ÂºÃ‚Â¡i Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c ÃƒÂ¡Ã‚ÂºÃ‚Â£nh cache ${name}.`);
      results.set(inputRefKey(input),{...hit,role:input?.role||hit.role,attached:true,cacheHit:true});
    }catch(error){
      if(hit?.mediaId) STALE_SERVER_MEDIA_IDS.add(String(hit.mediaId));
      if(String(input?.path||'')){cache?.delete(String(input.path));GLOBAL_ASSET_CACHE.delete(String(input.path));}
      missing.push(input);
      await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    }
  }
  if(missing.length){
    const still=[];
    for(const input of missing){
      const reused=await tryReuseExistingLibraryAsset(tabId,input,tag).catch(()=>null);
      if(reused?.mediaId){
        const attached=await trustedAttachIngredient(tabId,[reused.title||input?.title||input?.name,reused.name||input?.name],reused.mediaId,30000);
        if(!attached?.ok) throw new Error(`${tag} KhÃƒÆ’Ã‚Â´ng attach lÃƒÂ¡Ã‚ÂºÃ‚Â¡i Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c asset cÃƒâ€¦Ã‚Â© ${reused.name||input?.name||'image'}.`);
        const saved={mediaId:reused.mediaId,title:attached.title||reused.title||String(input?.title||input?.name||''),name:String(input?.name||reused.name||'image'),role:String(input?.role||'reference'),path:String(input?.path||'')};
        if(saved.path){ GLOBAL_ASSET_CACHE.set(saved.path,saved); cache?.set(saved.path,saved); }
        results.set(inputRefKey(input),{...saved,attached:true,cacheHit:true,reusedByName:true});
      }else{
        still.push(input);
      }
    }
    missing.splice(0,missing.length,...still);
  }
  if(!missing.length) return inputs.map(i=>results.get(inputRefKey(i)));
  if(missing.length===1){
    const one=await uploadAndAttachLocalImage(tabId,missing[0],options,tag);
    results.set(inputRefKey(missing[0]),one);
    return inputs.map(i=>results.get(inputRefKey(i)));
  }

  const existingInput=await findBestImageFileInput(tabId).catch(()=>null);
  if(existingInput && existingInput.score>0 && !Object.prototype.hasOwnProperty.call(existingInput.attrs||{},'multiple')){
    await appendLog(`TAB ${tag} uploader hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡n tÃƒÂ¡Ã‚ÂºÃ‚Â¡i khÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ multiple ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ fallback upload tuÃƒÂ¡Ã‚ÂºÃ‚Â§n tÃƒÂ¡Ã‚Â»Ã‚Â±`, 'info');
    for(const input of missing){
      const row=await uploadAndAttachLocalImage(tabId,input,options,tag);
      results.set(inputRefKey(input),row);
    }
    return inputs.map(i=>results.get(inputRefKey(i)));
  }
  await appendLog(`TAB ${tag} UPLOAD MULTI REF ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${missing.length} ÃƒÂ¡Ã‚ÂºÃ‚Â£nh trong 1 lÃƒÂ¡Ã‚ÂºÃ‚Â§n (${missing.map(x=>x.role||'ref').join(' + ')})`,'info');
  await ensureAssetPickerOpenTrusted(tabId);
  const marker=createNetworkMarker(tabId);
  const uploadAction=await setImageFileInputs(tabId,missing.map(x=>x.path));
  const events=await recentUploadEventsAfterMarker(tabId,marker,missing.length,15000);
  let pickerOpen=await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false);
  const autoAttached=!pickerOpen;
  const byOrder=events.map(e=>uploadMediaIdFromResponse(e.json)).filter(Boolean);

  for(let i=0;i<missing.length;i++){
    const input=missing[i];
    let found=await findUploadedMediaByCandidates(tabId,input,25000).catch(()=>null);
    let mediaId=found?.mediaId||byOrder[i]||null;
    const name=String(input?.name||'image');
    if(!mediaId) throw new Error(`${tag} Upload multi ${name} xong nhÃƒâ€ Ã‚Â°ng khÃƒÆ’Ã‚Â´ng xÃƒÆ’Ã‚Â¡c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c mediaId.`);
    const saved={mediaId,title:found?.title||name,name,role:String(input?.role||'reference'),path:String(input.path)};
    cache?.set(String(input.path),saved); GLOBAL_ASSET_CACHE.set(String(input.path),saved);
    results.set(inputRefKey(input),{...saved,attached:autoAttached,cacheHit:false,batchUpload:true,uploadMode:uploadAction.mode,uiRecovered:Boolean(uploadAction?.recovered)});
  }

  pickerOpen=await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false);
  if(autoAttached){
    if(pickerOpen) await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
  }else{
    // If Flow uploaded to the library without auto-attaching, attach exact mediaIds now.
    for(const input of missing){
      const row=results.get(inputRefKey(input));
      const attached=await trustedAttachIngredient(tabId,[row.title,row.name],row.mediaId,60000);
      if(!attached?.ok) throw new Error(`${tag} Upload multi nhÃƒâ€ Ã‚Â°ng attach ${row.name} thÃƒÂ¡Ã‚ÂºÃ‚Â¥t bÃƒÂ¡Ã‚ÂºÃ‚Â¡i.`);
      row.attached=true; row.title=attached.title||row.title;
    }
  }
  for(const input of missing){
    const row=results.get(inputRefKey(input));
    await appendLog(`TAB ${tag} REF ATTACHED ${row.role} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${row.mediaId} (${row.name}) Ãƒâ€šÃ‚Â· multi=${missing.length}`,'success');
  }
  return inputs.map(i=>results.get(inputRefKey(i)));
}

async function ensureSceneImageInputs(tabId,record,options,stage='image'){
  const allInputs=Array.isArray(record?.pair?.inputImages)?record.pair.inputImages:[];
  const inputs=stage==='video'?allInputs.filter(x=>x?.videoReference!==false):allInputs;
  if(!inputs.length) return [];
  const tag=`[${record.index+1}]`;
  if(stage==='video'&&inputs.length!==allInputs.length){
    await appendLog(`TAB ${tag} VIDEO REF FILTER ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${inputs.length}/${allInputs.length} refs; image-only refs giÃƒÂ¡Ã‚Â»Ã‚Â¯ trong scene image`,'info');
  }
  const attached=await uploadAndAttachLocalImagesBatch(tabId,inputs,options,tag);
  await patchJob(record.index,{inputRefs:attached.map(x=>({role:x.role,name:x.name,mediaId:x.mediaId,cacheHit:x.cacheHit,batchUpload:Boolean(x.batchUpload)}))});
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

async function trustedAttachIngredient(tabId,searchCandidates,mediaId,timeoutMs=60000){
  const wanted=String(mediaId||'').trim();
  if(!wanted) throw new Error('Ingredient mediaId Ãƒâ€žÃ¢â‚¬Ëœang rÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ng.');
  const candidates=[...new Set((Array.isArray(searchCandidates)?searchCandidates:[searchCandidates]).map(assetSearchText).filter(Boolean))];

  // v14.6.0: BEFORE picker/search/upload, verify the selected-reference chips in
  // the composer. If this mediaId is already attached, there is nothing to do.
  const composerState=await getComposerMediaStateSafe(tabId);
  if(composerHasMediaIdState(composerState,wanted)){
    if(await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false)) await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    return {ok:true,mediaId:wanted,title:'',method:'composer-dom',searchUsed:'',searchMode:'COMPOSER_ALREADY_ATTACHED'};
  }

  await ensureAssetPickerOpenTrusted(tabId);
  await ensureImagesTabTrusted(tabId);

  // Important: first look for the exact mediaId with NO text filter. A wrong/rephrased/
  // translated asset title must never hide the correct freshly generated image.
  // Only if the unfiltered recent list does not expose it do we try text filters.
  const phases=[{query:'',label:'NO_SEARCH',budgetMs:Math.min(15000,Math.max(5000,Math.floor(timeoutMs*0.25)))}];
  const remaining=Math.max(5000,timeoutMs-phases[0].budgetMs);
  const perCandidate=candidates.length?Math.max(5000,Math.floor(remaining/candidates.length)):remaining;
  for(const query of candidates) phases.push({query,label:'TEXT_FALLBACK',budgetMs:perCandidate});

  const started=Date.now();
  const tried=[];
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
        await trustedClickPoint(tabId,point);
        if(await waitPageCondition(tabId,'isAssetPickerOpen',false,4500)){
          return {ok:true,mediaId:wanted,title:point.title||'',method:'cdp-trusted',searchUsed:query,searchMode:phase.label};
        }

        const fresh=await callPage(tabId,'getAssetOptionPoint',[wanted]).catch(()=>null);
        if(fresh?.mediaId===wanted){
          await trustedClickPoint(tabId,fresh);
          if(await waitPageCondition(tabId,'isAssetPickerOpen',false,4500)){
            return {ok:true,mediaId:wanted,title:fresh.title||'',method:'cdp-trusted-retry',searchUsed:query,searchMode:phase.label};
          }
        }
        throw new Error(`Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ trusted-click Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng mediaId=${wanted} nhÃƒâ€ Ã‚Â°ng Asset Picker chÃƒâ€ Ã‚Â°a Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng.`);
      }

      if(Date.now()-lastRefresh>2500){
        lastRefresh=Date.now();
        await ensureImagesTabTrusted(tabId);
        await callPage(tabId,'setAssetSearch',[query]);
      }
      // Flow's virtualized Asset Picker can keep a stale result set while image jobs
      // finish in the background. Reopen the picker periodically to force a real refresh.
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

  throw new Error(`Asset Picker Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ mÃƒÂ¡Ã‚Â»Ã…Â¸ nhÃƒâ€ Ã‚Â°ng khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng mediaId=${wanted} sau ${Math.round(timeoutMs/1000)}s. Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ thÃƒÂ¡Ã‚Â»Ã‚Â­: ${tried.map(x=>JSON.stringify(x)).join(' ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ')}`);
}

async function trustedEscape(tabId){
  const d=debuggeeFor(tabId);
  await chrome.debugger.sendCommand(d,'Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
  await chrome.debugger.sendCommand(d,'Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
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

async function ensureSettingsOpenTrusted(tabId){
  if(await callPage(tabId,'isSettingsOpen',[]).catch(()=>false)) return true;
  // A half-open Asset Picker/Radix overlay can intercept the trusted Settings click.
  if(await callPage(tabId,'isAssetPickerOpen',[]).catch(()=>false)){
    await callPage(tabId,'closeAssetPicker',[]).catch(()=>{});
    await trustedEscape(tabId).catch(()=>{});
    await sleep(180);
  }
  const failures=[];
  for(let attempt=1;attempt<=2;attempt++){
    try{
      const point=await callPage(tabId,'getSettingsTriggerPoint',[]);
      await trustedClickPoint(tabId,point);
      if(await waitPageCondition(tabId,'isSettingsOpen',true,2500)) return true;
      failures.push({attempt,reason:'trusted-click-no-open',point});
    }catch(error){failures.push({attempt,error:error?.message||String(error)});}
    await trustedEscape(tabId).catch(()=>{});
    await sleep(180);
  }
  // React/native fallback in page world. This is still limited to the visible Flow UI.
  try{
    await callPage(tabId,'openSettings',[]);
    if(await waitPageCondition(tabId,'isSettingsOpen',true,2500)) return true;
  }catch(error){failures.push({fallback:'page-openSettings',error:error?.message||String(error)});}
  throw new Error(`KhÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c Settings sau trusted + page fallback: ${JSON.stringify(failures)}`);
}

async function ensureSettingsClosedTrusted(tabId){
  if(!(await callPage(tabId,'isSettingsOpen',[]).catch(()=>false))) return true;
  const point=await callPage(tabId,'getSettingsTriggerPoint',[]);
  await trustedClickPoint(tabId,point);
  if(await waitPageCondition(tabId,'isSettingsOpen',false,1800)) return true;
  await trustedEscape(tabId);
  if(await waitPageCondition(tabId,'isSettingsOpen',false,1800)) return true;
  throw new Error('KhÃƒÆ’Ã‚Â´ng Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c Settings bÃƒÂ¡Ã‚ÂºÃ‚Â±ng CDP/Escape.');
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
  if(!trigger?.alreadySelected) throw new Error(`Model chÃƒâ€ Ã‚Â°a Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng sau trusted click. cÃƒÂ¡Ã‚ÂºÃ‚Â§n=${requested}, hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡n=${trigger?.current||'unknown'}`);
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
      if(attempt<maxAttempts && /khÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c Settings|khÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y nÃƒÆ’Ã‚Âºt Settings|page fallback/i.test(text)){
        await reloadAndNormalizeFlow(tabId,`Settings khÃƒÆ’Ã‚Â´ng phÃƒÂ¡Ã‚ÂºÃ‚Â£n hÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i Ãƒâ€šÃ‚Â· attempt ${attempt}`).catch(()=>{});
      }
    }
    await ensureSettingsClosedTrusted(tabId).catch(async()=>{await trustedEscape(tabId).catch(()=>{});});
    await sleep(250);
  }
  throw new Error(`SETTINGS VERIFY FAILED sau ${maxAttempts} lÃƒÂ¡Ã‚ÂºÃ‚Â§n: ${JSON.stringify(failures)}`);
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

function mediaTypeHints(item){
  const md=item?.mediaMetadata||{};
  const gv=item?.video?.generatedVideo||{};
  const gi=item?.image?.generatedImage||{};
  const vals=[
    item?.mimeType,item?.contentType,item?.mediaType,item?.type,item?.kind,
    md?.mimeType,md?.mediaMimeType,md?.contentType,md?.mediaType,md?.mediaKind,
    gv?.mimeType,gv?.contentType,gi?.mimeType,gi?.contentType
  ].filter(v=>typeof v==='string'&&v.trim()).map(v=>String(v).toLowerCase());
  return vals;
}

function isExplicitVideoMedia(item){
  if(!item||typeof item!=='object') return false;
  // Fail closed: if this row carries an image payload, it is NOT a video ID.
  if(item?.image?.generatedImage || item?.generatedImage || item?.imageMedia) return false;
  const hints=mediaTypeHints(item);
  if(hints.some(v=>v.includes('image/')||v==='image'||v.includes('generated_image'))) return false;
  if(item?.video?.generatedVideo || item?.generatedVideo || item?.videoMedia) return true;
  if(hints.some(v=>v.includes('video/')||v==='video'||v.includes('generated_video'))) return true;
  const url=String(item?.video?.generatedVideo?.fifeUrl||item?.video?.generatedVideo?.url||item?.fifeUrl||item?.url||'').toLowerCase();
  if(url && (url.includes('/video/')||url.includes('flow-content.google/video')||url.match(/\.(mp4|webm)(\?|$)/))) return true;
  return false;
}

function workflowLooksVideo(wf){
  if(!wf||typeof wf!=='object') return false;
  const md=wf?.metadata||{};
  const vals=[md?.mimeType,md?.mediaMimeType,md?.mediaType,md?.type,md?.kind,wf?.type,wf?.kind]
    .filter(v=>typeof v==='string').map(v=>String(v).toLowerCase());
  return vals.some(v=>v.includes('video')) && !vals.some(v=>v.includes('image'));
}

function videoMediaFromResponse(json){
  const found=[];
  for(const item of (Array.isArray(json?.media)?json.media:[])){
    if(!isExplicitVideoMedia(item)) continue;
    if(item?.name) found.push({
      mediaId:item.name,
      workflowId:item?.workflowId||null,
      status:item?.mediaMetadata?.mediaStatus?.mediaGenerationStatus||null,
      title:item?.mediaMetadata?.mediaTitle||item?.video?.generatedVideo?.prompt||null,
      source:'media.video_typed'
    });
  }
  // Do not blindly adopt workflow.primaryMediaId anymore. In Flow responses this can
  // point at the input scene IMAGE. Only accept it when workflow metadata explicitly says VIDEO.
  for(const wf of (Array.isArray(json?.workflows)?json.workflows:[])){
    if(wf?.metadata?.primaryMediaId && workflowLooksVideo(wf)){
      found.push({mediaId:wf.metadata.primaryMediaId,workflowId:wf?.name||null,status:null,title:wf?.metadata?.displayName||null,source:'workflow.primaryMediaId.video_typed'});
    }
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
  const stageName=stage==='IMAGE'?'ÃƒÂ¡Ã‚ÂºÃ‚Â¢nh':stage==='VIDEO'?'Video':'TiÃƒÂ¡Ã‚ÂºÃ‚Â¿n trÃƒÆ’Ã‚Â¬nh';
  const jobPercent=stageToJobPercent({imageEnabled,videoEnabled,stage,percent:stagePercent});
  const jobs={...(runtimeCache.jobs||{})};
  const previous=Number(jobs[index]?.percent||0);
  jobs[index]={...(jobs[index]||{}),percent:Math.max(previous,jobPercent),stage,stagePercent:Number(stagePercent||0),workerLabel,tag};
  runtimeCache.jobs=jobs;
  let sum=0;
  for(let i=0;i<total;i++) sum+=Number(jobs[i]?.percent||0);
  const overall=total?sum/total:0;
  const mark=exact?'':'~';
  await setProgress(overall,`${workerLabel} ${tag} ${stageName} ${mark}${Math.round(stagePercent)}%`,detail?`${detail} Ãƒâ€šÃ‚Â· TÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢ng batch ${Math.round(overall)}%`:`TÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢ng batch ${Math.round(overall)}%`);
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
    updateJobProgress({...progressCtx,stage:'IMAGE',stagePercent:Math.min(96,8+ratio*88),detail:'ÃƒÂ¡Ã‚ÂºÃ‚Â¢nh Ãƒâ€žÃ¢â‚¬Ëœang tÃƒÂ¡Ã‚ÂºÃ‚Â¡o song song...',exact:false}).catch(()=>{});
  },1000);
  try{
    const createEvent=await waitExactRequestFinished(tabId,requestMeta.requestId,timeoutMs,`${tag} IMAGE requestId=${requestMeta.requestId}`);
    if(!(createEvent.status>=200&&createEvent.status<300)) throw new Error(`${tag} POST tÃƒÂ¡Ã‚ÂºÃ‚Â¡o ÃƒÂ¡Ã‚ÂºÃ‚Â£nh HTTP ${createEvent.status}. body=${String(createEvent.body||'').slice(0,300)}`);
    const direct=imageMediaFromResponse(createEvent.json);
    const requestBatch=createEvent?.requestInfo?.batchId||requestMeta?.requestInfo?.batchId||null;
    const responseBatches=batchIdsFromResponse(createEvent.json);
    const workflowIds=workflowIdsFromResponse(createEvent.json);
    await appendLog(`${workerLabel} ${tag} IMAGE POST 200 seq=${createEvent.seq} requestId=${createEvent.requestId} batch=${requestBatch||responseBatches[0]||'-'} media=${direct.map(x=>x.mediaId).join(',')||'chÃƒâ€ Ã‚Â°a cÃƒÆ’Ã‚Â³'}`,'info');
    if(direct.length) return {images:direct,raw:createEvent.json,eventMeta:createEvent,source:'image-post'};

    const left=Math.max(1000,timeoutMs-(Date.now()-startedAt));
    await appendLog(`${workerLabel} ${tag} POST ÃƒÂ¡Ã‚ÂºÃ‚Â£nh chÃƒâ€ Ã‚Â°a trÃƒÂ¡Ã‚ÂºÃ‚Â£ mediaId ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ chÃƒÂ¡Ã‚Â»Ã‚Â workflow patch Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng batch/workflow`,'info');
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
      if(event.failed) return {error:new Error(`${tag} flowWorkflow PATCH lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i: ${event.errorText||'unknown'}`)};
      if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} flowWorkflow PATCH HTTP ${event.status}`)};
      return {value:{event,media}};
    },left,`${tag} chÃƒÂ¡Ã‚Â»Ã‚Â correlated flowWorkflow.primaryMediaId`);
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
  return media.filter(isExplicitVideoMedia).some(item=>{
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
  },timeoutMs,`${tag} chÃƒÂ¡Ã‚Â»Ã‚Â video mediaId/mediaTitle bÃƒÂ¡Ã‚ÂºÃ‚Â±ng prompt correlation`);
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
      },Math.min(remain,20000),`${tag} gom Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã‚Â§ ${expected} video output`);
      cursor=Math.max(cursor,Number(wrapped.event?.seq||cursor));
      for(const a of wrapped.assets||[]) if(a?.mediaId) found.set(a.mediaId,a);
      await appendLog(`TAB ${tag} gom output video ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${found.size}/${expected}`,'info');
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
      updateJobProgress({...progressCtx,stage:'IMAGE',stagePercent:Math.min(96,5+ratio*91),detail:'Ãƒâ€žÃ‚Âang chÃƒÂ¡Ã‚Â»Ã‚Â Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng POST tÃƒÂ¡Ã‚ÂºÃ‚Â¡o ÃƒÂ¡Ã‚ÂºÃ‚Â£nh cÃƒÂ¡Ã‚Â»Ã‚Â§a job nÃƒÆ’Ã‚Â y...',exact:false}).catch(()=>{});
    },1000);
  }

  try{
    const createEvent=await waitNet(tabId,event=>{
      if(event.kind!=='IMAGE_CREATE') return false;
      if(!eventAfterMarker(event,marker)) return false;
      if(event.method!=='POST') return false;
      if(!event.requestInfo?.validGeneration) return false;
      if(!eventProjectMatches(event,projectId)) return false;
      if(event.failed) return {error:new Error(`${tag} POST tÃƒÂ¡Ã‚ÂºÃ‚Â¡o ÃƒÂ¡Ã‚ÂºÃ‚Â£nh lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i Network: ${event.errorText||'unknown'}`)};
      if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} POST tÃƒÂ¡Ã‚ÂºÃ‚Â¡o ÃƒÂ¡Ã‚ÂºÃ‚Â£nh HTTP ${event.status}. body=${String(event.body||'').slice(0,300)}`)};
      return {value:event};
    },timeoutMs,`${tag} chÃƒÂ¡Ã‚Â»Ã‚Â Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng POST flowMedia:batchGenerateImages`);

    const direct=imageMediaFromResponse(createEvent.json);
    const requestBatch=createEvent?.requestInfo?.batchId||null;
    const responseBatches=batchIdsFromResponse(createEvent.json);
    const workflowIds=workflowIdsFromResponse(createEvent.json);

    await appendLog(`${workerLabel} ${tag} IMAGE POST 200 seq=${createEvent.seq} batch=${requestBatch||responseBatches[0]||'-'} media=${direct.map(x=>x.mediaId).join(',')||'chÃƒâ€ Ã‚Â°a cÃƒÆ’Ã‚Â³'}`,'info');

    if(direct.length){
      return {images:direct,raw:createEvent.json,eventMeta:createEvent,source:'image-post'};
    }

    // Some Flow/model variants acknowledge the image POST first and write primaryMediaId
    // through flowWorkflows afterwards. This is still Network correlation, not Asset guessing.
    const left=Math.max(1000,timeoutMs-(Date.now()-startedAt));
    await appendLog(`${workerLabel} ${tag} POST ÃƒÂ¡Ã‚ÂºÃ‚Â£nh chÃƒâ€ Ã‚Â°a trÃƒÂ¡Ã‚ÂºÃ‚Â£ mediaId ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ tiÃƒÂ¡Ã‚ÂºÃ‚Â¿p tÃƒÂ¡Ã‚Â»Ã‚Â¥c chÃƒÂ¡Ã‚Â»Ã‚Â flowWorkflow.primaryMediaId cÃƒÆ’Ã‚Â¹ng batch/workflow`,'info');

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
      if(event.failed) return {error:new Error(`${tag} flowWorkflow PATCH lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i: ${event.errorText||'unknown'}`)};
      if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} flowWorkflow PATCH HTTP ${event.status}`)};
      return {value:{event,media}};
    },left,`${tag} chÃƒÂ¡Ã‚Â»Ã‚Â flowWorkflow.primaryMediaId`);

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
    if(event.failed) return {error:new Error(`${tag} POST tÃƒÂ¡Ã‚ÂºÃ‚Â¡o video lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i Network: ${event.errorText||'unknown'}`)};
    if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} Video create HTTP ${event.status}. body=${String(event.body||'').slice(0,300)}`)};
    return {value:{event,videos:videoMediaFromResponse(event.json),raw:event.json}};
  },timeoutMs,`${tag} chÃƒÂ¡Ã‚Â»Ã‚Â Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng POST batchAsyncGenerateVideo`);
}

async function waitVideoMediaIdsFromStatus(tabId,{marker,projectId,timeoutMs,tag}){
  const event=await waitNet(tabId,event=>{
    if(event.kind!=='VIDEO_STATUS') return false;
    if(!eventAfterMarker(event,marker)) return false;
    if(event.method!=='POST') return false;
    if(!eventProjectMatches(event,projectId)) return false;
    const ids=event.requestInfo?.mediaIds||[];
    return ids.length?{value:event}:false;
  },timeoutMs,`${tag} chÃƒÂ¡Ã‚Â»Ã‚Â video mediaId tÃƒÂ¡Ã‚Â»Ã‚Â« status poll`);
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
    const event=await waitNet(tabId,event=>{
      if(event.kind!=='VIDEO_STATUS') return false;
      if(Number(event.seq)<=Number(minSeq)) return false;
      if(event.method!=='POST') return false;
      if(!eventProjectMatches(event,projectId)) return false;
      const reqIds=event.requestInfo?.mediaIds||[];
      if(!reqIds.some(id=>remaining.has(id))) return false;
      if(event.failed) return {error:new Error(`${tag} Video status Network lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i: ${event.errorText||'unknown'}`)};
      if(!(event.status>=200&&event.status<300)) return {error:new Error(`${tag} Video status HTTP ${event.status}`)};
      return {value:event};
    },left,`${tag} chÃƒÂ¡Ã‚Â»Ã‚Â status Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng video mediaId`);

    minSeq=Number(event.seq||minSeq);
    const media=Array.isArray(event.json?.media)?event.json.media:[];
    const p=findNumericProgress(event.json);
    if(p!=null) numericProgress=p;

    for(const item of media){
      const id=item?.name;
      if(!remaining.has(id)) continue;
      const status=statusOfMedia(item);
      statuses.set(id,status);
      await appendLog(`${workerLabel} ${tag} video ${String(id).slice(0,8)}ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${status||'UNKNOWN'}`,isSuccessStatus(status)?'success':'info');
      if(isFailureStatus(status)) throw new Error(`${tag} Video ${id} thÃƒÂ¡Ã‚ÂºÃ‚Â¥t bÃƒÂ¡Ã‚ÂºÃ‚Â¡i: ${status}`);
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
    await updateJobProgress({...progressCtx,stage:'VIDEO',stagePercent,detail:`Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ xong ${successCount}/${mediaIds.length} video${numericProgress!=null?` Ãƒâ€šÃ‚Â· server ${Math.round(numericProgress)}%`:' Ãƒâ€šÃ‚Â· Ãƒâ€žÃ¢â‚¬Ëœang poll status'}`,exact:numericProgress!=null||!remaining.size});
  }

  return {mediaIds,statuses:Object.fromEntries(statuses)};
}


function normalizeScenes(scenes,imageEnabled=true,videoEnabled=true){
  if(!Array.isArray(scenes)) return [];
  return scenes.map((scene,index)=>{
    const imagePrompt=String(scene?.imagePrompt??'').trim();
    const videoPrompt=String(scene?.videoPrompt??'').trim();
    const sceneMeta=scene?.metadata||{};
    const sceneMediaMode=String(sceneMeta?.sceneMediaMode||'').toUpperCase();
    const sceneVideoEnabled=videoEnabled && sceneMeta?.makeVideo!==false && sceneMediaMode!=='IMAGE_ONLY';
    if(imageEnabled&&!imagePrompt) throw new Error(`Scene ${index+1} thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u imagePrompt.`);
    if(sceneVideoEnabled&&!videoPrompt) throw new Error(`Scene ${index+1} thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u videoPrompt.`);
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
    return {imagePrompt,videoPrompt,videoSegments,sceneId,inputImages,metadata:scene?.metadata||null};
  });
}

function parsePairs(raw,imageEnabled=true,videoEnabled=true){
  const lines=String(raw??'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  return lines.map((line,index)=>{
    const pos=line.indexOf('|');
    if(imageEnabled&&videoEnabled){
      if(pos<1||pos>=line.length-1) throw new Error(`DÃƒÆ’Ã‚Â²ng ${index+1} phÃƒÂ¡Ã‚ÂºÃ‚Â£i lÃƒÆ’Ã‚Â  imagePrompt|videoPrompt.`);
      return {imagePrompt:line.slice(0,pos).trim(),videoPrompt:line.slice(pos+1).trim()};
    }
    if(imageEnabled&&!videoEnabled){
      const imagePrompt=pos>=0?line.slice(0,pos).trim():line.trim();
      if(!imagePrompt) throw new Error(`DÃƒÆ’Ã‚Â²ng ${index+1} thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u imagePrompt.`);
      return {imagePrompt,videoPrompt:''};
    }
    if(!imageEnabled&&videoEnabled){
      const videoPrompt=pos>=0?line.slice(pos+1).trim():line.trim();
      if(!videoPrompt) throw new Error(`DÃƒÆ’Ã‚Â²ng ${index+1} thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u videoPrompt.`);
      return {imagePrompt:'',videoPrompt};
    }
    throw new Error('Image model vÃƒÆ’Ã‚Â  Video model Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã‚Âu lÃƒÆ’Ã‚Â  None.');
  });
}


async function startImageJob(tabId,record,options,limiter,total){
  const index=record.index, tag=`[${index+1}/${total}]`, workerLabel='TAB';
  const progressCtx={index,total,imageEnabled:true,videoEnabled:options.videoEnabled,workerLabel,tag};
  await patchJob(index,{imageState:'PREPARING'});
  const imageInputs=Array.isArray(record.pair.inputImages)?record.pair.inputImages:[];
  const imageKeepIds=[...new Set(imageInputs.flatMap(input=>referenceKnownMediaIds(input,options)))];
  const characterSetId=String(record?.pair?.metadata?.characterSetId||'').trim();
  if(characterSetId){
    const characterRefs=imageInputs.filter(x=>['mother_reference','child_reference','father_reference'].includes(String(x?.role||'')));
    await appendLog(`TAB ${tag} IMAGE CHARACTER SET ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${characterSetId} Ãƒâ€šÃ‚Â· refs=${characterRefs.map(x=>`${x.role}:${referenceEffectiveMediaId(x)||referenceSuppliedMediaId(x)||'NO_MEDIA_ID'}:${x.characterId||'-'}`).join(' + ')}`,'info');
  }
  await clearComposerBeforeCreate(tabId,tag,imageKeepIds);
  if(imageInputs.length){
    const refs=await ensureSceneImageInputs(tabId,record,options);
    if(refs.some(x=>x?.uiRecovered)){
      await appendLog(`TAB ${tag} upload Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ F5 Flow ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ re-verify IMAGE Settings trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc Create`, 'info');
      await configureStageSettings(tabId,{type:'IMAGE',aspectRatio:options.aspectRatio,outputs:options.imageOutputs,modelKind:'IMAGE',model:options.imageModel},3);
    }
  }
  await appendLog(`TAB ${tag} IMAGE chuÃƒÂ¡Ã‚ÂºÃ‚Â©n bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ prompt: ${record.pair.imagePrompt}`,'info');
  await callPage(tabId,'replacePrompt',[record.pair.imagePrompt]);
  await callPage(tabId,'waitCreateReady',[15000]);
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
  await appendLog(`PHASE IMAGE: set + verify Settings 1 lÃƒÂ¡Ã‚ÂºÃ‚Â§n, Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“ng thÃƒÂ¡Ã‚Â»Ã‚Âi tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi Ãƒâ€žÃ¢â‚¬Ëœa ${options.imageConcurrency}`,'info');
  const imageSettings=await configureStageSettings(tabId,{
    type:'IMAGE',aspectRatio:options.aspectRatio,outputs:options.imageOutputs,
    modelKind:'IMAGE',model:options.imageModel
  },3);
  await appendLog(`IMAGE SETTINGS VERIFIED (attempt ${imageSettings.attempt}) ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${JSON.stringify(imageSettings.verification.current)}`,'success');

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
        await appendLog(`TAB [${index+1}/${total}] ÃƒÂ¢Ã‚ÂÃ…â€™ IMAGE submit: ${text}`,'error');
        if(isQuotaLikeError(error)) stopSubmitting=true;
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
        record.error='IMAGE success nhÃƒâ€ Ã‚Â°ng mediaId rÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ng';
        record.imageState='ERROR';
        await patchJob(record.index,{imageState:'ERROR',error:record.error,done:!options.videoEnabled});
        await appendLog(`TAB ${tag} ÃƒÂ¢Ã‚ÂÃ…â€™ ${record.error}`,'error');
      }else{
        completionOrder.push(record.index);
        await updateJobProgress({index:record.index,total,imageEnabled:true,videoEnabled:options.videoEnabled,stage:'IMAGE',stagePercent:100,workerLabel:'TAB',tag,detail:`IMAGE SUCCESS ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${record.selectedImage.mediaId}`,exact:true});
        await patchJob(record.index,{imageState:'SUCCESS',imageMediaId:record.selectedImage.mediaId,imageSource:settled.value.source});
        await appendLog(`TAB ${tag} IMAGE SUCCESS ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${record.selectedImage.mediaId} | source=${settled.value.source}`,'success');
      }
    }else{
      const text=settled.error?.message||String(settled.error);
      record.error=text; record.imageState='ERROR';
      await patchJob(record.index,{imageState:'ERROR',error:text,done:!options.videoEnabled});
      await appendLog(`TAB ${tag} ÃƒÂ¢Ã‚ÂÃ…â€™ IMAGE: ${text}`,'error');
      if(isQuotaLikeError(settled.error)) stopSubmitting=true;
    }
  }

  if(stopSubmitting && cursor<queue.length){
    for(;cursor<queue.length;cursor++){
      const index=queue[cursor], text='DÃƒÂ¡Ã‚Â»Ã‚Â«ng submit ÃƒÂ¡Ã‚ÂºÃ‚Â£nh mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi vÃƒÆ’Ã‚Â¬ phÃƒÆ’Ã‚Â¡t hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡n quota/rate limit.';
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
  const pairMeta=record?.pair?.metadata||{};
  const isParentingScene=Boolean(pairMeta?.parenting);
  const allVideoInputs=Array.isArray(record?.pair?.inputImages)?record.pair.inputImages:[];
  // v14.6.0 Parenting contract:
  // IMAGE uses exact Character Set / product refs.
  // VIDEO uses only generated scene-image mediaId because that frame already
  // contains background + mother + child (+ product if present).
  const videoBaseInputs=isParentingScene?[]:allVideoInputs.filter(x=>x?.videoReference!==false);
  const videoKeepIds=[...new Set(videoBaseInputs.flatMap(input=>referenceKnownMediaIds(input,options)))];
  await clearComposerBeforeCreate(tabId,tag,videoKeepIds);

  if(options.imageEnabled){
    if(videoBaseInputs.length){
      const baseRefs=await ensureSceneImageInputs(tabId,record,options,'video');
      if(baseRefs.length){
        await appendLog(`TAB ${tag} VIDEO EXTRA REFS ATTACHED ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${baseRefs.map(x=>`${x.role}:${x.mediaId}`).join(' + ')}`,'success');
      }
    }else if(isParentingScene){
      await appendLog(`TAB ${tag} VIDEO SOURCE MODE ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ SCENE_IMAGE_ONLY Ãƒâ€šÃ‚Â· khÃƒÆ’Ã‚Â´ng attach lÃƒÂ¡Ã‚ÂºÃ‚Â¡i mother/child/product refs`,'info');
    }
    const mediaId=record.selectedImage?.mediaId;
    if(!mediaId) throw new Error(`${tag} KhÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ imageMediaId Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ attach.`);
    const searchCandidates=assetSearchCandidatesForRecord(record);
    await patchJob(index,{assetSearchCandidates:searchCandidates,imageGeneratedTitle:record.selectedImage?.title||null});
    await appendLog(`TAB ${tag} ATTACH exact imageMediaId ${mediaId} | Ãƒâ€ Ã‚Â°u tiÃƒÆ’Ã‚Âªn NO_SEARCH theo mediaId | text fallback=${JSON.stringify(searchCandidates)}`,'info');
    let attached=null;
    try{
      attached=await trustedAttachIngredient(tabId,searchCandidates,mediaId,60000);
    }catch(error){
      const text=error?.message||String(error);
      if(!/khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âºng mediaId|Asset Picker/i.test(text)) throw error;
      await appendLog(`TAB ${tag} asset chÃƒâ€ Ã‚Â°a index/stale ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ F5 project + verify VIDEO + retry exact mediaId`, 'info');
      await reloadAndNormalizeFlow(tabId,`video asset ${mediaId} chÃƒâ€ Ã‚Â°a thÃƒÂ¡Ã‚ÂºÃ‚Â¥y trong picker`,options.projectId);
      await configureStageSettings(tabId,{
        type:'VIDEO',...(options.imageEnabled?{videoMode:'INGREDIENTS'}:{}),
        aspectRatio:options.aspectRatio,duration:options.videoDuration,outputs:options.videoOutputs,
        modelKind:'VIDEO',model:options.videoModel
      },3);
      await sleep(1200);
      attached=await trustedAttachIngredient(tabId,searchCandidates,mediaId,90000);
    }
    if(!attached?.ok||attached.mediaId!==mediaId) throw new Error(`${tag} Ingredient attach verify thÃƒÂ¡Ã‚ÂºÃ‚Â¥t bÃƒÂ¡Ã‚ÂºÃ‚Â¡i.`);
    record.assetSearchPrompt=attached.searchUsed||'';
    await patchJob(index,{assetSearchPrompt:attached.searchUsed||'',assetSearchSource:attached.searchMode||'EXACT_MEDIA_ID',imageAssetTitle:attached.title||null});
    await appendLog(`TAB ${tag} INGREDIENT ATTACHED ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${mediaId} | mode=${attached.searchMode} | Search=${JSON.stringify(attached.searchUsed||'')} | title=${JSON.stringify(attached.title||'')}`,'success');
    if(isParentingScene){
      await appendLog(`TAB ${tag} VIDEO SOURCE FRAME ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ 1 ingredient duy nhÃƒÂ¡Ã‚ÂºÃ‚Â¥t Ãƒâ€šÃ‚Â· sceneImageMediaId=${mediaId}`,'success');
    }else{
      const refCount=videoBaseInputs.length;
      if(refCount) await appendLog(`TAB ${tag} VIDEO INGREDIENT PACK ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${refCount} extra ref + 1 scene frame = ${refCount+1} ingredients`,'success');
    }
  }

  await callPage(tabId,'replacePrompt',[record.pair.videoPrompt]);
  await callPage(tabId,'waitCreateReady',[15000]);
  await limiter.waitTurn(`VIDEO ${tag}`);
  const marker=createNetworkMarker(tabId);
  await appendLog(`TAB ${tag} CLICK CREATE VIDEO | marker=${marker.seq}`,'info');
  await trustedCreateClick(tabId);
  let requestMeta=null;
  let fallbackVideoAssets=[];
  try{
    requestMeta=await waitGenerationRequestStart(tabId,{
      kind:'VIDEO_CREATE',marker,projectId:options.projectId,prompt:record.pair.videoPrompt,
      referenceMediaId:options.imageEnabled?record.selectedImage.mediaId:null,
      timeoutMs:Math.min(options.videoTimeoutMs,45000),label:`VIDEO POST ${tag}`
    });
  }catch(error){
    // Flow UI can accept Create while its create endpoint shape changes and our POST
    // classifier misses it. Do NOT click Create a second time blindly. Correlate the
    // subsequent VIDEO_STATUS by project + exact prompt and adopt those mediaIds.
    await appendLog(`TAB ${tag} ÃƒÂ¢Ã…Â¡Ã‚Â  khÃƒÆ’Ã‚Â´ng bÃƒÂ¡Ã‚ÂºÃ‚Â¯t Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c VIDEO_CREATE POST ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ KHÃƒÆ’Ã¢â‚¬ÂNG click lÃƒÂ¡Ã‚ÂºÃ‚Â¡i; chÃƒÂ¡Ã‚Â»Ã‚Â VIDEO_STATUS/prompt Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ nhÃƒÂ¡Ã‚ÂºÃ‚Â­n clip Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ tÃƒÂ¡Ã‚ÂºÃ‚Â¡o`, 'warning');
    try{
      fallbackVideoAssets=await waitVideoAssetsFallbackConcurrent(tabId,{
        afterSeq:marker.seq,projectId:options.projectId,prompt:record.pair.videoPrompt,
        timeoutMs:Math.min(options.videoTimeoutMs,180000),tag
      });
    }catch(_fallbackError){
      throw error;
    }
    if(!fallbackVideoAssets.length) throw error;
    requestMeta={
      requestId:`status-fallback-${Date.now()}`,seq:marker.seq,
      requestInfo:{referenceMediaIds:options.imageEnabled?[record.selectedImage.mediaId]:[],validGeneration:true},
      statusFallback:true
    };
    await appendLog(`TAB ${tag} ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ STATUS FALLBACK nhÃƒÂ¡Ã‚ÂºÃ‚Â­n ${fallbackVideoAssets.length} video mediaId ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ giÃƒÂ¡Ã‚Â»Ã‚Â¯ clip Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ tÃƒÂ¡Ã‚ÂºÃ‚Â¡o, khÃƒÆ’Ã‚Â´ng submit lÃƒÂ¡Ã‚ÂºÃ‚Â¡i`, 'success');
  }
  await patchJob(index,{videoState:'ACTIVE',videoRequestId:requestMeta.requestId,videoSeq:requestMeta.seq});
  await appendLog(`TAB ${tag} VIDEO REQUEST START seq=${requestMeta.seq} requestId=${requestMeta.requestId} refs=${requestMeta.requestInfo?.referenceMediaIds?.join(',')||'-'}${requestMeta.statusFallback?' Ãƒâ€šÃ‚Â· STATUS_FALLBACK':''}`,'info');

  const lifecycle=(async()=>{
    const created=requestMeta.statusFallback
      ? {event:null,videos:fallbackVideoAssets,raw:{source:'video-status-fallback'}}
      : await waitVideoCreateForRequest(tabId,{requestMeta,timeoutMs:Math.min(options.videoTimeoutMs,180000),tag});
    let videoAssets=(created.videos||[]).filter(x=>x?.mediaId);
    const forbiddenVideoIds=new Set([
      String(record?.selectedImage?.mediaId||''),
      ...(requestMeta?.requestInfo?.referenceMediaIds||[]).map(x=>String(x||''))
    ].filter(Boolean));
    const beforeStrict=videoAssets.length;
    videoAssets=videoAssets.filter(x=>!forbiddenVideoIds.has(String(x?.mediaId||'')));
    if(videoAssets.length!==beforeStrict){
      await appendLog(`TAB ${tag} VIDEO ID STRICT FILTER ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ loÃƒÂ¡Ã‚ÂºÃ‚Â¡i ${beforeStrict-videoAssets.length} mediaId trÃƒÆ’Ã‚Â¹ng input/reference image`, 'warning');
    }
    let videoIds=videoAssets.map(x=>x.mediaId).filter(Boolean);
    const expectedOutputs=outputFactor(options.videoOutputs,4);
    if(videoIds.length<expectedOutputs){
      await appendLog(`TAB ${tag} VIDEO POST mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi cÃƒÆ’Ã‚Â³ ${videoIds.length}/${expectedOutputs} mediaId ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ chÃƒÂ¡Ã‚Â»Ã‚Â status Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ gom Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã‚Â§ output x${expectedOutputs}`,'info');
      try{
        videoAssets=await collectVideoAssetsUntilCount(tabId,{
          afterSeq:requestMeta.seq,projectId:options.projectId,prompt:record.pair.videoPrompt,
          existing:videoAssets,expected:expectedOutputs,timeoutMs:Math.min(options.videoTimeoutMs,90000),tag
        });
        videoAssets=videoAssets.filter(x=>!forbiddenVideoIds.has(String(x?.mediaId||'')));
        videoIds=videoAssets.map(x=>x.mediaId).filter(Boolean);
      }catch(error){
        await appendLog(`TAB ${tag} ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â gom output x${expectedOutputs}: ${error?.message||error}`,'error');
      }
    }
    if(!videoIds.length) throw new Error(`${tag} KhÃƒÆ’Ã‚Â´ng xÃƒÆ’Ã‚Â¡c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c VIDEO mediaId cÃƒÆ’Ã‚Â³ type hÃƒÂ¡Ã‚Â»Ã‚Â£p lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡; Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ tÃƒÂ¡Ã‚Â»Ã‚Â« chÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi image/reference mediaId Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ trÃƒÆ’Ã‚Â¡nh tÃƒÂ¡Ã‚ÂºÃ‚Â¡o lÃƒÂ¡Ã‚ÂºÃ‚Â¡i vÃƒÆ’Ã‚Â´ hÃƒÂ¡Ã‚ÂºÃ‚Â¡n.`);
    if(videoIds.length<expectedOutputs) throw new Error(`${tag} Flow yÃƒÆ’Ã‚Âªu cÃƒÂ¡Ã‚ÂºÃ‚Â§u x${expectedOutputs} nhÃƒâ€ Ã‚Â°ng chÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° xÃƒÆ’Ã‚Â¡c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c ${videoIds.length} video output.`);
    record.videoIds=videoIds;
    record.videoAssets=videoAssets;
    record.videoChainMediaIds=videoIds.length?[videoIds[0]]:[];
    await patchJob(index,{videoState:'ACTIVE',videoMediaIds:videoIds,videoAssets:videoAssets.map(x=>({mediaId:x.mediaId,title:x.title||null})),videoChainMediaIds:record.videoChainMediaIds});
    // Persist mediaIds to the server immediately. If status polling/WS later fails,
    // the server can download these exact clips instead of generating them again.
    sendSceneCheckpoint(record,'VIDEO_MEDIA_IDS_READY');
    for(const [i,asset] of videoAssets.entries()) await appendLog(`TAB ${tag} VIDEO ASSET ${i+1} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ mediaId=${asset.mediaId} | title=${JSON.stringify(asset.title||'')}`,'success');
    await updateJobProgress({...progressCtx,stage:'VIDEO',stagePercent:25,detail:`Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ nhÃƒÂ¡Ã‚ÂºÃ‚Â­n ${videoIds.length} video mediaId`,exact:false});
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
    queue=records.filter(r=>r.imageState==='SUCCESS'&&!r.error).map(r=>r.index)
      .sort((a,b)=>(rank.get(a)??999999)-(rank.get(b)??999999));
  }else queue=records.map(r=>r.index);
  if(!queue.length) return;

  await appendLog(`PHASE VIDEO: set + verify Settings 1 lÃƒÂ¡Ã‚ÂºÃ‚Â§n, Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“ng thÃƒÂ¡Ã‚Â»Ã‚Âi tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi Ãƒâ€žÃ¢â‚¬Ëœa ${options.videoConcurrency}`,'info');
  const videoSettings=await configureStageSettings(tabId,{
    type:'VIDEO',...(options.imageEnabled?{videoMode:'INGREDIENTS'}:{}),
    aspectRatio:options.aspectRatio,duration:options.videoDuration,outputs:options.videoOutputs,
    modelKind:'VIDEO',model:options.videoModel
  },3);
  await appendLog(`VIDEO SETTINGS VERIFIED (attempt ${videoSettings.attempt}) ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${JSON.stringify(videoSettings.verification.current)}`,'success');

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
        await appendLog(`TAB [${index+1}/${total}] ÃƒÂ¢Ã‚ÂÃ…â€™ VIDEO submit: ${text}`,'error');
        if(isQuotaLikeError(error)) stopSubmitting=true;
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
      await appendLog(`TAB ${tag} ÃƒÂ¢Ã‚ÂÃ…â€™ VIDEO: ${text}`,'error');
      if(isQuotaLikeError(settled.error)) stopSubmitting=true;
    }
  }
  if(stopSubmitting && cursor<queue.length){
    for(;cursor<queue.length;cursor++){
      const index=queue[cursor],text='DÃƒÂ¡Ã‚Â»Ã‚Â«ng submit video mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi vÃƒÆ’Ã‚Â¬ phÃƒÆ’Ã‚Â¡t hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡n quota/rate limit.';
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

async function waitBrowserDownload(downloadId,timeoutMs=180000){
  const started=Date.now();let last=null;
  while(Date.now()-started<timeoutMs){
    const rows=await chrome.downloads.search({id:downloadId});last=rows?.[0]||null;
    if(last?.state==='complete'&&last.filename) return last;
    if(last?.state==='interrupted') throw new Error(`Download bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ giÃƒÆ’Ã‚Â¡n Ãƒâ€žÃ¢â‚¬ËœoÃƒÂ¡Ã‚ÂºÃ‚Â¡n: ${last.error||'unknown'}`);
    await sleep(500);
  }
  throw new Error(`Timeout chÃƒÂ¡Ã‚Â»Ã‚Â browser download ${downloadId}. state=${last?.state||'unknown'}`);
}

function outputFactor(value,maximum=4){
  return Math.max(1,Math.min(maximum,Number(String(value||'x1').replace(/^x/i,''))||1));
}

async function downloadImageMediaIdsForServer({jobId,sceneId,mediaIds}){
  const ids=[...new Set(Array.isArray(mediaIds)?mediaIds:[])].filter(Boolean);
  if(!jobId||!sceneId||!ids.length) throw new Error('DOWNLOAD_IMAGE_MEDIA_FILES thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u jobId/sceneId/mediaIds');
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
        const downloadId=await chrome.downloads.download({url,filename,saveAs:false,conflictAction:'uniquify'});
        item=await waitBrowserDownload(downloadId,90000);lastError=null;break;
      }catch(error){lastError=error;await appendLog(`RECOVER IMAGE scene=${sceneId} media=${String(mediaId).slice(0,8)} attempt ${attempt}/3 lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i: ${error?.message||error}`,'error');if(attempt<3) await sleep(800*attempt);}
    }
    if(lastError||!item) throw lastError||new Error(`Image recovery khÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ file cho ${mediaId}`);
    const info={mediaId,mediaIndex:i,localPath:item.filename,state:item.state};results.push(info);
    await appendLog(`RECOVER IMAGE scene=${sceneId} [${i+1}/${ids.length}] ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${item.filename}`,'success');
    sendServerMessage({type:'IMAGE_FILE_READY',jobId,sceneId,mediaId,mediaIndex:i,localPath:item.filename,browserFilename:filename,recovery:true});
  }
  return results;
}

const VIDEO_SIGNED_URL_CACHE=new Map();

function signedVideoUrlLooksUsable(url=''){
  return isVideoCdnUrl(url);
}

async function resolveVideoSignedUrl(mediaId,{force=false}={}){
  mediaId=String(mediaId||'').trim();
  if(!mediaId) throw new Error('mediaId video rÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ng');
  const cached=VIDEO_SIGNED_URL_CACHE.get(mediaId);
  if(!force && cached?.url && Date.now()-Number(cached.at||0)<15*60*1000) return {...cached,source:'extension_cache'};
  const captured=capturedSignedVideoUrl(mediaId);
  if(!force && captured?.url && Date.now()-Number(captured.at||0)<15*60*1000){
    VIDEO_SIGNED_URL_CACHE.set(mediaId,captured);
    return {...captured,source:'cdp_redirect_capture'};
  }

  const redirectUrl=`https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${encodeURIComponent(mediaId)}`;
  let lastError=null;
  const attempts=[
    {method:'HEAD',headers:{}},
    {method:'GET',headers:{Range:'bytes=0-0'}}
  ];
  for(const spec of attempts){
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(new Error('resolve signed URL timeout')),12000);
    try{
      const response=await fetch(redirectUrl,{
        method:spec.method,headers:spec.headers,credentials:'include',redirect:'follow',cache:'no-store',signal:controller.signal
      });
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

  // v14.6.5 fallback: do not repeat the same failing fetch forever. Ask the Flow tab
  // itself to load the exact redirect in a hidden video element and let CDP capture the
  // final flow-content.google URL. This path does not call chrome.downloads.
  try{
    const pageHit=await resolveSignedUrlViaFlowTab(mediaId);
    if(pageHit?.url){
      VIDEO_SIGNED_URL_CACHE.set(mediaId,pageHit);
      return pageHit;
    }
  }catch(error){
    lastError=error;
  }

  const capturedAfter=capturedSignedVideoUrl(mediaId);
  if(capturedAfter?.url){
    VIDEO_SIGNED_URL_CACHE.set(mediaId,capturedAfter);
    return {...capturedAfter,source:'cdp_redirect_capture_after_probe'};
  }
  throw new Error(`KhÃƒÆ’Ã‚Â´ng resolve Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c signed URL cho mediaId=${mediaId}: ${lastError?.message||lastError||'unknown'}`);
}


async function downloadMediaIdsForServer({jobId,sceneId,mediaIds,downloadMode='server_signed_url',refreshSignedUrl=false}){
  const ids=[...new Set(Array.isArray(mediaIds)?mediaIds:[])].map(x=>String(x||'').trim()).filter(Boolean);
  if(!jobId||!sceneId||!ids.length) throw new Error('DOWNLOAD_MEDIA_FILES thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u jobId/sceneId/mediaIds');
  const results=[];
  for(let i=0;i<ids.length;i++){
    const mediaId=ids[i];
    let lastError=null;
    for(let attempt=1;attempt<=2;attempt++){
      try{
        const resolved=await resolveVideoSignedUrl(mediaId,{force:refreshSignedUrl||attempt>1});
        const payload={
          type:'VIDEO_DOWNLOAD_URL',jobId,sceneId,mediaId,mediaIndex:i,
          signedUrl:resolved.url,resolvedAt:new Date().toISOString(),source:resolved.source||'extension_resolver',
          resolverMethod:resolved.method||null,resolverStatus:resolved.status||null,downloadMode:'server_signed_url'
        };
        if(!sendServerMessage(payload)) throw new Error('Server bridge offline trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc khi gÃƒÂ¡Ã‚Â»Ã‚Â­i signed URL');
        results.push({mediaId,mediaIndex:i,signedUrl:resolved.url,resolvedAt:payload.resolvedAt});
        await appendLog(`RECOVER VIDEO scene=${sceneId} [${i+1}/${ids.length}] mediaId=${mediaId.slice(0,8)}ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ signed URL gÃƒÂ¡Ã‚Â»Ã‚Â­i SERVER, khÃƒÆ’Ã‚Â´ng browser download`,'success');
        lastError=null;break;
      }catch(error){
        lastError=error;
        VIDEO_SIGNED_URL_CACHE.delete(mediaId);
        await appendLog(`RESOLVE VIDEO scene=${sceneId} media=${mediaId.slice(0,8)} attempt ${attempt}/2 lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i: ${error?.message||error}`,'error');
        if(attempt<2) await sleep(1200);
      }
    }
    if(lastError){
      sendServerMessage({type:'VIDEO_DOWNLOAD_URL_ERROR',jobId,sceneId,mediaId,mediaIndex:i,error:lastError?.message||String(lastError)});
      throw lastError;
    }
  }
  sendServerMessage({type:'VIDEO_DOWNLOAD_SUMMARY',jobId,sceneId,expected:ids.length,urls:results.map(x=>({mediaId:x.mediaId,mediaIndex:x.mediaIndex,resolvedAt:x.resolvedAt}))});
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
    if(serverJobId){
      // v14.6.5: NEVER invoke chrome.downloads for server-controlled jobs.
      // Browser download UI can wait on Open/Save and deadlock rolling queues.
      for(let attempt=1;attempt<=4;attempt++){
        try{
          const resolved=await resolveVideoSignedUrl(mediaId,{force:attempt>1});
          const resolvedAt=new Date().toISOString();
          if(!sendServerMessage({
            type:'VIDEO_DOWNLOAD_URL',jobId:serverJobId,sceneId:record.sceneId??record.index+1,
            sceneIndex:record.serverSceneIndex??record.index,mediaId,mediaIndex:i,signedUrl:resolved.url,
            resolvedAt,source:'auto_download_server',downloadMode:'server_signed_url'
          })) throw new Error('Server bridge offline trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc khi gÃƒÂ¡Ã‚Â»Ã‚Â­i signed URL');
          downloads.push({mediaId,signedUrl:resolved.url,state:'server_downloading'});
          await appendLog(`TAB [${record.index+1}] AUTO DOWNLOAD ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ SERVER signed URL Ãƒâ€šÃ‚Â· media=${mediaId.slice(0,8)}ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦`,'success');
          lastError=null;break;
        }catch(error){lastError=error;VIDEO_SIGNED_URL_CACHE.delete(mediaId);if(attempt<4) await sleep(1200*attempt);}
      }
    }else{
      for(let attempt=1;attempt<=3;attempt++){
        try{
          const downloadId=await chrome.downloads.download({url,filename,saveAs:false,conflictAction:'uniquify'});
          const item=await waitBrowserDownload(downloadId,180000);
          const info={mediaId,downloadId,filename,localPath:item.filename,state:item.state};
          downloads.push(info);
          await appendLog(`TAB [${record.index+1}] AUTO DOWNLOAD ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${item.filename}`,'success');
          lastError=null;break;
        }catch(error){lastError=error;if(attempt<3) await sleep(1200*attempt);}
      }
    }
    if(lastError){
      await appendLog(`TAB [${record.index+1}] ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Auto download lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i media ${String(mediaId).slice(0,8)}ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦: ${lastError?.message||lastError}`,'error');
      if(serverJobId) sendServerMessage({type:'VIDEO_DOWNLOAD_URL_ERROR',jobId:serverJobId,sceneId:record.sceneId??record.index+1,mediaId,error:lastError?.message||String(lastError)});
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
  const current=await chrome.tabs.get(tabId).catch(()=>null);
  const target=flowProjectUrl(projectId,current?.url||'');
  if(!isFlowProjectRootUrl(current?.url||'',projectId)){
    await appendLog(`PROJECT VIEW RECOVERY ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ thoÃƒÆ’Ã‚Â¡t ${String(current?.url||'').includes('/edit/')?'media detail':'route con'} trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc khi tÃƒÆ’Ã‚Â¬m All Media`,'info');
    await chrome.tabs.update(tabId,{url:target});
    await waitTabState(tabId,t=>t.status==='complete'&&projectIdFromFlowUrl(t.url||'')===projectId&&isFlowProjectRootUrl(t.url||'',projectId),30000,'mÃƒÂ¡Ã‚Â»Ã…Â¸ Project root Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ tÃƒÆ’Ã‚Â¬m video');
  }else if(current?.status!=='complete'){
    await waitTabState(tabId,t=>t.status==='complete',30000,'Project tÃƒÂ¡Ã‚ÂºÃ‚Â£i xong');
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
      await appendLog(`TAB ${tag} EXTEND exact tile ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ mediaId=${mediaId} | title=${JSON.stringify(exact.title||title||'')} | tile=${exact.tileId||'-'}`,'success');
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
      await appendLog(`TAB ${tag} EXTEND fallback unique mediaTitle ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${JSON.stringify(title)} | tile=${byTitle.match.tileId||'-'}`,'info');
      return byTitle.match;
    }
  }
  throw new Error(`${tag} KhÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y exact video tile mediaId=${mediaId}. mediaTitle=${JSON.stringify(title||'')} visible=${JSON.stringify((lastTiles||[]).slice(0,8))}`);
}

async function openVideoEditForExtend(tabId,tile,projectId,tag=''){
  if(!tile?.href) throw new Error(`${tag} Video tile khÃƒÆ’Ã‚Â´ng cÃƒÆ’Ã‚Â³ href edit.`);
  const url=new URL(tile.href,'https://labs.google').href;
  await appendLog(`TAB ${tag} EXTEND mÃƒÂ¡Ã‚Â»Ã…Â¸ video ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${url}`,'info');
  await chrome.tabs.update(tabId,{url});
  await waitTabState(tabId,t=>t.status==='complete'&&String(t.url||'').includes('/edit/'),30000,'mÃƒÂ¡Ã‚Â»Ã…Â¸ video editor');
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
  if(!addPoint) throw new Error(`${tag} KhÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â¬m thÃƒÂ¡Ã‚ÂºÃ‚Â¥y Add Clip sau khi mÃƒÂ¡Ã‚Â»Ã…Â¸ video.`);
  await trustedClickPoint(tabId,addPoint);
  await sleep(250);
  let extendPoint=null;
  for(let attempt=1;attempt<=8;attempt++){
    try{extendPoint=await callPage(tabId,'getExtendMenuPoint',[]);break;}catch{await sleep(250);}
  }
  if(!extendPoint) throw new Error(`${tag} Add Clip Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ mÃƒÂ¡Ã‚Â»Ã…Â¸ nhÃƒâ€ Ã‚Â°ng khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚ÂºÃ‚Â¥y Extend.`);
  await trustedClickPoint(tabId,extendPoint);
  if(!await waitPageCondition(tabId,'isExtendComposerOpen',true,8000)) throw new Error(`${tag} Click Extend nhÃƒâ€ Ã‚Â°ng ÃƒÆ’Ã‚Â´ What happens next? khÃƒÆ’Ã‚Â´ng mÃƒÂ¡Ã‚Â»Ã…Â¸.`);
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
  if(!ids.length) throw new Error(`${tag} Extend khÃƒÆ’Ã‚Â´ng trÃƒÂ¡Ã‚ÂºÃ‚Â£ video mediaId.`);
  await waitVideosSuccessful(tabId,{mediaIds:ids,marker:{seq:requestMeta.seq,at:Date.now()},projectId:options.projectId,timeoutMs:options.videoTimeoutMs,tag,workerLabel:'TAB',progressCtx:null});
  const chosen=assets[0]||{mediaId:ids[0],title:null};
  await appendLog(`TAB ${tag} EXTEND SUCCESS ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ mediaId=${chosen.mediaId} | title=${JSON.stringify(chosen.title||'')}`,'success');
  return chosen;
}

async function runVideoExtendPhase(tabId,records,options,limiter){
  if(!options.videoEnabled || options.videoExtendFactor<=1) return;
  const rounds=options.videoExtendFactor-1;
  const candidates=records.filter(r=>r.videoState==='SUCCESS'&&(r.videoIds||[]).length);
  if(!candidates.length) return;
  await appendLog(`PHASE VIDEO EXTEND: x${options.videoExtendFactor} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ thÃƒÆ’Ã‚Âªm ${rounds} clip/scene Ãƒâ€šÃ‚Â· chÃƒÂ¡Ã‚ÂºÃ‚Â¡y tuÃƒÂ¡Ã‚ÂºÃ‚Â§n tÃƒÂ¡Ã‚Â»Ã‚Â± Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ khÃƒÆ’Ã‚Â´ng xung Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢t Flow editor`,'info');
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
        await appendLog(`TAB ${tag} EXTEND PLAN ${round}/${rounds} role=${planned?.role||'-'} ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${JSON.stringify(planned?.prompt||formatVideoExtendPrompt(options.videoExtendPrompt,record,round))}`,'info');
        const asset=await createOneVideoExtension(tabId,record,options,limiter,round,rounds);
        record.extendedVideoAssets.push(asset);
        record.videoChainMediaIds.push(asset.mediaId);
        if(!record.videoIds.includes(asset.mediaId)) record.videoIds.push(asset.mediaId);
        record.videoAssets=[...(record.videoAssets||[]),asset];
        const segmentPct=((round+1)/Math.max(1,options.videoExtendFactor))*100;
        await updateJobProgress({index:record.index,total:records.length,imageEnabled:options.imageEnabled,videoEnabled:true,stage:'VIDEO',stagePercent:segmentPct,workerLabel:'TAB',tag,detail:`EXTEND ${round}/${rounds} SUCCESS Ãƒâ€šÃ‚Â· role=${planned?.role||'-'}`,exact:true});
        await patchJob(record.index,{videoState:'EXTENDING',videoMediaIds:record.videoIds,videoChainMediaIds:record.videoChainMediaIds,videoExtendState:`${round}/${rounds}`,done:false});
      }
      await patchJob(record.index,{videoState:'SUCCESS',videoExtendState:'SUCCESS',videoChainMediaIds:record.videoChainMediaIds,videoMediaIds:record.videoIds,done:true});
      const chainServerJobId=String(record?.serverJobId||options.serverJobId||'').trim();
      if(chainServerJobId){
        sendServerMessage({type:'VIDEO_CHAIN_INFO',jobId:chainServerJobId,sceneId:record.sceneId??record.index+1,sceneIndex:record.serverSceneIndex??record.index,extendFactor:options.videoExtendFactor,mediaIds:record.videoChainMediaIds,titles:(record.videoAssets||[]).filter(a=>record.videoChainMediaIds.includes(a.mediaId)).map(a=>a.title||null)});
      }
      await appendLog(`TAB ${tag} VIDEO EXTEND CHAIN ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${record.videoChainMediaIds.join(' ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ')}`,'success');
      await navigateProjectAllMedia(tabId,options.projectId);
    }catch(error){
      const text=error?.message||String(error);
      record.error=record.error||`VIDEO EXTEND: ${text}`;
      record.videoState='ERROR';
      await patchJob(record.index,{videoState:'ERROR',videoExtendState:'ERROR',videoExtendError:text,error:record.error,done:true});
      await appendLog(`TAB ${tag} ÃƒÂ¢Ã‚ÂÃ…â€™ VIDEO EXTEND: ${text}`,'error');
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

function appendServerMessagesToRecords(messages,records,options){
  const added=[];
  for(const msg of (Array.isArray(messages)?messages:[])){
    const jobId=String(msg?.jobId||'').trim();
    if(!jobId) continue;
    const pairs=normalizeScenes(Array.isArray(msg?.scenes)?msg.scenes:[],options.imageEnabled,options.videoEnabled);
    for(let localIndex=0;localIndex<pairs.length;localIndex++){
      const pair=pairs[localIndex];
      const index=records.length;
      const resumeImageMediaId=String(pair?.metadata?.resumeImageMediaId||'').trim();
      const resumeSkipImage=Boolean(pair?.metadata?.resumeSkipImage && resumeImageMediaId);
      const record={
        index,
        serverJobId:jobId,
        serverSceneIndex:localIndex,
        serverKind:String(msg?.kind||''),
        serverDispatchEpoch:Number(msg?.dispatchEpoch||0),
        sceneId:pair.sceneId??localIndex+1,
        pair,
        imageState:options.imageEnabled?(resumeSkipImage?'SUCCESS':'WAIT'):'SKIP',
        videoState:(options.videoEnabled && pair?.metadata?.makeVideo!==false && String(pair?.metadata?.sceneMediaMode||'').toUpperCase()!=='IMAGE_ONLY')?(resumeSkipImage?'READY':'WAIT'):'SKIP',
        selectedImage:resumeSkipImage?{mediaId:resumeImageMediaId,title:'server checkpoint image',source:'server_checkpoint'}:null,
        imageResult:resumeSkipImage?{images:[{mediaId:resumeImageMediaId}],source:'server_checkpoint'}:null,
        resumeSkipImage,
        error:null,
        videoIds:[],
        imageRetryCount:0,
        videoRetryCount:0
      };
      records.push(record);added.push(record);
    }
  }
  return added;
}

async function runQueueScheduler(tabId,records,options,limiter,dynamicBatch=null){
  const assertAlive=()=>{if(dynamicBatch?.aborted) throw new Error(dynamicBatch.abortReason||'Server Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng');assertServerControlAlive();};
  assertAlive();
  const totalNow=()=>records.length;
  const wake=createWakeSignal();
  if(dynamicBatch) dynamicBatch.wake=wake;
  const imagePending=records.filter(r=>options.imageEnabled && r.imageState==='WAIT').map(r=>r.index);
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
  let systemicFailureStreak=0;
  const systemicFailureLimit=Math.max(2,Math.min(5,Number(options.systemicFailureLimit||3)));

  if(options.videoEnabled){
    for(const record of records){
      if((!options.imageEnabled || record.resumeSkipImage) && record.videoState==='READY'){
        videoReadyQueue.push(record.index);
      }
    }
  }

  const appendDynamicMessages=async()=>{
    if(!dynamicBatch?.take) return 0;
    const messages=dynamicBatch.take()||[];
    if(!messages.length) return 0;
    const added=appendServerMessagesToRecords(messages,records,options);
    for(const record of added){
      runtimeCache.jobs[record.index]={
        imageState:record.imageState,videoState:record.videoState,
        percent:record.resumeSkipImage?(options.videoEnabled?50:100):0,done:false
      };
      if(options.imageEnabled && record.imageState==='WAIT') imagePending.push(record.index);
      else if(options.videoEnabled && record.videoState==='READY'){
        videoReadyQueue.push(record.index);runtimeCache.jobs[record.index].videoState='READY';
        await appendLog(`SERVER CHECKPOINT ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ scene ${record.sceneId} bÃƒÂ¡Ã‚Â»Ã‚Â tÃƒÂ¡Ã‚ÂºÃ‚Â¡o lÃƒÂ¡Ã‚ÂºÃ‚Â¡i IMAGE, dÃƒÆ’Ã‚Â¹ng mediaId=${record.selectedImage?.mediaId||'-'} Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ retry VIDEO`,'success');
      }
    }
    await persistRuntime();
    await appendLog(`SERVER QUEUE APPEND Ãƒâ€šÃ‚Â· +${messages.length} job / +${added.length} scene Ãƒâ€šÃ‚Â· total=${totalNow()} Ãƒâ€šÃ‚Â· IMAGE cap=${options.imageConcurrency} Ãƒâ€šÃ‚Â· VIDEO cap=${options.videoConcurrency}`,'info');
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
    stopReason=error?.message||String(error||'Ãƒâ€žÃ‚ÂÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng submit mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi.');
    await appendLog(`ÃƒÂ¢Ã¢â‚¬ÂºÃ¢â‚¬Â DÃƒÂ¡Ã‚Â»Ã‚Â«ng SUBMIT mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi: ${stopReason}`,'error');
    // Anything already in-flight continues to be monitored; only queued/pending submits are cancelled.
    for(const task of [...imageSubmitQueue.splice(0),...videoSubmitQueue.splice(0)]){
      const record=records[task.index];
      const text=`KhÃƒÆ’Ã‚Â´ng submit vÃƒÆ’Ã‚Â¬ scheduler Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng: ${stopReason}`;
      record.error=record.error||text;
      if(task.type==='IMAGE') record.imageState='NOT_SUBMITTED'; else record.videoState='NOT_SUBMITTED';
      await patchJob(record.index,{[task.type==='IMAGE'?'imageState':'videoState']:'NOT_SUBMITTED',error:record.error,done:true});
    }
    for(;imageCursor<imagePending.length;imageCursor++){
      const record=records[imagePending[imageCursor]];
      if(record.imageState!=='WAIT') continue;
      const text=`KhÃƒÆ’Ã‚Â´ng submit ÃƒÂ¡Ã‚ÂºÃ‚Â£nh vÃƒÆ’Ã‚Â¬ scheduler Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng: ${stopReason}`;
      record.error=record.error||text;record.imageState='NOT_SUBMITTED';
      await patchJob(record.index,{imageState:'NOT_SUBMITTED',error:record.error,done:true});
    }
    await refreshMetrics();wake.notify();
  };

  const ensureMode=async(type)=>{
    if(currentMode===type) return;
    if(type==='IMAGE'){
      await appendLog('SUBMIT DISPATCHER ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ chuyÃƒÂ¡Ã‚Â»Ã†â€™n IMAGE + verify Settings','info');
      const settings=await configureStageSettings(tabId,{type:'IMAGE',aspectRatio:options.aspectRatio,outputs:options.imageOutputs,modelKind:'IMAGE',model:options.imageModel},3);
      await appendLog(`IMAGE SETTINGS VERIFIED (attempt ${settings.attempt}) ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${JSON.stringify(settings.verification.current)}`,'success');
    }else{
      await appendLog('SUBMIT DISPATCHER ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ chuyÃƒÂ¡Ã‚Â»Ã†â€™n VIDEO + INGREDIENTS + verify Settings','info');
      const settings=await configureStageSettings(tabId,{type:'VIDEO',...(options.imageEnabled?{videoMode:'INGREDIENTS'}:{}),aspectRatio:options.aspectRatio,duration:options.videoDuration,outputs:options.videoOutputs,modelKind:'VIDEO',model:options.videoModel},3);
      await appendLog(`VIDEO SETTINGS VERIFIED (attempt ${settings.attempt}) ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${JSON.stringify(settings.verification.current)}`,'success');
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
        if(!record.selectedImage?.mediaId) throw new Error('IMAGE success nhÃƒâ€ Ã‚Â°ng mediaId rÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ng');
        record.imageState='SUCCESS';record.imageCompletedAt=Date.now();
        await updateJobProgress({index,total:totalNow(),imageEnabled:true,videoEnabled:options.videoEnabled,stage:'IMAGE',stagePercent:100,workerLabel:'TAB',tag,detail:`IMAGE SUCCESS ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${record.selectedImage.mediaId}`,exact:true});
        await patchJob(index,{imageState:'SUCCESS',imageMediaId:record.selectedImage.mediaId,imageSource:value.source});
        await appendLog(`TAB ${tag} IMAGE SUCCESS ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ${record.selectedImage.mediaId} | source=${value.source}`,'success');
        sendSceneCheckpoint(record,'IMAGE_READY');
        if(options.videoEnabled){
          if(stopSubmitting){
            const text=`ÃƒÂ¡Ã‚ÂºÃ‚Â¢nh xong nhÃƒâ€ Ã‚Â°ng khÃƒÆ’Ã‚Â´ng submit video vÃƒÆ’Ã‚Â¬ scheduler Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng: ${stopReason}`;
            record.error=record.error||text;record.videoState='NOT_SUBMITTED';
            await patchJob(index,{videoState:'NOT_SUBMITTED',error:record.error,done:true});
          }else{
            record.videoState='READY';videoReadyQueue.push(index);
            await patchJob(index,{videoState:'READY'});
          }
        }else await patchJob(index,{done:true});
      }catch(error){
        const text=error?.message||String(error);
        const canRetry=isTransientFlowUiError(error) && Number(record.imageRetryCount||0)<2 && !stopSubmitting;
        if(canRetry){
          record.imageRetryCount=Number(record.imageRetryCount||0)+1;
          record.error=null;record.imageState='WAIT';
          await patchJob(index,{imageState:'RETRY_WAIT',error:text,done:false,imageRetryCount:record.imageRetryCount});
          await appendLog(`TAB ${tag} ÃƒÂ¢Ã¢â€žÂ¢Ã‚Â» IMAGE retry ${record.imageRetryCount}/2 Ãƒâ€šÃ‚Â· giÃƒÂ¡Ã‚Â»Ã‚Â¯ checkpoint cÃƒâ€¦Ã‚Â© Ãƒâ€šÃ‚Â· ${text}`,'warning');
          await sleep(700*record.imageRetryCount);
          imagePending.push(index);
        }else{
          record.error=text;record.imageState='ERROR';
          await patchJob(index,{imageState:'ERROR',error:text,done:true});
          sendSceneCheckpoint(record,'IMAGE_FAILED',{retryable:isTransientFlowUiError(error)});
          await appendLog(`TAB ${tag} ÃƒÂ¢Ã‚ÂÃ…â€™ IMAGE: ${text}`,'error');
          if(isQuotaLikeError(error)) await setStopped(error);
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
        await updateJobProgress({index,total:totalNow(),imageEnabled:options.imageEnabled,videoEnabled:true,stage:'VIDEO',stagePercent:basePct,workerLabel:'TAB',tag,detail:options.videoExtendFactor>1?`VIDEO BASE SUCCESS Ãƒâ€šÃ‚Â· 1/${options.videoExtendFactor}`:'VIDEO SUCCESS',exact:true});
        await patchJob(index,{videoState:options.videoExtendFactor>1?'BASE_SUCCESS':'SUCCESS',videoMediaIds:record.videoIds,videoAssets:(record.videoAssets||[]).map(x=>({mediaId:x.mediaId,title:x.title||null})),videoChainMediaIds:record.videoChainMediaIds,done:options.videoExtendFactor<=1});
        await appendLog(`TAB ${tag} ${options.videoExtendFactor>1?`VIDEO BASE SUCCESS Ãƒâ€šÃ‚Â· chÃƒÂ¡Ã‚Â»Ã‚Â ${options.videoExtendFactor-1} Extend`:'VIDEO SUCCESS'}`,'success');
        sendSceneCheckpoint(record,'VIDEO_READY');
        if(options.autoDownloadVideo && options.videoExtendFactor<=1){
          const task=autoDownloadVideos(record,options).then(async downloads=>{
            record.downloads=downloads;record.downloadState='DONE';record.downloadError=null;
            const expected=outputFactor(options.videoOutputs,4);
            if(downloads.length<expected){
              record.downloadState='ERROR';record.downloadError=`TÃƒÂ¡Ã‚ÂºÃ‚Â£i local thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u ${downloads.length}/${expected} video output`;
              await patchJob(index,{downloads,downloadState:'ERROR',downloadError:record.downloadError});
            }else await patchJob(index,{downloads,downloadState:'DONE'});
            return downloads;
          }).catch(async error=>{record.downloadState='ERROR';record.downloadError=error?.message||String(error);await patchJob(index,{downloadState:'ERROR',downloadError:record.downloadError});return [];});
          if(record?.serverJobId||options.serverJobId){options.downloadTasks.push(task);await patchJob(index,{downloadState:'DOWNLOADING'});}else await task;
        }
      }catch(error){
        const text=error?.message||String(error);
        // If Flow already returned video mediaIds, never click Create again: report the
        // checkpoint so the server can download/reconcile those clips.
        if((record.videoIds||[]).length){
          record.error=text;record.videoState='CHECKPOINTED';
          await patchJob(index,{videoState:'CHECKPOINTED',videoMediaIds:record.videoIds,error:text,done:true});
          sendSceneCheckpoint(record,'VIDEO_MEDIA_IDS_READY',{retryable:true});
          await appendLog(`TAB ${tag} ÃƒÂ¢Ã…Â¡Ã‚Â  VIDEO cÃƒÆ’Ã‚Â³ mediaId nhÃƒâ€ Ã‚Â°ng monitor lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ khÃƒÆ’Ã‚Â´ng generate lÃƒÂ¡Ã‚ÂºÃ‚Â¡i; server sÃƒÂ¡Ã‚ÂºÃ‚Â½ recover download. ${text}`,'warning');
        }else{
          const canRetry=isTransientFlowUiError(error) && Number(record.videoRetryCount||0)<2 && !stopSubmitting;
          if(canRetry){
            record.videoRetryCount=Number(record.videoRetryCount||0)+1;
            record.error=null;record.videoState='READY';
            await patchJob(index,{videoState:'RETRY_WAIT',error:text,done:false,videoRetryCount:record.videoRetryCount});
            await appendLog(`TAB ${tag} ÃƒÂ¢Ã¢â€žÂ¢Ã‚Â» VIDEO retry ${record.videoRetryCount}/2 Ãƒâ€šÃ‚Â· dÃƒÆ’Ã‚Â¹ng lÃƒÂ¡Ã‚ÂºÃ‚Â¡i scene image ${record.selectedImage?.mediaId||'-'} Ãƒâ€šÃ‚Â· ${text}`,'warning');
            await sleep(900*record.videoRetryCount);
            videoReadyQueue.push(index);
          }else{
            record.error=text;record.videoState='ERROR';
            await patchJob(index,{videoState:'ERROR',error:text,done:true});
            sendSceneCheckpoint(record,'VIDEO_FAILED',{retryable:isTransientFlowUiError(error)});
            await appendLog(`TAB ${tag} ÃƒÂ¢Ã‚ÂÃ…â€™ VIDEO: ${text}`,'error');
            if(isQuotaLikeError(error)) await setStopped(error);
          }
        }
      }finally{
        videoInFlight.delete(index);await refreshMetrics();wake.notify();
      }
    })();
    videoInFlight.set(index,tracked);
  };

  const submitTask=async task=>{
    assertAlive();
    const record=records[task.index],tag=`[${task.index+1}/${totalNow()}]`;
    try{
      if(task.type==='IMAGE'){
        await ensureMode('IMAGE');
        await appendLog(`SUBMIT QUEUE ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ IMAGE ${tag} | FIFO#${task.enqueuedSeq}`,'info');
        const started=await startImageJob(tabId,record,options,limiter,totalNow());
        systemicFailureStreak=0;
        finishImageLifecycle(task.index,started.lifecycle);
      }else{
        await ensureMode('VIDEO');
        await appendLog(`SUBMIT QUEUE ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ VIDEO ${tag} | FIFO#${task.enqueuedSeq}`,'info');
        const started=await startVideoJob(tabId,record,options,limiter,totalNow());
        systemicFailureStreak=0;
        finishVideoLifecycle(task.index,started.lifecycle);
      }
    }catch(error){
      const text=error?.message||String(error);
      const retryKey=task.type==='IMAGE'?'imageRetryCount':'videoRetryCount';
      const count=Number(record[retryKey]||0);
      if(isSystemicFlowUiError(error)) systemicFailureStreak+=1; else systemicFailureStreak=0;
      if(systemicFailureStreak>=systemicFailureLimit && !stopSubmitting){
        await appendLog(`ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂºÃ‚Â¡ CIRCUIT BREAKER Ãƒâ€šÃ‚Â· ${systemicFailureStreak} lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i UI hÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡ thÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœng liÃƒÆ’Ã‚Âªn tiÃƒÂ¡Ã‚ÂºÃ‚Â¿p ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ dÃƒÂ¡Ã‚Â»Ã‚Â«ng submit mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi. GiÃƒÂ¡Ã‚Â»Ã‚Â¯ checkpoint vÃƒÆ’Ã‚Â  trÃƒÂ¡Ã‚ÂºÃ‚Â£ phÃƒÂ¡Ã‚ÂºÃ‚Â§n thiÃƒÂ¡Ã‚ÂºÃ‚Â¿u vÃƒÂ¡Ã‚Â»Ã‚Â server retry sau.`, 'error');
        await setStopped(new Error(`Flow UI circuit breaker: ${text}`));
      }
      const canRetry=isTransientFlowUiError(error) && count<3 && !stopSubmitting;
      if(canRetry){
        record[retryKey]=count+1;record.error=null;
        const delay=Math.min(5000,700*(2**count));
        if(task.type==='IMAGE'){
          record.imageState='WAIT';
          await patchJob(task.index,{imageState:'RETRY_WAIT',error:text,done:false,[retryKey]:record[retryKey]});
          imageSubmitQueue.push({...task,enqueuedSeq:++enqueueSeq,enqueuedAt:Date.now()+delay});
        }else{
          record.videoState='READY';
          await patchJob(task.index,{videoState:'RETRY_WAIT',error:text,done:false,[retryKey]:record[retryKey]});
          videoSubmitQueue.push({...task,enqueuedSeq:++enqueueSeq,enqueuedAt:Date.now()+delay});
        }
        await appendLog(`TAB ${tag} ÃƒÂ¢Ã¢â€žÂ¢Ã‚Â» ${task.type} submit retry ${record[retryKey]}/3 sau ${(delay/1000).toFixed(1)}s Ãƒâ€šÃ‚Â· ${text}`,'warning');
        // One normalized reload per retry is cheaper than allowing 20 later tasks to
        // fail on the same broken Flow UI state.
        if(record[retryKey]===1){
          await reloadAndNormalizeFlow(tabId,`${task.type} transient submit lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i`).catch(()=>{});
          currentMode=null;
        }
        await sleep(delay);
      }else{
        record.error=text;
        if(task.type==='IMAGE'){
          record.imageState='ERROR';await patchJob(task.index,{imageState:'ERROR',error:text,done:true});
          sendSceneCheckpoint(record,'IMAGE_SUBMIT_FAILED',{retryable:isTransientFlowUiError(error)});
        }else{
          record.videoState='ERROR';await patchJob(task.index,{videoState:'ERROR',error:text,done:true});
          sendSceneCheckpoint(record,'VIDEO_SUBMIT_FAILED',{retryable:isTransientFlowUiError(error)});
        }
        await appendLog(`TAB ${tag} ÃƒÂ¢Ã‚ÂÃ…â€™ ${task.type} submit: ${text}`,'error');
        if(isQuotaLikeError(error)) await setStopped(error);
      }
    }finally{await refreshMetrics();wake.notify();}
  };

  await appendLog(`QUEUE SCHEDULER: IMAGE max=${options.imageConcurrency} Ãƒâ€šÃ‚Â· VIDEO max=${options.videoConcurrency} Ãƒâ€šÃ‚Â· policy=${options.submitPolicy==='GLOBAL_FIFO'?'FIFO tÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢ng':'FIFO nhÃƒÆ’Ã‚Â³m + Ãƒâ€ Ã‚Â°u tiÃƒÆ’Ã‚Âªn video nhÃƒÂ¡Ã‚ÂºÃ‚Â¹'} Ãƒâ€šÃ‚Â· autoDownload=${options.autoDownloadVideo?'ON':'OFF'}`,'info');

  while(true){
    assertAlive();
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
        const text=`KhÃƒÆ’Ã‚Â´ng submit video vÃƒÆ’Ã‚Â¬ scheduler Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ dÃƒÂ¡Ã‚Â»Ã‚Â«ng: ${stopReason}`;
        record.error=record.error||text;record.videoState='NOT_SUBMITTED';
        await patchJob(index,{videoState:'NOT_SUBMITTED',error:record.error,done:true});
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
  assertServerControlAlive();
  const tabId=message.tabId;
  if(!Number.isInteger(tabId)) throw new Error('KhÃƒÆ’Ã‚Â´ng xÃƒÆ’Ã‚Â¡c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tab hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡n tÃƒÂ¡Ã‚ÂºÃ‚Â¡i.');

  const imageModel=String(message.options?.imageModel||'').trim();
  const videoModel=String(message.options?.videoModel||'').trim();
  const imageEnabled=imageModel.toUpperCase()!=='NONE';
  const videoEnabled=videoModel.toUpperCase()!=='NONE';
  if(!imageEnabled&&!videoEnabled) throw new Error('Image model vÃƒÆ’Ã‚Â  Video model khÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚Â»Ã†â€™ cÃƒÆ’Ã‚Â¹ng lÃƒÆ’Ã‚Â  None.');
  const serverMessages=Array.isArray(message.serverJobMessages)?message.serverJobMessages:[];
  const pairs=serverMessages.length?[]:(Array.isArray(message.scenes)&&message.scenes.length
    ? normalizeScenes(message.scenes,imageEnabled,videoEnabled)
    : parsePairs(message.pairs,imageEnabled,videoEnabled));
  if(!serverMessages.length&&!pairs.length) throw new Error('ChÃƒâ€ Ã‚Â°a cÃƒÆ’Ã‚Â³ prompt Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ chÃƒÂ¡Ã‚ÂºÃ‚Â¡y.');

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
      assetCache:GLOBAL_ASSET_CACHE,
      systemicFailureLimit:Math.max(2,Math.min(5,Number(message.options?.systemicFailureLimit||3)))
    };

    const records=[];
    if(serverMessages.length) appendServerMessagesToRecords(serverMessages,records,{imageEnabled,videoEnabled});
    else records.push(...pairs.map((pair,index)=>({index,sceneId:pair.sceneId??index+1,pair,imageState:imageEnabled?'WAIT':'SKIP',videoState:videoEnabled?'WAIT':'SKIP',error:null,videoIds:[]})));
    runtimeCache.jobs=Object.fromEntries(records.map(r=>[r.index,{
      imageState:r.imageState,videoState:r.videoState,
      percent:r.resumeSkipImage?(videoEnabled?50:100):0,done:false
    }]));
    runtimeCache.metrics={imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,imageLimit:options.imageConcurrency,videoLimit:options.videoConcurrency,done:0,errors:0,total:records.length};
    await persistRuntime();

    await appendLog(`1 TAB / ${records.length} job | IMAGE max=${options.imageConcurrency} | VIDEO max=${options.videoConcurrency} | Extend=x${options.videoExtendFactor} | Submit=${options.submitPolicy==='GLOBAL_FIFO'?'FIFO tÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢ng':'FIFO nhÃƒÆ’Ã‚Â³m + Ãƒâ€ Ã‚Â°u tiÃƒÆ’Ã‚Âªn video nhÃƒÂ¡Ã‚ÂºÃ‚Â¹'} | Auto download=${options.autoDownloadVideo?'ON':'OFF'}`,'info');
    await appendLog(`project=${projectId} | view=All Media | 1 submit dispatcher duy nhÃƒÂ¡Ã‚ÂºÃ‚Â¥t ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ Settings/Prompt/Asset Picker/Create khÃƒÆ’Ã‚Â´ng chÃƒÂ¡Ã‚ÂºÃ‚Â¡y chÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“ng nhau`,'info');
    await setProgress(0,'Queue Scheduler Ãƒâ€šÃ‚Â· chuÃƒÂ¡Ã‚ÂºÃ‚Â©n bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹',`TÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢ng ${records.length} job`);

    const limiter=createSubmitLimiter({maxPerMinute:options.maxSubmitsPerMinute,minGapMs:options.submitGapMs});
    await runQueueScheduler(tabId,records,options,limiter,message.serverDynamicBatch||null);
    assertServerControlAlive();
    await runVideoExtendPhase(tabId,records,options,limiter);
    assertServerControlAlive();
    await downloadVideosAfterExtend(records,options);
    assertServerControlAlive();
    if(options.downloadTasks.length){
      await appendLog(`ChÃƒÂ¡Ã‚Â»Ã‚Â ${options.downloadTasks.length} tÃƒÆ’Ã‚Â¡c vÃƒÂ¡Ã‚Â»Ã‚Â¥ download cÃƒÂ¡Ã‚Â»Ã‚Â§a Server hoÃƒÆ’Ã‚Â n tÃƒÂ¡Ã‚ÂºÃ‚Â¥t...`,'info');
      await Promise.allSettled(options.downloadTasks);
    }
    const {done,errors}=countFinishedJobs(records.length);
    await setMetrics({imageInFlight:0,videoInFlight:0,submitImageQueued:0,submitVideoQueued:0,imageLimit:options.imageConcurrency,videoLimit:options.videoConcurrency,done,errors,total:records.length});
    const failures=records.filter(r=>r.error).map(r=>({index:r.index,pair:r.pair,error:r.error}));
    if(failures.length) await appendLog(`Queue batch hoÃƒÆ’Ã‚Â n tÃƒÂ¡Ã‚ÂºÃ‚Â¥t: ${records.length-failures.length}/${records.length} thÃƒÆ’Ã‚Â nh cÃƒÆ’Ã‚Â´ng, ${failures.length} lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i.`,'error');
    else await appendLog(`Queue batch hoÃƒÆ’Ã‚Â n tÃƒÂ¡Ã‚ÂºÃ‚Â¥t ${records.length}/${records.length} job.`,'success');
    return {results:records,failures};
  }finally{
    await detachWorkerDebugger(tabId);
  }
}

chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.type==='FLOW_RUN_PAIRS'){
    (async()=>{
      const runId=`run_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
      await resetRuntimeForRun(runId);
      await appendLog(`BÃƒÂ¡Ã‚ÂºÃ‚Â¯t Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â§u Flow Wardrobe Studio v${EXTENSION_VERSION} Queue Scheduler...`,'info');
      const batch=await runAutomation(message);
      const failures=batch?.failures||[];
      if(failures.length){
        await finishRuntime(false,`Batch xong nhÃƒâ€ Ã‚Â°ng cÃƒÆ’Ã‚Â³ ${failures.length} job lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i.`);
        return {ok:false,error:`CÃƒÆ’Ã‚Â³ ${failures.length} job lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i.`,results:batch.results,failures};
      }
      await finishRuntime(true,`HoÃƒÆ’Ã‚Â n tÃƒÂ¡Ã‚ÂºÃ‚Â¥t ${batch?.results?.length||0} job.`);
      return {ok:true,results:batch.results};
    })().then(sendResponse).catch(async error=>{
      const text=error?.message||String(error);
      await appendLog(`ÃƒÂ¢Ã‚ÂÃ…â€™ ${text}`,'error');
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



