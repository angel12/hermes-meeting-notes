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
        for row in rows:
            ident = row['id']
            try:
                if not re.fullmatch(r'[a-f0-9]{32}', ident):
                    raise ValueError('Invalid upload identity')
                root = upload_root(svc, db, ident)
                checked_file(svc, root/'uploads'/f'{ident}.0')
                paths = [root/'audio'/f'{ident}.upload']
                # Include atomic-write remnants even if the chunk DB commit was lost.
                pattern = re.compile(re.escape(ident)+r'\.\d+(?:\.[a-f0-9]{32}\.tmp)?')
                paths.extend(p for p in (root/'uploads').glob(ident+'.*') if pattern.fullmatch(p.name))
                paths = [checked_file(svc, p) for p in paths]
                for path in paths:
                    path.unlink(missing_ok=True)
                db.execute('DELETE FROM chunks WHERE upload=?', (ident,))
                db.execute('DELETE FROM upload_locations WHERE upload=?', (ident,))
                db.execute('DELETE FROM upload_cleanup_retry WHERE upload=?', (ident,))
                db.execute('DELETE FROM uploads WHERE id=? AND meeting IS NULL', (ident,))
                removed += 1
            except (OSError, ValueError):
                # Keep the durable references for an unavailable/foreign disk.
                # Don't hammer that disk or starve the next batch on every idle tick.
                db.execute('INSERT OR REPLACE INTO upload_cleanup_retry VALUES(?,?)', (ident, now+3600))
    return removed
