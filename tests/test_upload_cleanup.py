import hashlib
import time
import pytest
from meeting_notes.service import Service
from meeting_notes.upload_cleanup import cleanup, TTL
from meeting_notes import storage


def staged(svc,expired=True):
    uid=svc.begin_upload(4,'Synthetic','2026-11-06T12:00:00-07:00','America/Denver')['id']
    svc.chunk(uid,0,b'test',hashlib.sha256(b'test').hexdigest())
    if expired:
        with svc.db() as db:db.execute('UPDATE uploads SET created=? WHERE id=?',(time.time()-TTL-1,uid))
    return uid


def test_expired_staging_cleanup_preserves_fresh_finalized_and_unrelated_files(tmp_path):
    svc=Service(tmp_path)
    fresh=staged(svc,False)
    complete=staged(svc,False);mid=svc.finalize(complete)['id']
    expired=staged(svc)
    partial=svc.root/'uploads'/f'{expired}.9.{"a"*32}.tmp'
    partial.write_bytes(b'partial')
    stage=svc.root/'audio'/f'{expired}.upload';stage.write_bytes(b'partial')
    unrelated=svc.root/'uploads'/'keep.txt';unrelated.write_bytes(b'keep')
    assert cleanup(svc)==1
    assert not stage.exists() and not partial.exists()
    assert (svc.root/'uploads'/f'{fresh}.0').read_bytes()==b'test'
    assert unrelated.exists() and svc.get(mid)
    with svc.db() as db:
        assert not db.execute('select 1 from uploads where id=?',(expired,)).fetchone()
        assert not db.execute('pragma foreign_key_check').fetchall()
    assert cleanup(svc)==0


def test_expired_finalize_and_chunk_refused(tmp_path):
    svc=Service(tmp_path);uid=staged(svc)
    with pytest.raises(ValueError,match='expired'):svc.finalize(uid)
    with pytest.raises(ValueError,match='expired'):svc.chunk(uid,0,b'test',hashlib.sha256(b'test').hexdigest())


def test_next_upload_cleans_abandoned_uploads_without_worker(tmp_path):
    svc=Service(tmp_path);old=staged(svc)
    staged(svc,False)
    with svc.db() as db:assert not db.execute('select 1 from uploads where id=?',(old,)).fetchone()


def test_symlink_or_unavailable_location_keeps_durable_retry(tmp_path):
    svc=Service(tmp_path/'profile');uid=staged(svc)
    file=svc.root/'uploads'/f'{uid}.0';file.unlink()
    foreign=tmp_path/'foreign';foreign.write_bytes(b'do not remove');file.symlink_to(foreign)
    assert cleanup(svc)==0 and foreign.read_bytes()==b'do not remove'
    with svc.db() as db:assert db.execute('select next_check from upload_cleanup_retry').fetchone()[0]>time.time()
    file.unlink();file.write_bytes(b'test')
    with svc.db() as db:db.execute('update upload_cleanup_retry set next_check=0')
    assert cleanup(svc)==1


def test_pinned_external_location_used_after_settings_change(tmp_path):
    svc=Service(tmp_path/'profile')
    (tmp_path/'disk').mkdir()
    storage.configure(svc,dict(directory=str(tmp_path/'disk'),compress_playback=False,archive_mode='original'))
    uid=staged(svc);root=storage.settings(svc)['managed_path']
    storage.configure(svc,dict(directory='',compress_playback=False,archive_mode='original'))
    assert cleanup(svc)==1
    from pathlib import Path
    assert not (Path(root)/'uploads'/f'{uid}.0').exists()


def test_cleanup_is_bounded_and_defers_while_processing(tmp_path):
    svc=Service(tmp_path)
    from test_chunking import upload
    from meeting_notes.worker import Worker
    upload(svc,16000)
    ids=[staged(svc,False) for _ in range(10)]
    with svc.db() as db:db.execute('update uploads set created=?',(time.time()-TTL-1,))
    claimed=Worker(svc,lambda _:pytest.fail('No ASR')).claim()
    assert claimed and cleanup(svc)==0
    with svc.db() as db:db.execute("update jobs set state='failed',lease=NULL")
    assert cleanup(svc)==8
    assert cleanup(svc)==2


def test_copied_profile_cannot_clean_original_staging(tmp_path):
    import shutil
    svc=Service(tmp_path/'original');uid=staged(svc)
    shutil.copytree(svc.root,tmp_path/'copy')
    copied=Service(tmp_path/'copy')
    assert cleanup(copied)==0
    assert (svc.root/'uploads'/f'{uid}.0').read_bytes()==b'test'
    with copied.db() as db:assert db.execute('select 1 from uploads where id=?',(uid,)).fetchone()


def test_interrupted_cleanup_retries_missing_files_safely(tmp_path,monkeypatch):
    from pathlib import Path
    svc=Service(tmp_path);uid=staged(svc)
    stage=svc.root/'audio'/f'{uid}.upload';stage.write_bytes(b'temporary')
    original=Path.unlink
    def fail_chunk(path,*args,**kwargs):
        if path.name==f'{uid}.0':raise OSError('synthetic interruption')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail_chunk)
    assert cleanup(svc)==0 and not stage.exists()
    monkeypatch.setattr(Path,'unlink',original)
    with svc.db() as db:db.execute('update upload_cleanup_retry set next_check=0')
    assert cleanup(svc)==1
