import json
import shutil
from pathlib import Path

import pytest
from meeting_notes import actions, deletion, runtime, storage
from meeting_notes.service import Service
from meeting_notes.analysis import read_transcript
from meeting_notes.worker import Worker
from test_analysis import meeting
from test_actions import item, proposal, prepare, actor, report
from test_composition import recordings, body
from test_core import imported, Adapter
from test_api import client


def remove(svc, mid):
    return deletion.remove(svc, mid, deletion.review(svc, mid)['token'], True)


def test_delete_all_local_data_and_idempotent_retry(proposal):
    svc, mid = proposal
    hid, _ = prepare(svc, mid)
    scoped = actor(svc, mid, hid)
    claim = actions.callback(scoped, mid, hid, 'begin', 0)
    actions.callback(scoped, mid, hid, 'report', 1, report(), claim['attempt_token'])
    token = deletion.review(svc, mid)['token']
    result = deletion.remove(svc, mid, token, True)
    assert result == {'deleted': True, 'pending_files': 0}
    assert not svc.list()
    assert all(not list((svc.root/folder).iterdir()) for folder in ('audio', 'raw', 'uploads'))
    with svc.db() as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        for table in ('transcripts','corrections','action_sets','action_handoffs','handoff_claims','attempts','jobs','uploads','chunks','asr_parts','asr_plans'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert deletion.remove(svc, mid, token, True) == result
    with pytest.raises(ValueError): deletion.remove(svc, mid, 'different', True)
    assert imported(svc) != mid  # Dedupe pointer is removed; reimport works.


def test_delete_combination_keeps_originals_and_blocks_referenced_sources(recordings):
    svc, ids = recordings
    mid = svc.combine(**body(svc, ids))['id']
    before = {p: p.read_bytes() for p in (svc.root/'audio').iterdir()}
    preview = deletion.review(svc, ids[0])
    assert preview['dependencies'][0]['id'] == mid
    with pytest.raises(ValueError, match='combined'): deletion.remove(svc, ids[0], preview['token'], True)
    assert remove(svc, mid)['deleted']
    assert {m['id'] for m in svc.list()} == set(ids)
    assert all(p.read_bytes() == data for p, data in before.items())
    assert svc.audio_window(ids[0], 0, .05)['base64']
    assert remove(svc, ids[0])['deleted']
    assert svc.audio_window(ids[1], 0, .05)['base64']


def test_deletion_is_fenced_by_new_actions_corrections_and_combination(recordings):
    svc, ids = recordings
    preview = deletion.review(svc, ids[0])
    page = read_transcript(svc, ids[0])
    actions.save(svc, ids[0], page['basis'], None, [item(page)])
    with pytest.raises(ValueError, match='changed'): deletion.remove(svc, ids[0], preview['token'], True)
    preview = deletion.review(svc, ids[0])
    svc.correct(ids[0], 'rename', '1', 'Renamed', 0)
    with pytest.raises(ValueError, match='changed'): deletion.remove(svc, ids[0], preview['token'], True)
    preview = deletion.review(svc, ids[0])
    svc.combine(**body(svc, ids))
    with pytest.raises(ValueError, match='changed'): deletion.remove(svc, ids[0], preview['token'], True)


@pytest.mark.parametrize('state', ['preparing','in_flight'])
def test_active_transcription_blocks_deletion(meeting, state):
    svc, mid = meeting
    with svc.db() as db: db.execute('UPDATE jobs SET state=? WHERE meeting=?', (state,mid))
    with pytest.raises(ValueError, match='finish'): remove(svc, mid)
    assert svc.get(mid)


@pytest.mark.parametrize('state', ['running','unknown'])
def test_active_or_uncertain_handoff_blocks_deletion(proposal, state):
    svc, mid = proposal
    hid, _ = prepare(svc, mid)
    with svc.db() as db: db.execute('UPDATE action_handoffs SET state=? WHERE id=?', (state,hid))
    with pytest.raises(ValueError, match='handoffs'): remove(svc, mid)


def test_confirmation_and_agent_scope_required(proposal):
    svc, mid = proposal
    token = deletion.review(svc, mid)['token']
    with pytest.raises(ValueError): deletion.remove(svc, mid, token, False)
    svc.analysis_actor = 'default'
    for fn in (lambda: deletion.review(svc,mid), lambda: deletion.remove(svc,mid,token,True), lambda: deletion.cleanup(svc)):
        with pytest.raises(ValueError, match='importing'): fn()


def test_cleanup_recovers_after_crash_and_keeps_reimported_audio(meeting, monkeypatch):
    svc, mid = meeting
    real_cleanup = deletion.cleanup
    monkeypatch.setattr(deletion, 'cleanup', lambda _: None)
    assert remove(svc, mid)['pending_files'] > 0
    assert not svc.list()
    # Simulate restart then reimport before cleanup: same content-addressed audio.
    restarted = Service(svc.root)
    new = imported(restarted)
    audio = next(p for p in (svc.root/'audio').iterdir() if p.suffix == '')
    before = audio.read_bytes()
    monkeypatch.setattr(deletion, 'cleanup', real_cleanup)
    assert deletion.cleanup(restarted)['pending_files'] == 0
    assert audio.read_bytes() == before
    Worker(restarted, lambda _: Adapter()).run_once()
    assert restarted.get(new)['active']


def test_cleanup_never_deletes_changed_files_or_clone_sources(meeting, monkeypatch, tmp_path):
    svc, mid = meeting
    real_cleanup = deletion.cleanup
    monkeypatch.setattr(deletion,'cleanup', lambda _: None)
    remove(svc, mid)
    shutil.copytree(svc.root, tmp_path/'clone')
    clone = Service(tmp_path/'clone')
    before = {p:p.read_bytes() for p in (svc.root/'audio').iterdir()}
    monkeypatch.setattr(deletion,'cleanup', real_cleanup)
    deletion.cleanup(clone)
    assert all(p.read_bytes()==data for p,data in before.items())
    path = next(iter(before));path.write_bytes(b'replaced outside plugin')
    assert deletion.cleanup(svc)['pending_files'] == 1
    assert path.read_bytes() == b'replaced outside plugin'


def test_symlink_or_foreign_file_fails_before_metadata_deletion(meeting, tmp_path):
    svc, mid = meeting
    outside = tmp_path.parent/(tmp_path.name+'-outside');outside.write_bytes(b'keep')
    with svc.db() as db: original = Path(db.execute('SELECT audio FROM meetings WHERE id=?',(mid,)).fetchone()[0])
    original.unlink();original.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'): remove(svc, mid)
    assert svc.get(mid) and outside.read_bytes()==b'keep'


def test_delete_archived_audio_in_separate_storage(meeting, tmp_path):
    svc, mid = meeting
    disk=tmp_path/'external';disk.mkdir()
    storage.configure(svc, {'directory':str(disk),'compress_playback':True,'archive_mode':'original'})
    storage.enqueue(svc,mid);assert storage.run_once(svc)
    assert storage.status(svc,mid)['job']['state']=='completed'
    assert remove(svc,mid)['pending_files']==0
    assert not list(disk.rglob('*.flac'))


def test_deletion_api_get_is_read_only_and_post_requires_review(client, monkeypatch):
    svc=runtime.service();mid=imported(svc)
    monkeypatch.setattr(runtime,'start',lambda:pytest.fail('Deletion started ASR worker'))
    prefix='/api/plugins/meeting_notes'
    preview=client.get(f'{prefix}/meetings/{mid}/deletion').json()
    assert svc.list()
    assert client.post(f'{prefix}/meetings/{mid}/delete',json={'token':preview['token'],'confirm':False}).status_code==400
    assert client.post(f'{prefix}/meetings/{mid}/delete',json={'token':preview['token'],'confirm':True}).json()['deleted']
    assert client.get(f'{prefix}/deletions').json()=={'pending_files':0}
    assert client.post(f'{prefix}/deletions/cleanup').status_code==200


def test_late_asr_response_cannot_recreate_deleted_raw_file(tmp_path):
    svc=Service(tmp_path);mid=imported(svc)
    class LateAdapter(Adapter):
        def transcribe(self, path):
            with svc.db() as db: db.execute("UPDATE jobs SET state='interrupted_unknown',lease=0 WHERE meeting=?", (mid,))
            assert remove(svc, mid)['deleted']
            return super().transcribe(path)
    Worker(svc, lambda _: LateAdapter()).run_once()
    assert not svc.list() and not list((svc.root/'raw').iterdir()) and not list((svc.root/'audio').iterdir())


def test_late_preparation_cannot_recreate_deleted_playback(tmp_path, monkeypatch):
    from meeting_notes import worker
    svc=Service(tmp_path);mid=imported(svc)
    def late(source, target):
        with svc.db() as db: db.execute("UPDATE jobs SET state='interrupted_unknown',lease=0 WHERE meeting=?", (mid,))
        remove(svc,mid)
        target.write_bytes(b'late ffmpeg output')
    monkeypatch.setattr(worker,'prepare',late)
    Worker(svc, lambda _: pytest.fail('Deleted meeting submitted to ASR')).run_once()
    assert not svc.list() and not list((svc.root/'audio').iterdir())
