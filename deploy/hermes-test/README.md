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

The official image currently warns that its venv is out of sync with `uv.lock`; the runtime starts and required plugin dependencies are present. The derived image layers the Webapp idle-callback compatibility script and the explicit provider-request API extension described below over the pinned upstream image. Compiled application bundles remain unchanged. The default chat model is `glm-5.3` through the custom OpenAI-compatible LiteLLM endpoint `http://10.0.1.72:4000/v1` using `chat_completions`. `model.key_env` resolves `LITELLM_API_KEY` from the private Compose credentials file. ASR remains independent.

Run the browser test from the project with Playwright installed and a private password file. Never put the password itself in command arguments:

```sh
uv run --with playwright playwright install chromium --only-shell
uv run --with playwright python scripts/webapp_smoke.py --password-file /private/path/to/password
```

The optional audio test uses the actual file input, closes the tab after finalize, waits for the backend job, reopens the meeting, plays a WAV blob and renames/undoes a speaker. Use synthetic audio only. It preselects the persisted “choose provider later” and plugin-enabled preferences for this fixture; the interactive first-run and plugin-toggle flow are not covered. The test also works after a Compose restart and confirms reuse of the same audio. Current synthetic fixture state: one meeting, one transcription attempt.

## Browser compatibility patch

The upstream v0.23.0 Capabilities catalog prefetch calls `requestIdleCallback` unconditionally. Browsers without this API fail to render the workspace. The derived image inserts a same-origin compatibility script before the renderer module. It provides cancellable timer callbacks only when the native API pair is unavailable. It does not simulate actual browser idle time or relax CSP.

## Experimental OAuth multipart host API

The `oauth-stt-v1` image also applies `provider-requests.patch` to the host's `hermes_cli/plugin_provider_requests.py`. The build verifies the original module SHA-256 and checks the patch before applying it; an upstream change stops the build for review. This is a local host extension, not a claim of upstream support. No running plugin modifies Hermes code or reads its credential stores.

The patch adds the existing `xai-oauth` sign-in to core-made requests, multipart `data`/`files` arguments, and `provider_request_capabilities()` (a static, credential-free check). An installed plugin still needs `requires_auth: [xai-oauth]`. Hermes uses its own profile-aware credential resolver, rejects API-key fallback and foreign credential origins, attaches authorization itself and returns only response status/headers/body. For xAI, multipart options precede the file, redirects are disabled, proxy environment variables are ignored, and no upload is retried automatically. Authentication/transport exceptions exposed to the plugin contain fixed messages rather than credential-bearing request objects. Existing OpenAI callers retain their exception types, relogin instructions and environment proxy/TLS behavior. Known pre-submission refusals use a typed error, so the plugin records a setup failure with no send timestamp.

Sign in to xAI through Hermes on the test host's default profile only when testing this optional connection. The plugin's Custom connection works without any xAI sign-in. A successful OAuth login is not proof of STT entitlement. On November 6, the existing test-host OAuth sign-in successfully transcribed a short synthetic two-voice recording, returning two speaker labels and valid timestamps. Missing-login behavior also passed through the real installed plugin in a disposable profile with networking disabled. This does not establish included usage or access for other subscriptions. Both synthetic acceptance meetings were deleted afterward, with zero managed files awaiting cleanup.

Tests apply the patch to a temporary copy of the pinned module, use real core plugin admission with synthetic manifests/profiles, and mock only provider credentials and HTTP transport. They do not edit the installed Hermes source or use real auth stores. Rollback: restore the previous image and plugin together; leave the additive `transcription_connections` table intact. The legacy settings row continues to contain the saved Custom connection for older plugin versions. Back up plugin data and configured audio directories before deployment, and recreate only when transcription/storage/chat work is idle.

If you edit Custom settings while running a rolled-back older plugin, record those changes before upgrading again: the newer connections table takes precedence over the legacy row, so reapply those edits through the new settings UI. A repeated xAI 401/403 may require signing in again before Retry; the plugin deliberately does not refresh-and-resubmit a recording automatically. The initial xAI chunk setting is five minutes (a conservative test setting, not a claimed provider limit).

Rebuild/redeploy from this directory with `docker compose up -d --build webapp`. The build context allowlist excludes all credentials and persisted data. To remove the workaround after an upstream fix, restore the Compose service's image to the pinned `HERMES_TEST_IMAGE`, remove its build block, and recreate it. Persistent data is unchanged.

Validated by reproducing the original Capabilities crash with both native idle APIs removed, then remounting Capabilities with the served fallback: Skills and Plugins navigation rendered without the workspace error. This simulates the missing-API condition; it is not a native Safari test. Refresh the whole page to load the fix (the error boundary's Retry alone does not reload the renderer entry).


### Source-host setup

The GitHub plugin installation includes these extension files but does **not** run the installer. Applying it is a separate, experimental change to the Hermes source used by the backend. It affects every profile served by that source checkout. Packaged Desktop builds may use a different backend; patching an unrelated checkout will not enable their xAI connection.

For a source installation, identify that backend's checkout and wait until its chats, transcription and storage work are idle. Back up the profile database/audio together and preserve the original host module. From the Meeting Notes repository, replace the example path below with the actual Hermes source checkout:

```sh
HERMES_SOURCE=/absolute/path/to/hermes-agent
OAUTH_BACKUP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/meeting-notes-oauth-backup.XXXXXX")
cp "$HERMES_SOURCE/hermes_cli/plugin_provider_requests.py" "$OAUTH_BACKUP_DIR/plugin_provider_requests.py"
python3 deploy/hermes-test/install-provider-requests.py "$HERMES_SOURCE" deploy/hermes-test/provider-requests.patch
```

Keep the backup directory for rollback. The installer requires the exact reviewed module hash and a clean patch check; if it reports that the API changed, stop and review compatibility rather than bypassing the check. It also refuses an already-patched module. It does not read credentials or send audio.

Restart the backend process using that checkout, including any profile plugin-host processes, then reopen Meeting Notes. A page refresh alone does not reload Python, and closing Desktop can leave its backend running. The missing-extension notice should disappear. Sign in to xAI in the importing profile, save the connection, and select **Use this connection**. Availability of the button verifies host API support, not the account's STT entitlement.

For rollback, stop work on that backend, restore the backed-up module and restart the backend again. Custom remains available; do not retry xAI plans on an unextended host. Hermes updates can replace or conflict with the extension, so recheck compatibility after an update. Do not add this installer to plugin startup or installation hooks; an upstream host API is the path to ordinary plugin-only installation.

## Optional separate audio volume

The storage setting is a path inside the container. Compose includes `./audio:/opt/audio` beside the existing data volume. Make the host audio directory writable by UID/GID 1000, recreate the test service after checking for active work, then enter `/opt/audio` in Audio storage. The plugin creates its own profile-specific managed child directory. Merely saving that setting does not move existing recordings; use their reviewed storage action. Preserve both the profile data volume and audio volume in backups. Do not point the plugin at a browser-local path or mount a user's unrelated recordings wholesale.


Reviewed Action Items/storage code `4a3b50c` was deployed with the `/opt/audio` mount on 2026-11-06 UTC. The host authenticated API and synthetic codec/callback checks passed; existing meeting and file hashes were preserved. Storage still defaults to the profile and keeps uploaded originals. Enter `/opt/audio` only when you want new uploads there, then explicitly review/apply settings on any existing recording you choose to migrate.
