"""Host boundary. Single launch profile only; no copied request secret context."""
import threading
import time
import logging
import sqlite3
from .service import Service
from .worker import Worker
from .asr import NemoSpeechCpp

_lock = threading.Lock()
_workers = {}


def service():
    from agent.secret_scope import is_multiplex_active, serves_routed_profile
    from plugins.plugin_storage import plugin_data_dir
    if is_multiplex_active() or serves_routed_profile():
        raise ValueError('Meeting Notes MVP requires a dedicated single-profile host')
    from .profiles import current_profile
    svc = Service(plugin_data_dir('meeting_notes'))
    try:
        svc.owner_profile = current_profile()
    except ValueError:
        # Local import/transcription do not need a catalog identity. Profile
        # sharing remains fail-closed in select() and analysis_service().
        svc.owner_profile = None
    return svc


def analysis_service(meeting, source_profile=None):
    from .profiles import analysis_service as scoped
    return scoped(meeting,source_profile)


def work_loop(worker):
    while True:
        try:
            worked = worker.run_once()
        except sqlite3.OperationalError:
            # Recovery/claim remains authoritative: an uncertain attempt never
            # becomes queued here. Do not log exception text or private data.
            logging.getLogger(__name__).warning('Meeting worker database temporarily unavailable; retrying the queue check')
            time.sleep(5)
            continue
        if not worked:
            time.sleep(2)


def start():
    svc = service()
    key = str(svc.root.resolve())
    with _lock:
        if key in _workers and _workers[key].is_alive():
            return
        def run():
            def adapter(settings):
                # Recheck isolation inside worker. Do not retain request context or key.
                service()
                from agent.secret_scope import get_secret
                secret = get_secret('MEETING_NOTES_ASR_KEY')
                if not secret:
                    raise ValueError('Configure MEETING_NOTES_ASR_KEY in Hermes')
                return NemoSpeechCpp(settings,secret)
            worker = Worker(svc,adapter)
            work_loop(worker)
        thread = threading.Thread(target=run,name='meeting-notes-worker',daemon=True)
        _workers[key] = thread
        thread.start()
