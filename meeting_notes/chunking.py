"""Durable ASR request boundaries and immutable transcript assembly."""
import hashlib
import json
import wave

from .store import atomic_write
from .transcript import normalize


class PreparedAudioMismatch(ValueError):
    pass


def frame_ranges(start, frames, limit):
    """Balanced requests stay under the cap without a tiny tail request."""
    count = (frames+limit-1)//limit
    width, extra = divmod(frames,count)
    for index in range(count):
        size = width + (index < extra)
        yield start,size
        start += size


def replan_unfinished(db, job, settings):
    plan = db.execute('SELECT * FROM asr_plans WHERE job=?',(job,)).fetchone()
    if not plan:
        return
    parts = list(db.execute("SELECT * FROM asr_parts WHERE job=? AND state!='superseded' ORDER BY start_frame",(job,)))
    pending = [p for p in parts if p['state'] != 'completed']
    if not pending:
        return
    if any(p['state']=='completed' for p in parts) and settings['model'] != json.loads(plan['settings'])['model']:
        raise ValueError('Keep the original model when reusing completed chunks; endpoint, timeout and duration limit may change')
    next_index = db.execute('SELECT max(idx)+1 FROM asr_parts WHERE job=?',(job,)).fetchone()[0]
    # Keep replaced rows and their attempt links for audit; completed parts are immutable.
    for part in pending:
        db.execute("UPDATE asr_parts SET state='superseded' WHERE job=? AND idx=?",(job,part['idx']))
        for start,frames in frame_ranges(part['start_frame'],part['frames'],settings['max_audio_seconds']*plan['rate']):
            db.execute('INSERT INTO asr_parts(job,idx,start_frame,frames,state) VALUES(?,?,?,?,?)',
                       (job,next_index,start,frames,'queued'))
            next_index += 1
    db.execute('UPDATE asr_plans SET settings=? WHERE job=?',(json.dumps(settings),job))


def plan_for(worker, job, wav):
    with wave.open(str(wav),'rb') as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
        if not frames or (audio.getnchannels(),audio.getsampwidth(),rate) != (1,2,16000):
            raise ValueError('Expected nonempty mono PCM16 16kHz audio')
    with worker.service.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not worker.fenced(db,job):
            return None
        plan = db.execute('SELECT * FROM asr_plans WHERE job=?',(job['id'],)).fetchone()
        if not plan:
            settings = worker.service.settings()
            limit = settings['max_audio_seconds']*rate
            db.execute('INSERT INTO asr_plans VALUES(?,?,?,?,?)',
                       (job['id'],job['attempt'],json.dumps(settings),frames,rate))
            db.executemany('INSERT INTO asr_parts(job,idx,start_frame,frames,state) VALUES(?,?,?,?,?)',
                           ((job['id'],idx,start,size,'queued')
                            for idx,(start,size) in enumerate(frame_ranges(0,frames,limit))))
            plan = db.execute('SELECT * FROM asr_plans WHERE job=?',(job['id'],)).fetchone()
        if (plan['frames'],plan['rate']) != (frames,rate):
            raise PreparedAudioMismatch('Prepared audio no longer matches saved chunk boundaries. Restore the original prepared WAV before retrying; completed chunks were retained.')
        return dict(plan)


def parse_part(data, attempt, duration, split):
    raw = json.loads(data)
    if not isinstance(raw,dict):
        raise ValueError('Expected an ASR response object')
    # No recognized words is not proof of silence. Preserve the response and
    # expose the empty part in the UI; do not fabricate speech or diarization.
    if raw.get('words') == [] and isinstance(raw.get('text'),str) and not raw['text'].strip():
        return []
    segments = normalize(raw,attempt)
    if split and any(s['end'] > duration+.1 for s in segments):
        raise ValueError('ASR timestamps exceed the requested audio chunk')
    return segments


def publish(worker, job, plan):
    """Publish only when every part is durable, under the current lease fence."""
    svc = worker.service
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not worker.fenced(db,job):
            return False
        parts = [dict(p) for p in db.execute("SELECT * FROM asr_parts WHERE job=? AND state!='superseded' ORDER BY start_frame",(job['id'],))]
        if any(p['state'] != 'completed' for p in parts):
            db.execute("UPDATE jobs SET state='queued',lease=NULL,owner=NULL,error=NULL WHERE id=?",(job['id'],))
            return False
        segments, labels, sources = [], {}, []
        split = len(parts)>1
        for sequence,part in enumerate(parts,1):
            # Persisted successful parts are reused, never retranscribed on Retry.
            raw = svc.root/'raw'/part['raw']
            if hashlib.sha256(raw.read_bytes()).hexdigest() != part['sha']:
                raise ValueError('Saved transcription response changed')
            offset = part['start_frame']/plan['rate']
            for segment in json.loads(part['segments']):
                item = {**segment,'id':f'{plan["transcript"]}:s{len(segments):05d}'}
                if split:
                    key = (part['idx'],segment['label'])
                    labels.setdefault(key,str(len(labels)+1))
                    item.update(label=labels[key],start=offset+segment['start'],end=offset+segment['end'],
                                chunk=dict(index=sequence,start=segment['start'],end=segment['end'],
                                           raw_speaker=segment['label'],attempt=part['attempt']))
                segments.append(item)
            settings_row = db.execute('SELECT settings FROM asr_attempt_settings WHERE attempt=?',(part['attempt'],)).fetchone()
            sources.append(dict(index=sequence,start=offset,duration=part['frames']/plan['rate'],
                                attempt=part['attempt'],raw=part['raw'],sha256=part['sha'],
                                settings=json.loads(settings_row[0]) if settings_row else None))
        if split:
            data = json.dumps(dict(kind='chunked_transcription',version=1,parts=sources,
                                   settings=json.loads(plan['settings']))).encode()
            raw_path = svc.root/'raw'/f'{plan["transcript"]}.chunks.json'
            atomic_write(raw_path,data)
            sha = hashlib.sha256(data).hexdigest()
        else:
            raw_path = svc.root/'raw'/parts[0]['raw']
            sha = parts[0]['sha']
        db.execute('INSERT INTO transcripts VALUES(?,?,?,?,?)',
                   (plan['transcript'],job['meeting'],str(raw_path),sha,json.dumps(segments)))
        db.execute('UPDATE meetings SET active=? WHERE id=?',(plan['transcript'],job['meeting']))
        db.execute("UPDATE jobs SET state='completed',error=NULL,lease=NULL WHERE id=?",(job['id'],))
    return True
