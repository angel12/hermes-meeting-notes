"""Imported independently by Hermes. Host supplies authentication/secret scope."""
import importlib.util
import base64
import binascii
import json
from pathlib import Path
import sys

# Load our namespaced package without relying on host cwd or modifying sys.path.
_root = Path(__file__).resolve().parent.parent
if 'meeting_notes' not in sys.modules:
    _spec = importlib.util.spec_from_file_location('meeting_notes',_root/'meeting_notes/__init__.py',submodule_search_locations=[str(_root/'meeting_notes')])
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules['meeting_notes'] = _mod
    _spec.loader.exec_module(_mod)

from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from meeting_notes import runtime
from meeting_notes.service import CHUNK
from meeting_notes import actions
from meeting_notes import storage

router = APIRouter()


def call(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None


@router.get('/ping')
def ping():
    svc = runtime.service()
    return {'ok':True,'adapter':svc.settings()['adapter'],'analysis':'session_skill','profile_mode':'single'}


@router.get('/settings')
def settings():
    return call(lambda:runtime.service().settings())


@router.get('/storage-settings')
def storage_settings():
    return call(lambda:storage.settings(runtime.service()))


@router.put('/storage-settings')
def configure_storage(body: dict):
    return call(lambda:storage.configure(runtime.service(),
        {k: v for k, v in body.items() if k != 'acknowledge_lossy_replacement'},
        body.get('acknowledge_lossy_replacement', False)))


@router.get('/meetings/{ident}/storage')
def recording_storage(ident: str):
    return call(lambda:storage.status(runtime.service(), ident))


@router.post('/meetings/{ident}/storage')
def apply_recording_storage(ident: str, body: dict):
    if set(body) != {'apply_current_settings', 'settings'} or body['apply_current_settings'] is not True or not isinstance(body['settings'], dict):
        raise HTTPException(400, 'Explicitly apply the reviewed storage settings')
    result = call(lambda:storage.enqueue(runtime.service(), ident, expected_settings=body['settings']))
    runtime.start()
    return result


@router.get('/analysis-profiles')
def analysis_profiles():
    from meeting_notes.profiles import options
    return call(options)


@router.put('/meetings/{ident}/analysis-profile')
def analysis_profile(ident: str, body: dict):
    from meeting_notes.profiles import select
    if set(body) != {'profile','revision'}:
        raise HTTPException(400,'Expected profile and revision')
    return call(lambda:select(runtime.service(),ident,body['profile'],body['revision']))


@router.put('/settings')
def configure(body: dict):
    return call(lambda:runtime.service().configure(body))


def connection_view(state):
    from meeting_notes.asr import xai_host_status
    return {**state, 'availability': {'custom': {'available': True}, 'xai': xai_host_status()}}


@router.get('/connections')
def transcription_connections():
    return call(lambda:connection_view(runtime.service().connections()))


@router.put('/connections/{name}')
def save_connection(name: str, body: dict):
    from meeting_notes.connections import save
    if set(body) != {'settings','revision'}:
        raise HTTPException(400,'Expected settings and revision; credentials belong in Hermes')
    return call(lambda:connection_view(save(runtime.service(),name,body['settings'],body['revision'])))


@router.post('/connections/active')
def select_connection(body: dict):
    from meeting_notes.connections import select
    if set(body) != {'provider','revision'} or not isinstance(body['provider'], str):
        raise HTTPException(400,'Expected provider and revision')
    return call(lambda:connection_view(select(runtime.service(),body['provider'],body['revision'])))


@router.get('/meetings')
def meetings():
    return call(lambda:runtime.service().list())


@router.post('/meetings/combine')
def combine(body: dict):
    if set(body) != {'request_id','title','date','timezone','sources'}:
        raise HTTPException(400,'Expected request_id, title, date, timezone and ordered sources')
    return call(lambda:runtime.service().combine(**body))


@router.get('/meetings/{ident}')
def meeting(ident: str):
    return call(lambda:runtime.service().get(ident))


@router.get('/meetings/{ident}/deletion')
def deletion_review(ident: str):
    from meeting_notes import deletion
    return call(lambda:deletion.review(runtime.service(), ident))


@router.post('/meetings/{ident}/delete')
def delete_meeting(ident: str, body: dict):
    from meeting_notes import deletion
    if set(body) != {'token', 'confirm'}:
        raise HTTPException(400, 'Expected reviewed token and explicit confirmation')
    return call(lambda:deletion.remove(runtime.service(), ident, **body))


@router.get('/deletions')
def deletion_status():
    from meeting_notes import deletion
    return call(lambda:deletion.status(runtime.service()))


@router.post('/deletions/cleanup')
def deletion_cleanup():
    from meeting_notes import deletion
    return call(lambda:deletion.cleanup(runtime.service()))


@router.get('/meetings/{ident}/analyses/{analysis_id}')
def analysis(ident: str, analysis_id: str):
    return call(lambda:runtime.service().get_analysis(ident,analysis_id))


@router.get('/meetings/{ident}/actions')
def action_items(ident: str):
    return call(lambda:actions.view(runtime.service(),ident))


@router.post('/meetings/{ident}/handoffs')
def handoff(ident: str, body: dict):
    if set(body) != {'action_id','actions_id','revision','destination','instructions','request_id'}:
        raise HTTPException(400,'Expected action, action-set, revision, destination, instructions and request ID')
    return call(lambda:actions.create_handoff(runtime.service(),ident,**body))


@router.post('/meetings/{ident}/handoffs/{handoff_id}')
def update_handoff(ident: str, handoff_id: str, body: dict):
    if set(body) != {'version','operation','instructions'}:
        raise HTTPException(400,'Expected version, operation and instructions')
    return call(lambda:actions.user_update(runtime.service(),ident,handoff_id,**body))


@router.post('/uploads')
def upload(body: dict):
    if set(body) != {'size','title','date','timezone'}:
        raise HTTPException(400,'Expected size, title, date, timezone')
    return {**call(lambda:runtime.service().begin_upload(**body)), 'chunk_transport': 'json_base64'}


@router.put('/uploads/{ident}/chunks/{index}')
async def chunk(ident: str,index: int,sha: str,file: UploadFile = File(...)):
    data = await file.read(CHUNK+1)
    return call(lambda:runtime.service().chunk(ident,index,data,sha))


@router.put('/uploads/{ident}/chunks/{index}/json')
async def json_chunk(ident: str, index: int, sha: str, request: Request):
    # Desktop's OAuth remote bridge supports JSON, but not multipart uploads.
    # Bound the wire body before parsing; decoded bytes use the same durable
    # upload validation/path as multipart. Host authentication is unchanged.
    encoded_limit = 4 * ((CHUNK + 2) // 3)
    wire_limit = encoded_limit + 1024
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > wire_limit:
            raise HTTPException(413, 'Upload chunk is too large')
        body.extend(part)
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or set(value) != {'base64'} or not isinstance(value['base64'], str):
            raise ValueError()
        if len(value['base64']) > encoded_limit:
            raise HTTPException(413, 'Upload chunk is too large')
        data = base64.b64decode(value['base64'], validate=True)
    except (ValueError, RecursionError, binascii.Error):
        raise HTTPException(400, 'Expected a base64-encoded upload chunk') from None
    if len(data) > CHUNK:
        raise HTTPException(413, 'Upload chunk is too large')
    return call(lambda:runtime.service().chunk(ident,index,data,sha))


@router.post('/uploads/{ident}/finalize')
def finalize(ident: str):
    result = call(lambda:runtime.service().finalize(ident))
    runtime.start()
    return result


@router.post('/worker/start')
def start():
    call(runtime.start)
    return {'started':True}


@router.post('/meetings/{ident}/retry')
def retry(ident: str, body: dict | None = None):
    if body is not None and set(body) != {'use_current_settings'}:
        raise HTTPException(400,'Expected use_current_settings')
    result = call(lambda:runtime.service().retry(ident,(body or {}).get('use_current_settings',False)))
    runtime.start()
    return result


@router.post('/meetings/{ident}/recover-response')
def recover_response(ident: str, body: dict | None = None):
    if body:
        raise HTTPException(400,'No recovery parameters are accepted')
    from meeting_notes import recovery
    result = call(lambda:recovery.request(runtime.service(),ident))
    runtime.start()
    return result


@router.post('/meetings/{ident}/corrections')
def correct(ident: str,body: dict):
    if set(body) != {'kind','target','value','revision'}:
        raise HTTPException(400,'Expected kind, target, value, revision')
    return call(lambda:runtime.service().correct(ident,**body))


@router.get('/meetings/{ident}/audio')
def audio(ident: str,start: float=0,duration: float=20):
    return call(lambda:runtime.service().audio_window(ident,start,duration))
