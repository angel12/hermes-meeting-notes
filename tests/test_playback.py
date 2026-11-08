import wave
import pytest
from test_composition import recordings, body
from meeting_notes.playback import timeline, waveform


def test_combined_waveform_uses_full_audio_duration_and_real_peaks(recordings):
    svc, ids = recordings
    # Synthetic constant samples make ordering and the true tail measurable.
    from meeting_notes.storage import playback_path
    for mid, value in zip(ids, (8192, 16384)):
        with svc.db() as db: path = playback_path(svc, db, mid)
        with wave.open(str(path),'wb') as out:
            out.setparams((1,2,16000,0,'NONE','not compressed'))
            out.writeframes(value.to_bytes(2,'little',signed=True)*64000)
    mid = svc.combine(**body(svc,ids))['id']
    assert timeline(svc,mid)['duration'] == 8
    result = waveform(svc,mid)
    assert result['duration'] == 8
    assert len(result['peaks']) <= 1200
    assert result['peaks'][0] == .25
    assert result['peaks'][-1] == .5


def test_waveform_decodes_without_holding_database_lock(recordings, monkeypatch):
    svc, ids = recordings
    import meeting_notes.playback as module
    from contextlib import contextmanager
    original = module.pcm_stream
    @contextmanager
    def checked(*args):
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
        with original(*args) as read: yield read
    monkeypatch.setattr(module,'pcm_stream',checked)
    assert waveform(svc, ids[0])['duration'] == 4


def test_waveform_refuses_missing_recording(recordings):
    svc, _ = recordings
    with pytest.raises(ValueError): waveform(svc,'nonexistent')


def test_flac_waveform_matches_pcm_and_rejects_truncation(recordings, monkeypatch):
    from meeting_notes import playback, audio, storage
    svc, ids = recordings
    with svc.db() as db: source = storage.playback_path(svc, db, ids[0])
    with wave.open(str(source),'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed'))
        out.writeframes(b'\x00\x40'*64000)
    expected = waveform(svc, ids[0])
    target = source.with_suffix('.test.flac')
    audio.encode_archive(source, target, 'flac')
    monkeypatch.setattr(playback.storage, 'playback_path', lambda *_:target)
    assert waveform(svc,ids[0]) == expected
    target.write_bytes(b'invalid synthetic archive')
    with pytest.raises(ValueError): waveform(svc,ids[0])


def test_missing_and_corrupt_wav_are_reported_as_unavailable(recordings):
    from meeting_notes import storage
    svc, ids = recordings
    with svc.db() as db: path = storage.playback_path(svc, db, ids[0])
    path.write_bytes(b'broken wav')
    with pytest.raises(ValueError, match='unavailable'): waveform(svc,ids[0])
    path.unlink()
    with pytest.raises(ValueError): timeline(svc,ids[0])


def test_waveform_capacity_fails_fast_and_recovers(recordings):
    from meeting_notes.playback import _SCAN_SLOTS
    svc, ids = recordings
    with _SCAN_SLOTS, _SCAN_SLOTS:
        with pytest.raises(ValueError, match='busy'): waveform(svc,ids[0])
    assert waveform(svc,ids[0])['duration'] == 4


def test_owned_combination_waveform_survives_source_deletion(recordings):
    from meeting_notes import storage, deletion
    svc, ids = recordings
    mid = svc.combine(**{**body(svc, ids), 'own_audio': True})['id']
    before = waveform(svc, mid)
    assert storage.run_once(svc)
    for original in ids:
        deletion.remove(svc, original, deletion.review(svc, original)['token'], True)
    assert timeline(svc,mid)['duration'] == 8
    assert waveform(svc,mid) == before
