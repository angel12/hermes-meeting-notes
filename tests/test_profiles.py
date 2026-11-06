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


def test_profile_selection_persists_and_default_is_importing_profile(homes):
    svc,mid,dirs,current,*_=homes
    assert profiles.options()=={'current':'default','profiles':['default','other','research']}
    assert svc.get(mid)['analysis_profile']=='default'
    first=profiles.select(svc,mid,'research',0)
    assert first['analysis_profile']=='research' and first['revision']==1
    reopened=Service(svc.root); reopened.owner_profile='default'
    assert reopened.get(mid)['analysis_profile']=='research'
    assert profiles.select(svc,mid,'research',1)['revision']==1
    with pytest.raises(ValueError,match='refresh'): profiles.select(svc,mid,'other',0)
    with pytest.raises(ValueError,match='existing'): profiles.select(svc,mid,'../other',1)


def test_selected_profile_reads_and_saves_to_importing_profile(homes):
    svc,mid,dirs,current,*_=homes
    original={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (svc.root/'raw').iterdir()}
    profiles.select(svc,mid,'research',0)
    current.set(dirs['research'])
    scoped=profiles.analysis_service(mid,'default')
    page=read_transcript(scoped,mid)
    assert page['source_profile']=='default' and page['analysis_profile']=='research'
    result=save(scoped,mid,payload(scoped.get(mid)))
    assert result['profile']=='research'
    assert svc.get(mid)['analysis']['id']==result['id']
    assert not (dirs['research']/'plugin-data').exists(), 'transcript must not be copied into target profile'
    for path,digest in original.items(): assert hashlib.sha256(path.read_bytes()).hexdigest()==digest


def test_unselected_profiles_cannot_read_and_selection_revokes_access(homes):
    svc,mid,dirs,current,*_=homes
    profiles.select(svc,mid,'research',0)
    for actor in ('default','other'):
        current.set(dirs[actor])
        with pytest.raises(ValueError,match='not assigned'): profiles.analysis_service(mid,'default')
    current.set(dirs['research'])
    scoped=profiles.analysis_service(mid,'default')
    snapshot=scoped.get(mid)
    current.set(dirs['default']); profiles.select(svc,mid,'other',1)
    with pytest.raises(ValueError,match='not assigned'): scoped.get(mid)
    with pytest.raises(ValueError,match='not assigned'): scoped.save_analysis(snapshot,payload(snapshot))
    current.set(dirs['research'])
    with pytest.raises(ValueError,match='unavailable'): profiles.analysis_service(mid,'../../default')


def test_change_away_and_back_still_invalidates_inflight_notes(homes):
    svc,mid,dirs,current,*_=homes
    profiles.select(svc,mid,'research',0)
    current.set(dirs['research']); scoped=profiles.analysis_service(mid,'default'); snapshot=scoped.get(mid)
    current.set(dirs['default'])
    profiles.select(svc,mid,'other',1); profiles.select(svc,mid,'research',2)
    with pytest.raises(ValueError,match='changed during analysis'): scoped.save_analysis(snapshot,payload(snapshot))
    assert svc.get(mid)['analysis'] is None


def test_profile_change_marks_existing_notes_for_review_without_relabeling_history(homes):
    svc,mid,dirs,current,*_=homes
    scoped=profiles.analysis_service(mid)
    saved=save(scoped,mid,payload(scoped.get(mid)))
    profiles.select(svc,mid,'research',0)
    assert svc.get(mid)['analysis']['needs_review']
    assert svc.get_analysis(mid,saved['id'])['profile']=='default'


def test_multiplex_identity_must_be_bound_and_match(homes):
    svc,mid,dirs,current,bound,multiplex=homes
    profiles.select(svc,mid,'research',0)
    current.set(dirs['research']); multiplex.set(True)
    with pytest.raises(ValueError,match='bind a profile'): profiles.analysis_service(mid,'default')
    bound.set(str(dirs['other']))
    with pytest.raises(ValueError,match='do not match'): profiles.analysis_service(mid,'default')
    bound.set(str(dirs['research']))
    assert profiles.analysis_service(mid,'default').get(mid)['analysis_profile']=='research'
    from meeting_notes import runtime
    module=ModuleType('plugins.plugin_storage'); module.plugin_data_dir=lambda _:dirs['research']/'plugin-data/meeting_notes'
    # Install only for the isolated check below; worker boundary stays closed.
    previous=sys.modules.get('plugins.plugin_storage'); sys.modules['plugins.plugin_storage']=module
    try:
        with pytest.raises(ValueError,match='single-profile'): runtime.start()
    finally:
        if previous is None: sys.modules.pop('plugins.plugin_storage',None)
        else: sys.modules['plugins.plugin_storage']=previous


def test_scoped_service_cannot_read_another_meeting_or_silently_default(homes):
    svc,mid,dirs,current,*_=homes
    profiles.select(svc,mid,'research',0)
    current.set(dirs['research']); scoped=profiles.analysis_service(mid,'default')
    # Different meeting even with the same target is outside this tool invocation.
    with svc.db() as db:
        row=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
        db.execute('INSERT INTO meetings(id,title,date,timezone,audio,sha) VALUES(?,?,?,?,?,?)',('another','Another',row['date'],row['timezone'],row['audio'],'another'))
        db.execute("INSERT INTO meeting_profiles VALUES('another','research')")
    with pytest.raises(ValueError,match='not assigned'): scoped.get('another')
    with pytest.raises(ValueError,match='no Meeting Notes data'): profiles.analysis_service(mid)
    del dirs['research']
    with pytest.raises(ValueError,match='unambiguously'): profiles.analysis_service(mid,'default')


def test_selector_api_and_read_only_discovery(homes,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    svc,mid,*_=homes
    spec=importlib.util.spec_from_file_location('profile_api',Path(__file__).parents[1]/'dashboard/plugin_api.py')
    api=importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
    monkeypatch.setattr(api.runtime,'service',lambda:svc)
    monkeypatch.setattr(api.runtime,'start',lambda:pytest.fail('Profile selection starts no work'))
    app=FastAPI(); app.include_router(api.router); client=TestClient(app)
    assert client.get('/analysis-profiles').json()['current']=='default'
    assert svc.get(mid)['revision']==0
    response=client.put(f'/meetings/{mid}/analysis-profile',json={'profile':'research','revision':0})
    assert response.status_code==200 and response.json()['analysis_profile']=='research'
    assert client.put(f'/meetings/{mid}/analysis-profile',json={'profile':'other','revision':0}).status_code==400


def test_concurrent_profile_contexts_never_share_the_first_actor(homes):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    svc,mid,dirs,current,bound,multiplex=homes
    profiles.select(svc,mid,'research',0)
    barrier=Barrier(2)
    def read(actor):
        current.set(dirs[actor]); bound.set(str(dirs[actor])); multiplex.set(True)
        barrier.wait()
        try:
            return profiles.analysis_service(mid,'default').get(mid)['analysis_profile']
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(read,['research','other']))
    assert results==['research','This meeting is not assigned to the current Hermes profile']


def test_local_import_survives_unknown_catalog_identity_but_sharing_stays_closed(homes,monkeypatch):
    from meeting_notes import runtime
    svc,mid,dirs,current,bound,multiplex=homes
    current.set(dirs['default']/'uncataloged-home')
    module=ModuleType('plugins.plugin_storage'); module.plugin_data_dir=lambda _:svc.root
    monkeypatch.setitem(sys.modules,'plugins.plugin_storage',module)
    local=runtime.service()
    assert local.owner_profile is None
    assert local.get(mid)['id']==mid
    assert imported(local)==mid
    with pytest.raises(ValueError,match='unambiguously'): profiles.select(local,mid,'research',0)
    with pytest.raises(ValueError,match='unambiguously'): profiles.analysis_service(mid,'default')
