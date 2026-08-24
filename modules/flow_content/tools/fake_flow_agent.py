import asyncio, json, subprocess, uuid
from pathlib import Path
import requests, websockets
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
AGENT_URL='ws://127.0.0.1:8786/ws'

def find_web_port():
    for port in range(8897,8911):
        try:
            r=requests.get(f'http://127.0.0.1:{port}/api/health',timeout=.25)
            if r.ok: return port
        except Exception: pass
    return 8897

def fake_clip(path:Path,idx:int,duration=1.7):
    path.parent.mkdir(parents=True,exist_ok=True)
    colors=['0x9b4dca','0x376fb3','0xb14d6b','0x3c9970','0xc07a3d']
    subprocess.run(['ffmpeg','-y','-f','lavfi','-i',f'color=c={colors[idx%len(colors)]}:s=540x960:d={duration}:r=30','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

async def main():
    port=find_web_port()
    print('Fake Flow Agent ->',AGENT_URL,'| UI',port)
    async with websockets.connect(AGENT_URL) as ws:
        await ws.send(json.dumps({'type':'AGENT_HELLO','role':'flow-extension','extensionId':'FAKE_AGENT_V2','version':'14.7.0-fake','failSafeReady':True,'runtime':{'running':False}}))
        async for raw in ws:
            m=json.loads(raw)
            if m.get('type')=='PING': await ws.send(json.dumps({'type':'PONG'})); continue
            if m.get('type')!='RUN_FLOW_JOB': continue
            jid=m['jobId']; scenes=m.get('scenes',[]); video_on=str((m.get('flow') or {}).get('videoModel','NONE')).upper()!='NONE'
            await ws.send(json.dumps({'type':'FLOW_JOB_ACCEPTED','jobId':jid,'runId':'fake_'+uuid.uuid4().hex[:8],'sceneCount':len(scenes)}))
            results=[]
            for i,scene in enumerate(scenes):
                sid=scene.get('sceneId',i+1); downloads=[]; vids=[]
                fake_img=ROOT/'static'/f'fake_flow_{sid%10}.jpg'
                if not fake_img.exists():
                    im=Image.new('RGB',(768,1376),(45+(sid*31)%170,55+(sid*47)%160,70+(sid*59)%150)); d=ImageDraw.Draw(im); d.rectangle((70+sid*5,100,680,1180),outline=(255,255,255),width=14); d.text((110,1230),f'FAKE SCENE {sid}',fill=(255,255,255)); im.save(fake_img,quality=90)
                image_url=f'http://127.0.0.1:{port}/static/{fake_img.name}'
                if video_on:
                    clip=ROOT/'outputs'/'fake_agent'/jid/f'clip_{sid:03d}.mp4'; fake_clip(clip,i)
                    mid='fakevid_'+uuid.uuid4().hex[:10]; vids=[mid]; downloads=[{'mediaId':mid,'mediaIndex':0,'localPath':str(clip.resolve()),'state':'COMPLETE'}]
                    await ws.send(json.dumps({'type':'VIDEO_FILE_READY','jobId':jid,'sceneId':sid,'mediaId':mid,'mediaIndex':0,'localPath':str(clip.resolve())}))
                results.append({'index':i,'sceneId':sid,'imageState':'SUCCESS','videoState':'SUCCESS' if video_on else 'SKIP','error':None,'image':{'mediaId':'fakeimg_'+uuid.uuid4().hex[:10],'url':image_url,'title':scene.get('imagePrompt','')[:80]},'videoMediaIds':vids,'videoAssets':[{'mediaId':x} for x in vids],'videoChainMediaIds':vids,'downloads':downloads,'downloadState':'DONE' if video_on else None})
            await asyncio.sleep(.3)
            await ws.send(json.dumps({'type':'FLOW_JOB_RESULT','jobId':jid,'ok':True,'results':results}))

if __name__=='__main__': asyncio.run(main())

