"""Host boundary. Single launch profile only; no copied request secret context."""
import threading
import time
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
    svc.owner_profile = current_profile()
    return svc


def analysis_service(meeting, source_profile=None):
    from .profiles import analysis_service as scoped
    return scoped(meeting,source_profile)


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
            while True:
                if not worker.run_once():
                    time.sleep(2)
        thread = threading.Thread(target=run,name='meeting-notes-worker',daemon=True)
        _workers[key] = thread
        thread.start()
