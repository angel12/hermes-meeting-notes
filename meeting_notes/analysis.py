"""Session transcript reads and validated notes persistence. No model calls."""
from .links import meeting_link
import copy
from collections import Counter
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
MAX_FAILURES = 8


class NotesValidationError(ValueError):
    """Bounded diagnostics containing locations and codes, never source text."""

    def __init__(self, failures):
        self.failures = failures[:MAX_FAILURES]
        self.more_failures = max(0, len(failures) - MAX_FAILURES)
        first = self.failures[0]
        super().__init__(f"Notes validation failed at {first['path']} ({first['code']}); nothing was saved")

    def as_dict(self):
        result = {'error': str(self), 'failures': self.failures,
                  'more_failures': self.more_failures}
        if any(f['code'] == 'unknown_source' for f in self.failures):
            result['hint'] = ('Copy segment IDs exactly from meeting_transcript for the current basis (e.g. s0, s163). '
                              'Do not pad numbers, use stored transcript-qualified IDs, or substitute original-recording IDs. '
                              'Re-read transcript pages to recover IDs; do not inspect files or guess conversions.')
        return result


def check_schema(payload):
    try:
        Draft202012Validator(SCHEMA).validate(payload)
    except ValidationError as exc:
        # jsonschema.message can include submitted text. Only emit known field names.
        fields = {'items', 'kind', 'text', 'citations', 'segment_id', 'quote',
                  'owner', 'basis', 'name', 'due', 'proposed_date'}
        path = ''
        for part in exc.absolute_path:
            if type(part) is int:
                path += f'[{part}]'
            elif part in fields:
                path += ('.' if path else '') + part
            else:
                break
        code = 'unexpected_field' if exc.validator == 'additionalProperties' else 'schema_mismatch'
        raise NotesValidationError([{'path': path or 'items', 'code': code}]) from None


def basis_for(snapshot):
    return {'transcript_id': snapshot['active'], 'revision': snapshot['revision'],
            'analysis_id': snapshot['analysis']['id'] if snapshot['analysis'] else None}


def require_basis(snapshot, basis):
    try:
        Draft202012Validator(BASIS_SCHEMA).validate(basis)
    except ValidationError:
        raise ValueError('Use the unchanged basis returned by meeting_transcript') from None
    if basis != basis_for(snapshot):
        raise ValueError('Transcript or saved notes changed; restart meeting_transcript at offset 0 without a basis, review the current transcript, and rebuild the complete notes before saving')


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
        'transcription_chunks': snapshot.get('transcription_chunks',[]),
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
    aliases = {f's{i}': s['id'] for i, s in enumerate(snapshot['segments'])}
    # Validate against short IDs first, so diagnostics match the session's sources.
    # Store.save_analysis validates again against the real IDs before persistence.
    short_snapshot = {'segments': [{**s, 'id': f's{i}'} for i, s in enumerate(snapshot['segments'])]}
    payload = validate({'items': items}, short_snapshot)
    for item in payload['items']:
        for ref in [*item['citations'], item['owner'], item['due']]:
            sid = ref['segment_id']
            if sid is not None:
                ref['segment_id'] = aliases[sid]
    record = svc.save_analysis(snapshot, payload, origin='hermes_session')
    return {'status': 'saved', 'view_url': meeting_link(svc, meeting_id, 'notes'), 'meeting_id': meeting_id, 'analysis_id': record['id'],
            'revision': record['revision'], 'items_saved': len(record['items']),
            'items_by_kind': dict(Counter(item['kind'] for item in record['items'])),
            'basis': {'transcript_id': record['transcript'], 'revision': record['revision'],
                      'analysis_id': record['id']}}


def fingerprint(item):
    # Conservative lineage: only identical content/evidence/attribution is reusable.
    payload = {key: item[key] for key in ('kind', 'text', 'citations', 'owner', 'due')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate(payload, snapshot):
    check_schema(payload)
    segments = {s['id']: s for s in snapshot['segments']}
    short_ids = {s['id']: f's{i}' for i, s in enumerate(snapshot['segments'])}
    result = copy.deepcopy(payload)
    seen = set()
    failures = []

    def fail(path, code, sid=None):
        detail = {'path': path, 'code': code}
        if sid in short_ids:
            detail['source_id'] = short_ids[sid]
        failures.append(detail)

    for index, item in enumerate(result['items']):
        path = f'items[{index}]'
        cited = {}
        for ci, citation in enumerate(item['citations']):
            ref_path = f'{path}.citations[{ci}]'
            sid, quote = citation['segment_id'], citation['quote']
            if sid not in segments:
                fail(ref_path + '.segment_id', 'unknown_source')
            elif not quote.strip() or quote not in segments[sid]['text']:
                fail(ref_path + '.quote', 'quote_not_in_segment', sid)
            if sid in cited:
                fail(ref_path + '.segment_id', 'duplicate_source', sid)
            cited[sid] = quote
        owner, due = item['owner'], item['due']
        if owner['basis'] == 'unresolved':
            if owner['segment_id'] is not None or owner['name'] is not None:
                fail(path + '.owner', 'unresolved_owner_requires_null_fields')
        elif item['kind'] != 'commitment':
            fail(path + '.owner', 'owner_requires_commitment')
        else:
            sid = owner['segment_id']
            if sid not in segments:
                fail(path + '.owner.segment_id', 'unknown_source')
            elif sid not in cited:
                fail(path + '.owner.segment_id', 'owner_source_not_cited', sid)
            if owner['basis'] == 'speaker' and owner['name'] is not None:
                fail(path + '.owner.name', 'speaker_owner_requires_null_name')
            elif owner['basis'] == 'named':
                if not owner['name'] or not owner['name'].strip():
                    fail(path + '.owner.name', 'owner_name_required')
                elif sid in segments and sid in cited and owner['name'] not in cited[sid]:
                    fail(path + '.owner.name', 'owner_name_not_in_quote', sid)
        if any(v is not None for v in due.values()):
            sid = due['segment_id']
            if item['kind'] != 'commitment':
                fail(path + '.due', 'due_requires_commitment')
            elif sid not in segments:
                fail(path + '.due.segment_id', 'unknown_source')
            elif sid not in cited:
                fail(path + '.due.segment_id', 'due_source_not_cited', sid)
            elif not due['quote'] or not due['quote'].strip():
                fail(path + '.due.quote', 'due_quote_required')
            elif due['quote'] not in cited[sid]:
                fail(path + '.due.quote', 'due_quote_not_in_citation_quote', sid)
            if due['proposed_date'] is not None:
                try:
                    parsed = date.fromisoformat(due['proposed_date'])
                    if parsed.isoformat() != due['proposed_date']:
                        raise ValueError()
                except ValueError:
                    fail(path + '.due.proposed_date', 'invalid_calendar_date')
        key = fingerprint(item)
        if key in seen:
            fail(path, 'duplicate_item')
        seen.add(key)
    if failures:
        raise NotesValidationError(failures)
    for item in result['items']:
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
