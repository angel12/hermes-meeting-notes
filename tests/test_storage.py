import base64
import hashlib
import io
import json
from pathlib import Path
import time
import wave

import pytest

from meeting_notes import audio, storage
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from test_chunking import upload, Recognizer
from test_api import client


@pytest.fixture
def recorded(tmp_path):
    svc = Service(tmp_path/'profile')
    mid, original, pcm = upload(svc, 64000)
    adapter = Recognizer(); worker = Worker(svc, lambda _: adapter)
    assert worker.run_once()
    return svc, mid, original, pcm, adapter, worker


def config(svc, directory='', compress=True, mode='original'):
    return storage.configure(svc, {'directory': str(directory), 'compress_playback': compress, 'archive_mode': mode}, acknowledge_lossy_replacement=(mode == 'opus'))


def paths(svc, mid):
    with svc.db() as db:
        return Path(db.execute('SELECT audio FROM meetings WHERE id=?', (mid,)).fetchone()[0]), storage.playback_path(svc, db, mid)


def clip(svc, mid, start, duration):
    value = svc.audio_window(mid, start, duration)
    with wave.open(io.BytesIO(base64.b64decode(value['base64'])), 'rb') as f:
        return f.readframes(f.getnframes())


def test_upload_location_is_pinned_and_deduplication_reclaims_chunks(tmp_path):
    svc = Service(tmp_path/'profile'); disk = tmp_path/'disk'; disk.mkdir()
    original_settings = config(svc, disk, compress=False)
    data = b'original uploaded bytes'
    up = svc.begin_upload(len(data), 'Test', '2026-11-05T12:00:00-07:00', 'America/Denver')['id']
    config(svc, compress=False)
    svc.chunk(up, 0, data, hashlib.sha256(data).hexdigest())
    mid = svc.finalize(up)['id']
    original, _ = paths(svc, mid)
    assert original.parent.parent == Path(original_settings['managed_path'])
    assert original.read_bytes() == data
    assert not list((original.parent.parent/'uploads').iterdir())
    second = svc.begin_upload(len(data), 'Duplicate', '2026-11-05T12:00:00-07:00', 'America/Denver')['id']
    svc.chunk(second, 0, data, hashlib.sha256(data).hexdigest())
    assert svc.finalize(second)['id'] == mid
    assert not list((svc.root/'uploads').iterdir())


def test_storage_directories_are_owned_and_offline_storage_fails_closed(tmp_path):
    base = tmp_path/'disk'; base.mkdir()
    first, second = Service(tmp_path/'p1'), Service(tmp_path/'p2')
    a, b = config(first, base), config(second, base)
    assert a['managed_path'] != b['managed_path']
    for bad in ('relative', str(tmp_path/'missing'), 'http://host/audio'):
        with pytest.raises(ValueError): config(first, bad)
    root = Path(a['managed_path']); renamed = root.with_name('offline'); root.rename(renamed)
    with pytest.raises(ValueError, match='unavailable'):
        first.begin_upload(1, 'Test', '2026-11-05T12:00:00-07:00', 'America/Denver')
    assert not root.exists()  # Never recreates a missing mounted location.
    root.symlink_to(renamed)
    with pytest.raises(ValueError): storage.check_root(first, root)


def test_migrate_and_lossless_compress_preserve_pcm_transcript_and_original(recorded, tmp_path):
    svc, mid, original, pcm, adapter, worker = recorded
    before = svc.get(mid); old_original, old_wav = paths(svc, mid)
    raw = {p: p.read_bytes() for p in (svc.root/'raw').iterdir()}
    disk = tmp_path/'archive'; disk.mkdir(); config(svc, disk)
    job = storage.enqueue(svc, mid)
    assert storage.enqueue(svc, mid) == job
    assert worker.run_once() and len(adapter.calls) == 1
    stored, prepared = paths(svc, mid)
    assert stored.read_bytes() == original and prepared.suffix == '.flac'
    assert not old_original.exists() and not old_wav.exists()
    assert audio.pcm_info(prepared) == 64000
    assert clip(svc, mid, .125, 1.75) == pcm[2000*2:30000*2]
    assert svc.get(mid) == before and all(p.read_bytes() == content for p, content in raw.items())
    report = storage.status(svc, mid)
    assert report['job']['state'] == 'completed' and report['job']['result']['saved_bytes'] > 0
    assert not report['job']['cleanup_pending']
    assert Service(svc.root).audio_window(mid, 0, 1) == svc.audio_window(mid, 0, 1)


def test_explicit_small_archive_replaces_only_managed_original(recorded):
    svc, mid, original, pcm, adapter, worker = recorded
    old_original, old_wav = paths(svc, mid)
    source_outside_plugin = svc.root.parent/'source.wav'; source_outside_plugin.write_bytes(original)
    config(svc, mode='opus'); storage.enqueue(svc, mid); worker.run_once()
    stored, prepared = paths(svc, mid)
    assert stored.suffix == '.opus' and stored.stat().st_size < len(original)
    assert prepared.suffix == '.flac' and clip(svc, mid, 0, 4) == pcm
    assert not old_original.exists() and not old_wav.exists()
    assert source_outside_plugin.read_bytes() == original
    assert storage.status(svc, mid)['archive_mode'] == 'opus'
    # Dedupe retains the original upload identity, not the encoded archive hash.
    assert upload(svc, 64000)[0] == mid
    config(svc, mode='original')
    with pytest.raises(ValueError, match='cannot be restored'): storage.enqueue(svc, mid)
    config(svc, mode='opus'); before = stored.read_bytes()
    storage.enqueue(svc, mid); worker.run_once()
    assert paths(svc, mid)[0].read_bytes() == before  # Never recompress an Opus archive.


def test_compressed_recordings_combine_and_play_across_boundary(recorded):
    svc, mid, _, pcm, _, worker = recorded
    second, _, second_pcm = upload(svc, 48000); worker.run_once()
    config(svc); storage.enqueue(svc, mid); storage.enqueue(svc, second)
    worker.run_once(); worker.run_once()
    combined = svc.combine(request_id='synthetic-storage-combine', title='Combined', date='2026-11-05T12:00:00-07:00', timezone='America/Denver',
        sources=[{'id': m, 'active': svc.get(m)['active'], 'revision': 0} for m in (mid, second)])['id']
    assert svc.get(combined)['recordings'][1]['offset'] == 4
    assert clip(svc, combined, 3.5, 1) == pcm[-16000:] + second_pcm[:16000]
    assert storage.status(svc, combined) == {'combined': True}
    with pytest.raises(ValueError): storage.enqueue(svc, combined)


def test_failed_conversion_keeps_current_files_and_retry_is_explicit(recorded, monkeypatch):
    svc, mid, original, pcm, _, worker = recorded
    before = paths(svc, mid); config(svc)
    real = audio.encode_archive
    monkeypatch.setattr(audio, 'encode_archive', lambda *_: (_ for _ in ()).throw(OSError('private-path-no-space')))
    storage.enqueue(svc, mid); worker.run_once()
    status = storage.status(svc, mid)
    assert status['job']['state'] == 'failed' and 'private-path' not in json.dumps(status)
    assert paths(svc, mid) == before and before[0].read_bytes() == original and clip(svc, mid, 0, 4) == pcm
    assert not worker.run_once()
    monkeypatch.setattr(audio, 'encode_archive', real)
    storage.enqueue(svc, mid); worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'completed'


def test_corrupt_lossless_result_cannot_replace_audio(recorded, monkeypatch):
    svc, mid, original, _, _, worker = recorded
    old = paths(svc, mid); config(svc)
    real = audio.pcm_digest
    monkeypatch.setattr(audio, 'pcm_digest', lambda p: 'wrong' if p.suffix == '.flac' else real(p))
    storage.enqueue(svc, mid); worker.run_once()
    assert paths(svc, mid) == old and old[0].read_bytes() == original
    assert storage.status(svc, mid)['job']['state'] == 'failed'


def test_lost_lease_cannot_publish_and_partial_outputs_are_recovered(recorded, monkeypatch):
    svc, mid, original, _, _, worker = recorded
    before = paths(svc, mid); config(svc)
    real = audio.encode_archive
    def expire(*args):
        real(*args)
        with svc.db() as db: db.execute('UPDATE storage_jobs SET lease=0')
    monkeypatch.setattr(audio, 'encode_archive', expire)
    storage.enqueue(svc, mid); worker.run_once()
    assert paths(svc, mid) == before and before[0].read_bytes() == original
    worker.run_once(); worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    with svc.db() as db: assert db.execute('SELECT count(*) FROM storage_outputs').fetchone()[0] == 0


def test_committed_replacement_survives_cleanup_interruption(recorded, monkeypatch):
    svc, mid, original, pcm, _, worker = recorded
    old = paths(svc, mid); config(svc)
    real = storage.cleanup; calls = []
    def interrupt(svc):
        calls.append(1)
        if len(calls) > 1: raise OSError('interrupted after commit')
        real(svc)
    monkeypatch.setattr(storage, 'cleanup', interrupt)
    storage.enqueue(svc, mid); worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'completed'
    assert storage.status(svc, mid)['job']['cleanup_pending'] and all(p.exists() for p in old)
    assert clip(svc, mid, 0, 4) == pcm
    monkeypatch.setattr(storage, 'cleanup', real); real(svc)
    assert not any(p.exists() for p in old) and paths(svc, mid)[0].read_bytes() == original


def test_automatic_archive_is_queued_only_after_transcription_and_get_never_runs_work(tmp_path):
    svc = Service(tmp_path); config(svc)
    mid, _, pcm = upload(svc, 64000); adapter = Recognizer(); worker = Worker(svc, lambda _: adapter)
    assert storage.status(svc, mid)['job'] is None
    worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'queued'
    assert paths(svc, mid)[1].suffix == '.wav'
    worker.run_once()
    assert paths(svc, mid)[1].suffix == '.flac' and len(adapter.calls) == 1
    assert clip(svc, mid, 0, 4) == pcm


def test_storage_api_requires_explicit_apply_and_get_is_read_only(client, monkeypatch):
    from meeting_notes import runtime
    svc = runtime.service(); mid, _, _ = upload(svc, 64000)
    Worker(svc, lambda _: Recognizer()).run_once()
    calls = []; monkeypatch.setattr(runtime, 'start', lambda: calls.append(1))
    prefix = '/api/plugins/meeting_notes'
    assert client.get(prefix+'/storage-settings').status_code == 200
    assert client.get(f'{prefix}/meetings/{mid}/storage').status_code == 200 and not calls
    assert client.post(f'{prefix}/meetings/{mid}/storage', json={}).status_code == 400
    assert client.put(prefix+'/storage-settings', json=storage.DEFAULTS).status_code == 200 and not calls
    assert client.post(f'{prefix}/meetings/{mid}/storage', json={'apply_current_settings': True, 'settings': storage.settings(svc)}).status_code == 200 and calls == [1]


def test_expired_storage_job_recovers_allocated_partial_files_without_publication(recorded):
    svc, mid, original, _, _, worker = recorded
    previous = paths(svc, mid); config(svc)
    job = storage.enqueue(svc, mid)['job_id']
    partial = svc.root/'audio'/'synthetic-incomplete.flac'; partial.write_bytes(b'partial encoding')
    with svc.db() as db:
        db.execute("UPDATE storage_jobs SET state='running',owner='lost',lease=0 WHERE id=?", (job,))
        db.execute('INSERT INTO storage_outputs VALUES(?,?,?)', (job, 'lost', str(partial)))
    worker.run_once(); worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    assert not partial.exists() and paths(svc, mid) == previous and previous[0].read_bytes() == original


def test_apply_refuses_changed_policy_and_storage_waits_for_asr(recorded):
    svc, mid, _, _, _, worker = recorded
    prior = storage.settings(svc); config(svc, mode='opus')
    with pytest.raises(ValueError, match='changed'):
        storage.enqueue(svc, mid, expected_settings=prior)
    second, _, _ = upload(svc, 48000)
    active = worker.claim()
    assert active['meeting'] == second
    storage.enqueue(svc, mid)
    assert storage.run_once(svc) is False
    assert storage.status(svc, mid)['job']['state'] == 'queued'


@pytest.mark.parametrize('mode', ['original', 'opus'])
def test_cloned_profile_cannot_modify_source_profile_audio(recorded, tmp_path, mode):
    import shutil
    svc, mid, original, pcm, _, worker = recorded
    disk = tmp_path/'shared'; disk.mkdir(); config(svc, disk)
    storage.enqueue(svc, mid); worker.run_once()
    source_paths = paths(svc, mid)
    hashes = [storage.digest(p) for p in source_paths]
    # Also copy pending deletion work, as clone-all copies the whole plugin DB.
    storage.enqueue(svc, mid)
    clone_root = tmp_path/'cloned-profile'
    shutil.copytree(svc.root, clone_root)
    clone = Service(clone_root)
    assert storage.settings(clone)['notice']
    assert storage.settings(clone)['managed_path'] == str(clone_root)
    storage.cleanup(clone)
    assert not storage.run_once(clone)
    config(clone, disk, mode=mode)
    assert storage.settings(clone)['managed_path'] != storage.settings(svc)['managed_path']
    storage.enqueue(clone, mid); storage.run_once(clone)
    assert storage.status(clone, mid)['job']['state'] == 'failed'
    assert all(p.exists() and storage.digest(p) == sha for p, sha in zip(source_paths, hashes))
    assert source_paths[0].read_bytes() == original and clip(svc, mid, 0, 4) == pcm
    with pytest.raises(ValueError): clone.audio_window(mid, 0, 1)
    assert not storage.settings(Service(svc.root))['notice']


def test_unbound_legacy_storage_is_not_adopted_after_database_copy(recorded, tmp_path):
    import shutil
    svc, mid, _, pcm, _, worker = recorded
    disk = tmp_path/'shared'; disk.mkdir(); config(svc, disk)
    storage.enqueue(svc, mid); worker.run_once()
    with svc.db() as db: db.execute('DELETE FROM storage_identity')
    clone_root = tmp_path/'clone'; shutil.copytree(svc.root, clone_root)
    clone = Service(clone_root)
    with pytest.raises(ValueError): storage.check_root(clone, paths(svc, mid)[0].parent.parent)
    storage.cleanup(clone)
    assert clip(svc, mid, 0, 4) == pcm


def test_successful_upload_cleanup_is_not_revisited(recorded, monkeypatch):
    svc, *_ = recorded
    storage.cleanup(svc)
    with svc.db() as db: assert db.execute('SELECT count(*) FROM chunks').fetchone()[0] == 0
    monkeypatch.setattr(storage, 'upload_root', lambda *_: pytest.fail('Cleaned chunks must not be visited again'))
    storage.cleanup(svc)


def test_policy_revocation_cancels_automatic_archive_before_start(tmp_path):
    svc = Service(tmp_path); config(svc, mode='opus')
    mid, original, _ = upload(svc, 64000); worker = Worker(svc, lambda _: Recognizer()); worker.run_once()
    previous = paths(svc, mid)
    config(svc, compress=False)
    worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'cancelled'
    assert paths(svc, mid) == previous and previous[0].read_bytes() == original


def test_policy_revocation_during_automatic_archive_keeps_original(tmp_path, monkeypatch):
    svc = Service(tmp_path); config(svc, mode='opus')
    mid, original, _ = upload(svc, 64000); worker = Worker(svc, lambda _: Recognizer()); worker.run_once()
    previous = paths(svc, mid); encode = audio.encode_archive
    def change_policy(*args):
        encode(*args); config(svc, compress=False)
    monkeypatch.setattr(audio, 'encode_archive', change_policy)
    worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'cancelled'
    assert paths(svc, mid) == previous and previous[0].read_bytes() == original


def test_saving_again_does_not_recreate_missing_or_wrong_device_storage(tmp_path):
    svc = Service(tmp_path/'profile'); disk = tmp_path/'mount'; disk.mkdir()
    value = config(svc, disk); root = Path(value['managed_path'])
    moved = root.with_name('unmounted'); root.rename(moved)
    with pytest.raises(ValueError): config(svc, disk)
    assert not root.exists()
    moved.rename(root)
    with svc.db() as db: db.execute('UPDATE storage_devices SET device=device+1')
    with pytest.raises(ValueError, match='device changed'): config(svc, disk)


def test_lossy_replacement_requires_auditable_server_acknowledgement(tmp_path):
    svc = Service(tmp_path)
    value = {**storage.DEFAULTS, 'archive_mode': 'opus'}
    with pytest.raises(ValueError, match='acknowledge'): storage.configure(svc, value)
    assert storage.settings(svc)['archive_mode'] == 'original'
    storage.configure(svc, value, acknowledge_lossy_replacement=True)
    with svc.db() as db:
        assert json.loads(db.execute('SELECT settings FROM storage_consent').fetchone()[0])['archive_mode'] == 'opus'


def test_playback_releases_lock_and_open_files_survive_migration_cleanup(recorded, monkeypatch):
    svc, mid, _, pcm, _, worker = recorded
    config(svc); storage.enqueue(svc, mid); worker.run_once()
    old_paths = paths(svc, mid); read = audio.read_pcm
    def migrate_while_reading(file, start, count, codec=None):
        # Would deadlock/time out if playback retained its SQLite write lock.
        storage.enqueue(svc, mid); storage.run_once(svc)
        assert not old_paths[1].exists()
        return read(file, start, count, codec)
    monkeypatch.setattr(audio, 'read_pcm', migrate_while_reading)
    assert clip(svc, mid, .125, 1) == pcm[2000*2:18000*2]


def test_opus_refuses_multiple_audio_tracks_without_losing_original(recorded, monkeypatch):
    svc, mid, original, _, _, worker = recorded
    previous = paths(svc, mid); real = audio.subprocess.run
    def multiple_tracks(args, **kwargs):
        if args[0] == 'ffprobe' and 'stream=index' in args:
            from subprocess import CompletedProcess
            return CompletedProcess(args, 0, stdout=b'{"streams":[{"index":0},{"index":1}]}')
        return real(args, **kwargs)
    monkeypatch.setattr(audio.subprocess, 'run', multiple_tracks)
    config(svc, mode='opus'); storage.enqueue(svc, mid); worker.run_once()
    assert storage.status(svc, mid)['job']['state'] == 'failed'
    assert 'multi-track' in storage.status(svc, mid)['job']['error']
    assert paths(svc, mid) == previous and previous[0].read_bytes() == original
