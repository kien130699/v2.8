const fs=require('fs');
const path=require('path');
const root=path.resolve(__dirname,'..');
const page=fs.readFileSync(path.join(root,'page.js'),'utf8');
let n=0; function ok(cond,msg){ n++; if(!cond){ console.error('FAIL',n,msg); process.exitCode=1; } else console.log('PASS',n,msg); }
ok(page.includes('let durationControlPresent=false;'),'duration presence is telemetry');
ok(page.includes('observed:{durationControlPresent}'),'duration presence returned under observed');
ok(!page.includes('checks.durationControlPresent='),'duration presence is not a validation check');
ok(page.includes("if(type==='VIDEO' && ['4s','6s','8s','10s'].includes(dur))"),'duration validation scoped to VIDEO only');
ok(page.includes("checks.duration=durationTab ? specSelected(menu,{text:dur}) : true;"),'missing video duration control is accepted');
if(!process.exitCode) console.log('settings verification regression audit PASS');
