"""Host-owned audio locations and durable, verified storage maintenance.

Metadata and raw ASR responses always remain in the importing profile.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time

from . import audio
from .store import uid, atomic_write

DEFAULTS = {'directory': '', 'compress_playback': False, 'archive_mode': 'original'}
LEASE = 60


def settings(svc, db=None):
    if db is None:
        with svc.db() as db:
            return settings(svc, db)
    row = db.execute('SELECT namespace,data FROM storage_settings WHERE id=1').fetchone()
    value = {**DEFAULTS, **json.loads(row['data'])}
    root = Path(value['directory'])/('hermes-meeting-notes-'+row['namespace']) if value['directory'] else svc.root
    notice = db.execute('SELECT notice FROM storage_identity WHERE id=1').fetchone()[0]
    return {**value, 'managed_path': str(root), 'notice': notice}


def check_root(svc, root):
    root = Path(root)
    if root == svc.root:
        return root
    with svc.db() as db:
        namespace = db.execute('SELECT namespace FROM storage_settings').fetchone()[0]
        known = db.execute('SELECT 1 FROM storage_locations WHERE path=?', (str(root),)).fetchone()
        device = db.execute('SELECT device FROM storage_devices WHERE path=?', (str(root),)).fetchone()
    if not known or root.is_symlink() or not root.is_dir() or (root/'.meeting-notes-owner').is_symlink():
        raise ValueError('Audio storage is unavailable; reconnect its disk or mount')
    try:
        if not device or root.stat().st_dev != device[0]:
            raise ValueError('Audio storage device changed; reconnect the original disk or choose a new directory')
        if (root/'.meeting-notes-owner').read_text() != namespace:
            raise ValueError('Audio storage belongs to another profile')
        for name in ('audio', 'uploads'):
            if (root/name).is_symlink() or not (root/name).is_dir():
                raise ValueError('Audio storage directory is unavailable')
    except OSError:
        raise ValueError('Audio storage is unavailable; reconnect its disk or mount') from None
    return root


def configure(svc, value, acknowledge_lossy_replacement=False):
    if set(value) != set(DEFAULTS) or type(value['compress_playback']) is not bool or value['archive_mode'] not in ('original', 'opus'):
        raise ValueError('Expected directory, compress_playback and archive_mode (original or opus)')
    directory = value['directory']
    if not isinstance(directory, str) or len(directory) > 2000 or '\0' in directory:
        raise ValueError('Use an existing absolute directory on the Hermes host')
    value = dict(value)
    if value['archive_mode'] == 'opus':
        if acknowledge_lossy_replacement is not True:
            raise ValueError('Explicitly acknowledge lossy replacement of stored originals')
        value['compress_playback'] = True  # Preserve the exact canonical playback separately.
    if directory:
        base = Path(directory)
        if not base.is_absolute() or not base.is_dir():
            raise ValueError('Use an existing absolute directory on the Hermes host; mount external disks first')
        value['directory'] = str(base.resolve())
        with svc.db() as db:
            namespace = db.execute('SELECT namespace FROM storage_settings').fetchone()[0]
        root = base.resolve()/('hermes-meeting-notes-'+namespace)
        with svc.db() as db:
            known = db.execute('SELECT 1 FROM storage_locations WHERE path=?', (str(root),)).fetchone()
        if known:
            check_root(svc, root)  # Saving again cannot recreate an unmounted volume.
        if root.is_symlink():
            raise ValueError('Managed audio storage cannot be a symlink')
        try:
            root.mkdir(mode=0o700, exist_ok=True)
            root.chmod(0o700)
            marker = root/'.meeting-notes-owner'
            if marker.exists():
                if marker.is_symlink() or marker.read_text() != namespace:
                    raise ValueError('Audio storage belongs to another profile')
            elif any(root.iterdir()):
                raise ValueError('Managed directory already contains unrelated files')
            else:
                atomic_write(marker, namespace.encode())
            for name in ('audio', 'uploads'):
                child = root/name
                if child.is_symlink():
                    raise ValueError('Managed audio storage cannot contain directory symlinks')
                child.mkdir(mode=0o700, exist_ok=True)
                child.chmod(0o700)
            probe = root/('.write-test-'+uid())
            try:
                with probe.open('xb') as f:
                    os.chmod(probe, 0o600); f.write(b'test'); f.flush(); os.fsync(f.fileno())
            finally:
                probe.unlink(missing_ok=True)
        except OSError:
            raise ValueError('The Hermes host cannot write to the selected audio directory') from None
        with svc.db() as db:
            db.execute('INSERT OR IGNORE INTO storage_locations VALUES(?)', (str(root),))
            db.execute('INSERT OR IGNORE INTO storage_devices VALUES(?,?)', (str(root), root.stat().st_dev))
    with svc.db() as db:
        db.execute('UPDATE storage_settings SET data=? WHERE id=1', (json.dumps(value),))
        if value['archive_mode'] == 'opus':
            db.execute('INSERT INTO storage_consent(created,settings) VALUES(?,?)', (time.time(), json.dumps(value)))
    return settings(svc)


def upload_root(svc, db, upload):
    row = db.execute('SELECT path FROM upload_locations WHERE upload=?', (upload,)).fetchone()
    return check_root(svc, row[0] if row else svc.root)


def check_audio_path(svc, path):
    """Validate flat audio files and numbered files in a combination folder."""
    path = Path(path)
    parent = path.parent
    if parent.name != 'audio' and parent.parent.name == 'audio':
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError('Combined audio directory is unavailable')
        parent = parent.parent
    if parent.name != 'audio' or parent.is_symlink() or not path.is_absolute():
        raise ValueError('Audio path is outside managed storage')
    check_root(svc, parent.parent)
    if path.is_symlink():
        raise ValueError('Managed audio cannot be a symlink')
    if path.exists() and not path.is_file():
        raise ValueError('Managed audio is not a regular file')
    return path


def audio_referenced(db, path):
    return db.execute('SELECT 1 FROM meetings WHERE audio=? UNION ALL SELECT 1 FROM audio_assets WHERE playback=? '
                      'UNION ALL SELECT 1 FROM composition_audio WHERE recording=? OR playback=?', (str(path),)*4).fetchone()


def playback_path(svc, db, meeting):
    asset = db.execute('SELECT playback FROM audio_assets WHERE meeting=?', (meeting,)).fetchone()
    original = db.execute('SELECT audio FROM meetings WHERE id=?', (meeting,)).fetchone()
    if not original or not original[0]:
        raise ValueError('Choose an original recording')
    path = Path(asset[0]) if asset else Path(original[0]).parent/f'{meeting}.wav'
    check_audio_path(svc, path)
    if path.is_symlink():
        raise ValueError('Managed audio cannot be a symlink')
    return path


def status(svc, meeting):
    with svc.db() as db:
        original = db.execute('SELECT audio,sha FROM meetings WHERE id=?', (meeting,)).fetchone()
        if not original:
            raise ValueError('Meeting not found')
        if not original['audio']:
            assets = db.execute('SELECT part,recording,playback FROM composition_audio WHERE meeting=? ORDER BY part', (meeting,)).fetchall()
            if not assets:
                return {'combined': True}
            return {'combined': True, 'owns_audio': True, 'directory': str(Path(assets[0]['recording']).parent),
                    'files': [Path(a['recording']).name for a in assets]}
        asset = db.execute('SELECT * FROM audio_assets WHERE meeting=?', (meeting,)).fetchone()
        job = db.execute('SELECT id,state,error,result,cleanup FROM storage_jobs WHERE meeting=? ORDER BY rowid DESC LIMIT 1', (meeting,)).fetchone()
        playback = Path(asset['playback']) if asset else Path(original['audio']).parent/f'{meeting}.wav'
    def size(path):
        try: return Path(path).stat().st_size
        except OSError: return None
    return {'combined': False, 'directory': str(Path(original['audio']).parent.parent),
            'archive_mode': asset['archive_mode'] if asset else 'original', 'original_sha256': original['sha'],
            'recording_bytes': size(original['audio']), 'playback_bytes': size(playback),
            'playback_format': playback.suffix.lstrip('.'),
            'job': {'id': job['id'], 'state': job['state'], 'error': job['error'],
                    'result': json.loads(job['result']) if job['result'] else None,
                    'cleanup_pending': bool(json.loads(job['cleanup']))} if job else None}


def enqueue(svc, meeting, automatic=False, expected_settings=None, db=None):
    if db is None:
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return enqueue(svc, meeting, automatic, expected_settings, db)
    row = db.execute('SELECT m.audio,j.state FROM meetings m JOIN jobs j ON j.meeting=m.id WHERE m.id=?', (meeting,)).fetchone()
    if not row or not row['audio'] or row['state'] != 'completed':
        raise ValueError('Storage changes require a completed original recording')
    old = db.execute("SELECT id FROM storage_jobs WHERE meeting=? AND state IN ('queued','running')", (meeting,)).fetchone()
    if old:
        return {'job_id': old[0]}
    value = settings(svc, db)
    if expected_settings is not None and expected_settings != value:
        raise ValueError('Storage settings changed; refresh and review them before applying')
    if automatic and not value['compress_playback'] and value['archive_mode'] == 'original':
        return None
    # Original replacement is irreversible; switching back cannot reconstruct it.
    asset = db.execute('SELECT archive_mode FROM audio_assets WHERE meeting=?', (meeting,)).fetchone()
    if asset and asset[0] == 'opus' and value['archive_mode'] == 'original':
        raise ValueError('This recording is already an Opus archive; its uploaded original cannot be restored by changing settings')
    ident = uid()
    db.execute('INSERT INTO storage_jobs(id,meeting,settings,state,created) VALUES(?,?,?,?,?)',
               (ident, meeting, json.dumps(value), 'queued', time.time()))
    db.execute('INSERT INTO storage_job_policy VALUES(?,?)', (ident, int(automatic)))
    return {'job_id': ident}

def digest(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def copy_verified(source, target):
    if source.is_symlink():
        raise ValueError('Managed audio cannot be a symlink')
    with source.open('rb') as src, target.open('xb') as dst:
        os.chmod(target, 0o600)
        shutil.copyfileobj(src, dst, 1024*1024); dst.flush(); os.fsync(dst.fileno())
    if digest(source) != digest(target):
        raise ValueError('Audio copy verification failed')


def cleanup(svc):
    # Playback uses the same immediate lock while reading files. Never unlink a
    # path still referenced by a meeting, including interrupted/repeated migration.
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','in_flight') UNION ALL SELECT 1 FROM storage_jobs WHERE state='running'").fetchone():
            return
        # Allocated output paths are durable before encoding starts. A crash may
        # leave a partial file, but never makes it authoritative.
        abandoned = db.execute("SELECT o.* FROM storage_outputs o JOIN storage_jobs j ON j.id=o.job WHERE j.state!='running' OR o.owner!=j.owner").fetchall()
        for row in abandoned:
            path = Path(row['path'])
            try:
                check_audio_path(svc, path)
                used = audio_referenced(db, path)
                if not used and not path.is_symlink():
                    path.unlink(missing_ok=True)
                db.execute('DELETE FROM storage_outputs WHERE path=?', (str(path),))
            except (OSError, ValueError):
                pass
        for row in db.execute('SELECT u.id,c.idx FROM uploads u JOIN chunks c ON c.upload=u.id JOIN meetings m ON m.id=u.meeting').fetchall():
            try:
                root = upload_root(svc, db, row['id'])
                (root/'uploads'/f'{row["id"]}.{row["idx"]}').unlink(missing_ok=True)
                db.execute('DELETE FROM chunks WHERE upload=? AND idx=?', (row['id'], row['idx']))
            except (OSError, ValueError):
                pass
        for row in db.execute("SELECT id,cleanup FROM storage_jobs WHERE state='completed' AND cleanup!='[]'").fetchall():
            remaining = []
            for file in json.loads(row['cleanup']):
                path = Path(file['path'])
                try:
                    check_audio_path(svc, path)
                    referenced = audio_referenced(db, path)
                    if referenced or path.is_symlink():
                        remaining.append(file); continue
                    if path.exists() and digest(path) != file['sha']:
                        remaining.append(file); continue
                    path.unlink(missing_ok=True)
                except (OSError, ValueError):
                    remaining.append(file)
            db.execute('UPDATE storage_jobs SET cleanup=? WHERE id=?', (json.dumps(remaining), row['id']))


def run_once(svc):
    cleanup(svc)
    owner = uid()
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE storage_jobs SET state='failed',error='Storage work was interrupted. Originals and any committed replacement remain safe; explicitly apply settings again to retry.' WHERE state='running' AND lease<?", (time.time(),))
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','in_flight') UNION ALL SELECT 1 FROM storage_jobs WHERE state='running'").fetchone():
            return False
        row = db.execute("SELECT * FROM storage_jobs WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return False
        job = dict(row)
        policy = db.execute('SELECT automatic FROM storage_job_policy WHERE job=?', (job['id'],)).fetchone()
        if policy and policy[0] and json.loads(job['settings']) != settings(svc, db):
            db.execute("UPDATE storage_jobs SET state='cancelled',error='Automatic storage policy changed before work started; review and apply current settings explicitly.' WHERE id=?", (job['id'],))
            return True
        db.execute("UPDATE storage_jobs SET state='running',owner=?,lease=?,error=NULL WHERE id=?", (owner, time.time()+LEASE, job['id']))
        source = Path(db.execute('SELECT audio FROM meetings WHERE id=?', (job['meeting'],)).fetchone()[0])
        asset = db.execute('SELECT * FROM audio_assets WHERE meeting=?', (job['meeting'],)).fetchone()
        playback = Path(asset['playback']) if asset else source.parent/f"{job['meeting']}.wav"
        was_opus = bool(asset and asset['archive_mode'] == 'opus')
        source_sha = asset['recording_sha'] if asset else db.execute('SELECT sha FROM meetings WHERE id=?', (job['meeting'],)).fetchone()[0]
        plan = db.execute('SELECT p.frames FROM asr_plans p JOIN jobs j ON j.id=p.job WHERE j.meeting=?', (job['meeting'],)).fetchone()
        expected_frames = asset['frames'] if asset else (plan[0] if plan else None)
        expected_pcm = asset['pcm_sha'] if asset else None
    def fenced(db):
        return db.execute("SELECT 1 FROM storage_jobs WHERE id=? AND owner=? AND state='running' AND lease>=?", (job['id'], owner, time.time())).fetchone()
    stop = threading.Event()
    def beat():
        while not stop.wait(10):
            try:
                with svc.db() as db:
                    db.execute("UPDATE storage_jobs SET lease=? WHERE id=? AND owner=? AND state='running' AND lease>=?", (time.time()+LEASE, job['id'], owner, time.time()))
            except Exception:
                return  # Fence closes if the heartbeat cannot be maintained.
    thread = threading.Thread(target=beat, daemon=True); thread.start()
    outputs, committed = [], False
    try:
        value = json.loads(job['settings']); root = check_root(svc, value['managed_path'])
        check_audio_path(svc, source)
        check_audio_path(svc, playback)
        if source.is_symlink() or playback.is_symlink() or digest(source) != source_sha:
            raise ValueError('Stored recording changed; no storage change was made')
        frames = audio.pcm_info(playback); pcm_sha = audio.pcm_digest(playback)
        if not frames or (expected_frames is not None and frames != expected_frames) or (expected_pcm and pcm_sha != expected_pcm):
            raise ValueError('Prepared recording changed; no storage change was made')
        before = source.stat().st_size + playback.stat().st_size
        stem = job['id']+'-'+owner
        archive = root/'audio'/(stem+('.opus' if value['archive_mode'] == 'opus' else '.original'))
        prepared = root/'audio'/(stem+('.flac' if value['compress_playback'] else '.wav'))
        outputs = [archive, prepared]
        with svc.db() as db:
            db.executemany('INSERT INTO storage_outputs VALUES(?,?,?)', ((job['id'], owner, str(p)) for p in outputs))
        if value['archive_mode'] == 'opus' and not was_opus:
            audio.require_single_track(source)
            audio.encode_archive(source, archive, 'opus'); audio.verify_opus(archive, frames)
        else:
            copy_verified(source, archive)
        if value['compress_playback'] and playback.suffix != '.flac':
            audio.encode_archive(playback, prepared, 'flac')
        elif not value['compress_playback'] and playback.suffix != '.wav':
            audio.prepare(playback, prepared)
        else:
            copy_verified(playback, prepared)
        if audio.pcm_info(prepared) != frames or audio.pcm_digest(prepared) != pcm_sha:
            raise ValueError('Prepared archive failed exact sample verification')
        for path in outputs:
            with path.open('rb') as file: os.fsync(file.fileno())
        fd = os.open(root/'audio', os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        cleanup_files = [{'path': str(p), 'sha': digest(p)} for p in (source, playback)]
        if cleanup_files[0]['sha'] != source_sha or audio.pcm_digest(playback) != pcm_sha:
            raise ValueError('Recording changed while preparing its replacement')
        after = archive.stat().st_size + prepared.stat().st_size
        archive_sha = digest(archive)
        if (value['archive_mode'] == 'original' or was_opus) and archive_sha != source_sha:
            raise ValueError('Recording copy changed before publication')
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not fenced(db):
                return True
            if policy and policy[0] and value != settings(svc, db):
                db.execute("UPDATE storage_jobs SET state='cancelled',lease=NULL,error='Automatic storage policy changed during preparation; the current recording was kept.' WHERE id=?", (job['id'],))
                return True
            current = db.execute('SELECT audio FROM meetings WHERE id=?', (job['meeting'],)).fetchone()[0]
            if current != str(source) or playback_path(svc, db, job['meeting']) != playback:
                raise ValueError('Recording changed while storage work was running')
            db.execute('UPDATE meetings SET audio=? WHERE id=?', (str(archive), job['meeting']))
            db.execute('INSERT OR REPLACE INTO audio_assets VALUES(?,?,?,?,?,?)',
                       (job['meeting'], str(prepared), frames, pcm_sha, value['archive_mode'], archive_sha))
            db.execute("UPDATE storage_jobs SET state='completed',lease=NULL,result=?,cleanup=? WHERE id=?", (
                json.dumps({'before_bytes': before, 'after_bytes': after, 'saved_bytes': before-after,
                            'archive_mode': value['archive_mode'], 'playback_format': prepared.suffix[1:]}), json.dumps(cleanup_files), job['id']))
        committed = True
        cleanup(svc)
    except Exception as exc:
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if fenced(db):
                db.execute("UPDATE storage_jobs SET state='failed',lease=NULL,error=? WHERE id=?", (
                    str(exc) if isinstance(exc, audio.ArchiveInputError) else 'Storage change failed. Check available space, mounted folders and ffmpeg/ffprobe support. The current recording was kept; explicitly apply settings again to retry.', job['id']))
    finally:
        stop.set(); thread.join()
        if not committed:
            with svc.db() as db:
                db.execute('BEGIN IMMEDIATE')
                for path in outputs:
                    used = audio_referenced(db, path)
                    if not used:
                        try: path.unlink(missing_ok=True)
                        except OSError: pass
    return True
