"""Hermes registration. No background task or credential access during import."""
import importlib.util
import json
from pathlib import Path
import sys

if 'meeting_notes' not in sys.modules:
    root = Path(__file__).parent/'meeting_notes'
    spec = importlib.util.spec_from_file_location('meeting_notes',root/'__init__.py',submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['meeting_notes'] = module
    spec.loader.exec_module(module)


def register(ctx):
    from meeting_notes import runtime
    from meeting_notes.analysis import BASIS_SCHEMA, SCHEMA, NotesValidationError, read_transcript, save_notes
    ctx.register_skill('analyze', Path(__file__).parent/'skills/analyze/SKILL.md',
                       description='Generate sourced meeting notes in this Hermes session from an imported transcript, then validate and save them to Meeting Notes.')
    tools = {
        'meeting_transcript': ({'meeting_id':{'type':'string'},'source_profile':{'type':'string'},'offset':{'type':'integer','minimum':0},'basis':BASIS_SCHEMA},['meeting_id'],lambda a:read_transcript(runtime.analysis_service(a['meeting_id'],a.get('source_profile')),a['meeting_id'],a.get('offset',0),a.get('basis'))),
        'meeting_save_notes': ({'meeting_id':{'type':'string'},'source_profile':{'type':'string'},'basis':BASIS_SCHEMA,'items':SCHEMA['properties']['items']},['meeting_id','basis','items'],lambda a:save_notes(runtime.analysis_service(a['meeting_id'],a.get('source_profile')),a['meeting_id'],a['basis'],a['items'])),
        'meeting_list': ({},[],lambda a:runtime.service().list()),
        'meeting_get': ({'meeting_id':{'type':'string'}},['meeting_id'],lambda a:runtime.service().get(a['meeting_id'])),
        'meeting_correct': ({k:{'type':'integer' if k=='revision' else 'string'} for k in ('meeting_id','kind','target','value','revision')},['meeting_id','kind','target','value','revision'],lambda a:runtime.service().correct(a['meeting_id'],a['kind'],a['target'],a['value'],a['revision'])),
        'meeting_retry_transcription': ({'meeting_id':{'type':'string'},'use_current_settings':{'type':'boolean'}},['meeting_id'],lambda a:_retry(runtime,a['meeting_id'],a.get('use_current_settings',False))),
    }
    for name,(properties,required,operation) in tools.items():
        def handler(args,_operation=operation,**kwargs):
            try:
                return json.dumps(_operation(args), ensure_ascii=False)
            except NotesValidationError as exc:
                return json.dumps(exc.as_dict())
            except ValueError as exc:
                return json.dumps({'error':str(exc)})
            except Exception:
                return json.dumps({'error':'Meeting operation failed; check host configuration'})
        descriptions = {
            'meeting_transcript': 'Read corrected transcript text for meeting_notes:analyze. Pass source_profile when supplied by the meeting request; only its selected analysis profile may read it. Start at offset 0. Page size varies: wait for each page and follow its next_offset until null with the unchanged basis and source_profile; never guess page strides. Use this tool to re-read sources when repairing notes. No model call.',
            'meeting_save_notes': 'Validate and save the complete notes set produced in this session. Every success becomes the current analysis, not an append; never submit diagnostic subsets or parallel saves. Read the whole transcript first, echo basis/source_profile, and use short source IDs with exact quotes. Rejections write nothing and report zero-based failure paths and known source IDs; repair through meeting_transcript and resubmit the complete set. Checks profile and stale revisions; creates no external tasks.',
            'meeting_retry_transcription': 'Explicitly retry a stopped transcription. Completed chunks are retained. Set use_current_settings=true only when the user requests retry with updated endpoint/timeout/audio limit; unfinished audio is replanned, and the model must stay the same if any chunk completed.',
        }
        description = descriptions.get(name,name.replace('_',' ')+'. Corrections require the current revision. Retry explicitly resumes unfinished audio chunks; completed chunks are retained.')
        ctx.register_tool(name=name,toolset='meeting_notes',schema={'name':name,'description':description, 'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}},handler=handler)


def _retry(runtime,ident,use_current_settings=False):
    result=runtime.service().retry(ident,use_current_settings)
    runtime.start()
    return result
