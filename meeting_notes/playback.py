"""Read-only full-meeting timeline and bounded waveform envelopes."""
from contextlib import contextmanager, ExitStack
from array import array
import math
import subprocess
import sys
import threading
import wave
from . import storage, audio

_SCAN_SLOTS = threading.BoundedSemaphore(2)


@contextmanager
def _sources(svc, meeting):
    snapshot = svc.get(meeting)
    with ExitStack() as stack:
        opened = []
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
            parts = snapshot['recordings'] or [{'meeting_id': meeting}]
            for part in parts:
                owned = db.execute('SELECT playback FROM composition_audio WHERE meeting=? AND part=?', (meeting, part.get('part'))).fetchone()
                path = storage.check_audio_path(svc, owned[0]) if owned else storage.playback_path(svc, db, part['meeting_id'])
                file = stack.enter_context(path.open('rb'))
                asset = db.execute('SELECT frames FROM audio_assets WHERE meeting=?', (part['meeting_id'],)).fetchone()
                frames = part.get('frames', asset[0] if asset else None)
                if frames is None:
                    frames = audio.pcm_info(path)
                opened.append((file, path.suffix, frames))
        yield opened


@contextmanager
def sources(svc, meeting):
    try:
        with _sources(svc, meeting) as files:
            yield files
    except (OSError, wave.Error, EOFError):
        raise ValueError('A source recording is unavailable') from None


def timeline(svc, meeting):
    with sources(svc, meeting) as files:
        return {'duration': sum(f[2] for f in files)/16000, 'window_seconds': 30}


@contextmanager
def pcm_stream(file, codec):
    if codec == '.wav':
        with wave.open(file, 'rb') as reader:
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 16000):
                raise ValueError('Unsupported playback audio')
            yield reader.readframes
    else:
        process = subprocess.Popen(['ffmpeg','-nostdin','-v','error','-i','pipe:0','-map','0:a:0',
            '-f','s16le','-c:a','pcm_s16le','-'], stdin=file, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        timer = threading.Timer(120, process.kill)
        timer.start()
        try:
            yield lambda count: process.stdout.read(count*2)
            if process.stdout.read(1):
                raise ValueError('Playback audio duration changed')
            if process.wait(timeout=5):
                raise ValueError('Could not decode playback audio')
        finally:
            timer.cancel()
            if process.poll() is None: process.kill()
            process.wait()
            process.stdout.close()


def _waveform(svc, meeting):
    with sources(svc, meeting) as files:
        frames = sum(f[2] for f in files)
        width = max(1, math.ceil(frames/1200))
        peaks, peak, used = [], 0, 0
        for file, codec, count in files:
            with pcm_stream(file, codec) as read:
                remaining = count
                while remaining:
                    n = min(16000, width-used, remaining)
                    data = read(n)
                    if len(data) != n*2: raise ValueError('Playback audio is truncated')
                    samples = array('h', data)
                    if sys.byteorder != 'little': samples.byteswap()
                    peak = max(peak, max((abs(x) for x in samples), default=0))
                    used += n
                    remaining -= n
                    if used == width:
                        peaks.append(round(peak/32768, 4)); peak, used = 0, 0
        if used: peaks.append(round(peak/32768, 4))
        return {'duration': frames/16000, 'peaks': peaks}


def waveform(svc, meeting):
    # Fail fast instead of queueing uncancellable scans from abandoned views.
    if not _SCAN_SLOTS.acquire(blocking=False):
        raise ValueError('Waveform service is busy; reopen the meeting to try again')
    try:
        return _waveform(svc, meeting)
    finally:
        _SCAN_SLOTS.release()
