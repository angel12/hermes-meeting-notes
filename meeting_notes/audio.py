import base64
import io
import subprocess
import wave
import tempfile
import os
import re
import time
from pathlib import Path


def prepare(source, target):
    """ffmpeg is a host prerequisite; never invoke a shell or modify source."""
    with tempfile.NamedTemporaryFile(dir=Path(target).parent,prefix=Path(target).stem+'.prepare-',suffix='.wav',delete=False) as f:
        temp = Path(f.name)
    try:
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(source),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(temp)], check=True, capture_output=True, timeout=300)
        with temp.open('rb') as f: os.fsync(f.fileno())
        os.replace(temp,target)
    finally:
        temp.unlink(missing_ok=True)


def cleanup_temporary_audio(root, active_attempts, active_meetings):
    """Only recognizable, abandoned derivatives older than one day are removed."""
    cutoff = time.time()-86400
    for path in (Path(root)/'audio').iterdir():
        part = re.fullmatch(r'([0-9a-f]{32})\.part\.wav',path.name)
        prep = re.fullmatch(r'([0-9a-f]{32})\.prepare-[^.]+\.wav',path.name)
        if not (part or prep) or (part and part[1] in active_attempts) or (prep and prep[1] in active_meetings):
            continue
        try:
            if not path.is_symlink() and path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass  # Cleanup never prevents recovery or schedules a transcription.


def slice_wave(source, target, start_frame, frames):
    """Exact, bounded PCM slice. Original and full playback derivative are untouched."""
    with wave.open(str(source),'rb') as src, open(target,'xb') as file:
        os.chmod(target,0o600)
        src.setpos(start_frame)
        with wave.open(file,'wb') as dst:
            dst.setparams(src.getparams())
            remaining = frames
            while remaining:
                count = min(remaining,16000*10)
                data = src.readframes(count)
                if len(data) != count*src.getnchannels()*src.getsampwidth():
                    raise ValueError('Prepared recording is truncated')
                dst.writeframesraw(data)
                remaining -= count
        file.flush()
        os.fsync(file.fileno())


def window(path, start, duration):
    if not 0 <= start or not 0 < duration <= 30:
        raise ValueError('Playback windows must be 0–30 seconds')
    output = io.BytesIO()
    with wave.open(str(path),'rb') as src:
        src.setpos(min(int(start*src.getframerate()), src.getnframes()))
        with wave.open(output,'wb') as dst:
            dst.setparams(src.getparams())
            dst.writeframes(src.readframes(int(duration*src.getframerate())))
    return dict(mime='audio/wav', base64=base64.b64encode(output.getvalue()).decode(), start=start)
