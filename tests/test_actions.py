from concurrent.futures import ThreadPoolExecutor

import pytest

from meeting_notes import actions, profiles
from meeting_notes.analysis import read_transcript, NotesValidationError
from meeting_notes.service import Service
from test_analysis import meeting
from test_profiles import homes
from test_api import client


def item(page, text='Send the notes.', action_type='task'):
    return {'action_type': action_type, 'kind': 'commitment', 'text': text,
            'citations': [{'segment_id': 's0', 'quote': page['segments'][0][2]}],
            'owner': {'basis': 'speaker', 'segment_id': 's0', 'name': None},
            'due': {'segment_id': None, 'quote': None, 'proposed_date': None}}


@pytest.fixture
def proposal(meeting):
    svc, mid = meeting
    svc.owner_profile = 'default'
    page = read_transcript(svc, mid)
    actions.save(svc, mid, page['basis'], None, [item(page)])
    return svc, mid


def prepare(svc, mid, request_id='request-synthetic-0001'):
    view = actions.view(svc, mid)
    args = dict(action_id=view['items'][0]['id'], actions_id=view['actions_id'],
                revision=view['basis']['revision'], destination='todo', instructions='Use my test list.', request_id=request_id)
    return actions.create_handoff(svc, mid, **args)['handoff_id'], args


def actor(svc, mid, hid):
    scoped = Service(svc.root)
    scoped.owner_profile = 'default'
    scoped.handoff_actor = 'default'
    scoped.handoff_meeting = mid
    scoped.handoff_id = hid
    return scoped


def report(status='completed'):
    return dict(status=status, summary='Synthetic task added.', provider='Test calendar/list', external_id='test-item-1' if status in ('completed','unknown') else '', url='https://example.test/item/1' if status in ('completed','unknown') else '')


def test_proposals_are_grounded_and_do_not_create_handoffs(proposal):
    svc, mid = proposal
    view = actions.view(svc, mid)
    assert view['items'][0]['resolved_owner']['participant'] == '1'
    assert not view['handoffs']
    assert svc.get(mid)['analysis'] is None
    page = read_transcript(svc, mid)
    bad = item(page); bad['citations'][0]['quote'] = 'Not said'
    with pytest.raises(NotesValidationError): actions.save(svc, mid, page['basis'], view['actions_id'], [bad])
    assert actions.view(svc, mid)['actions_id'] == view['actions_id']
    with pytest.raises(ValueError, match='changed'): actions.save(svc, mid, page['basis'], None, [item(page)])
    svc.correct(mid, 'rename', '1', 'Alex', 0)
    with pytest.raises(ValueError, match='changed'): actions.save(svc, mid, page['basis'], view['actions_id'], [item(page)])
    assert actions.view(svc, mid)['needs_review']


def test_user_handoff_is_idempotent_and_claimed_once(proposal):
    svc, mid = proposal
    hid, args = prepare(svc, mid)
    assert actions.create_handoff(svc, mid, **args)['handoff_id'] == hid
    with pytest.raises(ValueError, match='Conflicting'):
        actions.create_handoff(svc, mid, **{**args, 'instructions': 'Changed'})
    with pytest.raises(ValueError, match='already'):
        prepare(svc, mid, 'request-synthetic-0002')
    scoped = actor(svc, mid, hid)
    read = actions.callback(scoped, mid, hid, 'read')
    assert not read['may_execute'] and read['state'] == 'pending'
    def begin(_):
        try: return actions.callback(scoped, mid, hid, 'begin', 0).get('may_execute', False)
        except ValueError: return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(begin, range(2))) == [False, True]
    assert actions.callback(scoped, mid, hid, 'begin', 1)['may_execute'] is False
    completed = actions.callback(scoped, mid, hid, 'report', 1, report())
    assert completed['status'] == 'completed'
    assert actions.callback(scoped, mid, hid, 'report', 1, report())['duplicate']
    handoff = actions.view(svc, mid)['handoffs'][0]
    assert len(handoff['history']) == 1 and handoff['result']['external_id'] == 'test-item-1'
    with pytest.raises(ValueError, match='already'): prepare(svc, mid, 'request-synthetic-0003')


def test_results_require_evidence_and_safe_links(proposal):
    svc, mid = proposal
    hid, _ = prepare(svc, mid); scoped = actor(svc, mid, hid)
    actions.callback(scoped, mid, hid, 'begin', 0)
    for bad in [dict(report(), external_id=''), dict(report(), provider=' '), dict(report(), url='javascript:alert(1)'), dict(report(), url='https://user:secret@example.test')]:
        with pytest.raises(ValueError): actions.callback(scoped, mid, hid, 'report', 1, bad)
    assert actions.callback(scoped, mid, hid, 'read')['state'] == 'running'
    assert not actions.view(svc, mid)['handoffs'][0]['history']


def test_needs_input_requires_user_continuation_and_unknown_never_resubmits(proposal):
    svc, mid = proposal
    hid, _ = prepare(svc, mid); scoped = actor(svc, mid, hid)
    actions.callback(scoped, mid, hid, 'begin', 0)
    actions.callback(scoped, mid, hid, 'report', 1, report('needs_input'))
    assert not actions.callback(scoped, mid, hid, 'begin', 2)['may_execute']
    actions.user_update(svc, mid, hid, 2, 'continue', 'Use the confirmed calendar.')
    assert actions.callback(scoped, mid, hid, 'read')['instructions'] == 'Use the confirmed calendar.'
    assert actions.callback(scoped, mid, hid, 'begin', 3)['may_execute']
    actions.callback(scoped, mid, hid, 'report', 4, report('unknown'))
    assert not actions.callback(scoped, mid, hid, 'begin', 5)['may_execute']
    with pytest.raises(ValueError): actions.user_update(svc, mid, hid, 5, 'continue')
    with pytest.raises(ValueError): actions.callback(scoped, mid, hid, 'report', 5, report('needs_input'))
    with pytest.raises(ValueError): prepare(svc, mid, 'request-synthetic-0002')
    actions.callback(scoped, mid, hid, 'report', 5, report())
    assert actions.view(svc, mid)['handoffs'][0]['state'] == 'completed'


def test_cancel_only_before_start_and_failed_retry_is_explicit(proposal):
    svc, mid = proposal
    hid, _ = prepare(svc, mid)
    actions.user_update(svc, mid, hid, 0, 'cancel')
    assert not actions.callback(actor(svc, mid, hid), mid, hid, 'begin', 1)['may_execute']
    hid, _ = prepare(svc, mid, 'request-synthetic-0002'); scoped = actor(svc, mid, hid)
    actions.callback(scoped, mid, hid, 'begin', 0)
    with pytest.raises(ValueError): actions.user_update(svc, mid, hid, 1, 'cancel')
    actions.callback(scoped, mid, hid, 'report', 1, report('failed'))
    assert not actions.callback(scoped, mid, hid, 'begin', 2)['may_execute']
    prepare(svc, mid, 'request-synthetic-0003')
    assert len(actions.view(svc, mid)['handoffs']) == 3


def test_corrections_block_new_execution_but_preserve_late_receipt(proposal):
    svc, mid = proposal
    hid, _ = prepare(svc, mid); scoped = actor(svc, mid, hid)
    actions.callback(scoped, mid, hid, 'begin', 0)
    svc.correct(mid, 'rename', '1', 'Alex', 0)
    result = actions.callback(scoped, mid, hid, 'report', 1, report())
    assert result['needs_review']
    view = actions.view(svc, mid)
    assert view['items'][0]['resolved_owner']['name'] == 'Alex'
    assert view['handoffs'][0]['snapshot']['item']['resolved_owner']['name'] is None
    assert view['handoffs'][0]['result']['external_id'] == 'test-item-1'


def test_profile_change_revokes_begin_but_frozen_actor_can_report(homes):
    svc, mid, dirs, current, *_ = homes
    page = read_transcript(svc, mid)
    actions.save(svc, mid, page['basis'], None, [item(page)])
    hid, _ = prepare(svc, mid)
    scoped = profiles.handoff_service(mid, hid, 'default')
    actions.callback(scoped, mid, hid, 'begin', 0)
    profiles.select(svc, mid, 'research', 0)
    assert actions.callback(scoped, mid, hid, 'read')['needs_review']
    assert actions.callback(scoped, mid, hid, 'report', 1, report())['needs_review']
    with pytest.raises(ValueError): scoped.get(mid)
    current.set(dirs['research'])
    with pytest.raises(ValueError, match='not assigned'): profiles.handoff_service(mid, hid, 'default')
    assert not (dirs['research']/'plugin-data').exists()


def test_pending_handoff_cannot_execute_after_profile_switch(homes):
    svc, mid, dirs, current, *_ = homes
    page = read_transcript(svc, mid)
    actions.save(svc, mid, page['basis'], None, [item(page)])
    hid, _ = prepare(svc, mid)
    scoped = profiles.handoff_service(mid, hid, 'default')
    profiles.select(svc, mid, 'research', 0)
    with pytest.raises(ValueError, match='changed'): actions.callback(scoped, mid, hid, 'begin', 0)
    with pytest.raises(ValueError): actions.callback(scoped, mid, hid, 'report', 0, report())


def test_reanalysis_keeps_exact_lineage_and_orphans_ambiguous_receipts(proposal):
    svc, mid = proposal
    hid, _ = prepare(svc, mid); scoped = actor(svc, mid, hid)
    actions.callback(scoped, mid, hid, 'begin', 0)
    actions.callback(scoped, mid, hid, 'report', 1, report())
    before = actions.view(svc, mid); page = read_transcript(svc, mid)
    actions.save(svc, mid, page['basis'], before['actions_id'], [item(page)])
    same = actions.view(svc, mid)
    assert same['items'][0]['id'] == before['items'][0]['id']
    assert not same['handoffs'][0]['needs_review']
    actions.save(svc, mid, page['basis'], same['actions_id'], [item(page, 'Send revised notes.')])
    changed = actions.view(svc, mid)
    assert changed['items'][0]['id'] != same['items'][0]['id']
    assert changed['handoffs'][0]['needs_review']
    assert changed['handoffs'][0]['snapshot']['item']['text'] == 'Send the notes.'
    assert changed['handoffs'][0]['result']['external_id'] == 'test-item-1'


def test_handoff_api_never_starts_worker(client, monkeypatch):
    from meeting_notes import runtime
    from test_core import imported, Adapter
    from meeting_notes.worker import Worker
    svc = runtime.service(); svc.owner_profile = 'default'
    mid = imported(svc); Worker(svc, lambda _: Adapter()).run_once()
    page = read_transcript(svc, mid)
    actions.save(svc, mid, page['basis'], None, [item(page)])
    monkeypatch.setattr(runtime, 'start', lambda: pytest.fail('Handoffs must not start a worker'))
    data = client.get(f'/api/plugins/meeting_notes/meetings/{mid}/actions').json()
    args = dict(action_id=data['items'][0]['id'], actions_id=data['actions_id'], revision=0,
                destination='calendar', instructions='Confirm timing in chat', request_id='request-synthetic-api')
    result = client.post(f'/api/plugins/meeting_notes/meetings/{mid}/handoffs', json=args)
    assert result.status_code == 200
    assert client.post(f'/api/plugins/meeting_notes/meetings/{mid}/handoffs', json=args).json() == result.json()
    assert client.get(f'/api/plugins/meeting_notes/meetings/{mid}/actions').json()['handoffs'][0]['state'] == 'pending'
    assert client.post(f'/api/plugins/meeting_notes/meetings/{mid}/handoffs', json={**args, 'profile': 'other'}).status_code == 400


def test_ambiguous_repeated_proposals_never_share_an_external_receipt(proposal):
    svc, mid = proposal
    hid, _ = prepare(svc, mid)
    before = actions.view(svc, mid); page = read_transcript(svc, mid)
    with pytest.raises(NotesValidationError, match='duplicate_item'):
        actions.save(svc, mid, page['basis'], before['actions_id'], [item(page), item(page)])
    after = actions.view(svc, mid)
    assert after['actions_id'] == before['actions_id']
    assert len(after['items']) == 1
    assert after['handoffs'][0]['action_id'] == after['items'][0]['id']
    assert after['handoffs'][0]['id'] == hid


def test_bound_agent_cannot_create_or_continue_user_handoffs(homes):
    svc, mid, *_ = homes
    page = read_transcript(svc, mid)
    actions.save(svc, mid, page['basis'], None, [item(page)])
    hid, args = prepare(svc, mid)
    agent = profiles.analysis_service(mid, 'default')
    with pytest.raises(ValueError, match='importing profile UI'):
        actions.create_handoff(agent, mid, **args)
    with pytest.raises(ValueError, match='importing profile UI'):
        actions.user_update(agent, mid, hid, 0, 'cancel')
    callback_svc = profiles.handoff_service(mid, hid, 'default')
    with pytest.raises(ValueError, match='not assigned'):
        actions.callback(callback_svc, 'different-meeting', hid, 'read')
    with pytest.raises(ValueError, match='not assigned'):
        actions.callback(callback_svc, mid, 'different-handoff', 'read')


def test_registered_action_tools_complete_a_synthetic_round_trip(homes):
    import importlib.util
    import json
    from pathlib import Path
    svc, mid, *_ = homes
    spec = importlib.util.spec_from_file_location('action_tools_test', Path(__file__).parents[1]/'__init__.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    handlers = {}
    class Context:
        def register_tool(self, **kwargs): handlers[kwargs['name']] = kwargs['handler']
        def register_skill(self, *args, **kwargs): pass
        @property
        def llm(self): pytest.fail('Actions must use the Hermes session, not a nested model')
    module.register(Context())
    def call(name, **args):
        return json.loads(handlers[name]({'meeting_id': mid, 'source_profile': 'default', **args}))
    state = call('meeting_actions')
    page = call('meeting_transcript')
    saved = call('meeting_save_actions', basis=state['basis'], actions_id=None, items=[item(page, action_type='training')])
    assert saved['status'] == 'saved'
    assert call('meeting_actions')['items'][0]['action_type'] == 'training'
    hid, _ = prepare(svc, mid)
    read = call('meeting_action_callback', handoff_id=hid, operation='read')
    assert read['state'] == 'pending' and not read['may_execute']
    begin = call('meeting_action_callback', handoff_id=hid, operation='begin', version=read['version'])
    assert begin['may_execute']
    assert call('meeting_action_callback', handoff_id=hid, operation='report', version=begin['version'], result=report())['status'] == 'completed'
    assert call('meeting_actions')['handoffs'][0]['result']['external_id'] == 'test-item-1'
