"""Session transcript reads and validated notes persistence. No model calls."""
import copy
from datetime import date, datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, ValidationError

from .store import uid


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


NULLABLE = {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 500}
CITATION = obj({'segment_id': {'type': 'string'},
                'quote': {'type': 'string', 'minLength': 1, 'maxLength': 2000}})
SCHEMA = obj({'items': {'type': 'array', 'maxItems': 60, 'items': obj({
    'kind': {'enum': ['summary', 'decision', 'question', 'commitment', 'suggestion']},
    'text': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
    'citations': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': CITATION},
    'owner': obj({'basis': {'enum': ['unresolved', 'speaker', 'named']},
                  'segment_id': NULLABLE, 'name': NULLABLE}),
    'due': obj({'segment_id': NULLABLE, 'quote': NULLABLE, 'proposed_date': NULLABLE}),
})}})

BASIS_SCHEMA = obj({
    'transcript_id': {'type': 'string'},
    'revision': {'type': 'integer', 'minimum': 0},
    'analysis_id': {'type': ['string', 'null']},
})
PAGE_CHARS = 6000


def basis_for(snapshot):
    return {'transcript_id': snapshot['active'], 'revision': snapshot['revision'],
            'analysis_id': snapshot['analysis']['id'] if snapshot['analysis'] else None}


def require_basis(snapshot, basis):
    try:
        Draft202012Validator(BASIS_SCHEMA).validate(basis)
    except ValidationError:
        raise ValueError('Use the unchanged basis returned by meeting_transcript') from None
    if basis != basis_for(snapshot):
        raise ValueError('Transcript or saved notes changed; reload the transcript before saving notes')


def read_transcript(svc, meeting_id, offset=0, basis=None):
    snapshot = svc.get(meeting_id)
    if not snapshot['active'] or not snapshot['segments']:
        raise ValueError('A transcript is required before generating notes')
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < len(snapshot['segments']):
        raise ValueError('Invalid transcript offset')
    if offset and basis is None:
        raise ValueError('Continue with the basis returned by the first transcript page')
    if basis is not None:
        require_basis(snapshot, basis)
    rows, size = [], 0
    for i in range(offset, len(snapshot['segments'])):
        segment = snapshot['segments'][i]
        row = [f's{i}', segment['participant'], segment['text']]
        row_size = len(json.dumps(row, ensure_ascii=False, separators=(',', ':')))
        if rows and size + row_size > PAGE_CHARS:
            break
        rows.append(row)
        size += row_size
    end = offset + len(rows)
    local_date = datetime.fromisoformat(snapshot['date']).astimezone(ZoneInfo(snapshot['timezone'])).date().isoformat()
    return {
        'meeting_id': meeting_id, 'title': snapshot['title'], 'basis': basis_for(snapshot),
        'source_profile': snapshot.get('owner_profile'), 'analysis_profile': snapshot.get('analysis_profile'),
        'meeting_local_date': local_date, 'timezone': snapshot['timezone'],
        'participants': {s['participant']: s['name'] for s in snapshot['segments']},
        'segment_columns': ['id', 'participant', 'text'], 'segments': rows,
        'offset': offset, 'next_offset': end if end < len(snapshot['segments']) else None,
        'total_segments': len(snapshot['segments']), 'skill': 'meeting_notes:analyze',
        'recordings': [{**part, 'first_source_id': next(
            (f's{i}' for i,s in enumerate(snapshot['segments']) if s.get('source',{}).get('part') == part['part']), None)}
            for part in snapshot.get('recordings',[])],
    }


def save_notes(svc, meeting_id, basis, items):
    snapshot = svc.get(meeting_id)
    require_basis(snapshot, basis)
    if not snapshot['active']:
        raise ValueError('A transcript is required before saving notes')
    payload = {'items': items}
    try:
        Draft202012Validator(SCHEMA).validate(payload)
    except ValidationError:
        raise ValueError('Notes do not match the save schema; nothing was saved') from None
    payload = copy.deepcopy(payload)
    aliases = {f's{i}': s['id'] for i, s in enumerate(snapshot['segments'])}
    for item in payload['items']:
        for ref in [*item['citations'], item['owner'], item['due']]:
            sid = ref['segment_id']
            if sid is not None:
                if sid not in aliases:
                    raise ValueError('Unknown source ID; use IDs from meeting_transcript')
                ref['segment_id'] = aliases[sid]
    payload = validate(payload, snapshot)
    record = svc.save_analysis(snapshot, payload, origin='hermes_session')
    return {'status': 'saved', 'meeting_id': meeting_id, 'analysis_id': record['id'],
            'revision': record['revision'], 'items_saved': len(record['items'])}


def fingerprint(item):
    # Conservative lineage: only identical content/evidence/attribution is reusable.
    payload = {key: item[key] for key in ('kind', 'text', 'citations', 'owner', 'due')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate(payload, snapshot):
    try:
        Draft202012Validator(SCHEMA).validate(payload)
    except ValidationError:
        raise ValueError('Notes did not match the notes schema; nothing was saved') from None
    segments = {s['id']: s for s in snapshot['segments']}
    result = copy.deepcopy(payload)
    seen = set()
    for item in result['items']:
        cited = {}
        for citation in item['citations']:
            sid, quote = citation['segment_id'], citation['quote']
            if sid not in segments or not quote.strip() or quote not in segments[sid]['text']:
                raise ValueError('Notes cited missing or inexact evidence; nothing was saved')
            if sid in cited:
                raise ValueError('Notes repeated a source segment; nothing was saved')
            cited[sid] = quote
        owner, due = item['owner'], item['due']
        if owner['basis'] == 'unresolved':
            valid_owner = owner['segment_id'] is None and owner['name'] is None
        elif owner['basis'] == 'speaker':
            valid_owner = owner['segment_id'] in cited and owner['name'] is None
        else:
            valid_owner = (owner['segment_id'] in cited and bool(owner['name']) and
                           owner['name'].strip() and owner['name'] in cited[owner['segment_id']])
        if not valid_owner or (item['kind'] != 'commitment' and owner['basis'] != 'unresolved'):
            raise ValueError('Proposed owner lacks valid commitment evidence; nothing was saved')
        if any(v is not None for v in due.values()):
            if (item['kind'] != 'commitment' or due['segment_id'] not in cited or
                    not due['quote'] or not due['quote'].strip() or
                    due['quote'] not in cited[due['segment_id']]):
                raise ValueError('Proposed date lacks commitment evidence; nothing was saved')
            if due['proposed_date'] is not None:
                try:
                    parsed = date.fromisoformat(due['proposed_date'])
                    if parsed.isoformat() != due['proposed_date']:
                        raise ValueError()
                except ValueError:
                    raise ValueError('Proposed date must be an ISO calendar date; nothing was saved') from None
        key = fingerprint(item)
        if key in seen:
            raise ValueError('Notes repeated an item; nothing was saved')
        seen.add(key)
        item['id'] = uid()
    return result


def project(record, meeting):
    """Resolve speaker owners afresh; explicit text attribution never follows merges."""
    result = copy.deepcopy(record)
    segments = {s['id']: s for s in meeting['segments']}
    result['needs_review'] = record['revision'] != meeting['revision']
    for item in result['items']:
        owner = item['owner']
        item['resolved_owner'] = None
        if owner['basis'] == 'named':
            item['resolved_owner'] = {'name': owner['name'], 'participant': None, 'basis': 'named'}
        elif owner['basis'] == 'speaker':
            segment = segments.get(owner['segment_id'])
            if segment:
                item['resolved_owner'] = {'participant': segment['participant'],
                                          'name': segment['name'], 'basis': 'speaker'}
        item['date_status'] = 'needs_confirmation' if item['due']['quote'] else 'unresolved'
        for citation in item['citations']:
            segment = segments.get(citation['segment_id'])
            citation['source_changed'] = segment is None or segment['text'] != result['sources'][citation['segment_id']]['text']
    return result
