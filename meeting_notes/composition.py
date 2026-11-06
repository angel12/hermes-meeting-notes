"""Ordered transcript snapshots; original recordings and ASR responses stay intact."""
import base64
from datetime import datetime
import hashlib
import io
import json
import math
import wave
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import atomic_write, uid
from .transcript import effective


def combine(svc, request_id, title, date, timezone, sources):
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
        raise ValueError('A stable request ID is required')
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError('A title of 1–300 characters is required')
    try:
        if datetime.fromisoformat(date).tzinfo is None:
            raise ValueError()
        ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise ValueError('Use a meeting date with UTC offset and a valid timezone') from None
    if not isinstance(sources, list) or not 2 <= len(sources) <= 20:
        raise ValueError('Choose 2–20 recordings in playback order')
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {'id','active','revision'}
                or not isinstance(source['id'], str) or not isinstance(source['active'], str)
                or type(source['revision']) is not int or source['revision'] < 0):
            raise ValueError('Each source needs its meeting ID, active transcript and revision')
    if len({s['id'] for s in sources}) != len(sources):
        raise ValueError('A recording can only appear once')
    request_hash = hashlib.sha256(json.dumps([title,date,timezone,sources],sort_keys=True).encode()).hexdigest()
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM compositions WHERE request_id=?',(request_id,)).fetchone()
        if previous:
            if previous['request_hash'] != request_hash:
                raise ValueError('This request ID was already used for a different combination')
            return {'id':previous['meeting']}
        meeting, transcript = uid(), uid()
        segments, parts = [], []
        frames, label_count = 0, 0
        for index, source in enumerate(sources):
            m = db.execute('SELECT m.*,j.state FROM meetings m JOIN jobs j ON j.meeting=m.id WHERE m.id=?',(source['id'],)).fetchone()
            if not m or m['state'] != 'completed' or not m['active']:
                raise ValueError('Every recording must have a completed transcript')
            if db.execute('SELECT 1 FROM compositions WHERE meeting=?',(m['id'],)).fetchone():
                raise ValueError('Choose the original recordings, not an already combined meeting')
            if m['active'] != source['active'] or m['revision'] != source['revision']:
                raise ValueError('A source transcript changed; refresh the selection before combining')
            path = svc.root/'audio'/f'{m["id"]}.wav'
            try:
                with wave.open(str(path),'rb') as audio:
                    if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate(),audio.getcomptype()) != (1,2,16000,'NONE'):
                        raise ValueError('A prepared recording has an unsupported audio format')
                    count = audio.getnframes()
            except (OSError, wave.Error, EOFError):
                raise ValueError('A source recording is not available for playback') from None
            original = json.loads(db.execute('SELECT segments FROM transcripts WHERE id=?',(m['active'],)).fetchone()[0])
            corrections = [dict(c) for c in db.execute('SELECT * FROM corrections WHERE transcript=? ORDER BY revision',(m['active'],))]
            corrected = effective(original,corrections)
            if not corrected or max(s['end'] for s in corrected) > count / 16000 + .1:
                raise ValueError('A source transcript extends beyond its recording; review it before combining')
            part = dict(meeting_id=m['id'], transcript_id=m['active'], revision=m['revision'],
                        title=m['title'], part=index+1, offset=frames/16000, frames=count,
                        duration=count/16000, date=m['date'], timezone=m['timezone'])
            parts.append(part)
            labels = {}
            for s in corrected:
                if s['participant'] not in labels:
                    label_count += 1
                    labels[s['participant']] = str(label_count)
                segments.append(dict(id=f'{transcript}:s{len(segments):05d}',
                                     label=labels[s['participant']], base_name=s['name'],
                                     start=s['start']+frames/16000, end=s['end']+frames/16000,
                                     text=s['text'], word_count=s['word_count'],
                                     source=dict(meeting_id=m['id'], transcript_id=m['active'],
                                                 revision=m['revision'], segment_id=s['id'],
                                                 start=s['start'], end=s['end'], part=index+1)))
            frames += count
        # This is a composition manifest, explicitly not a fabricated ASR response.
        raw = json.dumps({'kind':'transcript_composition','version':1,'parts':parts,'segments':segments}).encode()
        raw_path = svc.root/'raw'/f'{transcript}.composition.json'
        atomic_write(raw_path,raw)
        db.execute('INSERT INTO meetings(id,title,date,timezone,audio,sha,active) VALUES(?,?,?,?,?,?,?)',
                   (meeting,title.strip(),date,timezone,'','composition:'+meeting,transcript))
        db.execute('INSERT INTO transcripts VALUES(?,?,?,?,?)',
                   (transcript,meeting,str(raw_path),hashlib.sha256(raw).hexdigest(),json.dumps(segments)))
        db.execute('INSERT INTO jobs(id,meeting,state) VALUES(?,?,?)',(uid(),meeting,'completed'))
        db.execute('INSERT INTO compositions VALUES(?,?,?,?)',(meeting,request_id,request_hash,json.dumps(parts)))
    return {'id':meeting}


def audio_window(svc, meeting, start, duration):
    from .audio import window
    if (any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in (start,duration))
            or start < 0 or not 0 < duration <= 30):
        raise ValueError('Playback windows must be 0–30 seconds')
    snapshot = svc.get(meeting)
    parts = snapshot['recordings']
    if not parts:
        path = svc.root/'audio'/f'{meeting}.wav'
        if not path.exists():
            raise ValueError('Audio is not prepared yet')
        return window(path,start,duration)
    begin, end = int(start*16000), int(start*16000)+int(duration*16000)
    output = io.BytesIO()
    with wave.open(output,'wb') as dst:
        dst.setparams((1,2,16000,0,'NONE','not compressed'))
        offset = 0
        for part in parts:
            count = part['frames']
            left, right = max(begin,offset), min(end,offset+count)
            if left < right:
                path = svc.root/'audio'/f'{part["meeting_id"]}.wav'
                try:
                    with wave.open(str(path),'rb') as src:
                        src.setpos(left-offset)
                        audio = src.readframes(right-left)
                        if len(audio) != (right-left)*2:
                            raise ValueError('A source recording has changed; playback is unavailable')
                        dst.writeframes(audio)
                except (OSError, wave.Error, EOFError):
                    raise ValueError('A source recording is unavailable') from None
            offset += count
    return dict(mime='audio/wav',base64=base64.b64encode(output.getvalue()).decode(),start=start)
