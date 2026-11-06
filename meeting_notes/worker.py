"""Durable synchronous requests: never automatically resend an uncertain attempt."""
import json
import hashlib
import threading
import time
from .store import atomic_write, uid
from .transcript import normalize
from .audio import prepare

LEASE = 60


class Worker:
    def __init__(self, service, adapter_factory):
        self.service = service
        self.adapter_factory = adapter_factory
        self.owner = uid()

    def recover(self):
        with self.service.db() as db:
            # Even preparation is conservative after process death. Retry is explicit.
            db.execute("UPDATE jobs SET state='interrupted_unknown',error='Host or lease ended; result unknown. Explicit Retry required.' WHERE state IN ('preparing','in_flight') AND lease < ?",(time.time(),))

    def claim(self):
        self.recover()
        with self.service.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # One worker per profile database. Runtime disallows multiplex/routed profiles.
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','in_flight')").fetchone():
                return None
            job = db.execute("SELECT j.*,m.audio FROM jobs j JOIN meetings m ON m.id=j.meeting WHERE j.state='queued' ORDER BY j.rowid LIMIT 1").fetchone()
            if not job:
                return None
            attempt = uid()
            db.execute('INSERT INTO attempts(id,job,generation,started) VALUES(?,?,?,?)',(attempt,job['id'],job['generation'],time.time()))
            db.execute("UPDATE jobs SET state='preparing',owner=?,lease=?,attempt=? WHERE id=?",(self.owner,time.time()+LEASE,attempt,job['id']))
            return {**dict(job),'attempt':attempt}

    def fenced(self, db, job):
        return db.execute("SELECT 1 FROM jobs WHERE id=? AND owner=? AND generation=? AND attempt=? AND lease>=? AND state IN ('preparing','in_flight')",(job['id'],self.owner,job['generation'],job['attempt'],time.time())).fetchone()

    def run_once(self):
        job = self.claim()
        if not job:
            return False
        stop = threading.Event()
        def beat():
            while not stop.wait(10):
                with self.service.db() as db:
                    db.execute("UPDATE jobs SET lease=? WHERE id=? AND owner=? AND attempt=? AND lease>=? AND state IN ('preparing','in_flight')",(time.time()+LEASE,job['id'],self.owner,job['attempt'],time.time()))
        heartbeat = threading.Thread(target=beat,daemon=True)
        heartbeat.start()
        sent = False
        state, error = 'failed', 'Preparation failed; check ffmpeg, endpoint, model and Hermes ASR secret.'
        try:
            adapter = self.adapter_factory(self.service.settings())
            adapter.probe()
            wav = self.service.root/'audio'/f'{job["meeting"]}.wav'
            prepare(job['audio'],wav)
            with self.service.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self.fenced(db,job):
                    return True
                db.execute("UPDATE jobs SET state='in_flight' WHERE id=?",(job['id'],))
                db.execute('UPDATE attempts SET sent=? WHERE id=?',(time.time(),job['attempt']))
            sent = True
            status, data = adapter.transcribe(wav)
            path = self.service.root/'raw'/f'{job["attempt"]}.json'
            atomic_write(path,data)
            with self.service.db() as db:
                db.execute('UPDATE attempts SET raw=? WHERE id=?',(str(path),job['attempt']))
            state, error = 'invalid_response', 'Response saved but invalid; inspect adapter compatibility. No automatic resend.'
            if status != 200:
                state, error = 'interrupted_unknown', f'ASR HTTP {status}; response saved. Explicit Retry required.'
            else:
                segments = normalize(json.loads(data),job['attempt'])
                with self.service.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self.fenced(db,job):
                        return True
                    db.execute('INSERT INTO transcripts VALUES(?,?,?,?,?)',(job['attempt'],job['meeting'],str(path),hashlib.sha256(data).hexdigest(),json.dumps(segments)))
                    db.execute('UPDATE meetings SET active=? WHERE id=?',(job['attempt'],job['meeting']))
                    db.execute("UPDATE jobs SET state='completed',error=NULL,lease=NULL WHERE id=?",(job['id'],))
                state,error = 'completed',None
        except Exception:
            # Never expose HTTP exception strings (URLs, headers, provider response or transcripts).
            if sent and state == 'failed':
                state,error = 'interrupted_unknown','Request interrupted; result unknown. Explicit Retry required.'
        finally:
            stop.set()
            heartbeat.join()
            with self.service.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if self.fenced(db,job):
                    db.execute('UPDATE jobs SET state=?,error=?,lease=NULL WHERE id=?',(state,error,job['id']))
                db.execute('UPDATE attempts SET outcome=? WHERE id=?',(state,job['attempt']))
        return True
