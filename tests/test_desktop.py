"""Shared Desktop/Webapp UI checks.

Syntax uses `node --input-type=module --check`; file-mode node --check can skip
this input. Host-dependent checks use the installed Hermes checkout
(HERMES_AGENT_SRC, default ~/.hermes/hermes-agent) and skip when it is absent.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / 'desktop/plugin.js'
HERMES = Path(os.environ.get('HERMES_AGENT_SRC', Path.home() / '.hermes/hermes-agent'))
DESKTOP = HERMES / 'apps/desktop/src'


def node():
    found = shutil.which('node')
    if not found:
        pytest.skip('Node is not installed')
    return found


def test_desktop_esm_syntax():
    result = subprocess.run([node(), '--input-type=module', '--check'], input=PLUGIN.read_text(),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_imports_are_limited_and_exported_by_host_sdk():
    source = PLUGIN.read_text()
    specifiers = re.findall(r"^import .* from '([^']+)'", source, re.M)
    assert sorted(specifiers) == ['@hermes/plugin-sdk', 'react']
    assert 'electron' not in source.lower() and 'ctx.files' not in source
    sdk = DESKTOP / 'sdk/index.ts'
    if not sdk.exists():
        pytest.skip('Installed Hermes desktop source not found')
    names = re.search(r"import \{([^}]*)\} from '@hermes/plugin-sdk'", source).group(1)
    index = sdk.read_text()
    for name in (n.strip() for n in names.split(',')):
        assert re.search(rf"\b{name}\b", index), f'{name} is not exported by the host SDK'


def test_theme_variables_exist_in_host():
    styles = DESKTOP / 'styles.css'
    if not styles.exists():
        pytest.skip('Installed Hermes desktop styles not found')
    defined = set(re.findall(r'(--ui-[a-z-]+)\s*:', styles.read_text()))
    used = set(re.findall(r'var\((--ui-[a-z-]+)', PLUGIN.read_text()))
    assert used and used <= defined, sorted(used - defined)


def test_ui_interactions_with_synthetic_backend():
    modules = HERMES / 'node_modules'
    if not all((modules / m).exists() for m in ('react', 'react-dom', 'jsdom', '@testing-library/react')):
        pytest.skip('Installed Hermes test dependencies not found')
    result = subprocess.run([node(), str(ROOT / 'tests/ui/plugin_ui.mjs'), str(HERMES), str(PLUGIN)],
                            text=True, capture_output=True, timeout=180,
                            env={**os.environ, 'FORCE_COLOR': '0', 'DEBUG_PRINT_LIMIT': '2000'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('ok - ') >= 8, result.stdout
