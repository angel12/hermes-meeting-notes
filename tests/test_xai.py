"""Synthetic host broker and raw-transcript contracts; no live credentials."""
import json
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path
import pytest

from meeting_notes.asr import XaiOAuth, SetupError
from meeting_notes.chunking import parse_part
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from meeting_notes import connections
from test_chunking import upload


def raw(speaker=0):
    return dict(text='Synthetic speech.',words=[dict(text='Synthetic speech.',start=0,end=.5,speaker=speaker)])


@pytest.fixture
def broker(monkeypatch):
    mod=ModuleType('hermes_cli.plugin_provider_requests')
    mod.provider_request_capabilities=lambda:dict(providers=['xai-oauth'],multipart=True)
    mod.ProviderNotSignedIn=type('ProviderNotSignedIn',(RuntimeError,),{})
    monkeypatch.setitem(sys.modules,'hermes_cli',ModuleType('hermes_cli'))
    monkeypatch.setitem(sys.modules,mod.__name__,mod)
    return mod


def setup(tmp_path):
    svc=Service(tmp_path)
    state=svc.connections()
    state=connections.save(svc,'xai',{**state['connections']['xai'],'max_audio_seconds':1},state['revision'])
    connections.select(svc,'xai',state['revision'])
    return svc


def test_request_shape_keeps_raw_bytes_and_speaker_zero(tmp_path,broker):
    svc=setup(tmp_path); path=tmp_path/'test.wav'; path.write_bytes(b'RIFFsynthetic')
    response=json.dumps(raw()).encode(); calls=[]
    def send(provider,method,url,**kwargs):
        calls.append(provider)
        assert (provider,method,url)==('xai-oauth','POST','https://api.x.ai/v1/stt')
        assert kwargs['data']=={'model':'grok-voice-transcribe-2.0','diarize':'true'}
        assert kwargs['files']['file'][1].read()==b'RIFFsynthetic'
        assert 'headers' not in kwargs  # plugin never receives authentication
        return SimpleNamespace(status=200,body=response)
    broker.credentialed_provider_request=send
    adapter=XaiOAuth(svc.settings(),lambda:None); adapter.probe()
    assert adapter.transcribe(path)==(200,response) and calls==['xai-oauth']
    assert parse_part(response,'t',1,False,'xai_oauth')[0]['label']=='0'


@pytest.mark.parametrize('speaker',[None,True,'0',{},[]])
def test_actual_word_diarization_is_required(speaker):
    with pytest.raises(ValueError): parse_part(json.dumps(raw(speaker)).encode(),'t',1,False,'xai_oauth')


def test_timestamps_and_text_are_validated():
    for key,value in [('start',-1),('end',float('nan')),('text',None),('end',5)]:
        data=raw(); data['words'][0][key]=value
        with pytest.raises(ValueError): parse_part(json.dumps(data).encode(),'t',1,False,'xai_oauth')


def test_interrupted_chunk_explicit_retry_reuses_completed_response(tmp_path,broker):
    svc=setup(tmp_path); mid,_,_=upload(svc,32000); calls=[]
    response=json.dumps(raw()).encode()
    def send(*args,**kwargs):
        calls.append(True)
        if len(calls)==2: raise TimeoutError('private-sentinel')
        return SimpleNamespace(status=200,body=response)
    broker.credentialed_provider_request=send
    worker=Worker(svc,lambda s:XaiOAuth(s,lambda:None))
    worker.run_once(); worker.run_once()
    result=svc.get(mid)
    assert result['job']['state']=='interrupted_unknown'
    assert 'private-sentinel' not in json.dumps(result)
    assert not worker.run_once() and len(calls)==2
    svc.retry(mid); worker.run_once()
    assert svc.get(mid)['job']['state']=='completed' and len(calls)==3
    assert len({s['label'] for s in svc.get(mid)['segments']})==2
    with svc.db() as db:
        for row in db.execute("select raw from asr_parts where state='completed'"):
            assert (svc.root/'raw'/row[0]).read_bytes()==response


@pytest.mark.parametrize('status',[401,402,403,429,307])
def test_rejections_never_resend_or_change_provider(tmp_path,broker,status):
    svc=setup(tmp_path); mid,_,_=upload(svc,16000); calls=[]
    def send(*args,**kwargs):
        calls.append(args[0]); return SimpleNamespace(status=status,body=b'{"error":"synthetic"}')
    broker.credentialed_provider_request=send
    worker=Worker(svc,lambda s:XaiOAuth(s,lambda:None))
    worker.run_once(); assert not worker.run_once()
    assert calls==['xai-oauth'] and svc.get(mid)['job']['state']=='interrupted_unknown'
    assert svc.connections()['active']=='xai'


def test_missing_sign_in_is_safe_setup_error(tmp_path,broker):
    svc=setup(tmp_path); mid,_,_=upload(svc,16000)
    def send(*args,**kwargs): raise broker.ProviderNotSignedIn('secret-sentinel')
    broker.credentialed_provider_request=send
    Worker(svc,lambda s:XaiOAuth(s,lambda:None)).run_once()
    result=svc.get(mid)
    assert result['job']['state']=='failed' and 'Sign in to xAI' in result['job']['error']
    assert 'secret-sentinel' not in json.dumps(result)


def test_missing_diarization_preserves_raw_without_publication(tmp_path,broker):
    svc=setup(tmp_path); mid,_,_=upload(svc,16000)
    response=json.dumps(raw(None)).encode()
    broker.credentialed_provider_request=lambda *a,**k:SimpleNamespace(status=200,body=response)
    Worker(svc,lambda s:XaiOAuth(s,lambda:None)).run_once()
    result=svc.get(mid)
    assert result['job']['state']=='invalid_response' and not result['active']
    with svc.db() as db: path=db.execute('select raw from attempts').fetchone()[0]
    assert Path(path).read_bytes()==response
