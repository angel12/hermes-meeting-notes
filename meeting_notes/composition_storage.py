"""Durable background moves for independently owned combination audio."""
import json
import os
from pathlib import Path
import re
import time

from . import audio, storage
from .store import uid


def enqueue(svc, meeting, expected_settings=None, db=None):
    from .deletion import _local
    _local(svc)
    if db is None:
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return enqueue(svc, meeting, expected_settings, db)
    if not db.execute('SELECT 1 FROM compositions WHERE meeting=?', (meeting,)).fetchone():
        raise ValueError('Choose a combined meeting')
    active = db.execute("SELECT id FROM storage_jobs WHERE meeting=? AND state IN ('queued','running')", (meeting,)).fetchone()
    if active:
        return {'job_id': active[0]}
    value = storage.settings(svc, db)
    if expected_settings is not None and expected_settings != value:
        raise ValueError('Storage settings changed; review the destination again')
    ident = uid()
    db.execute('INSERT INTO storage_jobs(id,meeting,settings,state,created) VALUES(?,?,?,?,?)',
               (ident, meeting, json.dumps({'kind': 'composition', 'managed_path': value['managed_path']}), 'queued', time.time()))
    return {'job_id': ident}


def snapshot(db, meeting):
    composition = db.execute('SELECT parts FROM compositions WHERE meeting=?', (meeting,)).fetchone()
    if not composition:
        raise ValueError('Combined meeting is unavailable')
    result = []
    for part in json.loads(composition['parts']):
        source = db.execute('SELECT audio,sha FROM meetings WHERE id=?', (part['meeting_id'],)).fetchone()
        asset = db.execute('SELECT * FROM audio_assets WHERE meeting=?', (part['meeting_id'],)).fetchone()
        owned = db.execute('SELECT * FROM composition_audio WHERE meeting=? AND part=?', (meeting, part['part'])).fetchone()
        details = db.execute('SELECT data FROM composition_audio_details WHERE meeting=? AND part=?', (meeting, part['part'])).fetchone()
        if not owned and not source:
            raise ValueError('A source recording is unavailable')
        result.append({'part': part, 'source': dict(source) if source else None,
                       'asset': dict(asset) if asset else None, 'owned': dict(owned) if owned else None,
                       'details': json.loads(details[0]) if details else None})
    return result


def run(svc, job, owner, fenced):
    """The caller owns the job lease and heartbeat. No bulk I/O holds a DB lock."""
    root = storage.check_root(svc, json.loads(job['settings'])['managed_path'])
    folder = root/'audio'/('recordings-'+uid())
    storage.check_audio_path(svc, folder/'probe', allow_missing=True)
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not fenced(db):
            return
        basis = snapshot(db, job['meeting'])
        plans = []
        for item in basis:
            part, source, asset, owned = (item[k] for k in ('part', 'source', 'asset', 'owned'))
            if owned:
                original, playback = Path(owned['recording']), Path(owned['playback'])
                checks = item['details'] or {}  # Compatibility with the first ownership implementation.
            else:
                original = Path(source['audio'])
                playback = Path(asset['playback']) if asset else original.parent/f"{part['meeting_id']}.wav"
                checks = {'recording_sha': asset['recording_sha'] if asset else source['sha'],
                          'pcm_sha': asset['pcm_sha'] if asset else None,
                          'archive_mode': asset['archive_mode'] if asset else 'original'}
            base = re.sub(r'^(?:\d{2}-)+', '', original.name)
            recording = folder/f"{part['part']:02d}-{base}"
            prepared = folder/f"{part['part']:02d}-playback{playback.suffix}"
            plans.append((item, original, playback, recording, prepared, checks))
        # These allocations commit before any directory creation or file copying.
        db.execute('INSERT INTO audio_folders VALUES(?)', (str(folder),))
        db.executemany('INSERT INTO storage_outputs VALUES(?,?,?)',
                       ((job['id'], owner, str(path)) for plan in plans for path in plan[3:5]))
    folder.mkdir(mode=0o700)
    cleanup, results, signatures = [], [], {}
    for item, original, playback, recording, prepared, checks in plans:
        for path in (original, playback):
            storage.check_audio_path(svc, path)
            signatures[path] = storage.file_signature(path)
        recording_sha = storage.digest(original)
        pcm_sha = audio.pcm_digest(playback)
        frames = item['part']['frames']
        if ((checks.get('recording_sha') and recording_sha != checks['recording_sha'])
                or (checks.get('pcm_sha') and pcm_sha != checks['pcm_sha'])
                or audio.pcm_info(playback) != frames):
            raise ValueError('A source recording changed before moving')
        storage.copy_verified(original, recording)
        storage.copy_verified(playback, prepared)
        if storage.digest(recording) != recording_sha or audio.pcm_digest(prepared) != pcm_sha:
            raise ValueError('Audio changed while moving')
        for path in (recording, prepared):
            signatures[path] = storage.file_signature(path)
        cleanup.extend({'path': str(path), 'sha': storage.digest(path)} for path in (original, playback))
        results.append({'frames': frames, 'pcm_sha': pcm_sha, 'recording_sha': recording_sha,
                        'archive_mode': checks.get('archive_mode', 'opus' if original.suffix == '.opus' else 'original')})
    for directory in (folder, folder.parent):
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not fenced(db):
            return
        if snapshot(db, job['meeting']) != basis:
            raise ValueError('Audio references changed while moving')
        # Hashing happened outside the lock. Check that none of those files was
        # replaced or modified since verification before publishing their paths.
        if any(storage.file_signature(path) != value for path, value in signatures.items()):
            raise ValueError('Audio changed while moving')
        for plan, checks in zip(plans, results):
            item, original, playback, recording, prepared, _ = plan
            part, source, asset = (item[k] for k in ('part', 'source', 'asset'))
            db.execute('INSERT OR REPLACE INTO composition_audio VALUES(?,?,?,?)',
                       (job['meeting'], part['part'], str(recording), str(prepared)))
            db.execute('INSERT INTO composition_audio_details VALUES(?,?,?)',
                       (job['meeting'], part['part'], json.dumps(checks)))
            # Keep original entries working, including legacy combinations that
            # still follow them. Independently migrated sources stay independent.
            source_playback = (Path(asset['playback']) if asset else Path(source['audio']).parent/f"{part['meeting_id']}.wav") if source else None
            if source and source['audio'] == str(original) and source_playback == playback:
                db.execute('UPDATE meetings SET audio=? WHERE id=?', (str(recording), part['meeting_id']))
                db.execute('INSERT OR REPLACE INTO audio_assets VALUES(?,?,?,?,?,?)',
                           (part['meeting_id'], str(prepared), checks['frames'], checks['pcm_sha'], checks['archive_mode'], checks['recording_sha']))
        db.execute("UPDATE storage_jobs SET state='completed',lease=NULL,result=?,cleanup=? WHERE id=?",
                   (json.dumps({'directory': str(folder)}), json.dumps(cleanup), job['id']))
