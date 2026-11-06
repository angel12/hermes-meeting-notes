import base64
import io
import subprocess
import wave
from pathlib import Path


def prepare(source, target):
    """ffmpeg is a host prerequisite; never invoke a shell or modify source."""
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(source),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(target)], check=True, capture_output=True, timeout=300)


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
