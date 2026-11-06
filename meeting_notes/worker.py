"""Durable synchronous requests: never automatically resend an uncertain attempt."""
import json
import hashlib
import threading
import time
from .store import atomic_write, uid
from .audio import prepare, slice_wave, cleanup_temporary_audio
from .chunking import plan_for, parse_part, publish, PreparedAudioMismatch
from . import storage
from .asr import SetupError

LEASE = 60


class Worker:
    def __init__(self, service, adapter_factory):
        self.service = service
        self.adapter_factory = adapter_factory
        self.owner = uid()

    def recover(self):
        with self.service.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Even preparation is conservative after process death. Retry is explicit.
            db.execute("UPDATE jobs SET state='interrupted_unknown',error='Host or lease ended. Completed chunks are retained; an unfinished request may have an unknown outcome. Explicit Retry required.' WHERE state IN ('preparing','in_flight') AND lease < ?",(time.time(),))
            db.execute("UPDATE asr_parts SET state='interrupted_unknown' WHERE state='in_flight' AND attempt IN (SELECT attempt FROM jobs WHERE state='interrupted_unknown')")
            active = list(db.execute("SELECT meeting,attempt FROM jobs WHERE state IN ('preparing','in_flight')"))
        cleanup_temporary_audio(self.service.root,{r['attempt'] for r in active},{r['meeting'] for r in active})
        with self.service.db() as db:
            roots = [r[0] for r in db.execute('SELECT path FROM storage_locations')]
        for root in roots:
            try:
                storage.check_root(self.service, root)
                cleanup_temporary_audio(root,{r['attempt'] for r in active},{r['meeting'] for r in active})
            except (ValueError, OSError):
                pass

    def claim(self):
        self.recover()
        with self.service.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # One worker per profile database. Runtime disallows multiplex/routed profiles.
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','in_flight') UNION ALL SELECT 1 FROM storage_jobs WHERE state='running'").fetchone():
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
        from . import deletion
        from .upload_cleanup import cleanup as cleanup_uploads
        cleanup_uploads(self.service)
        deletion.cleanup(self.service, automatic=True)
        if storage.run_once(self.service):
            return True
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
        sent, part_saved = False, False
        part_path = None
        wav = None
        state, error = 'failed', 'Preparation failed; check ffmpeg, endpoint, model and Hermes ASR secret.'
        try:
            from . import recovery
            with self.service.db() as db:
                local_only = db.execute('SELECT 1 FROM asr_recovery WHERE job=?', (job['id'],)).fetchone() is not None
            if local_only:
                state,error = 'invalid_response','Saved response recovery failed validation or integrity checks. No audio was sent. The saved response remains unchanged.'
                state,error = recovery.run(self, job)
                return True
            from pathlib import Path
            storage.check_root(self.service, Path(job['audio']).parent.parent)
            wav = Path(job['audio']).parent/f'{job["meeting"]}.wav'
            with self.service.db() as db:
                exists = db.execute('SELECT 1 FROM asr_plans WHERE job=?',(job['id'],)).fetchone()
            if not exists or not wav.exists():
                prepare(job['audio'],wav)
            plan = plan_for(self,job,wav)
            if plan is None:
                return True
            with self.service.db() as db:
                part = db.execute("SELECT * FROM asr_parts WHERE job=? AND state NOT IN ('completed','superseded') ORDER BY start_frame LIMIT 1",(job['id'],)).fetchone()
                total = db.execute("SELECT count(*) FROM asr_parts WHERE job=? AND state!='superseded'",(job['id'],)).fetchone()[0]
            if part is None:
                # Crash after the last part was saved: finalize locally, without
                # another request (or even resolving ASR credentials).
                state,error='failed','Saved chunks could not be assembled; Retry resumes without retranscribing completed chunks.'
                if publish(self,job,plan): state,error='completed',None
                return True
            adapter = self.adapter_factory(json.loads(plan['settings']))
            adapter.probe()
            if total>1:
                part_path = wav.parent/f'{job["attempt"]}.part.wav'
                slice_wave(wav,part_path,part['start_frame'],part['frames'])
            request_audio = part_path or wav
            with self.service.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self.fenced(db,job):
                    return True
                db.execute("UPDATE jobs SET state='in_flight' WHERE id=?",(job['id'],))
                db.execute('UPDATE attempts SET sent=? WHERE id=?',(time.time(),job['attempt']))
                db.execute('INSERT INTO asr_attempt_parts VALUES(?,?,?)',(job['attempt'],job['id'],part['idx']))
                db.execute('INSERT INTO asr_attempt_settings VALUES(?,?)',(job['attempt'],plan['settings']))
                db.execute("UPDATE asr_parts SET state='in_flight',attempt=? WHERE job=? AND idx=?",(job['attempt'],job['id'],part['idx']))
            sent = True
            status, data = adapter.transcribe(request_audio)
            path = self.service.root/'raw'/f'{job["attempt"]}.json'
            with self.service.db() as db:
                db.execute('BEGIN IMMEDIATE')
                # Deletion may follow lease recovery while the old HTTP call is
                # still returning. Never resurrect its raw file after deletion.
                if not db.execute('SELECT 1 FROM jobs WHERE id=?', (job['id'],)).fetchone():
                    return True
                atomic_write(path,data)
                db.execute('UPDATE attempts SET raw=? WHERE id=?',(str(path),job['attempt']))
                db.execute('INSERT INTO asr_responses VALUES(?,?,?)', (job['attempt'],status,hashlib.sha256(data).hexdigest()))
            state, error = 'invalid_response', 'Response saved but invalid; inspect adapter compatibility. No automatic resend.'
            if status != 200:
                state, error = 'interrupted_unknown', f'ASR HTTP {status}; response saved. Explicit Retry required.'
                if json.loads(plan['settings']).get('adapter') == 'xai_oauth' and status in (401,402,403,429):
                    error += ' Check the host xAI login, STT entitlement and usage limits. No API-key fallback was used.'
            else:
                segments = parse_part(data,job['attempt'],part['frames']/plan['rate'],total>1,
                                      json.loads(plan['settings']).get('adapter','nemo_speech_cpp'))
                with self.service.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self.fenced(db,job):
                        state,error='interrupted_unknown','Response saved after the lease ended; explicit Retry required.'
                        return True
                    db.execute("UPDATE asr_parts SET state='completed',segments=?,raw=?,sha=? WHERE job=? AND idx=? AND attempt=?",
                               (json.dumps(segments),path.name,hashlib.sha256(data).hexdigest(),job['id'],part['idx'],job['attempt']))
                part_saved = True
                state,error = 'failed','Saved chunks could not be assembled; Retry resumes without retranscribing completed chunks.'
                complete = publish(self,job,plan)
                state,error = ('completed' if complete else 'queued'),None
        except PreparedAudioMismatch as exc:
            state,error='failed',str(exc)
        except SetupError as exc:
            # These fixed errors are raised only before any audio submission.
            state,error='failed',str(exc)
            with self.service.db() as db:
                db.execute('UPDATE attempts SET sent=NULL WHERE id=?',(job['attempt'],))
        except Exception:
            # Never expose HTTP exception strings (URLs, headers, provider response or transcripts).
            if sent and state == 'failed' and not part_saved:
                state,error = 'interrupted_unknown','Request interrupted; result unknown. Explicit Retry required.'
        finally:
            stop.set()
            heartbeat.join()
            if part_path:
                part_path.unlink(missing_ok=True)
            with self.service.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if wav is not None and not db.execute('SELECT 1 FROM jobs WHERE id=?', (job['id'],)).fetchone():
                    # Preparation can finish after a lost lease and deletion.
                    # This meeting-specific derivative must not be resurrected.
                    try:
                        deletion.checked_file(self.service, wav).unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass
                if self.fenced(db,job):
                    db.execute('UPDATE jobs SET state=?,error=?,lease=NULL WHERE id=?',(state,error,job['id']))
                    if not part_saved:
                        db.execute('UPDATE asr_parts SET state=? WHERE job=? AND attempt=? AND state!=?',(state,job['id'],job['attempt'],'completed'))
                db.execute('UPDATE attempts SET outcome=? WHERE id=?',('completed' if part_saved else state,job['attempt']))
        return True
