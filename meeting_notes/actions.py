"""Sourced action proposals and user-created handoffs. No external service calls."""
from .links import meeting_link
import copy
import hashlib
import json
import time
from urllib.parse import urlsplit

from .analysis import SCHEMA, obj, require_basis, basis_for, validate, project
from .store import uid

ACTION_ITEM = copy.deepcopy(SCHEMA['properties']['items']['items'])
ACTION_ITEM['properties']['kind'] = {'enum': ['commitment', 'suggestion']}
ACTION_ITEM['properties']['action_type'] = {'enum': ['task', 'meeting', 'training']}
ACTION_ITEM['required'].append('action_type')
ACTIONS_SCHEMA = obj({'items': {'type': 'array', 'maxItems': 60, 'items': ACTION_ITEM}})


def schema_check(schema, value):
    from jsonschema import Draft202012Validator, ValidationError
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError:
        raise ValueError('Action request does not match the schema; nothing was saved') from None


def latest(db, meeting):
    row = db.execute('SELECT data FROM action_sets WHERE meeting=? ORDER BY rowid DESC LIMIT 1', (meeting,)).fetchone()
    return json.loads(row[0]) if row else None


def fingerprint(item):
    return hashlib.sha256(json.dumps({k: v for k, v in item.items() if k != 'id'}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def view(svc, meeting, *, short_source_ids=False):
    with svc.db() as db:
        db.execute('BEGIN')
        # get uses a separate read connection; final mutations always fence both versions.
        snapshot = svc.get(meeting)
        record = latest(db, meeting)
        rows = [dict(r) for r in db.execute('SELECT * FROM action_handoffs WHERE meeting=? ORDER BY created DESC', (meeting,))]
    projected = None
    if record:
        current = snapshot
        if record['transcript'] != snapshot['active']:
            current = {**snapshot, 'segments': list(record['sources'].values()), 'revision': -1}
        projected = project(record, current)
    items = projected['items'] if projected else []
    ids = {i['id'] for i in items}
    for row in rows:
        row['snapshot'] = json.loads(row['snapshot'])
        row['result'] = json.loads(row['result']) if row['result'] else None
        row['history'] = json.loads(row['history'])
        row['needs_review'] = (row['revision'] != snapshot['revision'] or row['transcript'] != snapshot['active'] or row['action_id'] not in ids)
    result = {'meeting_id': meeting, 'basis': basis_for(snapshot), 'actions_id': record['id'] if record else None,
            'items': items, 'sources': record['sources'] if record else {},
            'needs_review': projected['needs_review'] if projected else False,
            'handoffs': rows}
    if short_source_ids:
        # Only the agent-facing read uses transcript aliases. UI playback, persisted
        # lineage and frozen handoff receipts retain their canonical source IDs.
        result = copy.deepcopy(result)
        current = not record or record['transcript'] == snapshot['active']
        result['source_id_format'] = ('Current proposal references use the exact unpadded IDs from meeting_transcript, e.g. s0, s163. '
                                      'Handoffs are frozen history, not save input.' if current else
                                      'These proposals belong to an older transcript. Their IDs are historical, not valid save input. '
                                      'Read meeting_transcript and rebuild proposals using its exact IDs.')
        result['sources_match_current_transcript'] = current
        if current:
            aliases = {s['id']: f's{n}' for n, s in enumerate(snapshot['segments'])}
            for item in result['items']:
                for ref in [*item['citations'], item['owner'], item['due']]:
                    if ref['segment_id'] is not None:
                        ref['segment_id'] = aliases[ref['segment_id']]
            result['sources'] = {aliases[sid]: {**source, 'id': aliases[sid]}
                                 for sid, source in result['sources'].items()}
            for source in result['sources'].values():
                if 'source' in source:
                    # Preserve recording/timing context without suggesting another
                    # citation ID to copy into a current-transcript save.
                    source['source'].pop('segment_id', None)
    return result


def save(svc, meeting, basis, actions_id, items):
    snapshot = svc.get(meeting)
    require_basis(snapshot, basis)
    if not snapshot['active'] or not snapshot['segments']:
        raise ValueError('Read a completed transcript first')
    schema_check(ACTIONS_SCHEMA, {'items': items})
    plain = [{k: v for k, v in i.items() if k != 'action_type'} for i in items]
    short_snapshot = {'segments': [{**s, 'id': f's{n}'} for n, s in enumerate(snapshot['segments'])]}
    normalized = validate({'items': plain}, short_snapshot)['items']
    aliases = {f's{n}': s['id'] for n, s in enumerate(snapshot['segments'])}
    for item, supplied in zip(normalized, items):
        item['action_type'] = supplied['action_type']
        for ref in [*item['citations'], item['owner'], item['due']]:
            if ref['segment_id'] is not None:
                ref['segment_id'] = aliases[ref['segment_id']]
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        svc.check_analysis_profile(db, meeting)
        m = db.execute('SELECT active,revision FROM meetings WHERE id=?', (meeting,)).fetchone()
        old_notes = db.execute('SELECT id FROM analyses WHERE meeting=? AND transcript=? ORDER BY rowid DESC LIMIT 1', (meeting, m['active'])).fetchone()
        if m['active'] != basis['transcript_id'] or m['revision'] != basis['revision'] or (old_notes[0] if old_notes else None) != basis['analysis_id']:
            raise ValueError('Meeting or notes changed; reread before saving actions')
        old = latest(db, meeting)
        if (old['id'] if old else None) != actions_id:
            raise ValueError('Action items changed; reload meeting_actions before saving')
        matches = {}
        for item in old['items'] if old and old['transcript'] == m['active'] else []:
            matches.setdefault(fingerprint(item), []).append(item['id'])
        for item in normalized:
            candidates = matches.get(fingerprint(item), [])
            if len(candidates) == 1:
                item['id'] = candidates[0]
        cited = {c['segment_id'] for i in normalized for c in i['citations']}
        record = {'id': uid(), 'transcript': m['active'], 'revision': m['revision'], 'created': time.time(),
                  'profile': getattr(svc, 'analysis_actor', None), 'items': normalized,
                  'sources': {s['id']: s for s in snapshot['segments'] if s['id'] in cited}}
        db.execute('INSERT INTO action_sets VALUES(?,?,?,?,?)', (record['id'], meeting, m['active'], m['revision'], json.dumps(record)))
    return {'status': 'saved', 'view_url': meeting_link(svc, meeting, 'actions'), 'actions_id': record['id'], 'items_saved': len(normalized)}


def _request_id(value):
    if not isinstance(value, str) or not 16 <= len(value) <= 100 or not all(c.isalnum() or c in '-_' for c in value):
        raise ValueError('A stable request ID is required')


def create_handoff(svc, meeting, action_id, actions_id, revision, destination, instructions, request_id):
    _request_id(request_id)
    if destination not in ('calendar', 'todo') or not isinstance(instructions, str) or len(instructions) > 4000:
        raise ValueError('Choose calendar or to-do list and instructions up to 4000 characters')
    if getattr(svc, 'analysis_actor', None) is not None:
        raise ValueError('Create handoffs from the importing profile UI')
    snapshot = svc.get(meeting)
    actor = snapshot['analysis_profile']
    if not actor or not snapshot['owner_profile']:
        raise ValueError('Choose an available Hermes profile before handing off an action')
    request = dict(action_id=action_id, actions_id=actions_id, revision=revision, destination=destination, instructions=instructions)
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT id,request_hash FROM action_handoffs WHERE meeting=? AND request_id=?', (meeting, request_id)).fetchone()
        if old:
            if old['request_hash'] != digest:
                raise ValueError('Conflicting repeated handoff request')
            return {'handoff_id': old['id']}
        m = db.execute('SELECT active,revision FROM meetings WHERE id=?', (meeting,)).fetchone()
        selected = svc.check_analysis_profile(db, meeting)
        record = latest(db, meeting)
        if (type(revision) is not int or m['revision'] != revision or snapshot['revision'] != revision or selected != actor or not record
                or record['id'] != actions_id or record['transcript'] != m['active'] or record['revision'] != revision):
            raise ValueError('Action items or profile changed; regenerate and review before handing off')
        item = next((i for i in record['items'] if i['id'] == action_id), None)
        if item is None:
            raise ValueError('Action item not found in the current set')
        if db.execute("SELECT 1 FROM action_handoffs WHERE meeting=? AND action_id=? AND state NOT IN ('failed','cancelled')", (meeting, action_id)).fetchone():
            raise ValueError('This action already has a handoff; review or continue it instead of creating a duplicate')
        projected = project(record, snapshot)
        item = next(i for i in projected['items'] if i['id'] == action_id)
        sources = {c['segment_id']: record['sources'][c['segment_id']] for c in item['citations']}
        frozen = {'item': item, 'sources': sources, 'title': snapshot['title'], 'date': snapshot['date'],
                  'timezone': snapshot['timezone'], 'original_instructions': instructions, 'destination': destination}
        ident = uid()
        db.execute('INSERT INTO action_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (ident, meeting, action_id, request_id, digest, actor, m['active'], revision, destination,
                    instructions, json.dumps(frozen), 'pending', 0, None, '[]', time.time(), None))
    return {'handoff_id': ident}


def user_update(svc, meeting, handoff_id, version, operation, instructions=''):
    if getattr(svc, 'analysis_actor', None) is not None:
        raise ValueError('Continue or cancel handoffs from the importing profile UI')
    if operation not in ('cancel', 'continue') or not isinstance(instructions, str) or len(instructions) > 4000:
        raise ValueError('Invalid handoff update')
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM action_handoffs WHERE id=? AND meeting=?', (handoff_id, meeting)).fetchone()
        if not row or type(version) is not int or row['version'] != version:
            raise ValueError('Handoff changed; refresh before updating')
        if operation == 'cancel':
            if row['state'] not in ('pending', 'needs_input'):
                raise ValueError('Only an unstarted handoff or one waiting for details can be cancelled')
            state = 'cancelled'
        else:
            if row['state'] != 'needs_input':
                raise ValueError('Only a handoff waiting for details can be continued; uncertain outcomes must be reconciled')
            state = 'pending'
        history = json.loads(row['history'])
        history.append({'at': time.time(), 'operation': operation, 'instructions': instructions})
        db.execute('UPDATE action_handoffs SET state=?,version=version+1,instructions=?,history=?,updated=? WHERE id=?',
                   (state, instructions if operation == 'continue' else row['instructions'], json.dumps(history), time.time(), handoff_id))
    return {'status': state}


def callback(svc, meeting, handoff_id, operation, version=None, result=None, attempt_token=None):
    """Claim once before acting; callbacks may record late results after corrections."""
    if operation not in ('read', 'begin', 'report'):
        raise ValueError('Choose read, begin or report')
    actor = getattr(svc, 'handoff_actor', None)
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM action_handoffs WHERE id=? AND meeting=?', (handoff_id, meeting)).fetchone()
        if (not row or actor != row['profile'] or handoff_id != getattr(svc, 'handoff_id', None)
                or meeting != getattr(svc, 'handoff_meeting', None)):
            raise ValueError('This handoff is not assigned to the current profile')
        m = db.execute('SELECT active,revision FROM meetings WHERE id=?', (meeting,)).fetchone()
        selected = db.execute('SELECT profile FROM meeting_profiles WHERE meeting=?', (meeting,)).fetchone()
        profile = selected[0] if selected else svc.owner_profile
        record = latest(db, meeting)
        current_ids = {i['id'] for i in record['items']} if record else set()
        stale = (m['active'] != row['transcript'] or m['revision'] != row['revision'] or profile != row['profile'] or row['action_id'] not in current_ids)
        if operation == 'read':
            return {'handoff_id': handoff_id, 'state': row['state'], 'version': row['version'], 'needs_review': stale,
                    'destination': row['destination'], 'instructions': row['instructions'], 'snapshot': json.loads(row['snapshot']),
                    'result': json.loads(row['result']) if row['result'] else None, 'may_execute': False}
        if type(version) is not int or version != row['version']:
            # Exact repeated callback is idempotent, including a lost response.
            if operation == 'report' and result is not None and row['result'] and json.loads(row['result']) == result:
                return {'status': row['state'], 'version': row['version'], 'duplicate': True, 'needs_review': stale}
            raise ValueError('Handoff changed; read its current state before proceeding')
        if operation == 'begin':
            if stale:
                raise ValueError('Meeting, action or profile changed; ask the user to review before acting')
            if row['state'] != 'pending':
                return {'status': row['state'], 'version': row['version'], 'may_execute': False}
            token = uid()
            db.execute('INSERT OR REPLACE INTO handoff_claims VALUES(?,?)', (handoff_id, token))
            db.execute("UPDATE action_handoffs SET state='running',version=version+1,updated=? WHERE id=?", (time.time(), handoff_id))
            return {'status': 'running', 'version': version + 1, 'may_execute': True, 'attempt_token': token}
        schema_check(RESULT_SCHEMA, result)
        if row['state'] not in ('running', 'unknown'):
            raise ValueError('Only started or uncertain handoffs can receive an outcome')
        claim = db.execute('SELECT token FROM handoff_claims WHERE handoff=?', (handoff_id,)).fetchone()
        if result['status'] in ('failed', 'needs_input') and (not claim or attempt_token != claim[0]):
            raise ValueError('Only the chat that claimed this attempt can confirm no external write; reconcile to completed or unknown instead')
        if result['status'] == 'completed' and (not result['provider'].strip() or not result['external_id'].strip()):
            raise ValueError('A completed handoff needs the real provider and external task/event ID')
        if not result['summary'].strip():
            raise ValueError('Describe the actual outcome or blocker')
        if result['status'] in ('failed', 'needs_input') and (result['external_id'] or result['url']):
            raise ValueError('A known external result must be completed or unknown, not safe to resend')
        if result['url']:
            parsed = urlsplit(result['url'])
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Result link must be HTTP(S) without embedded credentials')
        # Unknown work can be reconciled to completed/failed; it cannot become safe-to-resend needs_input.
        if row['state'] == 'unknown' and result['status'] == 'needs_input':
            raise ValueError('Reconcile the uncertain external outcome before requesting a new attempt')
        history = json.loads(row['history'])
        history.append({'at': time.time(), 'profile': actor, 'result': result})
        db.execute('UPDATE action_handoffs SET state=?,version=version+1,result=?,history=?,updated=? WHERE id=?',
                   (result['status'], json.dumps(result), json.dumps(history), time.time(), handoff_id))
        return {'status': result['status'], 'version': version + 1, 'needs_review': stale}


RESULT_SCHEMA = obj({
    'status': {'enum': ['completed', 'needs_input', 'failed', 'unknown']},
    'summary': {'type': 'string', 'minLength': 1, 'maxLength': 4000},
    'provider': {'type': 'string', 'maxLength': 200},
    'external_id': {'type': 'string', 'maxLength': 1000},
    'url': {'type': 'string', 'maxLength': 2000},
})
