"""Separate saved transcription connections. Credentials remain host-owned."""
import json
from urllib.parse import urlsplit

XAI_ENDPOINT = 'https://api.x.ai/v1'
ADAPTERS = {'custom': 'nemo_speech_cpp', 'xai': 'xai_oauth'}
FIELDS = {'endpoint', 'model', 'timeout', 'max_audio_seconds'}


def read(db):
    row = db.execute('SELECT data FROM transcription_connections WHERE id=1').fetchone()
    if row:
        return json.loads(row[0])
    legacy = db.execute('SELECT data FROM settings WHERE id=1').fetchone()
    custom = dict(endpoint='http://10.0.1.128:18082/v1', model='nemotron-3.5-transcribe-30b-a4b', timeout=600, max_audio_seconds=3600)
    if legacy:
        custom.update(json.loads(legacy[0]))
    return dict(active='custom', revision=0, connections=dict(custom=custom, xai=dict(
        endpoint=XAI_ENDPOINT, model='grok-voice-transcribe-2.0', timeout=600, max_audio_seconds=300)))


def effective(state):
    name = state['active']
    return {**state['connections'][name], 'adapter': ADAPTERS[name]}


def validate(name, value, previous):
    if name not in ADAPTERS:
        raise ValueError('Unknown transcription connection')
    if not isinstance(value, dict) or set(value) not in (FIELDS, FIELDS-{'max_audio_seconds'}):
        raise ValueError('Expected endpoint, model, timeout and max_audio_seconds; credentials belong in Hermes')
    value = {**value, 'max_audio_seconds': value.get('max_audio_seconds', previous['max_audio_seconds'])}
    if type(value['max_audio_seconds']) is not int or not 1 <= value['max_audio_seconds'] <= 86400:
        raise ValueError('Maximum audio per request must be 1–86400 seconds')
    if not isinstance(value['endpoint'], str):
        raise ValueError('Endpoint must be an HTTP(S) URL')
    url = urlsplit(value['endpoint'])
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Endpoint must be HTTP(S) without embedded credentials or query')
    value['endpoint'] = value['endpoint'].rstrip('/')
    if name == 'xai' and value['endpoint'] != XAI_ENDPOINT:
        raise ValueError('xAI OAuth requires https://api.x.ai/v1; custom endpoints are not allowed')
    if not isinstance(value['model'], str) or not value['model'].strip():
        raise ValueError('Model is required')
    if type(value['timeout']) is not int or not 30 <= value['timeout'] <= 7200:
        raise ValueError('Timeout must be 30–7200 seconds')
    return value


def write(db, state):
    state['revision'] += 1
    db.execute('INSERT OR REPLACE INTO transcription_connections VALUES(1,?)', (json.dumps(state),))
    # Preserve the working custom configuration for older plugin versions too.
    db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(state['connections']['custom']),))


def require_revision(state, revision):
    if type(revision) is not int or revision != state['revision']:
        raise ValueError('Transcription connections changed; reload settings')


def require_idle(db):
    if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','in_flight')").fetchone():
        raise ValueError('Wait for current transcription before changing the active connection')


def save(svc, name, value, revision):
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        state = read(db)
        require_revision(state, revision)
        if name not in ADAPTERS:
            raise ValueError('Unknown transcription connection')
        if state['active'] == name:
            require_idle(db)
        state['connections'][name] = validate(name, value, state['connections'][name])
        write(db, state)
        return state


def select(svc, name, revision):
    if name not in ADAPTERS:
        raise ValueError('Unknown transcription connection')
    if name == 'xai':
        from .asr import require_xai_host
        require_xai_host()
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        state = read(db)
        require_revision(state, revision)
        require_idle(db)
        if state['active'] != name:
            state['active'] = name
            write(db, state)
        return state
