import json
from types import SimpleNamespace
from pathlib import Path
import pytest

from meeting_notes import connections, runtime
from meeting_notes.asr import SetupError
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from test_chunking import upload, Recognizer
from test_api import client


def test_legacy_connection_survives_optional_xai_and_reopen(tmp_path,monkeypatch):
    svc=Service(tmp_path)
    legacy={'endpoint':'http://private-asr.test/v1','model':'my-nemotron','timeout':720,'max_audio_seconds':1234}
    with svc.db() as db: db.execute('INSERT INTO settings VALUES(1,?)',(json.dumps(legacy),))
    state=svc.connections()
    assert state['active']=='custom' and state['connections']['custom']==legacy
    custom_snapshot=svc.settings()
    xai={**state['connections']['xai'],'timeout':900,'max_audio_seconds':120}
    state=connections.save(svc,'xai',xai,state['revision'])
    assert svc.settings()==custom_snapshot  # saving is not activation
    monkeypatch.setattr('meeting_notes.asr.require_xai_host',lambda:None)
    state=connections.select(svc,'xai',state['revision'])
    assert svc.settings()=={**xai,'adapter':'xai_oauth'}
    svc=Service(tmp_path)
    state=connections.select(svc,'custom',state['revision'])
    assert svc.settings()==custom_snapshot
    assert state['connections']['xai']==xai
    with svc.db() as db: assert json.loads(db.execute('SELECT data FROM settings').fetchone()[0])==legacy


def test_stale_config_and_unknown_or_secret_fields_rejected(tmp_path):
    svc=Service(tmp_path); state=svc.connections()
    custom=state['connections']['custom']
    state=connections.save(svc,'custom',custom,0)
    with pytest.raises(ValueError,match='reload'): connections.save(svc,'xai',state['connections']['xai'],0)
    for field in ('key','token','auth','provider'):
        with pytest.raises(ValueError): connections.save(svc,'custom',{**custom,field:'sentinel'},state['revision'])
    with pytest.raises(ValueError): connections.save(svc,'unknown',custom,state['revision'])
    for endpoint in ('https://evil.test/v1','http://api.x.ai/v1','https://api.x.ai/v1?key=sentinel'):
        with pytest.raises(ValueError): connections.save(svc,'xai',{**state['connections']['xai'],'endpoint':endpoint},state['revision'])
    assert svc.connections()==state


def test_provider_change_never_rewrites_existing_plan(tmp_path,monkeypatch):
    svc=Service(tmp_path); svc.configure({**svc.settings(),'max_audio_seconds':1})
    mid,_,_=upload(svc,32000)
    adapter=Recognizer(); adapter.fail=2
    worker=Worker(svc,lambda _:adapter)
    worker.run_once(); worker.run_once()
    with svc.db() as db: original=db.execute('select settings from asr_plans').fetchone()[0]
    monkeypatch.setattr('meeting_notes.asr.require_xai_host',lambda:None)
    state=svc.connections(); xai={**state['connections']['xai'],'model':svc.settings()['model']}
    state=connections.save(svc,'xai',xai,state['revision'])
    connections.select(svc,'xai',state['revision'])
    with pytest.raises(ValueError,match='original model and adapter'): svc.retry(mid,True)
    svc.retry(mid)
    worker.run_once()
    assert svc.get(mid)['job']['state']=='completed'
    with svc.db() as db: assert db.execute('select settings from asr_plans').fetchone()[0]==original
    assert len(adapter.calls)==3


def test_connection_reads_do_not_resolve_login_and_unsupported_host_fails_closed(client,monkeypatch):
    from meeting_notes import asr
    monkeypatch.setattr(runtime,'start',lambda:pytest.fail('settings started a worker'))
    monkeypatch.setattr(asr,'xai_host_status',lambda:dict(available=False,message='Host extension required'))
    prefix='/api/plugins/meeting_notes'
    state=client.get(prefix+'/connections').json()
    assert state['active']=='custom' and not state['availability']['xai']['available']
    saved=client.put(prefix+'/connections/xai',json={'revision':state['revision'],'settings':state['connections']['xai']})
    assert saved.status_code==200 and saved.json()['active']=='custom'
    response=client.post(prefix+'/connections/active',json={'provider':'xai','revision':saved.json()['revision']})
    assert response.status_code==400 and 'Host extension' in response.text
    assert runtime.service().connections()['active']=='custom'


def test_worker_owner_is_checked_on_request_not_captured_from_browser(tmp_path,monkeypatch):
    svc=Service(tmp_path)
    monkeypatch.setattr(runtime,'service',lambda:svc)
    monkeypatch.setattr('meeting_notes.asr.require_xai_host',lambda:None)
    adapter=runtime.adapter_for({**svc.connections()['connections']['xai'],'adapter':'xai_oauth'},svc.root)
    adapter.probe()
    monkeypatch.setattr(runtime,'service',lambda:SimpleNamespace(root=Path('/different-profile')))
    with pytest.raises(SetupError,match='profile changed'): adapter.probe()


def test_active_work_blocks_switching_but_not_editing_inactive_connection(tmp_path,monkeypatch):
    svc=Service(tmp_path); mid,_,_=upload(svc,16000)
    with svc.db() as db: db.execute("UPDATE jobs SET state='preparing' WHERE meeting=?",(mid,))
    state=svc.connections()
    state=connections.save(svc,'xai',{**state['connections']['xai'],'timeout':1200},state['revision'])
    monkeypatch.setattr('meeting_notes.asr.require_xai_host',lambda:None)
    with pytest.raises(ValueError,match='Wait'): connections.select(svc,'xai',state['revision'])
    with pytest.raises(ValueError,match='Wait'): connections.save(svc,'custom',state['connections']['custom'],state['revision'])
    assert svc.connections()['active']=='custom'
