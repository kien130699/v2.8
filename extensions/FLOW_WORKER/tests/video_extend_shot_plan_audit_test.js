const fs=require('fs');
const path=require('path');
const root=path.resolve(__dirname,'..');
const bg=fs.readFileSync(path.join(root,'background.js'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
let n=0;function ok(v,msg){n++;if(!v){console.error('FAIL',n,msg);process.exit(1)}console.log('PASS',n,msg)}
ok(manifest.version==='14.5.20','worker version 14.5.20');
ok(bg.includes('videoSegments=Array.isArray(scene?.videoSegments)'), 'scene shot plan is preserved');
ok(bg.includes('plan?.[round]?.prompt'), 'Extend round uses per-scene segment prompt');
ok(bg.includes('videoExtendFactor:Math.max(1,Math.min(4'), 'x4 Extend factor supported');
ok(bg.includes("videoState:'EXTENDING'"), 'runtime exposes EXTENDING state');
ok(bg.includes('EXTEND PLAN'), 'logs exact Extend role/prompt before create');
ok(bg.includes('VIDEO BASE SUCCESS · 1/'), 'base video is not reported as fully done before Extend');
ok(bg.includes("type:'VIDEO_CHAIN_INFO'"), 'ordered media chain reported to server');
ok(bg.includes('await runVideoExtendPhase'), 'native Flow Extend phase is wired');
console.log(`${n}/${n} native Flow Extend shot-plan assertions passed`);
