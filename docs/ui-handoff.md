# Shared UI handoff — 2026-11-05

`desktop/plugin.js` is the single UI for native Desktop and the Webapp. It imports only `Button`, `Badge` and the two area constants from `@hermes/plugin-sdk`, plus React. Styling is one `.mn`-scoped stylesheet injected in `register()` and removed via `ctx.onDispose` (the same pattern as the bundled Radio plugin). It uses only `--ui-*` theme variables that exist in the host's `styles.css`; the old `--ui-border` did not exist. Webapp CSP allows inline styles; native Desktop is not yet checked.

## Layout

- **Sidebar:** meeting list with date and non-complete state; New recording; Transcription settings; Start queued work (only while something is queued).
- **New recording:** title, `datetime-local` converted to ISO with the browser's offset (shown before upload), file picker, explicit Upload and transcribe. Submit reads the input's live FileList. Same preflight, 8 MiB SHA-256 chunks, 30 s timeouts and idempotent finalize. Navigation is locked during upload.
- **Meeting:** one-line header (title, transcript coverage, state) with date/zone, speaker, turn and note counts; status/retry callout; sticky player; then **Transcript** and **Notes** tabs. Layout borrows from halo-transcribe's editor:
  - **Transcript:** toolbar (search, speaker filter, Saving…, Undo last change, Speakers toggle at narrow widths). Each turn has a speaker chip (click → inline “Who said this?” picker), a ▶ timestamp, corrected badges, and text you double-click (or Edit text) to edit inline (Enter saves, Esc cancels).
  - **Speakers panel** docked to the right (a toggled drawer below ~860 px of pane width, via a container query): swatch, click-to-rename name, talk time, share and turn count, ▶ sample, Next (jumps to that speaker's next turn), Merge… with an inline confirmation stating how much talk moves. Below it, the change history with per-item Undo.
  - **Notes:** copyable `meeting_notes:analyze` skill request when there are no notes or they need review (otherwise under “Regenerate notes”), stale/unmatched warnings, version selector for history, grouped sections with jump links, owner/due rows marked Unconfirmed, quotes with saved-source playback and Show in transcript.
  - Not borrowed (no backend support): waveform, word-level seek/selection, continuous playback, redo, drag-to-merge, chunk retry.
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

Per-meeting Analysis profile now lists existing Hermes profiles on the importing host and persists the selection with revision checks. Notes requests carry the importing `source_profile`; Copy request and open profile resolves only the selected target on the original connection using public SDK routes, then opens a normal chat draft. It never auto-submits or overrides the model. Unavailable or ambiguous routes fail visibly. A single-profile Webapp cannot route another profile. Saved notes display their actual analysis profile. Interaction checks cover selector persistence, unavailable saved profiles, and duplicate profile names on separate connections. Host visual acceptance is still open.


Deployment update: the polished shared UI, combined-recording form and profile selector are now installed on the Compose test host after Opus 5.5 review and fixes. The host-reconciled `desktop/plugin.js` matches the local artifact exactly. Seventy-four tests and authenticated API/playback checks pass. Reload the Webapp to load it; real host visual acceptance and native Desktop checks are still pending.


## Notes session button — 2026-11-06

The Notes tab now always exposes **Start summarization session** once a transcript exists, including when viewing existing notes. It opens the selected analysis profile and prepares the skill request for the user to send. Opus 5.5 review identified an asynchronous composer-mount race and missing clipboard recovery; both were fixed before deployment. Filling waits up to about ten seconds for the selected profile and fresh composer, verifies the text after insertion, retries a late draft reset, and preserves user-entered text. Unavailable profile routes still fail closed, with a copy fallback.

Deployed only `desktop/plugin.js` to the isolated test host, then used the authenticated host reconciliation endpoint. Previous source and served copies are backed up under `~/hermes-test/backups/notes-session-20261106/`. The served artifact matches the local file exactly. All 112 pytest checks pass, including the expanded synthetic UI scenarios. A headless authenticated Webapp check used an existing synthetic recording, clicked Notes → Start summarization session, and confirmed the full request remained in the new composer after settling. It did not submit a model request. Cross-profile activation is covered synthetically; live cross-profile routing and native Desktop remain unverified.
