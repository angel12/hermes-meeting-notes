"""Meeting-scoped notes access. Host identity is never supplied by a model."""
from pathlib import Path
import sqlite3


def catalog():
    from hermes_constants import get_default_hermes_root, named_profile_is_live, PROFILE_ID_RE
    # Resolve afresh on each request; never retain another profile's context.
    # These public path/liveness helpers cover custom homes and deleted profiles.
    # No profile configuration, credentials or private CLI implementation is read.
    root = Path(get_default_hermes_root()).resolve()
    available = {'default':root}
    directory = root/'profiles'
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if (path.name != 'default' and PROFILE_ID_RE.fullmatch(path.name)
                    and path.is_dir() and not path.is_symlink() and named_profile_is_live(path)):
                available[path.name] = path.resolve()
    return available


def current_profile(available=None):
    from hermes_constants import get_hermes_home
    from agent.secret_scope import current_secret_scope_home, is_multiplex_active
    available = catalog() if available is None else available
    home = Path(get_hermes_home()).resolve()
    bound = current_secret_scope_home()
    if is_multiplex_active() and not bound:
        raise ValueError('Hermes did not bind a profile to this request')
    if bound and Path(bound).resolve() != home:
        raise ValueError('Hermes profile and request scope do not match')
    matches = [name for name,path in available.items() if path == home]
    if len(matches) != 1:
        raise ValueError('The current Hermes profile could not be identified unambiguously')
    return matches[0]


def options():
    # Kept for result-link identity checks and older clients, not profile selection.
    actor = current_profile()
    return {'current': actor, 'profiles': [actor]}


def analysis_service(meeting, source_profile=None):
    from .service import Service
    available = catalog()
    actor = current_profile(available)
    source = actor if source_profile is None else source_profile
    if source != actor:
        raise ValueError('Open this meeting in its owning Hermes profile; cross-profile analysis is no longer supported')
    root = available[source]/'plugin-data'/'meeting_notes'
    if not (root/'data.sqlite').is_file():
        raise ValueError('The importing profile has no Meeting Notes data')
    if root.resolve() != root:
        raise ValueError('Meeting storage must remain inside its owning profile')
    # An unassigned profile must not initialize/migrate another profile's database.
    with sqlite3.connect((root/'data.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        if not db.execute('SELECT 1 FROM meetings WHERE id=?',(meeting,)).fetchone():
            raise ValueError('Meeting not found in the importing profile')
    # Historical meeting_profiles rows no longer grant another profile access.
    # Never redirect paths from a model, change HERMES_HOME, or read credentials.
    svc = Service(root)
    svc.owner_profile = source
    svc.analysis_actor = actor
    svc.analysis_meeting = meeting
    with svc.db() as db:
        svc.check_analysis_profile(db,meeting)
    return svc


def handoff_service(meeting, handoff_id, source_profile=None):
    """A frozen handoff grant permits its original actor to report late receipts.

    This does not grant access to the current transcript after profile revocation.
    Begin requires the owning profile; read returns only the frozen grant.
    Legacy cross-profile handoffs can only read/report an already-started outcome.
    """
    from .service import Service
    available = catalog()
    actor = current_profile(available)
    source = actor if source_profile is None else source_profile
    if not isinstance(source, str) or source not in available:
        raise ValueError('The importing Hermes profile is unavailable')
    root = available[source]/'plugin-data'/'meeting_notes'
    if root.resolve() != root or not (root/'data.sqlite').is_file():
        raise ValueError('Meeting storage is unavailable')
    with sqlite3.connect((root/'data.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_handoffs'").fetchone():
            raise ValueError('Handoff not found')
        row = db.execute('SELECT profile FROM action_handoffs WHERE id=? AND meeting=?', (handoff_id, meeting)).fetchone()
        if not row or row[0] != actor:
            raise ValueError('This handoff is not assigned to the current Hermes profile')
    svc = Service(root)
    svc.owner_profile = source
    svc.analysis_actor = actor
    svc.analysis_meeting = meeting
    svc.handoff_actor = actor
    svc.handoff_id = handoff_id
    svc.handoff_meeting = meeting
    return svc
