// Shared UI for native Hermes Desktop and the Webapp. Uncompiled ESM: only
// @hermes/plugin-sdk and react resolve, so no JSX and no other imports.
import { Badge, Button, host, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { cloneElement, createElement as h, Fragment, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'

const STYLE_ID = 'hermes-meeting-notes-styles'
const POLL_MS = 3000
const MAX_UPLOAD = 512 * 1024 * 1024
const UPLOAD_TIMEOUT_MS = 120000
const PLAY_WINDOW_MAX = 30

// Disk plugins are not scanned by Tailwind. Every rule is scoped under .mn and
// uses only host theme variables, so light/dark and custom skins follow the app.
export const CSS = `
.mn{--mn-gap:16px;--mn-line:var(--ui-stroke-tertiary,var(--ui-stroke-secondary));--mn-soft:var(--ui-bg-quaternary,transparent);display:grid;grid-template-columns:minmax(220px,280px) minmax(0,1fr);height:100%;min-height:0;color:var(--ui-text-primary);font-size:13px;line-height:1.5}
.mn *{box-sizing:border-box}
.mn :focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px}
.mn h1,.mn h2,.mn h3,.mn h4,.mn p{margin:0}
.mn-side{display:flex;flex-direction:column;min-width:0;min-height:0;border-right:1px solid var(--mn-line)}
.mn-side-head{display:grid;grid-template-columns:minmax(0,1fr);gap:8px;padding:16px 12px 8px 16px}
.mn-side-head>button{width:100%;min-width:0;white-space:normal;height:auto;min-height:28px}
.mn-side-head h1{font-size:14px;font-weight:600}
.mn-list{list-style:none;margin:0;padding:4px 8px;overflow:auto;flex:1;min-height:0}
.mn-list li{margin:0 0 2px}
.mn-item{display:grid;gap:2px;width:100%;text-align:left;padding:8px 10px;border:0;border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.mn-item:disabled{opacity:.6;cursor:default}
.mn-item:hover:not(:disabled){background:var(--ui-row-hover-background,var(--mn-soft))}
.mn-item[aria-current=true]{background:var(--ui-row-active-background,var(--mn-soft))}
.mn-item-title{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mn-item-meta{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ui-text-tertiary)}
.mn-side-foot{display:grid;gap:6px;padding:10px 12px 14px;border-top:1px solid var(--mn-line)}
.mn-main{min-width:0;min-height:0;overflow:auto;container-type:inline-size}
.mn-page{max-width:1180px;margin:0 auto;padding:24px 28px 48px;display:grid;gap:var(--mn-gap)}
.mn-muted{color:var(--ui-text-secondary)}
.mn-faint{color:var(--ui-text-tertiary);font-size:12px}
.mn-header{display:grid;gap:6px}
.mn-header h2{font-size:20px;font-weight:600;line-height:1.3;overflow-wrap:anywhere;min-width:0;flex:1}
.mn-title-row{display:flex;align-items:center;gap:12px}
.mn-nowrap{white-space:nowrap;font-variant-numeric:tabular-nums}
.mn-grow{flex:1}
.mn-facts{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--ui-text-secondary);font-size:12.5px}
.mn-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.mn-spread{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px}
.mn-card{border:1px solid var(--mn-line);border-radius:8px;padding:14px 16px;background:var(--ui-bg-card,transparent);display:grid;gap:10px}
.mn-callout{border-radius:6px;padding:10px 12px;border:1px solid var(--mn-line);border-left-width:3px;display:grid;gap:6px}
.mn-callout[data-tone=warn]{border-left-color:var(--ui-yellow,var(--ui-accent))}
.mn-callout[data-tone=error]{border-left-color:var(--ui-red,var(--ui-accent))}
.mn-callout[data-tone=info]{border-left-color:var(--ui-accent)}
.mn-callout[data-tone=ok]{border-left-color:var(--ui-green,var(--ui-accent))}
.mn-callout strong{font-weight:600}
.mn-field{display:grid;gap:4px;min-width:0}
.mn-field>label{font-weight:500;font-size:12.5px}
.mn-field small{color:var(--ui-text-tertiary);font-size:11.5px}
.mn-input{font:inherit;color:inherit;background:transparent;border:1px solid var(--ui-stroke-secondary);border-radius:5px;padding:6px 8px;width:100%;min-width:0}
.mn-input:disabled{opacity:.6}
textarea.mn-input{resize:vertical;min-height:64px;line-height:1.5}
select.mn-input{padding-right:24px}
.mn-input[aria-invalid=true]{border-color:var(--ui-red,var(--ui-accent))}
.mn-grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.mn-file{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.mn-progress{width:100%;height:8px;accent-color:var(--ui-accent)}
.mn-tabs{display:flex;gap:2px;border-bottom:1px solid var(--mn-line);overflow-x:auto}
.mn-tab{font:inherit;color:var(--ui-text-secondary);background:transparent;border:0;border-bottom:2px solid transparent;padding:8px 12px;margin-bottom:-1px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.mn-tab:hover{color:var(--ui-text-primary)}
.mn-tab[aria-selected=true]{color:var(--ui-text-primary);border-bottom-color:var(--ui-accent);font-weight:500}
.mn-count{font-size:11px;color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums}
.mn-player{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;padding:8px 12px;border:1px solid var(--mn-line);border-radius:8px;background:var(--ui-bg-elevated,var(--ui-bg-card,transparent));backdrop-filter:blur(8px)}
.mn-player audio{height:32px;flex:1 1 240px;min-width:0}
.mn-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--mn-line)}
.mn-toolbar .mn-field{flex:1 1 180px}
.mn-input-sm{padding:4px 8px;font-size:12.5px}
.mn-search{flex:1 1 200px;width:auto}
.mn-filter{flex:0 1 180px;width:auto}
.mn-editor{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:24px;align-items:start}
.mn-editor-main{display:grid;gap:8px;min-width:0}
.mn-panel{position:sticky;top:12px;display:grid;gap:10px;align-content:start;max-height:calc(100vh - 140px);overflow:auto;padding:12px;border:1px solid var(--mn-line);border-radius:8px;background:var(--ui-bg-card,transparent)}
.mn-panel-title{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--ui-text-secondary)}
.mn-spk-list{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.mn-spk{display:grid;gap:6px;padding:6px;border-radius:6px}
.mn-spk:hover{background:var(--ui-row-hover-background,var(--mn-soft))}
.mn-spk-main{display:flex;align-items:flex-start;gap:8px}
.mn-spk-name{display:grid;gap:1px;min-width:0;flex:1}
.mn-spk-tools{display:flex;gap:0;flex:none}
.mn-name-btn{font:inherit;font-weight:600;color:inherit;background:transparent;border:0;padding:0;text-align:left;cursor:text;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mn-name-btn:hover:not(:disabled){text-decoration:underline}
.mn-dot-lg{width:11px;height:11px;margin-top:4px}
.mn-merge{display:grid;gap:6px;padding:8px;border-radius:6px;background:var(--mn-soft)}
.mn .mn-drawer-only{display:none}
.mn-chip{font:inherit;font-size:12.5px;font-weight:600;color:inherit;display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border:0;border-radius:999px;background:transparent;cursor:pointer}
.mn-chip:hover:not(:disabled){background:var(--ui-row-hover-background,var(--mn-soft))}
.mn-chip[aria-pressed=true]{background:var(--ui-row-active-background,var(--mn-soft))}
.mn-picker{display:flex;flex-wrap:wrap;align-items:center;gap:4px;padding:4px;border-radius:6px;background:var(--mn-soft)}
.mn-inline-edit{display:grid;gap:6px}
.mn-transcript{list-style:none;margin:0;padding:8px;max-height:60vh;min-height:240px;overflow:auto;overscroll-behavior:contain;border:1px solid var(--mn-line);border-radius:8px}
.mn-meeting-player{position:sticky;top:0;z-index:3;background:var(--ui-bg-elevated,var(--ui-bg-card));padding:12px}
.mn-wave{height:80px;cursor:crosshair;color:var(--ui-text-secondary)}
.mn-wave svg{width:100%;height:100%}
.mn-player-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.mn-player-controls input[type=range]{flex:1;min-width:100px}
.mn-seg{display:grid;gap:4px;padding:10px 8px;border-radius:6px;scroll-margin-top:64px}
.mn-seg:hover{background:var(--ui-row-hover-background,transparent)}
.mn-seg[data-active=true]{background:var(--ui-selection-background,var(--mn-soft))}
.mn-seg[data-flash=true]{background:var(--ui-row-active-background,var(--mn-soft))}
.mn-time{font:inherit;font-family:var(--font-mono,ui-monospace,monospace);font-size:11.5px;color:var(--ui-text-tertiary);background:transparent;border:0;padding:2px 4px;border-radius:4px;cursor:pointer;font-variant-numeric:tabular-nums}
.mn-time:hover{color:var(--ui-accent)}
.mn-seg-body{display:grid;gap:4px;min-width:0}
.mn-seg-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.mn-seg-text{overflow-wrap:anywhere;white-space:pre-wrap;line-height:1.65;padding-left:8px}
.mn-seg-actions{display:flex;gap:4px;margin-left:auto;opacity:0}
.mn-seg:hover .mn-seg-actions,.mn-seg:focus-within .mn-seg-actions{opacity:1}
@media (hover:none){.mn .mn-seg-actions{opacity:1}}
.mn-who{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:12.5px}
.mn-dot{width:9px;height:9px;border-radius:50%;flex:none;background:var(--mn-c,var(--ui-accent))}
.mn-history{list-style:none;margin:0;padding:0;display:grid}
.mn-history li{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--mn-line);font-size:12px}
.mn-history li[data-undone=true] .mn-history-text{text-decoration:line-through;color:var(--ui-text-tertiary)}
.mn-section{display:grid;gap:8px}
.mn-section h3{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--ui-text-secondary);display:flex;align-items:center;gap:8px}
.mn-note{display:grid;gap:8px;padding:12px 0;border-bottom:1px solid var(--mn-line)}
.mn-note:last-child{border-bottom:0}
.mn-note-text{font-size:13.5px;overflow-wrap:anywhere}
.mn-attrs{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 12px;font-size:12.5px;margin:0}
.mn-attrs dt{color:var(--ui-text-tertiary)}
.mn-attrs dd{margin:0;display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.mn-cite{display:grid;gap:4px;padding:8px 10px;border-radius:6px;background:var(--mn-soft)}
.mn-cite blockquote{margin:0;overflow-wrap:anywhere}
.mn-cite-meta{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;font-size:12px;color:var(--ui-text-tertiary)}
.mn-jump{display:flex;flex-wrap:wrap;gap:6px}
.mn-empty{display:grid;gap:10px;justify-items:start;padding:28px 4px}
.mn-empty h2{font-size:18px;font-weight:600}
.mn-steps{margin:0;padding-left:20px;display:grid;gap:4px;color:var(--ui-text-secondary)}
.mn-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.mn-skel{height:12px;border-radius:4px;background:var(--mn-soft)}
@container (max-width:860px){
  .mn-editor{grid-template-columns:minmax(0,1fr)}
  .mn-panel{display:none;position:static;max-height:none}
  .mn-editor[data-drawer=open] .mn-panel{display:grid;order:-1}
  .mn .mn-drawer-only{display:inline-flex}
}
@media (max-width:760px){
  .mn{grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr);overflow:auto}
  .mn-side{border-right:0;border-bottom:1px solid var(--mn-line)}
  .mn-list{max-height:30vh}
  .mn-page{padding:16px 14px 40px}
}
`

export function ensureStyles(doc = globalThis.document) {
  if (!doc || doc.getElementById(STYLE_ID)) return null
  const style = doc.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  doc.head.appendChild(style)
  return style
}

// ── Pure helpers (exported for regression tests) ─────────────────────────────

export const STATE_INFO = {
  queued: { label: 'Queued', tone: 'muted', active: true,
    detail: 'Waiting for the next audio chunk. The running worker continues automatically; after a host restart, use Start queued work if needed.' },
  preparing: { label: 'Preparing audio', tone: 'default', active: true,
    detail: 'Converting the recording to the transcription format on the Hermes host.' },
  in_flight: { label: 'Transcribing', tone: 'default', active: true,
    detail: 'The current chunk is being transcribed. Progress updates after each completed chunk; this page refreshes every 3 seconds.' },
  completed: { label: 'Transcribed', tone: 'success', active: false, detail: '' },
  interrupted_unknown: { label: 'Interrupted', tone: 'warn', active: false,
    detail: 'Work was interrupted; an unfinished request may have an unknown outcome. Retry resumes unfinished audio and keeps completed chunks.' },
  failed: { label: 'Failed', tone: 'destructive', active: false,
    detail: 'Transcription did not complete. Check the transcription settings and the server, then retry.' },
  invalid_response: { label: 'Unreadable result', tone: 'destructive', active: false,
    detail: 'The response was saved but could not be turned into a transcript. Recover saved response retries parsing locally when available. Retry transcription sends unfinished audio again; completed chunks are kept.' }
}
export const RETRYABLE = ['failed', 'invalid_response', 'interrupted_unknown']
export const stateInfo = state => STATE_INFO[state] || { label: String(state || 'Unknown'), tone: 'muted', active: false, detail: '' }

export const KINDS = [
  { kind: 'summary', label: 'Summary' },
  { kind: 'decision', label: 'Decisions' },
  { kind: 'question', label: 'Open questions' },
  { kind: 'commitment', label: 'Commitments' },
  { kind: 'suggestion', label: 'Suggestions', note: 'Ideas raised in the meeting. These are not commitments.' }
]

const SPEAKER_COLORS = ['--ui-blue', '--ui-green', '--ui-orange', '--ui-purple', '--ui-cyan', '--ui-red', '--ui-yellow']

export function clock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0))
  const hours = Math.floor(s / 3600), minutes = Math.floor((s % 3600) / 60), rest = s % 60
  const mm = hours ? String(minutes).padStart(2, '0') : String(minutes)
  return `${hours ? `${hours}:` : ''}${mm}:${String(rest).padStart(2, '0')}`
}

export function spokenDuration(seconds) {
  const s = Math.round(Number(seconds) || 0)
  if (s < 60) return `${s} s`
  const hours = Math.floor(s / 3600), minutes = Math.round((s % 3600) / 60)
  return hours ? `${hours} h ${minutes} min` : `${minutes} min`
}

export function fileSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

/** `<input type=datetime-local>` value for a Date, in the browser's zone. */
export function localInputValue(date = new Date()) {
  const p = n => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`
}

/** Local wall time → ISO 8601 with the browser's offset for that instant. */
export function withOffset(localValue) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(String(localValue || ''))
  if (!m) return null
  const [y, mo, d, hh, mi, ss = '00'] = m.slice(1)
  const date = new Date(Number(y), Number(mo) - 1, Number(d), Number(hh), Number(mi), Number(ss))
  if (Number.isNaN(date.getTime()) || date.getFullYear() !== Number(y) || date.getMonth() !== Number(mo) - 1 || date.getDate() !== Number(d) || date.getHours() !== Number(hh) || date.getMinutes() !== Number(mi) || date.getSeconds() !== Number(ss)) return null
  const offset = -date.getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  const abs = Math.abs(offset)
  return `${y}-${mo}-${d}T${hh}:${mi}:${ss}${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}

/** Mirrors backend upload rules so mistakes are reported before any bytes move. */
export function uploadProblem({ file, title, date, secure }) {
  if (!file) return 'Choose a recording first.'
  if (!title || !title.trim()) return 'Enter a meeting title.'
  if (title.length > 300) return 'Keep the title to 300 characters or fewer.'
  if (!date || !Number.isFinite(Date.parse(date)) || !/(Z|[+-]\d{2}:\d{2})$/.test(date)) return 'Enter a valid meeting date and time.'
  if (!secure) return 'Secure upload is unavailable at this address. Open the Webapp over HTTPS or through a localhost tunnel.'
  if (!(file.size > 0) || file.size > MAX_UPLOAD) return 'Choose an audio file between 1 byte and 512 MB.'
  return null
}

export function formatMeetingDate(iso, timezone, opts = { dateStyle: 'medium', timeStyle: 'short' }) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return String(iso || '')
  try { return new Intl.DateTimeFormat(undefined, { ...opts, timeZone: timezone || undefined }).format(new Date(t)) }
  catch { return new Date(t).toLocaleString() }
}

/** YYYY-MM-DD as a calendar date, never shifted through UTC. */
export function calendarDate(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''))
  if (!m) return String(value || '')
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' })
}

export const speakerName = (participant, name) => name || `Participant ${participant}`

/** Current participants in first-appearance order, with talk time and counts. */
export function participantsOf(segments = [], corrections = []) {
  let labels = Object.fromEntries(segments.map(s => [s.label, s.label]))
  for (const c of activeCorrections(corrections)) {
    if (c.kind === 'merge') {
      const from = labels[c.target], to = labels[c.value]
      labels = Object.fromEntries(Object.entries(labels).map(([key, value]) => [key, value === from ? to : value]))
    }
  }
  const map = new Map()
  for (const s of segments) {
    if (!map.has(s.participant)) map.set(s.participant, { id: s.participant, name: s.name || null, segments: 0, seconds: 0, words: 0, first: s, index: map.size, mergeable: labels[s.participant] === s.participant, mergeSegments: 0, mergeSeconds: 0 })
    const p = map.get(s.participant)
    p.segments += 1
    p.seconds += Math.max(0, (s.end || 0) - (s.start || 0))
    // A merge moves every turn shown under this speaker, including turns
    // that were moved to it individually.
    p.mergeSegments += 1
    p.mergeSeconds += Math.max(0, (s.end || 0) - (s.start || 0))
    p.words += s.word_count || 0
    if (!p.name && s.name) p.name = s.name
  }
  return [...map.values()]
}

export function speakerColor(participant, participants) {
  const found = participants.find(p => p.id === participant)
  const index = found ? found.index : 0
  return `var(${SPEAKER_COLORS[index % SPEAKER_COLORS.length]}, var(--ui-accent))`
}

/** Corrections that are in effect (not undo records and not undone). */
export function activeCorrections(corrections = []) {
  const undone = new Set(corrections.filter(c => c.kind === 'undo').map(c => c.value))
  return corrections.filter(c => c.kind !== 'undo' && !undone.has(c.id))
}

export function describeCorrection(c, segments = [], originalName = label => `Participant ${label}`) {
  const seg = segments.find(s => s.id === c.target)
  const at = seg ? ` at ${clock(seg.start)}` : ''
  switch (c.kind) {
    case 'merge': return `Merged ${originalName(c.target)} into ${originalName(c.value)}`
    case 'rename': return `Named Participant ${c.target} “${c.value}”`
    case 'speaker': return `Reassigned the segment${at} to ${originalName(c.value)}`
    case 'text': return `Edited the text${at}`
    case 'undo': return 'Undo'
    default: return c.kind
  }
}

export function notesRequest(meeting) {
  const scope = meeting.owner_profile ? ` Pass source_profile=${JSON.stringify(meeting.owner_profile)} to every transcript read and notes save. Run this in Hermes profile ${JSON.stringify(meeting.owner_profile)}.` : ''
  return `Use the meeting_notes:analyze skill to generate and save sourced notes for meeting ${meeting.id}.${scope}`
}

export function ownerText(item) {
  const owner = item.resolved_owner
  if (owner && owner.basis === 'named') return { text: owner.name, note: 'Named in the transcript' }
  if (owner) return { text: speakerName(owner.participant, owner.name), note: 'Speaker of the commitment' }
  if (item.owner && item.owner.basis === 'speaker') return { text: 'Unresolved', note: 'The cited speaker segment is no longer in the transcript' }
  return { text: 'Unresolved', note: '' }
}

export function dueText(item) {
  const due = item.due || {}
  if (!due.quote) return { text: 'No date stated', badge: null }
  if (due.proposed_date) return { text: calendarDate(due.proposed_date), quote: due.quote, badge: 'Unconfirmed' }
  return { text: 'Date unclear', quote: due.quote, badge: 'Unconfirmed' }
}

function relative(seconds) {
  if (!seconds) return ''
  const diff = Date.now() / 1000 - seconds
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

const errorText = e => String((e && (e.message || e.detail)) || e || 'Something went wrong')

async function sha256Hex(bytes) {
  const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('')
}

// ── Small building blocks ────────────────────────────────────────────────────

const btn = (props, ...children) => h(Button, { type: 'button', size: 'sm', variant: 'outline', ...props }, ...children)

function Callout({ tone = 'info', title, children, role }) {
  return h('div', { className: 'mn-callout', 'data-tone': tone, role },
    title && h('strong', null, title),
    children)
}

function StateBadge({ state }) {
  const info = stateInfo(state)
  return h(Badge, { variant: info.tone }, info.label)
}

function Who({ participant, name, participants }) {
  return h('span', { className: 'mn-who' },
    h('span', { className: 'mn-dot', 'aria-hidden': true, style: { '--mn-c': speakerColor(participant, participants) } }),
    speakerName(participant, name))
}

// Label + control + hint; the hint is announced as the control's description.
function Field({ label, hint, children }) {
  const id = useId()
  const control = cloneElement(children, { id, 'aria-describedby': hint ? `${id}-hint` : undefined })
  return h('div', { className: 'mn-field' }, h('label', { htmlFor: id }, label), control, hint && h('small', { id: `${id}-hint` }, hint))
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

function Sidebar({ meetings, loaded, selectedId, view, onSelect, onNew, onCombine, onSettings, onStartQueued, busy, locked }) {
  const queued = meetings.some(m => ['queued', 'preparing', 'in_flight'].includes(m.state))
  return h('nav', { className: 'mn-side', 'aria-label': 'Meetings' },
    h('div', { className: 'mn-side-head' },
      h('h1', null, 'Meeting Notes'),
      btn({ variant: 'default', onClick: onNew, disabled: locked, 'aria-current': view === 'upload' ? 'page' : undefined }, 'New recording'),
      btn({ onClick: onCombine, disabled: locked, 'aria-current': view === 'combine' ? 'page' : undefined }, 'Combine meetings')),
    !loaded ? h('div', { className: 'mn-list', 'aria-busy': true },
      h('p', { className: 'mn-faint', style: { padding: '8px 10px' } }, 'Loading meetings…'))
      : meetings.length === 0 ? h('p', { className: 'mn-faint', style: { padding: '8px 16px' } }, 'No meetings yet.')
        : h('ul', { className: 'mn-list' }, ...meetings.map(m => h('li', { key: m.id },
          h('button', { type: 'button', className: 'mn-item', disabled: locked, 'aria-current': view === 'meeting' && m.id === selectedId ? 'true' : undefined, onClick: () => onSelect(m.id) },
            h('span', { className: 'mn-item-title' }, m.title),
            h('span', { className: 'mn-item-meta' },
              h('span', null, formatMeetingDate(m.date, m.timezone, { dateStyle: 'medium' })),
              m.state !== 'completed' && h(StateBadge, { state: m.state })))))),
    h('div', { className: 'mn-side-foot' },
      queued && btn({ onClick: onStartQueued, disabled: busy || locked }, 'Start queued work'),
      btn({ variant: 'ghost', onClick: onSettings, disabled: locked, 'aria-current': view === 'settings' ? 'page' : undefined }, 'Transcription settings')))
}

// ── Upload ───────────────────────────────────────────────────────────────────

export function CombineForm({ meetings, rest, onCreated, onBusy }) {
  const [sources, setSources] = useState([])
  const [title, setTitle] = useState('')
  const [date, setDate] = useState('')
  const [timezone, setTimezone] = useState('')
  const [ownAudio, setOwnAudio] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef(null)
  const available = meetings.filter(m => m.state === 'completed' && m.active && !m.combined && !sources.some(s => s.id === m.id))
  const move = (i, direction) => {
    const copy = [...sources]
    ;[copy[i], copy[i + direction]] = [copy[i + direction], copy[i]]
    setSources(copy)
  }
  const refreshSources = async () => {
    setBusy(true); onBusy(true); setError('')
    try {
      const current = await rest('/meetings')
      const refreshed = sources.map(source => {
        const m = current.find(m => m.id === source.id && m.state === 'completed' && m.active && !m.combined)
        if (!m) throw new Error(`Recording ${source.title} is no longer ready. Remove it from the selection.`)
        return m
      })
      setSources(refreshed)
    } catch (e) { setError(errorText(e)) }
    finally { setBusy(false); onBusy(false) }
  }
  const submit = async event => {
    event.preventDefault()
    if (busy) return
    setError('')
    if (sources.length < 2 || !title.trim() || !date || !timezone) {
      setError('Choose at least two recordings and enter a title, meeting date and timezone.'); return
    }
    setBusy(true); onBusy(true)
    try {
      const body = { title: title.trim(), date, timezone, sources: sources.map(({ id, active, revision }) => ({ id, active, revision })) }
      if (ownAudio) body.own_audio = true
      const signature = JSON.stringify(body)
      if (request.current?.signature !== signature) {
        const bytes = new Uint8Array(16)
        globalThis.crypto.getRandomValues(bytes)
        request.current = { signature, id: Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('') }
      }
      const result = await rest('/meetings/combine', { method: 'POST', body: { ...body, request_id: request.current.id } })
      await onCreated(result.id)
    } catch (e) { setError(errorText(e)) }
    finally { setBusy(false); onBusy(false) }
  }
  return h('form', { className: 'mn-page mn-section', onSubmit: submit },
    h('h2', null, 'Combine transcribed recordings'),
    h('p', { className: 'mn-muted' }, 'Create one meeting from recordings split by your recorder. Originals and their notes stay intact. No audio is sent for transcription.'),
    h(Field, { label: 'Add recording in playback order' },
      h('select', { className: 'mn-input', value: '', disabled: busy || sources.length >= 20,
        onChange: e => { const m = available.find(x => x.id === e.target.value); if (!m) return; setSources([...sources, m]); if (!sources.length) { setTitle(m.title); setDate(m.date); setTimezone(m.timezone) } } },
      h('option', { value: '' }, 'Choose a completed recording…'),
      ...available.map(m => h('option', { key: m.id, value: m.id }, `${m.title} · ${formatMeetingDate(m.date, m.timezone)}`)))),
    !available.length && !sources.length && h('p', { className: 'mn-faint' }, 'Import and transcribe at least two recordings first.'),
    sources.length > 0 && btn({ disabled: busy, onClick: refreshSources }, 'Refresh selected transcripts'),
    h('ol', { 'aria-label': 'Recording order' }, ...sources.map((m, i) => h('li', { key: m.id },
      h('span', null, `${m.title} · revision ${m.revision} `),
      btn({ size: 'xs', disabled: busy || i === 0, onClick: () => move(i, -1), 'aria-label': `Move recording ${i + 1} up` }, '↑'),
      btn({ size: 'xs', disabled: busy || i === sources.length - 1, onClick: () => move(i, 1), 'aria-label': `Move recording ${i + 1} down` }, '↓'),
      btn({ size: 'xs', disabled: busy, onClick: () => setSources(sources.filter(s => s.id !== m.id)), 'aria-label': `Remove recording ${i + 1}` }, 'Remove')))),
    h(Field, { label: 'Combined meeting title' }, h('input', { className: 'mn-input', value: title, disabled: busy, onChange: e => setTitle(e.target.value), maxLength: 300 })),
    h(Field, { label: 'Meeting date with UTC offset', hint: 'Used to interpret dates in the combined notes.' }, h('input', { className: 'mn-input', value: date, disabled: busy, onChange: e => setDate(e.target.value) })),
    h(Field, { label: 'Meeting timezone' }, h('input', { className: 'mn-input', value: timezone, disabled: busy, onChange: e => setTimezone(e.target.value) })),
    h('p', { className: 'mn-muted' }, 'Recordings play back to back; gaps or overlaps are not inferred. Each recording keeps separate speakers until you merge them. This copies the current corrected transcripts; later edits to originals do not change the combined meeting. Generate new notes after reviewing it.'),
    h('label', null, h('input', { type: 'checkbox', checked: ownAudio, disabled: busy, onChange: e => setOwnAudio(e.target.checked) }), ' Move audio into a numbered folder owned by the combined meeting'),
    ownAudio && h('p', { className: 'mn-muted' }, 'Queues a background move into your configured audio storage with 01-, 02-, … filenames in playback order. After it completes, you can delete the original meeting entries; their notes are not copied. Original files on your recorder/computer stay unchanged.'),
    error && h(Callout, { tone: 'error', role: 'alert' }, error),
    btn({ type: 'submit', variant: 'default', disabled: busy || sources.length < 2 }, busy ? 'Combining…' : 'Create combined meeting'))
}

export function UploadForm({ rest, onUploaded, onBusy }) {
  const [title, setTitle] = useState('')
  const [when, setWhen] = useState(() => localInputValue())
  const [file, setFile] = useState(null)
  const [phase, setPhase] = useState('idle') // idle | uploading | finalizing
  const [progress, setProgress] = useState(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const picker = useRef(null)
  const iso = withOffset(when)
  const zone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone } catch { return 'UTC' } })()
  const working = phase !== 'idle'

  const submit = async event => {
    event.preventDefault()
    if (working) return
    // Read the input's live FileList: some browsers (Safari) can miss change events.
    const chosen = picker.current?.files?.[0] || file
    const problem = uploadProblem({ file: chosen, title, date: iso, secure: Boolean(globalThis.crypto?.subtle) })
    if (problem) { setError(problem); setStatus(''); return }
    setError(''); setPhase('uploading'); onBusy?.(true); setProgress(0)
    try {
      setStatus(`Starting upload of ${chosen.name}…`)
      const session = await rest('/uploads', { method: 'POST', body: { size: chosen.size, title: title.trim(), date: iso, timezone: zone } })
      const total = Math.ceil(chosen.size / session.chunk_size)
      for (let offset = 0, index = 0; offset < chosen.size; offset += session.chunk_size, index++) {
        setStatus(`Uploading part ${index + 1} of ${total}…`)
        const bytes = await chosen.slice(offset, offset + session.chunk_size).arrayBuffer()
        const sha = await sha256Hex(bytes)
        if (session.chunk_transport === 'json_base64') {
          const array = new Uint8Array(bytes)
          const parts = []
          for (let i = 0; i < array.length; i += 0x8000) parts.push(String.fromCharCode(...array.subarray(i, i + 0x8000)))
          await rest(`/uploads/${session.id}/chunks/${index}/json?sha=${sha}`, { method: 'PUT', body: { base64: btoa(parts.join('')) }, timeoutMs: UPLOAD_TIMEOUT_MS })
        } else {
          await rest(`/uploads/${session.id}/chunks/${index}?sha=${sha}`, { method: 'PUT', upload: { filename: 'chunk', contentType: 'application/octet-stream', bytes }, timeoutMs: UPLOAD_TIMEOUT_MS })
        }
        setProgress(Math.round(Math.min(offset + bytes.byteLength, chosen.size) / chosen.size * 100))
      }
      setPhase('finalizing')
      setStatus('Upload complete. Verifying the recording on the host…')
      const result = await rest(`/uploads/${session.id}/finalize`, { method: 'POST', timeoutMs: UPLOAD_TIMEOUT_MS })
      setStatus('Recording saved.')
      await onUploaded(result.id)
    } catch (e) {
      setError(`Upload stopped: ${errorText(e)}`)
      setStatus('')
    } finally {
      setPhase('idle'); onBusy?.(false)
    }
  }

  return h('div', { className: 'mn-page' },
    h('header', { className: 'mn-header' },
      h('h2', null, 'New recording'),
      h('p', { className: 'mn-muted' }, 'Upload an audio file to transcribe it with speaker separation. The original file is kept unchanged on this Hermes host; transcription sends the audio to the server set in Transcription settings.')),
    h('form', { className: 'mn-card', onSubmit: submit, noValidate: true, 'aria-label': 'Upload a recording' },
      h(Field, { label: 'Meeting title' },
        h('input', { className: 'mn-input', value: title, maxLength: 300, required: true, disabled: working, autoComplete: 'off', placeholder: 'Weekly planning',
          'aria-invalid': error === 'Enter a meeting title.' ? 'true' : undefined, onChange: e => setTitle(e.target.value) })),
      h(Field, { label: 'Meeting date and time', hint: iso ? `Saved as ${iso} (${zone}). Relative dates in notes are read against this.` : 'Choose a date and time.' },
        h('input', { className: 'mn-input', type: 'datetime-local', value: when, required: true, disabled: working, onChange: e => setWhen(e.target.value),
          'aria-invalid': !iso ? 'true' : undefined })),
      h(Field, { label: 'Recording', hint: 'Audio file up to 512 MB. Choosing a file does not start the upload.' },
        h('input', { ref: picker, className: 'mn-input', type: 'file', accept: 'audio/*,.wav,.WAV', disabled: working,
          onChange: e => { const chosen = e.currentTarget.files?.[0] || null; setFile(chosen); setProgress(null); setError(''); setStatus('') } })),
      file && !working && h('p', { className: 'mn-faint' }, `Selected: ${file.name} · ${fileSize(file.size)}`),
      h('div', { className: 'mn-row' },
        h(Button, { type: 'submit', size: 'sm', disabled: working }, working ? 'Uploading…' : 'Upload and transcribe')),
      progress !== null && h('progress', { className: 'mn-progress', max: 100, value: progress, 'aria-label': 'Upload progress', 'aria-valuetext': `${progress}%` }),
      h('p', { role: 'status', 'aria-live': 'polite', className: 'mn-muted' }, status ? `${status}${progress !== null && phase === 'uploading' ? ` ${progress}% uploaded.` : ''}` : ''),
      error && h(Callout, { tone: 'error', role: 'alert' }, error)),
    h('p', { className: 'mn-faint' }, 'Uploading the same audio again opens the existing meeting instead of transcribing it twice. Transcription continues on the host if you close this page; stopping the Hermes backend can interrupt it.'))
}

// ── Settings ─────────────────────────────────────────────────────────────────

export function StorageSettings({ rest }) {
  const [value, setValue] = useState(null), [error, setError] = useState(''), [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false), [confirmed, setConfirmed] = useState(false)
  useEffect(() => { let live = true
    rest('/storage-settings').then(v => { if (live) setValue(v) }).catch(e => { if (live) setError(errorText(e)) })
    return () => { live = false }
  }, [rest])
  const save = async e => {
    e.preventDefault(); setSaving(true); setError(''); setMessage('')
    try {
      const v = await rest('/storage-settings', { method: 'PUT', body: { directory: value.directory.trim(), compress_playback: value.compress_playback, archive_mode: value.archive_mode, acknowledge_lossy_replacement: confirmed } })
      setValue(v); setConfirmed(false)
      setMessage('Storage settings saved. New uploads use this location. Compression runs after future transcriptions finish. Existing recordings change only when you apply these settings in their Audio storage panel.')
    } catch (e) { setError(errorText(e)) } finally { setSaving(false) }
  }
  return h('section', { className: 'mn-card', 'aria-label': 'Audio storage settings' },
    h('h3', null, 'Audio storage'),
    h('p', { className: 'mn-muted' }, 'Choose a directory on the Hermes backend machine. In Webapp this is the server, not your browser’s computer. Docker must have the directory mounted. Notes and the database stay in the importing profile.'),
    value?.notice && h(Callout, { tone: 'warn' }, value.notice),
    value && h('form', { onSubmit: save },
      h(Field, { label: 'Audio directory on Hermes host', hint: 'Existing absolute path. Leave blank to use profile storage. Each profile gets a separate managed subfolder.' },
        h('input', { className: 'mn-input', value: value.directory, disabled: saving, onChange: e => { setMessage(''); setValue({ ...value, directory: e.target.value }) } })),
      h('p', { className: 'mn-faint' }, `Current saved location: ${value.managed_path}`),
      h('label', { className: 'mn-row' }, h('input', { type: 'checkbox', checked: value.compress_playback, disabled: saving || value.archive_mode === 'opus',
        onChange: e => setValue({ ...value, compress_playback: e.target.checked }) }), 'Losslessly compress the playback copy (FLAC)'),
      h(Field, { label: 'Stored recording', hint: 'The original source file on your recorder/computer is never modified.' },
        h('select', { className: 'mn-input', value: value.archive_mode, disabled: saving, onChange: e => { setConfirmed(false); setValue({ ...value, archive_mode: e.target.value, compress_playback: e.target.value === 'opus' || value.compress_playback }) } },
          h('option', { value: 'original' }, 'Keep uploaded original'),
          h('option', { value: 'opus' }, 'Smaller speech archive · mono Opus, 48 kb/s target'))),
      value.archive_mode === 'opus' && h(Fragment, null,
        h(Callout, { tone: 'warn' }, 'This replaces the plugin’s stored original after verification. Opus is lossy and mono; original stereo detail and metadata cannot be recovered from it. An exact lossless playback copy is kept for citations. Keep your source recording separately if needed.'),
        h('label', { className: 'mn-row' }, h('input', { type: 'checkbox', checked: confirmed, disabled: saving, onChange: e => setConfirmed(e.target.checked) }), 'Allow replacement of stored originals after future transcriptions')),
      btn({ type: 'submit', disabled: saving || (value.archive_mode === 'opus' && !confirmed) }, saving ? 'Saving…' : 'Save storage settings')),
    message && h('p', { role: 'status', className: 'mn-muted' }, message),
    error && h(Callout, { tone: 'error', role: 'alert' }, error))
}

export function RecordingStorage({ meeting, rest }) {
  const [value, setValue] = useState(null), [policy, setPolicy] = useState(null), [error, setError] = useState('')
  const [busy, setBusy] = useState(false), [review, setReview] = useState(false)
  const alive = useRef(true), serial = useRef(0), locked = useRef(false)
  const load = useCallback(async () => {
    const seq = ++serial.current
    try {
      const v = await rest(`/meetings/${meeting.id}/storage`)
      if (alive.current && seq === serial.current) setValue(v)
    } catch (e) { if (alive.current && seq === serial.current) setError(errorText(e)) }
  }, [rest, meeting.id])
  useEffect(() => { alive.current = true; setValue(null); setReview(false); load()
    const timer = setInterval(() => { if (!locked.current && !globalThis.document?.hidden) load() }, POLL_MS)
    return () => { alive.current = false; serial.current++; clearInterval(timer) }
  }, [load])
  const reviewSettings = async () => {
    setError(''); setBusy(true)
    try { const p = await rest('/storage-settings'); if (alive.current) { setPolicy(p); setReview(true) } }
    catch (e) { if (alive.current) setError(errorText(e)) } finally { if (alive.current) setBusy(false) }
  }
  const apply = async () => {
    if (locked.current) return
    locked.current = true; serial.current++; setBusy(true); setError('')
    try {
      await rest(`/meetings/${meeting.id}/storage`, { method: 'POST', body: { apply_current_settings: true, settings: policy } })
      if (alive.current) { setReview(false); await load() }
    } catch (e) { if (alive.current) setError(errorText(e)) }
    finally { locked.current = false; if (alive.current) setBusy(false) }
  }
  const organize = async () => {
    if (locked.current) return
    locked.current = true; serial.current++; setBusy(true); setError('')
    try {
      const result = await rest(`/meetings/${meeting.id}/organize-audio`, { method: 'POST', body: { confirm: true, settings: policy } })
      if (alive.current) { setValue(result); setReview(false) }
    } catch (e) { if (alive.current) setError(errorText(e)) }
    finally { locked.current = false; if (alive.current) setBusy(false) }
  }
  const running = ['queued', 'running'].includes(value?.job?.state)
  if (meeting.recordings?.length) return h('details', { className: 'mn-card' },
    h('summary', null, 'Audio storage'),
    value?.owns_audio
      ? h(Fragment, null,
          h('p', null, 'This combined meeting owns its audio. Original meeting entries can be deleted unless another combination still depends on them.'),
          h('p', { className: 'mn-faint' }, value.directory),
          h('ol', null, ...(value.files || []).map(file => h('li', { key: file }, file))))
      : h('p', null, 'Move audio into a numbered folder so this combined meeting owns its audio. You can then delete the original meeting entries unless another combination needs them. Their notes are not copied.'),
    value?.job && h('p', { role: 'status' }, `Audio move: ${value.job.state}`),
    value?.job?.error && h(Callout, { tone: 'error' }, value.job.error),
    value?.job?.cleanup_pending && h('p', null, 'Older copies are kept while other meetings use them or their storage is unavailable.'),
    !review && btn({ disabled: busy || running || !value, onClick: reviewSettings },
      value?.owns_audio ? 'Review audio relocation' : 'Review numbered audio move'),
    review && policy && h('div', null,
      h('p', null, `Destination: ${policy.managed_path}`),
      h('p', null, 'Creates a new numbered folder and moves audio in the background. Current recording and playback formats are preserved; compression settings are not applied. Keep both storage locations connected until the move and cleanup finish.'),
      btn({ disabled: busy || running, onClick: organize }, 'Confirm audio move'),
      btn({ variant: 'ghost', disabled: busy, onClick: () => setReview(false) }, 'Cancel')),
    error && h(Callout, { tone: 'error', role: 'alert' }, error))
  const size = n => n == null ? 'unavailable' : `${(n / 1048576).toFixed(1)} MiB`
  return h('details', { className: 'mn-card' }, h('summary', null, 'Audio storage'),
    value && !value.combined && h(Fragment, null,
      h('p', { className: 'mn-faint' }, value.directory),
      h('p', null, `${value.archive_mode === 'opus' ? 'Opus speech archive' : 'Uploaded original'}: ${size(value.recording_bytes)} · Playback (${value.playback_format}): ${size(value.playback_bytes)}`),
      value.job && h('p', { role: 'status' }, `Storage: ${value.job.state}${value.job.result ? ` · ${(value.job.result.saved_bytes / 1048576).toFixed(1)} MiB saved` : ''}`),
      value.job?.error && h(Callout, { tone: 'error' }, value.job.error),
      value.job?.cleanup_pending && h('p', { className: 'mn-faint' }, 'The verified replacement is in use. Older copies await cleanup when their storage is available.'),
      !review && btn({ disabled: busy || running || meeting.job.state !== 'completed', onClick: reviewSettings }, 'Review move / compression'),
      review && policy && h('div', null,
        h('p', null, `Destination: ${policy.managed_path}`),
        h('p', null, `Playback: ${policy.compress_playback ? 'lossless FLAC' : 'WAV'}. Recording: ${policy.archive_mode === 'opus' ? 'mono Opus speech archive (lossy)' : 'uploaded original'}.`),
        policy.archive_mode === 'opus' && h(Callout, { tone: 'warn' }, 'Applying this replaces the plugin’s stored original after verification. The source file on your computer is untouched. This cannot restore or preserve the original’s stereo detail or metadata.'),
        h('p', { className: 'mn-faint' }, 'Files are copied and checked before switching locations and removing old managed copies. Playback and combined meetings keep their timeline. Keep both locations mounted until cleanup finishes.'),
        btn({ disabled: busy || running, onClick: apply }, 'Apply reviewed storage settings'),
        btn({ variant: 'ghost', disabled: busy, onClick: () => setReview(false) }, 'Cancel'))),
    error && h(Callout, { tone: 'error', role: 'alert' }, error))
}

export function DeletionCleanup({ rest }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { rest('/deletions').then(setStatus).catch(e => setError(errorText(e))) }, [rest])
  const retry = async () => {
    setBusy(true); setError('')
    try { setStatus(await rest('/deletions/cleanup', { method: 'POST' })) }
    catch (e) { setError(errorText(e)) }
    finally { setBusy(false) }
  }
  return h('section', { className: 'mn-card', 'aria-label': 'Deleted recording cleanup' },
    h('h3', null, 'Deleted recording cleanup'),
    h('p', null, status ? `${status.pending_files} managed files awaiting removal.` : 'Checking file cleanup…'),
    status?.pending_files > 0 && h('p', { className: 'mn-muted' }, 'Reconnect the original storage location and retry. Files that changed or are outside this profile’s storage are kept.'),
    btn({ disabled: busy, onClick: retry }, 'Retry file cleanup'),
    error && h(Callout, { tone: 'error', role: 'alert' }, error))
}

export function DeleteMeeting({ meeting, rest, onDeleted, onBusy = () => {}, disabled = false }) {
  const [review, setReview] = useState(null)
  const [title, setTitle] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = async () => {
    setBusy(true); onBusy(true); setError(''); setConfirmed(false); setTitle('')
    try { setReview(await rest(`/meetings/${meeting.id}/deletion`)) }
    catch (e) { setError(errorText(e)) }
    finally { setBusy(false); onBusy(false) }
  }
  const remove = async e => {
    e.preventDefault()
    if (!review || title.trim() !== review.title.trim() || !confirmed || review.blockers.length) return
    setBusy(true); onBusy(true); setError('')
    try {
      const result = await rest(`/meetings/${meeting.id}/delete`, { method: 'POST', body: { token: review.token, confirm: true } })
      onDeleted(result, meeting.id)
    } catch (e) { setError(errorText(e)) }
    finally { setBusy(false); onBusy(false) }
  }
  return h('section', { 'aria-label': 'Delete meeting' },
    !review && btn({ variant: 'ghost', disabled: disabled || busy, onClick: load }, 'Delete meeting…'),
    review && h('form', { className: 'mn-card', 'aria-label': 'Confirm meeting deletion', onSubmit: remove },
      h('h3', null, `Delete “${review.title}”?`),
      h('p', null, 'Permanently remove this meeting’s transcripts, corrections, notes, action proposals and local handoff receipts. This cannot be undone.'),
      h('p', null, 'Managed audio is removed only when no other meeting uses it. Other meeting entries are kept.'),
      h('p', null, 'The original file on your recorder/computer, Hermes chat history, backups and external calendar events or tasks are not deleted.'),
      ...review.blockers.map((text, i) => h(Callout, { key: i, tone: 'warn' }, text)),
      review.dependencies.length > 0 && h('ul', null, ...review.dependencies.map(d => h('li', { key: d.id }, d.title))),
      h('p', { className: 'mn-faint' }, `${review.counts.analyses} saved notes versions · ${review.counts.action_sets} action sets · ${review.counts.action_handoffs} handoff receipts`),
      h(Field, { label: 'Type the meeting title to confirm' }, h('input', { className: 'mn-input', value: title, disabled: busy, onChange: e => setTitle(e.target.value) })),
      h('label', null, h('input', { type: 'checkbox', checked: confirmed, disabled: busy, onChange: e => setConfirmed(e.target.checked) }), ' I understand this permanently deletes local meeting data.'),
      h('div', { className: 'mn-actions' },
        btn({ type: 'submit', disabled: busy || disabled || !confirmed || title.trim() !== review.title.trim() || review.blockers.length > 0 }, 'Permanently delete meeting'),
        btn({ variant: 'ghost', disabled: busy, onClick: load }, 'Refresh deletion review'),
        btn({ variant: 'ghost', disabled: busy, onClick: () => { setReview(null); setError('') } }, 'Keep meeting'))),
    error && h(Callout, { tone: 'error', role: 'alert' }, error))
}

function XaiSetupGuide() {
  const commands = `(
set -e
HERMES_SOURCE='/absolute/path/to/hermes-agent'
MEETING_PLUGIN='/absolute/path/to/plugins/meeting_notes'
OAUTH_BACKUP_DIR=$(mktemp -d "\${TMPDIR:-/tmp}/meeting-notes-oauth-backup.XXXXXX")
cp "$HERMES_SOURCE/hermes_cli/plugin_provider_requests.py" "$OAUTH_BACKUP_DIR/plugin_provider_requests.py"
python3 "$MEETING_PLUGIN/deploy/hermes-test/install-provider-requests.py" "$HERMES_SOURCE" "$MEETING_PLUGIN/deploy/hermes-test/provider-requests.patch"
printf 'Keep this backup for rollback: %s\\n' "$OAUTH_BACKUP_DIR"
)`
  return h('section', { className: 'mn-card', 'aria-label': 'Set up xAI host support' },
    h('h3', null, 'Set up xAI host support'),
    h('p', null, 'An xAI sign-in and a GitHub plugin install do not add the required host API. This is a separate, experimental Hermes source patch. This page checks support; it does not apply the patch or access your credentials.'),
    h('ol', null,
      h('li', null, h('strong', null, 'Find the backend. '), 'For Webapp, use a terminal on its server, not your browser’s computer. For Desktop, use the machine running Desktop’s selected Hermes backend (local or remote). Identify its Hermes source checkout and the installed Meeting Notes directory. The patch affects every profile using that checkout.'),
      h('li', null, h('strong', null, 'Prepare the host. '), 'Wait for chats, transcriptions and storage jobs to finish. Back up the profile database and all audio folders together. For Docker, ask the host administrator to adapt the linked pinned test-image build for your deployment. If a packaged host has no editable source checkout, ask its administrator for an extended build.'),
      h('li', null, h('strong', null, 'Apply the checked patch to a source installation. '), 'Replace both paths below, then run the commands on the backend. Keep the printed module-backup location. The installer checks the exact reviewed module and patch compatibility. If it refuses a changed or already-patched module, stop; do not bypass the check.',
        h('textarea', { className: 'mn-input', 'aria-label': 'Host patch commands', readOnly: true, rows: 12, value: commands, spellCheck: false })),
      h('li', null, h('strong', null, 'Restart and check again. '), 'Restart that backend and its profile plugin-host processes, then return here and choose Check host support again. Refreshing the page alone does not reload Python; closing Desktop may leave its backend running. If support is still missing, confirm the restarted backend uses the patched checkout.'),
      h('li', null, h('strong', null, 'Connect xAI. '), 'Once host support is detected, sign in through Hermes in the importing profile, save any settings changes, then choose Use this connection. This check does not verify login, subscription entitlement or billing. Your Custom connection is kept.')),
    h('p', { className: 'mn-muted' }, 'To roll back, wait for work to stop, restore the backed-up module and restart the backend. Use Custom while xAI support is unavailable. Recheck host compatibility after Hermes updates.'),
    h('a', { href: 'https://github.com/angel12/hermes-meeting-notes/blob/main/deploy/hermes-test/README.md#source-host-setup', target: '_blank', rel: 'noopener noreferrer' }, 'Host setup and Docker instructions'))
}

export function SettingsForm({ rest }) {
  const [settings, setSettings] = useState(null)
  const [connections, setConnections] = useState(null), [editing, setEditing] = useState('custom')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const [saving, setSaving] = useState(false)
  const [checkingHost, setCheckingHost] = useState(false), [hostCheckError, setHostCheckError] = useState('')
  const hostCheck = useRef(0)
  const checkHost = useCallback(async () => {
    const request = ++hostCheck.current
    setCheckingHost(true); setHostCheckError('')
    try {
      const v = await rest('/connections')
      if (request === hostCheck.current) {
        // A support check must not discard a draft or adopt a newer settings
        // revision. Concurrent settings edits still use the normal save fence.
        setConnections(current => current && { ...current, availability: v.availability })
      }
    } catch (e) {
      if (request === hostCheck.current) {
        setHostCheckError(`Could not check host support: ${errorText(e)}`)
        setConnections(current => current && { ...current, availability: { ...current.availability, xai: { available: false } } })
      }
    } finally { if (request === hostCheck.current) setCheckingHost(false) }
  }, [rest])
  useEffect(() => {
    if (editing === 'xai') checkHost()
    return () => { ++hostCheck.current }
  }, [editing, checkHost])
  useEffect(() => { let live = true
    rest('/connections').then(v => { if (live) { setConnections(v); setEditing(v.active); setSettings(v.connections[v.active]) } }).catch(e => { if (live) setError(errorText(e)) })
    return () => { live = false }
  }, [rest])
  const save = async event => {
    event.preventDefault()
    setSaving(true); setError(''); setSaved('')
    try {
      const v = await rest(`/connections/${editing}`, { method: 'PUT', body: { revision: connections.revision, settings: { endpoint: settings.endpoint.trim(), model: settings.model.trim(), timeout: Number(settings.timeout), max_audio_seconds: Number(settings.max_audio_seconds ?? 3600) } } })
      setConnections(v); setSettings(v.connections[editing])
      setSaved(v.active === editing ? 'Settings saved. New transcription plans use them; normal retries retain their saved settings. Use Retry with current settings to replan unfinished audio.' : 'Connection saved. Your active connection is unchanged. Select Use this connection to switch.')
    } catch (e) { setError(errorText(e)) }
    finally { setSaving(false) }
  }
  const activate = async () => {
    setSaving(true); setError(''); setSaved('')
    try {
      const v = await rest('/connections/active', { method: 'POST', body: { provider: editing, revision: connections.revision } })
      setConnections(v); setSettings(v.connections[editing])
      setSaved('Active connection changed. Both saved connections are kept. Existing transcription plans and normal retries keep their original provider.')
    } catch (e) { setError(errorText(e)) } finally { setSaving(false) }
  }
  const set = key => e => { setSaved(''); setSettings({ ...settings, [key]: e.target.value }) }
  const original = connections?.connections[editing]
  const dirty = settings && (settings.endpoint.trim() !== original?.endpoint || settings.model.trim() !== original?.model ||
    Number(settings.timeout) !== Number(original?.timeout) || Number(settings.max_audio_seconds ?? 3600) !== Number(original?.max_audio_seconds ?? 3600))
  return h('div', { className: 'mn-page' },
    h('header', { className: 'mn-header' },
      h('h2', null, 'Transcription settings'),
      h('p', { className: 'mn-muted' }, 'Keep separate Custom and xAI connections. Only the selected connection is used for new transcription plans.')),
    h(DeletionCleanup, { rest }),
    !settings && !error && h('div', { className: 'mn-card', 'aria-busy': true }, h('p', { className: 'mn-faint' }, 'Loading settings…')),
    settings && h('form', { className: 'mn-card', onSubmit: save, 'aria-label': 'Transcription settings' },
      h('p', { className: 'mn-muted' }, `Active connection: ${connections.active === 'custom' ? 'Custom · NeMo-Speech.cpp' : 'xAI · Hermes OAuth'}`),
      h(Field, { label: 'Connection to configure', hint: 'Choose a saved connection. Save changes before leaving this form; selecting a form does not change the active provider.' },
        h('select', { className: 'mn-input', value: editing, disabled: saving, onChange: e => {
          const name = e.target.value
          setEditing(name); setSettings(connections.connections[name]); setSaved(''); setError(''); setCheckingHost(name === 'xai'); setHostCheckError('')
        } }, h('option', { value: 'custom' }, 'Custom · NeMo-Speech.cpp'), h('option', { value: 'xai' }, 'xAI · existing Hermes OAuth (optional)'))),
      editing === 'xai' && h(Fragment, null,
        h(Callout, { tone: 'info' },
          h('p', { role: 'status' }, checkingHost ? 'Checking xAI host support…' : hostCheckError ? 'Host support could not be verified.' : connections.availability?.xai?.available === true ? 'xAI host support detected. This does not check your login or STT entitlement.' : 'xAI host extension required. Follow the setup steps below to enable this connection.'),
          h('p', null, 'Sign in to xAI through Hermes on the importing backend profile. Audio is sent to xAI; subscription coverage is not guaranteed. No API-key fallback is used. Your Custom connection is kept.'),
          btn({ disabled: saving || checkingHost, onClick: checkHost }, 'Check host support again')),
        hostCheckError && h(Callout, { tone: 'error', role: 'alert' }, hostCheckError),
        !checkingHost && !hostCheckError && connections.availability?.xai?.available !== true && h(XaiSetupGuide)),
      h(Field, { label: 'Server endpoint', hint: 'HTTP(S) base URL, without credentials. Use plain HTTP only on a trusted network.' },
        h('input', { className: 'mn-input', type: 'url', value: settings.endpoint, onChange: set('endpoint'), required: true, disabled: saving || editing === 'xai' })),
      h('div', { className: 'mn-grid2' },
        h(Field, { label: 'Model' }, h('input', { className: 'mn-input', value: settings.model, onChange: set('model'), required: true, disabled: saving })),
        h(Field, { label: 'Request timeout (seconds)', hint: '30 to 7200.' },
          h('input', { className: 'mn-input', type: 'number', min: 30, max: 7200, step: 1, value: settings.timeout, onChange: set('timeout'), required: true, disabled: saving })),
        h(Field, { label: 'Maximum audio per request (minutes)', hint: 'Set this within your provider’s audio limit. This is audio length, not request timeout.' },
          h('input', { className: 'mn-input', type: 'number', min: 1 / 60, max: 1440, step: 'any', value: (settings.max_audio_seconds ?? 3600) / 60,
            onChange: e => { setSaved(''); setSettings({ ...settings, max_audio_seconds: Math.round(Number(e.target.value) * 60) }) }, required: true, disabled: saving }))),
      h('p', { className: 'mn-muted' }, 'Long recordings are cut at time boundaries, transcribed sequentially, and assembled into one meeting. Review speech near cut points and merge matching speakers across chunks. The original recording stays intact.'),
      h('div', { className: 'mn-row' }, h(Button, { type: 'submit', size: 'sm', disabled: saving }, saving ? 'Saving…' : 'Save settings'),
        connections.active !== editing && btn({ disabled: saving || dirty || (editing === 'xai' && checkingHost) || connections.availability?.[editing]?.available !== true, onClick: activate }, 'Use this connection')),
      dirty && h('p', { className: 'mn-faint' }, 'Save changes before switching the active connection.'),
      h('p', { role: 'status', 'aria-live': 'polite', className: 'mn-muted' }, saved)),
    error && h(Callout, { tone: 'error', role: 'alert' }, error),
    h(StorageSettings, { rest }),
    h(Callout, { tone: 'info', title: 'Host requirements' },
      h('ul', { className: 'mn-steps' },
        h('li', null, 'Custom uses MEETING_NOTES_ASR_KEY in Hermes secrets. Optional xAI uses the existing Hermes sign-in on the importing backend profile. Credentials never reach this page.'),
        h('li', null, 'OpenAI / ChatGPT OAuth diarized file transcription is not yet verified. Existing OAuth models can still generate notes in Hermes chat.'),
        h('li', null, 'ffmpeg and ffprobe must be installed on the Hermes host; optional archives need FLAC and libopus support.'),
        h('li', null, 'Use a single-profile host. Run one host per transcription server for now.'))))
}

// ── Meeting workspace ────────────────────────────────────────────────────────

export function MeetingPlayer({ meetingId, rest, seekRequest, onPosition, suspended, onStart }) {
  const [timeline, setTimeline] = useState(null)
  const [peaks, setPeaks] = useState([])
  const [error, setError] = useState('')
  const [waveError, setWaveError] = useState('')
  const [position, setPosition] = useState(0)
  const [running, setRunning] = useState(false)
  const [loading, setLoading] = useState(false)
  const [rate, setRate] = useState(1)
  const audio = useRef(null), generation = useRef(0), url = useRef(null)
  const offset = useRef(0), length = useRef(0), next = useRef(null), desired = useRef(false)
  const total = useRef(0), speed = useRef(1)
  const scrubTimer = useRef(null), scrubTarget = useRef(null), handledSeek = useRef(null)
  const wavePath = useMemo(() => peaks.map((p,i) => `M${i*1200/peaks.length},${40-p*38}v${p*76}`).join(' '), [peaks])
  const fetchWindow = start => rest(`/meetings/${meetingId}/audio?start=${start}&duration=30`, { timeoutMs: 120000 })
  const stop = () => { desired.current = false; audio.current?.pause(); setRunning(false) }
  const load = async (start, autoplay) => {
    const seq = ++generation.current
    desired.current = autoplay
    if (autoplay) onStart?.()
    audio.current?.pause(); setRunning(false); setLoading(true); setError('')
    start = Math.max(0, Math.min(start, Math.max(0, total.current - .001)))
    try {
      const pending = next.current
      next.current = null
      const result = await (pending?.start === start ? pending.promise : fetchWindow(start))
      if (generation.current !== seq) return
      const bytes = Uint8Array.from(atob(result.base64), c => c.charCodeAt(0))
      const newUrl = URL.createObjectURL(new Blob([bytes], { type: result.mime }))
      if (url.current) URL.revokeObjectURL(url.current)
      url.current = newUrl; offset.current = start; length.current = Math.min(30, total.current-start)
      setPosition(start); onPosition(start)
      const element = audio.current
      element.src = newUrl; element.playbackRate = speed.current
      setLoading(false)
      if (desired.current) {
        try { await element.play() } catch { if (generation.current === seq) { stop(); setError('Press Play to allow audio playback.') } }
      }
      if (generation.current !== seq) return
      const following = start + length.current
      if (following < total.current) {
        // One bounded look-ahead; catch immediately so a failed prefetch never
        // becomes an unhandled rejection. A boundary failure offers explicit retry.
        const promise = fetchWindow(following).then(value => ({value}), error => ({error}))
        next.current = {start:following, promise:promise.then(r => { if(r.error) throw r.error; return r.value })}
        next.current.promise.catch(() => {})
      }
    } catch (e) {
      if (generation.current !== seq) return
      stop(); setLoading(false)
      if (url.current) URL.revokeObjectURL(url.current)
      url.current = null
      audio.current?.removeAttribute('src')
      setPosition(start); onPosition(start)
      setError(`Playback unavailable: ${errorText(e)}`)
    }
  }
  useEffect(() => {
    let live = true
    const element = audio.current
    rest(`/meetings/${meetingId}/playback`).then(data => {
      if (!live) return
      if (!(data.duration > 0) || !Number.isFinite(data.duration)) throw new Error('Audio has no playable duration')
      total.current = data.duration; setTimeline(data)
    }).catch(e => { if(live) setError(errorText(e)) })
    rest(`/meetings/${meetingId}/waveform`, {timeoutMs:120000}).then(data => {
      if(live) setPeaks(data.peaks || [])
    }).catch(() => { if(live) setWaveError('Waveform unavailable. Playback and seeking remain available.') })
    return () => { clearTimeout(scrubTimer.current); live=false; generation.current++; desired.current=false; element?.pause(); if(url.current) URL.revokeObjectURL(url.current); url.current=null }
  }, [meetingId, rest])
  useEffect(() => { if(suspended) stop() }, [suspended])
  useEffect(() => {
    if(seekRequest && timeline && handledSeek.current !== seekRequest) {
      handledSeek.current = seekRequest
      clearTimeout(scrubTimer.current); scrubTarget.current=null
      load(seekRequest.start, true)
    }
  }, [seekRequest, timeline])
  const toggle = async () => {
    if(loading) { generation.current++; setLoading(false); stop(); return }
    if(desired.current) { stop(); return }
    if(!url.current || position >= total.current) { load(position >= total.current ? 0 : position, true); return }
    desired.current=true
    onStart?.()
    const seq = generation.current
    try { await audio.current.play() } catch { if(generation.current === seq) { stop(); setError('Audio could not start. Seek to retry.') } }
  }
  const commitSeek = () => {
    clearTimeout(scrubTimer.current)
    const value = scrubTarget.current
    scrubTarget.current = null
    if(value !== null) load(value, desired.current)
  }
  const seek = value => {
    scrubTarget.current = Number(value); setPosition(Number(value))
    clearTimeout(scrubTimer.current)
    scrubTimer.current = setTimeout(commitSeek, 250)
  }
  return h('section', {className:'mn-meeting-player mn-card', 'aria-label':'Meeting audio player'},
    h('div', {className:'mn-wave', onClick:e => {
      if(!timeline) return
      const rect=e.currentTarget.getBoundingClientRect()
      if(rect.width) seek((e.clientX-rect.left)/rect.width*timeline.duration)
    }, 'aria-hidden':true},
      h('svg', {viewBox:'0 0 1200 80', preserveAspectRatio:'none'},
        h('line',{x1:0,x2:1200,y1:40,y2:40,stroke:'currentColor',strokeWidth:.5}),
        h('path',{d:wavePath,stroke:'currentColor',fill:'none',strokeWidth:Math.max(1,1200/Math.max(1,peaks.length)*.7)}),
        h('line',{x1:timeline?position/timeline.duration*1200:0,x2:timeline?position/timeline.duration*1200:0,y1:0,y2:80,stroke:'var(--ui-accent)',strokeWidth:3}))),
    h('div',{className:'mn-player-controls'},
      btn({onClick:toggle,disabled:!timeline,'aria-label':loading?'Cancel audio loading':running?'Pause meeting audio':'Play meeting audio'},loading?'Cancel buffering':running?'Pause':'Play'),
      h('span',{className:'mn-nowrap'},`${clock(position)} / ${timeline?clock(timeline.duration):'…'}`),
      h('input',{type:'range',min:0,max:timeline?.duration || 1,step:.1,value:position,disabled:!timeline,'aria-label':'Seek meeting audio',onChange:e=>seek(e.target.value),onPointerUp:commitSeek,onKeyUp:commitSeek,onBlur:commitSeek}),
      h('select',{'aria-label':'Playback speed',value:rate,onChange:e=>{const v=Number(e.target.value);speed.current=v;setRate(v);if(audio.current)audio.current.playbackRate=v}},...[.75,1,1.25,1.5,2].map(v=>h('option',{key:v,value:v},`${v}×`)))),
    h('audio',{ref:audio,preload:'auto','aria-label':'Full meeting audio',onPlay:()=>setRunning(true),onPause:()=>setRunning(false),
      onTimeUpdate:()=>{if(scrubTarget.current !== null)return;const time=Math.min(total.current,offset.current+(audio.current?.currentTime || 0));setPosition(time);onPosition(time)},
      onEnded:()=>{const end=offset.current+length.current;if(end<total.current && desired.current)load(end,true);else{stop();setPosition(total.current);onPosition(total.current)}},
      onError:()=>{stop();setError('Audio could not be decoded. Seek to retry.')}}),
    error && h('p',{role:'alert'},error), waveError && h('p',{className:'mn-faint'},waveError))
}

function Player({ playing, onClose }) {
  if (!playing) return null
  return h('div', { className: 'mn-player', role: 'region', 'aria-label': 'Audio playback' },
    h('div', { style: { display: 'grid', minWidth: 0 } },
      h('strong', { style: { fontSize: 12.5 } }, playing.label),
      h('span', { className: 'mn-faint' }, `${clock(playing.start)} – ${clock(playing.start + playing.duration)}${playing.clipped ? ' · first 30 s of this passage' : ''}`)),
    h('audio', { src: playing.url, controls: true, autoPlay: true, 'aria-label': `Audio from ${clock(playing.start)}` }),
    btn({ variant: 'ghost', size: 'xs', onClick: onClose, 'aria-label': 'Close player' }, 'Close'))
}

function Tabs({ tabs, value, onChange, label }) {
  const refs = useRef({})
  const keyDown = e => {
    const i = tabs.findIndex(t => t.id === value)
    let next = null
    if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length]
    if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length]
    if (e.key === 'Home') next = tabs[0]
    if (e.key === 'End') next = tabs[tabs.length - 1]
    if (next) { e.preventDefault(); onChange(next.id); refs.current[next.id]?.focus() }
  }
  return h('div', { className: 'mn-tabs', role: 'tablist', 'aria-label': label, onKeyDown: keyDown },
    ...tabs.map(t => h('button', {
      key: t.id, ref: el => { refs.current[t.id] = el }, type: 'button', role: 'tab', className: 'mn-tab', id: `mn-tab-${t.id}`,
      'aria-selected': t.id === value ? 'true' : 'false', 'aria-controls': `mn-panel-${t.id}`, tabIndex: t.id === value ? 0 : -1,
      onClick: () => onChange(t.id)
    }, t.label, t.count != null && h('span', { className: 'mn-count' }, t.count), t.badge)))
}

function StatusPanel({ meeting, busy, act }) {
  const info = meeting.job.local_recovery && ['queued', 'preparing'].includes(meeting.job.state)
    ? { label: meeting.job.state === 'queued' ? 'Recovery queued' : 'Recovering saved response', active: true, detail: 'Parsing saved responses on the host. This recovery does not send audio to the provider. After a host restart, use Start queued work if needed.' }
    : stateInfo(meeting.job.state)
  if (meeting.job.state === 'completed' && !meeting.job.error) return null
  const retry = RETRYABLE.includes(meeting.job.state)
  return h(Callout, { tone: retry ? 'error' : 'info', title: info.label, role: retry ? 'alert' : undefined },
    info.detail && h('p', null, info.detail),
    meeting.transcription_chunks?.length > 0 && h('p', { role: 'status' },
      `${meeting.transcription_chunks.filter(c => c.state === 'completed').length} of ${meeting.transcription_chunks.length} audio chunks completed. Retry keeps completed chunks and resumes unfinished work.`),
    meeting.job.error && h('p', { className: 'mn-muted' }, `Details: ${meeting.job.error}`),
    info.active && meeting.job.state !== 'queued' && h('p', { className: 'mn-faint' }, 'You can close this page; the host keeps working. Stopping the Hermes backend can interrupt it.'),
    h('div', { className: 'mn-row' },
      retry && meeting.can_recover_response && btn({ disabled: busy, onClick: () => act('Local recovery requested. No audio will be sent.', rest => rest(`/meetings/${meeting.id}/recover-response`, { method: 'POST' })) }, 'Recover saved response'),
      retry && btn({ variant: 'default', disabled: busy, onClick: () => act('Retry requested. Completed chunks are retained.', rest => rest(`/meetings/${meeting.id}/retry`, { method: 'POST' })) }, 'Retry transcription'),
      retry && meeting.transcription_chunks?.length > 0 && btn({ disabled: busy, onClick: () => act('Retry requested with current settings. Completed chunks are retained; unfinished audio was replanned.', rest => rest(`/meetings/${meeting.id}/retry`, { method: 'POST', body: { use_current_settings: true } })) }, 'Retry with current settings'),
      meeting.job.state === 'queued' && btn({ disabled: busy, onClick: () => act('Queued work started.', rest => rest('/worker/start', { method: 'POST' })) }, 'Start queued work')))
}

// Next segment by this speaker after `after` seconds, wrapping around.
export function nextTurnOf(segments, participant, after = -1) {
  const mine = segments.filter(s => s.participant === participant)
  return mine.find(s => s.start > after) || mine[0] || null
}

function TextEditor({ segment, busy, onSave, onCancel }) {
  const [text, setText] = useState(segment.text)
  const ref = useRef(null)
  useEffect(() => { ref.current?.focus(); ref.current?.select?.() }, [])
  const value = text.trim()
  const save = () => { if (value && value !== segment.text) onSave(value); else onCancel() }
  return h('form', { className: 'mn-inline-edit', 'aria-label': `Edit text at ${clock(segment.start)}`, onSubmit: e => { e.preventDefault(); save() } },
    h('textarea', { ref, className: 'mn-input', 'aria-label': 'Segment text', value: text, disabled: busy,
      rows: Math.min(8, Math.max(2, Math.ceil(text.length / 90))), onChange: e => setText(e.target.value),
      onKeyDown: e => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel() }
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); save() }
      } }),
    h('div', { className: 'mn-row' },
      h(Button, { type: 'submit', size: 'xs', disabled: busy || !value || value === segment.text }, 'Save'),
      btn({ variant: 'ghost', size: 'xs', onClick: onCancel, disabled: busy }, 'Cancel'),
      h('span', { className: 'mn-faint' }, 'Enter saves · Shift+Enter new line · Esc cancels. The original stays on file.')))
}

function SpeakerPicker({ segment, participants, busy, onPick, onCancel }) {
  const ref = useRef(null)
  useEffect(() => { ref.current?.focus() }, [])
  return h('div', { className: 'mn-picker', role: 'group', 'aria-label': `Reassign segment at ${clock(segment.start)}`,
    onKeyDown: e => { if (e.key === 'Escape') { e.preventDefault(); onCancel() } } },
  h('span', { className: 'mn-faint' }, 'Who said this?'),
  ...participants.map((p, i) => h('button', { key: p.id, ref: i === 0 ? ref : undefined, type: 'button', className: 'mn-chip', disabled: busy,
    'aria-pressed': p.id === segment.participant ? 'true' : 'false',
    onClick: () => (p.id === segment.participant ? onCancel() : onPick(p.id)) },
  h('span', { className: 'mn-dot', 'aria-hidden': true, style: { '--mn-c': speakerColor(p.id, participants) } }), speakerName(p.id, p.name))),
  btn({ variant: 'ghost', size: 'xs', onClick: onCancel }, 'Cancel'))
}

function SegmentRow({ s, participants, corrected, active, flash, busy, play, correct, editing, setEditing }) {
  const label = speakerName(s.participant, s.name)
  return h('li', { className: 'mn-seg', 'data-seg': s.id, 'data-active': active ? 'true' : undefined, 'data-flash': flash ? 'true' : undefined },
    h('div', { className: 'mn-seg-head' },
      editing === `speaker:${s.id}`
        ? h(SpeakerPicker, { segment: s, participants, busy, onCancel: () => setEditing(null),
          onPick: async who => { if (await correct([['speaker', s.id, who]])) setEditing(null) } })
        : h('button', { type: 'button', className: 'mn-chip', disabled: busy, onClick: () => setEditing(`speaker:${s.id}`),
          'aria-label': `${label} at ${clock(s.start)}. Change speaker` },
        h('span', { className: 'mn-dot', 'aria-hidden': true, style: { '--mn-c': speakerColor(s.participant, participants) } }), label),
      h('button', { type: 'button', className: 'mn-time', onClick: () => play(s), 'aria-label': `Play from ${clock(s.start)}, ${label}` }, `▶ ${clock(s.start)}`),
      s.source && h('span', { className: 'mn-faint' }, `Recording ${s.source.part} · ${clock(s.source.start)}`),
      s.chunk && h('span', { className: 'mn-faint' }, `Audio chunk ${s.chunk.index}`),
      corrected?.has('speaker') && h(Badge, { variant: 'outline' }, 'Speaker corrected'),
      corrected?.has('text') && h(Badge, { variant: 'outline' }, 'Text edited'),
      editing !== `text:${s.id}` && h('span', { className: 'mn-seg-actions' },
        btn({ variant: 'ghost', size: 'xs', disabled: busy, onClick: () => setEditing(`text:${s.id}`), 'aria-label': `Edit text at ${clock(s.start)}` }, 'Edit text'))),
    editing === `text:${s.id}`
      ? h(TextEditor, { segment: s, busy, onCancel: () => setEditing(null), onSave: async text => { if (await correct([['text', s.id, text]])) setEditing(null) } })
      : h('p', { className: 'mn-seg-text', onDoubleClick: () => !busy && setEditing(`text:${s.id}`) }, s.text))
}

function SpeakerRow({ person, participants, total, busy, play, correct, onJump }) {
  const [mode, setMode] = useState(null) // rename | merge
  const [name, setName] = useState(person.name || '')
  const others = participants.filter(p => p.id !== person.id && p.mergeable)
  const [into, setInto] = useState(others[0]?.id || '')
  useEffect(() => { if (!others.some(o => o.id === into)) setInto(others[0]?.id || '') }, [participants])
  const label = speakerName(person.id, person.name)
  const share = total > 0 ? Math.round(person.seconds / total * 100) : 0
  const target = others.find(o => o.id === into)
  const rename = async () => {
    const value = name.trim()
    if (!value || value === person.name) { setMode(null); return }
    if (await correct([['rename', person.id, value]])) setMode(null)
  }
  return h('li', { className: 'mn-spk', 'aria-label': label },
    h('div', { className: 'mn-spk-main' },
      h('span', { className: 'mn-dot mn-dot-lg', 'aria-hidden': true, style: { '--mn-c': speakerColor(person.id, participants) } }),
      h('div', { className: 'mn-spk-name' },
        mode === 'rename'
          ? h('form', { onSubmit: e => { e.preventDefault(); rename() } },
            h('input', { className: 'mn-input mn-input-sm', 'aria-label': `Name for ${label}`, value: name, maxLength: 200, autoFocus: true, disabled: busy,
              onFocus: e => e.target.select(), onChange: e => setName(e.target.value),
              onKeyDown: e => { if (e.key === 'Escape') { e.preventDefault(); setName(person.name || ''); setMode(null) } } }),
            h('span', { className: 'mn-faint' }, 'Enter saves · Esc cancels'))
          : h('button', { type: 'button', className: 'mn-name-btn', disabled: busy, onClick: () => { setName(person.name || ''); setMode('rename') }, 'aria-label': `Rename ${label}` }, label),
        h('span', { className: 'mn-faint' }, `${spokenDuration(person.seconds)} · ${share}% · ${person.segments} ${person.segments === 1 ? 'turn' : 'turns'}${person.name ? ` · label ${person.id}` : ''}`)),
      h('div', { className: 'mn-spk-tools' },
        btn({ variant: 'ghost', size: 'xs', onClick: () => play(person.first), 'aria-label': `Play from first turn of ${label}` }, '▶'),
        btn({ variant: 'ghost', size: 'xs', onClick: () => onJump(person.id), 'aria-label': `Next turn by ${label}` }, 'Next'),
        person.mergeable && person.mergeSegments > 0 && others.length > 0 && btn({ variant: 'ghost', size: 'xs', disabled: busy, 'aria-expanded': mode === 'merge', onClick: () => setMode(mode === 'merge' ? null : 'merge'), 'aria-label': `Merge ${label} into another speaker` }, 'Merge…'))),
    mode === 'merge' && h('form', { className: 'mn-merge', onSubmit: async e => { e.preventDefault(); if (into && await correct([['merge', person.id, into]])) setMode(null) } },
      h(Field, { label: `Merge ${label} into` },
        h('select', { className: 'mn-input mn-input-sm', value: into, onChange: e => setInto(e.target.value), disabled: busy },
          ...others.map(o => h('option', { key: o.id, value: o.id }, speakerName(o.id, o.name))))),
      target && h('p', { className: 'mn-faint' }, `${person.mergeSegments} ${person.mergeSegments === 1 ? 'turn' : 'turns'} (${spokenDuration(person.mergeSeconds)} of transcribed speech) become ${speakerName(target.id, target.name)}'s, including turns you moved to this speaker. You can undo this.`),
      h('div', { className: 'mn-row' },
        h(Button, { type: 'submit', size: 'xs', disabled: busy || !into }, 'Merge'),
        btn({ variant: 'ghost', size: 'xs', onClick: () => setMode(null) }, 'Cancel'))))
}

function SpeakersPanel({ meeting, participants, busy, play, correct, onJump, onClose }) {
  const corrections = meeting.corrections || []
  const undone = new Set(corrections.filter(c => c.kind === 'undo').map(c => c.value))
  const history = corrections.filter(c => c.kind !== 'undo').slice().reverse()
  const total = participants.reduce((sum, p) => sum + p.seconds, 0)
  const nameOf = label => {
    const p = participants.find(x => x.id === label)
    return p ? speakerName(p.id, p.name) : `Participant ${label}`
  }
  return h('aside', { className: 'mn-panel', 'aria-label': 'Speakers' },
    h('div', { className: 'mn-spread' },
      h('h3', { className: 'mn-panel-title' }, 'Speakers', h('span', { className: 'mn-count' }, participants.length)),
      onClose && btn({ variant: 'ghost', size: 'xs', className: 'mn-drawer-only', onClick: onClose, 'aria-label': 'Close speakers panel' }, 'Close')),
    h('p', { className: 'mn-faint' }, 'Anonymous voices from diarization. Merge labels that are one person; names are your own labels.'),
    h('p', { className: 'mn-faint' }, 'Times estimate speech from transcript segments. Percentages show each speaker’s share of that time.'),
    h('ul', { className: 'mn-spk-list' }, ...participants.map(p => h(SpeakerRow, { key: p.id, person: p, participants, total, busy, play, correct, onJump }))),
    h('h3', { className: 'mn-panel-title' }, 'Changes', h('span', { className: 'mn-count' }, `rev ${meeting.revision}`)),
    history.length === 0 ? h('p', { className: 'mn-faint' }, 'No corrections yet. The original transcript is always kept.')
      : h('ul', { className: 'mn-history', 'aria-label': 'Correction history' }, ...history.map(c => h('li', { key: c.id, 'data-undone': undone.has(c.id) ? 'true' : undefined },
        h('span', { className: 'mn-history-text' }, describeCorrection(c, meeting.segments, nameOf)),
        undone.has(c.id) ? h(Badge, { variant: 'muted' }, 'Undone')
          : btn({ size: 'xs', variant: 'ghost', disabled: busy, onClick: () => correct([['undo', '', c.id]]), 'aria-label': `Undo: ${describeCorrection(c, meeting.segments, nameOf)}` }, 'Undo')))))
}

function TranscriptPanel({ meeting, participants, cursor, activeId, playing, focusId, setFocusId, busy, play, correct }) {
  const [query, setQuery] = useState('')
  const [speaker, setSpeaker] = useState('')
  const [editing, setEditing] = useState(null) // 'text:<id>' | 'speaker:<id>'
  const [drawer, setDrawer] = useState(false)
  const [follow, setFollow] = useState(true)
  const listRef = useRef(null)
  const corrected = useMemo(() => {
    const map = {}
    for (const c of activeCorrections(meeting.corrections)) if (c.kind === 'text' || c.kind === 'speaker') (map[c.target] ||= new Set()).add(c.kind)
    return map
  }, [meeting.corrections])
  const latest = activeCorrections(meeting.corrections).at(-1)
  useEffect(() => { if (speaker && !participants.some(p => p.id === speaker)) setSpeaker('') }, [participants, speaker])
  useEffect(() => { if (focusId) { setQuery(''); setSpeaker('') } }, [focusId])
  useEffect(() => {
    if (!focusId || query || speaker) return
    const el = listRef.current?.querySelector(`[data-seg="${CSS_ESCAPE(focusId.id)}"]`)
    if (el) { el.scrollIntoView?.({ block: 'center', behavior: 'smooth' }); el.querySelector('.mn-time')?.focus({ preventScroll: true }) }
  }, [focusId, query, speaker])
  useEffect(() => {
    if (!follow || editing || query || speaker || !activeId) return
    const pane = listRef.current, row = pane?.querySelector(`[data-seg="${CSS_ESCAPE(activeId)}"]`)
    if (pane && row) pane.scrollTo?.({top: pane.scrollTop + row.getBoundingClientRect().top - pane.getBoundingClientRect().top - pane.clientHeight/3, behavior:'smooth'})
  }, [activeId, follow, editing, query, speaker])
  const jump = participant => {
    const after = Math.max(cursor.current, focusId ? (meeting.segments.find(s => s.id === focusId.id)?.start ?? -1) : -1)
    const next = nextTurnOf(meeting.segments, participant, after)
    if (next) { setFocusId({ id: next.id, at: Date.now() }); setDrawer(false) }
  }
  const q = query.trim().toLowerCase()
  const shown = meeting.segments.filter(s => (!speaker || s.participant === speaker) && (!q || s.text.toLowerCase().includes(q)))
  const describe = c => describeCorrection(c, meeting.segments, label => { const p = participants.find(x => x.id === label); return p ? speakerName(p.id, p.name) : `Participant ${label}` })
  return h('div', { className: 'mn-editor', 'data-drawer': drawer ? 'open' : undefined },
    h('div', { className: 'mn-editor-main' },
      h('div', { className: 'mn-toolbar', role: 'toolbar', 'aria-label': 'Transcript tools' },
        h('input', { className: 'mn-input mn-input-sm mn-search', type: 'search', value: query, placeholder: 'Search transcript', 'aria-label': 'Search transcript', onChange: e => setQuery(e.target.value) }),
        h('select', { className: 'mn-input mn-input-sm mn-filter', value: speaker, 'aria-label': 'Show speaker', onChange: e => setSpeaker(e.target.value) },
          h('option', { value: '' }, 'All speakers'),
          ...participants.map(p => h('option', { key: p.id, value: p.id }, speakerName(p.id, p.name)))),
        h('span', { className: 'mn-grow' }),
        h('label', null, h('input', {type:'checkbox',checked:follow,onChange:e=>setFollow(e.target.checked)}), ' Follow audio'),
        busy && h('span', { className: 'mn-faint', role: 'status' }, 'Saving…'),
        btn({ variant: 'ghost', disabled: busy || !latest, onClick: () => correct([['undo', '', latest.id]]),
          'aria-label': latest ? `Undo last change: ${describe(latest)}` : 'Undo last change', title: latest ? describe(latest) : 'Nothing to undo' }, 'Undo'),
        btn({ className: 'mn-drawer-only', 'aria-expanded': drawer, onClick: () => setDrawer(!drawer) }, `Speakers ${participants.length}`)),
      h('p', { className: 'mn-faint', role: 'status', 'aria-live': 'polite' },
        shown.length === meeting.segments.length ? `${meeting.segments.length} turns · click a speaker name to reassign, double-click text to edit`
          : `Showing ${shown.length} of ${meeting.segments.length} turns`),
      shown.length === 0 && h('p', { className: 'mn-muted' }, 'No turns match.'),
      h('ol', { className: 'mn-transcript', ref: listRef, tabIndex:0, 'aria-label':'Scrollable transcript' },
        ...shown.map(s => h(SegmentRow, { key: s.id, s, participants, corrected: corrected[s.id], active: activeId === s.id, flash: focusId?.id === s.id,
          busy, play, correct, editing, setEditing })))),
    h(SpeakersPanel, { meeting, participants, busy, play, correct, onJump: jump, onClose: () => setDrawer(false) }))
}

// `CSS.escape` is standard in browsers; ids here are hex + ':s00001' anyway.
const CSS_ESCAPE = value => (globalThis.CSS && globalThis.CSS.escape ? globalThis.CSS.escape(value) : String(value).replace(/["\\]/g, '\\$&'))


export async function analysisChatRoute(hostApi, profile, connectionId) {
  if (!profile || !hostApi?.profileRoutes || !hostApi?.newChat) throw new Error('Open a Hermes chat in the current profile and paste the request below.')
  // Desktop route names can alias the backend's profile name. Capture the
  // current UI identity, then verify the route maps it to this meeting's owner.
  const openProfile = hostApi.state?.profile?.get ? (hostApi.state.profile.get() || 'default') : null
  const checkCurrent = () => {
    if (openProfile !== null && (hostApi.state.profile.get() || 'default') !== openProfile)
      throw new Error('The open profile changed. Reopen this meeting in its owning profile before starting a session.')
    if (hostApi.state?.connectionId?.get && (hostApi.state.connectionId.get() ?? null) !== (connectionId ?? null))
      throw new Error('The open connection changed. Reopen this meeting before starting a session.')
  }
  checkCurrent()
  const routes = await hostApi.profileRoutes()
  checkCurrent()
  const candidates = routes.filter(r => r.targetProfile === profile &&
    (openProfile === null || (r.profile || r.targetProfile) === openProfile) &&
    (connectionId ? r.connectionId === connectionId : r.mode === 'local'))
  if (candidates.length !== 1) throw new Error('The meeting’s profile is not uniquely reachable on this connection. Reopen the meeting in its owning profile, or copy the request into that profile’s chat.')
  return candidates[0]
}

// newChat returns before React mounts/resets the fresh composer. Only address
// 'new', wait for the selected gateway, and verify the text survived that reset.
export async function fillNotesDraft(hostApi, text, route, {
  wait = () => new Promise(resolve => setTimeout(resolve, 100)), attempts = 100
} = {}) {
  if (!hostApi.composer?.insertText) return false
  const initialHash = globalThis.location?.hash
  for (let attempt = 0; attempt < attempts; attempt++) {
    await wait()
    if (globalThis.location?.hash !== initialHash) return false
    if (route && hostApi.state?.profile && (hostApi.state.profile.get() || 'default') !== (route.profile || route.targetProfile)) continue
    if (route && hostApi.state?.connectionId && hostApi.state.connectionId.get() !== route.connectionId) continue
    const draft = await hostApi.composer.getDraft?.('new')
    if (draft === text) return true
    if (draft) return false // Never append to text the user has started typing.
    if (!await hostApi.composer.insertText('new', text)) continue
    await wait()
    if (!hostApi.composer.getDraft) return true
    const saved = await hostApi.composer.getDraft('new')
    if (saved === text) return true
    if (saved) return false
  }
  return false
}

async function copyNotesRequest(text) {
  try {
    await globalThis.navigator.clipboard.writeText(text)
    return true
  } catch { return false }
}

export function RequestBox({ meeting, hasNotes, hostApi = host, requestText, title, description, inputLabel, hint }) {
  const text = requestText || notesRequest(meeting)
  const ref = useRef(null)
  const [copied, setCopied] = useState('')
  const [opening, setOpening] = useState(false)
  const connection = useRef(hostApi?.state?.connectionId?.get?.() ?? null)
  const starting = useRef(false)
  const openProfile = async () => {
    if (starting.current) return
    starting.current = true
    setOpening(true)
    setCopied('')
    let opened = false
    let copiedRequest = false
    try {
      const profile = meeting.owner_profile
      if (!hostApi?.newChat) throw new Error('This Hermes version cannot open a session. Copy the request below into a new chat.')
      const route = profile ? await analysisChatRoute(hostApi, profile, connection.current) : undefined
      if (!hostApi.composer?.insertText) copiedRequest = await copyNotesRequest(text)
      if (!hostApi.composer?.insertText && !copiedRequest) {
        throw new Error('Copy the request below, then open a new Hermes chat. This host cannot fill the chat automatically.')
      }
      await hostApi.newChat(route)
      opened = true
      const inserted = await fillNotesDraft(hostApi, text, route)
      if (!inserted && !copiedRequest) copiedRequest = await copyNotesRequest(text)
      const message = inserted
        ? (requestText ? 'Hermes session ready. Review and send the prepared request.' : 'Summarization session ready. Send the request to generate and save notes.')
        : copiedRequest
          ? (requestText ? 'New session opened. Paste the copied request, review it and send.' : 'New session opened. Paste the copied request and send it to generate notes.')
          : `New session opened, but the request could not be filled. Return to ${requestText ? 'Action Items' : 'Notes'} to copy the request.`
      setCopied(message)
      hostApi.notify?.({ kind: inserted || copiedRequest ? 'info' : 'error', message })
    } catch (e) {
      if (!copiedRequest) copiedRequest = await copyNotesRequest(text)
      const message = `${copiedRequest ? 'Request copied. Paste it into a new chat in the meeting’s owning profile. ' : ''}${errorText(e)}`
      setCopied(message)
      if (opened) hostApi.notify?.({ kind: 'error', message })
    } finally {
      starting.current = false
      setOpening(false)
    }
  }
  const copy = async () => {
    try {
      if (!globalThis.navigator?.clipboard?.writeText) throw new Error('unavailable')
      await globalThis.navigator.clipboard.writeText(text)
      setCopied('Copied. Paste it into a Hermes chat.')
    } catch {
      ref.current?.focus(); ref.current?.select()
      setCopied('Copying is not available here. The request is selected; copy it with your keyboard.')
    }
  }
  return h('div', { className: 'mn-card' },
    h('div', { className: 'mn-spread' },
      h('strong', null, title || (hasNotes ? 'Regenerate notes in Hermes chat' : 'Generate notes in Hermes chat')),
      btn({ size: 'xs', onClick: copy }, 'Copy request'),
      btn({ size: 'sm', variant: 'default', onClick: openProfile, disabled: opening }, opening ? 'Opening…' : requestText ? 'Start Hermes session' : 'Start summarization session')),
    h('p', { className: 'mn-muted' }, description || `Start a new session, then send the prepared request. The meeting_notes:analyze skill loads the transcript into your Hermes chat. Hermes writes the notes in that session and saves them here with checked source quotes.${meeting.owner_profile ? ` This session uses the open Hermes profile (${meeting.owner_profile}) and its model, instructions and tools.` : ''}`),
    h('input', { ref, className: 'mn-input', readOnly: true, value: text, 'aria-label': inputLabel || 'Notes request to paste into Hermes chat', onFocus: e => e.target.select(), style: { fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 12 } }),
    h('p', { className: 'mn-faint', role: 'status', 'aria-live': 'polite' }, copied || hint || 'Review speakers first. The skill reads the current transcript and checks its revision before saving.'))
}

function Citation({ citation, source, participants, play, onShow, canShow }) {
  const who = source ? speakerName(source.participant, source.name) : 'Unknown speaker'
  return h('div', { className: 'mn-cite' },
    h('blockquote', null, `“${citation.quote}”`),
    h('div', { className: 'mn-cite-meta' },
      source && h(Who, { participant: source.participant, name: source.name, participants }),
      source && h('span', null, clock(source.start)),
      source?.source && h('span', null, `Recording ${source.source.part} · ${clock(source.source.start)}`),
      source && btn({ variant: 'ghost', size: 'xs', onClick: () => play(source), 'aria-label': `Play source at ${clock(source.start)}, ${who}` }, '▶ Play source'),
      canShow && btn({ variant: 'ghost', size: 'xs', onClick: onShow }, 'Show in transcript')),
    citation.source_changed && h(Badge, { variant: 'warn' }, 'Transcript changed since these notes. Quote is from the analyzed version.'))
}

function NoteItem({ item, analysis, participants, play, showSegment, liveIds }) {
  const owner = ownerText(item), due = dueText(item)
  return h('article', { className: 'mn-note' },
    h('p', { className: 'mn-note-text' }, item.text),
    item.kind === 'commitment' && h('dl', { className: 'mn-attrs' },
      h('dt', null, 'Owner'),
      h('dd', null, h('span', null, owner.text), owner.note && h('span', { className: 'mn-faint' }, owner.note)),
      h('dt', null, 'Due'),
      h('dd', null, h('span', null, due.text), due.quote && h('span', { className: 'mn-faint' }, `from “${due.quote}”`), due.badge && h(Badge, { variant: 'warn' }, due.badge))),
    ...item.citations.map(c => h(Citation, { key: c.segment_id, citation: c, source: analysis.sources[c.segment_id], participants, play,
      canShow: liveIds.has(c.segment_id), onShow: () => showSegment(c.segment_id) })))
}

function NotesPanel({ meeting, participants, rest, play, showSegment }) {
  const [historical, setHistorical] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const request = useRef(0)
  useEffect(() => {
    request.current++; setHistorical(null); setError(''); setLoading(false)
    return () => { request.current++ }
  }, [meeting.id, meeting.revision, meeting.analysis?.id])
  const latest = meeting.analysis
  const analysis = historical || latest
  const liveIds = useMemo(() => new Set(meeting.segments.map(s => s.id)), [meeting.segments])
  const versions = meeting.analysis_versions || []
  const choose = async id => {
    const seq = ++request.current
    setError('')
    if (!id || id === latest?.id) { setHistorical(null); setLoading(false); return }
    setLoading(true)
    try {
      const result = await rest(`/meetings/${meeting.id}/analyses/${id}`)
      if (request.current === seq) setHistorical(result)
    }
    catch (e) { if (request.current === seq) setError(errorText(e)) }
    finally { if (request.current === seq) setLoading(false) }
  }
  if (!meeting.active) return h('p', { className: 'mn-muted' }, 'Notes can be generated after the transcript is ready.')
  const counts = analysis ? KINDS.map(k => ({ ...k, n: analysis.items.filter(i => i.kind === k.kind).length })).filter(k => k.n) : []
  return h('div', { className: 'mn-section' },
    h(RequestBox, { key: `${meeting.id}:${meeting.owner_profile || ''}`, meeting, hasNotes: Boolean(latest) }),
    versions.length > 0 && h('div', { className: 'mn-toolbar' },
      h(Field, { label: 'Notes version' },
        h('select', { className: 'mn-input', value: analysis?.id || '', disabled: loading, onChange: e => choose(e.target.value) },
          !latest && h('option', { value: '' }, 'No notes for the current transcript'),
          ...versions.map(v => h('option', { key: v.id, value: v.id },
            `${v.id === latest?.id ? 'Latest · ' : ''}${new Date(v.created * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })} · revision ${v.revision}${v.transcript !== meeting.active ? ' · earlier transcript' : ''}`)))),
      historical && btn({ size: 'sm', onClick: () => choose('') }, latest ? 'Back to latest notes' : 'Close')),
    error && h(Callout, { tone: 'error', role: 'alert' }, error),
    !analysis && h('p', { className: 'mn-faint' }, 'No notes yet for this transcript.'),
    analysis && h(Fragment, null,
      historical && h(Callout, { tone: 'info', title: 'Viewing an earlier version' }, h('p', null, 'This is a saved version from history. It is kept unchanged for review.')),
      analysis.needs_review && h(Callout, { tone: 'warn', title: 'Notes may be out of date', role: 'status' },
        h('p', null, historical ? 'The transcript has changed since this version was generated.' : 'The meeting changed after these notes were generated. Review them or regenerate with the request above. Owners based on who spoke already reflect your speaker corrections; owners named in the text do not change.')),
      analysis.unmatched_previous_items?.length > 0 && h(Callout, { tone: 'info' },
        h('p', null, `${analysis.unmatched_previous_items.length} ${analysis.unmatched_previous_items.length === 1 ? 'item' : 'items'} from the previous version did not carry over exactly. Choose an earlier version above to review them.`)),
      h('p', { className: 'mn-faint' }, `Generated ${relative(analysis.created)} from revision ${analysis.revision}${analysis.transcript !== meeting.active ? ' of an earlier transcript' : ''}${analysis.profile ? ` · profile ${analysis.profile}` : ''}${analysis.model ? ` · ${analysis.model}` : ''}. Quotes are checked word for word against the transcript; the interpretation still needs your review.`),
      counts.length > 1 && h('nav', { className: 'mn-jump', 'aria-label': 'Note sections' },
        ...counts.map(k => h('a', { key: k.kind, href: `#mn-notes-${k.kind}`, className: 'mn-faint',
          onClick: e => { e.preventDefault(); globalThis.document?.getElementById(`mn-notes-${k.kind}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }) } }, `${k.label} ${k.n}`))),
      analysis.items.length === 0 && h('p', { className: 'mn-muted' }, 'The model found no notes it could support with quotes from this transcript.'),
      ...KINDS.map(k => {
        const items = analysis.items.filter(i => i.kind === k.kind)
        if (!items.length) return null
        return h('section', { key: k.kind, id: `mn-notes-${k.kind}`, className: 'mn-card', 'aria-label': k.label },
          h('h3', null, k.label, h('span', { className: 'mn-count' }, items.length)),
          k.note && h('p', { className: 'mn-faint' }, k.note),
          k.kind === 'commitment' && h('p', { className: 'mn-faint' }, 'Owners and due dates are proposals from the transcript and are not confirmed. Nothing is sent to a task manager.'),
          ...items.map(item => h(NoteItem, { key: item.id, item, analysis, participants, play, showSegment, liveIds })))
      })))
}

export function actionsRequest(meeting) {
  return `Use meeting_notes:action_items to find and save sourced tasks, follow-up meetings and training proposals for meeting ${meeting.id}.${meeting.owner_profile ? ` Pass source_profile=${JSON.stringify(meeting.owner_profile)} to every meeting tool call. Run in profile ${JSON.stringify(meeting.owner_profile)}.` : ''} Save proposals only; do not create calendar events or external tasks.`
}

export function handoffRequest(meeting, handoff) {
  return `Use meeting_notes:handoff for meeting ${meeting.id}, handoff ${handoff.id}. Pass source_profile=${JSON.stringify(meeting.owner_profile)} to every callback. Run in profile ${JSON.stringify(handoff.profile)}. Read the recorded user request and current handoff state first. Claim it before any external write; never repeat an already-started operation. Report the actual outcome with meeting_action_callback so Action Items is updated.`
}

const HANDOFF_LABELS = { pending: 'Ready to hand off', running: 'In progress · outcome not confirmed', needs_input: 'Needs details or access', unknown: 'Outcome uncertain', completed: 'Handoff completed · Hermes reported', failed: 'No external change reported', cancelled: 'Cancelled before starting' }

export function ActionItemsPanel({ meeting, participants = [], rest, play, showSegment, hostApi = host }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState(null)
  const [details, setDetails] = useState({})
  const serial = useRef(0), alive = useRef(true), locked = useRef(false)
  const load = useCallback(async () => {
    const seq = ++serial.current
    try {
      const result = await rest(`/meetings/${meeting.id}/actions`)
      if (alive.current && seq === serial.current) { setData(result); setError('') }
    } catch (e) { if (alive.current && seq === serial.current) setError(errorText(e)) }
  }, [rest, meeting.id])
  useEffect(() => {
    alive.current = true; setData(null); setForm(null); setDetails({}); load()
    const timer = setInterval(() => { if (!locked.current && !globalThis.document?.hidden) load() }, POLL_MS)
    return () => { alive.current = false; serial.current++; clearInterval(timer) }
  }, [load])
  useEffect(() => { if (!locked.current) load() }, [load, meeting.revision])
  const mutate = async action => {
    if (locked.current) return
    locked.current = true; serial.current++; setBusy(true); setError('')
    try { await action(); if (alive.current) { setForm(null); await load() } }
    catch (e) { if (alive.current) setError(errorText(e)) }
    finally { locked.current = false; if (alive.current) setBusy(false) }
  }
  const update = (handoff, operation) => mutate(() => rest(`/meetings/${meeting.id}/handoffs/${handoff.id}`, {
    method: 'POST', body: { operation, version: handoff.version, instructions: details[handoff.id] ?? handoff.instructions }
  }))
  const requestBox = h(RequestBox, { meeting, hostApi, requestText: actionsRequest(meeting), title: data?.items.length ? 'Refresh action proposals in Hermes' : 'Find action items with Hermes',
    inputLabel: 'Action items request to paste into Hermes chat', description: 'Hermes reviews the transcript for tasks, follow-up meetings and training. These are proposals. Choose an item below to hand it to your calendar or to-do tools.' })
  const handoffCard = handoff => h('div', { key: handoff.id, className: 'mn-card', 'aria-label': 'Handoff outcome' },
    h('strong', null, HANDOFF_LABELS[handoff.state] || handoff.state),
    h('p', { className: 'mn-faint' }, `${handoff.destination === 'calendar' ? 'Calendar' : 'To-do list'} · profile ${handoff.profile}`),
    handoff.needs_review && h(Callout, { tone: 'warn' }, 'The meeting or proposal changed after this handoff. Review its external result manually; nothing was updated automatically.'),
    handoff.result && h(Fragment, null,
      h('p', null, handoff.result.summary),
      handoff.result.provider && h('p', { className: 'mn-faint' }, `${handoff.result.provider}${handoff.result.external_id ? ` · ${handoff.result.external_id}` : ''}`),
      handoff.result.url && h('a', { href: handoff.result.url, target: '_blank', rel: 'noopener noreferrer' }, 'Open external result')),
    handoff.state === 'unknown' && h(Callout, { tone: 'warn' }, 'An external change may have happened. Ask Hermes to look it up before any retry.'),
    handoff.profile !== meeting.owner_profile && h(Callout, { tone: 'warn' }, `This older handoff belongs to profile ${handoff.profile}. Unstarted handoffs can be cancelled and prepared again here. For an already-started handoff, use its original chat to reconcile and report the outcome; do not create a duplicate.`),
    ['pending', 'running', 'unknown'].includes(handoff.state) && handoff.profile === meeting.owner_profile && h(RequestBox, {
      meeting, hostApi,
      requestText: handoffRequest(meeting, handoff), title: handoff.state === 'pending' ? 'Send this handoff to Hermes' : 'Review this handoff with Hermes',
      inputLabel: 'Handoff request to paste into Hermes chat',
      hint: 'Hermes reads this saved handoff and records its outcome here. Copying alone does not execute it.',
      description: handoff.state === 'pending' ? 'Paste this request into the current profile chat. Hermes uses its available calendar or to-do tools, asks for missing details, and reports the actual outcome here.' : 'The same handoff ID preserves its history. Hermes must check the existing outcome instead of starting another external write.' }),
    ['pending', 'needs_input'].includes(handoff.state) && btn({ variant: 'ghost', disabled: busy, onClick: () => update(handoff, 'cancel') }, 'Cancel handoff'),
    handoff.state === 'needs_input' && h('form', { onSubmit: e => { e.preventDefault(); update(handoff, 'continue') } },
      h(Field, { label: 'Additional handoff details', hint: 'Confirm the calendar/list, timing, attendees or other details Hermes requested. Configure service access privately in Hermes.' },
        h('textarea', { className: 'mn-input', maxLength: 4000, value: details[handoff.id] ?? handoff.instructions,
          onChange: e => setDetails({ ...details, [handoff.id]: e.target.value }) })),
      btn({ type: 'submit', disabled: busy || handoff.needs_review }, 'Continue with these details')),
    handoff.history?.length > 1 && h('details', null, h('summary', null, 'Outcome history'), ...handoff.history.filter(e => e.result).map((event, i) => h('p', { key: i }, `${new Date(event.at * 1000).toLocaleString()}: ${event.result.summary}`))))
  if (!meeting.active) return h('p', { className: 'mn-muted' }, 'Action items can be proposed after transcription is ready.')
  return h('div', { className: 'mn-section' },
    h('h2', null, 'Action Items'),
    h('p', { className: 'mn-muted' }, 'Review each proposal, then hand it to Hermes. Adding a task or scheduling an event does not mean the underlying work is finished.'),
    requestBox,
    error && h(Callout, { tone: 'error', role: 'alert' }, error),
    !data && h('p', { role: 'status' }, 'Loading action items…'),
    data?.needs_review && h(Callout, { tone: 'warn' }, 'The transcript or profile changed. Regenerate and review action proposals before creating a handoff.'),
    data && !data.items.length && h('p', { className: 'mn-faint' }, 'No action proposals saved yet.'),
    ...(data?.items || []).map(item => {
      const handoffs = data.handoffs.filter(h => h.action_id === item.id)
      const existing = handoffs.some(h => !['failed', 'cancelled'].includes(h.state))
      return h('article', { key: item.id, className: 'mn-card', 'aria-label': `Action: ${item.text}` },
        h('div', { className: 'mn-spread' }, h('strong', null, item.action_type === 'meeting' ? 'Follow-up meeting' : item.action_type === 'training' ? 'Training' : 'Task'),
          h(Badge, { variant: item.kind === 'commitment' ? 'info' : 'neutral' }, item.kind === 'commitment' ? 'Transcript commitment · review owner' : 'Proposed action')),
        h(NoteItem, { item, analysis: { sources: data.sources }, participants, play, showSegment, liveIds: new Set(meeting.segments.map(s => s.id)) }),
        item.kind !== 'commitment' && h('p', { className: 'mn-faint' }, 'Owner and timing need confirmation.'),
        !existing && form?.action_id !== item.id && btn({ disabled: busy || data.needs_review, onClick: () => setForm({ action_id: item.id, actions_id: data.actions_id, revision: meeting.revision,
          destination: item.action_type === 'task' ? 'todo' : 'calendar', instructions: '', request_id: globalThis.crypto.randomUUID() }) }, 'Hand off to Hermes'),
        form?.action_id === item.id && h('form', { 'aria-label': 'Review handoff', onSubmit: e => { e.preventDefault(); mutate(() => rest(`/meetings/${meeting.id}/handoffs`, { method: 'POST', body: form })) } },
          h(Field, { label: 'Destination' }, h('select', { className: 'mn-input', value: form.destination, onChange: e => setForm({ ...form, destination: e.target.value }) },
            h('option', { value: 'todo' }, 'My to-do list'), h('option', { value: 'calendar' }, 'My calendar'))),
          h(Field, { label: 'Instructions for Hermes', hint: 'Specify your list/calendar and confirmed details, or let Hermes ask in chat. Do not enter passwords or keys.' },
            h('textarea', { className: 'mn-input', maxLength: 4000, value: form.instructions, onChange: e => setForm({ ...form, instructions: e.target.value }) })),
          h('p', { className: 'mn-faint' }, `This prepares a request for profile ${meeting.owner_profile || 'current profile'}. No calendar event or task is created until you send it in Hermes chat.`),
          btn({ type: 'submit', disabled: busy || data.needs_review }, 'Prepare handoff'),
          btn({ variant: 'ghost', disabled: busy, onClick: () => setForm(null) }, 'Cancel')),
        ...handoffs.map(handoffCard))
    }),
    data?.handoffs.some(h => !data.items.some(i => i.id === h.action_id)) && h('section', { 'aria-label': 'Earlier action handoffs' },
      h('h3', null, 'Earlier action handoffs'),
      h('p', { className: 'mn-faint' }, 'These proposals did not carry over exactly when actions were regenerated. Their external results are retained for review.'),
      ...data.handoffs.filter(h => !data.items.some(i => i.id === h.action_id)).map(handoff => h('article', { key: handoff.id }, h('p', null, handoff.snapshot.item.text), handoffCard(handoff)))))
}

function MeetingView({ meeting, notice, busy, act, rest, play, playing, closePlayer, correct, tab, setTab, onDeleted, onDeleteBusy }) {
  const participants = useMemo(() => participantsOf(meeting.segments, meeting.corrections), [meeting.segments, meeting.corrections])
  const [focusId, setFocusId] = useState(null)
  const cursor = useRef(-1)
  const [activeId, setActiveId] = useState(null)
  const setCursor = useCallback(time => {
    cursor.current = time
    setActiveId(meeting.segments.find(s => time >= s.start && time < s.end)?.id ?? null)
  }, [meeting.segments])
  const [seekRequest, setSeekRequest] = useState(null)
  const duration = meeting.segments.length ? Math.max(...meeting.segments.map(s => s.end || 0)) : 0
  const ready = Boolean(meeting.active)
  const analysis = meeting.analysis
  const noteCount = analysis ? analysis.items.length : null
  const tabs = [
    { id: 'transcript', label: 'Transcript', count: ready ? meeting.segments.length : null },
    { id: 'notes', label: 'Notes', count: noteCount, badge: analysis?.needs_review ? h(Badge, { variant: 'warn', size: 'xs' }, 'Review') : null },
    { id: 'actions', label: 'Action Items', count: null }
  ]
  const showSegment = id => { setTab('transcript'); setFocusId({ id, at: Date.now() }) }
  return h('div', { className: 'mn-page' },
    h('header', { className: 'mn-header' },
      h('div', { className: 'mn-title-row' },
        h('h2', null, meeting.title),
        ready && h('span', { className: 'mn-muted mn-nowrap', title: 'End of the returned transcript; the recording may be longer' }, `Transcript to ${clock(duration)}`),
        h(StateBadge, { state: meeting.job.state })),
      h('div', { className: 'mn-facts' },
        h('span', null, `${formatMeetingDate(meeting.date, meeting.timezone)}${meeting.timezone ? ` · ${meeting.timezone}` : ''}`),
        ready && h('span', null, `${participants.length} ${participants.length === 1 ? 'speaker' : 'speakers'}`),
        ready && h('span', null, `${meeting.segments.length} turns`),
        ready && analysis && h('span', null, `${noteCount} ${noteCount === 1 ? 'note' : 'notes'}${analysis.needs_review ? ' · needs review' : ''}`))),
    notice && h(Callout, { tone: notice.tone || 'ok', role: 'status' }, h('p', null, notice.text)),
    ready && h(MeetingPlayer, {meetingId:meeting.id,rest,seekRequest,onPosition:setCursor,suspended:Boolean(playing),onStart:closePlayer}),
    h(DeleteMeeting, { meeting, rest, disabled: busy, onBusy: onDeleteBusy, onDeleted }),
    meeting.transcription_chunks?.some(c => c.recognized_segments === 0) && meeting.segments.length > 0 &&
      h(Callout, { tone: 'warn', title: 'Some audio has no recognized words', role: 'status' }, 'At least one chunk returned no words. Review its audio before relying on these notes; this does not prove silence.'),
    meeting.transcription_chunks?.length > 1 && h('details', { className: 'mn-card' },
      h('summary', null, `${meeting.transcription_chunks.length} transcription chunks`),
      h('p', { className: 'mn-muted' }, 'Timestamps follow the full recording. Speaker labels are separate per chunk; merge matching speakers before generating notes. Review speech near the cut points.'),
      h('ol', null, ...meeting.transcription_chunks.map(c => h('li', { key: c.index },
        `${clock(c.start)} – ${clock(c.start + c.duration)} · ${stateInfo(c.state).label}${c.recognized_segments === 0 ? ' · no words returned; review the audio' : ''}`)))),
    ready && !meeting.segments.length && h(Callout, { tone: 'warn', title: 'No words returned' }, 'The model returned no words. This does not confirm that the recording was silent.'),
    meeting.recordings?.length > 0 && h('details', { className: 'mn-card' },
      h('summary', null, `${meeting.recordings.length} combined recordings`),
      h('p', { className: 'mn-muted' }, 'Transcript snapshots in playback order. Match speakers across recordings before generating notes. Later edits to the originals do not change this meeting.'),
      h('ol', null, ...meeting.recordings.map(r => h('li', { key: r.part }, `${r.title} · starts ${clock(r.offset)} · source revision ${r.revision}`)))),
    h(StatusPanel, { meeting, busy, act }),
    h(RecordingStorage, { meeting, rest }),
    h(Player, { playing, onClose: closePlayer }),
    !ready ? h('div', { className: 'mn-card', 'aria-busy': stateInfo(meeting.job.state).active ? true : undefined },
      h('p', { className: 'mn-muted' }, stateInfo(meeting.job.state).active ? 'The transcript will appear here when transcription finishes.' : 'No transcript is available for this meeting.'),
      stateInfo(meeting.job.state).active && h('div', { 'aria-hidden': true, style: { display: 'grid', gap: 8 } },
        h('div', { className: 'mn-skel', style: { width: '70%' } }), h('div', { className: 'mn-skel', style: { width: '90%' } }), h('div', { className: 'mn-skel', style: { width: '55%' } })))
      : h(Fragment, null,
        h(Tabs, { tabs, value: tab, onChange: setTab, label: 'Meeting sections' }),
        h('div', { role: 'tabpanel', id: `mn-panel-${tab}`, 'aria-labelledby': `mn-tab-${tab}`, tabIndex: -1 },
          tab === 'transcript' && h(TranscriptPanel, { meeting, participants, cursor, activeId, playing, focusId, setFocusId, busy, play:segment=>{closePlayer();setSeekRequest({start:segment.start,at:Date.now()})}, correct }),
          tab === 'notes' && h(NotesPanel, { meeting, participants, rest, play, showSegment }),
          tab === 'actions' && h(ActionItemsPanel, { meeting, participants, rest, play, showSegment }))))
}

function Welcome({ onNew, hasMeetings }) {
  return h('div', { className: 'mn-page' },
    h('div', { className: 'mn-empty' },
      h('h2', null, hasMeetings ? 'Choose a meeting' : 'Start with a recording'),
      h('ol', { className: 'mn-steps' },
        h('li', null, 'Upload a meeting recording. It is transcribed with separate speakers.'),
        h('li', null, 'Review the speakers: merge split voices, add names, fix text.'),
        h('li', null, 'Generate sourced notes from Hermes chat. Every note links to the quote it came from.')),
      btn({ variant: 'default', onClick: onNew }, 'New recording')))
}

// ── Root ─────────────────────────────────────────────────────────────────────

// Hash links work in both hosts. Only this plugin's exact link namespace is
// intercepted; no server URL, credentials or native-only protocol is embedded.
export function parseMeetingLink(href) {
  if (typeof href !== 'string' || !href.startsWith('#/meeting-notes?')) return null
  const params = new URLSearchParams(href.slice(href.indexOf('?') + 1))
  if ([...params.keys()].some(k => !['meeting', 'tab', 'profile'].includes(k)) ||
      [...params.keys()].some(k => params.getAll(k).length !== 1)) return null
  const meeting = params.get('meeting'), tab = params.get('tab'), profile = params.get('profile')
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(meeting || '') || !['notes', 'actions'].includes(tab) ||
      (profile !== null && !/^[a-zA-Z0-9_-]{1,128}$/.test(profile))) return null
  return { meeting, tab, profile }
}

export function installMeetingLinks(ctx, hostApi = host, doc = globalThis.document) {
  const click = event => {
    const href = event.target?.closest?.('a[href]')?.getAttribute('href')
    if (!parseMeetingLink(href) || !hostApi?.navigate || event.button !== 0 || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    event.stopPropagation()
    if (!globalThis.window?.dispatchEvent(new Event('meeting-notes:before-navigate', { cancelable: true }))) return
    hostApi.navigate(href.slice(1))
    globalThis.window?.dispatchEvent(new Event('meeting-notes:navigate'))
  }
  doc?.addEventListener('click', click, true)
  const dispose = () => doc?.removeEventListener('click', click, true)
  ctx.onDispose?.(dispose)
  return dispose
}

export function Meetings({ ctx }) {
  const [location, setLocation] = useState(() => ({ target: parseMeetingLink(globalThis.window?.location.hash || ''), visit: 0 }))
  const [linkNotice, setLinkNotice] = useState('')
  const locked = useRef(false)
  const onLock = useCallback(value => { locked.current = value }, [])
  useEffect(() => {
    const changed = event => {
      const target = parseMeetingLink(window.location.hash)
      // Route tiles remain mounted beside chat. Unrelated navigation must not
      // reset an upload, editor, selection or player inside this tile.
      if (!target) return
      if (locked.current) { setLinkNotice('Finish the current upload or change, then open the meeting link again.'); return }
      setLocation(old => JSON.stringify(old.target) === JSON.stringify(target) && event.type !== 'meeting-notes:navigate'
        ? old : { target, visit: old.visit + 1 })
      setLinkNotice('')
    }
    const before = event => {
      if (locked.current) {
        event.preventDefault()
        setLinkNotice('Finish the current upload or change, then open the meeting link again.')
      }
    }
    window.addEventListener('hashchange', changed)
    window.addEventListener('popstate', changed)
    window.addEventListener('meeting-notes:navigate', changed)
    window.addEventListener('meeting-notes:before-navigate', before)
    return () => {
      window.removeEventListener('hashchange', changed)
      window.removeEventListener('popstate', changed)
      window.removeEventListener('meeting-notes:navigate', changed)
      window.removeEventListener('meeting-notes:before-navigate', before)
    }
  }, [])
  const reset = () => {
    if (locked.current) return
    host?.navigate?.('/meeting-notes')
    setLocation(old => ({ target: null, visit: old.visit + 1 }))
    setLinkNotice('')
  }
  return h(Fragment, null,
    linkNotice && h('p', { role: 'status' }, linkNotice),
    h(MeetingLinkTarget, { key: JSON.stringify(location.target) + ':' + location.visit, ctx, target: location.target, onLock, reset }))
}

function MeetingLinkTarget({ ctx, target, onLock, reset }) {
  const [scope, setScope] = useState(target?.profile ? 'checking' : 'ready')
  const [error, setError] = useState('')
  useEffect(() => {
    if (!target?.profile) return
    let live = true
    ctx.rest('/analysis-profiles').then(result => {
      if (!live) return
      if (result.current === target.profile) setScope('ready')
      else { setScope('blocked'); setError(`This meeting belongs to profile ${target.profile}. Switch to that profile on the importing connection, then open this link again.`) }
    }).catch(() => { if (live) { setScope('blocked'); setError('Could not verify the meeting’s importing profile. Open that profile on the importing connection, then try the link again.') } })
    return () => { live = false }
  }, [ctx.rest, target?.profile])
  if (scope !== 'ready') return h('div', { className: 'mn-page', role: 'status' }, error || 'Checking meeting profile…',
    btn({ onClick: reset }, 'Show Meeting Notes'))
  return h(MeetingWorkspace, { ctx, target, onLock })
}

let lastSelected = null // survives route remounts within one app session

function MeetingWorkspace({ ctx, target, onLock }) {
  const rest = ctx.rest
  const [meetings, setMeetings] = useState([])
  const [loaded, setLoaded] = useState(false)
  const [view, setView] = useState(target || lastSelected ? 'meeting' : 'home')
  const [selected, setSelected] = useState(null)
  const [tab, setTab] = useState(target?.tab || 'transcript')
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  useEffect(() => { onLock(busy || uploading); return () => onLock(false) }, [busy, uploading, onLock])
  const [notice, setNotice] = useState(null)
  const [actionError, setActionError] = useState('')
  const [pollError, setPollError] = useState('')
  const [playing, setPlaying] = useState(null)
  const selectedId = useRef(target?.meeting || lastSelected)
  const mutation = useRef(0)
  const audioUrl = useRef(null)
  const audioRequest = useRef(0)
  const mounted = useRef(true)
  const inflight = useRef(false)

  const loadSelected = useCallback(async () => {
    const id = selectedId.current
    if (!id) return
    const seq = mutation.current
    let meeting
    try { meeting = await rest(`/meetings/${id}`) }
    catch (e) {
      if (/not found/i.test(errorText(e)) && selectedId.current === id) {
        selectedId.current = null; if (lastSelected === id) lastSelected = null; setSelected(null)
        setView(v => (v === 'meeting' ? 'home' : v))
      }
      if (target?.meeting === id && /not found/i.test(errorText(e))) throw new Error('This meeting is not on this connection, or it was deleted. Open its importing connection and profile.')
      throw e
    }
    // Drop stale poll results: a different meeting was opened or a correction landed meanwhile.
    if (selectedId.current === id && mutation.current === seq) { setSelected(meeting); lastSelected = id }
  }, [rest])

  const refresh = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true
    try {
      const seq = mutation.current
      const listed = await rest('/meetings')
      if (seq === mutation.current) setMeetings(listed)
      setLoaded(true)
      await loadSelected()
      setPollError('')
    } catch (e) { setPollError(errorText(e)) }
    finally { inflight.current = false }
  }, [rest, loadSelected])

  useEffect(() => {
    mounted.current = true
    refresh()
    // GET only: polling never starts transcription work.
    const timer = setInterval(() => { if (!globalThis.document?.hidden) refresh() }, POLL_MS)
    const visible = () => { if (!globalThis.document?.hidden) refresh() }
    globalThis.document?.addEventListener?.('visibilitychange', visible)
    return () => {
      mounted.current = false
      audioRequest.current++
      clearInterval(timer)
      globalThis.document?.removeEventListener?.('visibilitychange', visible)
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current)
      audioUrl.current = null
    }
  }, [refresh])

  const closePlayer = () => {
    audioRequest.current++
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current)
    audioUrl.current = null
    setPlaying(null)
  }

  const open = async (id, message = null) => {
    if (parseMeetingLink(globalThis.window?.location.hash)) window.history.replaceState(window.history.state, '', '#/meeting-notes')
    selectedId.current = id
    lastSelected = id
    setView('meeting'); setNotice(message); setActionError('')
    if (selected?.id !== id) { setSelected(null); setTab('transcript'); closePlayer() }
    const seq = ++mutation.current
    try {
      const result = await rest(`/meetings/${id}`)
      if (mounted.current && selectedId.current === id && mutation.current === seq) setSelected(result)
    }
    catch (e) { if (mounted.current && selectedId.current === id && mutation.current === seq) setActionError(errorText(e)) }
  }

  const act = async (message, fn) => {
    setBusy(true); setActionError(''); mutation.current++
    try {
      const result = await fn(rest)
      if (result && result.id === selectedId.current && result.segments) setSelected(result)
      if (message) setNotice({ text: message, tone: 'ok' })
      mutation.current++
      await refresh()
      return true
    } catch (e) { setActionError(errorText(e)); return false }
    finally { setBusy(false) }
  }

  // Corrections are applied one at a time, each against the revision the last returned.
  const correct = steps => act(null, async () => {
    let meeting = selected
    for (const [kind, target, value] of steps) {
      meeting = await rest(`/meetings/${meeting.id}/corrections`, { method: 'POST', body: { kind, target, value, revision: meeting.revision } })
      setSelected(meeting)
    }
    setNotice(null)
    return meeting
  })

  const play = async segment => {
    if (!selected) return
    const meetingId = selected.id
    const seq = ++audioRequest.current
    const span = Math.max(0, (segment.end || segment.start) - segment.start)
    const duration = Math.min(PLAY_WINDOW_MAX, Math.max(1, span + 1))
    try {
      const result = await rest(`/meetings/${meetingId}/audio?start=${segment.start}&duration=${duration}`)
      if (!mounted.current || audioRequest.current !== seq || selectedId.current !== meetingId) return
      const bytes = Uint8Array.from(atob(result.base64), c => c.charCodeAt(0))
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current)
      audioUrl.current = URL.createObjectURL(new Blob([bytes], { type: result.mime }))
      setPlaying({ url: audioUrl.current, segmentId: segment.id, start: segment.start, duration, clipped: span + 1 > PLAY_WINDOW_MAX,
        label: speakerName(segment.participant, segment.name) })
    } catch (e) {
      if (mounted.current && audioRequest.current === seq && selectedId.current === meetingId) setActionError(`Playback unavailable: ${errorText(e)}`)
    }
  }

  const deleted = (result, deletedId) => {
    if (parseMeetingLink(globalThis.window?.location.hash)?.meeting === deletedId) window.history.replaceState(window.history.state, '', '#/meeting-notes')
    mutation.current++; closePlayer(); selectedId.current = null; if (lastSelected === deletedId) lastSelected = null; setSelected(null)
    setView('home'); setActionError(''); setPollError(''); setMeetings(rows => rows.filter(m => m.id !== deletedId))
    setNotice({ tone: result.pending_files ? 'warn' : 'ok', text: result.pending_files ? 'Meeting deleted. Some managed files await cleanup; reconnect storage and use Retry file cleanup in settings.' : 'Meeting and its managed files deleted.' })
  }

  const page = view === 'upload'
    ? h(UploadForm, { rest, onBusy: setUploading, onUploaded: async id => {
      const existing = meetings.some(m => m.id === id)
      await open(id, { tone: 'ok', text: existing
        ? 'This recording was already imported, so its existing meeting is open. Nothing new was sent for transcription.'
        : 'Recording saved. Transcription status updates here automatically.' })
      refresh()
    } })
    : view === 'combine' ? h(CombineForm, { meetings, rest, onBusy: setUploading, onCreated: async id => {
      await open(id, { tone: 'ok', text: 'Combined meeting created. Review speakers across recordings, then generate notes. Source meetings are unchanged.' })
      refresh()
    } })
    : view === 'settings' ? h(SettingsForm, { rest })
      : view === 'meeting' && selected ? h(MeetingView, { key: selected.id, meeting: selected, notice, busy, act, rest, play, playing, closePlayer, correct, tab, setTab, onDeleted: deleted, onDeleteBusy: setBusy })
        : view === 'meeting' ? h('div', { className: 'mn-page', 'aria-busy': true }, h('div', { className: 'mn-skel', style: { width: '40%', height: 20 } }), h('div', { className: 'mn-skel', style: { width: '70%' } }), h('p', { className: 'mn-faint' }, 'Loading meeting…'))
          : h(Welcome, { onNew: () => setView('upload'), hasMeetings: meetings.length > 0 })

  const leave = next => { if (uploading || busy) return; closePlayer(); setView(next); setNotice(null); setActionError('') }
  return h('div', { className: 'mn' },
    h(Sidebar, { meetings, loaded, selectedId: selected?.id || selectedId.current, view, busy, locked: uploading || busy,
      onSelect: id => { if (!uploading && !busy) open(id) }, onNew: () => leave('upload'), onCombine: () => leave('combine'), onSettings: () => leave('settings'),
      onStartQueued: () => act('Queued work started.', r => r('/worker/start', { method: 'POST' })) }),
    h('main', { className: 'mn-main', 'aria-label': 'Meeting workspace' },
      (actionError || pollError) && h('div', { style: { padding: '12px 28px 0', maxWidth: 1180, margin: '0 auto' } },
        actionError && h(Callout, { tone: 'error', role: 'alert' }, h('div', { className: 'mn-spread' }, h('span', null, actionError), btn({ size: 'xs', variant: 'ghost', onClick: () => setActionError('') }, 'Dismiss'))),
        !actionError && pollError && h(Callout, { tone: 'warn', role: 'status' }, h('p', null, `Could not refresh: ${pollError}. Retrying every few seconds.`))),
      view === 'home' && notice && h(Callout, { tone: notice.tone, role: 'status' }, notice.text),
      page))
}

export default {
  id: 'meeting_notes',
  name: 'Meeting Notes',
  defaultEnabled: false,
  register(ctx) {
    installMeetingLinks(ctx)
    const style = ensureStyles()
    if (style) ctx.onDispose?.(() => style.remove())
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/meeting-notes' }, render: () => h(Meetings, { ctx }) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/meeting-notes', label: 'Meeting Notes', codicon: 'record' } }
    ])
  }
}
