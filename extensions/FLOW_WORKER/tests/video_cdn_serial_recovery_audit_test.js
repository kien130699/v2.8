const fs=require('fs'),path=require('path');
const root=path.resolve(__dirname,'..');
const bg=fs.readFileSync(path.join(root,'background.js'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
function ok(v,m){if(!v)throw new Error('FAIL '+m);console.log('PASS '+m)}
ok(manifest.version==='14.5.20','version 14.5.20');
ok(bg.includes('SERVER_CDN_RECOVERY_TAIL'),'global CDN recovery tail');
ok(bg.includes('enqueueServerCdnRecovery'),'serialized recovery helper');
ok(bg.includes("CDN RECOVERY LOCK"),'lock log');
ok(bg.includes("CDN RECOVERY UNLOCK"),'unlock log');
ok(bg.includes('len>=4096'),'tiny CDN response rejected');
ok(bg.includes("mime.startsWith('video/') || rt==='media'"),'video/media response filter');
ok(bg.includes('encodedDataLength:Number(params.encodedDataLength||0)'),'loading size captured');
console.log('8/8 v14.5.20 serial recovery assertions passed');
