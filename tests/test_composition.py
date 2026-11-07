import base64
import hashlib
import io
import json
import wave
from pathlib import Path
from meeting_notes import storage

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
    starts = []
    monkeypatch.setattr(api.runtime, 'start', lambda: starts.append(True))
    request = {'confirm': True, 'settings': storage.settings(svc)}
    assert client.post(f'/meetings/{mid}/organize-audio', json={}).status_code == 400
    organized = client.post(f'/meetings/{mid}/organize-audio', json=request)
    assert organized.status_code == 200
    assert organized.json()['job']['state'] == 'queued'
    assert client.post(f'/meetings/{mid}/organize-audio', json=request).json() == organized.json()
    assert len(starts) == 2
    assert storage.run_once(svc)
    assert client.get(f'/meetings/{mid}/audio?start=3.5&duration=1').status_code == 200


def test_owned_audio_survives_source_deletion_and_cleans_up(recordings):
    from pathlib import Path
    from meeting_notes import deletion, storage
    svc, ids = recordings
    request = {**body(svc, ids), 'own_audio': True}
    old_audio = set((svc.root/'audio').iterdir())
    with svc.db() as db:
        names = [Path(db.execute('SELECT audio FROM meetings WHERE id=?', (i,)).fetchone()[0]).name for i in ids]
    mid = svc.combine(**request)['id']
    assert storage.status(svc, mid)['job']['state'] == 'queued'
    assert storage.run_once(svc)
    status = storage.status(svc, mid)
    folder = Path(status['directory'])
    assert status['owns_audio']
    assert status['files'] == [f'{i:02d}-{name}' for i, name in enumerate(names, 1)]
    assert not any(p.exists() for p in old_audio)
    expected = svc.audio_window(mid, 3.5, 1)
    assert svc.combine(**request)['id'] == mid
    for source in ids:
        assert svc.audio_window(source, 0, .1)['base64']
        preview = deletion.review(svc, source)
        assert not preview['blockers']
        deletion.remove(svc, source, preview['token'], True)
        assert svc.audio_window(mid, 3.5, 1) == expected
    assert svc.get(mid)['segments']
    assert all((folder/name).exists() for name in status['files'])
    deletion.remove(svc, mid, deletion.review(svc, mid)['token'], True)
    assert not folder.exists()
    with svc.db() as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_organize_existing_preserves_other_combinations_and_storage_moves(recordings):
    from meeting_notes import deletion, storage
    from meeting_notes.composition import organize_audio
    svc, ids = recordings
    legacy = svc.combine(**body(svc, ids))['id']
    request = {**body(svc, ids[::-1]), 'request_id': 'owned', 'own_audio': True}
    owned = svc.combine(**request)['id']
    assert storage.run_once(svc)
    expected = svc.audio_window(owned, 3.5, 1)
    assert deletion.review(svc, ids[0])['dependencies'] == [{'id': legacy, 'title': 'Whole meeting'}]
    organize_audio(svc, legacy)
    assert storage.run_once(svc)
    for source in ids:
        storage.enqueue(svc, source)
        assert storage.run_once(svc)
        assert svc.audio_window(owned, 3.5, 1) == expected
        deletion.remove(svc, source, deletion.review(svc, source)['token'], True)
    deletion.remove(svc, legacy, deletion.review(svc, legacy)['token'], True)
    assert svc.audio_window(owned, 3.5, 1) == expected
    deletion.remove(svc, owned, deletion.review(svc, owned)['token'], True)
    assert not [p for p in (svc.root/'audio').rglob('*') if p.is_file()]


def test_organize_failed_copy_keeps_sources_and_can_retry(recordings, monkeypatch):
    from meeting_notes import storage
    from meeting_notes.composition import organize_audio
    svc, ids = recordings
    mid = svc.combine(**body(svc, ids))['id']
    expected = svc.audio_window(mid, 3.5, 1)
    original_files = {p: p.read_bytes() for p in (svc.root/'audio').iterdir()}
    copy = storage.copy_verified
    count = 0
    def fail(source, target):
        nonlocal count
        count += 1
        copy(source, target)
        if count == 3:
            raise OSError('Disk full')
    monkeypatch.setattr(storage, 'copy_verified', fail)
    organize_audio(svc, mid)
    assert storage.run_once(svc)
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    assert svc.audio_window(mid, 3.5, 1) == expected
    assert {p: p.read_bytes() for p in (svc.root/'audio').iterdir()} == original_files
    monkeypatch.setattr(storage, 'copy_verified', copy)
    organize_audio(svc, mid)
    assert storage.run_once(svc)
    assert storage.status(svc, mid)['owns_audio']
    assert svc.audio_window(mid, 3.5, 1) == expected


def test_organize_rejects_active_work_and_symlink(recordings, tmp_path):
    from meeting_notes.composition import organize_audio
    svc, ids = recordings
    mid = svc.combine(**body(svc, ids))['id']
    with svc.db() as db:
        db.execute("UPDATE jobs SET state='preparing' WHERE meeting=?", (ids[0],))
    organize_audio(svc, mid)
    assert not storage.run_once(svc)
    assert storage.status(svc, mid)['job']['state'] == 'queued'
    with svc.db() as db:
        db.execute("UPDATE jobs SET state='completed' WHERE meeting=?", (ids[0],))
    path = svc.root/'audio'/f'{ids[0]}.wav'
    target = tmp_path/'outside.wav'
    path.rename(target)
    path.symlink_to(target)
    assert storage.run_once(svc)
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    assert target.exists()


def test_background_move_allows_writes_and_playback(recordings, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    svc, ids = recordings
    mid = svc.combine(**{**body(svc, ids), 'own_audio': True})['id']
    entered, resume = Event(), Event()
    copy = storage.copy_verified
    def delayed(source, target):
        entered.set()
        assert resume.wait(10)
        copy(source, target)
    monkeypatch.setattr(storage, 'copy_verified', delayed)
    def other_operations():
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE meetings SET title=? WHERE id=?', ('Still responsive', ids[0]))
        return svc.audio_window(mid, 3.5, 1)
    with ThreadPoolExecutor(2) as pool:
        moving = pool.submit(storage.run_once, svc)
        try:
            assert entered.wait(5)
            assert pool.submit(other_operations).result(timeout=2)['base64']
        finally:
            resume.set()
        assert moving.result(timeout=10)
    assert storage.status(svc, mid)['job']['state'] == 'completed'


def test_hard_exit_reclaims_durable_outputs_and_folder(recordings):
    import subprocess
    import sys
    from meeting_notes.composition import organize_audio
    svc, ids = recordings
    mid = svc.combine(**{**body(svc, ids), 'own_audio': True})['id']
    expected = svc.audio_window(mid, 3.5, 1)
    script = '''
import os, sys
from meeting_notes.service import Service
from meeting_notes import storage
svc = Service(sys.argv[1])
copy = storage.copy_verified
def crash(source, target):
    copy(source, target)
    os._exit(71)
storage.copy_verified = crash
storage.run_once(svc)
'''
    result = subprocess.run([sys.executable, '-c', script, str(svc.root)], timeout=15)
    assert result.returncode == 71
    with svc.db() as db:
        folder = Path(db.execute('SELECT path FROM audio_folders').fetchone()[0])
        assert db.execute('SELECT count(*) FROM storage_outputs').fetchone()[0] == 4
        assert list(folder.iterdir())
        db.execute("UPDATE storage_jobs SET lease=0 WHERE meeting=?", (mid,))
    storage.cleanup(svc)
    assert not folder.exists()
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    assert svc.audio_window(mid, 3.5, 1) == expected
    with svc.db() as db:
        assert not db.execute('SELECT 1 FROM storage_outputs').fetchone()
    organize_audio(svc, mid)
    assert storage.run_once(svc)
    assert storage.status(svc, mid)['owns_audio']


def test_failed_background_move_keeps_combination_and_request_id(recordings, monkeypatch):
    from meeting_notes.composition import organize_audio
    svc, ids = recordings
    request = {**body(svc, ids), 'own_audio': True}
    mid = svc.combine(**request)['id']
    copy = storage.copy_verified
    monkeypatch.setattr(storage, 'copy_verified', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    assert storage.run_once(svc)
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    assert svc.combine(**request) == {'id': mid}
    assert len(svc.list()) == 3
    monkeypatch.setattr(storage, 'copy_verified', copy)
    organize_audio(svc, mid)
    assert storage.run_once(svc)
    assert len(svc.list()) == 3


def test_combination_and_move_enqueue_are_atomic(recordings, monkeypatch):
    from meeting_notes import composition_storage
    svc, ids = recordings
    monkeypatch.setattr(composition_storage, 'enqueue', lambda *a, **kw: (_ for _ in ()).throw(ValueError('queue unavailable')))
    with pytest.raises(ValueError, match='queue unavailable'):
        svc.combine(**{**body(svc, ids), 'own_audio': True})
    assert len(svc.list()) == 2


@pytest.mark.parametrize('reverse', [False, True])
def test_two_owners_deletion_orders_and_relocation_without_sources(recordings, reverse):
    from meeting_notes import deletion
    from meeting_notes.composition import organize_audio
    svc, ids = recordings
    owners = []
    for i in range(2):
        mid = svc.combine(**{**body(svc, ids), 'request_id': f'owner-{i}', 'own_audio': True})['id']
        assert storage.run_once(svc)
        owners.append(mid)
    assert storage.status(svc, ids[0])['directory'] == str(svc.root)
    for source in ids:
        deletion.remove(svc, source, deletion.review(svc, source)['token'], True)
    disk = svc.root/'new-disk'; disk.mkdir()
    policy = storage.configure(svc, {'directory': str(disk), 'compress_playback': False, 'archive_mode': 'original'})
    expected = svc.audio_window(owners[0], 3.5, 1)
    organize_audio(svc, owners[0], expected_settings=policy)
    assert storage.run_once(svc)
    assert Path(storage.status(svc, owners[0])['directory']).parent == Path(policy['managed_path'])/'audio'
    assert svc.audio_window(owners[0], 3.5, 1) == expected
    for mid in owners[::(-1 if reverse else 1)]:
        assert svc.audio_window(mid, 3.5, 1) == expected
        deletion.remove(svc, mid, deletion.review(svc, mid)['token'], True)
    storage.cleanup(svc)
    assert not list((svc.root/'audio').iterdir())
    assert not list((Path(policy['managed_path'])/'audio').iterdir())


def test_lost_move_lease_cannot_publish_and_missing_folder_cleanup_finishes(recordings, monkeypatch):
    svc, ids = recordings
    mid = svc.combine(**{**body(svc, ids), 'own_audio': True})['id']
    copy = storage.copy_verified
    def lose_lease(source, target):
        copy(source, target)
        with svc.db() as db:
            db.execute('UPDATE storage_jobs SET lease=0 WHERE meeting=?', (mid,))
    monkeypatch.setattr(storage, 'copy_verified', lose_lease)
    assert storage.run_once(svc)
    assert not storage.status(svc, mid).get('owns_audio')
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    with svc.db() as db:
        assert not db.execute('SELECT 1 FROM storage_outputs').fetchone()
    folder = svc.root/'audio'/'recordings-missing'
    with svc.db() as db:
        db.execute('INSERT INTO audio_folders VALUES(?)', (str(folder),))
        db.execute("UPDATE storage_jobs SET state='completed',cleanup=? WHERE meeting=?", (json.dumps([{'path': str(folder/'01-missing'), 'sha': 'unused'}]), mid))
    storage.cleanup(svc)
    assert not storage.status(svc, mid)['job']['cleanup_pending']
    with svc.db() as db:
        assert not db.execute('SELECT 1 FROM audio_folders').fetchone()


def test_cleanup_hashing_does_not_block_writer_and_rechecks_changes(recordings, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    svc, ids = recordings
    # Keep the post-commit cleanup pending so we can exercise it separately.
    cleanup = storage.cleanup
    calls = []
    def delay_cleanup(service):
        calls.append(True)
        if len(calls) == 1:
            cleanup(service)
    monkeypatch.setattr(storage, 'cleanup', delay_cleanup)
    mid = svc.combine(**{**body(svc, ids), 'own_audio': True})['id']
    assert storage.run_once(svc)
    assert storage.status(svc, mid)['job']['cleanup_pending']
    monkeypatch.setattr(storage, 'cleanup', cleanup)
    entered, resume = Event(), Event()
    digest = storage.digest
    paths = []
    def delayed_hash(path):
        if not paths:
            paths.append(path)
            entered.set()
            assert resume.wait(10)
        return digest(path)
    monkeypatch.setattr(storage, 'digest', delayed_hash)
    def modify_during_hash():
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE meetings SET title=? WHERE id=?', ('Responsive cleanup', mid))
        paths[0].write_bytes(b'changed externally')
    with ThreadPoolExecutor(2) as pool:
        cleaning = pool.submit(storage.cleanup, svc)
        try:
            assert entered.wait(5)
            pool.submit(modify_during_hash).result(timeout=2)
        finally:
            resume.set()
        cleaning.result(timeout=10)
    assert paths[0].read_bytes() == b'changed externally'
    assert storage.status(svc, mid)['job']['cleanup_pending']
