import hashlib
import json
import math
import time
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .store import Store, atomic_write, uid

CHUNK = 8 * 1024 * 1024
MAX_AUDIO = 512 * 1024 * 1024


class Service(Store):
    def combine(self, **kwargs):
        from .composition import combine
        return combine(self, **kwargs)

    def audio_window(self, meeting, start=0, duration=20):
        from .composition import audio_window
        return audio_window(self, meeting, start, duration)

    def configure(self, settings):
        if set(settings) not in ({'endpoint','model','timeout'},{'endpoint','model','timeout','max_audio_seconds'}):
            raise ValueError('Expected endpoint, model, timeout and max_audio_seconds; key belongs in Hermes secrets')
        settings = {**settings,'max_audio_seconds':settings.get('max_audio_seconds',self.settings()['max_audio_seconds'])}
        if type(settings['max_audio_seconds']) is not int or not 1 <= settings['max_audio_seconds'] <= 86400:
            raise ValueError('Maximum audio per request must be 1–86400 seconds')
        url = urlsplit(settings['endpoint'])
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Endpoint must be HTTP(S) without embedded credentials or query')
        if not isinstance(settings['model'],str) or not settings['model'].strip():
            raise ValueError('Model is required')
        if isinstance(settings['timeout'],bool) or not isinstance(settings['timeout'],int) or not 30 <= settings['timeout'] <= 7200:
            raise ValueError('Timeout must be 30–7200 seconds')
        settings['endpoint'] = settings['endpoint'].rstrip('/')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','in_flight')").fetchone():
                raise ValueError('Wait for current transcription before changing endpoint')
            db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)',(json.dumps(settings),))
        return settings

    def begin_upload(self, size, title, date, timezone):
        from . import storage
        if isinstance(size,bool) or not isinstance(size,int) or not 0 < size <= MAX_AUDIO:
            raise ValueError('Audio must be between 1 byte and 512 MiB')
        if not isinstance(title,str) or not title.strip() or len(title)>300:
            raise ValueError('A title of 1–300 characters is required')
        dt = datetime.fromisoformat(date)
        if dt.tzinfo is None:
            raise ValueError('Meeting date must include a UTC offset')
        ZoneInfo(timezone)
        ident = uid()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            location = storage.check_root(self, storage.settings(self, db)['managed_path'])
            db.execute('INSERT INTO uploads VALUES(?,?,?,?,?)',(ident,size,json.dumps(dict(title=title,date=date,timezone=timezone)),None,time.time()))
            db.execute('INSERT INTO upload_locations VALUES(?,?)', (ident, str(location)))
        return dict(id=ident,chunk_size=CHUNK)

    def chunk(self, ident, index, data, sha):
        from .storage import upload_root
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            upload = db.execute('SELECT * FROM uploads WHERE id=?',(ident,)).fetchone()
            if not upload or upload['created'] < time.time()-86400 or upload['meeting']:
                raise ValueError('Upload missing, expired or finalized')
            if not 0 <= index < math.ceil(upload['size']/CHUNK):
                raise ValueError('Invalid chunk index')
            expected = min(CHUNK, upload['size'] - index*CHUNK)
            if len(data) != expected or hashlib.sha256(data).hexdigest() != sha:
                raise ValueError('Chunk size or hash mismatch')
            old = db.execute('SELECT sha FROM chunks WHERE upload=? AND idx=?',(ident,index)).fetchone()
            if old and old[0] != sha:
                raise ValueError('Conflicting duplicate chunk')
            atomic_write(upload_root(self, db, ident)/'uploads'/f'{ident}.{index}',data)
            db.execute('INSERT OR IGNORE INTO chunks VALUES(?,?,?,?)',(ident,index,sha,len(data)))
        return {'accepted':True}

    def finalize(self, ident):
        from .storage import upload_root
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            upload = db.execute('SELECT * FROM uploads WHERE id=?',(ident,)).fetchone()
            if not upload:
                raise ValueError('Unknown upload')
            if upload['meeting']:
                return {'id':upload['meeting']}
            chunks = list(db.execute('SELECT * FROM chunks WHERE upload=? ORDER BY idx',(ident,)))
            if len(chunks) != math.ceil(upload['size']/CHUNK):
                raise ValueError('Upload is incomplete')
            location = upload_root(self, db, ident)
            staging = location/'audio'/f'{ident}.upload'
            digest = hashlib.sha256()
            with staging.open('wb') as out:
                import os
                os.chmod(staging,0o600)
                for c in chunks:
                    data = (location/'uploads'/f'{ident}.{c["idx"]}').read_bytes()
                    if hashlib.sha256(data).hexdigest() != c['sha']:
                        raise ValueError('Stored chunk hash mismatch')
                    digest.update(data)
                    out.write(data)
                out.flush()
                os.fsync(out.fileno())
            sha = digest.hexdigest()
            old = db.execute('SELECT id FROM meetings WHERE sha=?',(sha,)).fetchone()
            meeting = old[0] if old else uid()
            if not old:
                final = location/'audio'/sha
                staging.replace(final)
                fd = os.open(final.parent, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
                m = json.loads(upload['metadata'])
                db.execute('INSERT INTO meetings(id,title,date,timezone,audio,sha) VALUES(?,?,?,?,?,?)',(meeting,m['title'],m['date'],m['timezone'],str(final),sha))
                db.execute('INSERT INTO jobs(id,meeting,state) VALUES(?,?,?)',(uid(),meeting,'queued'))
            else:
                staging.unlink()
            db.execute('UPDATE uploads SET meeting=? WHERE id=?',(meeting,ident))
        # The finalized copy is committed before removing duplicate upload chunks.
        for c in chunks:
            try: (location/'uploads'/f'{ident}.{c["idx"]}').unlink(missing_ok=True)
            except OSError: pass  # Recovery retries cleanup after the commit.
        return {'id':meeting, 'sha256':sha}

    def retry(self, meeting, use_current_settings=False):
        if type(use_current_settings) is not bool:
            raise ValueError('use_current_settings must be a boolean')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute("SELECT id FROM jobs WHERE meeting=? AND state IN ('interrupted_unknown','failed','invalid_response')",(meeting,)).fetchone()
            if not job:
                raise ValueError('Only stopped jobs can be retried')
            if use_current_settings:
                from .chunking import replan_unfinished
                replan_unfinished(db,job['id'],self.settings())
            cur = db.execute("UPDATE jobs SET state='queued',generation=generation+1,owner=NULL,lease=NULL,attempt=NULL,error=NULL WHERE meeting=? AND state IN ('interrupted_unknown','failed','invalid_response')",(meeting,))
            if not cur.rowcount:
                raise ValueError('Only stopped jobs can be retried')
        return self.get(meeting)
