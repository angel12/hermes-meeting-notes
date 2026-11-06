"""The verified NeMo-Speech.cpp v0.2.0 contract, not a generic OpenAI adapter."""
import httpx


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
