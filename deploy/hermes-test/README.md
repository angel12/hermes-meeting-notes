# Isolated remote Webapp test host

Deployed at `spencer@10.0.1.72:~/hermes-test`. The Compose project is `hermes-meeting-notes-test`; all mutable data and the installed plugin copy live under that directory. Existing services and the user's other Hermes homes are untouched.

The remote `.env` pins the official image:

```
HERMES_TEST_IMAGE=nousresearch/hermes-agent@sha256:adb7890916de63245523492be8f68f9d2095fecbec4e2a6dab7960381f0fc002
```

That artifact reports Hermes v0.23.0, upstream `2bd378ed`. The `0.22.6` Docker tag was not published. The plugin floor remains >=0.22.6; this deployment does not prove the minimum-version matrix.

## Access

```sh
ssh -N -L 127.0.0.1:19120:127.0.0.1:19120 spencer@10.0.1.72
```

Open http://127.0.0.1:19120 and log in as `spencer` with the password supplied through the private card. The stack is bound to remote loopback, not published on the LAN. The SSH tunnel also provides a secure browser context for Web Crypto chunk hashing.

Credentials are in `~/hermes-test/secrets/runtime.env` (0600) and are not in this repository. Compose uses raw env-file parsing. The ASR key is passed to Hermes and resolved in the plugin with `get_secret`. Hermes generates its own per-process login signing key, so log in again after restarting the stack.

## Operations

```sh
ssh spencer@10.0.1.72 'cd ~/hermes-test && docker compose ps'
ssh spencer@10.0.1.72 'cd ~/hermes-test && docker compose logs --tail=60 webapp'
ssh spencer@10.0.1.72 'cd ~/hermes-test && docker compose restart webapp'
ssh spencer@10.0.1.72 'cd ~/hermes-test && docker compose down'
```

`down` preserves the bind-mounted data. A plugin Python change needs the test host restarted; refresh/reconcile the desktop copy for JS changes. Do not mount the Docker socket, production Hermes homes, or private recordings into this test container.

The official image currently warns that its venv is out of sync with `uv.lock`; the runtime starts and required plugin dependencies are present. The test image now layers a Webapp-only idle-callback compatibility script over the pinned upstream image; core Python and compiled application bundles remain unchanged. The default chat model is `glm-5.3` through the custom OpenAI-compatible LiteLLM endpoint `http://10.0.1.72:4000/v1` using `chat_completions`. `model.key_env` resolves `LITELLM_API_KEY` from the private Compose credentials file. ASR remains independent.

Run the browser test from the project with Playwright installed and a private password file. Never put the password itself in command arguments:

```sh
uv run --with playwright playwright install chromium --only-shell
uv run --with playwright python scripts/webapp_smoke.py --password-file /private/path/to/password
```

The optional audio test uses the actual file input, closes the tab after finalize, waits for the backend job, reopens the meeting, plays a WAV blob and renames/undoes a speaker. Use synthetic audio only. It preselects the persisted “choose provider later” and plugin-enabled preferences for this fixture; the interactive first-run and plugin-toggle flow are not covered. The test also works after a Compose restart and confirms reuse of the same audio. Current synthetic fixture state: one meeting, one transcription attempt.

## Browser compatibility patch

The upstream v0.23.0 Capabilities catalog prefetch calls `requestIdleCallback` unconditionally. Browsers without this API fail to render the workspace. The derived image `hermes-meeting-notes-test:idle-compat-v1` inserts a same-origin compatibility script before the renderer module. It provides cancellable timer callbacks only when the native API pair is unavailable. It does not simulate actual browser idle time or relax CSP.

Rebuild/redeploy from this directory with `docker compose up -d --build webapp`. The build context allowlist excludes all credentials and persisted data. To remove the workaround after an upstream fix, restore the Compose service's image to the pinned `HERMES_TEST_IMAGE`, remove its build block, and recreate it. Persistent data is unchanged.

Validated by reproducing the original Capabilities crash with both native idle APIs removed, then remounting Capabilities with the served fallback: Skills and Plugins navigation rendered without the workspace error. This simulates the missing-API condition; it is not a native Safari test. Refresh the whole page to load the fix (the error boundary's Retry alone does not reload the renderer entry).


## Optional separate audio volume

The storage setting is a path inside the container. To use a separate host directory, bind-mount it in Compose (for example `./audio:/opt/audio` beside the existing data volume), make it writable by UID/GID 1000, recreate the test service after checking for active work, then enter `/opt/audio` in Audio storage. The plugin creates its own profile-specific managed child directory. Merely saving that setting does not move existing recordings; use their reviewed storage action. Preserve both the profile data volume and audio volume in backups. Do not point the plugin at a browser-local path or mount a user's unrelated recordings wholesale.
