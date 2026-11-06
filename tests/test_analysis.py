import copy
import hashlib
import json

import pytest

from meeting_notes.analysis import read_transcript, save_notes, validate
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
    assert svc.get(mid)['analysis'] is None
