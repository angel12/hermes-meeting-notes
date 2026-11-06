"""Explicit, durable local reparse. This path never constructs an ASR adapter."""
import hashlib
import json
from .chunking import parse_part, publish
from .deletion import checked_file

STOPPED = ('failed', 'invalid_response', 'interrupted_unknown')


def available(db, job):
    return db.execute('''SELECT 1 FROM asr_parts p JOIN asr_responses r ON r.attempt=p.attempt
        WHERE p.job=? AND p.state NOT IN ('completed','superseded') AND r.status=200 LIMIT 1''', (job,)).fetchone() is not None


def request(svc, meeting):
    if getattr(svc, 'analysis_actor', None) or getattr(svc, 'handoff_actor', None):
        raise ValueError('Recover transcription from the importing profile')
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute('SELECT * FROM jobs WHERE meeting=?', (meeting,)).fetchone()
        if not job or job['state'] not in STOPPED:
            raise ValueError('Only stopped transcription jobs can recover saved responses')
        if not available(db, job['id']):
            raise ValueError('No recoverable HTTP 200 response with an integrity record is saved')
        db.execute('INSERT OR REPLACE INTO asr_recovery VALUES(?)', (job['id'],))
        db.execute("UPDATE jobs SET state='queued',generation=generation+1,owner=NULL,lease=NULL,attempt=NULL,error=NULL WHERE id=?", (job['id'],))
    return svc.get(meeting)


def run(worker, job):
    svc = worker.service
    with svc.db() as db:
        plan = dict(db.execute('SELECT * FROM asr_plans WHERE job=?', (job['id'],)).fetchone())
        parts = [dict(p) for p in db.execute("SELECT * FROM asr_parts WHERE job=? AND state!='superseded' ORDER BY start_frame", (job['id'],))]
    for part in parts:
        if part['state'] == 'completed':
            continue
        with svc.db() as db:
            row = db.execute('''SELECT r.status,r.sha,a.raw,s.settings FROM asr_responses r
                JOIN attempts a ON a.id=r.attempt JOIN asr_attempt_settings s ON s.attempt=r.attempt
                WHERE r.attempt=?''', (part['attempt'],)).fetchone()
        if not row or row['status'] != 200:
            continue
        # Old/moved profile paths and symlinks are not a grant to read foreign files.
        path = checked_file(svc, row['raw'])
        if path.parent != svc.root/'raw':
            raise ValueError('Response is outside this profile')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha']:
            raise ValueError('Saved response integrity check failed')
        settings = json.loads(row['settings'])
        segments = parse_part(data, part['attempt'], part['frames']/plan['rate'], len(parts)>1,
                              settings.get('adapter','nemo_speech_cpp'))
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not worker.fenced(db, job):
                return 'interrupted_unknown', 'Local recovery lease ended. No audio was sent.'
            db.execute("UPDATE asr_parts SET state='completed',segments=?,raw=?,sha=? WHERE job=? AND idx=? AND attempt=?",
                       (json.dumps(segments),path.name,row['sha'],job['id'],part['idx'],part['attempt']))
    with svc.db() as db:
        incomplete = db.execute("SELECT 1 FROM asr_parts WHERE job=? AND state NOT IN ('completed','superseded')", (job['id'],)).fetchone()
    if incomplete:
        return 'failed', 'Saved responses recovered where available. Unfinished audio remains; explicit Retry transcription is required to send it. No audio was sent by recovery.'
    if publish(worker, job, plan):
        return 'completed', None
    return 'interrupted_unknown', 'Local recovery lease ended. No audio was sent.'
