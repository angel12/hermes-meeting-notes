"""Exercise missing idle APIs and cancellation without weakening browser security."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_idle_compat_fallback_and_native_preservation():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is not installed')
    shim = (Path(__file__).parents[1] / 'deploy/hermes-test/idle-callback-compat.js').read_text()
    source = '''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const shim = SHIM;
(async () => {
  const sandbox = {setTimeout, clearTimeout};
  vm.runInNewContext(shim, sandbox);
  let ran = false;
  const handle = sandbox.requestIdleCallback(() => {ran = true;});
  sandbox.cancelIdleCallback(handle);
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(ran, false);
  let synchronous = true;
  await new Promise(resolve => {
    sandbox.requestIdleCallback(deadline => {
      assert.equal(synchronous, false);
      assert.equal(deadline.didTimeout, false);
      assert.equal(deadline.timeRemaining(), 0);
      resolve();
    });
    synchronous = false;
  });
  const request = () => 42, cancel = () => {};
  const native = {requestIdleCallback: request, cancelIdleCallback: cancel};
  vm.runInNewContext(shim, native);
  assert.equal(native.requestIdleCallback, request);
  assert.equal(native.cancelIdleCallback, cancel);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''.replace('SHIM', json.dumps(shim))
    result = subprocess.run([node, '--input-type=commonjs'], input=source,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
