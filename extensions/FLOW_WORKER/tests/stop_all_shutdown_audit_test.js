const fs=require('fs');
const bg=fs.readFileSync('../background.js','utf8');
const manifest=JSON.parse(fs.readFileSync('../manifest.json','utf8'));
function ok(c,m){if(!c)throw new Error(m);console.log('PASS',m)}
ok(manifest.version==='14.5.20','extension version 14.5.20');
ok(bg.includes("message?.type==='STOP_ALL'")&&bg.includes('stopAllExtensionWork'),'STOP_ALL command wired');
ok(bg.includes("runtimeCache?.running || serverRunPromise || ACTIVE_DOWNLOAD_IDS.size"),'websocket disconnect fail-safe wired');
ok(bg.includes('chrome.downloads.cancel'),'active browser downloads cancelled');
ok(bg.includes('chrome.tabs.reload'),'Flow tab local automation aborted');
ok(bg.includes("type:'STOP_ALL_ACK'"),'STOP_ALL acknowledgement returned');
ok(bg.includes('throwIfServerStopRequested'),'long-running download/automation abort guard exists');
console.log('7/7 STOP_ALL shutdown assertions passed');
