const fs=require('fs'),path=require('path');
const root=path.resolve(__dirname,'..');
const bg=fs.readFileSync(path.join(root,'background.js'),'utf8');
const page=fs.readFileSync(path.join(root,'page.js'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
function ok(v,msg){if(!v)throw new Error('FAIL '+msg);console.log('PASS '+msg);}
ok(manifest.version==='14.5.20','version 14.5.20');
ok(bg.includes('freshDetailVideoCdnUrl'),'fresh exact-detail network fallback');
ok(bg.includes('mappedStatusVideoCdnUrl'),'status response mediaId -> CDN mapping');
ok(bg.includes('video-detail-currentSrc'),'exact detail currentSrc source');
ok(bg.includes('video-detail-fresh-network'),'fresh opaque CDN source');
ok(bg.includes('seqBefore'),'network sequence fence');
ok(page.includes('getExactVideoDetailDownloadUrlHints'),'page exact-detail hints');
ok(page.includes('forceExactVideoDetailMediaLoad'),'force lazy video load');
ok(page.includes('href.includes(`/edit/${mediaId}`)'),'exact detail route fence');
console.log('9/9 v14.5.20 CDN detail fallback assertions passed');
