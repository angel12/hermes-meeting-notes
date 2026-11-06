"""Build-time, pinned host API extension. Never imported by the plugin."""
import hashlib
from pathlib import Path
import subprocess
import sys

EXPECTED = '60c537ec1d98e84c2bcf24cc2a8df20d1b1e96bba305d7f7e0970b002af15a9d'


def install(root, patch):
    source = root/'hermes_cli/plugin_provider_requests.py'
    if hashlib.sha256(source.read_bytes()).hexdigest() != EXPECTED:
        raise SystemExit('Host provider-request API changed; review the patch before rebuilding')
    subprocess.run(['git','-C',str(root),'apply','--check',str(patch.resolve())],check=True)
    subprocess.run(['git','-C',str(root),'apply',str(patch.resolve())],check=True)


if __name__ == '__main__':
    install(Path(sys.argv[1]) if len(sys.argv)>1 else Path('/opt/hermes'),
            Path(sys.argv[2]) if len(sys.argv)>2 else Path('/tmp/provider-requests.patch'))
