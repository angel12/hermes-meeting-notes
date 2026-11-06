import base64
import hashlib
import io
import wave

import pytest

from meeting_notes.service import Service
from meeting_notes.worker import Worker
from test_core import Adapter
from test_analysis import payload, save
from meeting_notes.analysis import read_transcript


@pytest.fixture
def recordings(tmp_path):
    svc = Service(tmp_path)
    ids = []
    for i in (1,2):
        output = io.BytesIO()
        with wave.open(output,'wb') as f:
            f.setparams((1,2,16000,0,'NONE','not compressed'))
            f.writeframes(i.to_bytes(2,'little')*16000*4)
        data = output.getvalue()
        upload = svc.begin_upload(len(data),f'Part {i}','2026-11-05T12:00:00-07:00','America/Denver')['id']
        svc.chunk(upload,0,data,hashlib.sha256(data).hexdigest())
        ids.append(svc.finalize(upload)['id'])
        Worker(svc,lambda _:Adapter()).run_once()
    return svc, ids


def body(svc,ids):
    return dict(request_id='synthetic-combine-request',title='Whole meeting',date='2026-11-05T12:00:00-07:00',timezone='America/Denver',
                sources=[{k:svc.get(i)[k] for k in ('id','active','revision')} for i in ids])


def test_combine_snapshot_sources_names_undo_and_notes(recordings):
    svc, ids = recordings
    originals = {p:p.read_bytes() for folder in ('audio','raw') for p in (svc.root/folder).iterdir()}
    first = svc.get(ids[0])
    svc.correct(ids[0],'merge','3','1',0)
    svc.correct(ids[0],'rename','1','Sam',1)
    svc.correct(ids[1],'rename','1','Sam',0)
    svc.correct(ids[0],'text',first['segments'][0]['id'],'I will send the draft.',2)
    mid = svc.combine(**body(svc,ids))['id']
    combined = svc.get(mid)
    assert combined['job']['state'] == 'completed'
    assert len(combined['recordings']) == 2
    assert combined['recordings'][1]['offset'] == 4
    a,b,c,d,*_ = combined['segments']
    assert a['text'] == 'I will send the draft.'
    assert a['participant'] == c['participant'] != d['participant']
    assert a['name'] == d['name'] == 'Sam'  # same name never establishes identity
    assert d['start'] == 4
    assert d['source']['start'] == 0
    assert d['source']['meeting_id'] == ids[1]
    assert a['source']['segment_id'] == first['segments'][0]['id']
    assert len({s['id'] for s in combined['segments']}) == 6
    assert not ({s['id'] for s in combined['segments']} & {s['id'] for s in first['segments']})
    notes = save(svc,mid,payload(combined))
    assert notes['sources'][a['id']]['source'] == a['source']
    assert read_transcript(svc,mid)['recordings'][1]['first_source_id'] == 's3'
    svc.correct(ids[0],'text',a['source']['segment_id'],'Changed original.',3)
    assert svc.get(mid)['segments'][0]['text'] == a['text']
    merged = svc.correct(mid,'merge',d['participant'],a['participant'],0)
    assert merged['segments'][3]['participant'] == a['participant']
    svc.correct(mid,'undo','',merged['corrections'][-1]['id'],1)
    assert svc.get(mid)['segments'][3]['participant'] == d['participant']
    assert svc.get(ids[1])['revision'] == 1
    for path,data in originals.items(): assert path.read_bytes() == data
    assert not Worker(svc,lambda _:pytest.fail('Combination must not call ASR')).run_once()


def test_combined_playback_crosses_boundary_and_preserves_order(recordings):
    svc,ids = recordings
    mid = svc.combine(**body(svc,ids))['id']
    result = svc.audio_window(mid,3.5,1)
    with wave.open(io.BytesIO(base64.b64decode(result['base64'])),'rb') as f:
        assert f.getnframes() == 16000
        assert f.readframes(8000) == b'\x01\x00'*8000
        assert f.readframes(8000) == b'\x02\x00'*8000
    reverse = body(svc,ids[::-1]); reverse['request_id']='reverse'
    rev = svc.combine(**reverse)['id']
    with wave.open(io.BytesIO(base64.b64decode(svc.audio_window(rev,0,.1)['base64'])),'rb') as f:
        assert f.readframes(1600) == b'\x02\x00'*1600
    assert svc.get(rev)['segments'][0]['source']['meeting_id'] == ids[1]


def test_combine_idempotence_and_conflict(recordings):
    from concurrent.futures import ThreadPoolExecutor
    svc,ids = recordings
    request = body(svc,ids)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _:svc.combine(**request),range(2)))
    assert results[0] == results[1]
    assert len(svc.list()) == 3
    svc.correct(ids[0],'rename','1','Changed',0)
    assert svc.combine(**request) == results[0]  # retry returns original outcome
    with pytest.raises(ValueError,match='different combination'):
        svc.combine(**{**request,'title':'Different'})


@pytest.mark.parametrize('defect',['duplicate','stale','missing','pending','nested','empty','bad_audio','bad_date'])
def test_combine_rejects_invalid_sources_atomically(recordings,defect):
    svc,ids = recordings
    request = body(svc,ids)
    if defect == 'duplicate': request['sources'][1]=request['sources'][0]
    elif defect == 'stale': svc.correct(ids[0],'rename','1','Changed',0)
    elif defect == 'missing': request['sources'][0]['id']='missing'
    elif defect == 'pending':
        with svc.db() as db: db.execute("UPDATE jobs SET state='queued' WHERE meeting=?",(ids[0],))
    elif defect == 'nested':
        mid=svc.combine(**request)['id']; request=body(svc,[mid,ids[1]]); request['request_id']='nested'
    elif defect == 'empty': request['sources']=[]
    elif defect == 'bad_audio': (svc.root/'audio'/f'{ids[0]}.wav').unlink()
    elif defect == 'bad_date': request['date']='2026-11-05'
    before=len(svc.list())
    with pytest.raises(ValueError): svc.combine(**request)
    assert len(svc.list()) == before


@pytest.mark.parametrize('start,duration',[(float('nan'),1),(0,float('inf')),(-1,1),(0,31)])
def test_audio_window_rejects_nonfinite_or_unbounded_requests(recordings,start,duration):
    svc,ids=recordings
    with pytest.raises(ValueError): svc.audio_window(ids[0],start,duration)


def test_combined_api_does_not_start_worker(recordings,monkeypatch):
    import importlib.util
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    svc,ids=recordings
    spec=importlib.util.spec_from_file_location('composition_api',Path(__file__).parents[1]/'dashboard/plugin_api.py')
    api=importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
    monkeypatch.setattr(api.runtime,'service',lambda:svc)
    monkeypatch.setattr(api.runtime,'start',lambda:pytest.fail('Combine submitted work'))
    app=FastAPI(); app.include_router(api.router)
    client=TestClient(app)
    response=client.post('/meetings/combine',json=body(svc,ids))
    assert response.status_code==200
    mid=response.json()['id']
    assert client.get(f'/meetings/{mid}').json()['recordings'][0]['meeting_id']==ids[0]
    assert client.get(f'/meetings/{mid}/audio?start=3.5&duration=1').status_code==200
