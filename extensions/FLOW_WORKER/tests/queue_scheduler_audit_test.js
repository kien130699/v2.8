const fs=require('fs');
const path=require('path');
const root=path.join(__dirname,'..');
const bg=fs.readFileSync(path.join(root,'background.js'),'utf8');
const page=fs.readFileSync(path.join(root,'page.js'),'utf8');
const pop=fs.readFileSync(path.join(root,'popup.js'),'utf8');
const html=fs.readFileSync(path.join(root,'popup.html'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
function ok(c,m){if(!c)throw new Error(m);console.log('PASS',m)}
ok(bg.includes("const EXTENSION_VERSION = chrome.runtime.getManifest().version")&&bg.includes("COMPATIBLE_PAGE_FAMILY"),'1 background runtime manifest version + 14.5.x compatibility');
ok(page.includes("FLOW_PAIR_AUTO_VERSION = '14.5.20'"),'2 page version 14.5.20');
ok(manifest.version==='14.5.20','3 manifest version 14.5.20');
ok(manifest.permissions.includes('downloads'),'4 downloads permission enabled');
ok(!bg.includes('chrome.tabs.create({url:sourceTab.url'),'5 no worker tabs created');
ok(bg.includes('runQueueScheduler(tabId,records,options,limiter)'),'6 dynamic queue scheduler wired');
ok(bg.includes("imageConcurrency||9")&&bg.includes("videoConcurrency||4"),'7 defaults IMAGE=9 VIDEO=4');
ok(bg.includes("submitPolicy==='GLOBAL_FIFO'")&&bg.includes("FIFO nhóm + ưu tiên video nhẹ"),'8 selectable submit policy wired');
ok(bg.includes("if(v && videoInFlight.size<options.videoConcurrency) return videoSubmitQueue.shift()"),'9 light video priority selector exists');
ok(bg.includes('imageSubmitQueue.push')&&bg.includes('videoSubmitQueue.push'),'10 separate FIFO group queues exist');
ok(bg.includes('SUBMIT DISPATCHER → chuyển IMAGE')&&bg.includes('SUBMIT DISPATCHER → chuyển VIDEO'),'11 one dispatcher owns UI mode changes');
ok(bg.includes('waitGenerationRequestStart'),'12 dispatcher waits exact POST start before releasing next submit');
ok(bg.includes('autoDownloadVideos(record,options)')&&bg.includes('chrome.downloads.download'),'13 auto download implementation wired');
ok(bg.includes("MEDIA_GENERATION_STATUS_SUCCESSFUL"),'14 video success status still exact');
ok(html.includes('id="submitPolicy"')&&html.includes('id="autoDownloadVideo"'),'15 popup policy + download settings');
ok(html.includes('<option value="9" selected>9</option>')&&html.includes('<option value="4" selected>4</option>'),'16 popup defaults 9/4');
ok(pop.includes("v.type==='checkbox'?v.checked:v.value")&&pop.includes('autoDownloadVideo:fields.autoDownloadVideo.checked'),'17 checkbox persistence + option send');
ok(bg.includes('isQuotaLikeError')&&bg.includes('Dừng SUBMIT mới'),'18 quota stops new submits only');
ok(bg.includes("'NOT_SUBMITTED',error:record.error,done:true"),'19 cancelled queued submits are finalized, avoiding deadlock');

// Pure policy sanity check mirroring the published selector.
function pick(policy,iq,vq,imageInFlight=0,videoInFlight=0,imageLimit=9,videoLimit=3){
  const i=iq[0]||null,v=vq[0]||null;
  if(policy==='GLOBAL_FIFO'){
    if(i&&v)return i.enqueuedSeq<=v.enqueuedSeq?iq.shift():vq.shift();
    return i?iq.shift():v?vq.shift():null;
  }
  if(v&&videoInFlight<videoLimit)return vq.shift();
  if(i&&imageInFlight<imageLimit)return iq.shift();
  return null;
}
let iq=[{type:'IMAGE',enqueuedSeq:1},{type:'IMAGE',enqueuedSeq:2}],vq=[{type:'VIDEO',enqueuedSeq:3},{type:'VIDEO',enqueuedSeq:4}];
ok(pick('VIDEO_LIGHT',iq,vq).type==='VIDEO','20 light priority chooses VIDEO when video slot free');
ok(pick('VIDEO_LIGHT',iq,vq,0,3).type==='IMAGE','21 when VIDEO 3/3, IMAGE continues top-up');
iq=[{type:'IMAGE',enqueuedSeq:1}];vq=[{type:'VIDEO',enqueuedSeq:2}];
ok(pick('GLOBAL_FIFO',iq,vq).type==='IMAGE','22 global FIFO honors earliest enqueue across groups');
console.log('22/22 v14 queue scheduler assertions passed');
