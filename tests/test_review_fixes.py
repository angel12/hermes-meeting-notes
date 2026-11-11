"""Regression tests for the review findings: busy database, endpoint default,
agent tool scope and speaker reassignment followed by merge/rename."""
from contextlib import contextmanager
import importlib.util
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from meeting_notes import connections, deletion, recovery, store
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from meeting_notes.transcript import effective
from test_core import imported, Adapter


@pytest.fixture
def svc(tmp_path):
    return Service(tmp_path)


def no_sleep(monkeypatch):
    monkeypatch.setattr(store.time, 'sleep', lambda _: None)


def test_busy_database_after_response_keeps_raw_and_records_it(svc, monkeypatch):
    """The paid response is on disk before any lock, and a busy database only delays recording it."""
    mid = imported(svc)
    no_sleep(monkeypatch)
    real_record = Worker.record_response
    calls = []
    def flaky(self, job, path, status, data):
        calls.append(path.exists())
        if len(calls) < 3:
            raise sqlite3.OperationalError('database is locked')
        return real_record(self, job, path, status, data)
    monkeypatch.setattr(Worker, 'record_response', flaky)
    adapter = Adapter()
    assert Worker(svc, lambda _: adapter).run_once()
    assert calls == [True, True, True]  # Saved before the first database attempt.
    assert svc.get(mid)['job']['state'] == 'completed' and adapter.calls == 1


def test_lasting_busy_database_leaves_a_recoverable_response(svc, monkeypatch):
    """If recording never succeeds, the response file survives for later inspection
    and the outcome is never reported as an adapter problem."""
    mid = imported(svc)
    no_sleep(monkeypatch)
    def locked(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(Worker, 'record_response', locked)
    adapter = Adapter()
    Worker(svc, lambda _: adapter).run_once()
    raw = list((svc.root/'raw').glob('*.json'))
    assert len(raw) == 1 and json.loads(raw[0].read_bytes())['words']
    job = svc.get(mid)['job']
    assert job['state'] == 'interrupted_unknown' and 'Response saved to disk' in job['error']


def test_busy_database_when_saving_parsed_part_is_retried(svc, monkeypatch):
    mid = imported(svc)
    no_sleep(monkeypatch)
    real_fenced = Worker.fenced
    failures = []
    def fenced(self, db, job):
        # Fail the part save (third fenced call) twice.
        if len(failures) < 2 and getattr(self, '_saved_response', False):
            failures.append(True)
            raise sqlite3.OperationalError('database is locked')
        return real_fenced(self, db, job)
    real_record = Worker.record_response
    def record(self, *args):
        result = real_record(self, *args)
        self._saved_response = True
        return result
    monkeypatch.setattr(Worker, 'fenced', fenced)
    monkeypatch.setattr(Worker, 'record_response', record)
    Worker(svc, lambda _: Adapter()).run_once()
    assert failures == [True, True]
    assert svc.get(mid)['job']['state'] == 'completed'


def test_retry_busy_only_retries_busy_errors():
    calls = []
    def other():
        calls.append(1)
        raise sqlite3.OperationalError('no such table: x')
    with pytest.raises(sqlite3.OperationalError):
        store.retry_busy(other, sleep=lambda _: None)
    assert len(calls) == 1
    attempts = []
    def busy():
        attempts.append(1)
        raise sqlite3.OperationalError('database is locked')
    with pytest.raises(sqlite3.OperationalError):
        store.retry_busy(busy, attempts=3, sleep=lambda _: None)
    assert len(attempts) == 3


def test_asr_heartbeat_survives_a_busy_database(svc, monkeypatch):
    mid = imported(svc)
    with svc.db() as db:
        db.execute("UPDATE jobs SET state='in_flight',owner='o',attempt='a',lease=? WHERE meeting=?", (time.time()+5, mid))
        job = db.execute('SELECT id FROM jobs WHERE meeting=?', (mid,)).fetchone()[0]
    original_db = svc.db
    attempts = []
    @contextmanager
    def busy_once():
        attempts.append(True)
        if len(attempts) == 1:
            raise sqlite3.OperationalError('database is locked')
        with original_db() as db:
            yield db
    monkeypatch.setattr(svc, 'db', busy_once)
    class Stop:
        def wait(self, _):
            return len(attempts) >= 2
    before = time.time()+30
    store.renew_lease(svc, Stop(),
        "UPDATE jobs SET lease=? WHERE id=? AND owner=? AND attempt=? AND lease>=? AND state IN ('preparing','in_flight')",
        lambda: (time.time()+60, job, 'o', 'a', time.time()))
    assert len(attempts) == 2
    with original_db() as db:
        assert db.execute('SELECT lease FROM jobs WHERE id=?', (job,)).fetchone()[0] > before


def test_finalize_hashes_without_holding_the_write_lock(svc, monkeypatch):
    import hashlib, math
    from meeting_notes import service as service_module
    data = b'\x01' * 1000
    u = svc.begin_upload(len(data), 'Lock test', '2026-11-05T12:00:00-07:00', 'America/Denver')['id']
    svc.chunk(u, 0, data, hashlib.sha256(data).hexdigest())
    entered, resume = threading.Event(), threading.Event()
    real_sha = service_module.hashlib.sha256
    class Slow:
        def __init__(self, *a):
            self.inner = real_sha(*a)
            if not a:  # The streaming digest of the whole file.
                entered.set(); assert resume.wait(10)
        def update(self, b): self.inner.update(b)
        def hexdigest(self): return self.inner.hexdigest()
    monkeypatch.setattr(service_module.hashlib, 'sha256', Slow)
    result = {}
    worker = threading.Thread(target=lambda: result.update(svc.finalize(u)))
    worker.start()
    try:
        assert entered.wait(5)
        with svc.db() as db:  # A writer gets in while finalize is hashing.
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE uploads SET created=created WHERE id=?", (u,))
    finally:
        resume.set()
        worker.join(10)
    assert result['id'] and svc.get(result['id'])
    assert not list((svc.root/'audio').glob('*.upload'))


def test_concurrent_finalize_creates_one_meeting(svc):
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    data = b'\x02' * 1000
    u = svc.begin_upload(len(data), 'Race', '2026-11-05T12:00:00-07:00', 'America/Denver')['id']
    svc.chunk(u, 0, data, hashlib.sha256(data).hexdigest())
    with ThreadPoolExecutor(4) as pool:
        ids = {r['id'] for r in pool.map(lambda _: svc.finalize(u), range(4))}
    assert len(ids) == 1 and len(svc.list()) == 1
    assert not list((svc.root/'audio').glob('*.upload'))


def test_deletion_hashes_outside_the_write_lock(svc, monkeypatch):
    from meeting_notes import storage
    mid = imported(svc)
    Worker(svc, lambda _: Adapter()).run_once()
    token = deletion.review(svc, mid)['token']
    real = storage.digest
    def checked(path):
        # Another writer can take the lock while a file is hashed.
        with svc.db() as db:
            db.execute('BEGIN IMMEDIATE')
        return real(path)
    monkeypatch.setattr(storage, 'digest', checked)
    monkeypatch.setattr(svc, 'db', _short_timeout(svc.db))
    assert deletion.remove(svc, mid, token, True)['deleted']
    assert deletion.status(svc) == {'pending_files': 0}


def _short_timeout(db):
    # Make "hashed under the lock" fail fast instead of waiting 30 s.
    @contextmanager
    def quick():
        with db() as conn:
            conn.execute('PRAGMA busy_timeout=200')
            yield conn
    return quick


@pytest.mark.unconfigured_endpoint
def test_no_builtin_asr_endpoint(tmp_path, monkeypatch):
    svc = Service(tmp_path)
    assert svc.settings()['endpoint'] == ''
    from meeting_notes import runtime
    from meeting_notes.asr import SetupError
    monkeypatch.setattr(runtime, 'service', lambda: svc)
    with pytest.raises(SetupError, match='endpoint'):
        runtime.adapter_for(svc.settings(), svc.root)
    mid = imported(svc)
    Worker(svc, lambda s: runtime.adapter_for(s, svc.root)).run_once()
    job = svc.get(mid)['job']
    assert job['state'] == 'failed' and 'endpoint' in job['error']
    with svc.db() as db:
        assert db.execute('SELECT sent FROM attempts').fetchone()[0] is None


def _tools():
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location('meeting_tools_scope_test', root/'__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tools = {}
    class Context:
        def register_tool(self, **kwargs): tools[kwargs['name']] = kwargs
        def register_skill(self, *args, **kwargs): pass
    mod.register(Context())
    return tools


def test_agents_cannot_edit_transcript_evidence_or_resend_audio():
    tools = _tools()
    assert 'meeting_correct' not in tools and 'meeting_retry_transcription' not in tools
    rename = tools['meeting_rename_speaker']['schema']['parameters']
    assert set(rename['properties']) == {'meeting_id', 'participant', 'name', 'revision'}
    assert rename['additionalProperties'] is False


def test_agent_rename_tool_only_renames(svc, monkeypatch):
    from meeting_notes import runtime
    mid = imported(svc)
    Worker(svc, lambda _: Adapter()).run_once()
    monkeypatch.setattr(runtime, 'service', lambda: svc)
    handler = _tools()['meeting_rename_speaker']['handler']
    result = json.loads(handler({'meeting_id': mid, 'participant': '1', 'name': 'Alice', 'revision': 0}))
    assert [c['kind'] for c in result['corrections']] == ['rename']
    assert result['segments'][0]['name'] == 'Alice'
    error = json.loads(handler({'meeting_id': mid, 'participant': result['segments'][0]['id'], 'name': 'x', 'revision': 1}))
    assert 'error' in error


def test_reassigned_turn_follows_a_later_merge_and_rename():
    segments = [dict(id='a', label='1', text='', start=0, end=1, word_count=1),
                dict(id='b', label='2', text='', start=1, end=2, word_count=1),
                dict(id='c', label='3', text='', start=2, end=3, word_count=1)]
    corrections = [dict(id='x1', kind='speaker', target='c', value='2'),
                   dict(id='x2', kind='merge', target='2', value='1'),
                   dict(id='x3', kind='rename', target='1', value='Alice')]
    result = effective(segments, corrections)
    assert [(s['participant'], s['name']) for s in result] == [('1', 'Alice')] * 3
    # Undoing the merge returns the reassigned turn to speaker 2.
    result = effective(segments, corrections + [dict(id='x4', kind='undo', target='', value='x2')])
    assert [s['participant'] for s in result] == ['1', '2', '2']
    # A reassignment made after a merge still wins.
    result = effective(segments, corrections + [dict(id='x5', kind='speaker', target='c', value='3')])
    assert result[2]['participant'] == '3'
