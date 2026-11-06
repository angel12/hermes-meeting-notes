"""Host-relative links; no hostname, credentials or transcript content."""
from urllib.parse import urlencode


def meeting_link(svc, meeting, tab):
    if tab not in ('notes', 'actions'):
        raise ValueError('Unsupported meeting view')
    params = {'meeting': meeting, 'tab': tab}
    profile = getattr(svc, 'owner_profile', None)
    if profile:
        params['profile'] = profile
    return '#/meeting-notes?' + urlencode(params)
