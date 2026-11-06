"""Explicit transcription contracts. OAuth requests are sent by the host."""
import httpx

from .connections import XAI_ENDPOINT


class SetupError(ValueError):
    """Safe, fixed setup error text; never contains provider exception details."""


def xai_host_status():
    try:
        from hermes_cli.plugin_provider_requests import provider_request_capabilities
        capabilities = provider_request_capabilities()
        ready = capabilities.get('multipart') is True and 'xai-oauth' in capabilities.get('providers', [])
    except (ImportError, AttributeError):
        ready = False
    return dict(available=ready, message=(
        'Uses the importing host profile’s Hermes xAI login. STT subscription access still needs verification.' if ready else
        'This Hermes host needs the OAuth multipart request extension before xAI can be selected. Custom remains available.'))


def require_xai_host():
    status = xai_host_status()
    if not status['available']:
        raise SetupError(status['message'])


class XaiOAuth:
    def __init__(self, settings, check_owner):
        self.settings, self.check_owner = settings, check_owner

    def probe(self):
        self.check_owner()
        require_xai_host()
        if self.settings['endpoint'] != XAI_ENDPOINT:
            raise SetupError('xAI OAuth requires https://api.x.ai/v1; custom endpoints are not allowed.')

    def transcribe(self, path):
        self.probe()
        from hermes_cli.plugin_provider_requests import credentialed_provider_request, ProviderNotSignedIn, ProviderRequestNotSent
        try:
            with path.open('rb') as audio:
                response = credentialed_provider_request('xai-oauth', 'POST', XAI_ENDPOINT+'/stt',
                    data={'model':self.settings['model'], 'diarize':'true'},
                    files={'file':('meeting.wav',audio,'audio/wav')}, timeout=self.settings['timeout'])
            return response.status, response.body
        except ProviderNotSignedIn:
            raise SetupError('Sign in to xAI in the importing Hermes host profile, then Retry. No audio request was sent; API-key fallback is disabled.') from None
        except (ProviderRequestNotSent, PermissionError):
            raise SetupError('Hermes refused the xAI request before sending audio. Check the host sign-in, plugin declaration and provider endpoint before Retry.') from None


def canonical_response(raw, adapter):
    if adapter == 'nemo_speech_cpp':
        return raw
    if adapter != 'xai_oauth':
        raise ValueError('Unsupported transcription adapter')
    # A live synthetic silence request returned text="" with no words field.
    # Treat it as no recognized speech, not proof of silence or diarization.
    words = raw.get('words', [] if raw.get('text') == '' else None)
    if not isinstance(words,list) or any(not isinstance(w,dict) or type(w.get('speaker')) is not int for w in words):
        raise ValueError('xAI must return timestamped words with integer speaker labels')
    return {**raw,'words':[{**w,'word':w.get('text')} for w in words]}


class NemoSpeechCpp:
    def __init__(self, settings, key):
        self.settings, self.key = settings, key

    def probe(self):
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            response = client.get(self.settings['endpoint']+'/models', headers={'Authorization':'Bearer '+self.key})
            response.raise_for_status()
            models = response.json().get('data', [])
            model = next((m for m in models if m.get('id') == self.settings['model']), None)
            if model is None:
                raise ValueError('Configured model is not advertised by endpoint')
            if model.get('speaker_diarization') is not True or model.get('word_timestamps') is not True:
                raise ValueError('Model does not advertise diarization and word timestamps')
            return model

    def transcribe(self, path):
        with path.open('rb') as audio, httpx.Client(timeout=self.settings['timeout'], follow_redirects=False, trust_env=False) as client:
            response = client.post(self.settings['endpoint']+'/audio/transcriptions',
                headers={'Authorization':'Bearer '+self.key},
                files={'file':('meeting.wav',audio,'audio/wav')},
                data={'model':self.settings['model'],'response_format':'verbose_json','diarization':'true'})
            # Return bytes even on errors: worker durably saves before parsing.
            return response.status_code, response.content
