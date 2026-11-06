"""Build-time Webapp shim insertion. Fail closed if the upstream layout changes."""
from pathlib import Path

root = Path('/opt/hermes/apps/desktop/dist-webapp')
page = root / 'index.html'
html = page.read_text()
marker = '<script type="module"'
shim = '<script src="./assets/idle-callback-compat-v1.js"></script>\n    '
if html.count(marker) != 1 or 'idle-callback-compat' in html:
    raise SystemExit('Unexpected renderer entry layout; review compatibility patch')
page.write_text(html.replace(marker, shim + marker, 1))
