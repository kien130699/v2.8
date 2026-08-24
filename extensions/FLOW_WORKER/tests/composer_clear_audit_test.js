const fs=require('fs');
const page=fs.readFileSync('../page.js','utf8');
const bg=fs.readFileSync('../background.js','utf8');
const ok=(c,m)=>{if(!c){console.error('FAIL',m);process.exitCode=1}else console.log('PASS',m)};
ok(page.includes('getComposerMediaRemovePoint') && page.includes('button[data-card-open]') && page.includes("norm(i.textContent)==='cancel'"),'detect exact Flow reference-chip cancel controls');
ok(page.includes('const clearPrompt = async'),'exposes Slate clearPrompt');
ok(bg.includes('clearComposerBeforeCreate(tabId,tag)'),'composer clean helper exists');
ok((bg.match(/await clearComposerBeforeCreate\(tabId,tag\);/g)||[]).length>=2,'clean runs before IMAGE and VIDEO');
ok(bg.includes('COMPOSER CLEAN → refs=0 · prompt=empty'),'verified clean state is logged');
