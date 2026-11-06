from urllib.parse import parse_qs

from meeting_notes import actions
from meeting_notes.analysis import read_transcript, save_notes
from meeting_notes.links import meeting_link
from test_analysis import meeting


def test_successful_saves_link_to_importing_profile_and_respective_tab(meeting):
    svc, mid = meeting
    svc.owner_profile = 'source-profile'
    svc.analysis_actor = 'source-profile'
    svc.analysis_meeting = mid
    # Default selection follows the source profile for this synthetic store.
    page = read_transcript(svc, mid)
    notes = save_notes(svc, mid, page['basis'], [])
    saved_actions = actions.save(svc, mid, notes['basis'], None, [])
    for response, tab in [(notes, 'notes'), (saved_actions, 'actions')]:
        assert response['status'] == 'saved'
        assert response['view_url'].startswith('#/meeting-notes?')
        assert parse_qs(response['view_url'].split('?', 1)[1]) == {
            'meeting': [mid], 'tab': [tab], 'profile': ['source-profile']}


def test_link_parameters_are_encoded_without_a_server_or_secrets():
    class S: owner_profile = 'a&tab=actions'
    result = meeting_link(S(), 'id&profile=other', 'notes')
    assert parse_qs(result.split('?', 1)[1]) == {
        'meeting': ['id&profile=other'], 'tab': ['notes'], 'profile': ['a&tab=actions']}
