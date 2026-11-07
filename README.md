<img src="assets/icon.svg" width="64" height="64" alt="">

# Hermes Meeting Notes

Early implementation of an import → diarized transcript → corrected speakers → sourced notes → action handoff workflow. One agent package and one `desktop/plugin.js` target native Desktop and the new Webapp. The legacy dashboard has no UI; its manifest mounts backend routes only.

## Implemented

- Reviewed meeting deletion with combined-recording dependency checks, durable managed-file cleanup and preserved external items.
- Durable SQLite metadata, 8 MiB hashed multipart chunks, duplicate-chunk conflict detection, idempotent finalize and cleanup of expired unfinished uploads.
- Separate saved Custom (NeMo-Speech.cpp) and optional xAI OAuth transcription connections, each with its own model, timeout and chunk limit. xAI requires the experimental host API extension described below.
- Configurable host audio directory, explicit migration of existing recordings, lossless FLAC playback compression, and opt-in smaller Opus recording archives with verified replacement and durable recovery.
- Configurable maximum audio duration per ASR request (60 minutes by default). Longer recordings are split into bounded requests and assembled into one meeting with full-recording timestamps. Successful chunks are retained across explicit retries.
- Backend-only authenticated transcription, local attempts, heartbeat/lease fencing, immutable response files saved before parsing, and explicit retry after unknown outcomes.
- Deterministic timestamped segments, transcript-scoped IDs, whole-label merges, participant renames, segment speaker/text corrections and append-only undo with revision checks.
- Prototype shared UI with explicit Upload and transcribe, persistent selection/error feedback, chunk progress, live transcription status, corrections and short WAV playback windows.
- Sourced notes, decisions, open questions, commitments and suggestions through the bundled `meeting_notes:analyze` skill in the current Hermes session. Exact excerpts, correction-aware speaker owners, immutable analysis history and revision checks.
- Action Items beside Notes: sourced tasks, follow-up meetings and training proposals, with user-selected handoffs to the Hermes profile’s calendar or to-do tools and durable reported outcomes.
- Nine registered agent tools: `meeting_list`, `meeting_get`, `meeting_correct`, `meeting_retry_transcription`, `meeting_transcript`, `meeting_save_notes`, `meeting_actions`, `meeting_save_actions`, `meeting_action_callback`. Three bundled skills: `analyze`, `action_items`, `handoff`.
- Combine 2–20 completed recordings in a chosen order into a new meeting without retranscription. Corrected transcript snapshots retain recording/segment provenance, independent speaker labels and audio playback across file boundaries. Original meetings remain unchanged.
- A persistent Hermes analysis-profile selector per meeting. The selected profile may read that meeting's transcript and save sourced notes back to its importing profile; saved analyses record the actual profile. Profile changes invalidate in-flight notes.

**Not release-ready.** Authenticated Webapp passes the synthetic upload/transcription/playback smoke test on the remote Compose host; native Desktop source-build acceptance now covers installation/toggle, upload, transcription, playback, speaker rename, result routes, interruption recovery and reviewed deletion with synthetic data. Packaged-release and additional-profile acceptance remain open. Shared UI polish, combining recordings and profile selection are deployed to the test Webapp and need visual/user acceptance. Action handoffs are implemented and covered by synthetic tests; live calendar/to-do acceptance remains open and requires the selected profile’s connected tools. Expired-upload cleanup and explicit local response recovery passed installed-runtime synthetic acceptance. No external task system is simulated, and no direct Todoist integration is planned.

## Install from GitHub

Install into the Hermes profile that will import recordings:

```sh
hermes plugins install https://github.com/angel12/hermes-meeting-notes --yes-deps --enable
```

For a named profile, put `-p PROFILE` before `plugins`. Select that same profile in Desktop, then enable Meeting Notes under **Capabilities → Plugins**. The shared UI can be visible while a different profile is selected; a `404: Plugin not found` means the selected backend has not loaded the plugin. The Webapp needs the plugin installed on its serving host. If Meeting Notes is already installed, use `hermes plugins update meeting_notes` in the intended profile instead of installing it again. Reload the backend after Python plugin changes.

Install `ffmpeg` and `ffprobe` on that backend machine, then configure a reachable Custom/NeMo-Speech.cpp connection and its Hermes secret as described below.

**Optional xAI needs an additional host setup step.** Installing or updating this GitHub plugin does not install the experimental OAuth multipart extension into Hermes. The extension files are included in the repository, but the plugin installer does not apply them. An existing xAI sign-in alone is insufficient: **Use this connection** stays disabled until the backend exposes the required API. See [Optional xAI transcription](#optional-xai-transcription) and the [explicit host-extension setup](deploy/hermes-test/README.md#source-host-setup). Custom works without this extension. A standard plugin-only install can offer xAI once equivalent support ships in Hermes itself; this repository does not silently modify the host during installation or plugin loading.

## Development

Python 3.11+ and host `ffmpeg`/`ffprobe` are required. Optional archives require FLAC and libopus encoder support. No binaries are bundled.

```sh
uv sync --group dev
uv run pytest -q
node --input-type=module --check < desktop/plugin.js
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
```

Install this package using Hermes' supported plugin installation flow, enable the Python plugin and opt into its Desktop half. The Webapp must load the desktop plugin on its serving host. See [Hermes' unified package documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk#one-package-both-sdks). The unified package was loaded by the remote Webapp host; browser tests preselect its persisted enabled preference. Installation through the supported CLI and the interactive Desktop toggle passed in an isolated native source-build profile; packaged distribution remains untested.

Configure `MEETING_NOTES_ASR_KEY` through Hermes secret configuration. The plugin reads it exclusively through `agent.secret_scope.get_secret`; never put it in source, the settings API, or the browser. Endpoint, model and timeout are editable in the prototype UI. The default endpoint is the development LAN server; other users must configure their own reachable NeMo-Speech.cpp server. Plain HTTP is intended only for a trusted LAN; use TLS otherwise.

### Optional xAI transcription

In **Transcription settings**, choose **Connection to configure → xAI**. **Save settings** saves that connection without selecting it. **Use this connection** selects it for new transcription plans. Choose Custom and use it again to restore the saved NeMo endpoint, model, timeout and duration limit. Editing/saving either connection preserves the other. Unsaved form edits must be saved before leaving the form. Custom remains the default, and requires no xAI account.

Selecting xAI checks the running backend's host API. When support is missing, the page walks through locating the backend, backing it up, applying the compatible source patch (or rebuilding Docker), restarting and signing in. **Check host support again** rechecks after setup without discarding unsaved fields, changing the active connection or sending audio. A failed check keeps activation disabled. Host support does not verify login or STT entitlement.

xAI uses the existing Hermes OAuth sign-in on the **importing backend profile**, not the browser's machine or the meeting's analysis profile. Sign in through Hermes's provider UI first. The plugin declares optional use of that sign-in in its manifest, but never reads tokens or auth files, refreshes credentials itself, or sends credentials to the browser. There is no API-key or alternate-provider fallback. Audio goes to `https://api.x.ai/v1/stt`, with `diarize=true` and the selected model (default `grok-voice-transcribe-2.0`). Raw responses are retained before word timestamps and speaker labels are validated. A short synthetic two-voice recording passed on the extended test host using its existing OAuth sign-in, returning two speaker labels and valid timestamps. This verifies that account’s access at test time; it does not establish included usage or entitlement for other subscriptions.

**Host dependency:** stock Hermes inspected for this implementation lacks xAI and multipart support in its public `credentialed_provider_request` API. This repository provides a pinned **test-host API extension**, applied at image build time in [deploy/hermes-test](deploy/hermes-test/README.md). It is not an upstream release or a plugin runtime override. On unextended hosts, xAI settings can be saved but activation is unavailable; Custom continues to work. The extension keeps token resolution and HTTP authentication in Hermes. Distribution of xAI support to ordinary installs requires an upstream equivalent or an explicitly extended host.

Existing transcription plans and ordinary retries keep their saved provider/model/settings. Selecting a connection applies when a new plan is first prepared, including queued recordings without a plan. **Retry with current settings** is explicit; once any chunks complete, changing their adapter or model is refused. Missing login and HTTP/timeout failures never silently switch connections or automatically resend audio.

OpenAI/ChatGPT OAuth remains available for notes and action generation through normal Hermes chat. Diarized file transcription under that sign-in has not been verified and is not offered as a transcription connection.

## Lifecycle and current limits

Use a dedicated single-profile host for import, correction and transcription. Those operations still reject multiplexed/routed requests. Notes and action read/save tools permit a host-bound selected analysis profile to access its assigned meeting in the importing profile on the same machine. Handoff callbacks are scoped to the original selected profile and the particular user-created handoff. Workers run in a daemon thread in the API/plugin host; closing a tab does not itself cancel them, but stopping that host interrupts them. Desktop backend lifecycle depends on the host: the tested source build kept its local serve process alive after the app closed, while other hosts may stop it. Do not assume work survives quitting Desktop. On restart, explicitly start queued work; expired attempts become `interrupted_unknown`, and only Retry sends another request. GET/status never starts work.

Concurrency is one active transcription per profile database. **Multiple independent host/profile databases pointing at the same endpoint are not yet coordinated; run only one for this early build.** A future endpoint-wide coordination mechanism is required before claiming unrestricted concurrency safety.

The UI caps uploads at 512 MiB (a local resource cap, not a verified model limit). Uploads currently expire logically after 24 hours; abandoned uploads still require cleanup work. Finalized uploads release their duplicate chunks after the recording commits, with recovery cleanup for interrupted finalizations. Finalize verifies every chunk again and returns the full file hash. ffmpeg creates a separate mono PCM16 16 kHz derivative. The uploaded original is retained unless you explicitly opt into a smaller recording archive. Lossless playback compression preserves the exact canonical samples. Audio playback returns at most 30 seconds as base64 PCM WAV in authenticated JSON. Native OAuth-remote uploads are not supported by the inspected host bridge; local native backend and authenticated hosted Webapp are the intended test targets.

Raw word confidence values from the verified server are placeholders and are not shown as confidence. Speaker labels are anonymous participants, not identities. No claim is made that audio after the final returned word is silence. Transcript completeness and the serving host's practical limits still require review.

Transcripts, corrections and database metadata remain in the profile’s `plugin-data/meeting_notes` directory. Audio defaults there but can be stored in a separate host directory. ASR requests send audio to the configured endpoint. The notes skill reads the corrected transcript into the Hermes conversation, where it is processed by that session’s selected model and retained in normal chat history. There is no telemetry. The plugin itself makes no calendar/task API calls; a user-requested handoff is executed by Hermes using that profile’s existing tools, and its result is stored back in Meeting Notes. Test fixtures are synthetic; private recordings and responses must remain outside the repository and public demo assets.

See [feasibility evidence and remaining gates](docs/feasibility.md).

## Generate notes

Review the speakers, then choose **Start summarization session** in the recording’s **Notes** tab. Send the prepared request in the new Hermes chat:

> Use the meeting_notes:analyze skill to generate and save sourced notes for meeting MEETING_ID.

Hermes loads the bundled skill, reads the corrected transcript with `meeting_transcript`, writes the notes itself in the conversation, and calls `meeting_save_notes`. The plugin makes no nested model call and has no analysis provider, retry loop, or inference timeout. The selected chat model handles the analysis as part of its normal turn. After saving notes, the skill extracts action proposals into Action Items separately; a proposal failure does not discard saved notes.

Transcript pages preserve all text and short source IDs. The skill reads every page before analysis. Saving checks the transcript version, correction revision and prior saved analysis, validates the structured items and exact source quotes, and writes an immutable analysis. Dates stay unconfirmed, missing owners remain unresolved, and no external tasks are created. Citation validation establishes source integrity, not semantic correctness; review the claims.

Rejected notes return up to eight diagnostic locations (`failures`, with zero-based item/citation paths, error codes and known short source IDs), plus `more_failures` for remaining errors. Diagnostics never echo submitted quotes or transcript text. Rejection writes nothing: repair sources through `meeting_transcript` and resubmit the complete notes set. Each successful save becomes the current analysis rather than appending items; its response includes the new `basis`, `items_saved`, and `items_by_kind`. The skill prohibits diagnostic partial saves, direct database/raw-file inspection, and self-editing the plugin during analysis. Pages vary in length; follow the returned `next_offset` sequentially.

The shared UI refreshes to show saved notes, quoted evidence and source playback. Correcting a transcript marks existing notes for review. Speaker-based owners update immediately; explicitly named owners stay unchanged. Regeneration retains history, preserves IDs only for exact item matches, and marks unmatched previous items for review.

## Recorders that split meetings into files

Import and transcribe each recording, then choose **Combine meetings**. Add completed recordings in playback order, adjust the order if needed, and confirm the title, meeting date and timezone. Creating the combined meeting copies the current corrected transcripts and leaves the original recordings, transcripts, corrections and notes intact. Later source edits do not silently change the combined snapshot. Generate new notes from the combined meeting; previous notes and item IDs are not merged.

Speaker identities remain separate across files, even when their displayed names match. Use the speakers panel to match them before analysis. Excerpts show both the combined time and original recording time; playback reads the original prepared audio and can cross a recording boundary. Recordings play back to back: no wall-clock gap, overlap removal or missing speech is inferred. Already combined meetings cannot be nested; select their original recordings instead. Duplicate submission of the same request returns the same meeting, and stale source revisions are rejected.

## Model audio-duration limits

In **Transcription settings**, set **Maximum audio per request (minutes)** to your serving model's limit or lower. It defaults to 60 minutes: NVIDIA documents [Nemotron 3.5 Transcribe's maximum supported audio length as one hour](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B). The separate request timeout controls how long a request can wait; it does not change the audio duration.

The host prepares one full playback WAV and persists an exact frame-based request plan. Request sizes are balanced within the limit to avoid tiny final chunks. Longer recordings are cut at time boundaries without gaps, overlap or edits to the original. Requests run sequentially. Each attempt is persisted before submission, and each raw response is saved before parsing. Only a complete set of successful responses publishes the meeting transcript. Timestamps and citation playback refer to the original full recording. Speaker labels remain separate per chunk until you match them; review speech near cut points because a cut can divide a word or sentence.

Progress shows completed chunks. A timeout, interrupted lease or failed chunk stops the job; **Retry transcription** resends only unfinished work using the saved plan. Completed responses are reused. If only final assembly failed, Retry assembles the saved results without any model request. For an unusable endpoint, timeout or duration limit, change Transcription settings and choose **Retry with current settings** on the stopped meeting. This replans unfinished audio while preserving completed chunks and prior attempt records. The model must stay the same if any chunk has completed. Changing settings alone never resubmits or replans a job.

**Recover saved response** is a separate, local-only action on stopped jobs with a saved HTTP 200 response and integrity record. It verifies the original bytes and parses them again with the installed adapter, without loading credentials, decoding audio or contacting the provider. If a prepared playback WAV exists, its header must match the saved plan before recovery proceeds. A missing WAV does not block transcript recovery: timestamps follow the saved plan, but playback requires restoring that audio. Copied/moved profiles with foreign raw-file paths still fail closed until their paths are restored. This is useful after fixing an adapter/parser problem; it cannot repair a malformed provider response. Recovered chunks are retained, but any remaining unsent audio stays stopped until you explicitly choose **Retry transcription**. Recovery intent survives a host restart. Responses from older releases without a recorded HTTP status and hash are not offered for recovery; error responses are never treated as successful transcripts. Raw files remain unchanged. Before rolling back to a version without this recovery mode, stop the host and ensure no local recovery is queued; older workers do not understand the local-only marker.

Unfinished uploads expire 24 hours after they start. The next valid upload request or an idle running worker removes their staging chunks and temporary assembled upload files, up to eight uploads per pass. Finalized recordings and raw responses are excluded. Cleanup pauses during transcription/storage processing and retains metadata when storage is unavailable or unsafe, retrying that location no more than hourly. No background cleanup runs while the host is stopped, and status reads never start it.

Chunks returning no words receive a visible audio-review warning, never an assertion of silence. Recognizable abandoned temporary WAVs older than a day are cleaned up during worker recovery; active attempts and original/full playback files are protected. If a missing playback WAV is regenerated with a different frame count, work stops with a specific error until the original prepared WAV is restored; it does not silently reuse mismatched timestamps. The existing 512 MiB upload cap still applies.

## Choose the Hermes analysis profile

Each meeting defaults to its importing profile. **Analysis profile** saves a different existing Hermes profile on the same host. Selecting it explicitly grants that profile access to this meeting’s transcript, notes and action items, and permission to save sourced notes and action proposals back. Audio, raw responses, corrections and notes stay in the importing profile's database; no profile credentials are read or copied. The analysis profile must have Meeting Notes enabled and its own working model credentials.

Use **Start summarization session** in the **Notes** tab to open a new chat in that profile with the notes request filled in, then send it. The button remains available for regenerating existing notes. Older hosts without composer support, or hosts where filling the draft fails, try to copy the request for you to paste; **Copy request** is also available as a manual fallback. The request includes `source_profile`, which the skill passes on every transcript read and notes save. A new chat uses the profile's normal configuration; any model override you make in chat remains your choice. Existing conversations are not interrupted and no model call runs just from changing the selector. Return to the importing profile's Meeting Notes view to review saved notes.

The plugin verifies the actual host-bound profile on each read and again inside the notes-save transaction. Changing the selected profile invalidates old in-flight notes, even if you switch away and back. Earlier analyses retain their original profile attribution. Unavailable profiles are not silently replaced. The open-chat action uses the importing connection's exact route; ambiguous or unsupported routes produce an error. A single-profile Webapp deployment cannot open another profile through that server: use a Hermes host that serves the selected profile, with access to the same profile storage. Cross-machine meeting transfer is not implemented.

## Review and hand off action items

Open **Action Items** next to **Notes**. New notes runs extract proposals automatically through the bundled skills; for an existing meeting, copy **Find action items with Hermes** into the selected profile's chat. Hermes reads the corrected transcript and saves sourced tasks, follow-up meetings and training proposals. This does not create external tasks or events. Collective wording such as “we need to” leaves the individual owner unresolved; exact quotes alone do not prove an action was accepted.

For a proposal you want to act on:

1. Choose **Hand off to Hermes**, select **My calendar** or **My to-do list**, and add confirmed details or instructions.
2. **Prepare handoff** saves the request. Use **Start Hermes session**, then review and send the prepared request in that profile’s chat (or use Copy request). The profile needs the appropriate connected tools; the plugin has no provider-specific adapter or credentials.
3. Hermes claims that handoff once, checks the destination and missing details, performs the authorized action through its normal tools and approvals, then calls `meeting_action_callback` with the outcome. The tab refreshes to show what was done, the provider, external ID and any returned link. “Handoff completed” means a task was added or event scheduled, not that the underlying work was finished.

If details or access are missing, Hermes reports **Needs details or access** before making an external change. Add the requested information and press **Continue with these details**, then send the updated handoff request to Hermes. Configure credentials privately through Hermes, never in the instructions box. Requests that have not started, or that reported missing details before any external write, can be cancelled. The claiming chat receives an attempt token; another chat cannot declare that attempt failed or safe to retry while its write may still be running. An interrupted or timed-out operation stays in progress or uncertain until Hermes looks up the actual outcome; reopening its request does not authorize a duplicate write.

Results are **Hermes-reported**, not independently verified by Meeting Notes. A completed report requires a real provider and task/event ID. The plugin cannot enforce how a model uses other tools, and a receipt does not itself create anything. The handoff skill forbids blind retries, invented confirmations, silent invitations and unrequested external updates/deletes.

Each handoff freezes its reviewed evidence, destination, instructions and profile. Changes to the transcript, owner or profile flag it for manual review and prevent an unstarted stale handoff from executing. An already-started handoff may still report its actual outcome from its original profile, even after the meeting selects another profile; that limited grant exposes only the frozen handoff, not the newly edited transcript. No credentials cross profiles. Regeneration preserves IDs only for unambiguous exact matches; unmatched handoffs remain under **Earlier action handoffs**, with their original proposals and outcome history, rather than being silently reassigned. Review earlier outcomes before handing off a revised proposal that could duplicate existing work.


## Choose audio storage and compression

In **Transcription settings → Audio storage**, enter an existing absolute directory on the **Hermes backend machine**, or leave it blank to use profile storage. The plugin creates a private, profile-specific managed subfolder. A Webapp cannot use a folder on the browser's computer. Docker users must bind-mount the intended directory and enter its container path. New uploads bind to the saved location when they start; changing settings does not move older recordings or in-progress uploads. Unavailable managed folders fail closed instead of silently recreating a missing disk’s location. Saving settings again also checks the recorded filesystem device. A cloned or moved profile database receives a fresh namespace and quarantines its old audio locations; it cannot overwrite or clean up the source profile’s files. Existing references require an explicit restore, while new recordings can use newly configured storage.

Choose the storage policy:

- **Keep uploaded original** (default): retains the uploaded bytes and their original SHA-256 identity. Optionally compress the prepared playback copy as lossless FLAC. Its decoded samples and frame count are verified against the WAV before replacement.
- **Smaller speech archive** (explicit opt-in): replaces the plugin's uploaded copy with mono Opus at a 48 kb/s variable-bitrate target after transcription completes. This is lossy, discards stereo detail and original metadata, and is not reversible. The backend requires and records an explicit replacement acknowledgement. Sources with multiple audio tracks are refused for Opus replacement; keep their uploaded originals instead. A separate lossless FLAC of the canonical mono 16 kHz playback remains for accurate citation playback and combined-recording boundaries. The original source file on your recorder/computer is never changed. Keep it separately if needed. Already archived Opus audio is copied on later moves rather than re-encoded.

The new policy applies after future transcriptions finish. If it changes before an automatic archive is published, that automatic job is cancelled and the current recording is kept. For an existing completed recording, open **Audio storage → Review move / compression**, inspect the saved destination and policy, then **Apply reviewed storage settings**. That explicit action also migrates existing audio to the configured directory. A concurrent policy change is rejected rather than applying an unseen archive choice. Combined meetings share their source recordings; manage storage on those originals. The panel shows formats, byte sizes, job state and the completed size difference. Compression savings depend on the recording and can be negative for already compressed input. Switching a compacted recording back to “keep uploaded original” cannot reconstruct the deleted original; leave its archive mode selected when moving it again.

Storage work runs durably on the host and is serialized with transcription. It copies/encodes to new files, verifies original copies by hash, FLAC by decoded PCM hash and frame count, and Opus by full decode and duration. Only then does a fenced SQLite transaction switch the active file pointers. Old managed files are removed afterward; interrupted cleanup is retried without changing the authoritative audio. Extra free space for both old and replacement files is required until cleanup finishes. An interrupted conversion keeps the current recording and requires an explicit retry; browser close does not cancel it, but host shutdown can interrupt it. Use **Start queued work** after restart. No status request starts a conversion.

Archive verification does not assess perceptual quality or validate transcript accuracy. The plugin never transcribes lossy archive audio during this operation and never changes existing transcripts, corrections, notes or citations. Stopped transcription jobs retain their original/prepared audio and cannot be archived until transcription completes. Back up the profile database and every configured audio directory together; copying only the database does not back up audio stored elsewhere.

## Delete meetings and recordings

Open a meeting and choose **Delete meeting…**. The review shows saved notes/action-set/receipt counts and any combined meetings that depend on it. Type its title (surrounding spaces are ignored) and acknowledge permanent deletion. The importing profile UI removes the local meeting, transcripts, corrections, saved notes, action proposals, handoff receipts and its managed audio/raw-response files. There is no undo. Source files on your recorder/computer, Hermes chat history, backups and external calendar/tasks are not erased.

A combined meeting can be deleted without deleting its source recordings. To delete a recording referenced by combined meetings, delete those combinations first. Running transcription/storage work and running or uncertain handoffs block deletion. Queued jobs are removed with the meeting; deletion never starts ASR or sends external requests. Changes since the review require a fresh confirmation.

File cleanup is durable and checks profile ownership, references and file hashes. Unavailable/foreign storage or unsafe paths can prevent deletion before local data is removed. If cleanup is interrupted after removal, reconnect storage and use **Retry file cleanup** in settings; a running worker also retries pending cleanup. Changed files are kept rather than blindly removed. Reimporting the same audio does not let old cleanup remove its new copy. Deletion is not a secure erase of disks, backups or chat history.

Deletion pauses while any transcription/storage job on this profile host is active, so hashing cannot interrupt its lease. Changed files are retried only with the explicit cleanup button. After copying or moving a profile, legacy audio paths may belong to the previous profile; deletion fails closed until storage is restored rather than removing that other profile’s files.

### Links from analysis sessions

Successful notes and action-item saves return a `view_url`. In Desktop/Webapp sessions, the bundled skills include **Open sourced notes** and **Open Action Items** links in the session recap. With Meeting Notes enabled, clicking one opens that meeting and tab inside the same Desktop/Webapp host. Links show the current saved view, not a frozen analysis version. They contain no hostname or credentials and do not grant access. If the importing profile differs, the plugin asks you to switch to it on the original connection and reopen the link; it does not read another profile through the active connection. These are in-app links, not public share links or external `hermes://` URLs.

Meeting links wait while an upload or change is in progress. Unrelated app navigation leaves a mounted meeting workspace intact. A blocked profile link offers **Show Meeting Notes** to return to the current profile’s meetings. In messaging gateways, skills give navigation instructions instead of app-relative links.
