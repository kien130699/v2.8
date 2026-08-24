const fs=require('fs');const path=require('path');const root=path.join(__dirname,'..');const bg=fs.readFileSync(path.join(root,'background.js'),'utf8');const html=fs.readFileSync(path.join(root,'popup.html'),'utf8');const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));function ok(c,m){if(!c)throw new Error(m);console.log('PASS',m)}
ok(bg.includes("DEFAULT_SERVER_URL = 'ws://127.0.0.1:3000/ws/flow'"),'1 local server websocket default');
ok(bg.includes("type:'AGENT_HELLO'")&&bg.includes("role:'flow-extension'"),'2 extension handshake');
ok(bg.includes("message?.type==='RUN_FLOW_JOB'")&&bg.includes('runServerJob(message)'),'3 server job command wired');
ok(bg.includes('findOrOpenFlowTab()'),'4 server mode can find/open Flow tab');
ok(bg.includes('Array.isArray(message.scenes)')&&bg.includes('normalizeScenes'),'5 server scenes bypass pipe-text parser');
ok(bg.includes('directDownloadVideoToServer')&&bg.includes("type:'VIDEO_DOWNLOAD_URL'")&&bg.includes('resolveSignedVideoUrlViaFlowPage'),'6 server jobs resolve signed URL in Flow page and let server download');
ok(bg.includes('options.downloadTasks.push(task)')&&bg.includes('Promise.allSettled(options.downloadTasks)'),'7 downloads do not occupy video slot but batch waits before result');
ok(bg.includes("type:'FLOW_JOB_RESULT'")&&bg.includes('jobId,ok:true'),'8 final result returns to server');
ok(manifest.permissions.includes('alarms')&&manifest.permissions.includes('downloads'),'9 MV3 keepalive/download permissions');
ok(JSON.stringify(manifest).includes('ws://127.0.0.1:*'),'10 extension CSP allows localhost websocket');
ok(html.includes('id="serverEnabled"')&&html.includes('id="serverUrl"')&&html.includes('id="serverBadge"'),'11 popup server controls');
console.log('11/11 v14.5.20 server bridge assertions passed');
