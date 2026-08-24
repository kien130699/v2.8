import asyncio, os, sys, shutil, subprocess
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
os.environ['AUTO_CACHE_FLOW_IMAGES']='0'
sys.path.insert(0,str(ROOT))
import app as s
from fastapi.testclient import TestClient


def mkimg(path:Path, idx:int):
    im=Image.new('RGB',(768,1376),(30+idx*25,60+idx*15,100+idx*10))
    d=ImageDraw.Draw(im); d.rectangle((80+idx*7,120,650,1100),outline=(255,255,255),width=12); d.text((120,1200),f'VAR {idx}',fill=(255,255,255))
    im.save(path,quality=92)

def mkclip(path:Path, idx:int):
    colors=['red','blue','green','purple','orange']
    subprocess.run(['ffmpeg','-y','-f','lavfi','-i',f'color=c={colors[idx%len(colors)]}:s=540x960:d=2.5:r=30','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def main():
    class DummyWs:
        async def send_text(self, text):
            return None
    s.AGENTS.clear()
    agent = s.AgentRuntime('TEST_AGENT', DummyWs())
    agent.version = '14.7.0'
    agent.ready = True
    agent.phase = 'idle'
    s.AGENTS[agent.id] = agent
    if s.DB_PATH.exists(): s.DB_PATH.unlink()
    for x in [s.DB_PATH.with_suffix('.sqlite3-wal'),s.DB_PATH.with_suffix('.sqlite3-shm')]:
        if x.exists(): x.unlink()
    s.init_db()
    td=ROOT/'outputs'/'_v2_test'; shutil.rmtree(td,ignore_errors=True); td.mkdir(parents=True)
    person=td/'person.jpg'; mkimg(person,0)
    with TestClient(s.app) as c:
        r=c.post('/api/page-profiles',json={
            'id':'gym_a','name':'Gym Girl A','theme':'adult fitness glamour','persona_path':str(person),
            'body_preset':'curvy_fit','sexiness_level':70,'outfit_prompts':['black fitted gym set','white fitted athleisure set'],
            'backgrounds':['modern gym','hotel gym','rooftop'],'poses':['walking toward camera','mirror selfie','side pose'],
            'default_video_mode':'AUTO','image_to_video_ratio':25,'image_model':'Nano Banana 2','video_model':'Veo 3.1 - Fast'
        }); assert r.status_code==200,r.text
        p=c.get('/api/page-profiles/gym_a').json(); assert p['persona_path']==str(person)

        beat=c.post('/api/factory/v2/generate',json={'page_profile_id':'gym_a','videos':1,'mode':'IMAGE_BEAT','beat_image_count':4,'beat_duration_sec':5,'image_concurrency':4}).json()
        bj=beat['jobs'][0]['job_id']; detail=c.get('/api/flow/jobs/'+bj).json();
        assert detail['kind']=='factory_v2_beat'; assert detail['flow']['videoModel']=='NONE'; assert len(detail['scenes'])==4
        for i in range(4):
            f=td/f'beat_{i}.jpg'; mkimg(f,i+1); s.add_asset(bj,i+1,'image',local_path=str(f),media_id=f'm{i}')
        asyncio.run(s.render_factory_v2(bj))
        d=c.get('/api/flow/jobs/'+bj).json(); assert d['status']=='done',d
        fa=[x for x in d['assets'] if x['kind']=='final_video']; assert len(fa)==1
        q=c.get('/api/qc/'+bj).json(); assert q['passed'] is True,q

        i2v=c.post('/api/factory/v2/generate',json={'page_profile_id':'gym_a','videos':1,'mode':'IMAGE_TO_VIDEO','i2v_clip_count':3,'i2v_clip_duration':'4s','image_concurrency':3,'video_concurrency':2}).json()
        ij=i2v['jobs'][0]['job_id']; detail=c.get('/api/flow/jobs/'+ij).json()
        assert detail['kind']=='factory_v2_i2v'; assert detail['flow']['videoModel']=='Veo 3.1 - Fast'
        assert all(x['videoPrompt'] for x in detail['scenes'])
        assert len({x['videoPrompt'] for x in detail['scenes']})==3
        for i in range(3):
            f=td/f'clip_{i}.mp4'; mkclip(f,i); s.add_asset(ij,i+1,'video',local_path=str(f),media_id=f'v{i}')
        asyncio.run(s.render_factory_v2(ij))
        d=c.get('/api/flow/jobs/'+ij).json(); assert d['status']=='done',d
        fa=[x for x in d['assets'] if x['kind']=='final_video']; assert len(fa)==1
        q=c.get('/api/qc/'+ij).json(); assert q['passed'] is True,q

        runs=c.get('/api/factory/v2/runs').json(); assert len(runs)>=2
    print('V2 TRIAL TEST OK')

if __name__=='__main__': main()

