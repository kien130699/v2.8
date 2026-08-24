const fs=require('fs');
const path=require('path');
const root=path.join(__dirname,'..');
const page=fs.readFileSync(path.join(root,'page.js'),'utf8');
function ok(c,m){if(!c)throw new Error(m);console.log('PASS',m)}
ok(page.includes('if(findTab(durationMenu,{text:dur})) await selectOption({text:dur},dur);'),'1 duration apply is conditional on visible Flow control');
ok(page.includes('checks.duration=durationTab ? specSelected(menu,{text:dur}) : true;'),'2 missing duration control is accepted as model-fixed duration');
ok(page.includes('observed:{durationControlPresent}'),'3 verification exposes duration-control diagnostic as telemetry');
console.log('3/3 Veo fixed-duration regression assertions passed');
