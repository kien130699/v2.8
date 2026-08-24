const $ = id => document.getElementById(id);
let lastVideoTestJob = null, lastFactoryRun = null, logMode = 'short', logRows = [], aiModelsCache = [];
const uploadMeta = new Map();

const actionLocks = new Map();
const rapidClicks = new WeakMap();
const mutationInFlight = new Map();
const recentMutations = new Map();
function uiText(v){try{return window.UI_I18N?.dynamic?.(String(v??''))||String(v??'')}catch{return String(v??'')}}
function ensureToastHost(){let h=document.getElementById('toastHost');if(!h){h=document.createElement('div');h.id='toastHost';h.className='toast-host';document.body.appendChild(h)}return h}
function toast(message,type='info',timeout=3200){const h=ensureToastHost();const el=document.createElement('div');el.className='toast '+type;el.textContent=uiText(message);h.appendChild(el);requestAnimationFrame(()=>el.classList.add('show'));setTimeout(()=>{el.classList.remove('show');setTimeout(()=>el.remove(),220)},timeout)}
async function guardedAction(key,label,fn){if(actionLocks.has(key)){toast(`${label} đang xử lý. Không gửi lặp.`, 'warn', 3600);return {blocked:true}}actionLocks.set(key,Date.now());try{return await fn()}finally{actionLocks.delete(key)}}
function friendlySuccess(url,method){if(url.includes('/prepare-persona'))return 'Đã tạo lại FRONT chuẩn thành công.';if(url.includes('/angles/generate-missing'))return 'Đã kiểm tra/tạo các góc còn thiếu.';if(url.includes('/angles/')&&url.includes('/generate'))return 'Đã gửi yêu cầu tạo góc.';if(url.includes('/scheduler/')&&url.endsWith('/start'))return 'Đã bật/cập nhật lịch đăng.';if(url.includes('/scheduler/')&&url.endsWith('/stop'))return 'Đã dừng lịch đăng.';if(url.includes('/fill-now'))return 'Đã kiểm tra và tạo bù hàng đợi.';if(url.includes('/publish-now'))return 'Đã xử lý yêu cầu đăng ngay.';if(url.includes('/factory/v2/generate'))return 'Đã tạo batch thành công.';if(url.includes('/page-profiles')&&method==='POST')return 'Đã lưu hồ sơ Trang.';if(method==='DELETE')return 'Đã xóa thành công.';return 'Thao tác thành công.'}
document.addEventListener('click',e=>{const b=e.target?.closest?.('button');if(!b||b.disabled)return;const now=Date.now(),last=rapidClicks.get(b)||0;if(now-last<700){e.preventDefault();e.stopImmediatePropagation();toast('Bạn vừa bấm nút này. Vui lòng chờ thao tác trước xử lý xong.', 'warn');return}rapidClicks.set(b,now)},true);

function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
async function api(url,opts={}){
  const cfg={...opts};cfg.headers={...(opts.headers||{})};if(!(opts.body instanceof FormData)){cfg.headers['Content-Type']='application/json'}
  const method=String(cfg.method||'GET').toUpperCase();
  const mutation=method!=='GET';
  const bodyKey=typeof cfg.body==='string'?cfg.body:(cfg.body instanceof FormData?'[formdata]':'');
  const reqKey=mutation?`${method}|${url}|${bodyKey}`:'';
  if(mutation){
    const recent=recentMutations.get(reqKey);
    if(recent&&Date.now()-recent.at<3000){toast('Thao tác này vừa thực hiện thành công. Không gửi lặp lần hai.','warn',3800);return recent.data}
    if(mutationInFlight.has(reqKey)){toast('Thao tác này đang xử lý. Không gửi request trùng.','warn',3800);return mutationInFlight.get(reqKey)}
  }
  const task=(async()=>{
    let r;try{r=await fetch(url,cfg)}catch(err){if(mutation&&!opts.silentError)toast('Không kết nối được server: '+err.message,'error',4500);throw err}
    const t=await r.text();let d;try{d=JSON.parse(t)}catch{d=t}
    if(!r.ok){const msg=typeof d==='string'?d:(d.detail||JSON.stringify(d));if(mutation&&!opts.silentError)toast(msg,'error',5000);throw new Error(msg)}
    if(mutation){recentMutations.set(reqKey,{at:Date.now(),data:d});setTimeout(()=>{const x=recentMutations.get(reqKey);if(x&&Date.now()-x.at>=2900)recentMutations.delete(reqKey)},3200);if(!opts.silentSuccess)toast(opts.successMessage||friendlySuccess(url,method),'success')}
    return d
  })();
  if(mutation)mutationInFlight.set(reqKey,task);
  try{return await task}finally{if(mutation&&mutationInFlight.get(reqKey)===task)mutationInFlight.delete(reqKey)}
}
function lines(id){return ($(id)?.value||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean)}
async function uploadInput(fileId,pathId){const f=$(fileId)?.files?.[0];if(!f)return ($(pathId)?.value||'').trim()||null;const fd=new FormData();fd.append('file',f);const r=await fetch('/api/uploads',{method:'POST',body:fd});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Upload lỗi');uploadMeta.set(fileId,d);if($(pathId))$(pathId).value=d.path;if(fileId==='pfPersonFile'&&$('pfImportStatus')){$('pfImportStatus').innerHTML=d.image_valid?`<span class="good"><b>ẢNH FRONT ĐÃ KIỂM TRA</b> · ${esc(d.width)}×${esc(d.height)} · ${esc(d.format||'IMAGE')}</span>`:'<span class="bad">Ảnh FRONT chưa xác minh.</span>';}return d.path}
function go(id){document.querySelectorAll('.section').forEach(x=>x.classList.toggle('active',x.id===id));document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.tab===id));$('pageTitle').textContent=document.querySelector(`.nav button[data-tab="${id}"]`)?.textContent||id;if(id==='pages'){loadSimplePages()}if(id==='logs'){loadLogs()}if(id==='system'){refreshTop();refreshDownloadTestStatus()}if(id==='autofactory'){closeSettingsEditor();loadProfiles().then(()=>{const pid=$('fvProfile')?.value||'';showSettingsProfileSummary(pid);if(pid)loadProfileWorkspace(pid)});loadPages();loadAiModels()}}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>go(b.dataset.tab));
function logEvent(x){return x}
function pill(s){return `<span class="pill ${esc(s)}">${esc(s)}</span>`}
function qcBadge(q){if(!q)return '';const score=q.score??q.details?.score??0;const pass=q.passed??q.details?.passed;return `<div class="${pass?'good':'bad'}"><b>QC ${esc(score)}</b> ${pass?'PASS':'FAIL'}</div>`}



function fmtBytes(n){n=Number(n||0);if(n<1024)return n+' B';if(n<1024*1024)return (n/1024).toFixed(1)+' KB';return (n/1024/1024).toFixed(1)+' MB'}
function renderMusicLibrary(d){
  const rows=d?.items||[];
  if($('musicLibrarySummary'))$('musicLibrarySummary').textContent=`${rows.length} track · AUTO random khi render Page này`;
  if($('pfMusic'))$('pfMusic').value=rows.map(x=>x.path).join('\n');
  const box=$('musicLibraryTable');if(!box)return;
  if(!rows.length){box.innerHTML='<div class="hint">Chưa có MP3. Dán link TikTok/CapCut ở trên để import.</div>';return}
  box.innerHTML=`<table><thead><tr><th>Track</th><th>Nguồn</th><th>Thông tin</th><th></th></tr></thead><tbody>${rows.map(x=>`
    <tr>
      <td><b>${esc(x.name||'')}</b><br><span class="mono">${esc((x.sha256||'').slice(0,12))}</span></td>
      <td>${esc(x.source_host||'LOCAL')}</td>
      <td>${x.exists?'<span class="good">READY</span>':'<span class="bad">MISSING</span>'} · ${esc(fmtBytes(x.size))}${x.duration?` · ${Number(x.duration).toFixed(1)}s`:''}${x.codec?` · ${esc(x.codec)}`:''}</td>
      <td><button class="btn secondary" onclick='removeMusicTrack(${JSON.stringify(x.path)})'>BỎ</button></td>
    </tr>`).join('')}</tbody></table>`;
}
async function loadMusicLibrary(profileId){
  if(!profileId)return;
  try{const d=await api('/api/page-profiles/'+encodeURIComponent(profileId)+'/music');renderMusicLibrary(d)}
  catch(e){if($('musicLibraryTable'))$('musicLibraryTable').innerHTML=`<div class="bad">${esc(e.message)}</div>`}
}
async function importMusicUrl(){
  const pid=$('pfId')?.value||$('fvProfile')?.value||'';
  const url=($('musicImportUrl')?.value||'').trim();
  if(!pid){toast('Chọn/Lưu Page Profile trước.','error');return}
  if(!url){toast('Dán URL TikTok hoặc CapCut.','error');return}
  const b=$('musicImportBtn');if(b)b.disabled=true;
  if($('musicImportStatus'))$('musicImportStatus').innerHTML='<span class="warn">Đang tải + chuyển MP3 + ffprobe + SHA-256...</span>';
  try{
    const d=await api('/api/page-profiles/'+encodeURIComponent(pid)+'/music/import-url',{method:'POST',body:JSON.stringify({url})});
    if($('musicImportStatus'))$('musicImportStatus').innerHTML=`<span class="good"><b>MP3 READY</b> · ${esc(d.music.name)} · ${Number(d.music.duration||0).toFixed(1)}s · ${esc((d.music.sha256||'').slice(0,16))}</span>`;
    if($('musicImportUrl'))$('musicImportUrl').value='';
    await loadMusicLibrary(pid);
    toast('Đã thêm MP3 vào đúng Page.','success');
  }catch(e){
    if($('musicImportStatus'))$('musicImportStatus').innerHTML=`<span class="bad">${esc(e.message)}</span>`;
    toast(e.message,'error');
  }finally{if(b)b.disabled=false}
}
async function removeMusicTrack(path){
  const pid=$('pfId')?.value||$('fvProfile')?.value||'';if(!pid)return;
  try{
    const d=await api('/api/page-profiles/'+encodeURIComponent(pid)+'/music/remove',{method:'POST',body:JSON.stringify({path})});
    renderMusicLibrary({items:d.items||[]});
    toast('Đã bỏ track khỏi Page.','success');
  }catch(e){toast(e.message,'error')}
}

let profileWorkspaceCache={profile_id:'',activity:[],results:[]};
let profileResultsFilter='all';

function fmtWsTime(v){
  if(!v)return '-';
  try{return new Intl.DateTimeFormat(undefined,{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v))}catch{return String(v)}
}
function renderProfileActivity(){
  const box=$('profileActivityTable'); if(!box)return;
  const rows=profileWorkspaceCache.activity||[];
  if(!rows.length){box.innerHTML='<div class="hint">Chưa có lịch sử/tác vụ của hồ sơ này.</div>';return}
  box.innerHTML=`<table><thead><tr><th>Thời gian</th><th>Loại</th><th>Trạng thái</th><th>Chi tiết / QC</th><th></th></tr></thead><tbody>${rows.map(x=>`
    <tr>
      <td>${esc(fmtWsTime(x.created_at))}</td>
      <td>${esc(x.label||x.kind||'')}</td>
      <td>${pill(x.status||'-')}</td>
      <td>${esc(x.detail||'')}${x.qc_text?`<br><span class="${x.qc_pass?'good':'warn'}">${esc(x.qc_text)}</span>`:''}${x.error?`<br><span class="bad">${esc(x.error)}</span>`:''}</td>
      <td>${x.retry_job_id?`<button class="btn secondary" onclick="retryProfileJob('${esc(x.retry_job_id)}')">CHẠY LẠI</button>`:''}</td>
    </tr>`).join('')}</tbody></table>`;
}
function profileResultType(a){
  if(a.kind==='final_video'||a.kind==='video')return 'video';
  if(a.kind==='image')return 'image';
  return 'other';
}
function renderProfileResults(){
  const box=$('profileResultsGrid');if(!box)return;
  const all=profileWorkspaceCache.results||[];
  const rows=all.filter(a=>profileResultsFilter==='all'||profileResultType(a)===profileResultsFilter);
  const videos=all.filter(a=>profileResultType(a)==='video').length;
  const images=all.filter(a=>profileResultType(a)==='image').length;
  if($('profileResultsSummary'))$('profileResultsSummary').textContent=`${all.length} media · ${videos} video · ${images} ảnh · child=STREAM · final=LOCAL`;
  document.querySelectorAll('[data-profile-results-filter]').forEach(b=>b.classList.toggle('active-filter',b.dataset.profileResultsFilter===profileResultsFilter));
  if(!rows.length){box.innerHTML='<div class="profile-result-empty">Chưa có media phù hợp của hồ sơ này.</div>';return}
  box.innerHTML=rows.map(a=>{
    const src=a.stream_url||a.local_url||a.url||'';
    const type=profileResultType(a),isFinal=!!a.is_final;
    const file=String(a.local_path||'').split(/[\\/]/).pop()||a.title||`${type}-${a.scene_id||0}`;
    const media=type==='video'
      ?`<div class="profile-result-media video"><video controls playsinline preload="metadata" src="${esc(src)}"></video></div>`
      :`<div class="profile-result-media"><img loading="lazy" src="${esc(src)}"></div>`;
    const badge=isFinal?'<span class="good">FINAL · LOCAL</span>':'<span class="stream-badge">CHILD · STREAM</span>';
    const buttons=isFinal
      ?`<a class="btn secondary" href="${esc(src)}" target="_blank" rel="noopener">MỞ OUTPUT</a>`
      :`<a class="btn secondary" href="${esc(src)}" target="_blank" rel="noopener">STREAM</a>`;
    return `<div class="profile-result-card">${media}<div class="profile-result-body">
      <div class="profile-result-title">${esc(a.title||file)}</div>
      <div class="profile-result-meta">${badge} · ${type==='video'?'VIDEO':'ẢNH'} · scene ${Number(a.scene_id||0)} · ${esc(fmtWsTime(a.created_at))}${a.qc_text?`<br>${esc(a.qc_text)}`:''}</div>
      <div class="toolbar">${buttons}</div>
    </div></div>`;
  }).join('');
}

let profileWorkspaceRequestSeq=0;
async function loadProfileWorkspace(profileId){
  if(!profileId)return;
  const seq=++profileWorkspaceRequestSeq;
  profileWorkspaceCache={profile_id:profileId,activity:[],results:[],scheduler:null};
  renderProfileActivity();renderProfileResults();showSettingsProfileSummary(profileId);
  try{
    const d=await api('/api/auto/profiles/'+encodeURIComponent(profileId)+'/workspace');
    if(seq!==profileWorkspaceRequestSeq||String($('fvProfile')?.value||'')!==String(profileId))return;
    profileWorkspaceCache=d;
    renderProfileActivity();
    renderProfileResults();
    showSettingsProfileSummary(profileId);
  }catch(e){
    if($('profileActivityTable'))$('profileActivityTable').innerHTML=`<div class="bad">${esc(e.message)}</div>`;
    if($('profileResultsGrid'))$('profileResultsGrid').innerHTML=`<div class="profile-result-empty bad">${esc(e.message)}</div>`;
  }
}
async function retryProfileJob(jobId){
  try{await api('/api/flow/jobs/'+encodeURIComponent(jobId)+'/retry',{method:'POST',body:'{}'});toast('Đã đưa tác vụ vào hàng đợi.','success');await loadProfileWorkspace($('fvProfile')?.value||'')}catch(e){toast(e.message,'error')}
}

let resultsFilter='all';
let resultsCache=[];

function resultType(a){
  if(a?.kind==='final_video'||(a?.kind==='video'&&Number(a?.scene_id||0)===0))return 'video';
  if(a?.kind==='image')return 'image';
  return 'other';
}
function resultIdentity(a){
  return String(a?.local_path||a?.media_id||a?.url||`${a?.job_id||''}:${a?.scene_id||''}:${a?.kind||''}`);
}
function finalResultAssets(items){
  const seen=new Set(),out=[];
  for(const a of (items||[])){
    const type=resultType(a);
    if(type==='other')continue;
    // Scene video clips are technical intermediates; only final_video/video scene 0 is a result.
    if(type==='video' && !(a.kind==='final_video'||Number(a.scene_id||0)===0))continue;
    const src=a.local_url||a.url||'';
    if(!src)continue;
    const key=resultIdentity(a);
    if(seen.has(key))continue;
    seen.add(key);out.push({...a,_result_type:type});
  }
  return out;
}
function fmtResultTime(v){
  if(!v)return '-';
  try{return new Intl.DateTimeFormat(undefined,{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(v))}catch{return String(v)}
}
function resultCard(a){
  const src=a.local_url||a.url||'';
  const type=a._result_type||resultType(a);
  const media=type==='video'
    ?`<div class="result-media video"><video controls preload="metadata" src="${esc(src)}"></video></div>`
    :`<div class="result-media"><img loading="lazy" src="${esc(src)}"></div>`;
  const filename=String(a.local_path||'').split(/[\\/]/).pop()||a.title||`${type}-${a.id||''}`;
  const title=a.title||filename;
  const scene=Number(a.scene_id||0)>0?` · cảnh ${esc(a.scene_id)}`:'';
  const qc=(type==='video'&&a.qc)?qcBadge(a.qc):'';
  const fb=(type==='video'&&a.local_path)
    ?`<button class="btn secondary" data-path="${esc(a.local_path)}" data-title="${esc(title)}" onclick="useVideo(this)">FACEBOOK</button>`:'';
  return `<div class="result-card">
    ${media}
    <div class="result-body">
      <div class="result-title" title="${esc(title)}">${esc(title)}</div>
      <div class="result-meta">${type==='video'?'VIDEO':'ẢNH'}${scene}<br>${esc(fmtResultTime(a.created_at))}${qc}</div>
      <div class="result-actions">
        <a class="btn secondary" href="${esc(src)}" target="_blank" rel="noopener">XEM</a>
        <a class="btn secondary" href="${esc(src)}" download>TẢI</a>
        ${fb}
      </div>
    </div>
  </div>`;
}
function renderResults(){
  if(!$('resultsGrid'))return;
  const rows=resultsCache.filter(a=>resultsFilter==='all'||a._result_type===resultsFilter);
  const videos=resultsCache.filter(a=>a._result_type==='video').length;
  const images=resultsCache.filter(a=>a._result_type==='image').length;
  if($('resultsSummary'))$('resultsSummary').textContent=`${resultsCache.length} kết quả · ${videos} video · ${images} ảnh`;
  $('resultsGrid').innerHTML=rows.length?rows.map(resultCard).join(''):`<div class="result-empty">Chưa có ${resultsFilter==='video'?'video thành phẩm':resultsFilter==='image'?'ảnh':'kết quả'}.</div>`;
  document.querySelectorAll('[data-results-filter]').forEach(b=>b.classList.toggle('active-filter',b.dataset.resultsFilter===resultsFilter));
}
function setResultsFilter(v){
  resultsFilter=['all','video','image'].includes(v)?v:'all';
  renderResults();
}
async function loadResults(){
  if(!$('resultsGrid'))return;
  try{
    resultsCache=finalResultAssets(await api('/api/assets?limit=500'));
    renderResults();
  }catch(e){
    $('resultsGrid').innerHTML=`<div class="result-empty bad">${esc(e.message)}</div>`;
  }
}

// V2.14.29: Results controls do not depend on inline onclick/global lookup.
// Keep window exports only as compatibility fallback for old cached HTML.
window.setResultsFilter=setResultsFilter;
window.loadResults=loadResults;
document.querySelectorAll('[data-results-filter]').forEach(b=>{
  b.addEventListener('click',()=>setResultsFilter(b.dataset.resultsFilter));
});
document.querySelectorAll('[data-results-refresh]').forEach(b=>{
  b.addEventListener('click',()=>loadResults());
});

document.querySelectorAll('[data-profile-results-filter]').forEach(b=>{
  b.addEventListener('click',()=>{profileResultsFilter=b.dataset.profileResultsFilter||'all';renderProfileResults()});
});
if($('profileWorkspaceRefresh'))$('profileWorkspaceRefresh').addEventListener('click',()=>loadProfileWorkspace($('fvProfile')?.value||''));
if($('musicImportBtn'))$('musicImportBtn').addEventListener('click',importMusicUrl);

function assetCards(items){return (items||[]).map(a=>{const src=a.local_url||a.url||'';const isVideo=['video','final_video'].includes(a.kind);const media=isVideo?(src?`<video controls preload="metadata" src="${esc(src)}"></video>`:''):(src?`<img loading="lazy" src="${esc(src)}">`:'');const use=(isVideo&&a.local_path)?`<div class="toolbar"><button class="btn secondary" data-path="${esc(a.local_path)}" data-title="${esc(a.title||'')}" onclick="useVideo(this)">ĐƯA SANG FACEBOOK</button></div>`:'';return `<div class="asset">${media}<div class="meta"><b>${esc(a.kind)}</b> · scene ${esc(a.scene_id??'-')}<br>${esc(a.title||'')}${qcBadge(a.qc)}</div>${use}</div>`}).join('')}
function useVideo(btn){toast('Video đã sẵn sàng. Page sẽ đăng theo lịch đã đặt.','info')}
const FRONTEND_VERSION='2.14.29';
function versionFamily(v){const p=String(v||'').split('.');return p.length>=2?`${p[0]}.${p[1]}`:String(v||'')}
function frontendBackendCompatible(backend){return !backend || versionFamily(backend)===versionFamily(FRONTEND_VERSION)}
let refreshTopPromise=null, refreshTopTimer=null;
async function refreshTop(){
  if(refreshTopPromise)return refreshTopPromise;
  refreshTopPromise=(async()=>{try{
    const h=await api('/api/dashboard/summary');
    $('kAgents').textContent=h.agents_connected;
    $('kActive').textContent=h.active_jobs;
    $('kProfiles').textContent=h.page_profiles;
    $('kVideos').textContent=h.videos_completed;
    $('agentText').textContent=`${h.agents_connected} Flow agent`;
    $('agentDot').classList.toggle('on',h.agents_connected>0);
    if(!frontendBackendCompatible(h.server_version)){
      const warn=`FRONTEND V${FRONTEND_VERSION} nhưng BACKEND ${h.server_version} — major/minor không khớp; kiểm tra patch.`;
      if($('profileResult'))$('profileResult').innerHTML=`<span class="bad">${esc(warn)}</span>`;
      console.error(warn)
    }
    renderAgents(h.agents||[])
  }catch(e){}finally{refreshTopPromise=null}})();
  return refreshTopPromise
}
function scheduleRefreshTop(delay=120){clearTimeout(refreshTopTimer);refreshTopTimer=setTimeout(refreshTop,delay)}
function renderAgents(rows){$('agentsTable').innerHTML=rows.length?`<div class="tablewrap"><table><thead><tr><th>Agent</th><th>Version</th><th>Status</th><th>Job</th></tr></thead><tbody>${rows.map(x=>`<tr><td class="mono">${esc(x.extension_id||x.id)}</td><td>${esc(x.version||'-')}</td><td>${x.compatible===false?'<span class="pill fail">QUÁ CŨ · cần '+esc(x.required_version||'?')+'</span>':pill(x.busy?'running':'done')}</td><td class="mono">${esc(x.job_id||'-')}</td></tr>`).join('')}</tbody></table></div>`:'<div class="bad">Chưa có Flow extension kết nối.</div>'}
async function pingAgents(){try{alert(JSON.stringify(await api('/api/agents/ping',{method:'POST',body:'{}'}),null,2))}catch(e){alert(e.message)}}

async function loadAiModels(){
  try{
    const d=await api('/api/ai/models');
    aiModelsCache=d.models||[];
    if($('router9Info')) $('router9Info').innerHTML=d.ok?`<span class="good">9Router OK</span> · ${esc(aiModelsCache.length)} model gọn`:`<span class="bad">9Router lỗi</span>`;
    if($('pfAiModel')){
      const current=$('pfAiModel').value;
      $('pfAiModel').innerHTML='<option value="">AUTO — ưu tiên model đầu tiên</option>'+aiModelsCache.map(x=>`<option value="${esc(x.id)}">${esc(x.label||x.id)}</option>`).join('');
      if(current&&[...$('pfAiModel').options].some(o=>o.value===current)) $('pfAiModel').value=current;
    }
  }catch(e){if($('router9Info'))$('router9Info').innerHTML=`<span class="bad">9Router: ${esc(e.message)}</span>`}
}
function renderAiModelsTable(){
  if(!$('aiModelsTable')) return;
  const rows=(aiModelsCache||[]).filter(x=>['gpt','gemini'].includes(x.family));
  $('aiModelsTable').innerHTML=rows.length?`<table><thead><tr><th>Status</th><th>Family</th><th>Model</th><th>Latency</th><th>Lỗi</th><th></th></tr></thead><tbody>${rows.map(x=>{
    const st=x.status||'untested';
    const icon=x.disabled?'⛔ CLEARED':st==='ok'?'🟢 OK':st==='error'?'🔴 ERROR':st==='testing'?'🟡 TESTING':'⚪ UNTESTED';
    const cls=(st==='error'||x.disabled)?'bad':st==='ok'?'good':'';
    return `<tr><td class="${cls}"><b>${icon}</b></td><td>${esc(x.family.toUpperCase())}</td><td class="mono">${esc(x.id)}</td><td>${x.latency_ms?esc(x.latency_ms)+' ms':'-'}</td><td class="bad">${esc(x.error||'')}</td><td><button class="btn secondary" onclick="testAiModel('${esc(x.id)}')">TEST / RETEST</button></td></tr>`;
  }).join('')}</tbody></table>`:'<div class="hint">Không thấy GPT/Gemini model từ 9router.</div>';
}
async function testAiModel(modelId){
  try{
    if($('router9TestInfo')) $('router9TestInfo').innerHTML=`<span class="hint">Đang test ${esc(modelId)}...</span>`;
    const d=await api('/api/ai/models/test',{method:'POST',body:JSON.stringify({model_id:modelId})});
    if(!d.ok) alert(`MODEL LỖI\n${modelId}\n${d.error||''}`);
    await loadAiModels();
  }catch(e){alert(e.message);await loadAiModels()}
}
async function testAllAiModels(){
  if(!confirm('Test tất cả GPT/Gemini model từ 9router? Mỗi model sẽ gửi 1 request rất ngắn.'))return;
  try{
    const d=await api('/api/ai/models/test-all',{method:'POST',body:'{}'});
    if($('router9TestInfo')) $('router9TestInfo').innerHTML=`<span class="hint">Đang test ${d.testing} model nền... bảng sẽ tự refresh.</span>`;
    const poll=async()=>{await loadAiModels();const pending=(aiModelsCache||[]).filter(x=>['gpt','gemini'].includes(x.family)&&x.status==='testing').length;if(pending>0)setTimeout(poll,1800)};
    setTimeout(poll,800);
  }catch(e){alert(e.message)}
}
async function clearFailedAiModels(){
  if(!confirm('CLEAR model lỗi? Lỗi model_not_supported sẽ PERMANENT BLOCK và biến khỏi danh sách. Lỗi tạm sẽ soft-clear. Toàn bộ GitHub đã bị chặn sẵn.'))return;
  try{const d=await api('/api/ai/models/clear-errors',{method:'POST',body:'{}'});alert(d.message||('Đã clear '+d.disabled+' model'));await loadAiModels();await loadProfiles()}catch(e){alert(e.message)}
}
async function resetClearedAiModels(){
  if(!confirm('Khôi phục các model SOFT-CLEAR để RETEST? Model GitHub và model_not_supported sẽ KHÔNG được khôi phục.'))return;
  try{const d=await api('/api/ai/models/reset-cleared',{method:'POST',body:'{}'});alert('Đã khôi phục '+d.restored+' model');await loadAiModels()}catch(e){alert(e.message)}
}
let simpleRows=[];
async function simpleImportToken(){
  const token=($('simpleToken')?.value||'').trim();
  if(!token){toast('Dán token Facebook trước.','warn');return}
  return guardedAction('simple-import-token','Import token Page',async()=>{
    const b=$('simpleImportBtn');if(b)b.disabled=true;
    try{
      const d=await api('/api/facebook/pages/import-token',{method:'POST',body:JSON.stringify({token}),silentSuccess:true});
      $('simpleToken').value='';
      toast(`Đã import ${d.saved||0} Page${d.profile_links?` · đồng bộ ${d.profile_links} hồ sơ`:''}.`,'success');
      await loadSimplePages();await loadPages();await loadProfiles();
    }catch(e){toast(e.message,'error',7000)}finally{if(b)b.disabled=false}
  })
}
function simpleModeLabel(v){return v==='IMAGE_BEAT'?'ẢNH':v==='IMAGE_MIX'?'ẢNH + VIDEO':v==='IMAGE_TO_VIDEO'?'ẢNH → VIDEO':'AUTO RANDOM'}
function simpleTransitionLabel(v){return ({
  chaos_mix:'Chaos Mix — SIÊU GIẬT',
  impact_shake:'Impact Shake — GIẬT MẠNH',
  whip_shake:'Whip Shake — QUĂNG CẢNH',
  flash_smash:'Flash Smash — ĐẬP FLASH',
  capcut_beat:'Impact Shake — GIẬT MẠNH',
  flash_cut:'Flash Smash — ĐẬP FLASH',
  mix:'Chaos Mix — SIÊU GIẬT',
  smooth:'Impact Shake — GIẬT MẠNH'
})[v]||v}
function simpleFmtTime(v){if(!v)return '-';try{return new Date(v).toLocaleString('vi-VN')}catch{return v}}

function sceneModeLabel(v,mix=[]){
  const mode=String(v||'GYM').toUpperCase();
  if(mode==='GYM')return 'GYM · PHÒNG GYM';
  if(mode==='BEACH')return 'BEACH · BIỂN';
  if(mode==='VIETNAM')return 'VIỆT NAM · PHỐ/ĐỊA ĐIỂM';
  if(mode==='RANDOM')return 'RANDOM · TẤT CẢ';
  if(mode==='CUSTOM')return 'CUSTOM';
  if(mode==='MIX')return 'MIX · '+((mix||[]).join(' + ')||'GYM + BEACH');
  return mode;
}
function parseSceneMix(id){
  return String($(id)?.value||'').split(/[,;|]+/).map(x=>x.trim().toUpperCase()).filter((x,i,a)=>['GYM','BEACH','VIETNAM','CUSTOM'].includes(x)&&a.indexOf(x)===i);
}
function addSceneMix(v,id){
  const el=$(id);if(!el)return;
  const arr=parseSceneMix(id);v=String(v).toUpperCase();
  if(!arr.includes(v))arr.push(v);
  el.value=arr.join(',');
  el.dispatchEvent(new Event('change',{bubbles:true}));
}
function toggleSimpleSceneMix(){
  const show=$('simpleSceneMode')?.value==='MIX';
  if($('simpleSceneMixWrap'))$('simpleSceneMixWrap').style.display=show?'grid':'none';
}
window.addSceneMix=addSceneMix;

function selectedSimplePageRow(){return simpleRows.find(r=>String(r.page_id)===String($('simplePage')?.value||''))||null}
function syncSimpleProfileSelect(preferId=''){
  const row=selectedSimplePageRow(),sel=$('simpleProfile');if(!sel)return;
  const profiles=row?.profiles||[];
  const current=preferId||sel.value||'';
  sel.innerHTML='<option value="__new__">＋ TẠO HỒ SƠ MỚI</option>'+profiles.map(p=>`<option value="${esc(p.profile_id)}">${esc(p.profile_name||p.profile_id)}</option>`).join('');
  if(current&&profiles.some(p=>String(p.profile_id)===String(current)))sel.value=current;
  else if(profiles.length)sel.value=profiles[0].profile_id;
  else sel.value='__new__';
  applySimpleProfileSelection();
}
function applySimpleProfileSelection(){
  const row=selectedSimplePageRow();if(!row)return;
  const pid=$('simpleProfile')?.value||'__new__';
  const p=(row.profiles||[]).find(x=>String(x.profile_id)===String(pid));
  if(!p){
    $('simpleProfileName').value='';
    $('simpleVideoMode').value='AUTO';$('simpleTransition').value='chaos_mix';$('simpleSceneMode').value='GYM';$('simpleSceneMix').value='GYM,BEACH';toggleSimpleSceneMix();$('simpleFacePath').value='';
    $('simpleSaveStatus').innerHTML='<span class="hint">Hồ sơ mới: nhập tên (tùy chọn) + import FRONT rồi LƯU.</span>';return;
  }
  $('simpleProfileName').value=p.profile_name||'';
  $('simpleVideoMode').value=p.video_mode||'AUTO';$('simpleTransition').value=p.transition_preset||'chaos_mix';$('simpleSceneMode').value=p.scene_mode||'GYM';$('simpleSceneMix').value=(p.scene_mix||['GYM','BEACH']).join(',');toggleSimpleSceneMix();$('simpleFacePath').value='';
  const face=p.persona_ready?'Ảnh mặt READY':p.has_face?'Ảnh mặt đã lưu · chưa PREP':'chưa có ảnh mặt';
  $('simpleSaveStatus').innerHTML=`<span class="good"><b>${esc(p.profile_name||p.profile_id)}</b> · ${esc(simpleModeLabel(p.video_mode))} · ${esc(sceneModeLabel(p.scene_mode,p.scene_mix))} · ${esc(simpleTransitionLabel(p.transition_preset))} · ${esc(face)}</span>`;
}
async function loadSimplePages(){
  try{
    simpleRows=await api('/api/simple/pages');
    const sel=$('simplePage'),current=sel?.value||'';
    if(sel){
      sel.innerHTML=simpleRows.length?simpleRows.map(x=>`<option value="${esc(x.page_id)}">${esc(x.page_name)} — ${esc(x.page_id)}</option>`).join(''):'<option value="">— Chưa có Page —</option>';
      if(current&&simpleRows.some(x=>String(x.page_id)===String(current)))sel.value=current;
    }
    syncSimpleProfileSelect();
    renderSimplePageCards();
  }catch(e){if($('simplePageCards'))$('simplePageCards').innerHTML=`<span class="bad">${esc(e.message)}</span>`}
}
function renderSimplePageCards(){
  const box=$('simplePageCards');if(!box)return;
  if(!simpleRows.length){box.innerHTML='<div class="hint">Chưa có Page. Dán token phía trên rồi IMPORT TOKEN PAGE.</div>';return}
  box.innerHTML=simpleRows.map(x=>{
    const profiles=x.profiles||[];
    const rows=profiles.length?profiles.map(p=>{
      const configured=!!p.config_saved,ready=!!p.persona_ready,hasFace=!!p.has_face,on=!!p.scheduler_enabled,pending=!!p.start_pending;
      const state=on?'<span class="good"><b>ĐANG CHẠY</b></span>':pending?'<span class="warn"><b>ĐANG PREP PERSONA</b></span>':'<span class="hint"><b>ĐANG DỪNG</b></span>';
      const canStart=configured&&hasFace&&!on&&!pending;
      return `<div class="simple-profile-row">
        <div class="simple-profile-name">${esc(p.profile_name||p.profile_id)}</div>
        <div class="page-meta">${state} · ${esc(simpleModeLabel(p.video_mode))} · <b>${esc(sceneModeLabel(p.scene_mode,p.scene_mix))}</b> · ${esc(simpleTransitionLabel(p.transition_preset))}<br>${ready?'Persona READY':hasFace?'FRONT đã lưu · chưa prep':'CHƯA CÓ FRONT'}${on?` · Next ${esc(simpleFmtTime(p.next_publish_at))}`:''}</div>
        <div class="toolbar">
          <button class="btn green" ${canStart?'':'disabled'} onclick="simpleStartPage('${esc(p.profile_id)}','${esc(p.profile_name||x.page_name)}')">${pending?'ĐANG PREP':'START'}</button>
          <button class="btn red" ${on||pending?'':'disabled'} onclick="simpleStopPage('${esc(p.profile_id)}')">STOP</button>
          <button class="btn secondary" onclick="openProfileAuto('${esc(p.profile_id)}')">AUTO</button>
          <button class="btn secondary" onclick="cloneProfile('${esc(p.profile_id)}')">NHÂN</button>
          <button class="btn red" onclick="deleteProfileKeepPage('${esc(p.profile_id)}','${esc(p.profile_name||p.profile_id)}')">XÓA HỒ SƠ</button>
        </div>
      </div>`;
    }).join(''):'<div class="hint">Page chưa có hồ sơ. Chọn Page phía trên → TẠO HỒ SƠ MỚI.</div>';
    return `<div class="simple-page-card"><div class="page-name">${esc(x.page_name)} <span class="pill">${profiles.length} hồ sơ</span></div>${rows}</div>`;
  }).join('');
}
async function simpleSavePage(){
  const pageId=$('simplePage')?.value||'';if(!pageId){toast('Chọn Page trước.','warn');return}
  const selected=$('simpleProfile')?.value||'__new__';
  return guardedAction('simple-save:'+pageId+':'+selected,'Lưu hồ sơ',async()=>{
    const b=$('simpleSaveBtn');if(b)b.disabled=true;
    try{
      const face=await uploadInput('simpleFaceFile','simpleFacePath');
      const payload={
        facebook_page_id:pageId,
        profile_id:selected==='__new__'?null:selected,
        profile_name:($('simpleProfileName')?.value||'').trim()||null,
        persona_path:face,
        video_mode:$('simpleVideoMode').value,
        transition_preset:$('simpleTransition').value,
        scene_mode:$('simpleSceneMode').value||'GYM',
        scene_mix:parseSceneMix('simpleSceneMix')
      };
      const d=await api('/api/simple/pages/save',{method:'POST',body:JSON.stringify(payload),silentSuccess:true});
      $('simpleSaveStatus').innerHTML=`<span class="good"><b>ĐÃ LƯU HỒ SƠ</b> · ${esc(d.profile.name)} · ${esc(simpleModeLabel(d.profile.default_video_mode))}</span>`;
      if($('simpleFaceFile'))$('simpleFaceFile').value='';$('simpleFacePath').value='';
      toast('Đã lưu hồ sơ. Facebook Page vẫn giữ nguyên.','success',4500);
      await loadSimplePages();syncSimpleProfileSelect(d.profile.id);await loadProfiles();
    }catch(e){$('simpleSaveStatus').innerHTML=`<span class="bad">${esc(e.message)}</span>`;toast(e.message,'error',6000)}finally{if(b)b.disabled=false}
  })
}
async function simpleStartPage(profileId,pageName){
  if(!profileId)return;
  if(!confirm(`START hồ sơ ${pageName}?\n\nTự hoàn thiện Persona 3/3 rồi chạy lịch của riêng hồ sơ này.`))return;
  return guardedAction('simple-start:'+profileId,'START hồ sơ',async()=>{
    try{const d=await api('/api/simple/pages/'+encodeURIComponent(profileId)+'/start',{method:'POST',body:'{}',silentSuccess:true});toast(d.start_pending?'Đang hoàn thiện Persona.':'Hồ sơ đã START.',d.start_pending?'info':'success',5000);await loadSimplePages();await loadProfiles()}catch(e){toast(e.message,'error',6000)}
  })
}
async function simpleStopPage(profileId){
  if(!profileId)return;
  return guardedAction('simple-stop:'+profileId,'STOP hồ sơ',async()=>{
    try{await api('/api/simple/pages/'+encodeURIComponent(profileId)+'/stop',{method:'POST',body:'{}',silentSuccess:true});toast('Hồ sơ đã STOP / hủy START đang chờ.','success');await loadSimplePages();await loadProfiles()}catch(e){toast(e.message,'error',6000)}
  })
}
async function cloneProfile(profileId){
  if(!profileId)return;
  if(!confirm('Nhân hồ sơ này?\n\nBẮT BUỘC CLEAR: FRONT, Face Crop, Bust, LEFT, RIGHT, BACK và outfit image refs.\nFacebook Page + prompt + lịch + nhạc được giữ.'))return;
  try{
    const d=await api('/api/page-profiles/'+encodeURIComponent(profileId)+'/clone',{method:'POST',body:'{}'});
    toast('Đã nhân hồ sơ · toàn bộ ảnh Persona đã CLEAR.','success',5000);
    await loadSimplePages();await loadProfiles();
    if(d.profile?.id)openProfileAuto(d.profile.id);
  }catch(e){toast(e.message,'error',6000)}
}
async function deleteProfileKeepPage(profileId,name){
  if(!profileId)return;
  if(!confirm(`XÓA HỒ SƠ "${name}"?\n\nFacebook Page/token KHÔNG bị xóa.`))return;
  try{await api('/api/page-profiles/'+encodeURIComponent(profileId),{method:'DELETE'});toast('Đã xóa hồ sơ. Page vẫn giữ nguyên.','success');await loadSimplePages();await loadProfiles()}catch(e){toast(e.message,'error',6000)}
}
function openProfileAuto(profileId){
  go('autofactory');
  setTimeout(async()=>{await loadProfiles();if($('fvProfile'))$('fvProfile').value=profileId;showSettingsProfileSummary(profileId);await openSettingsEditor()},100);
}
if($('simplePage'))$('simplePage').addEventListener('change',()=>syncSimpleProfileSelect());
if($('simpleProfile'))$('simpleProfile').addEventListener('change',applySimpleProfileSelection);
if($('simpleSceneMode'))$('simpleSceneMode').addEventListener('change',toggleSimpleSceneMix);


function setLogMode(mode){logMode=mode==='full'?'full':'short';$('logShortBtn')?.classList.toggle('secondary',logMode!=='short');$('logFullBtn')?.classList.toggle('secondary',logMode!=='full');loadLogs()}
async function loadLogs(){try{logRows=await api(`/api/logs?mode=${encodeURIComponent(logMode)}&limit=${+$('logLimit').value||300}`);renderLogs()}catch(e){$('logsView').innerHTML=`<span class="bad">${esc(e.message)}</span>`}}
function renderLogs(){const q=($('logFilter')?.value||'').trim().toLowerCase();const rows=(logRows||[]).filter(x=>!q||JSON.stringify(x).toLowerCase().includes(q));$('logSummary').textContent=`${rows.length}/${logRows.length} dòng · ${logMode==='full'?'FULL PAYLOAD':'NGẮN GỌN'}`;if(logMode==='full'){$('logsView').innerHTML=rows.map(x=>`<div class="profile-card"><div><b>${esc(x.ts)}</b> · ${esc(x.type)} · <span class="mono">${esc(x.job_id||'')}</span></div><pre class="mono">${esc(JSON.stringify(x.payload,null,2))}</pre></div>`).join('')||'<div class="hint">Chưa có log.</div>'}else{$('logsView').innerHTML=`<table><thead><tr><th>Time</th><th>Type</th><th>Job</th><th>Nội dung</th></tr></thead><tbody>${rows.map(x=>`<tr><td class="mono">${esc(x.ts)}</td><td>${esc(x.type)}</td><td class="mono">${esc(x.job_id||'-')}</td><td>${esc(x.message||'')}</td></tr>`).join('')}</tbody></table>`}}
async function clearLogs(){if(!confirm('Xóa log đã lưu?'))return;await api('/api/logs',{method:'DELETE'});logRows=[];renderLogs()}

function renderPersonaPreview(profile){
  const box=$('personaPreview'); if(!box) return;
  const assets=profile?.persona_assets||{};
  const frontItems=[['original','Original'],['face_crop','Face Crop 1024'],['master_2048','FRONT Master 2048'],['bust_2048','Bust 2048']].filter(([k])=>assets[k]?.url);
  const front=frontItems.map(([k,label])=>`<div class="asset"><img src="${esc(assets[k].url)}?t=${Date.now()}" alt="${esc(label)}"><div class="meta"><b>${esc(label)}</b>${k==='master_2048'?'<div class="toolbar"><button class="btn secondary" onclick="preparePersonaProfile()">REBUILD FRONT MASTER</button></div>':''}</div></div>`).join('');
  const labels={left:'LEFT 3/4',right:'RIGHT 3/4',back:'BACK HAIR'};
  const cards=['left','right','back'].map(angle=>{
    const slot=profile?.persona_angle_slots?.[angle]||{};
    const running=profile?.persona_active_jobs?.[angle];
    const img=slot.url?`<img src="${esc(slot.url)}?t=${Date.now()}" alt="${labels[angle]}">`:`<div style="height:280px;display:flex;align-items:center;justify-content:center" class="hint">CHƯA CÓ ${labels[angle]}</div>`;
    const useBtn=slot.ready?`<button class="btn ${slot.enabled?'secondary':'green'}" onclick="togglePersonaAngle('${angle}',${slot.enabled?'false':'true'})">${slot.enabled?'BỎ DÙNG':'DÙNG'}</button>`:'';
    const genBtn=`<button class="btn purple" onclick="generatePersonaAngle('${angle}',${slot.ready?'true':'false'})">${slot.ready?'GEN LẠI':'GEN GÓC'}</button>`;
    const delBtn=slot.ready?`<button class="btn red" onclick="deletePersonaAngle('${angle}')">XÓA</button>`:'';
    const status=running?`<span class="pill ${esc(running.status)}">${esc(running.status)}</span>`:(slot.ready?(slot.enabled?'<span class="good">ĐANG DÙNG</span>':'<span class="bad">ĐÃ BỎ DÙNG</span>'):'<span class="hint">missing</span>');
    return `<div class="asset">${img}<div class="meta"><b>${labels[angle]}</b><div style="margin:6px 0">${status}</div><div class="toolbar">${useBtn}${genBtn}${delBtn}</div></div></div>`;
  }).join('');
  box.innerHTML=(front||cards)?front+cards:'<div class="hint">Chưa có Persona. Upload FRONT rồi SAVE PROFILE.</div>';
}


let settingsProfiles=[];
let settingsFbPages=[];
async function loadProfiles(){
  try{
    const current=$('fvProfile')?.value||'';
    try{await api('/api/simple/pages')}catch{}
    const [profiles,pages]=await Promise.all([api('/api/page-profiles'),api('/api/facebook/pages')]);
    settingsProfiles=profiles||[];
    settingsFbPages=pages||[];
    const opts=settingsProfiles.map(p=>{const pg=settingsFbPages.find(x=>String(x.id)===String(p.facebook_page_id));return `<option value="${esc(p.id)}">${esc(pg?.name||'Chưa map')} → ${esc(p.name)}</option>`}).join('');
    $('fvProfile').innerHTML=opts||'<option value="">— Chưa có hồ sơ —</option>';
    if(current&&settingsProfiles.some(p=>p.id===current))$('fvProfile').value=current;
    else if(settingsProfiles.length)$('fvProfile').value=settingsProfiles[0].id;
    $('pfFacebook').innerHTML='<option value="">— Chưa map —</option>'+pages.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    showSettingsProfileSummary($('fvProfile')?.value||'');
    await loadSchedulerStatus();
  }catch(e){
    if($('settingsProfileSummary'))$('settingsProfileSummary').innerHTML=`<span class="bad">${esc(e.message)}</span>`;
  }
}
function settingsProfileFromCache(id){
  return (settingsProfiles||[]).find(p=>String(p.id)===String(id))||null;
}
function settingsStatusLabel(p){
  if(!p)return '-';
  if(p.scheduler_enabled)return '<span class="good">ĐANG CHẠY</span>';
  return '<span class="hint">ĐANG DỪNG</span>';
}
function opsPublishLabel(dry){
  return dry?'CHẠY THỬ · KHÔNG POST FB':'ĐĂNG THẬT LÊN FACEBOOK';
}
function opsDate(v){
  if(!v)return '—';
  try{return new Intl.DateTimeFormat('vi-VN',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v))}catch{return String(v)}
}
function showSettingsProfileSummary(id){
  const box=$('settingsProfileSummary');if(!box)return;
  const p=settingsProfileFromCache(id);
  if(!p){box.innerHTML='<span class="hint">Chưa chọn hồ sơ.</span>';return}

  const ws=(profileWorkspaceCache&&String(profileWorkspaceCache.profile_id||'')===String(id))?(profileWorkspaceCache.scheduler||{}):{};
  const cfgObj=p.scheduler_config||{};
  const dry=(ws.dry_run!==undefined)?!!ws.dry_run:!!p.scheduler_dry_run;
  const schedulerOn=(ws.enabled!==undefined)?!!ws.enabled:!!p.scheduler_enabled;
  const face=p.persona_ready?'SẴN SÀNG':p.persona_path?'ĐÃ LƯU · CHƯA PREP':'CHƯA CÓ';
  const angles=Number(p.persona_angle_count??(
    (p.persona_assets?.left_2048?.url?1:0)+(p.persona_assets?.right_2048?.url?1:0)+(p.persona_assets?.back_2048?.url?1:0)
  ));
  const fbPage=(settingsFbPages||[]).find(x=>String(x.id)===String(p.facebook_page_id||''));
  const fb=p.facebook_page_id?(fbPage?`${fbPage.name} · ${p.facebook_page_id}`:`Page ${p.facebook_page_id}`):'CHƯA MAP';
  const contentMode=simpleModeLabel(p.default_video_mode||cfgObj.mode||'AUTO');
  const transition=simpleTransitionLabel(cfgObj.beat_motion_preset||'chaos_mix');
  const sceneMode=String(cfgObj.scene_mode||'GYM').toUpperCase();
  const sceneMix=Array.isArray(cfgObj.scene_mix)?cfgObj.scene_mix:[];
  const sceneLabel=sceneModeLabel(sceneMode,sceneMix);
  const beatCount=Number(cfgObj.beat_image_count||10);
  const beatDuration=Number(cfgObj.beat_duration_sec||15);
  const clipCount=Number(cfgObj.i2v_clip_count||3);
  const clipDuration=String(cfgObj.i2v_clip_duration||'8s');
  const musicCount=(p.music_paths||[]).length;
  const scheduleMode=String(ws.scheduler_mode||p.scheduler_mode||cfgObj.scheduler_mode||'DAILY_SLOTS').toUpperCase();
  const slots=(ws.daily_slots||p.daily_slots||cfgObj.daily_slots||['08:00','14:00','21:00']);
  const interval=Number(ws.interval_minutes||p.publish_interval_minutes||180);
  const sched=scheduleMode==='DAILY_SLOTS'?`Mốc ${slots.join(' / ')}`:`Mỗi ${interval} phút`;
  const randomMin=Number(ws.daily_random_minutes??p.daily_random_minutes??cfgObj.daily_random_minutes??30);
  const resumeMin=Number(ws.resume_random_minutes??p.resume_random_minutes??cfgObj.resume_random_minutes??30);
  const target=Number(ws.buffer_target??p.buffer_target??2);
  const ready=Number(ws.ready||0),gen=Number(ws.generating||0),publishing=Number(ws.publishing||0),failed=Number(ws.failed||0),published=Number(ws.published||0);
  const active=Number(ws.buffer_active??(ready+gen+publishing));
  const next=ws.next_publish_at||p.next_publish_at||null;
  const last=ws.last_publish_at||p.last_publish_at||null;
  const blocked=!!ws.generation_blocked;
  const personaStatus=angles>=3&&p.persona_ready?'READY 3/3':`${angles}/3${p.persona_ready?' · FRONT READY':''}`;

  const publishBadge=dry
    ?'<span class="ops-badge dry">🧪 CHẠY THỬ · KHÔNG POST FB</span>'
    :'<span class="ops-badge real">🔴 ĐĂNG THẬT FACEBOOK</span>';
  const schedBadge=schedulerOn?'<span class="ops-badge on">▶ SCHEDULER ON</span>':'<span class="ops-badge off">■ SCHEDULER OFF</span>';
  const fbBadge=p.facebook_page_id?'<span class="ops-badge fbok">f FACEBOOK READY</span>':'<span class="ops-badge fbbad">! CHƯA MAP FACEBOOK</span>';

  box.innerHTML=`
    <div class="ops-summary-head">
      <div>
        <div class="ops-title">${esc(p.name)}</div>
        <div style="margin-top:6px">${settingsStatusLabel(p)}</div>
      </div>
      <div class="ops-badges">${publishBadge}${schedBadge}${fbBadge}</div>
    </div>

    <div class="ops-dashboard-grid">
      <div class="ops-metric ${dry?'critical-dry':'critical-real'}">
        <div class="k">Facebook khi chạy</div>
        <div class="v">${esc(opsPublishLabel(dry))}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Facebook Page</div>
        <div class="v">${esc(fb)}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Chế độ nội dung</div>
        <div class="v">${esc(contentMode)}${String(p.default_video_mode||'AUTO').toUpperCase()==='AUTO'?'<br><span class="hint">random: ẢNH / ẢNH+VIDEO / ẢNH→VIDEO</span>':''}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Phong cảnh</div>
        <div class="v">${esc(sceneLabel)}${sceneMode==='GYM'?'<br><span class="good">LOCK GYM · không nhảy ra phố</span>':''}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Chuyển cảnh</div>
        <div class="v">${esc(transition)}</div>
      </div>

      <div class="ops-metric">
        <div class="k">Video ảnh</div>
        <div class="v">${beatDuration}s · ${beatCount} ảnh</div>
      </div>
      <div class="ops-metric">
        <div class="k">Ảnh + Video / I2V</div>
        <div class="v">${clipCount} clip · ${esc(clipDuration)}/clip</div>
      </div>
      <div class="ops-metric ${musicCount?'okbox':'warnbox'}">
        <div class="k">Nhạc</div>
        <div class="v">${musicCount?`${musicCount} track · AUTO RANDOM`:'AUTO MUSIC · TỰ TẠO HARD BEAT'}${!musicCount?'<br><span class="hint">job đầu tự sinh 3 track · không cần upload</span>':''}</div>
      </div>
      <div class="ops-metric ${angles>=3?'okbox':'warnbox'}">
        <div class="k">Persona</div>
        <div class="v">${esc(face)} · ${esc(personaStatus)}</div>
      </div>

      <div class="ops-metric">
        <div class="k">Lịch đăng</div>
        <div class="v">${esc(sched)}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Random lịch</div>
        <div class="v">±${randomMin} phút · resume 0→${resumeMin} phút</div>
      </div>
      <div class="ops-metric ${blocked||failed?'warnbox':''}">
        <div class="k">Buffer hiện tại / mục tiêu</div>
        <div class="v">${active} / ${target}
          <div class="ops-queue" style="margin-top:6px">
            <span class="ops-q good">READY ${ready}</span>
            <span class="ops-q">GEN ${gen}</span>
            <span class="ops-q">POST ${publishing}</span>
            ${failed?`<span class="ops-q bad">LỖI CHỜ CÁCH LY ${failed}</span>`:''}
          ${Number(ws.recent_auto_failures||0)?`<span class="ops-q warn">RECOVERY ${Number(ws.recent_auto_failures||0)}</span>`:''}
          </div>
        </div>
      </div>
      <div class="ops-metric">
        <div class="k">Đã đăng / xử lý</div>
        <div class="v">${published} bài${blocked?`<br><span class="warn">AUTO COOLDOWN ${Math.ceil(Number(ws.auto_recovery_cooldown_seconds||0)/60)} phút · tự chạy lại</span>`:'<br><span class="good">AUTO tiếp tục khi job lỗi</span>'}</div>
      </div>

      <div class="ops-metric">
        <div class="k">Đăng kế tiếp</div>
        <div class="v">${schedulerOn?esc(opsDate(next)):'SCHEDULER OFF'}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Lần đăng gần nhất</div>
        <div class="v">${esc(opsDate(last))}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Ảnh mặt</div>
        <div class="v">${esc(face)}</div>
      </div>
      <div class="ops-metric">
        <div class="k">Góc Persona</div>
        <div class="v">${angles}/3 · ${angles>=3?'ĐỦ':'CHƯA ĐỦ'}</div>
      </div>
    </div>`;

  if($('settingsEditorTitle'))$('settingsEditorTitle').textContent=`Chỉnh cấu hình · ${p.name}`;
  if($('settingsFbSyncHint'))$('settingsFbSyncHint').innerHTML=p.facebook_page_id
    ?`<span class="good">FACEBOOK ĐÃ ĐỒNG BỘ${fbPage?` · ${esc(fbPage.name)}`:''} · ${dry?'CHẠY THỬ, KHÔNG POST':'ĐĂNG THẬT'}</span>`
    :'<span class="warn">CHƯA MAP FACEBOOK · mở Pages hoặc import lại token để tự repair</span>';
}

async function openSettingsEditor(){
  const id=$('fvProfile')?.value||'';
  if(!id){toast('Chọn hồ sơ trước.','warn');return}
  const panel=$('settingsEditorPanel');if(panel)panel.style.display='';
  await editProfile(id);
  await loadSchedulerStatus();
  await loadProfileWorkspace(id);
  panel?.scrollIntoView({behavior:'smooth',block:'start'});
}
function closeSettingsEditor(){
  const panel=$('settingsEditorPanel');if(panel)panel.style.display='none';
}

async function editProfile(id){const p=await api('/api/page-profiles/'+encodeURIComponent(id));$('pfId').value=p.id;$('pfName').value=p.name;$('pfTheme').value=p.theme||'';$('pfPersona').value=p.persona_path||'';$('pfBody').value=p.body_preset||'curvy_fit';$('pfSexy').value=p.sexiness_level??60;$('pfOutfits').value=(p.outfit_prompts||[]).join('\n');$('pfOutfitPaths').value=(p.outfit_paths||[]).join('\n');$('pfBackgrounds').value=(p.backgrounds||[]).join('\n');$('pfPoses').value=(p.poses||[]).join('\n');$('pfMusic').value=(p.music_paths||[]).join('\n');$('pfMode').value=p.default_video_mode||'AUTO';if($('pfSceneMode'))$('pfSceneMode').value=p.scheduler_config?.scene_mode||'GYM';if($('pfSceneMix'))$('pfSceneMix').value=(p.scheduler_config?.scene_mix||['GYM','BEACH']).join(',');$('pfImageModel').value=p.image_model||'Nano Banana 2';$('pfVideoModel').value=p.video_model||'Veo 3.1 - Fast';$('pfFacebook').value=p.facebook_page_id||'';$('pfEnabled').value=String(!!p.enabled);$('pfTitleHint').value=p.title_hint||'';$('pfCaptionStyle').value=p.caption_style||'engaging_short';$('pfAiModel').value=p.ai_model||'';uploadMeta.delete('pfPersonFile');if($('pfPersonFile'))$('pfPersonFile').value='';if($('pfImportStatus')){$('pfImportStatus').innerHTML=p.persona_path?(p.persona_ready?'<span class="good"><b>ẢNH FRONT LOCAL OK</b> · Master 2048 sẵn sàng · Flow sẽ kiểm tra attach trước khi Create</span>':'<span class="warn"><b>ẢNH FRONT ĐÃ LƯU</b> · chưa có Master 2048</span>'):'<span class="bad"><b>CHƯA CÓ ẢNH FRONT</b> · cần tải ảnh mặt tham chiếu</span>';}renderPersonaPreview(p);pollPersonaPack(id);loadMusicLibrary(id);if($('settingsEditorTitle'))$('settingsEditorTitle').textContent=`Chỉnh cấu hình · ${p.name}`;}
function clearProfileForm(){
  $('pfId').value='';$('pfName').value='Hồ sơ '+Math.floor(Math.random()*900+100);
  $('pfPersona').value='';if($('pfPersonFile'))$('pfPersonFile').value='';uploadMeta.delete('pfPersonFile');
  $('pfOutfitPaths').value='';$('profileResult').textContent='';$('pfAiModel').value='';
  $('pfTheme').value='adult glamour lifestyle in Vietnam';$('pfTitleHint').value='Phong cách Việt Nam cuốn hút mỗi ngày';
  if($('pfImportStatus'))$('pfImportStatus').innerHTML='<span class="warn">Hồ sơ mới · bắt buộc import FRONT.</span>';
  renderPersonaPreview(null)
}

async function saveProfile(){const b=$('saveProfileBtn');b.disabled=true;try{const person=await uploadInput('pfPersonFile','pfPersona');if($('pfPersonFile')?.files?.[0]){const m=uploadMeta.get('pfPersonFile');if(!m?.image_valid)throw new Error('Ảnh FRONT chưa được server xác minh thành công.');}const payload={id:$('pfId').value||null,name:$('pfName').value,theme:$('pfTheme').value,persona_path:person,body_preset:$('pfBody').value,sexiness_level:+$('pfSexy').value,outfit_prompts:lines('pfOutfits'),outfit_paths:lines('pfOutfitPaths'),backgrounds:lines('pfBackgrounds'),poses:lines('pfPoses'),music_paths:lines('pfMusic'),default_video_mode:$('pfMode').value,image_model:$('pfImageModel').value,video_model:$('pfVideoModel').value,facebook_page_id:$('pfFacebook').value||null,title_hint:$('pfTitleHint').value||'',caption_style:$('pfCaptionStyle').value||'engaging_short',ai_model:$('pfAiModel').value||'',ai_provider:'router9',enabled:$('pfEnabled').value==='true'};const d=await api('/api/page-profiles',{method:'POST',body:JSON.stringify(payload)});$('pfId').value=d.profile.id;await api('/api/simple/pages/'+encodeURIComponent(d.profile.id)+'/settings',{method:'POST',body:JSON.stringify({scene_mode:$('pfSceneMode')?.value||'GYM',scene_mix:parseSceneMix('pfSceneMix')}),silentSuccess:true});$('profileResult').innerHTML=`<span class="good">ĐÃ LƯU ${esc(d.profile.name)} · ${d.profile.persona_ready?'FRONT MASTER READY':'persona saved'} · ảnh FRONT đã kiểm tra</span>`;if($('pfImportStatus'))$('pfImportStatus').innerHTML=`<span class="good"><b>IMPORT LOCAL OK</b> · ${d.profile.persona_ready?'Master 2048 sẵn sàng':'đã lưu ảnh gốc'}</span>`;renderPersonaPreview(d.profile);await loadProfiles();$('fvProfile').value=d.profile.id;showSettingsProfileSummary(d.profile.id);if($('settingsEditorPanel'))$('settingsEditorPanel').style.display='';await pollPersonaPack(d.profile.id,false)}catch(e){$('profileResult').innerHTML=`<span class="bad">${esc(e.message)}</span>`}finally{b.disabled=false}}
async function preparePersonaProfile(){const id=($('pfId').value||'').trim();if(!id){toast('Hãy LƯU HỒ SƠ trước.','warn');return}return guardedAction('prepare:'+id,'Tạo lại FRONT chuẩn',async()=>{try{const p=await api('/api/page-profiles/'+encodeURIComponent(id));if(!p.persona_source_exists){toast('Không có ảnh FRONT gốc để tạo lại. Hãy tải lại FRONT rồi LƯU HỒ SƠ.','warn',5200);return}$('profileResult').textContent='Đang tạo lại FRONT chuẩn...';const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/prepare-persona',{method:'POST',body:'{}'});$('profileResult').innerHTML=`<span class="good">FRONT MASTER 2048 đã sẵn sàng</span>`;renderPersonaPreview(d.profile);await loadProfiles();toast('Tạo lại FRONT chuẩn thành công.','success')}catch(e){$('profileResult').innerHTML=`<span class="bad">${esc(e.message)}</span>`}})}
async function preparePersonaById(id){return guardedAction('prepare:'+id,'Tạo lại FRONT chuẩn',async()=>{try{const p=await api('/api/page-profiles/'+encodeURIComponent(id));if(!p.persona_source_exists){toast('Hồ sơ này không còn ảnh FRONT gốc. Hãy EDIT và tải lại FRONT trước.','warn',5000);return}const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/prepare-persona',{method:'POST',body:'{}'});if(($('pfId').value||'')===id)renderPersonaPreview(d.profile);await loadProfiles();toast('FRONT chuẩn đã tạo lại: '+d.profile.name,'success')}catch(e){toast(e.message,'error',5000)}})}
async function generatePersonaAngle(angle,force=false){const id=($('pfId').value||'').trim();if(!id){toast('Hãy LƯU HỒ SƠ trước.','warn');return}return guardedAction('angle:'+id+':'+angle,'Tạo góc '+angle.toUpperCase(),async()=>{try{const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/angles/'+encodeURIComponent(angle)+'/generate?force='+(force?'true':'false'),{method:'POST',body:'{}',silentSuccess:true});if(d.already_running){toast(angle.toUpperCase()+' đang chạy rồi — không tạo job trùng.','warn',4200);return}if(d.already_ready&&!force){toast(angle.toUpperCase()+' đã có sẵn. Không tạo lại.','warn');return}toast('Đã gửi tạo góc '+angle.toUpperCase()+'.','success');await pollPersonaPack(id,true)}catch(e){toast(e.message,'error',5000)}})}
async function generateMissingPersonaAngles(){const id=($('pfId').value||'').trim();if(!id){toast('Hãy LƯU HỒ SƠ trước.','warn');return}return guardedAction('angles-missing:'+id,'Tạo các góc còn thiếu',async()=>{try{const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/angles/generate-missing',{method:'POST',body:'{}',silentSuccess:true});const created=(d.jobs||[]).filter(x=>!x.already_running&&!x.already_ready).length;const running=(d.jobs||[]).filter(x=>x.already_running).length;const ready=(d.jobs||[]).filter(x=>x.already_ready).length;$('personaAngleStatus').innerHTML=`Tạo mới ${created}${running?` · ${running} đang chạy`:''}${ready?` · ${ready} đã có`:''}`;if(created===0&&running>0)toast('Các góc cần tạo đang chạy rồi — không tạo job trùng.','warn');else if(created===0)toast('Persona Pack đã đủ, không cần tạo thêm.','info');else toast(`Đã queue ${created} góc còn thiếu.`,'success');await pollPersonaPack(id,true)}catch(e){$('personaAngleStatus').innerHTML=`<span class="bad">${esc(e.message)}</span>`}})}
async function generatePersonaAngles(force=false,silent=false){if(force){if(!silent)alert('REGEN 3 GÓC đã tắt để tránh spam. Dùng GEN LẠI trên từng card LEFT/RIGHT/BACK.');return}return generateMissingPersonaAngles()}
async function deletePersonaAngle(angle){const id=($('pfId').value||'').trim();if(!id)return;if(!confirm('Xóa góc '+angle.toUpperCase()+'? Sau đó có thể GEN GÓC lại.'))return;try{const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/angles/'+encodeURIComponent(angle),{method:'DELETE'});renderPersonaPreview(d.profile);await pollPersonaPack(id,false);await loadProfiles()}catch(e){alert(e.message)}}
async function togglePersonaAngle(angle,enabled){const id=($('pfId').value||'').trim();if(!id)return;try{const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/angles/'+encodeURIComponent(angle)+'/use?enabled='+(enabled?'true':'false'),{method:'POST',body:'{}'});renderPersonaPreview(d.profile);await loadProfiles()}catch(e){alert(e.message)}}
async function pollPersonaPack(id,repeat=true){try{const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/persona-pack-status');const p=d.profile;p.persona_active_jobs=d.active_jobs||{};const activeCount=Object.keys(d.active_jobs||{}).length;if($('personaAngleStatus'))$('personaAngleStatus').innerHTML=`Có ${p.persona_angle_count||0}/3 góc · đang dùng ${p.persona_angle_enabled_count||0}/3${activeCount?` · ${activeCount} job đang chạy`:''} · Backend ${esc(d.server_version||'?')}`;renderPersonaPreview(p);if(repeat&&activeCount)setTimeout(()=>pollPersonaPack(id,true),2500);await loadProfiles()}catch(e){if($('personaAngleStatus'))$('personaAngleStatus').innerHTML=`<span class="bad">${esc(e.message)}</span>`}}

async function deleteProfile(id){return deleteProfileKeepPage(id,id)}
async function cloneCurrentProfile(){const id=($('pfId')?.value||'').trim();if(!id){toast('Chọn hồ sơ trước.','warn');return}return cloneProfile(id)}
async function deleteCurrentProfile(){const id=($('pfId')?.value||'').trim();if(!id)return;return deleteProfileKeepPage(id,$('pfName')?.value||id)}


async function applyVietnamPreset(){const id=($('pfId')?.value||'').trim();if(!id){toast('Lưu hồ sơ trước rồi nạp preset Việt Nam.','warn');return}if(!confirm('Nạp preset lifestyle Việt Nam cho hồ sơ này? Outfit/background/pose hiện tại sẽ được thay bằng preset mới.'))return;return guardedAction('vietnam-preset:'+id,'Nạp preset Việt Nam',async()=>{const d=await api('/api/page-profiles/'+encodeURIComponent(id)+'/apply-vietnam-preset',{method:'POST',body:'{}',silentSuccess:true});editProfile(id);toast('Đã nạp preset Việt Nam: địa điểm cụ thể + màu trang phục đa dạng.','success',5000)})}

function toggleScheduleFields(){const daily=$('fsMode')?.value==='DAILY_SLOTS';if($('dailyScheduleFields'))$('dailyScheduleFields').style.display=daily?'grid':'none';if($('intervalScheduleFields'))$('intervalScheduleFields').style.display=daily?'none':'grid'}
async function runManualTool(){const v=$('manualTool')?.value||'fill';if(v==='fill')return fillSchedulerNow();if(v==='publish')return publishSchedulerNow();if(v==='download')return testLatestDownloadOnly();if(v==='test')return generateOneTest();if(v==='preview')return previewAiPlan();if(v==='batch')return generateFactoryV2()}
async function refreshDownloadTestStatus(){if(!$('downloadTestStatus'))return;try{const d=await api('/api/download-test/status');if(!d.available){$('downloadTestStatus').innerHTML='<span class="hint">Chưa có video Flow nào để test tải.</span>';return}$('downloadTestStatus').innerHTML=`<span class="good">Có thể TEST TẢI</span> · nguồn <span class="mono">${esc(d.source_job_id)}</span> · local ${esc(d.local_ready)}/${esc(d.expected)} · ${esc(d.source_status||'')}`;}catch(e){$('downloadTestStatus').innerHTML=`<span class="bad">${esc(e.message)}</span>`}}
async function testLatestDownloadOnly(){return guardedAction('download-only-test','Kiểm thử tải video',async()=>{try{const d=await api('/api/download-test/latest',{method:'POST',body:'{}',silentSuccess:true});$('factoryV2Result').innerHTML=`<span class="good">Đang test tải ${esc(d.expected)} clip từ job ${esc(d.source_job_id)}...</span>`;toast(`Đã yêu cầu tải lại ${d.expected} clip của CHÍNH job cũ. Không gen ảnh/video mới, không đăng.`,'success',4500);pollDownloadOnlyTest(d.test_job_id);refreshDownloadTestStatus()}catch(e){toast(e.message,'error',6000)}})}
async function pollDownloadOnlyTest(id){try{const j=await api('/api/flow/jobs/'+encodeURIComponent(id));const assets=(j.assets||[]).filter(x=>x.kind==='video'&&x.scene_id>0);const text=`TEST DOWNLOAD · ${assets.length} clip local · ${j.status}`;$('factoryV2Result').innerHTML=`${pill(j.status)} <span class="mono">${esc(id)}</span> · ${esc(text)}`;if(['downloading','queued','running','dispatching'].includes(j.status))return setTimeout(()=>pollDownloadOnlyTest(id),1200);if(j.status==='done')toast(`TEST DOWNLOAD THÀNH CÔNG · ${assets.length} clip đã về server.`,'success',6000);else toast(j.error||`TEST DOWNLOAD lỗi · ${assets.length} clip local`,'error',7000)}catch(e){toast(e.message,'error',6000)}}
function fmtLocalTime(v){if(!v)return '-';try{return new Date(v).toLocaleString(window.UI_I18N?.getLang?.()==='en'?'en-US':'vi-VN')}catch{return v}}

let simpleSettingsSaveTimer=null;
let simpleSettingsSaving=false;
async function saveSimpleRuntimeSettings(){
  const id=$('fvProfile')?.value||'';
  if(!id||simpleSettingsSaving)return;
  simpleSettingsSaving=true;
  const st=$('simpleSettingsSaveStatus');
  if(st)st.textContent='Đang lưu Cài đặt...';
  try{
    const payload={
      scheduler_mode:$('fsMode')?.value||'DAILY_SLOTS',
      scene_mode:$('pfSceneMode')?.value||'GYM',
      scene_mix:parseSceneMix('pfSceneMix'),
      facebook_dry_run:$('fsDry')?.value!=='false',
      publish_interval_minutes:+($('fsInterval')?.value||180),
      buffer_target:+($('fsBuffer')?.value||2),
      first_publish_delay_minutes:+($('fsFirstDelay')?.value||0),
      daily_slots:String($('fsDailySlots')?.value||'08:00,14:00,21:00').split(/[,;\s]+/).map(x=>x.trim()).filter(Boolean),
      daily_random_minutes:+($('fsDailyRandom')?.value||0),
      resume_random_minutes:+($('fsResumeRandom')?.value||0),
      beat_image_count:+($('fvBeatCount')?.value||7),
      beat_duration_sec:+($('fvBeatDuration')?.value||15),
      i2v_clip_count:+($('fvClipCount')?.value||3),
      i2v_clip_duration:$('fvClipDuration')?.value||'8s'
    };
    await api('/api/simple/pages/'+encodeURIComponent(id)+'/settings',{method:'POST',body:JSON.stringify(payload),silentSuccess:true});
    if(st)st.innerHTML='<span class="good">ĐÃ TỰ LƯU CÀI ĐẶT</span>';
  }catch(e){
    if(st)st.innerHTML='<span class="bad">'+esc(e.message)+'</span>';
  }finally{simpleSettingsSaving=false}
}
function queueSimpleRuntimeSettingsSave(){
  clearTimeout(simpleSettingsSaveTimer);
  simpleSettingsSaveTimer=setTimeout(saveSimpleRuntimeSettings,650);
}

if($('fvBeatDuration'))$('fvBeatDuration').addEventListener('change',()=>{
  const sec=Number($('fvBeatDuration').value||15);
  const suggested=sec>=15?10:sec>=12?9:sec>=10?7:6;
  if($('fvBeatCount'))$('fvBeatCount').value=String(suggested);
});

['fsMode','fsDry','fsInterval','fsBuffer','fsFirstDelay','fsDailySlots','fsDailyRandom','fsResumeRandom','fvBeatCount','fvBeatDuration','fvClipCount','fvClipDuration','pfSceneMode','pfSceneMix'].forEach(id=>{
  const el=$(id); if(el){el.addEventListener('change',queueSimpleRuntimeSettingsSave);el.addEventListener('blur',queueSimpleRuntimeSettingsSave)}
});

function schedulerPayload(){const f=factoryPayload(false);const slots=($('fsDailySlots')?.value||'08:00,14:00,21:00').split(/[,;\s]+/).map(x=>x.trim()).filter(Boolean);return {enabled:true,scheduler_mode:$('fsMode')?.value||'INTERVAL',publish_interval_minutes:+$('fsInterval').value||180,buffer_target:+$('fsBuffer').value||2,facebook_dry_run:$('fsDry').value==='true',first_publish_delay_minutes:+$('fsFirstDelay').value||0,daily_slots:slots,daily_random_minutes:+$('fsDailyRandom').value||0,resume_random_minutes:+$('fsResumeRandom').value||0,mode:f.mode,beat_image_count:f.beat_image_count,beat_duration_sec:f.beat_duration_sec,beat_motion_preset:f.beat_motion_preset,i2v_clip_count:f.i2v_clip_count,i2v_clip_duration:f.i2v_clip_duration,image_concurrency:f.image_concurrency,video_concurrency:f.video_concurrency}}
function renderSchedulerStatus(d){if(!$('fsStatus')||!d)return;const live=d.dry_run?'DRY RUN':'PUBLISH THẬT';const isDaily=d.scheduler_mode==='DAILY_SLOTS';const hold=d.generation_blocked?`<br><span class="bad"><b>ĐANG GIỮ LỖI:</b> ${esc(d.generation_block_reason||'Không tạo video thay thế cho tới khi xử lý job lỗi.')}</span>`:'';const modeText=isDaily?`DAILY SLOTS · ${(d.daily_slots||[]).join(' / ')} · random ±${d.daily_random_minutes||0}m`:`INTERVAL · ${d.interval_minutes} phút/bài`;const plan=(d.daily_plan||[]);const planHtml=isDaily&&plan.length?`<div class="tags" style="margin-top:8px">${plan.map(x=>{const cls=x.state==='published'?'good':x.state==='skipped'?'bad':'';return `<span class="tag ${cls}">${esc(x.slot)} → ${esc(fmtLocalTime(x.catchup_at||x.at))} · ${esc(x.state||'pending')}</span>`}).join('')}</div>`:'';$('fsStatus').innerHTML=`${d.enabled?'<span class="good"><b>SCHEDULER ON</b></span>':'<span class="bad"><b>SCHEDULER OFF</b></span>'} · ${esc(modeText)} · buffer <b>${esc(d.ready)}/${esc(d.buffer_target)}</b> READY · generating ${esc(d.generating)} · publishing ${esc(d.publishing)} · ${esc(live)}<br>Next: <b>${esc(fmtLocalTime(d.next_publish_at))}</b> · Last: ${esc(fmtLocalTime(d.last_publish_at))}${d.warmup?' · <span class="hint">warm-up: chờ đủ buffer trước khi đăng</span>':''}${hold}${planHtml}`;if($('fsMode'))$('fsMode').value=d.scheduler_mode||'INTERVAL';if($('fsInterval'))$('fsInterval').value=d.interval_minutes||180;if($('fsBuffer'))$('fsBuffer').value=d.buffer_target||2;if($('fsDry'))$('fsDry').value=String(!!d.dry_run);if($('fsDailySlots'))$('fsDailySlots').value=(d.daily_slots||['08:00','14:00','21:00']).join(',');if($('fsDailyRandom'))$('fsDailyRandom').value=d.daily_random_minutes??30;if($('fsResumeRandom'))$('fsResumeRandom').value=d.resume_random_minutes??30;toggleScheduleFields();const q=d.queue||[];$('fsQueue').innerHTML=q.length?`<table><thead><tr><th>Queue</th><th>Status</th><th>Video</th><th>Scheduled</th><th>Error</th></tr></thead><tbody>${q.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${pill(x.status)}</td><td class="mono">${esc(x.video_path?x.video_path.split(/[\/]/).pop():'-')}</td><td>${esc(fmtLocalTime(x.scheduled_for))}</td><td class="bad">${esc(x.error||'')}</td></tr>`).join('')}</tbody></table>`:'<div class="hint">Queue chưa có video.</div>'}
async function loadSchedulerStatus(){const id=$('fvProfile')?.value;if(!id||!$('fsStatus'))return;try{renderSchedulerStatus(await api('/api/scheduler/'+encodeURIComponent(id)))}catch(e){$('fsStatus').innerHTML=`<span class="bad">Scheduler: ${esc(e.message)}</span>`}}
async function startPublishScheduler(){const id=$('fvProfile')?.value;if(!id){toast('Chọn Page Profile trước.','warn');return}return guardedAction('scheduler-start:'+id,'Bật lịch đăng',async()=>{const p=schedulerPayload();const desc=p.scheduler_mode==='DAILY_SLOTS'?`Mỗi ngày: ${p.daily_slots.join(' / ')} · random ±${p.daily_random_minutes} phút.\nRestart trễ giờ: catch-up 1 bài trong 0–${p.resume_random_minutes} phút.`:`Mỗi ${p.publish_interval_minutes} phút đăng 1 bài.\nRestart quá hạn: đăng bù trong 0–${p.resume_random_minutes} phút.`;if(!p.facebook_dry_run&&!confirm(`BẬT SCHEDULER ĐĂNG THẬT?\n\n${desc}\nLuôn giữ sẵn ${p.buffer_target} video.\nChỉ đăng khi buffer warm-up đủ.`))return;try{if($('fvAutoPublish'))$('fvAutoPublish').value='false';const d=await api('/api/scheduler/'+encodeURIComponent(id)+'/start',{method:'POST',body:JSON.stringify(p),silentSuccess:true});renderSchedulerStatus(d.status);await loadProfiles();if(d.already_enabled){toast('Lịch của Page này đã bật với đúng cấu hình hiện tại — không chạy lại, không tạo video trùng.','warn',5200);return}toast(`Đã BẬT LỊCH ĐĂNG · ${p.scheduler_mode} · buffer ${p.buffer_target}.`,'success',4500)}catch(e){toast(e.message,'error',5000)}})}
async function stopPublishScheduler(){const id=$('fvProfile')?.value;if(!id)return;return guardedAction('scheduler-stop:'+id,'Dừng lịch đăng',async()=>{try{const d=await api('/api/scheduler/'+encodeURIComponent(id)+'/stop',{method:'POST',body:'{}',silentSuccess:true});renderSchedulerStatus(d.status);await loadProfiles();toast('Đã dừng lịch đăng.','success')}catch(e){toast(e.message,'error',5000)}})}
async function discardFailedSchedulerJobs(){const id=$('fvProfile')?.value;if(!id){toast('Chọn Page Profile trước.','warn');return}if(!confirm('Bỏ qua toàn bộ job lỗi của Page này?\\n\\nChỉ dùng khi bạn KHÔNG muốn cứu lại video cũ. Sau đó scheduler mới được phép tạo bù.'))return;return guardedAction('scheduler-discard-failed:'+id,'Bỏ qua job lỗi',async()=>{try{const d=await api('/api/scheduler/'+encodeURIComponent(id)+'/discard-failed',{method:'POST',body:'{}',silentSuccess:true});renderSchedulerStatus(d.status);toast(`Đã bỏ qua ${d.discarded||0} job lỗi. Scheduler có thể tiếp tục tạo bù.`,'success',5000)}catch(e){toast(e.message,'error',6000)}})}
async function fillSchedulerNow(){const id=$('fvProfile')?.value;if(!id)return;return guardedAction('scheduler-fill:'+id,'Tạo bù',async()=>{try{const d=await api('/api/scheduler/'+encodeURIComponent(id)+'/fill-now',{method:'POST',body:'{}',silentSuccess:true});renderSchedulerStatus(d.status);toast(`Đã kiểm tra buffer · tạo thêm ${d.fill.created||0} video.`,(d.fill.created||0)>0?'success':'info')}catch(e){toast(e.message,'error',5000)}})}
async function publishSchedulerNow(){const id=$('fvProfile')?.value;if(!id)return;if($('fsDry').value==='false'&&!confirm('ĐĂNG NGAY 1 video READY lên Facebook?'))return;return guardedAction('scheduler-publish:'+id,'Đăng 1 bài ngay',async()=>{try{const d=await api('/api/scheduler/'+encodeURIComponent(id)+'/publish-now',{method:'POST',body:'{}',silentSuccess:true});renderSchedulerStatus(d.status);if(!d.publish)toast('Chưa có video READY để đăng.','warn');else toast('Đã gửi 1 video READY sang luồng đăng Facebook.','success')}catch(e){toast(e.message,'error',5000)}})}

function factoryPayload(one=false){return {page_profile_id:$('fvProfile').value,videos:one?1:+$('fvVideos').value,mode:$('fvMode').value,beat_image_count:+$('fvBeatCount').value,beat_duration_sec:+$('fvBeatDuration').value,beat_motion_preset:$('fvBeatPreset').value,i2v_clip_count:+$('fvClipCount').value,i2v_clip_duration:$('fvClipDuration').value,image_concurrency:+$('fvImageConc').value,video_concurrency:+$('fvVideoConc').value,auto_publish:$('fvAutoPublish').value==='true',facebook_dry_run:$('fvDry').value==='true'}}
async function previewAiPlan(){try{const d=await api('/api/ai/plan-preview',{method:'POST',body:JSON.stringify(factoryPayload(true))});$('aiPreview').textContent=JSON.stringify(d,null,2)}catch(e){$('aiPreview').textContent=e.message}}
async function generateFactoryV2(){const id=$('fvProfile').value;if(!id){toast('Tạo/chọn Page Profile trước.','warn');return}if($('fvAutoPublish').value==='true'&&$('fvDry').value==='false'&&!confirm('Bạn đang bật AUTO PUBLISH THẬT. Tiếp tục?'))return;return guardedAction('factory-generate:'+id,'Tạo batch',async()=>{const b=$('generateV2Btn');if(b)b.disabled=true;try{const d=await api('/api/factory/v2/generate',{method:'POST',body:JSON.stringify(factoryPayload(false)),silentSuccess:true});lastFactoryRun=d.run_id;$('factoryV2Result').innerHTML=`Run <span class="mono">${esc(d.run_id)}</span> · ${d.jobs.length} final video queued`;toast(`Đã tạo batch ${d.jobs.length} video.`,'success');pollFactoryRun()}catch(e){$('factoryV2Result').innerHTML=`<span class="bad">${esc(e.message)}</span>`}finally{if(b)b.disabled=false}})}
async function generateOneTest(){if(!$('fvProfile').value)return alert('Tạo/chọn Page Profile trước');try{const p=factoryPayload(true);p.auto_publish=false;p.facebook_dry_run=true;const d=await api('/api/factory/v2/generate',{method:'POST',body:JSON.stringify(p)});lastFactoryRun=d.run_id;$('factoryV2Result').innerHTML=`TEST VIDEO ONLY <span class="mono">${esc(d.run_id)}</span> · mode=${esc(d.jobs[0].mode)}`;pollFactoryRun()}catch(e){alert(e.message)}}
async function autoTestOne(realPublish=false){if(!$('fvProfile').value)return alert('Tạo/chọn Page Profile trước');try{const profile=await api('/api/page-profiles/'+encodeURIComponent($('fvProfile').value));if(!profile.facebook_page_id)return alert('Page Profile chưa map Facebook Page.');await api('/api/facebook/pages/'+encodeURIComponent(profile.facebook_page_id)+'/test',{method:'POST',body:'{}'});if(realPublish&&!confirm(`AUTO 100% + ĐĂNG THẬT lên Facebook Page đã map?\n\n${profile.name}\n\nFlow → Render → QC PASS → Facebook`))return;const p=factoryPayload(true);p.auto_publish=true;p.facebook_dry_run=!realPublish;const d=await api('/api/factory/v2/generate',{method:'POST',body:JSON.stringify(p)});lastFactoryRun=d.run_id;$('factoryV2Result').innerHTML=`<span class="good">AUTO TEST 1 PAGE</span> · ${realPublish?'PUBLISH THẬT':'DRY RUN'} · <span class="mono">${esc(d.run_id)}</span>`;pollFactoryRun();go('autofactory')}catch(e){alert('AUTO TEST dừng trước khi chạy: '+e.message)}}
async function pollFactoryRun(){if(!lastFactoryRun)return;try{const runs=await api('/api/factory/v2/runs?limit=50');const r=runs.find(x=>x.id===lastFactoryRun);if(!r)return;$('factoryV2Result').innerHTML=`Run <span class="mono">${esc(r.id)}</span> · done=${r.done}/${r.requested_count} · failed=${r.failed} · active=${r.active}`;let all=[];for(const j of r.jobs){const a=await api('/api/assets?job_id='+encodeURIComponent(j.id));all.push(...a.filter(x=>x.kind==='final_video'))}$('factoryPreview').innerHTML=assetCards(all);if(r.active>0||r.done+r.failed<r.requested_count)setTimeout(pollFactoryRun,2500)}catch(e){}}
async function loadRuns(){const box=$('runsTable');if(!box)return;try{const rows=await api('/api/factory/v2/runs?limit=100');box.innerHTML=`<table><thead><tr><th>Run</th><th>Profile</th><th>Mode</th><th>Progress</th><th>Jobs</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="mono">${esc(r.id)}</td><td>${esc(r.page_profile_id)}</td><td>${esc(r.requested_mode)}</td><td>${r.done}/${r.requested_count} done · ${r.failed} fail · ${r.active} active</td><td>${r.jobs.map(j=>`${pill(j.status)} <span class="mono">${esc(j.id.slice(-10))}</span>`).join('<br>')}<div class="toolbar"><button class="btn secondary" onclick="previewRun('${esc(r.id)}')">PREVIEW</button></div></td></tr>`).join('')}</tbody></table>`}catch(e){box.textContent=e.message}}
async function previewRun(id){const p=$('fvProfile')?.value||'';if(p)await loadProfileWorkspace(p)}
async function loadJobs(){const box=$('jobsTable');if(!box)return;try{const rows=await api('/api/flow/jobs?limit=120&compact=true');box.innerHTML=`<table><thead><tr><th>Job</th><th>Kind</th><th>Status</th><th>Scenes</th><th>Error</th><th></th></tr></thead><tbody>${rows.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${esc(x.kind)}</td><td>${pill(x.status)}</td><td>${x.scene_count||0}</td><td class="bad">${esc(x.error||'')}</td><td><button class="btn secondary" onclick="retryJob('${esc(x.id)}')">RETRY</button></td></tr>`).join('')}</tbody></table>`}catch(e){box.textContent=e.message}}
async function retryJob(id){await api('/api/flow/jobs/'+encodeURIComponent(id)+'/retry',{method:'POST',body:'{}'});loadJobs()}
async function previewJob(id){const p=$('fvProfile')?.value||'';if(p)await loadProfileWorkspace(p)}
async function loadAssets(){if(!$('assetGrid'))return;try{$('assetGrid').innerHTML=assetCards(await api('/api/assets?limit=300'))}catch(e){$('assetGrid').textContent=e.message}}

async function syncPages(){try{const d=await api('/api/facebook/pages/sync',{method:'POST',body:JSON.stringify({user_access_token:$('fbUserToken').value})});$('fbUserToken').value='';alert(`Đã lưu ${d.saved} Page${d.skipped_ignored?` · bỏ qua ${d.skipped_ignored} Page đã xóa`:''}`);loadPages();loadProfiles()}catch(e){alert(e.message)}}
async function savePage(){try{await api('/api/facebook/pages',{method:'POST',body:JSON.stringify({page_id:$('fbPageId').value,name:$('fbPageName').value,access_token:$('fbPageToken').value})});$('fbPageToken').value='';loadPages();loadProfiles()}catch(e){alert(e.message)}}
async function testPage(id){try{alert(JSON.stringify(await api('/api/facebook/pages/'+encodeURIComponent(id)+'/test',{method:'POST',body:'{}'}),null,2));loadPages()}catch(e){alert(e.message)}}
async function deletePage(id,name){if(!confirm(`Xóa Page "${name}" khỏi server và KHÔNG lưu lại khi bấm SYNC PAGES lần sau?`))return;try{await api('/api/facebook/pages/'+encodeURIComponent(id)+'/delete',{method:'POST',body:'{}'});await loadPages();await loadProfiles()}catch(e){alert(e.message)}}
async function keepOnlyPage(id,name){if(!confirm(`Chỉ GIỮ "${name}" để test? Tất cả Page khác sẽ bị xóa khỏi server và đưa vào danh sách bỏ qua khi Sync.`))return;try{const d=await api('/api/facebook/pages/'+encodeURIComponent(id)+'/keep-only',{method:'POST',body:'{}'});alert(`Đã giữ 1 Page · xóa ${d.removed} Page khác`);await loadPages();await loadProfiles()}catch(e){alert(e.message)}}
async function resetIgnoredPages(){if(!confirm('Cho phép các Page đã xóa xuất hiện lại ở lần SYNC PAGES tiếp theo?'))return;try{const d=await api('/api/facebook/pages/ignored/reset',{method:'DELETE'});alert('Đã bỏ ignore '+d.cleared+' Page. Bấm SYNC PAGES để lấy lại.');}catch(e){alert(e.message)}}
async function loadPages(){try{const p=await api('/api/facebook/pages');if($('pagesTable'))$('pagesTable').innerHTML=`<table><thead><tr><th>Page</th><th>ID</th><th>Tasks</th><th>Test</th><th>Quản lý</th></tr></thead><tbody>${p.map(x=>`<tr><td><b>${esc(x.name)}</b></td><td class="mono">${esc(x.id)}</td><td>${esc((x.tasks||[]).join(', '))}</td><td>${x.last_test?'<span class="good">OK</span>':'-'}</td><td><div class="toolbar"><button class="btn secondary" onclick="testPage('${esc(x.id)}')">TEST TOKEN</button></div></td></tr>`).join('')}</tbody></table>`;if($('pubPage'))$('pubPage').innerHTML=p.map(x=>`<option value="${esc(x.id)}">${esc(x.name)} — ${esc(x.id)}</option>`).join('');if($('pfFacebook'))$('pfFacebook').innerHTML='<option value="">— Chưa map —</option>'+p.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('')}catch(e){}}
async function preflight(){try{$('preflightResult').textContent=JSON.stringify(await api('/api/facebook/preflight',{method:'POST',body:JSON.stringify({video_path:$('pubVideo').value})}),null,2)}catch(e){$('preflightResult').textContent=e.message}}
async function publishReel(){if($('pubDry').value==='false'&&!confirm('ĐĂNG THẬT lên Facebook Page?'))return;try{const d=await api('/api/facebook/publish/reel',{method:'POST',body:JSON.stringify({page_id:$('pubPage').value,video_path:$('pubVideo').value,title:$('pubTitle').value,description:$('pubDesc').value,dry_run:$('pubDry').value==='true'})});alert('Publish job '+d.publish_job_id);setTimeout(loadPublish,600)}catch(e){alert(e.message)}}
async function loadPublish(){try{const r=await api('/api/facebook/publish/jobs?limit=100');$('publishTable').innerHTML=`<table><thead><tr><th>Job</th><th>Page</th><th>Status</th><th>Dry</th><th>Video ID</th><th>Error</th></tr></thead><tbody>${r.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td class="mono">${esc(x.page_id)}</td><td>${pill(x.status)}</td><td>${x.dry_run?'YES':'NO'}</td><td class="mono">${esc(x.fb_video_id||'-')}</td><td class="bad">${esc(x.error||'')}</td></tr>`).join('')}</tbody></table>`}catch(e){}}

function connectUi(){const proto=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${proto}://${location.host}/ws/ui`);ws.onmessage=e=>{try{const m=JSON.parse(e.data);logEvent(m);if(['AGENT_HELLO','AGENT_CONNECTED','AGENT_DISCONNECTED','EXTENSION_VERSION_MISMATCH','FLOW_JOB_ACCEPTED','FLOW_JOB_RESULT','FACTORY_VIDEO_READY','VIDEO_READY'].includes(m.type))scheduleRefreshTop();if(m.type==='EXTENSION_VERSION_MISMATCH')toast('Extension quá cũ: '+(m.agent?.version||'?')+' · cần >= '+(m.requiredVersion||'?')+'. Đã STOP_ALL và khóa tạo job.','error',12000);if(m.type==='FACTORY_VIDEO_READY'&&lastFactoryRun)pollFactoryRun();if(['FLOW_JOB_RESULT','VIDEO_READY','DOWNLOAD_ONLY_TEST_OK','DOWNLOAD_ONLY_TEST_FAILED'].includes(m.type))refreshDownloadTestStatus();if(m.type==='VIDEO_READY'&&m.jobId===lastVideoTestJob)pollVideoTest();if(['FACTORY_VIDEO_READY','VIDEO_READY','IMAGE_READY','JOB_STATUS','PERSONA_ANGLE_READY'].includes(m.type)&&document.getElementById('autofactory')?.classList.contains('active'))setTimeout(()=>loadProfileWorkspace($('fvProfile')?.value||''),350);if(['PERSONA_ANGLE_READY','PERSONA_PACK_READY','PERSONA_PACK_PARTIAL'].includes(m.type)&&$('pfId')?.value===m.profileId)pollPersonaPack(m.profileId,true);if(document.getElementById('logs')?.classList.contains('active'))setTimeout(loadLogs,200)}catch{}};ws.onclose=()=>setTimeout(connectUi,1500)}
document.addEventListener('ui-language-changed',()=>{try{renderLogs();loadSimplePages();loadProfiles();loadPages();loadSchedulerStatus();const p=$('fvProfile')?.value||'';if(p&&document.getElementById('autofactory')?.classList.contains('active'))loadProfileWorkspace(p)}catch{}});
if($('pfPersonFile'))$('pfPersonFile').addEventListener('change',()=>{uploadMeta.delete('pfPersonFile');const f=$('pfPersonFile').files?.[0];if($('pfImportStatus'))$('pfImportStatus').innerHTML=f?`<span class="warn"><b>CHƯA KIỂM TRA</b> · ${esc(f.name)} · bấm LƯU HỒ SƠ để server upload + xác minh ảnh</span>`:'<span class="hint">Chưa kiểm tra ảnh FRONT.</span>';});
refreshTop();loadSimplePages();loadProfiles();loadPages();loadAiModels();refreshDownloadTestStatus();connectUi();toggleScheduleFields();if($('fvProfile'))$('fvProfile').addEventListener('change',()=>{closeSettingsEditor();const id=$('fvProfile').value;showSettingsProfileSummary(id);loadSchedulerStatus();if(id)loadProfileWorkspace(id)});setInterval(refreshTop,12000);setInterval(()=>{if(document.getElementById('autofactory')?.classList.contains('active'))loadSchedulerStatus()},15000);
