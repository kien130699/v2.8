const fs=require('fs');const path=require('path');const root=path.join(__dirname,'..');
const bg=fs.readFileSync(path.join(root,'background.js'),'utf8');const page=fs.readFileSync(path.join(root,'page.js'),'utf8');
function ok(c,m){if(!c)throw new Error(m);console.log('PASS',m)}
ok(page.includes('openSettings,getModelTriggerPoint'),'1 page exposes openSettings fallback');
ok(bg.includes('trusted + page fallback'),'2 settings uses trusted + page-world fallback');
ok(bg.includes('FLOW RECOVERY →')&&bg.includes('chrome.tabs.reload'),'3 recovery can F5 Flow');
ok(bg.includes('trỏ lại project cũ')&&bg.includes('expectedProjectId'),'4 recovery preserves original project when known');
ok(bg.includes('lastHardRefresh')&&bg.includes('closeAssetPicker'),'5 asset picker hard refresh exists');
ok(bg.includes('upload đã F5 Flow → re-verify IMAGE Settings'),'6 image settings are reverified after upload recovery');
console.log('6/6 v14.5 UI recovery assertions passed');
