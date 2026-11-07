import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def uid():
    return uuid.uuid4().hex


def atomic_write(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uid() + '.tmp')
    try:
        with temp.open('xb') as f:
            os.chmod(temp, 0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for name in ('audio', 'raw', 'uploads'):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS meetings(id TEXT PRIMARY KEY, title TEXT NOT NULL,
                date TEXT NOT NULL, timezone TEXT NOT NULL, audio TEXT NOT NULL, sha TEXT UNIQUE NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0, active TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, meeting TEXT NOT NULL REFERENCES meetings(id),
                state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, owner TEXT,
                lease REAL, attempt TEXT, error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_job ON jobs(meeting);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id),
                generation INTEGER NOT NULL, started REAL NOT NULL, sent REAL, raw TEXT, outcome TEXT);
            CREATE TABLE IF NOT EXISTS asr_responses(attempt TEXT PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
                status INTEGER NOT NULL, sha TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS asr_recovery(job TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS transcripts(id TEXT PRIMARY KEY, meeting TEXT NOT NULL REFERENCES meetings(id),
                raw TEXT NOT NULL, sha TEXT NOT NULL, segments TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS corrections(id TEXT PRIMARY KEY, transcript TEXT NOT NULL REFERENCES transcripts(id),
                revision INTEGER NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transcription_connections(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, size INTEGER NOT NULL, metadata TEXT NOT NULL,
                meeting TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS upload_cleanup_retry(upload TEXT PRIMARY KEY REFERENCES uploads(id) ON DELETE CASCADE, next_check REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(upload TEXT NOT NULL REFERENCES uploads(id), idx INTEGER NOT NULL,
                sha TEXT NOT NULL, size INTEGER NOT NULL, PRIMARY KEY(upload,idx));
            CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, meeting TEXT NOT NULL REFERENCES meetings(id),
                transcript TEXT NOT NULL REFERENCES transcripts(id), revision INTEGER NOT NULL,
                created REAL NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS compositions(meeting TEXT PRIMARY KEY REFERENCES meetings(id),
                request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, parts TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meeting_profiles(meeting TEXT PRIMARY KEY REFERENCES meetings(id),
                profile TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS asr_plans(job TEXT PRIMARY KEY REFERENCES jobs(id),
                transcript TEXT NOT NULL, settings TEXT NOT NULL, frames INTEGER NOT NULL, rate INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS asr_parts(job TEXT NOT NULL REFERENCES jobs(id), idx INTEGER NOT NULL,
                start_frame INTEGER NOT NULL, frames INTEGER NOT NULL, state TEXT NOT NULL,
                attempt TEXT, segments TEXT, raw TEXT, sha TEXT, PRIMARY KEY(job,idx));
            CREATE TABLE IF NOT EXISTS asr_attempt_parts(attempt TEXT PRIMARY KEY REFERENCES attempts(id),
                job TEXT NOT NULL, part INTEGER NOT NULL, FOREIGN KEY(job,part) REFERENCES asr_parts(job,idx));
            CREATE TABLE IF NOT EXISTS asr_attempt_settings(attempt TEXT PRIMARY KEY REFERENCES attempts(id),
                settings TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS action_sets(id TEXT PRIMARY KEY, meeting TEXT NOT NULL REFERENCES meetings(id),
                transcript TEXT NOT NULL REFERENCES transcripts(id), revision INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS action_handoffs(id TEXT PRIMARY KEY, meeting TEXT NOT NULL REFERENCES meetings(id),
                action_id TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL, profile TEXT NOT NULL,
                transcript TEXT NOT NULL, revision INTEGER NOT NULL, destination TEXT NOT NULL, instructions TEXT NOT NULL,
                snapshot TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL, result TEXT, history TEXT NOT NULL,
                created REAL NOT NULL, updated REAL, UNIQUE(meeting,request_id));
            CREATE TABLE IF NOT EXISTS handoff_claims(handoff TEXT PRIMARY KEY REFERENCES action_handoffs(id), token TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS storage_settings(id INTEGER PRIMARY KEY CHECK(id=1), namespace TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS storage_locations(path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS upload_locations(upload TEXT PRIMARY KEY REFERENCES uploads(id), path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS composition_audio(meeting TEXT NOT NULL REFERENCES meetings(id),
                part INTEGER NOT NULL, recording TEXT NOT NULL, playback TEXT NOT NULL,
                PRIMARY KEY(meeting, part));
            CREATE TABLE IF NOT EXISTS audio_assets(meeting TEXT PRIMARY KEY REFERENCES meetings(id),
                playback TEXT NOT NULL, frames INTEGER NOT NULL, pcm_sha TEXT NOT NULL, archive_mode TEXT NOT NULL, recording_sha TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS storage_jobs(id TEXT PRIMARY KEY, meeting TEXT NOT NULL REFERENCES meetings(id),
                settings TEXT NOT NULL, state TEXT NOT NULL, owner TEXT, lease REAL, error TEXT, result TEXT,
                cleanup TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_storage_job ON storage_jobs(meeting) WHERE state IN ('queued','running');
            CREATE TABLE IF NOT EXISTS storage_outputs(job TEXT NOT NULL REFERENCES storage_jobs(id), owner TEXT NOT NULL, path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS storage_identity(id INTEGER PRIMARY KEY CHECK(id=1), root TEXT NOT NULL, notice TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS storage_devices(path TEXT PRIMARY KEY, device INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS storage_job_policy(job TEXT PRIMARY KEY REFERENCES storage_jobs(id), automatic INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS storage_consent(id INTEGER PRIMARY KEY, created REAL NOT NULL, settings TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deletions(meeting TEXT PRIMARY KEY, token TEXT NOT NULL, root TEXT NOT NULL, files TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO storage_settings VALUES(1,?,?)', (uid(), '{}'))
            identity = db.execute('SELECT root FROM storage_identity WHERE id=1').fetchone()
            # A database clone is not a grant over its source profile's files.
            # Legacy separate locations with no binding are also quarantined.
            if ((identity and identity[0] != str(self.root)) or
                    (not identity and db.execute('SELECT 1 FROM storage_locations').fetchone())):
                db.execute('UPDATE storage_settings SET namespace=?,data=? WHERE id=1', (uid(), '{}'))
                db.execute('DELETE FROM storage_locations')
                db.execute('DELETE FROM storage_devices')
                db.execute("UPDATE storage_jobs SET state='failed',lease=NULL,error='Profile storage identity changed; old audio locations are foreign. Configure storage for this profile.' WHERE state IN ('queued','running')")
                db.execute('INSERT OR REPLACE INTO storage_identity VALUES(1,?,?)', (str(self.root),
                    'This database was cloned, moved or has unbound legacy storage. Previous audio locations are foreign and will not be modified. Save a storage location for new recordings; existing file references require a separate restore.'))
            else:
                db.execute('INSERT OR IGNORE INTO storage_identity VALUES(1,?,?)', (str(self.root), ''))

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'data.sqlite', timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        from .connections import effective
        return effective(self.connections())

    def connections(self):
        from .connections import read
        with self.db() as db:
            return read(db)

    def list(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT m.id,m.title,m.date,m.timezone,m.revision,m.active,j.state,j.error,EXISTS(SELECT 1 FROM compositions c WHERE c.meeting=m.id) AS combined FROM meetings m JOIN jobs j ON j.meeting=m.id ORDER BY m.rowid DESC')]

    def get(self, meeting):
        from .transcript import effective
        from .analysis import project
        with self.db() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM meetings WHERE id=?', (meeting,)).fetchone()
            if not row:
                raise ValueError('Meeting not found')
            selected_profile = self.check_analysis_profile(db,meeting)
            result = {k: row[k] for k in ('id','title','date','timezone','revision','active')}
            result['owner_profile'] = getattr(self,'owner_profile',None)
            result['analysis_profile'] = selected_profile
            composition = db.execute('SELECT parts FROM compositions WHERE meeting=?', (meeting,)).fetchone()
            result['recordings'] = json.loads(composition[0]) if composition else []
            result['job'] = dict(db.execute('SELECT id,state,error FROM jobs WHERE meeting=?', (meeting,)).fetchone())
            from .recovery import available
            result['can_recover_response'] = available(db, result['job']['id'])
            result['job']['local_recovery'] = db.execute('SELECT 1 FROM asr_recovery WHERE job=?', (result['job']['id'],)).fetchone() is not None
            plan = db.execute('SELECT * FROM asr_plans WHERE job=?',(result['job']['id'],)).fetchone()
            result['transcription_chunks'] = []
            if plan:
                result['job']['max_audio_seconds'] = json.loads(plan['settings'])['max_audio_seconds']
                result['transcription_chunks'] = [dict(index=index+1,start=p['start_frame']/plan['rate'],
                    duration=p['frames']/plan['rate'],state=p['state'],
                    recognized_segments=len(json.loads(p['segments'])) if p['segments'] is not None else None) for index,p in enumerate(db.execute(
                    "SELECT * FROM asr_parts WHERE job=? AND state!='superseded' ORDER BY start_frame",(result['job']['id'],)))]
            result['segments'] = []
            if row['active']:
                t = db.execute('SELECT segments FROM transcripts WHERE id=?', (row['active'],)).fetchone()
                cs = [dict(c) for c in db.execute('SELECT * FROM corrections WHERE transcript=? ORDER BY revision', (row['active'],))]
                result['segments'] = effective(json.loads(t[0]), cs)
                result['corrections'] = cs
            analysis = db.execute('SELECT data FROM analyses WHERE meeting=? AND transcript=? ORDER BY rowid DESC LIMIT 1', (meeting,row['active'])).fetchone()
            result['analysis'] = project(json.loads(analysis[0]), result) if analysis else None
            result['analysis_versions'] = [dict(a) for a in db.execute('SELECT id,transcript,revision,created FROM analyses WHERE meeting=? ORDER BY rowid DESC', (meeting,))]
        return result

    def check_analysis_profile(self, db, meeting):
        # Keep the old selection table as historical data, but ignore it. Analysis
        # belongs to the profile serving this meeting, just like its transcript.
        owner = getattr(self, 'owner_profile', None)
        actor = getattr(self, 'analysis_actor', None)
        if actor is not None and (actor != owner or meeting != getattr(self, 'analysis_meeting', None)):
            raise ValueError('This meeting belongs to another Hermes profile')
        return owner

    def save_analysis(self, snapshot, payload, *, provider=None, model=None, origin=None):
        from .analysis import fingerprint, validate
        # Validate at the persistence boundary too; no caller can bypass citations.
        payload = validate({ 'items': [{k:v for k,v in item.items() if k != 'id'} for item in payload['items']]}, snapshot)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.check_analysis_profile(db,snapshot['id'])
            current = db.execute('SELECT active,revision FROM meetings WHERE id=?', (snapshot['id'],)).fetchone()
            if not current or current['active'] != snapshot['active'] or current['revision'] != snapshot['revision']:
                raise ValueError('Transcript changed during analysis; nothing was saved. Generate notes again')
            old = db.execute('SELECT id,data FROM analyses WHERE meeting=? AND transcript=? ORDER BY rowid DESC LIMIT 1', (snapshot['id'],snapshot['active'])).fetchone()
            expected = snapshot.get('analysis')
            if (old['id'] if old else None) != (expected['id'] if expected else None):
                raise ValueError('Another analysis finished first; refresh to review it')
            previous = json.loads(old['data']) if old else None
            matches = {}
            for item in previous['items'] if previous else []:
                matches.setdefault(fingerprint(item), []).append(item['id'])
            reused = set()
            for item in payload['items']:
                candidates = matches.get(fingerprint(item), [])
                if len(candidates) == 1:
                    item['id'] = candidates[0]
                    reused.add(item['id'])
            referenced = {c['segment_id'] for item in payload['items'] for c in item['citations']}
            record = dict(id=uid(), transcript=snapshot['active'], revision=snapshot['revision'],
                          created=time.time(), provider=provider if isinstance(provider,str) else None,
                          model=model if isinstance(model,str) else None, items=payload['items'],
                          origin=origin,
                          profile=getattr(self,'analysis_actor',None),
                          previous_analysis=previous['id'] if previous else None,
                          unmatched_previous_items=[i['id'] for i in previous['items'] if i['id'] not in reused] if previous else [],
                          sources={s['id']:s for s in snapshot['segments'] if s['id'] in referenced})
            db.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?)', (record['id'],snapshot['id'],snapshot['active'],snapshot['revision'],record['created'],json.dumps(record)))
        return record

    def get_analysis(self, meeting, analysis_id):
        from .analysis import project
        current = self.get(meeting)
        with self.db() as db:
            row = db.execute('SELECT data FROM analyses WHERE id=? AND meeting=?', (analysis_id,meeting)).fetchone()
        if not row:
            raise ValueError('Analysis not found for this meeting')
        record = json.loads(row[0])
        if record['transcript'] != current['active']:
            # Old transcript: use its saved attribution, never labels from a new ASR version.
            current = {**current, 'segments':list(record['sources'].values()), 'revision':-1}
        return project(record,current)

    def correct(self, meeting, kind, target, value, revision):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            m = db.execute('SELECT * FROM meetings WHERE id=?', (meeting,)).fetchone()
            if not m or not m['active'] or m['revision'] != revision:
                raise ValueError('Transcript changed; refresh before correcting')
            segments = json.loads(db.execute('SELECT segments FROM transcripts WHERE id=?', (m['active'],)).fetchone()[0])
            labels = {s['label'] for s in segments}
            ids = {s['id'] for s in segments}
            if kind in ('merge','rename') and target not in labels:
                raise ValueError('Unknown participant')
            if kind in ('speaker','text') and target not in ids:
                raise ValueError('Unknown segment')
            if kind in ('merge','speaker') and value not in labels:
                raise ValueError('Unknown participant')
            if kind == 'undo':
                original = db.execute('SELECT kind FROM corrections WHERE id=? AND transcript=?', (value,m['active'])).fetchone()
                if not original or original[0] == 'undo' or db.execute("SELECT 1 FROM corrections WHERE kind='undo' AND value=?", (value,)).fetchone():
                    raise ValueError('Correction cannot be undone')
            if kind not in ('merge','rename','speaker','text','undo') or not isinstance(value,str) or not value or len(value)>10000:
                raise ValueError('Invalid correction')
            db.execute('INSERT INTO corrections VALUES(?,?,?,?,?,?)',(uid(),m['active'],revision+1,kind,target,value))
            db.execute('UPDATE meetings SET revision=revision+1 WHERE id=?',(meeting,))
        return self.get(meeting)
