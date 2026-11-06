import importlib.util
from pathlib import Path
import sys
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_notes.service import Service


@pytest.fixture
def client(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('meeting_api_test',Path(__file__).parents[1]/'dashboard/plugin_api.py')
    api=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=api
    spec.loader.exec_module(api)
    svc=Service(tmp_path)
    monkeypatch.setattr(api.runtime,'service',lambda:svc)
    monkeypatch.setattr(api.runtime,'analysis_service',lambda meeting,source_profile=None:svc)
    app=FastAPI()
    app.include_router(api.router,prefix='/api/plugins/meeting_notes')
    return TestClient(app)


def test_status_does_not_start_worker(client,monkeypatch):
    from meeting_notes import runtime
    monkeypatch.setattr(runtime,'start',lambda:pytest.fail('GET started worker'))
    assert client.get('/api/plugins/meeting_notes/ping').json()['ok']
    assert client.get('/api/plugins/meeting_notes/meetings').json()==[]


def test_settings_refuse_credentials(client):
    r=client.put('/api/plugins/meeting_notes/settings',json={'endpoint':'http://user:secret@host/v1','model':'test','timeout':600})
    assert r.status_code==400
    assert 'secret@' not in r.text
    assert 'key' not in client.get('/api/plugins/meeting_notes/settings').json()


def test_tool_manifest_matches_registration():
    import yaml
    root=Path(__file__).parents[1]
    spec=importlib.util.spec_from_file_location('meeting_agent_test',root/'__init__.py')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names=[]
    skills=[]
    class Context:
        def register_tool(self,**kwargs): names.append(kwargs['name'])
        def register_skill(self,name,path,**kwargs):
            assert path.is_file()
            skills.append(name)
        @property
        def llm(self): pytest.fail('Notes workflow must not access a nested LLM')
    mod.register(Context())
    assert set(names)==set(yaml.safe_load((root/'plugin.yaml').read_text())['provides_tools'])
    assert skills==['analyze', 'action_items', 'handoff']
    assert 'meeting_analyze' not in names


def test_analysis_history_is_read_only_and_scoped(client,monkeypatch):
    from meeting_notes import runtime
    from meeting_notes.worker import Worker
    from test_core import imported, Adapter
    from test_analysis import save, payload
    svc=runtime.service()
    mid=imported(svc)
    Worker(svc,lambda _:Adapter()).run_once()
    result=save(svc,mid,payload(svc.get(mid)))
    monkeypatch.setattr(runtime,'start',lambda:pytest.fail('History GET started work'))
    response=client.get(f'/api/plugins/meeting_notes/meetings/{mid}/analyses/{result["id"]}')
    assert response.status_code==200
    assert response.json()['id']==result['id']
    assert client.get(f'/api/plugins/meeting_notes/meetings/missing/analyses/{result["id"]}').status_code==400
    assert client.get(f'/api/plugins/meeting_notes/meetings/{mid}').json()['analysis']['id']==result['id']


def test_session_tools_work_without_llm_access(client):
    import json
    from meeting_notes import runtime
    from meeting_notes.worker import Worker
    from test_core import imported, Adapter
    svc=runtime.service()
    mid=imported(svc)
    Worker(svc,lambda _:Adapter()).run_once()
    root=Path(__file__).parents[1]
    spec=importlib.util.spec_from_file_location('meeting_session_tools_test',root/'__init__.py')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    handlers={}
    class Context:
        def register_tool(self,**kwargs): handlers[kwargs['name']]=kwargs['handler']
        def register_skill(self,*args,**kwargs): pass
        @property
        def llm(self): pytest.fail('Session workflow attempted a nested model call')
    mod.register(Context())
    corrected = 'I’ll send café notes — next\u00a0weekend.'
    sid = svc.get(mid)['segments'][0]['id']
    svc.correct(mid, 'text', sid, corrected, 0)
    wire = handlers['meeting_transcript']({'meeting_id':mid})
    assert corrected in wire and '\\u2019' not in wire
    page=json.loads(wire)
    assert page['segments'][0][2] == corrected
    result=json.loads(handlers['meeting_save_notes']({
        'meeting_id':mid,'basis':page['basis'],'items':[{
            'kind':'commitment','text':'Send it.',
            'citations':[{'segment_id':'s0','quote':page['segments'][0][2]}],
            'owner':{'basis':'speaker','segment_id':'s0','name':None},
            'due':{'segment_id':None,'quote':None,'proposed_date':None},
        }]}))
    assert result['status']=='saved' and result['items_saved']==1
    assert svc.get(mid)['analysis']['origin']=='hermes_session'
    saved_basis = result['basis']
    rejected = json.loads(handlers['meeting_save_notes']({
        'meeting_id': mid, 'basis': saved_basis, 'items': [{
            'kind': 'summary', 'text': 'A synthetic claim.',
            'citations': [{'segment_id': 's0', 'quote': 'Private wrong quote'}],
            'owner': {'basis': 'unresolved', 'segment_id': None, 'name': None},
            'due': {'segment_id': None, 'quote': None, 'proposed_date': None},
        }]}))
    assert rejected['failures'] == [{'path': 'items[0].citations[0].quote',
                                     'code': 'quote_not_in_segment', 'source_id': 's0'}]
    assert 'Private' not in json.dumps(rejected)
    assert json.loads(handlers['meeting_transcript']({'meeting_id': mid}))['basis'] == saved_basis
    assert len(svc.get(mid)['analysis_versions']) == 1
