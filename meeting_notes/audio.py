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
    frames = pcm_info(path)
    data = read_pcm(path, min(int(start*16000), frames), min(int(duration*16000), max(0, frames-int(start*16000))))
    with wave.open(output, 'wb') as dst:
        dst.setparams((1,2,16000,0,'NONE','not compressed'))
        dst.writeframes(data)
    return dict(mime='audio/wav', base64=base64.b64encode(output.getvalue()).decode(), start=start)


def pcm_info(path):
    """Inspect the canonical mono PCM16/16k playback stream (WAV or FLAC)."""
    path = Path(path)
    if path.suffix == '.wav':
        with wave.open(str(path), 'rb') as src:
            if (src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getcomptype()) != (1, 2, 16000, 'NONE'):
                raise ValueError('Prepared audio must be mono PCM16 at 16kHz')
            return src.getnframes()
    import json
    data = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
        'stream=codec_name,sample_rate,channels,duration_ts,time_base', '-of', 'json', str(path)],
        check=True, capture_output=True, timeout=30).stdout)['streams'][0]
    if data['codec_name'] != 'flac' or int(data['sample_rate']) != 16000 or data['channels'] != 1 or data['time_base'] != '1/16000':
        raise ValueError('Unsupported prepared archive')
    return int(data['duration_ts'])


def read_pcm(path, start_frame, frames, codec=None):
    if frames <= 0:
        return b''
    opened = hasattr(path, 'read')
    if opened:
        path.seek(0)
    if codec == '.wav' or (not opened and Path(path).suffix == '.wav'):
        with wave.open(path if opened else str(path), 'rb') as src:
            src.setpos(min(start_frame, src.getnframes()))
            return src.readframes(frames)
    # Decode only the requested sample range. No lossy seeking or timestamp rounding.
    return subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', 'pipe:0' if opened else str(path), '-map', '0:a:0',
        '-af', f'atrim=start_sample={start_frame}:end_sample={start_frame + frames}',
        '-f', 's16le', '-c:a', 'pcm_s16le', '-'], stdin=path if opened else None, check=True, capture_output=True, timeout=60).stdout


class ArchiveInputError(ValueError):
    pass


def require_single_track(path):
    import json
    streams = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a',
        '-show_entries', 'stream=index', '-of', 'json', str(path)],
        check=True, capture_output=True, timeout=30).stdout)['streams']
    if len(streams) != 1:
        raise ArchiveInputError('Opus replacement requires exactly one audio track. Keep the uploaded original for multi-track recordings; lossless playback compression is still available.')


def pcm_digest(path):
    import hashlib
    digest = hashlib.sha256()
    if Path(path).suffix == '.wav':
        with wave.open(str(path), 'rb') as src:
            while block := src.readframes(16000 * 10):
                digest.update(block)
    else:
        # A temporary hash output avoids buffering full recordings in memory.
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:a:0',
            '-c:a', 'pcm_s16le', '-f', 'hash', '-hash', 'sha256', '-'],
            check=True, capture_output=True, timeout=600).stdout.decode().strip()
        return result.split('=', 1)[1].lower()
    return digest.hexdigest()


def encode_archive(source, target, codec):
    """Write a new archive. Callers verify it and atomically publish its pointer."""
    options = ['-c:a', 'flac', '-compression_level', '5'] if codec == 'flac' else [
        '-ac', '1', '-c:a', 'libopus', '-b:a', '48k', '-vbr', 'on', '-application', 'voip']
    with open(target, 'xb') as f:
        os.chmod(target, 0o600)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source), '-map', '0:a:0',
        '-vn', '-map_metadata', '-1', *options, str(target)], check=True, capture_output=True, timeout=600)
    with open(target, 'rb') as f:
        os.fsync(f.fileno())


def verify_opus(path, frames):
    # Fully decode to prove the archive is readable; verify duration, not perceptual quality.
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:a:0',
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-f', 'framehash', '-'],
        check=True, capture_output=True, timeout=600).stdout.decode()
    count = sum(int(line.split(',')[3]) for line in result.splitlines() if line and not line.startswith('#'))
    if abs(count - frames) > 320:
        raise ValueError('Compressed archive duration does not match the recording')
