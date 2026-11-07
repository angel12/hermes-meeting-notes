from contextvars import ContextVar
import hashlib
import importlib.util
from pathlib import Path
import sys
import re
from types import ModuleType

import pytest

from meeting_notes import profiles
from meeting_notes.analysis import read_transcript
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from test_analysis import payload, save
from test_core import imported, Adapter


@pytest.fixture
def homes(tmp_path, monkeypatch):
    dirs={'default':tmp_path,'research':tmp_path/'profiles/research','other':tmp_path/'profiles/other'}
    for path in dirs.values(): path.mkdir(parents=True,exist_ok=True)
    current=ContextVar('profile',default=tmp_path)
    bound=ContextVar('bound',default=None)
    multiplex=ContextVar('multiplex',default=False)
    def module(name,**attrs):
        value=ModuleType(name)
        for k,v in attrs.items(): setattr(value,k,v)
        monkeypatch.setitem(sys.modules,name,value)
    module('hermes_constants',get_hermes_home=current.get,get_default_hermes_root=lambda:tmp_path,
           named_profile_is_live=lambda path:path.name in dirs,PROFILE_ID_RE=re.compile(r'[a-z0-9][a-z0-9_-]{0,63}'))
    module('agent.secret_scope',current_secret_scope_home=bound.get,is_multiplex_active=multiplex.get,
           serves_routed_profile=multiplex.get,get_secret=lambda *_:pytest.fail('Notes must never read credentials'))
    svc=Service(tmp_path/'plugin-data/meeting_notes'); svc.owner_profile='default'
    mid=imported(svc); Worker(svc,lambda _:Adapter()).run_once()
    return svc,mid,dirs,current,bound,multiplex


def legacy_selection(svc, mid):
    with svc.db() as db:
        db.execute('INSERT OR REPLACE INTO meeting_profiles VALUES(?,?)', (mid, 'research'))


def test_legacy_selection_is_ignored_and_owner_can_read_save(homes):
    svc, mid, dirs, current, *_ = homes
    legacy_selection(svc, mid)
    original = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (svc.root/'raw').iterdir()}
    assert profiles.options() == {'current':'default', 'profiles':['default']}
    scoped = profiles.analysis_service(mid, 'default')
    page = read_transcript(scoped, mid)
    assert page['source_profile'] == page['analysis_profile'] == 'default'
    result = save(scoped, mid, payload(scoped.get(mid)))
    assert result['profile'] == 'default'
    assert svc.get(mid)['analysis']['id'] == result['id']
    assert svc.get(mid)['revision'] == 0
    with svc.db() as db:
        assert db.execute('SELECT profile FROM meeting_profiles WHERE meeting=?',(mid,)).fetchone()[0] == 'research'
    for path, digest in original.items(): assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_other_profiles_cannot_use_old_grant_or_open_foreign_store(homes, monkeypatch):
    svc, mid, dirs, current, *_ = homes
    legacy_selection(svc, mid)
    current.set(dirs['research'])
    monkeypatch.setattr(profiles.sqlite3, 'connect', lambda *a, **k: pytest.fail('Foreign database must not be opened'))
    with pytest.raises(ValueError, match='cross-profile'): profiles.analysis_service(mid, 'default')
    with pytest.raises(ValueError, match='cross-profile'): profiles.analysis_service(mid, '../../default')
    assert not (dirs['research']/'plugin-data').exists()


def test_stale_cross_profile_service_cannot_read_or_save(homes):
    svc, mid, *_ = homes
    snapshot = svc.get(mid)
    svc.analysis_actor = 'research'; svc.analysis_meeting = mid
    with pytest.raises(ValueError, match='another Hermes profile'): svc.get(mid)
    with pytest.raises(ValueError, match='another Hermes profile'): svc.save_analysis(snapshot, payload(snapshot))


def test_corrected_revision_still_invalidates_inflight_notes(homes):
    svc, mid, *_ = homes
    scoped = profiles.analysis_service(mid)
    snapshot = scoped.get(mid)
    svc.correct(mid, 'rename', '1', 'Alex', 0)
    with pytest.raises(ValueError, match='changed during analysis'): scoped.save_analysis(snapshot, payload(snapshot))
    assert svc.get(mid)['analysis'] is None


def test_multiplex_identity_must_be_bound_and_match(homes):
    svc, mid, dirs, current, bound, multiplex = homes
    multiplex.set(True)
    with pytest.raises(ValueError, match='bind a profile'): profiles.analysis_service(mid)
    bound.set(str(dirs['other']))
    with pytest.raises(ValueError, match='do not match'): profiles.analysis_service(mid)
    bound.set(str(dirs['default']))
    assert profiles.analysis_service(mid).get(mid)['analysis_profile'] == 'default'
    from meeting_notes import runtime
    with pytest.raises(ValueError, match='single-profile'): runtime.start()


def test_scoped_service_cannot_read_another_meeting(homes):
    svc, mid, *_ = homes
    scoped = profiles.analysis_service(mid)
    with svc.db() as db:
        row = db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
        db.execute('INSERT INTO meetings(id,title,date,timezone,audio,sha) VALUES(?,?,?,?,?,?)',('another','Another',row['date'],row['timezone'],row['audio'],'another'))
    with pytest.raises(ValueError, match='another Hermes profile'): scoped.get('another')


def test_selector_api_refuses_old_clients_and_discovery_only_names_current(homes, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    svc, mid, *_ = homes
    spec = importlib.util.spec_from_file_location('profile_api',Path(__file__).parents[1]/'dashboard/plugin_api.py')
    api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
    monkeypatch.setattr(api.runtime,'service',lambda:svc)
    monkeypatch.setattr(api.runtime,'start',lambda:pytest.fail('Identity lookup starts no work'))
    app = FastAPI(); app.include_router(api.router); client = TestClient(app)
    assert client.get('/analysis-profiles').json() == {'current':'default','profiles':['default']}
    assert client.put(f'/meetings/{mid}/analysis-profile',json={'profile':'research','revision':0}).status_code == 410
    assert svc.get(mid)['revision'] == 0


def test_concurrent_profile_contexts_never_share_the_first_actor(homes):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    svc, mid, dirs, current, bound, multiplex = homes
    legacy_selection(svc, mid)
    barrier = Barrier(2)
    def read(actor):
        current.set(dirs[actor]); bound.set(str(dirs[actor])); multiplex.set(True)
        barrier.wait()
        try: return profiles.analysis_service(mid,'default').get(mid)['analysis_profile']
        except ValueError: return 'denied'
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(read, ['default','research'])) == ['default','denied']


def test_local_import_survives_unknown_identity_but_analysis_stays_closed(homes, monkeypatch):
    from meeting_notes import runtime
    svc, mid, dirs, current, *_ = homes
    current.set(dirs['default']/'uncataloged-home')
    module = ModuleType('plugins.plugin_storage'); module.plugin_data_dir = lambda _:svc.root
    monkeypatch.setitem(sys.modules,'plugins.plugin_storage',module)
    local = runtime.service()
    assert local.owner_profile is None
    assert local.get(mid)['id'] == mid
    with pytest.raises(ValueError, match='unambiguously'): profiles.analysis_service(mid,'default')
