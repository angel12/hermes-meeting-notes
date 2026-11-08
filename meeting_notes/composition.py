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
from . import storage, audio as audio_files


def combine(svc, request_id, title, date, timezone, sources, own_audio=False):
    if type(own_audio) is not bool:
        raise ValueError('own_audio must be a boolean')
    return _combine(svc, request_id, title, date, timezone, sources, own_audio)


def _combine(svc, request_id, title, date, timezone, sources, own_audio):
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
    request_hash = hashlib.sha256(json.dumps([title,date,timezone,sources] + ([True] if own_audio else []),sort_keys=True).encode()).hexdigest()
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
            path = storage.playback_path(svc, db, m['id'])
            try:
                count = audio_files.pcm_info(path)
            except (OSError, wave.Error, EOFError):
                raise ValueError('A source recording is not available for playback') from None
            original = json.loads(db.execute('SELECT segments FROM transcripts WHERE id=?',(m['active'],)).fetchone()[0])
            corrections = [dict(c) for c in db.execute('SELECT * FROM corrections WHERE transcript=? ORDER BY revision',(m['active'],))]
            corrected = effective(original,corrections)
            if not corrected:
                raise ValueError('A source recording has no recognized words; review its audio before combining')
            if max(s['end'] for s in corrected) > count / 16000 + .1:
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
        if own_audio:
            from .composition_storage import enqueue
            enqueue(svc, meeting, db=db)
    return {'id':meeting}


def organize_audio(svc, meeting, expected_settings=None):
    from .composition_storage import enqueue
    enqueue(svc, meeting, expected_settings=expected_settings)
    return storage.status(svc, meeting)


def audio_window(svc, meeting, start, duration):
    from contextlib import ExitStack
    if (any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in (start,duration))
            or start < 0 or not 0 < duration <= 30):
        raise ValueError('Playback windows must be 0–30 seconds')
    snapshot = svc.get(meeting)
    begin, end = int(start*16000), int(start*16000)+int(duration*16000)
    with ExitStack() as files:
        reads = []
        # Open stable file handles under the short cleanup lock. POSIX unlink
        # cannot invalidate these readers; ffmpeg decoding happens after release.
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            parts = snapshot['recordings']
            if not parts:
                path = storage.playback_path(svc, db, meeting)
                asset = db.execute('SELECT frames FROM audio_assets WHERE meeting=?', (meeting,)).fetchone()
                count = asset[0] if asset else audio_files.pcm_info(path)
                parts = [{'meeting_id': meeting, 'frames': count}]
            offset = 0
            for part in parts:
                count = part['frames']
                left, right = max(begin, offset), min(end, offset+count)
                if left < right:
                    owned = db.execute('SELECT playback FROM composition_audio WHERE meeting=? AND part=?', (meeting, part.get('part'))).fetchone()
                    path = storage.check_audio_path(svc, owned[0]) if owned else storage.playback_path(svc, db, part['meeting_id'])
                    try:
                        file = files.enter_context(path.open('rb'))
                    except OSError:
                        raise ValueError('A source recording is unavailable') from None
                    reads.append((file, path.suffix, left-offset, right-left))
                offset += count
        output = io.BytesIO()
        with wave.open(output, 'wb') as dst:
            dst.setparams((1,2,16000,0,'NONE','not compressed'))
            for file, codec, offset, count in reads:
                data = audio_files.read_pcm(file, offset, count, codec)
                if len(data) != count*2:
                    raise ValueError('A source recording has changed; playback is unavailable')
                dst.writeframes(data)
    return dict(mime='audio/wav',base64=base64.b64encode(output.getvalue()).decode(),start=start)
