import json
import app

class Resp:
    def __init__(self,status=200,data=None):
        self.status_code=status; self._data=data or {}; self.ok=200<=status<300
        self.text=json.dumps(self._data); self.headers={'content-type':'application/json'}
    def json(self): return self._data

def fake_get(url,**kwargs):
    return Resp(data={'data':[{'id':'cx/gpt-5.4'},{'id':'ag/gemini-3.1-pro-high'}]})

def fake_post(url,**kwargs):
    model=(kwargs.get('json') or {}).get('model')
    if model=='ag/gemini-3.1-pro-high': return Resp(503,{'error':{'message':'quota'}})
    return Resp(data={'choices':[{'message':{'content':'OK'}}]})

def main():
    app.init_db(); app.ROUTER9_API_KEY='sk-test'; app.requests.get=fake_get; app.requests.post=fake_post
    assert app.test_router9_model_sync('cx/gpt-5.4')['ok'] is True
    assert app.test_router9_model_sync('ag/gemini-3.1-pro-high')['ok'] is False
    assert [m['id'] for m in app.router9_usable_models()] == ['cx/gpt-5.4','ag/gemini-3.1-pro-high']
    print('model_health_test PASS')

if __name__=='__main__': main()

