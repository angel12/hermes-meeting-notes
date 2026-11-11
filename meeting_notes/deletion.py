"""User-reviewed local deletion. Durable cleanup never calls external services."""
import hashlib
import json
from pathlib import Path

from . import storage
from .store import working


def _local(svc):
    if getattr(svc, 'analysis_actor', None) is not None or getattr(svc, 'handoff_actor', None) is not None:
        raise ValueError('Delete meetings from the importing profile UI')


def _working(db):
    return working(db)


def _review(db, meeting):
    row = db.execute('SELECT * FROM meetings WHERE id=?', (meeting,)).fetchone()
    if not row:
        raise ValueError('Meeting not found')
    dependencies = []
    for combined in db.execute('SELECT c.meeting,c.parts,m.title FROM compositions c JOIN meetings m ON m.id=c.meeting'):
        if any(p['meeting_id'] == meeting for p in json.loads(combined['parts'])) and not db.execute('SELECT 1 FROM composition_audio WHERE meeting=?', (combined['meeting'],)).fetchone():
            dependencies.append({'id': combined['meeting'], 'title': combined['title']})
    jobs = [dict(r) for r in db.execute('SELECT * FROM jobs WHERE meeting=?', (meeting,))]
    storage_jobs = [dict(r) for r in db.execute('SELECT * FROM storage_jobs WHERE meeting=?', (meeting,))]
    handoffs = [dict(r) for r in db.execute('SELECT id,state,version FROM action_handoffs WHERE meeting=? ORDER BY id', (meeting,))]
    counts = {table: db.execute(f'SELECT count(*) FROM {table} WHERE meeting=?', (meeting,)).fetchone()[0]
              for table in ('transcripts', 'analyses', 'action_sets', 'action_handoffs')}
    blockers = []
    if dependencies:
        blockers.append('Delete the combined meetings that use this recording first.')
    if _working(db):
        blockers.append('Wait for transcription or audio storage work on this host to finish. After a host restart, use Start queued work to recover expired jobs, then refresh this review.')
    if any(h['state'] in ('running', 'unknown') for h in handoffs):
        blockers.append('Resolve running or uncertain handoffs before deleting their receipts.')
    # Counts and row IDs fence newly saved notes/actions as well as corrections.
    versions = {t: [r[0] for r in db.execute(f'SELECT id FROM {t} WHERE meeting=? ORDER BY id', (meeting,))]
                for t in ('transcripts', 'analyses', 'action_sets', 'uploads')}
    payload = [dict(row), jobs, storage_jobs, handoffs, versions, dependencies]
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {'id': meeting, 'title': row['title'], 'token': token, 'counts': counts,
            'combined': bool(db.execute('SELECT 1 FROM compositions WHERE meeting=?', (meeting,)).fetchone()),
            'dependencies': dependencies, 'blockers': blockers}


def review(svc, meeting):
    _local(svc)
    with svc.db() as db:
        db.execute('BEGIN')
        return _review(db, meeting)


def _paths(svc, db, meeting):
    paths = set()
    row = db.execute('SELECT audio FROM meetings WHERE id=?', (meeting,)).fetchone()
    if row['audio']:
        paths.update((row['audio'], str(Path(row['audio']).parent / f'{meeting}.wav')))
    for asset in db.execute('SELECT recording,playback FROM composition_audio WHERE meeting=?', (meeting,)):
        paths.update(asset)
    for table, column, predicate in (
        ('audio_assets', 'playback', 'meeting=?'), ('transcripts', 'raw', 'meeting=?'),
        ('attempts', 'raw', 'job IN (SELECT id FROM jobs WHERE meeting=?)'),
        ('storage_outputs', 'path', 'job IN (SELECT id FROM storage_jobs WHERE meeting=?)')):
        paths.update(r[0] for r in db.execute(f'SELECT {column} FROM {table} WHERE {predicate}', (meeting,)) if r[0])
    for row in db.execute('SELECT id FROM attempts WHERE job IN (SELECT id FROM jobs WHERE meeting=?)', (meeting,)):
        paths.add(str(svc.root/'raw'/f'{row[0]}.json'))
    for row in db.execute('SELECT cleanup FROM storage_jobs WHERE meeting=?', (meeting,)):
        paths.update(p['path'] for p in json.loads(row[0]))
    for row in db.execute('SELECT u.id,c.idx,l.path FROM uploads u JOIN chunks c ON c.upload=u.id LEFT JOIN upload_locations l ON l.upload=u.id WHERE u.meeting=?', (meeting,)):
        paths.add(str(Path(row['path'] or svc.root)/'uploads'/f'{row["id"]}.{row["idx"]}'))
    return paths


def checked_file(svc, value):
    path = Path(value)
    if path.parent.name != 'audio' and path.parent.parent.name == 'audio':
        return storage.check_audio_path(svc, path, allow_missing=True)
    root = path.parent.parent
    if not path.is_absolute() or path.parent.name not in ('audio', 'raw', 'uploads'):
        raise ValueError('Recording path is outside managed storage')
    if path.parent.name == 'raw' and root != svc.root:
        raise ValueError('Transcript path is outside this profile')
    storage.check_root(svc, root)
    if path.parent.is_symlink() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError('Managed storage contains a symlink or is unavailable')
    if path.exists() and not path.is_file():
        raise ValueError('Managed file is not a regular file')
    return path


def remove(svc, meeting, token, confirm):
    _local(svc)
    if confirm is not True or not isinstance(token, str):
        raise ValueError('Review and explicitly confirm deletion first')
    # Hash files before taking the write lock; large recordings on slow disks
    # must not block the worker. The locked pass reuses a hash only for a file
    # whose identity, size and timestamps are unchanged.
    hashes = {}
    with svc.db() as db:
        db.execute('BEGIN')
        pending = db.execute('SELECT 1 FROM deletions WHERE meeting=?', (meeting,)).fetchone()
        current = None if pending else _review(db, meeting)
        values = sorted(_paths(svc, db, meeting)) if current and current['token'] == token and not current['blockers'] else []
    for value in values:
        try:
            path = checked_file(svc, value)
            before = storage.file_signature(path)
            sha = storage.digest(path)
            if storage.file_signature(path) == before:
                hashes[str(path)] = (sha, before)
        except (OSError, ValueError):
            pass  # The locked pass reports the real error.
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT token,root FROM deletions WHERE meeting=?', (meeting,)).fetchone()
        if previous:
            if previous['token'] != token or previous['root'] != str(svc.root):
                raise ValueError('Deletion request does not match this profile')
        else:
            current = _review(db, meeting)
            if current['token'] != token:
                raise ValueError('Meeting changed; review deletion again')
            if current['blockers']:
                raise ValueError(' '.join(current['blockers']))
            files = []
            for value in sorted(_paths(svc, db, meeting)):
                path = checked_file(svc, value)
                if path.exists():
                    sha, signature = hashes.get(str(path), (None, None))
                    if signature != storage.file_signature(path):
                        sha = storage.digest(path)  # Rare: changed since the first pass.
                    files.append({'path': str(path), 'sha': sha})
            db.execute('INSERT INTO deletions VALUES(?,?,?,?)', (meeting, token, str(svc.root), json.dumps(files)))
            for table in ('handoff_claims',):
                db.execute(f'DELETE FROM {table} WHERE handoff IN (SELECT id FROM action_handoffs WHERE meeting=?)', (meeting,))
            for table in ('asr_attempt_parts', 'asr_attempt_settings'):
                db.execute(f'DELETE FROM {table} WHERE attempt IN (SELECT a.id FROM attempts a JOIN jobs j ON j.id=a.job WHERE j.meeting=?)', (meeting,))
            for table in ('asr_parts', 'asr_plans', 'attempts'):
                db.execute(f'DELETE FROM {table} WHERE job IN (SELECT id FROM jobs WHERE meeting=?)', (meeting,))
            for table in ('storage_outputs', 'storage_job_policy'):
                db.execute(f'DELETE FROM {table} WHERE job IN (SELECT id FROM storage_jobs WHERE meeting=?)', (meeting,))
            for table in ('chunks', 'upload_locations'):
                db.execute(f'DELETE FROM {table} WHERE upload IN (SELECT id FROM uploads WHERE meeting=?)', (meeting,))
            db.execute('DELETE FROM corrections WHERE transcript IN (SELECT id FROM transcripts WHERE meeting=?)', (meeting,))
            for table in ('action_handoffs', 'action_sets', 'analyses', 'transcripts', 'compositions', 'meeting_profiles',
                          'audio_assets', 'composition_audio', 'storage_jobs', 'uploads', 'jobs'):
                db.execute(f'DELETE FROM {table} WHERE meeting=?', (meeting,))
            db.execute('DELETE FROM meetings WHERE id=?', (meeting,))
    cleanup(svc)
    return {'deleted': True, **status(svc)}


def status(svc):
    _local(svc)
    with svc.db() as db:
        rows = db.execute('SELECT files FROM deletions WHERE root=?', (str(svc.root),)).fetchall()
    return {'pending_files': sum(len(json.loads(r[0])) for r in rows)}


def cleanup(svc, *, automatic=False):
    _local(svc)
    # First pass: list candidates under the lock, then hash outside it.
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if _working(db):
            return status(svc)
        candidates = [file for row in db.execute("SELECT files FROM deletions WHERE root=? AND files!='[]'", (str(svc.root),))
                      for file in json.loads(row['files']) if not (automatic and file.get('manual_retry'))]
    verified = {}
    for file in candidates:
        try:
            path = checked_file(svc, file['path'])
            if path.exists():
                before = storage.file_signature(path)
                sha = storage.digest(path)
                if storage.file_signature(path) == before:
                    verified[str(path)] = (sha, before)
        except (OSError, ValueError):
            pass
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if _working(db):
            return status(svc)
        for row in db.execute("SELECT meeting,files FROM deletions WHERE root=? AND files!='[]'", (str(svc.root),)).fetchall():
            pending = []
            for file in json.loads(row['files']):
                if automatic and file.get('manual_retry'):
                    pending.append(file)
                    continue
                try:
                    path = checked_file(svc, file['path'])
                    # A reimport may reuse a content-addressed audio filename.
                    used = db.execute('SELECT 1 FROM meetings WHERE audio=? UNION ALL SELECT 1 FROM audio_assets WHERE playback=? UNION ALL SELECT 1 FROM transcripts WHERE raw=? UNION ALL SELECT 1 FROM attempts WHERE raw=? UNION ALL SELECT 1 FROM storage_outputs WHERE path=?', (str(path),)*5).fetchone()
                    if used or storage.audio_referenced(db, path):
                        continue
                    if path.exists():
                        sha, signature = verified.get(str(path), (None, None))
                        if signature != storage.file_signature(path):
                            # Changed or created since the first pass. Check again
                            # on the next run instead of hashing under the lock.
                            pending.append(file)
                            continue
                        if sha != file['sha']:
                            pending.append({**file, 'manual_retry': True})
                            continue
                        path.unlink()
                except (OSError, ValueError):
                    pending.append(file)
            db.execute('UPDATE deletions SET files=? WHERE meeting=?', (json.dumps(pending), row['meeting']))
        storage.cleanup_folders(svc, db)
    return status(svc)
