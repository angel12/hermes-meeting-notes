import copy
import hashlib
import json

import pytest

from meeting_notes.analysis import NotesValidationError, read_transcript, save_notes, validate
from meeting_notes.service import Service
from meeting_notes.worker import Worker
from test_core import Adapter, imported


@pytest.fixture
def meeting(tmp_path):
    svc = Service(tmp_path)
    mid = imported(svc)
    Worker(svc, lambda _: Adapter()).run_once()
    return svc, mid


def payload(snapshot):
    segments = snapshot['segments']
    return {'items': [{
        'kind': 'commitment', 'text': 'Send it.',
        'citations': [{'segment_id': segments[0]['id'], 'quote': segments[0]['text']}],
        'owner': {'basis': 'speaker', 'segment_id': segments[0]['id'], 'name': None},
        'due': {'segment_id': None, 'quote': None, 'proposed_date': None},
    }]}


def save(svc, mid, data, basis=None):
    page = read_transcript(svc, mid)
    wire = copy.deepcopy(data)
    if isinstance(wire,dict):
        for item in wire['items']:
            for ref in [*item['citations'],item['owner'],item['due']]:
                sid=ref['segment_id']
                if isinstance(sid,str) and ':s' in sid:
                    ref['segment_id']='s'+str(int(sid.rsplit(':s',1)[1]))
    result=save_notes(svc,mid,basis or page['basis'],wire['items'] if isinstance(wire,dict) else None)
    assert result['status']=='saved'
    return svc.get(mid)['analysis']


def test_analysis_sources_date_anchor_and_raw_preservation(meeting):
    svc, mid = meeting
    with svc.db() as db:
        db.execute('UPDATE meetings SET date=? WHERE id=?', ('2026-11-06T01:00:00+00:00',mid))
    snapshot = svc.get(mid)
    raw = next((svc.root/'raw').glob('*.json'))
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    sent = read_transcript(svc,mid)
    assert sent['meeting_local_date'] == '2026-11-05'
    assert sent['timezone'] == 'America/Denver'
    assert [row[2] for row in sent['segments']] == [s['text'] for s in snapshot['segments']]
    result = save(svc,mid,payload(snapshot))
    assert result['origin'] == 'hermes_session'
    assert result['model'] is None
    assert result['items'][0]['resolved_owner']['participant'] == '1'
    assert result['items'][0]['date_status'] == 'unresolved'
    assert result['sources'][snapshot['segments'][0]['id']]['text'] == 'I will send it.'
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('defect', ['unknown_source','invented_quote','owner_not_cited','invented_name','date_not_cited','bad_date','suggestion_owner','extra_field','duplicate','not_json'])
def test_reject_untrusted_model_output(meeting, defect):
    svc, mid = meeting
    snapshot = svc.get(mid)
    data = payload(snapshot)
    item = data['items'][0]
    if defect == 'unknown_source': item['citations'][0]['segment_id'] = 'missing'
    elif defect == 'invented_quote': item['citations'][0]['quote'] = 'Never said this.'
    elif defect == 'owner_not_cited': item['owner']['segment_id'] = snapshot['segments'][1]['id']
    elif defect == 'invented_name': item['owner'].update(basis='named',name='Invented Person')
    elif defect == 'date_not_cited': item['due'].update(segment_id=snapshot['segments'][2]['id'],quote='Tomorrow.',proposed_date='2026-11-06')
    elif defect == 'bad_date': item['due'].update(segment_id=snapshot['segments'][0]['id'],quote='send',proposed_date='2026-02-30')
    elif defect == 'suggestion_owner': item['kind'] = 'suggestion'
    elif defect == 'extra_field': item['assignee_id'] = 'someone'
    elif defect == 'duplicate': data['items'].append(copy.deepcopy(item))
    elif defect == 'not_json': data = None
    with pytest.raises(ValueError): save(svc,mid,data)
    assert svc.get(mid)['analysis'] is None


def test_speaker_owners_follow_corrections_but_named_owners_do_not(meeting):
    svc, mid = meeting
    snapshot = svc.get(mid)
    sid = snapshot['segments'][0]['id']
    svc.correct(mid,'text',sid,'I will send it. Morgan will review it.',0)
    data = payload(svc.get(mid))
    named = copy.deepcopy(data['items'][0])
    named.update(text='Review it.',owner={'basis':'named','name':'Morgan','segment_id':sid})
    data['items'].append(named)
    result = save(svc,mid,data)
    ids = [i['id'] for i in result['items']]
    svc.correct(mid,'rename','2','Alex',1)
    svc.correct(mid,'merge','1','2',2)
    result = svc.get(mid)['analysis']
    assert result['needs_review']
    assert result['items'][0]['resolved_owner']['name'] == 'Alex'
    assert result['items'][1]['resolved_owner']['name'] == 'Morgan'
    svc.correct(mid,'speaker',sid,'3',3)
    assert svc.get(mid)['analysis']['items'][0]['resolved_owner']['participant'] == '3'
    correction = svc.get(mid)['corrections'][-1]['id']
    svc.correct(mid,'undo','',correction,4)
    result = svc.get(mid)['analysis']
    assert result['items'][0]['resolved_owner']['participant'] == '2'
    assert [i['id'] for i in result['items']] == ids
    svc.correct(mid,'text',sid,'I will not send it.',5)
    assert svc.get(mid)['analysis']['items'][0]['citations'][0]['source_changed']
    assert svc.get(mid)['analysis']['sources'][sid]['text'] == 'I will send it. Morgan will review it.'


def test_correction_during_generation_cannot_publish(meeting):
    svc, mid = meeting
    basis = read_transcript(svc,mid)['basis']
    data = payload(svc.get(mid))
    svc.correct(mid,'rename','1','Alex',0)
    with pytest.raises(ValueError,match='changed'): save(svc,mid,data,basis)
    assert svc.get(mid)['analysis'] is None


def test_reanalysis_preserves_exact_ids_and_unmatched_history(meeting):
    svc, mid = meeting
    data = payload(svc.get(mid))
    first = save(svc,mid,data)
    second = save(svc,mid,data)
    assert first['id'] != second['id']
    assert first['items'][0]['id'] == second['items'][0]['id']
    assert second['unmatched_previous_items'] == []
    data['items'][0]['text'] = 'Send the unspecified item.'
    third = save(svc,mid,data)
    assert third['items'][0]['id'] != second['items'][0]['id']
    assert third['unmatched_previous_items'] == [second['items'][0]['id']]
    assert len(svc.get(mid)['analysis_versions']) == 3
    assert svc.get_analysis(mid,first['id'])['items'][0]['text'] == 'Send it.'
    with pytest.raises(ValueError): svc.get_analysis(mid,'unknown')
    with svc.db() as db:
        assert json.loads(db.execute('SELECT data FROM analyses WHERE id=?',(first['id'],)).fetchone()[0])['items'][0]['text'] == 'Send it.'


def test_concurrent_analysis_does_not_overwrite(meeting):
    svc, mid = meeting
    snapshot = svc.get(mid)
    data = validate(payload(snapshot),snapshot)
    svc.save_analysis(snapshot,data)
    with pytest.raises(ValueError,match='finished first'): svc.save_analysis(snapshot,data)
    assert len(svc.get(mid)['analysis_versions']) == 1


def test_date_is_never_automatically_confirmed(meeting):
    svc, mid = meeting
    snapshot = svc.get(mid)
    data = payload(snapshot)
    sid = snapshot['segments'][2]['id']
    data['items'][0]['citations'].append({'segment_id':sid,'quote':'Tomorrow.'})
    data['items'][0]['due'] = {'segment_id':sid,'quote':'Tomorrow.','proposed_date':'2026-11-06'}
    result = save(svc,mid,data)
    assert result['items'][0]['date_status'] == 'needs_confirmation'



def test_paging_preserves_all_text_and_source_ids(meeting,monkeypatch):
    import meeting_notes.analysis as analysis
    svc,mid=meeting
    monkeypatch.setattr(analysis,'PAGE_CHARS',45)
    page=read_transcript(svc,mid)
    basis=page['basis']
    rows=[]
    while True:
        rows.extend(page['segments'])
        assert page['basis']==basis
        if page['next_offset'] is None:
            break
        page=read_transcript(svc,mid,page['next_offset'],basis)
    snapshot=svc.get(mid)
    assert rows==[[f's{i}',s['participant'],s['text']] for i,s in enumerate(snapshot['segments'])]
    assert len(rows)==page['total_segments']
    assert snapshot['analysis'] is None


@pytest.mark.parametrize('offset',[True,-1,999,1.5,'1'])
def test_invalid_page_offsets_rejected(meeting,offset):
    svc,mid=meeting
    with pytest.raises(ValueError): read_transcript(svc,mid,offset)


def test_page_reads_require_the_same_basis(meeting,monkeypatch):
    import meeting_notes.analysis as analysis
    svc,mid=meeting
    monkeypatch.setattr(analysis,'PAGE_CHARS',45)
    page=read_transcript(svc,mid)
    with pytest.raises(ValueError): read_transcript(svc,mid,page['next_offset'])
    svc.correct(mid,'rename','1','Alex',0)
    with pytest.raises(ValueError,match='changed'): read_transcript(svc,mid,page['next_offset'],page['basis'])


def test_oversized_single_segment_is_not_truncated(meeting):
    svc,mid=meeting
    sid=svc.get(mid)['segments'][0]['id']
    svc.correct(mid,'text',sid,'x'*10000,0)
    page=read_transcript(svc,mid)
    assert page['segments']==[['s0','1','x'*10000]]
    assert page['next_offset']==1


def test_save_rejects_reused_basis_after_another_analysis(meeting):
    svc,mid=meeting
    page=read_transcript(svc,mid)
    data=payload(svc.get(mid))
    save(svc,mid,data,page['basis'])
    with pytest.raises(ValueError,match='changed'): save(svc,mid,data,page['basis'])
    assert len(svc.get(mid)['analysis_versions'])==1


def test_short_ids_cannot_cross_transcript_versions(meeting):
    svc,mid=meeting
    page=read_transcript(svc,mid)
    data=payload(svc.get(mid))
    with svc.db() as db:
        row=db.execute('SELECT * FROM transcripts WHERE id=?',(page['basis']['transcript_id'],)).fetchone()
        segments=json.loads(row['segments'])
        for i,segment in enumerate(segments): segment['id']=f'new:s{i:05d}'
        db.execute('INSERT INTO transcripts VALUES(?,?,?,?,?)',('new',mid,row['raw'],row['sha'],json.dumps(segments)))
        db.execute('UPDATE meetings SET active=? WHERE id=?',('new',mid))
    with pytest.raises(ValueError,match='changed'): save(svc,mid,data,page['basis'])


def wire_item(page, text='Send it.'):
    sid, _, quote = page['segments'][0]
    return {'kind': 'commitment', 'text': text,
            'citations': [{'segment_id': sid, 'quote': quote}],
            'owner': {'basis': 'speaker', 'segment_id': sid, 'name': None},
            'due': {'segment_id': None, 'quote': None, 'proposed_date': None}}


def test_all_bad_quotes_reported_without_publishing_partial_notes(meeting):
    svc, mid = meeting
    page = read_transcript(svc, mid)
    first = save_notes(svc, mid, page['basis'], [wire_item(page)])
    items = [wire_item(page, 'First claim.'), wire_item(page, 'Second claim.')]
    items[0]['citations'][0]['quote'] = 'Private incorrect quote'
    items[1]['citations'][0]['quote'] = 'Another private incorrect quote'
    with pytest.raises(NotesValidationError) as error:
        save_notes(svc, mid, first['basis'], items)
    result = error.value.as_dict()
    assert result['failures'] == [
        {'path': 'items[0].citations[0].quote', 'code': 'quote_not_in_segment', 'source_id': 's0'},
        {'path': 'items[1].citations[0].quote', 'code': 'quote_not_in_segment', 'source_id': 's0'}]
    assert result['more_failures'] == 0
    assert 'nothing was saved' in result['error']
    assert 'Private' not in json.dumps(result) and 'private' not in json.dumps(result)
    assert read_transcript(svc, mid)['basis'] == first['basis']
    assert len(svc.get(mid)['analysis_versions']) == 1
    for item in items:
        item['citations'][0]['quote'] = page['segments'][0][2]
    complete = save_notes(svc, mid, first['basis'], items)
    assert complete['items_saved'] == 2
    assert complete['items_by_kind'] == {'commitment': 2}
    assert complete['basis'] == read_transcript(svc, mid)['basis']
    assert len(svc.get(mid)['analysis_versions']) == 2


def test_diagnostics_are_bounded_and_never_echo_unknown_source_text(meeting):
    svc, mid = meeting
    page = read_transcript(svc, mid)
    items = [wire_item(page, f'Claim {i}.') for i in range(12)]
    for item in items:
        item['owner'] = {'basis': 'unresolved', 'segment_id': None, 'name': None}
        item['citations'][0]['segment_id'] = 'private value submitted as an ID'
    with pytest.raises(NotesValidationError) as error:
        save_notes(svc, mid, page['basis'], items)
    result = error.value.as_dict()
    assert len(result['failures']) == 8 and result['more_failures'] == 4
    assert all(f['code'] == 'unknown_source' and 'source_id' not in f for f in result['failures'])
    assert 'private value' not in json.dumps(result)
    assert svc.get(mid)['analysis'] is None


def test_short_substrings_and_ambiguous_dates_use_corrected_transcript(meeting):
    svc, mid = meeting
    sid = svc.get(mid)['segments'][0]['id']
    svc.correct(mid, 'text', sid, 'Yes. I will send it next weekend.', 0)
    page = read_transcript(svc, mid)
    item = wire_item(page)
    item['citations'][0]['quote'] = 'I will send it next weekend.'
    item['due'] = {'segment_id': 's0', 'quote': 'next weekend', 'proposed_date': None}
    reply = {'kind': 'summary', 'text': 'Affirmative reply.',
             'citations': [{'segment_id': 's0', 'quote': 'Yes'}],
             'owner': {'basis': 'unresolved', 'segment_id': None, 'name': None},
             'due': {'segment_id': None, 'quote': None, 'proposed_date': None}}
    result = save_notes(svc, mid, page['basis'], [item, reply])
    assert result['items_by_kind'] == {'commitment': 1, 'summary': 1}
    saved = svc.get(mid)['analysis']
    assert saved['items'][0]['date_status'] == 'needs_confirmation'
    assert saved['sources'][sid]['text'] == page['segments'][0][2]
    # Moving an exact quote to a different source must still fail.
    reply['citations'][0]['segment_id'] = 's1'
    with pytest.raises(NotesValidationError) as error:
        save_notes(svc, mid, result['basis'], [item, reply])
    assert error.value.failures[0]['source_id'] == 's1'
    assert len(svc.get(mid)['analysis_versions']) == 1


def test_owner_date_schema_and_duplicate_errors_have_safe_locations(meeting):
    svc, mid = meeting
    page = read_transcript(svc, mid)
    item = wire_item(page)
    item['citations'].append(copy.deepcopy(item['citations'][0]))
    item['owner']['segment_id'] = 's1'
    item['due'] = {'segment_id': 's1', 'quote': 'Private date', 'proposed_date': 'not a date'}
    with pytest.raises(NotesValidationError) as error:
        save_notes(svc, mid, page['basis'], [item])
    assert {f['code'] for f in error.value.failures} == {
        'duplicate_source', 'owner_source_not_cited', 'due_source_not_cited', 'invalid_calendar_date'}
    assert 'Private' not in json.dumps(error.value.as_dict())
    item = wire_item(page)
    item['owner']['name'] = {'private content': 'never echo this'}
    with pytest.raises(NotesValidationError) as error:
        save_notes(svc, mid, page['basis'], [item])
    assert error.value.failures == [{'path': 'items[0].owner.name', 'code': 'schema_mismatch'}]
    assert 'never echo' not in str(error.value)
    assert svc.get(mid)['analysis'] is None


def test_persistence_boundary_reports_short_ids_and_rejects_entire_batch(meeting):
    svc, mid = meeting
    snapshot = svc.get(mid)
    data = payload(snapshot)
    data['items'][0]['citations'][0]['quote'] = 'Invented private wording'
    with pytest.raises(NotesValidationError) as error:
        svc.save_analysis(snapshot, data)
    assert error.value.failures[0]['source_id'] == 's0'
    assert snapshot['active'] not in json.dumps(error.value.as_dict())
    assert svc.get(mid)['analysis'] is None


@pytest.mark.parametrize('case,code,source', [
    ('summary_owner', 'owner_requires_commitment', None),
    ('uncited_owner', 'owner_source_not_cited', 's1'),
    ('unknown_owner', 'unknown_source', None),
    ('invented_name', 'owner_name_not_in_quote', 's0'),
    ('missing_name', 'owner_name_required', None),
    ('speaker_name', 'speaker_owner_requires_null_name', None),
    ('unresolved_fields', 'unresolved_owner_requires_null_fields', None),
    ('summary_due', 'due_requires_commitment', None),
    ('uncited_due', 'due_source_not_cited', 's1'),
    ('unknown_due', 'unknown_source', None),
    ('missing_due_quote', 'due_quote_required', None),
    ('uncited_date_text', 'due_quote_not_in_citation_quote', 's0'),
    ('extra_field', 'unexpected_field', None),
])
def test_owner_date_failures_identify_the_actual_cause(meeting, case, code, source):
    svc, mid = meeting
    sid = svc.get(mid)['segments'][0]['id']
    svc.correct(mid, 'text', sid, 'I will send it tomorrow.', 0)
    page = read_transcript(svc, mid)
    item = wire_item(page)
    if case == 'summary_owner': item['kind'] = 'summary'
    elif case == 'uncited_owner': item['owner']['segment_id'] = 's1'
    elif case == 'unknown_owner': item['owner']['segment_id'] = 'private unknown ID'
    elif case == 'invented_name': item['owner'].update(basis='named', name='Private invented name')
    elif case == 'missing_name': item['owner']['basis'] = 'named'
    elif case == 'speaker_name': item['owner']['name'] = 'Private name'
    elif case == 'unresolved_fields': item['owner']['basis'] = 'unresolved'
    elif case == 'extra_field': item['Private field'] = 'Private value'
    else:
        item['due'] = {'segment_id': 's0', 'quote': 'tomorrow', 'proposed_date': None}
        if case == 'summary_due':
            item['kind'] = 'summary'
            item['owner'] = {'basis': 'unresolved', 'segment_id': None, 'name': None}
        elif case == 'uncited_due': item['due']['segment_id'] = 's1'
        elif case == 'unknown_due': item['due']['segment_id'] = 'private unknown ID'
        elif case == 'missing_due_quote': item['due']['quote'] = None
        elif case == 'uncited_date_text': item['citations'][0]['quote'] = 'I will send it'
    with pytest.raises(NotesValidationError) as error:
        save_notes(svc, mid, page['basis'], [item])
    assert len(error.value.failures) == 1
    failure = error.value.failures[0]
    assert failure['code'] == code and failure.get('source_id') == source
    assert 'private' not in json.dumps(error.value.as_dict()).lower()
    assert svc.get(mid)['analysis'] is None
