import hashlib
import pytest
from meeting_notes import recovery, worker as worker_module, deletion
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from test_chunking import upload, Recognizer
from test_api import client


def failed_parse(tmp_path, monkeypatch, frames=16000):
    svc=Service(tmp_path)
    svc.configure({**svc.settings(),'max_audio_seconds':1})
    mid,_,_=upload(svc,frames)
    adapter=Recognizer()
    original=worker_module.parse_part
    def rejected(*args,**kwargs): raise ValueError('synthetic parser issue')
    monkeypatch.setattr(worker_module,'parse_part',rejected)
    Worker(svc,lambda _:adapter).run_once()
    monkeypatch.setattr(worker_module,'parse_part',original)
    assert svc.get(mid)['job']['state']=='invalid_response'
    return svc,mid,adapter


def local_worker(svc):
    return Worker(svc,lambda _:pytest.fail('Local recovery must not resolve credentials or create an ASR adapter'))


def test_recover_without_audio_network_or_credentials_and_preserve_raw(tmp_path,monkeypatch):
    svc,mid,adapter=failed_parse(tmp_path,monkeypatch)
    before={p.name:p.read_bytes() for p in (svc.root/'raw').iterdir()}
    for p in (svc.root/'audio').iterdir(): p.unlink()
    assert svc.get(mid)['can_recover_response']
    recovery.request(svc,mid)
    # Durable intent survives a fresh Service/Worker, without the audio files.
    local_worker(Service(svc.root)).run_once()
    assert svc.get(mid)['job']['state']=='completed'
    assert not svc.get(mid)['job']['local_recovery']
    assert {p.name:p.read_bytes() for p in (svc.root/'raw').iterdir()}==before
    assert len(adapter.calls)==1
    with svc.db() as db:
        assert db.execute('select sent from attempts order by rowid desc').fetchone()[0] is None
    with pytest.raises(ValueError):recovery.request(svc,mid)


def test_recovery_never_submits_remaining_chunks(tmp_path,monkeypatch):
    svc,mid,adapter=failed_parse(tmp_path,monkeypatch,32000)
    recovery.request(svc,mid);worker=local_worker(svc);worker.run_once()
    assert svc.get(mid)['job']['state']=='failed'
    assert [c['state'] for c in svc.get(mid)['transcription_chunks']]==['completed','queued']
    assert not worker.run_once() and len(adapter.calls)==1
    svc.retry(mid);Worker(svc,lambda _:adapter).run_once()
    assert svc.get(mid)['job']['state']=='completed' and len(adapter.calls)==2


def test_process_death_after_receipt_requires_explicit_local_recovery(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    svc=Service(tmp_path);mid,_,_=upload(svc,16000)
    # Terminate the process after raw bytes + receipt commit, before parsing or
    # the worker's finally block. Only the synthetic adapter runs in the child.
    script='''
import os, sys
sys.path.insert(0, 'tests')
from test_chunking import Recognizer
from meeting_notes import worker
from meeting_notes.service import Service
worker.parse_part=lambda *a,**k: os._exit(27)
worker.Worker(Service(sys.argv[1]),lambda _: Recognizer()).run_once()
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],cwd=Path(__file__).parents[1],timeout=15)
    assert result.returncode==27
    with svc.db() as db:
        assert db.execute('select status from asr_responses').fetchone()[0]==200
        db.execute('update jobs set lease=0')  # advance past the dead host's lease
    worker=local_worker(svc)
    assert not worker.run_once()
    assert svc.get(mid)['job']['state']=='interrupted_unknown'
    recovery.request(svc,mid)
    assert worker.run_once() and svc.get(mid)['job']['state']=='completed'
    with svc.db() as db:assert db.execute('select count(*) from attempts where sent is not null').fetchone()[0]==1


@pytest.mark.parametrize('damage',['hash','missing','symlink','invalid'])
def test_bad_saved_response_never_falls_back_to_transcription(tmp_path,monkeypatch,damage):
    svc,mid,_=failed_parse(tmp_path,monkeypatch)
    raw=next((svc.root/'raw').iterdir())
    if damage=='missing':raw.unlink()
    elif damage=='symlink':
        other=tmp_path/'elsewhere';other.write_bytes(raw.read_bytes());raw.unlink();raw.symlink_to(other)
    else:
        raw.write_bytes(b'{"text":"broken"}')
        if damage=='invalid':
            with svc.db() as db:db.execute('UPDATE asr_responses SET sha=?',(hashlib.sha256(raw.read_bytes()).hexdigest(),))
    recovery.request(svc,mid);worker=local_worker(svc);worker.run_once()
    assert svc.get(mid)['job']['state']=='invalid_response'
    assert not svc.get(mid)['active'] and not worker.run_once()
    assert not svc.get(mid)['job']['local_recovery']


def test_existing_mismatched_playback_blocks_recovery_until_restored(tmp_path,monkeypatch):
    import wave
    svc,mid,adapter=failed_parse(tmp_path,monkeypatch)
    wav=svc.root/'audio'/f'{mid}.wav'
    original=wav.read_bytes()
    with wave.open(str(wav),'wb') as audio:
        audio.setparams((1,2,16000,0,'NONE','not compressed'))
        audio.writeframes(b'\0\0'*100)
    recovery.request(svc,mid);local_worker(svc).run_once()
    meeting=svc.get(mid)
    assert meeting['job']['state']=='failed' and 'chunk boundaries' in meeting['job']['error']
    assert not meeting['active'] and meeting['can_recover_response']
    with svc.db() as db:assert not db.execute('select * from storage_jobs').fetchall()
    wav.write_bytes(original)
    recovery.request(svc,mid);local_worker(svc).run_once()
    assert svc.get(mid)['job']['state']=='completed' and len(adapter.calls)==1


@pytest.mark.parametrize('status',[401,429,500])
def test_error_response_is_not_recoverable_even_with_transcript_shape(tmp_path,status):
    svc=Service(tmp_path);mid,_,_=upload(svc,16000)
    class Error(Recognizer):
        def transcribe(self,p): return status,super().transcribe(p)[1]
    Worker(svc,lambda _:Error()).run_once()
    assert not svc.get(mid)['can_recover_response']
    with pytest.raises(ValueError,match='HTTP 200'):recovery.request(svc,mid)


def test_legacy_response_without_integrity_record_is_not_guessed(tmp_path,monkeypatch):
    svc,mid,_=failed_parse(tmp_path,monkeypatch)
    with svc.db() as db:db.execute('DELETE FROM asr_responses')
    with pytest.raises(ValueError):recovery.request(svc,mid)


def test_lost_recovery_lease_cannot_publish(tmp_path,monkeypatch):
    svc,mid,_=failed_parse(tmp_path,monkeypatch)
    recovery.request(svc,mid)
    original=recovery.parse_part
    def steal(*args,**kwargs):
        with svc.db() as db:db.execute("UPDATE jobs SET lease=0")
        return original(*args,**kwargs)
    monkeypatch.setattr(recovery,'parse_part',steal)
    local_worker(svc).run_once()
    assert not svc.get(mid)['active']
    with svc.db() as db:assert db.execute('select state from asr_parts').fetchone()[0]=='invalid_response'


def test_recovery_metadata_cascades_on_reviewed_deletion(tmp_path,monkeypatch):
    svc,mid,_=failed_parse(tmp_path,monkeypatch)
    recovery.request(svc,mid);local_worker(svc).run_once()
    review=deletion.review(svc,mid);deletion.remove(svc,mid,review['token'],True)
    with svc.db() as db:
        assert not db.execute('SELECT * FROM asr_responses').fetchall()
        assert not db.execute('SELECT * FROM asr_recovery').fetchall()
        assert not db.execute('pragma foreign_key_check').fetchall()


def test_recovery_api_rejects_unexpected_arguments_without_start(client,monkeypatch):
    from meeting_notes import runtime
    monkeypatch.setattr(runtime,'start',lambda:pytest.fail('Invalid request started worker'))
    assert client.post('/api/plugins/meeting_notes/meetings/unknown/recover-response',json={'retry':True}).status_code==400
    assert client.post('/api/plugins/meeting_notes/meetings/unknown/recover-response').status_code==400


def test_recovery_api_queues_local_only_and_get_never_starts_it(client,monkeypatch):
    from meeting_notes import runtime
    svc,mid,_=failed_parse(runtime.service().root,monkeypatch)
    started=[]
    monkeypatch.setattr(runtime,'start',lambda:started.append(True))
    base=f'/api/plugins/meeting_notes/meetings/{mid}'
    assert client.get(base).json()['can_recover_response'] and not started
    result=client.post(base+'/recover-response')
    assert result.status_code==200 and result.json()['job']['local_recovery']
    assert result.json()['job']['state']=='queued' and started==[True]
    local_worker(svc).run_once()
    assert client.get(base).json()['job']['state']=='completed'


@pytest.mark.parametrize('actor',['analysis_actor','handoff_actor'])
def test_other_profile_context_cannot_request_recovery(tmp_path,monkeypatch,actor):
    svc,mid,_=failed_parse(tmp_path,monkeypatch)
    setattr(svc,actor,'other')
    with pytest.raises(ValueError,match='importing profile'):recovery.request(svc,mid)
