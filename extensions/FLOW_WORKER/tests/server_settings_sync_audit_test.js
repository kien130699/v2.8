const fs=require('fs');
const bg=fs.readFileSync('../background.js','utf8');
function ok(v,msg){if(!v){console.error('FAIL',msg);process.exit(1)}console.log('PASS',msg)}
ok(bg.includes("DEFAULT_SERVER_URL = 'ws://127.0.0.1:3000/ws/flow'"),'default websocket is 3000 /ws/flow');
ok(bg.includes('async function syncServerFlowToPopup'),'server settings sync helper exists');
ok(bg.includes("imageOutputs:'imageOutputs'"),'image outputs mirrored to popup');
ok(bg.includes("videoOutputs:'videoOutputs'"),'video outputs mirrored to popup');
ok(bg.includes('await syncServerFlowToPopup(message?.flow||{})'),'server job syncs settings before run');
