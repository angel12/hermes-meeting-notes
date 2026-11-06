"""Imported independently by Hermes. Host supplies authentication/secret scope."""
import importlib.util
from pathlib import Path
import sys

# Load our namespaced package without relying on host cwd or modifying sys.path.
_root = Path(__file__).resolve().parent.parent
if 'meeting_notes' not in sys.modules:
    _spec = importlib.util.spec_from_file_location('meeting_notes',_root/'meeting_notes/__init__.py',submodule_search_locations=[str(_root/'meeting_notes')])
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules['meeting_notes'] = _mod
    _spec.loader.exec_module(_mod)

from fastapi import APIRouter, HTTPException, UploadFile, File
from meeting_notes import runtime
from meeting_notes.service import CHUNK

router = APIRouter()


def call(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None


@router.get('/ping')
def ping():
    runtime.service()
    return {'ok':True,'adapter':'nemo_speech_cpp','analysis':'session_skill','profile_mode':'single'}


@router.get('/settings')
def settings():
    return call(lambda:runtime.service().settings())


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


@router.get('/meetings/{ident}/analyses/{analysis_id}')
def analysis(ident: str, analysis_id: str):
    return call(lambda:runtime.service().get_analysis(ident,analysis_id))


@router.post('/uploads')
def upload(body: dict):
    if set(body) != {'size','title','date','timezone'}:
        raise HTTPException(400,'Expected size, title, date, timezone')
    return call(lambda:runtime.service().begin_upload(**body))


@router.put('/uploads/{ident}/chunks/{index}')
async def chunk(ident: str,index: int,sha: str,file: UploadFile = File(...)):
    data = await file.read(CHUNK+1)
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


@router.post('/meetings/{ident}/corrections')
def correct(ident: str,body: dict):
    if set(body) != {'kind','target','value','revision'}:
        raise HTTPException(400,'Expected kind, target, value, revision')
    return call(lambda:runtime.service().correct(ident,**body))


@router.get('/meetings/{ident}/audio')
def audio(ident: str,start: float=0,duration: float=20):
    return call(lambda:runtime.service().audio_window(ident,start,duration))
