// Shared UI for native Hermes Desktop and the Webapp. Uncompiled ESM: only
// @hermes/plugin-sdk and react resolve, so no JSX and no other imports.
import { Badge, Button, host, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { cloneElement, createElement as h, Fragment, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'

const STYLE_ID = 'hermes-meeting-notes-styles'
const POLL_MS = 3000
const MAX_UPLOAD = 512 * 1024 * 1024
const UPLOAD_TIMEOUT_MS = 30000
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
.mn-transcript{list-style:none;margin:0;padding:0}
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
    detail: 'The response was saved but could not be turned into a transcript. Retry resends unfinished audio; completed chunks are kept.' }
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
  const overridden = new Set()
  for (const c of activeCorrections(corrections)) {
    if (c.kind === 'merge') {
      const from = labels[c.target], to = labels[c.value]
      labels = Object.fromEntries(Object.entries(labels).map(([key, value]) => [key, value === from ? to : value]))
    }
    if (c.kind === 'speaker') overridden.add(c.target)
  }
  const map = new Map()
  for (const s of segments) {
    if (!map.has(s.participant)) map.set(s.participant, { id: s.participant, name: s.name || null, segments: 0, seconds: 0, words: 0, first: s, index: map.size, mergeable: labels[s.participant] === s.participant, mergeSegments: 0, mergeSeconds: 0 })
    const p = map.get(s.participant)
    p.segments += 1
    p.seconds += Math.max(0, (s.end || 0) - (s.start || 0))
    if (!overridden.has(s.id)) {
      p.mergeSegments += 1
      p.mergeSeconds += Math.max(0, (s.end || 0) - (s.start || 0))
    }
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
  const scope = meeting.owner_profile ? ` Pass source_profile=${JSON.stringify(meeting.owner_profile)} to every transcript read and notes save. Run this in Hermes profile ${JSON.stringify(meeting.analysis_profile || meeting.owner_profile)}.` : ''
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
  const queued = meetings.some(m => m.state === 'queued')
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
        await rest(`/uploads/${session.id}/chunks/${index}?sha=${sha}`, { method: 'PUT', upload: { filename: 'chunk', contentType: 'application/octet-stream', bytes }, timeoutMs: UPLOAD_TIMEOUT_MS })
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
    h('p', { className: 'mn-faint' }, 'Uploading the same audio again opens the existing meeting instead of transcribing it twice. Transcription continues on the host if you close this page; quitting Hermes interrupts it.'))
}

// ── Settings ─────────────────────────────────────────────────────────────────

export function SettingsForm({ rest }) {
  const [settings, setSettings] = useState(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => { rest('/settings').then(setSettings).catch(e => setError(errorText(e))) }, [rest])
  const save = async event => {
    event.preventDefault()
    setSaving(true); setError(''); setSaved('')
    try {
      setSettings(await rest('/settings', { method: 'PUT', body: { endpoint: settings.endpoint.trim(), model: settings.model.trim(), timeout: Number(settings.timeout), max_audio_seconds: Number(settings.max_audio_seconds ?? 3600) } }))
      setSaved('Settings saved. New transcription plans use them; normal retries retain their saved settings. Use Retry with current settings to replan unfinished audio.')
    } catch (e) { setError(errorText(e)) }
    finally { setSaving(false) }
  }
  const set = key => e => { setSaved(''); setSettings({ ...settings, [key]: e.target.value }) }
  return h('div', { className: 'mn-page' },
    h('header', { className: 'mn-header' },
      h('h2', null, 'Transcription settings'),
      h('p', { className: 'mn-muted' }, 'A NeMo-Speech.cpp server with speaker diarization and word timestamps. These settings apply to every meeting on this host.')),
    !settings && !error && h('div', { className: 'mn-card', 'aria-busy': true }, h('p', { className: 'mn-faint' }, 'Loading settings…')),
    settings && h('form', { className: 'mn-card', onSubmit: save, 'aria-label': 'Transcription settings' },
      h(Field, { label: 'Server endpoint', hint: 'HTTP(S) base URL, without credentials. Use plain HTTP only on a trusted network.' },
        h('input', { className: 'mn-input', type: 'url', value: settings.endpoint, onChange: set('endpoint'), required: true, disabled: saving })),
      h('div', { className: 'mn-grid2' },
        h(Field, { label: 'Model' }, h('input', { className: 'mn-input', value: settings.model, onChange: set('model'), required: true, disabled: saving })),
        h(Field, { label: 'Request timeout (seconds)', hint: '30 to 7200.' },
          h('input', { className: 'mn-input', type: 'number', min: 30, max: 7200, step: 1, value: settings.timeout, onChange: set('timeout'), required: true, disabled: saving })),
        h(Field, { label: 'Maximum audio per request (minutes)', hint: 'Nemotron 3.5 supports up to 60 minutes. Set a lower limit if your server requires it. This is audio length, not request timeout.' },
          h('input', { className: 'mn-input', type: 'number', min: 1 / 60, max: 1440, step: 'any', value: (settings.max_audio_seconds ?? 3600) / 60,
            onChange: e => { setSaved(''); setSettings({ ...settings, max_audio_seconds: Math.round(Number(e.target.value) * 60) }) }, required: true, disabled: saving }))),
      h('p', { className: 'mn-muted' }, 'Long recordings are cut at time boundaries, transcribed sequentially, and assembled into one meeting. Review speech near cut points and merge matching speakers across chunks. The original recording stays intact.'),
      h('div', { className: 'mn-row' }, h(Button, { type: 'submit', size: 'sm', disabled: saving }, saving ? 'Saving…' : 'Save settings')),
      h('p', { role: 'status', 'aria-live': 'polite', className: 'mn-muted' }, saved)),
    error && h(Callout, { tone: 'error', role: 'alert' }, error),
    h(Callout, { tone: 'info', title: 'Host requirements' },
      h('ul', { className: 'mn-steps' },
        h('li', null, 'Set the API key as MEETING_NOTES_ASR_KEY in Hermes secrets. It is read on the host and never sent to this page.'),
        h('li', null, 'ffmpeg must be installed on the Hermes host.'),
        h('li', null, 'Use a single-profile host. Run one host per transcription server for now.'))))
}

// ── Meeting workspace ────────────────────────────────────────────────────────

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
  const info = stateInfo(meeting.job.state)
  if (meeting.job.state === 'completed' && !meeting.job.error) return null
  const retry = RETRYABLE.includes(meeting.job.state)
  return h(Callout, { tone: retry ? 'error' : 'info', title: info.label, role: retry ? 'alert' : undefined },
    info.detail && h('p', null, info.detail),
    meeting.transcription_chunks?.length > 0 && h('p', { role: 'status' },
      `${meeting.transcription_chunks.filter(c => c.state === 'completed').length} of ${meeting.transcription_chunks.length} audio chunks completed. Retry keeps completed chunks and resumes unfinished work.`),
    meeting.job.error && h('p', { className: 'mn-muted' }, `Details: ${meeting.job.error}`),
    info.active && meeting.job.state !== 'queued' && h('p', { className: 'mn-faint' }, 'You can close this page; the host keeps working. Quitting Hermes interrupts it.'),
    h('div', { className: 'mn-row' },
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
        btn({ variant: 'ghost', size: 'xs', onClick: () => play(person.first), 'aria-label': `Play a sample of ${label}` }, '▶'),
        btn({ variant: 'ghost', size: 'xs', onClick: () => onJump(person.id), 'aria-label': `Next turn by ${label}` }, 'Next'),
        person.mergeable && person.mergeSegments > 0 && others.length > 0 && btn({ variant: 'ghost', size: 'xs', disabled: busy, 'aria-expanded': mode === 'merge', onClick: () => setMode(mode === 'merge' ? null : 'merge'), 'aria-label': `Merge ${label} into another speaker` }, 'Merge…'))),
    mode === 'merge' && h('form', { className: 'mn-merge', onSubmit: async e => { e.preventDefault(); if (into && await correct([['merge', person.id, into]])) setMode(null) } },
      h(Field, { label: `Merge ${label} into` },
        h('select', { className: 'mn-input mn-input-sm', value: into, onChange: e => setInto(e.target.value), disabled: busy },
          ...others.map(o => h('option', { key: o.id, value: o.id }, speakerName(o.id, o.name))))),
      target && h('p', { className: 'mn-faint' }, `${person.mergeSegments} ${person.mergeSegments === 1 ? 'turn' : 'turns'} (${spokenDuration(person.mergeSeconds)} of transcribed speech) become ${speakerName(target.id, target.name)}'s. Individual speaker overrides stay unchanged. You can undo this.`),
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

function TranscriptPanel({ meeting, participants, playing, focusId, setFocusId, busy, play, correct }) {
  const [query, setQuery] = useState('')
  const [speaker, setSpeaker] = useState('')
  const [editing, setEditing] = useState(null) // 'text:<id>' | 'speaker:<id>'
  const [drawer, setDrawer] = useState(false)
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
  const jump = participant => {
    const after = focusId ? (meeting.segments.find(s => s.id === focusId.id)?.start ?? -1) : (playing ? playing.start : -1)
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
        busy && h('span', { className: 'mn-faint', role: 'status' }, 'Saving…'),
        btn({ variant: 'ghost', disabled: busy || !latest, onClick: () => correct([['undo', '', latest.id]]),
          'aria-label': latest ? `Undo last change: ${describe(latest)}` : 'Undo last change', title: latest ? describe(latest) : 'Nothing to undo' }, 'Undo'),
        btn({ className: 'mn-drawer-only', 'aria-expanded': drawer, onClick: () => setDrawer(!drawer) }, `Speakers ${participants.length}`)),
      h('p', { className: 'mn-faint', role: 'status', 'aria-live': 'polite' },
        shown.length === meeting.segments.length ? `${meeting.segments.length} turns · click a speaker name to reassign, double-click text to edit`
          : `Showing ${shown.length} of ${meeting.segments.length} turns`),
      shown.length === 0 && h('p', { className: 'mn-muted' }, 'No turns match.'),
      h('ol', { className: 'mn-transcript', ref: listRef },
        ...shown.map(s => h(SegmentRow, { key: s.id, s, participants, corrected: corrected[s.id], active: playing?.segmentId === s.id, flash: focusId?.id === s.id,
          busy, play, correct, editing, setEditing })))),
    h(SpeakersPanel, { meeting, participants, busy, play, correct, onJump: jump, onClose: () => setDrawer(false) }))
}

// `CSS.escape` is standard in browsers; ids here are hex + ':s00001' anyway.
const CSS_ESCAPE = value => (globalThis.CSS && globalThis.CSS.escape ? globalThis.CSS.escape(value) : String(value).replace(/["\\]/g, '\\$&'))


export async function analysisChatRoute(hostApi, profile, connectionId) {
  if (!profile || !hostApi?.profileRoutes || !hostApi?.newChat) throw new Error('Open a Hermes chat in the selected profile and paste the request below.')
  const routes = await hostApi.profileRoutes()
  const candidates = routes.filter(r => r.targetProfile === profile &&
    (connectionId ? r.connectionId === connectionId : r.mode === 'local'))
  if (candidates.length !== 1) throw new Error('The selected profile is not uniquely reachable on this connection. Open it in Hermes first; single-profile Webapp hosts cannot route other profiles.')
  return candidates[0]
}

export function AnalysisProfile({ meeting, rest, act, busy }) {
  const [options, setOptions] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    rest('/analysis-profiles').then(result => { if (live) { setOptions(result); setError('') } })
      .catch(e => { if (live) setError(errorText(e)) })
    return () => { live = false }
  }, [rest, meeting.id])
  const selected = meeting.analysis_profile || meeting.owner_profile || options?.current || ''
  const missing = options && selected && !options.profiles.includes(selected)
  return h('div', { className: 'mn-card' },
    h(Field, { label: 'Analysis profile', hint: 'Uses this Hermes profile’s model, instructions and tools in its chat. Only this meeting’s transcript and notes are shared with it.' },
      h('select', { className: 'mn-input', value: selected, disabled: busy || !options,
        onChange: e => { const profile = e.target.value; act('Analysis profile saved. Use a chat in that profile to generate notes.', r => r(`/meetings/${meeting.id}/analysis-profile`, { method: 'PUT', body: { profile, revision: meeting.revision } })) } },
      !options && h('option', { value: selected }, selected || 'Loading profiles…'),
      missing && h('option', { value: selected }, `${selected} (unavailable)`),
      ...(options?.profiles || []).map(p => h('option', { key: p, value: p }, p === options.current ? `${p} (importing profile)` : p)))),
    error && h('p', { className: 'mn-faint', role: 'status' }, `Profiles unavailable: ${error}`),
    missing && h(Callout, { tone: 'warn', role: 'status' }, 'The saved profile is unavailable. Select an existing profile; analysis will not silently use another one.'))
}

export function RequestBox({ meeting, hasNotes, hostApi = host }) {
  const text = notesRequest(meeting)
  const ref = useRef(null)
  const [copied, setCopied] = useState('')
  const [opening, setOpening] = useState(false)
  const connection = useRef(hostApi?.state?.connectionId?.get?.() ?? null)
  const openProfile = async () => {
    setOpening(true)
    let copiedRequest = false
    try {
      if (!globalThis.navigator?.clipboard?.writeText) throw new Error('Copy the request below, then open a Hermes chat in the selected profile.')
      await globalThis.navigator.clipboard.writeText(text)
      copiedRequest = true
      const route = await analysisChatRoute(hostApi, meeting.analysis_profile || meeting.owner_profile, connection.current)
      hostApi.newChat(route)
      setCopied('Request copied. Paste it into the new profile chat. Notes save back to the importing profile.')
    } catch (e) { setCopied(`${copiedRequest ? 'Request copied. ' : ''}${errorText(e)}`) }
    finally { setOpening(false) }
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
      h('strong', null, hasNotes ? 'Regenerate notes in Hermes chat' : 'Generate notes in Hermes chat'),
      btn({ size: 'xs', onClick: copy }, 'Copy request'),
      meeting.owner_profile && btn({ size: 'xs', onClick: openProfile, disabled: opening }, opening ? 'Opening…' : 'Copy request and open profile')),
    h('p', { className: 'mn-muted' }, `The meeting_notes:analyze skill loads the transcript into your Hermes chat. Hermes writes the notes in that session and saves them here with checked source quotes.${meeting.owner_profile ? ` Analyze in profile ${meeting.analysis_profile || meeting.owner_profile}; notes remain with the importing profile ${meeting.owner_profile}. The plugin must be enabled in the analysis profile.` : ''}`),
    h('input', { ref, className: 'mn-input', readOnly: true, value: text, 'aria-label': 'Notes request to paste into Hermes chat', onFocus: e => e.target.select(), style: { fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 12 } }),
    h('p', { className: 'mn-faint', role: 'status', 'aria-live': 'polite' }, copied || 'Review speakers first. The skill reads the current transcript and checks its revision before saving.'))
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
    (!latest || latest.needs_review) && !historical && h(RequestBox, { meeting, hasNotes: Boolean(latest) }),
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
        h('p', null, historical ? 'The transcript or analysis profile has changed since this version was generated.' : 'The transcript or analysis profile changed after these notes were generated. Review them or regenerate with the request above. Owners based on who spoke already reflect your speaker corrections; owners named in the text do not change.')),
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
      })),
    latest && !latest.needs_review && !historical && h('details', null,
      h('summary', { className: 'mn-faint', style: { cursor: 'pointer' } }, 'Regenerate notes'),
      h('div', { style: { marginTop: 8 } }, h(RequestBox, { meeting, hasNotes: true }))))
}

function MeetingView({ meeting, notice, busy, act, rest, play, playing, closePlayer, correct, tab, setTab }) {
  const participants = useMemo(() => participantsOf(meeting.segments, meeting.corrections), [meeting.segments, meeting.corrections])
  const [focusId, setFocusId] = useState(null)
  const duration = meeting.segments.length ? Math.max(...meeting.segments.map(s => s.end || 0)) : 0
  const ready = Boolean(meeting.active)
  const analysis = meeting.analysis
  const noteCount = analysis ? analysis.items.length : null
  const tabs = [
    { id: 'transcript', label: 'Transcript', count: ready ? meeting.segments.length : null },
    { id: 'notes', label: 'Notes', count: noteCount, badge: analysis?.needs_review ? h(Badge, { variant: 'warn', size: 'xs' }, 'Review') : null }
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
    h(AnalysisProfile, { meeting, rest, act, busy }),
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
    h(Player, { playing, onClose: closePlayer }),
    !ready ? h('div', { className: 'mn-card', 'aria-busy': stateInfo(meeting.job.state).active ? true : undefined },
      h('p', { className: 'mn-muted' }, stateInfo(meeting.job.state).active ? 'The transcript will appear here when transcription finishes.' : 'No transcript is available for this meeting.'),
      stateInfo(meeting.job.state).active && h('div', { 'aria-hidden': true, style: { display: 'grid', gap: 8 } },
        h('div', { className: 'mn-skel', style: { width: '70%' } }), h('div', { className: 'mn-skel', style: { width: '90%' } }), h('div', { className: 'mn-skel', style: { width: '55%' } })))
      : h(Fragment, null,
        h(Tabs, { tabs, value: tab, onChange: setTab, label: 'Meeting sections' }),
        h('div', { role: 'tabpanel', id: `mn-panel-${tab}`, 'aria-labelledby': `mn-tab-${tab}`, tabIndex: -1 },
          tab === 'transcript' && h(TranscriptPanel, { meeting, participants, playing, focusId, setFocusId, busy, play, correct }),
          tab === 'notes' && h(NotesPanel, { meeting, participants, rest, play, showSegment }))))
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

let lastSelected = null // survives route remounts within one app session

export function Meetings({ ctx }) {
  const rest = ctx.rest
  const [meetings, setMeetings] = useState([])
  const [loaded, setLoaded] = useState(false)
  const [view, setView] = useState(lastSelected ? 'meeting' : 'home')
  const [selected, setSelected] = useState(null)
  const [tab, setTab] = useState('transcript')
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState(null)
  const [actionError, setActionError] = useState('')
  const [pollError, setPollError] = useState('')
  const [playing, setPlaying] = useState(null)
  const selectedId = useRef(lastSelected)
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
        selectedId.current = null; lastSelected = null; setSelected(null)
        setView(v => (v === 'meeting' ? 'home' : v))
      }
      throw e
    }
    // Drop stale poll results: a different meeting was opened or a correction landed meanwhile.
    if (selectedId.current === id && mutation.current === seq) setSelected(meeting)
  }, [rest])

  const refresh = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true
    try {
      setMeetings(await rest('/meetings'))
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
      : view === 'meeting' && selected ? h(MeetingView, { key: selected.id, meeting: selected, notice, busy, act, rest, play, playing, closePlayer, correct, tab, setTab })
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
      page))
}

export default {
  id: 'meeting_notes',
  name: 'Meeting Notes',
  defaultEnabled: false,
  register(ctx) {
    const style = ensureStyles()
    if (style) ctx.onDispose?.(() => style.remove())
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/meeting-notes' }, render: () => h(Meetings, { ctx }) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/meeting-notes', label: 'Meeting Notes', codicon: 'record' } }
    ])
  }
}
