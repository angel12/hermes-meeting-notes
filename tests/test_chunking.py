import base64
import hashlib
import io
import json
import time
import wave

import pytest

from meeting_notes.service import Service
from meeting_notes.worker import Worker
from meeting_notes.analysis import read_transcript
from test_analysis import payload, save


def upload(svc, frames):
    pcm=b''.join((i%30000).to_bytes(2,'little') for i in range(frames))
    out=io.BytesIO()
    with wave.open(out,'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed')); f.writeframes(pcm)
    data=out.getvalue()
    up=svc.begin_upload(len(data),'Synthetic long meeting','2026-11-05T12:00:00-07:00','America/Denver')['id']
    svc.chunk(up,0,data,hashlib.sha256(data).hexdigest())
    return svc.finalize(up)['id'],data,pcm


class Recognizer:
    def __init__(self): self.calls=[]; self.fail=None; self.empty=set()
    def probe(self): pass
    def transcribe(self,path):
        with wave.open(str(path),'rb') as f:
            frames=f.getnframes(); pcm=f.readframes(frames)
        self.calls.append((frames,pcm))
        n=len(self.calls)
        if self.fail==n: raise TimeoutError('private credential must not be logged')
        if n in self.empty: return 200,json.dumps({'text':'','words':[]}).encode()
        return 200,json.dumps({'text':f'Chunk {n}.','words':[
            {'word':f'Chunk {n}.','start':0,'end':min(frames/16000,.5),'speaker':1,'confidence':0}]}).encode()


@pytest.fixture
def setup(tmp_path):
    svc=Service(tmp_path)
    svc.configure({**svc.settings(),'max_audio_seconds':2})
    adapter=Recognizer()
    return svc,adapter,Worker(svc,lambda _:adapter)


@pytest.mark.parametrize('frames,expected',[(31999,[31999]),(32000,[32000]),(32001,[16001,16000]),(80000,[26667,26667,26666])])
def test_exact_boundaries_cover_every_sample_once(setup,frames,expected):
    svc,adapter,worker=setup
    mid,original,pcm=upload(svc,frames)
    for _ in expected: assert worker.run_once()
    assert not worker.run_once()
    assert [c[0] for c in adapter.calls]==expected
    assert b''.join(c[1] for c in adapter.calls)==pcm
    result=svc.get(mid)
    assert result['job']['state']=='completed'
    assert len(svc.list())==1
    assert all(c['state']=='completed' for c in result['transcription_chunks'])
    with svc.db() as db:
        assert db.execute('select count(*) from transcripts').fetchone()[0]==1
        row=db.execute('select audio from meetings where id=?',(mid,)).fetchone()
        assert open(row[0],'rb').read()==original
    assert not list((svc.root/'audio').glob('*.part.wav'))


def test_timeline_speakers_citations_and_original_playback(setup):
    svc,adapter,worker=setup
    mid,_,pcm=upload(svc,96000)
    assert worker.run_once()
    assert svc.get(mid)['active'] is None  # no partial analysis presented as complete
    with pytest.raises(ValueError): read_transcript(svc,mid)
    assert worker.run_once() and worker.run_once()
    result=svc.get(mid)
    assert [s['start'] for s in result['segments']]==[0,2,4]
    assert [s['label'] for s in result['segments']]==['1','2','3']
    assert [s['chunk']['raw_speaker'] for s in result['segments']]==['1','1','1']
    assert len({s['id'] for s in result['segments']})==3
    notes=save(svc,mid,payload(result))
    assert notes['sources'][result['segments'][0]['id']]['chunk']['index']==1
    hashes={p:p.read_bytes() for p in (svc.root/'raw').iterdir()}
    updated=svc.correct(mid,'merge','2','1',0)
    assert updated['segments'][1]['participant']=='1'
    svc.correct(mid,'undo','',updated['corrections'][-1]['id'],1)
    assert svc.get(mid)['segments'][1]['participant']=='2'
    for path,data in hashes.items(): assert path.read_bytes()==data
    clip=svc.audio_window(mid,1.5,1)
    with wave.open(io.BytesIO(base64.b64decode(clip['base64'])),'rb') as f:
        assert f.readframes(16000)==pcm[24000*2:40000*2]


def test_timeout_keeps_completed_parts_and_requires_explicit_retry(setup):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,96000)
    worker.run_once(); adapter.fail=2; worker.run_once()
    before=svc.get(mid)
    assert before['job']['state']=='interrupted_unknown'
    assert [p['state'] for p in before['transcription_chunks']]==['completed','interrupted_unknown','queued']
    assert 'private credential' not in json.dumps(before)
    assert not worker.run_once() and len(adapter.calls)==2
    # Reusing a part also reuses its endpoint/model/limit snapshot, not new global settings.
    svc.configure({**svc.settings(),'model':'different','max_audio_seconds':1})
    seen=[]
    resumed=Worker(svc,lambda settings:(seen.append(settings),adapter)[1])
    svc.retry(mid); resumed.run_once(); resumed.run_once()
    assert svc.get(mid)['job']['state']=='completed'
    assert [c[0] for c in adapter.calls]==[32000,32000,32000,32000]
    assert all(s['max_audio_seconds']==2 and s['model']!='different' for s in seen)
    with svc.db() as db:
        assert db.execute('select count(*) from asr_parts where state=?',('completed',)).fetchone()[0]==3
        assert db.execute('select count(*) from attempts where sent is not null').fetchone()[0]==4


def test_interruption_between_parts_survives_new_service_without_resubmit(setup):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000)
    worker.run_once()
    claim=worker.claim()
    with svc.db() as db: db.execute('update jobs set lease=? where id=?',(time.time()-1,claim['id']))
    reopened=Service(svc.root); resumed=Worker(reopened,lambda _:adapter)
    assert not resumed.run_once()
    assert len(adapter.calls)==1
    reopened.retry(mid); resumed.run_once()
    assert len(adapter.calls)==2 and reopened.get(mid)['job']['state']=='completed'


def test_late_response_from_expired_lease_cannot_publish_part(setup):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000)
    worker.run_once()
    original=adapter.transcribe
    def late(path):
        result=original(path)
        with svc.db() as db: db.execute('update jobs set lease=?',(time.time()-1,))
        return result
    adapter.transcribe=late
    worker.run_once(); worker.recover()
    assert svc.get(mid)['active'] is None
    assert [c['state'] for c in svc.get(mid)['transcription_chunks']]==['completed','interrupted_unknown']
    assert len(list((svc.root/'raw').glob('*.json')))==2
    adapter.transcribe=original
    svc.retry(mid); worker.run_once()
    assert svc.get(mid)['job']['state']=='completed' and len(adapter.calls)==3


def test_finalization_retry_reuses_all_parts_without_model_or_key_access(setup,monkeypatch):
    import meeting_notes.worker as module
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000)
    worker.run_once()
    real=module.publish
    def fail(*_): raise OSError('private path')
    monkeypatch.setattr(module,'publish',fail)
    worker.run_once()
    assert svc.get(mid)['job']['state']=='failed'
    assert all(c['state']=='completed' for c in svc.get(mid)['transcription_chunks'])
    monkeypatch.setattr(module,'publish',real)
    svc.retry(mid)
    assert Worker(svc,lambda _:pytest.fail('Finalization must not resolve ASR credentials')).run_once()
    assert svc.get(mid)['job']['state']=='completed' and len(adapter.calls)==2


@pytest.mark.parametrize('response', [b'broken JSON',b'{"words":[{"word":"x","start":0,"end":1}]}',b'{"words":[{"word":"x","start":0,"end":99,"speaker":1}]}'])
def test_invalid_chunk_retains_raw_and_does_not_publish_partial_meeting(setup,response):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000)
    worker.run_once()
    adapter.transcribe=lambda _: (200,response)
    worker.run_once()
    result=svc.get(mid)
    assert result['job']['state']=='invalid_response' and result['active'] is None
    assert result['transcription_chunks'][0]['state']=='completed'
    assert response in [p.read_bytes() for p in (svc.root/'raw').glob('*.json')]


def test_empty_chunk_is_preserved_and_reported_without_claiming_silence(setup):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,96000); adapter.empty={2}
    for _ in range(3): worker.run_once()
    result=svc.get(mid)
    assert result['job']['state']=='completed'
    assert [s['start'] for s in result['segments']]==[0,4]
    assert result['transcription_chunks'][1]['recognized_segments']==0
    assert read_transcript(svc,mid)['transcription_chunks'][1]['recognized_segments']==0


def test_legacy_settings_default_limit_and_validation(tmp_path):
    svc=Service(tmp_path)
    legacy={'endpoint':'http://asr.test/v1','model':'test','timeout':600}
    with svc.db() as db: db.execute('insert into settings values(1,?)',(json.dumps(legacy),))
    assert svc.settings()['max_audio_seconds']==3600
    for limit in (0,-1,86401,True,1.5,'60'):
        with pytest.raises(ValueError): svc.configure({**legacy,'max_audio_seconds':limit})
    svc.configure({**legacy,'max_audio_seconds':120})
    svc.configure(legacy)  # older clients must not reset an existing limit
    assert svc.settings()['max_audio_seconds']==120


def test_replan_keeps_completed_audio_and_applies_current_connection_and_limit(setup):
    svc,adapter,worker=setup
    svc.configure({**svc.settings(),'max_audio_seconds':3})
    mid,_,pcm=upload(svc,144000)
    worker.run_once();adapter.fail=2;worker.run_once()
    with svc.db() as db:
        original=dict(db.execute("select * from asr_parts where state='completed'").fetchone())
    raw=(svc.root/'raw'/original['raw']).read_bytes()
    settings={**svc.settings(),'endpoint':'http://replacement.test/v1','timeout':900,'max_audio_seconds':1}
    svc.configure(settings)
    result=svc.retry(mid,use_current_settings=True)
    assert len(result['transcription_chunks'])==7
    seen=[]
    resumed=Worker(svc,lambda s:(seen.append(s),adapter)[1])
    for _ in range(6): assert resumed.run_once()
    assert svc.get(mid)['job']['state']=='completed'
    assert len(adapter.calls)==8
    assert b''.join(c[1] for i,c in enumerate(adapter.calls) if i!=1)==pcm
    assert all(c[0]<=16000 for c in adapter.calls[2:])
    assert all(s==settings for s in seen)
    with svc.db() as db:
        saved=dict(db.execute('select * from asr_parts where job=? and idx=?',(original['job'],original['idx'])).fetchone())
        assert saved==original
        assert db.execute("select count(*) from asr_parts where state='superseded'").fetchone()[0]==2
        records=[json.loads(r[0]) for r in db.execute('select settings from asr_attempt_settings order by rowid')]
        assert records[0]['max_audio_seconds']==3 and records[-1]==settings
    assert (svc.root/'raw'/original['raw']).read_bytes()==raw


def test_replan_refuses_mixed_models_and_active_jobs(setup):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000)
    worker.run_once();adapter.fail=2;worker.run_once()
    svc.configure({**svc.settings(),'model':'different'})
    before=svc.get(mid)
    with pytest.raises(ValueError,match='original model'): svc.retry(mid,use_current_settings=True)
    assert svc.get(mid)==before
    svc.retry(mid)
    with pytest.raises(ValueError,match='stopped jobs'): svc.retry(mid,use_current_settings=True)
    with pytest.raises(ValueError,match='boolean'): svc.retry(mid,use_current_settings='true')


def test_stale_response_cannot_restore_superseded_part_after_replan(setup):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000)
    worker.run_once()
    original=adapter.transcribe
    def late(path):
        result=original(path)
        with svc.db() as db: db.execute('update jobs set lease=?',(time.time()-1,))
        worker.recover()
        svc.configure({**svc.settings(),'max_audio_seconds':1})
        svc.retry(mid,use_current_settings=True)
        return result
    adapter.transcribe=late;worker.run_once()
    assert svc.get(mid)['job']['state']=='queued'
    with svc.db() as db:
        assert db.execute("select count(*) from asr_parts where state='superseded'").fetchone()[0]==1
        assert db.execute('select outcome from attempts order by rowid desc limit 1').fetchone()[0]=='interrupted_unknown'
    adapter.transcribe=original
    worker.run_once();worker.run_once()
    assert svc.get(mid)['job']['state']=='completed'


def test_replan_retry_api_validates_flag_and_keeps_completed_parts(setup,monkeypatch):
    import importlib.util
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000)
    worker.run_once();adapter.fail=2;worker.run_once()
    svc.configure({**svc.settings(),'max_audio_seconds':1})
    spec=importlib.util.spec_from_file_location('replan_api',Path(__file__).parents[1]/'dashboard/plugin_api.py')
    api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
    monkeypatch.setattr(api.runtime,'service',lambda:svc)
    started=[];monkeypatch.setattr(api.runtime,'start',lambda:started.append(True))
    app=FastAPI();app.include_router(api.router);client=TestClient(app)
    assert client.post(f'/meetings/{mid}/retry',json={'use_current_settings':'yes'}).status_code==400
    assert not started
    response=client.post(f'/meetings/{mid}/retry',json={'use_current_settings':True})
    assert response.status_code==200 and started==[True]
    assert len(response.json()['transcription_chunks'])==3
    assert response.json()['transcription_chunks'][0]['state']=='completed'


def test_abandoned_temp_cleanup_preserves_originals_fresh_and_active_files(setup):
    import os
    from meeting_notes.audio import cleanup_temporary_audio
    svc,_,_=setup
    attempt='a'*32; meeting='b'*32; abandoned='c'*32
    names=[attempt+'.part.wav',meeting+'.prepare-old.wav',abandoned+'.part.wav',abandoned+'.prepare-old.wav',
           meeting+'.wav','original.wav','f'*64,'d'*32+'.part.wav']
    for name in names:
        p=svc.root/'audio'/name;p.write_bytes(b'preserved audio')
        os.utime(p,(time.time()-90000,time.time()-90000))
    fresh=svc.root/'audio'/names[-1];os.utime(fresh,None)
    cleanup_temporary_audio(svc.root,{attempt},{meeting})
    assert not (svc.root/'audio'/names[2]).exists() and not (svc.root/'audio'/names[3]).exists()
    for name in names[:2]+names[4:]: assert (svc.root/'audio'/name).read_bytes()==b'preserved audio'


def test_changed_prepared_length_stops_without_resending_completed_audio(setup):
    svc,adapter,worker=setup
    mid,_,_=upload(svc,64000);worker.run_once()
    with wave.open(str(svc.root/'audio'/f'{mid}.wav'),'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*63000)
    worker.run_once()
    assert len(adapter.calls)==1
    assert svc.get(mid)['job']['state']=='failed'
    assert 'saved chunk boundaries' in svc.get(mid)['job']['error']
    assert svc.get(mid)['transcription_chunks'][0]['state']=='completed'
