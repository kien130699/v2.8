const fs=require('fs');
const bg=fs.readFileSync(require('path').join(__dirname,'..','background.js'),'utf8');
function ok(c,m){if(!c)throw new Error(m);console.log('PASS',m)}
ok(bg.includes('function isFlowProjectRootUrl'),'root route detector exists');
ok(bg.includes('function normalizeProjectRoot'),'project root normalizer exists');
ok(bg.includes("String(tab.url||'').includes('/edit/')"),'detail route is explicitly detected');
ok(bg.includes("await normalizeProjectRoot(tabId,projectId,'job bắt đầu')"),'job start exits detail before All Media');
ok(bg.includes('if(!isFlowProjectRootUrl(current?.url||\'\',projectId))'),'All Media navigator no longer uses startsWith');
ok(bg.includes('ALL MEDIA RECOVERY'),'All Media missing recovery exists');
console.log('6/6 media detail exit assertions passed');
