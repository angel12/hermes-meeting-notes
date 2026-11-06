import hashlib
import json
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from meeting_notes.transcript import normalize


def raw():
    return {'duration':3,'language':'','task':'transcribe','text':'Synthetic fixture', 'words':[
        {'word':'I will send it.','start':0,'end':1,'speaker':1,'confidence':0},
        {'word':'Thank you.','start':1,'end':2,'speaker':2,'confidence':0},
        {'word':'Tomorrow.','start':2,'end':3,'speaker':3,'confidence':0}]}


@pytest.fixture
def svc(tmp_path):
    return Service(tmp_path)


def imported(svc):
    import io
    b=io.BytesIO()
    with wave.open(b,'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed'))
        f.writeframes(b'\0\0'*1600)
    data=b.getvalue()
    u=svc.begin_upload(len(data),'Synthetic meeting','2026-11-05T12:00:00-07:00','America/Denver')['id']
    sha=hashlib.sha256(data).hexdigest()
    svc.chunk(u,0,data,sha)
    svc.chunk(u,0,data,sha)
    return svc.finalize(u)['id']


class Adapter:
    calls=0
    def probe(self): pass
    def transcribe(self,path):
        self.calls+=1
        return 200,json.dumps(raw()).encode()


def test_upload_and_finalize_idempotent(svc):
    assert imported(svc)==imported(svc)
    assert len(svc.list())==1


def test_hash_and_missing_chunks(svc):
    u=svc.begin_upload(4,'Test','2026-11-05T00:00:00Z','UTC')['id']
    with pytest.raises(ValueError): svc.chunk(u,0,b'abcd','wrong')
    with pytest.raises(ValueError): svc.finalize(u)


def test_worker_corrections_and_raw_immutable(svc):
    mid=imported(svc)
    adapter=Adapter()
    assert Worker(svc,lambda _:adapter).run_once()
    result=svc.get(mid)
    assert result['job']['state']=='completed'
    original=list((svc.root/'raw').glob('*.json'))[0].read_bytes()
    result=svc.correct(mid,'merge','3','1',0)
    assert [s['participant'] for s in result['segments']]==['1','2','1']
    merge=result['corrections'][0]['id']
    result=svc.correct(mid,'speaker',result['segments'][2]['id'],'2',1)
    assert result['segments'][2]['participant']=='2'
    result=svc.correct(mid,'undo','',merge,2)
    assert result['segments'][2]['participant']=='2'
    assert list((svc.root/'raw').glob('*.json'))[0].read_bytes()==original
    assert not Worker(svc,lambda _:adapter).run_once()
    assert adapter.calls==1


def test_expired_lease_never_resubmits(svc):
    mid=imported(svc)
    w=Worker(svc,lambda _:Adapter())
    job=w.claim()
    with svc.db() as db: db.execute('UPDATE jobs SET lease=?',(time.time()-1,))
    assert not w.run_once()
    assert svc.get(mid)['job']['state']=='interrupted_unknown'
    svc.retry(mid)
    assert w.run_once()
    with svc.db() as db: assert db.execute('SELECT count(*) FROM attempts').fetchone()[0]==2


def test_single_claim(svc):
    imported(svc)
    with ThreadPoolExecutor(2) as pool:
        claims=list(pool.map(lambda _:Worker(svc,lambda _:None).claim(),range(2)))
    assert sum(c is not None for c in claims)==1


def test_timeout_unknown(svc):
    mid=imported(svc)
    class Timeout(Adapter):
        def transcribe(self,path): raise TimeoutError('secret must not leak')
    Worker(svc,lambda _:Timeout()).run_once()
    assert svc.get(mid)['job']['state']=='interrupted_unknown'
    assert 'secret' not in json.dumps(svc.get(mid))


def test_missing_speakers_raw_preserved(svc):
    mid=imported(svc)
    class Invalid(Adapter):
        def transcribe(self,path): return 200,b'{"words":[{"word":"hello","start":0,"end":1}]}'
    Worker(svc,lambda _:Invalid()).run_once()
    assert svc.get(mid)['job']['state']=='invalid_response'
    assert len(list((svc.root/'raw').glob('*.json')))==1


def test_normalize_rejects_bad_timestamps():
    data=raw()
    data['words'][0]['start']=float('nan')
    with pytest.raises(ValueError): normalize(data,'t')


def test_stale_correction_refused(svc):
    mid=imported(svc)
    Worker(svc,lambda _:Adapter()).run_once()
    svc.correct(mid,'rename','1','Alex',0)
    with pytest.raises(ValueError): svc.correct(mid,'merge','3','1',0)


def test_lost_lease_cannot_publish_response(svc):
    mid=imported(svc)
    class LostLease(Adapter):
        def transcribe(self,path):
            with svc.db() as db: db.execute('UPDATE jobs SET lease=?',(time.time()-1,))
            return super().transcribe(path)
    worker=Worker(svc,lambda _:LostLease())
    worker.run_once()
    worker.recover()
    assert svc.get(mid)['active'] is None
    assert svc.get(mid)['job']['state']=='interrupted_unknown'
    assert len(list((svc.root/'raw').glob('*.json')))==1


def test_out_of_order_chunks_and_conflict(svc,monkeypatch):
    import meeting_notes.service as service_module
    monkeypatch.setattr(service_module,'CHUNK',4)
    u=svc.begin_upload(6,'Test','2026-11-05T00:00:00Z','UTC')['id']
    svc.chunk(u,1,b'ef',hashlib.sha256(b'ef').hexdigest())
    svc.chunk(u,0,b'abcd',hashlib.sha256(b'abcd').hexdigest())
    with pytest.raises(ValueError): svc.chunk(u,0,b'wxyz',hashlib.sha256(b'wxyz').hexdigest())
    result=svc.finalize(u)
    assert result['sha256']==hashlib.sha256(b'abcdef').hexdigest()
    assert svc.finalize(u)['id']==result['id']
