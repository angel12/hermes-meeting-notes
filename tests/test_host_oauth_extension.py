"""Apply the pinned host patch to a temporary copy; exercise real core admission.

Uses installed Hermes source, synthetic profiles and a mock HTTP transport. The
actual Hermes checkout, auth stores and running services are never changed.
"""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

ROOT=Path(__file__).parents[1]
HERMES=Path(os.environ.get('HERMES_AGENT_SRC',Path.home()/'.hermes/hermes-agent'))
EXPECTED='60c537ec1d98e84c2bcf24cc2a8df20d1b1e96bba305d7f7e0970b002af15a9d'


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def host(tmp_path,monkeypatch):
    source=HERMES/'hermes_cli/plugin_provider_requests.py'
    if not source.exists(): pytest.skip('Installed Hermes source is required for host-extension tests')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=EXPECTED:
        pytest.skip('Installed Hermes module differs from pinned test image; rebase host patch first')
    root=tmp_path/'host'; (root/'hermes_cli').mkdir(parents=True)
    patched=root/'hermes_cli/plugin_provider_requests.py'; patched.write_bytes(source.read_bytes())
    result=subprocess.run([sys.executable,str(ROOT/'deploy/hermes-test/install-provider-requests.py'),str(root),str(ROOT/'deploy/hermes-test/provider-requests.patch')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    monkeypatch.syspath_prepend(str(HERMES))
    monkeypatch.setenv('HERMES_HOME',str(tmp_path/'profile-a'))
    broker=load(patched,'synthetic_provider_request_host')
    roots={tmp_path/'profile-a/plugins',tmp_path/'profile-b/plugins'}
    monkeypatch.setattr(broker,'_plugin_roots',lambda:roots)
    plugin=tmp_path/'profile-a/plugins/synthetic'
    plugin.mkdir(parents=True)
    manifest=plugin/'plugin.yaml'; manifest.write_text('name: synthetic\nrequires_auth: [xai-oauth]\n')
    caller=plugin/'caller.py'
    caller.write_text('def send(broker, url="https://api.x.ai/v1/stt", **kwargs):\n    return broker.credentialed_provider_request("xai-oauth", "POST", url, **kwargs)\n')
    caller=load(caller,'synthetic_plugin_caller')
    from tools import xai_http
    resolutions=[]
    def resolve():
        profile=Path(os.environ['HERMES_HOME']).name
        resolutions.append(profile)
        return dict(provider='xai-oauth',api_key='synthetic-token-'+profile,base_url='https://api.x.ai/v1')
    monkeypatch.setattr(xai_http,'resolve_xai_http_credentials',resolve)
    sent=[]
    original=httpx.Client
    response={'status':200,'headers':{},'body':b'{"text":"synthetic"}'}
    def handle(request):
        sent.append((request,request.read()))
        if response.get('error'): raise httpx.ReadTimeout('synthetic-token-profile-a',request=request)
        return httpx.Response(response['status'],headers=response['headers'],content=response['body'])
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(handle),**kwargs)
    monkeypatch.setattr(httpx,'Client',client)
    return broker,caller,manifest,xai_http,resolutions,sent,response


def test_real_admission_multipart_and_profile_resolution(host,monkeypatch,tmp_path):
    broker,caller,_,_,resolutions,sent,_=host
    assert broker.provider_request_capabilities()['multipart'] is True
    assert resolutions==[] and sent==[]  # no credential lookup for UI status
    for profile in ('profile-a','profile-b'):
        monkeypatch.setenv('HERMES_HOME',str(tmp_path/profile))
        response=caller.send(broker,data={'model':'grok-voice-transcribe-2.0','diarize':'true'},
                             files={'file':('synthetic.wav',io.BytesIO(b'RIFFsynthetic'),'audio/wav')})
        request,body=sent[-1]
        assert request.headers['Authorization']=='Bearer synthetic-token-'+profile
        assert body.index(b'name="diarize"')<body.index(b'name="file"')
        assert b'\r\ntrue\r\n' in body
        assert response.body==b'{"text":"synthetic"}'
        assert 'synthetic-token' not in repr(response) and not hasattr(response,'request')
    assert resolutions==['profile-a','profile-b']


def test_uninstalled_or_undeclared_call_fails_before_auth(host):
    broker,caller,manifest,_,resolutions,sent,_=host
    with pytest.raises(PermissionError): broker.credentialed_provider_request('xai-oauth','POST','https://api.x.ai/v1/stt')
    manifest.write_text('name: synthetic\n')
    with pytest.raises(PermissionError): caller.send(broker)
    assert resolutions==[] and sent==[]


@pytest.mark.parametrize('url',['http://api.x.ai/v1/stt','https://evil.test/v1/stt','https://api.x.ai.evil.test/v1/stt','https://api.x.ai:444/v1/stt','https://user:password@api.x.ai/v1/stt'])
def test_origin_refused_before_resolving_oauth(host,url):
    broker,caller,_,_,resolutions,sent,_=host
    with pytest.raises(PermissionError): caller.send(broker,url=url)
    assert resolutions==[] and sent==[]


def test_no_api_key_fallback_or_foreign_credential(host,monkeypatch):
    broker,caller,_,xai,_,sent,_=host
    monkeypatch.setattr(xai,'resolve_xai_http_credentials',lambda:dict(provider='xai',api_key='synthetic-paid-key',base_url='https://api.x.ai/v1'))
    with pytest.raises(broker.ProviderNotSignedIn) as error: caller.send(broker)
    assert 'synthetic-paid-key' not in str(error.value) and not sent
    monkeypatch.setattr(xai,'resolve_xai_http_credentials',lambda:dict(provider='xai-oauth',api_key='synthetic-token',base_url='https://other.test/v1'))
    with pytest.raises(PermissionError): caller.send(broker)
    assert not sent


def test_redirect_and_timeout_are_not_replayed_or_leaked(host):
    broker,caller,_,_,_,sent,response=host
    response.update(status=307,headers={'location':'https://evil.test/'})
    assert caller.send(broker).status==307 and len(sent)==1
    response.update(error=True)
    with pytest.raises(RuntimeError) as error: caller.send(broker)
    assert len(sent)==2 and 'synthetic-token' not in str(error.value)
    assert error.value.__context__ is None and not hasattr(error.value,'request')


@pytest.mark.parametrize('headers',[{'Authorization':'caller-key'},{'Host':'other.test'},{'Cookie':'credential'}])
def test_plugin_cannot_supply_credentials_or_host_header(host,headers):
    broker,caller,_,_,resolutions,sent,_=host
    with pytest.raises(PermissionError): caller.send(broker,headers=headers)
    assert not resolutions and not sent


def test_rejects_ambiguous_body_before_auth(host):
    broker,caller,_,_,resolutions,sent,_=host
    with pytest.raises(ValueError): caller.send(broker,json={'x':1},data={'y':'2'})
    assert not resolutions and not sent


def test_json_requests_remain_supported_and_refresh_errors_are_sanitized(host,monkeypatch):
    broker,caller,_,xai,_,sent,_=host
    assert caller.send(broker,json={'synthetic':True}).status==200
    assert sent[0][1]==b'{"synthetic":true}'
    def failed_refresh(): raise RuntimeError('synthetic-refresh-secret')
    monkeypatch.setattr(xai,'resolve_xai_http_credentials',failed_refresh)
    with pytest.raises(RuntimeError) as error: caller.send(broker)
    assert 'synthetic-refresh-secret' not in str(error.value) and len(sent)==1
    assert error.value.__context__ is None
