import os, sys, json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ['AUTO_CACHE_FLOW_IMAGES'] = '0'
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
import app as server


def main():
    # clean DB for deterministic smoke test
    if server.DB_PATH.exists():
        server.DB_PATH.unlink()
    with TestClient(server.app) as client:
        h = client.get('/api/health').json()
        assert h['ok'] is True

        with client.websocket_connect('/ws') as ws:
            ws.send_json({
                'type':'AGENT_HELLO','role':'flow-extension','extensionId':'TEST_EXT','version':'14.7.0','failSafeReady':True,'runtime':{'running':False}
            })
            # create one test image job
            r = client.post('/api/flow/test', json={
                'prompt':'Photorealistic adult fitness woman in a modern gym',
                'image_model':'Nano Banana 2','aspect_ratio':'9:16','image_outputs':'x1'
            })
            assert r.status_code == 200, r.text
            jid = r.json()['job_id']
            cmd = ws.receive_json()
            assert cmd['type'] == 'RUN_FLOW_JOB'
            assert cmd['jobId'] == jid
            assert cmd['flow']['videoModel'] == 'NONE'
            assert cmd['scenes'][0]['sceneId'] == 1

            ws.send_json({'type':'FLOW_JOB_ACCEPTED','jobId':jid,'runId':'test_run','sceneCount':1})
            ws.send_json({
                'type':'FLOW_JOB_RESULT','jobId':jid,'ok':True,
                'results':[{
                    'index':0,'sceneId':1,'imageState':'SUCCESS','videoState':'WAIT','error':None,
                    'image':{'mediaId':'m_test_001','url':'https://example.invalid/test.jpg','title':'test'},
                    'videoMediaIds':[],'videoAssets':[],'videoChainMediaIds':[],'downloads':[]
                }]
            })
            time.sleep(0.1)
            detail = client.get(f'/api/flow/jobs/{jid}').json()
            assert detail['status'] == 'done', detail
            assert len(detail['assets']) == 1
            assert detail['assets'][0]['media_id'] == 'm_test_001'

        # Facebook local-only dry-run setup. No network call should happen.
        rr = client.post('/api/facebook/pages', json={'page_id':'123','name':'Test Page','access_token':'dummy'})
        assert rr.status_code == 200
        # create a dummy path and verify missing-file behavior through preflight
        miss = client.post('/api/facebook/preflight', json={'video_path':'no_such_file.mp4'})
        assert miss.status_code == 404

    print('SMOKE TEST OK')


if __name__ == '__main__':
    main()

