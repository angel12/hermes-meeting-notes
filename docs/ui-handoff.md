# Shared UI handoff — 2026-11-05

`desktop/plugin.js` is the single UI for native Desktop and the Webapp. It imports only `Button`, `Badge` and the two area constants from `@hermes/plugin-sdk`, plus React. Styling is one `.mn`-scoped stylesheet injected in `register()` and removed via `ctx.onDispose` (the same pattern as the bundled Radio plugin). It uses only `--ui-*` theme variables that exist in the host's `styles.css`; the old `--ui-border` did not exist. Webapp CSP allows inline styles; native Desktop is not yet checked.

## Layout

- **Sidebar:** meeting list with date and non-complete state; New recording; Transcription settings; Start queued work (only while something is queued).
- **New recording:** title, `datetime-local` converted to ISO with the browser's offset (shown before upload), file picker, explicit Upload and transcribe. Submit reads the input's live FileList. Same preflight, 8 MiB SHA-256 chunks, 30 s timeouts and idempotent finalize. Navigation is locked during upload.
- **Meeting:** one-line header (title, transcript coverage, state) with date/zone, speaker, turn and note counts; status/retry callout; sticky player; then **Transcript** and **Notes** tabs. Layout borrows from halo-transcribe's editor:
  - **Transcript:** toolbar (search, speaker filter, Saving…, Undo last change, Speakers toggle at narrow widths). Each turn has a speaker chip (click → inline “Who said this?” picker), a ▶ timestamp, corrected badges, and text you double-click (or Edit text) to edit inline (Enter saves, Esc cancels).
  - **Speakers panel** docked to the right (a toggled drawer below ~860 px of pane width, via a container query): swatch, click-to-rename name, talk time, share and turn count, ▶ sample, Next (jumps to that speaker's next turn), Merge… with an inline confirmation stating how much talk moves. Below it, the change history with per-item Undo.
  - **Notes:** copyable `meeting_notes:analyze` skill request when there are no notes or they need review (otherwise under “Regenerate notes”), stale/unmatched warnings, version selector for history, grouped sections with jump links, owner/due rows marked Unconfirmed, quotes with saved-source playback and Show in transcript.
  - Not borrowed (no backend support): word-level seek/selection, redo and drag-to-merge. Full-meeting playback and a real waveform were added subsequently (see below).
- Polling is GET-only every 3 s, pauses while the tab is hidden, and discards responses that started before a mutation.

## Tests

`uv run pytest -q` includes `tests/test_desktop.py`: ESM syntax, an import allowlist checked against the host SDK exports, theme variables checked against the host stylesheet, and `tests/ui/plugin_ui.mjs`. That script renders the real plugin in jsdom with Testing Library from the installed Hermes checkout against a synthetic backend: no upload on file selection, chunk hashes and timeouts, merge/rename/undo/segment revisions, notes warnings, saved-source playback with URL revocation, history and keyboard tabs. These checks skip when the host checkout is absent.

## Not verified / API needs

- Not yet viewed in a real host. Visual review in the Webapp and native Desktop is still needed, including light/dark and narrow widths.
- `GET /meetings` has no participant count or duration, so the sidebar can't show them without fetching every meeting.
- No API to rename or delete a meeting, cancel an upload, or set note owner/date confirmation. The UI doesn't offer these.
- Transcription progress is not reported by the backend, so only the state is shown.

## Parent review

Reviewed the shared UI and reran all 37 pytest checks, including 11 synthetic interaction scenarios. Fixed stale meeting selection and audio responses after navigation or newer playback requests. Leaving the meeting or closing playback invalidates pending audio and revokes its blob URL. Navigation is disabled during corrections as well as uploads.

Merge confirmation now counts only turns without explicit speaker overrides, and merged-away labels that survive through an override cannot be used as a merge source/target. This matches backend correction precedence. The header says “Transcript to …” rather than presenting the last returned timestamp as recording length; speaking-time percentages are explicitly estimates from transcript segments. Filtered speaker navigation and rename drafts after undo were corrected, and historical note requests are invalidated when the meeting revision changes. Invalid or nonexistent local wall times are rejected before upload.

Updated scripts/webapp_smoke.py for the new navigation, upload, playback, rename and undo controls. It has not been rerun against a deployed UI. No remote files, services, or user test data were changed during this review. Real host visual acceptance remains open.

The notes request now invokes the bundled session skill. The agent reads transcript pages and saves its own analysis with meeting_save_notes; there is no nested inference call.

The per-meeting Analysis profile selector has been removed. Notes and Action Items use the open owning profile; historical selections no longer grant access. Requests carry the owning `source_profile`. Session routing checks the current Desktop route name and connection against the backend profile, supporting aliased remote profiles without switching profiles. Saved results retain their actual original profile attribution. Synthetic checks cover retired selection requests, owner-only access and routing changes during session startup.


Deployment update: the polished shared UI, combined-recording form and the then-current profile selector were installed on the Compose test host after Opus 5.5 review and fixes. The host-reconciled `desktop/plugin.js` matches the local artifact exactly. Seventy-four tests and authenticated API/playback checks pass. Reload the Webapp to load it; real host visual acceptance and native Desktop checks are still pending.


## Action Items tab

Action Items sits beside Notes and uses the same shared plugin in Desktop and Webapp. Users generate proposals in Hermes, review quoted evidence, choose a calendar/to-do destination, then explicitly prepare and copy a handoff into the current owning profile chat. The panel polls persisted results and shows needs-input, uncertain, completed and failed outcomes with provider IDs/links. A task being added is distinct from its work being finished. Older unmatched handoffs remain visible after regeneration. Synthetic UI tests cover a lost prepare response with the same request ID, clipboard handoff scope, completed-result refresh, stale proposal gating and retained uncertain history. Visual acceptance and a real connected-service round trip remain open.


## Audio storage controls

Transcription settings contains Audio storage: a host directory field, optional lossless FLAC playback, and optional mono Opus archive with explicit original-replacement acknowledgement. Each original recording has an Audio storage disclosure with formats, sizes and maintenance status. Review move/compression loads the current policy and requires an explicit Apply; the API compares that exact reviewed policy before enqueueing. Combined meetings point users to their original recordings. Synthetic UI checks cover consent, host-versus-browser path wording, review before mutation, and the exact policy payload. No native filesystem picker or browser-specific filesystem API is used.


## Notes session button — 2026-11-06

The Notes tab now always exposes **Start summarization session** once a transcript exists, including when viewing existing notes. It opens the current owning profile and prepares the skill request for the user to send. Opus 5.5 review identified an asynchronous composer-mount race and missing clipboard recovery; both were fixed before deployment. Filling waits up to about ten seconds for the current profile and fresh composer, verifies the text after insertion, retries a late draft reset, and preserves user-entered text. Unavailable profile routes still fail closed, with a copy fallback.

Deployed only `desktop/plugin.js` to the isolated test host, then used the authenticated host reconciliation endpoint. Previous source and served copies are backed up under `~/hermes-test/backups/notes-session-20261106/`. The served artifact matches the local file exactly. All 112 pytest checks pass, including the expanded synthetic UI scenarios. A headless authenticated Webapp check used an existing synthetic recording, clicked Notes → Start summarization session, and confirmed the full request remained in the new composer after settling. It did not submit a model request. Cross-profile activation is covered synthetically; live cross-profile routing and native Desktop remain unverified.


## Full-meeting player and transcript frame — 2026-11-08

The shared UI now keeps a waveform player above the meeting content. It uses the full canonical recording duration, including combined source boundaries and trailing audio, with global seek, pause and speed controls. Transcript turn timestamps seek this player. A bounded look-ahead advances 30-second authenticated JSON audio windows; this supports both UI hosts and OAuth remotes without new credential handling or a full-recording browser allocation. Transitions can buffer and are not claimed sample-gapless. Saved-note excerpt playback remains available and pauses the meeting player.

The transcript list scrolls independently beneath its toolbar. Playback highlights the current timed turn and scrolls only the list. Follow audio can be disabled, and editing/search/filtering suppress automatic movement. The waveform comes from real PCM sample peaks, not transcript timing or decorative data. Missing waveform computation does not prevent duration-based playback. No transcription jobs or provider calls are started by these read-only routes. Native/Webapp visual and audible acceptance of this addition remains pending.
