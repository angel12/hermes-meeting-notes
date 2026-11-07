"""Retire expired, unfinalized upload staging only; never original recordings."""
import re
import time
from .storage import upload_root
from .deletion import checked_file

TTL = 86400


def cleanup(svc):
    now = time.time()
    removed = 0
    with svc.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','in_flight') UNION ALL SELECT 1 FROM storage_jobs WHERE state='running'").fetchone():
            return 0
        rows = db.execute('''SELECT u.id FROM uploads u LEFT JOIN upload_cleanup_retry r ON r.upload=u.id
            WHERE u.meeting IS NULL AND u.created<? AND (r.next_check IS NULL OR r.next_check<=?)
            ORDER BY u.created LIMIT 8''', (now-TTL, now)).fetchall()
    # Expiry is irreversible and chunk/finalize reject it under the same lock.
    # Do mount I/O outside the write transaction so a stalled disk cannot hold
    # up job heartbeats. Multiple cleaners can safely unlink the same staging.
    for row in rows:
        ident = row['id']
        try:
            if not re.fullmatch(r'[a-f0-9]{32}', ident):
                raise ValueError('Invalid upload identity')
            with svc.db() as db:
                if not db.execute('SELECT 1 FROM uploads WHERE id=? AND meeting IS NULL AND created<?', (ident,now-TTL)).fetchone():
                    continue
                root = upload_root(svc, db, ident)
            checked_file(svc, root/'uploads'/f'{ident}.0')
            paths = [root/'audio'/f'{ident}.upload']
            # Include atomic-write remnants even if the chunk DB commit was lost.
            pattern = re.compile(re.escape(ident)+r'\.\d+(?:\.[a-f0-9]{32}\.tmp)?')
            paths.extend(p for p in (root/'uploads').glob(ident+'.*') if pattern.fullmatch(p.name))
            paths = [checked_file(svc, p) for p in paths]
            for path in paths:
                path.unlink(missing_ok=True)
            with svc.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM uploads WHERE id=? AND meeting IS NULL AND created<?', (ident,now-TTL)).fetchone():
                    continue
                db.execute('DELETE FROM chunks WHERE upload=?', (ident,))
                db.execute('DELETE FROM upload_locations WHERE upload=?', (ident,))
                db.execute('DELETE FROM upload_cleanup_retry WHERE upload=?', (ident,))
                db.execute('DELETE FROM uploads WHERE id=? AND meeting IS NULL', (ident,))
                removed += 1
        except (OSError, ValueError):
            # Keep references for unavailable/foreign disks without hammering
            # them or starving the next batch on every idle tick.
            with svc.db() as db:
                db.execute('INSERT OR REPLACE INTO upload_cleanup_retry SELECT id,? FROM uploads WHERE id=? AND meeting IS NULL', (now+3600,ident))
    return removed
