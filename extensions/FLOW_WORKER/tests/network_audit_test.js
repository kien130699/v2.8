const fs=require('fs');
const f=JSON.parse(fs.readFileSync(__dirname+'/fixtures.json','utf8'));
const parseJsonSafe=t=>{try{return JSON.parse(t)}catch{return null}};
const compactText=v=>String(v??'').replace(/\s+/g,' ').trim();
function collectStructuredTexts(node,out=[]){if(!node||typeof node!=='object')return out;if(Array.isArray(node)){for(const i of node)collectStructuredTexts(i,out);return out;}for(const [k,v] of Object.entries(node)){if(k==='text'&&typeof v==='string'&&v.trim())out.push(compactText(v));else if(k!=='token'&&k!=='recaptchaContext')collectStructuredTexts(v,out);}return out;}
function projectIdFromImageApiUrl(url=''){return String(url).match(/\/v1\/projects\/([^/?#]+)\/flowMedia:batchGenerateImages/)?.[1]||null;}
function parseRequestInfo(kind,url='',postData=''){
  const json=parseJsonSafe(postData);
  const empty={parsed:!!json,validGeneration:false,projectId:projectIdFromImageApiUrl(url),projectIds:[],batchId:null,texts:[],mediaIds:[],referenceMediaIds:[],requestCount:0};
  if(!json)return empty;
  const projectIds=[...new Set([json?.clientContext?.projectId,json?.workflow?.projectId,...(Array.isArray(json?.media)?json.media.map(x=>x?.projectId):[]),...(Array.isArray(json?.requests)?json.requests.map(x=>x?.clientContext?.projectId):[])].filter(Boolean))];
  const projectId=projectIds[0]||empty.projectId||null;
  const batchId=json?.mediaGenerationContext?.batchId||json?.workflow?.metadata?.batchId||null;
  const texts=[...new Set(collectStructuredTexts(json,[]))];
  if(kind==='IMAGE_CREATE'){const requests=Array.isArray(json?.requests)?json.requests:[];return {...empty,parsed:true,projectId,projectIds,batchId,texts,validGeneration:requests.length>0,requestCount:requests.length};}
  if(kind==='VIDEO_CREATE'){const requests=Array.isArray(json?.requests)?json.requests:[];const refs=[];for(const r of requests)for(const ref of (Array.isArray(r?.referenceImages)?r.referenceImages:[]))if(ref?.mediaId)refs.push(ref.mediaId);return {...empty,parsed:true,projectId,projectIds,batchId,texts,validGeneration:requests.length>0,requestCount:requests.length,referenceMediaIds:[...new Set(refs)]};}
  if(kind==='VIDEO_STATUS'){const media=Array.isArray(json?.media)?json.media:[];return {...empty,parsed:true,projectId,projectIds,texts,validGeneration:media.length>0,requestCount:media.length,mediaIds:media.map(x=>x?.name).filter(Boolean)};}
  return {...empty,parsed:true,projectId,projectIds,batchId,texts};
}
function unique(items){const seen=new Set();return items.filter(x=>x.mediaId&&!seen.has(x.mediaId)&&seen.add(x.mediaId));}
function imageMediaFromResponse(json){const found=[];for(const item of (Array.isArray(json?.media)?json.media:[])){const g=item?.image?.generatedImage||{};const id=item?.name||g?.mediaId;if(id)found.push({mediaId:id});}for(const wf of (Array.isArray(json?.workflows)?json.workflows:[])){const id=wf?.metadata?.primaryMediaId;if(id)found.push({mediaId:id});}return unique(found);}
function videoMediaFromResponse(json){const found=[];for(const item of (Array.isArray(json?.media)?json.media:[]))if(item?.name)found.push({mediaId:item.name});for(const wf of (Array.isArray(json?.workflows)?json.workflows:[]))if(wf?.metadata?.primaryMediaId)found.push({mediaId:wf.metadata.primaryMediaId});return unique(found);}
function assert(cond,msg){if(!cond)throw new Error(msg);console.log('PASS',msg)}
const project='b9a6ed68-bcc3-441c-97db-7d728390ee89';
const imageUrl=`https://aisandbox-pa.googleapis.com/v1/projects/${project}/flowMedia:batchGenerateImages`;
const ir=parseRequestInfo('IMAGE_CREATE',imageUrl,f.imageRequest);
assert(ir.validGeneration,'1 image request recognized');
assert(ir.projectId===project,'2 image projectId exact');
assert(!!ir.batchId,'3 image batchId extracted');
const imageJson=JSON.parse(f.imageResponse);
const imageIds=imageMediaFromResponse(imageJson).map(x=>x.mediaId);
assert(imageIds.length===1,'4 image media deduped from media + workflow');
assert(imageIds[0]==='3dbf10ff-d650-4f9b-b9e6-e402d2476950','5 exact image mediaId');
const vr=parseRequestInfo('VIDEO_CREATE','https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoReferenceImages',f.videoRequest);
assert(vr.validGeneration,'6 video request recognized');
assert(vr.projectId===project,'7 video projectId exact');
assert(vr.referenceMediaIds.includes(imageIds[0]),'8 video request contains exact image reference');
const videoIds=videoMediaFromResponse(JSON.parse(f.videoResponse)).map(x=>x.mediaId);
assert(videoIds.length===1,'9 video media deduped from media + workflow');
assert(videoIds[0]==='e5831372-b867-470f-844b-4d7a38ea40c7','10 exact video mediaId');
const sr=parseRequestInfo('VIDEO_STATUS','https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus',f.statusRequest);
assert(sr.mediaIds.includes(videoIds[0]),'11 status request contains exact video mediaId');
assert(sr.projectIds.includes(project),'12 status request project exact');
const status=JSON.parse(f.statusResponse).media[0].mediaMetadata.mediaStatus.mediaGenerationStatus;
assert(status==='MEDIA_GENERATION_STATUS_SUCCESSFUL','13 final status successful');
assert(!parseRequestInfo('IMAGE_CREATE',imageUrl,'{}').validGeneration,'14 empty/non-generation payload rejected');
console.log('14/14 v10 HAR fixture assertions passed');
