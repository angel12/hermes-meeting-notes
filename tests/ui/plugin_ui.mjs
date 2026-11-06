// Interaction checks for desktop/plugin.js against a synthetic in-memory backend.
// React, react-dom, jsdom and Testing Library come from the installed Hermes
// checkout (argv[2]); the plugin's two import lines are swapped for those
// instances plus minimal SDK stubs. No real recordings or meetings are used.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

const [hermesRoot, pluginPath] = process.argv.slice(2)
const require = createRequire(path.join(hermesRoot, 'package.json'))
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { url: 'https://webapp.test/', pretendToBeVisual: true })
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'Event', 'KeyboardEvent', 'MouseEvent', 'getComputedStyle', 'HTMLInputElement']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true })
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
dom.window.HTMLElement.prototype.scrollIntoView = function () {}

const React = require('react')
const { render, fireEvent, screen, waitFor, within, cleanup } = require('@testing-library/react')

// Playback bookkeeping: Node's own Blob/URL are used by the plugin.
const created = [], revoked = []
URL.createObjectURL = blob => { const url = `blob:test/${created.length}`; created.push({ url, blob }); return url }
URL.revokeObjectURL = url => { revoked.push(url) }

const sdk = {
  ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'sidebar-nav',
  Button: ({ variant, size, children, ...props }) => React.createElement('button', { ...props, 'data-variant': variant }, children),
  Badge: ({ variant, size, children, ...props }) => React.createElement('span', { ...props, 'data-badge': variant }, children)
}
globalThis.__SDK__ = sdk
globalThis.__REACT__ = React
const source = readFileSync(pluginPath, 'utf8')
  .replace(/import\s*\{([^}]*)\}\s*from\s*'@hermes\/plugin-sdk'/, (_, names) => `const {${names}} = globalThis.__SDK__`)
  .replace(/import\s*\{([^}]*)\}\s*from\s*'react'/, (_, names) => `const {${names.replace(/\s+as\s+/g, ': ')}} = globalThis.__REACT__`)
assert.ok(!/from\s+'/.test(source), 'plugin must only import @hermes/plugin-sdk and react')
const plugin = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

// ── Synthetic backend ────────────────────────────────────────────────────────
const T = 'tr1'
const baseSegments = [
  { id: `${T}:s00000`, label: '0', start: 0, end: 4.2, text: 'Welcome to the synthetic planning sync.', word_count: 6 },
  { id: `${T}:s00001`, label: '1', start: 5, end: 9, text: 'I will send the draft budget by Friday.', word_count: 8 },
  { id: `${T}:s00002`, label: '2', start: 10, end: 75, text: 'Sounds good, Avery can review it.', word_count: 6 }
]

function effective(corrections) {
  const undone = new Set(corrections.filter(c => c.kind === 'undo').map(c => c.value))
  let labels = Object.fromEntries(baseSegments.map(s => [s.label, s.label]))
  const names = {}, over = {}, texts = {}
  for (const c of corrections) {
    if (undone.has(c.id) || c.kind === 'undo') continue
    if (c.kind === 'merge') { const o = labels[c.target], n = labels[c.value]; labels = Object.fromEntries(Object.entries(labels).map(([k, v]) => [k, v === o ? n : v])) }
    if (c.kind === 'rename') names[c.target] = c.value
    if (c.kind === 'speaker') over[c.target] = c.value
    if (c.kind === 'text') texts[c.target] = c.value
  }
  return baseSegments.map(s => { const p = over[s.id] ?? labels[s.label]; return { ...s, participant: p, name: names[p] ?? null, text: texts[s.id] ?? s.text } })
}

function makeBackend({ withAnalysis = false } = {}) {
  const state = { revision: 2, corrections: [{ id: 'c1', revision: 1, kind: 'rename', target: '0', value: 'Host', transcript: T }, { id: 'c2', revision: 2, kind: 'text', target: `${T}:s00000`, value: 'Welcome to the synthetic planning sync.', transcript: T }], uploads: {} }
  const analysis = {
    id: 'a2', transcript: T, revision: 1, created: Date.now() / 1000 - 120, model: 'synthetic-model', needs_review: true, previous_analysis: 'a1', unmatched_previous_items: ['old1'],
    sources: { [`${T}:s00001`]: { ...baseSegments[1], participant: '1', name: null, start: 5.5, end: 9 } },
    items: [{
      id: 'i1', kind: 'commitment', text: 'Send the draft budget.',
      citations: [{ segment_id: `${T}:s00001`, quote: 'I will send the draft budget by Friday.', source_changed: true }],
      owner: { basis: 'speaker', segment_id: `${T}:s00001`, name: null }, resolved_owner: { participant: '1', name: 'Sam', basis: 'speaker' },
      due: { segment_id: `${T}:s00001`, quote: 'by Friday', proposed_date: '2026-11-06' }, date_status: 'needs_confirmation'
    }, {
      id: 'i2', kind: 'suggestion', text: 'Avery could review it.',
      citations: [{ segment_id: `${T}:s00001`, quote: 'draft budget', source_changed: false }],
      owner: { basis: 'unresolved', segment_id: null, name: null }, resolved_owner: null, due: { segment_id: null, quote: null, proposed_date: null }, date_status: 'unresolved'
    }]
  }
  const meeting = () => ({
    id: 'm1', title: 'Synthetic planning sync', date: '2026-11-05T16:00:00-07:00', timezone: 'America/Denver', revision: state.revision, active: T,
    job: { id: 'j1', state: 'completed', error: null }, segments: effective(state.corrections), corrections: state.corrections,
    analysis: withAnalysis ? analysis : null,
    analysis_versions: withAnalysis ? [{ id: 'a2', transcript: T, revision: 1, created: analysis.created }, { id: 'a1', transcript: T, revision: 0, created: analysis.created - 600 }] : []
  })
  const calls = []
  const rest = async (p, opts = {}) => {
    calls.push({ path: p, ...opts, method: opts.method || 'GET' })
    const method = opts.method || 'GET'
    if (p === '/meetings' && method === 'GET') return [{ id: 'm1', title: 'Synthetic planning sync', date: '2026-11-05T16:00:00-07:00', timezone: 'America/Denver', revision: state.revision, state: 'completed', error: null }]
    if (p === '/meetings/m1' && method === 'GET') return meeting()
    if (p === '/meetings/m1/analyses/a1') return { ...analysis, id: 'a1', revision: 0, needs_review: true, unmatched_previous_items: [], items: [{ ...analysis.items[1], id: 'old1', text: 'Historical synthetic note.' }] }
    if (p === '/meetings/m1/corrections' && method === 'POST') {
      const b = opts.body
      assert.deepEqual(Object.keys(b).sort(), ['kind', 'revision', 'target', 'value'])
      if (b.revision !== state.revision) throw new Error('Transcript changed; refresh before correcting')
      state.revision += 1
      state.corrections = [...state.corrections, { id: `c${state.revision}`, revision: state.revision, transcript: T, kind: b.kind, target: b.target, value: b.value }]
      return meeting()
    }
    if (p.startsWith('/meetings/m1/audio?')) return { mime: 'audio/wav', base64: Buffer.from('RIFFsynthetic').toString('base64') }
    if (p === '/uploads' && method === 'POST') { state.uploads.u1 = []; return { id: 'u1', chunk_size: 8 } }
    if (p.startsWith('/uploads/u1/chunks/') && method === 'PUT') { state.uploads.u1.push(opts); return { accepted: true } }
    if (p === '/uploads/u1/finalize') return { id: 'm1', sha256: 'f'.repeat(64) }
    if (p === '/settings') return { endpoint: 'http://asr.test/v1', model: 'synthetic', timeout: 600 }
    if (p === '/analysis-profiles') return { current: 'default', profiles: ['default','research'] }
    if (p === '/worker/start') return { started: true }
    throw new Error(`Unexpected ${method} ${p}`)
  }
  return { rest, calls, state }
}

const mount = backend => render(React.createElement(plugin.Meetings, { ctx: { rest: backend.rest } }))
const mutations = calls => calls.filter(c => c.method !== 'GET')
const tests = []
const test = (name, fn) => tests.push({ name, fn })

// ── Tests ────────────────────────────────────────────────────────────────────

test('pure helpers: offset dates, upload preflight, correction state', () => {
  assert.match(plugin.withOffset('2026-11-05T16:00'), /^2026-11-05T16:00:00[+-]\d{2}:\d{2}$/)
  assert.equal(plugin.withOffset('2026-02-30T10:00'), null)
  assert.equal(plugin.withOffset('2026-11-05T16:61'), null)
  assert.equal(plugin.withOffset(''), null)
  const file = { name: 'a.wav', size: 10 }
  assert.equal(plugin.uploadProblem({ file, title: ' ', date: '2026-11-05T16:00:00-07:00', secure: true }), 'Enter a meeting title.')
  assert.match(plugin.uploadProblem({ file, title: 'x', date: '2026-11-05T16:00:00', secure: true }), /date/)
  assert.match(plugin.uploadProblem({ file, title: 'x', date: '2026-11-05T16:00:00Z', secure: false }), /HTTPS/)
  assert.match(plugin.uploadProblem({ file: { size: 513 * 1048576 }, title: 'x', date: '2026-11-05T16:00:00Z', secure: true }), /512/)
  assert.equal(plugin.uploadProblem({ file, title: 'x', date: '2026-11-05T16:00:00Z', secure: true }), null)
  const cs = [{ id: 'a', kind: 'merge' }, { id: 'b', kind: 'rename' }, { id: 'u', kind: 'undo', value: 'a' }]
  assert.deepEqual(plugin.activeCorrections(cs).map(c => c.id), ['b'])
  assert.equal(plugin.clock(3725), '1:02:05')
  assert.equal(plugin.calendarDate('2026-11-06').includes('2026'), true)
  assert.equal(plugin.dueText({ due: { quote: 'soon', proposed_date: null } }).badge, 'Unconfirmed')
})

test('styles are injected once and scoped', () => {
  const first = plugin.ensureStyles(document)
  assert.ok(first)
  assert.equal(plugin.ensureStyles(document), null)
  const selectors = plugin.CSS.replace(/@(media|container)[^{]*\{/g, '').match(/(^|\})\s*([^{}@]+)\{/g).map(s => s.replace(/^\}?\s*/, '').replace(/\{$/, ''))
  for (const sel of selectors) for (const part of sel.split(',')) assert.match(part.trim(), /^[a-z]*\.mn/, `unscoped selector: ${part}`)
  assert.ok(!/#[0-9a-f]{3,6}\b|rgb\(/i.test(plugin.CSS), 'CSS must use theme variables, not literal colors')
  first.remove()
})

test('mounting and polling only read; selecting a file does not upload', async () => {
  const b = makeBackend()
  mount(b)
  await screen.findByRole('button', { name: /^Synthetic planning sync/ })
  fireEvent.click(screen.getAllByRole('button', { name: 'New recording' })[0])
  const input = await screen.findByLabelText('Recording')
  const file = new File([new Uint8Array(20).map((_, i) => i)], 'synthetic.wav', { type: 'audio/wav' })
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  fireEvent.change(input)
  await screen.findByText(/Selected: synthetic.wav/)
  assert.deepEqual(mutations(b.calls), [])
  fireEvent.click(screen.getByRole('button', { name: 'Upload and transcribe' }))
  await screen.findByRole('alert')
  assert.match(screen.getByRole('alert').textContent, /title/)
  assert.deepEqual(mutations(b.calls), [], 'missing title must not contact the backend')
})

test('upload hashes 8-byte synthetic chunks, finalizes and opens the meeting', async () => {
  const b = makeBackend()
  mount(b)
  await screen.findByRole('button', { name: /^Synthetic planning sync/ })
  fireEvent.click(screen.getAllByRole('button', { name: 'New recording' })[0])
  fireEvent.change(await screen.findByLabelText('Meeting title'), { target: { value: 'Synthetic upload' } })
  const input = screen.getByLabelText('Recording')
  const bytes = new Uint8Array(20).map((_, i) => i)
  // The button reads the input's live FileList even without a change event.
  Object.defineProperty(input, 'files', { value: [new File([bytes], 'synthetic.wav')], configurable: true })
  fireEvent.click(screen.getByRole('button', { name: 'Upload and transcribe' }))
  await screen.findByText(/already imported/, {}, { timeout: 4000 })
  const writes = mutations(b.calls)
  assert.deepEqual(writes.map(c => `${c.method} ${c.path.split('?')[0]}`), ['POST /uploads', 'PUT /uploads/u1/chunks/0', 'PUT /uploads/u1/chunks/1', 'PUT /uploads/u1/chunks/2', 'POST /uploads/u1/finalize'])
  const start = writes[0].body
  assert.equal(start.title, 'Synthetic upload')
  assert.equal(start.size, 20)
  assert.match(start.date, /[+-]\d{2}:\d{2}$/)
  assert.ok(start.timezone)
  const { createHash } = await import('node:crypto')
  writes.slice(1, 4).forEach((c, i) => {
    const expected = createHash('sha256').update(bytes.slice(i * 8, i * 8 + 8)).digest('hex')
    assert.equal(new URL(c.path, 'https://x').searchParams.get('sha'), expected)
    assert.equal(c.timeoutMs, 30000)
    assert.equal(c.upload.bytes.byteLength, Math.min(8, 20 - i * 8))
  })
  assert.equal(writes[4].timeoutMs, 30000)
  assert.ok(screen.getByRole('heading', { name: 'Synthetic planning sync' }))
})

async function openMeeting(b) {
  mount(b)
  fireEvent.click(await screen.findByRole('button', { name: /^Synthetic planning sync/ }))
  await screen.findByRole('heading', { name: 'Synthetic planning sync' })
}

test('speakers panel: merge with confirmation, rename, jump, undo send the current revision', async () => {
  const b = makeBackend()
  await openMeeting(b)
  const panel = screen.getByRole('complementary', { name: 'Speakers' })
  assert.ok(screen.getByText('3 speakers'))
  const row = () => within(panel).getByRole('listitem', { name: 'Participant 2' })
  fireEvent.click(within(row()).getByRole('button', { name: 'Merge Participant 2 into another speaker' }))
  fireEvent.change(within(row()).getByLabelText('Merge Participant 2 into'), { target: { value: '1' } })
  assert.ok(within(row()).getByText(/1 turn \(1 min of transcribed speech\) become Participant 1's/))
  assert.deepEqual(mutations(b.calls), [], 'opening merge does not write')
  fireEvent.click(within(row()).getByRole('button', { name: 'Merge' }))
  await screen.findByText('2 speakers')
  assert.deepEqual(mutations(b.calls).at(-1).body, { kind: 'merge', target: '2', value: '1', revision: 2 })
  const p1 = within(panel).getByRole('listitem', { name: 'Participant 1' })
  fireEvent.click(within(p1).getByRole('button', { name: 'Rename Participant 1' }))
  const input = within(p1).getByLabelText('Name for Participant 1')
  fireEvent.change(input, { target: { value: 'Sam' } })
  fireEvent.submit(input.form)
  await within(panel).findByRole('listitem', { name: 'Sam' })
  assert.deepEqual(mutations(b.calls).at(-1).body, { kind: 'rename', target: '1', value: 'Sam', revision: 3 })
  fireEvent.click(within(panel).getByRole('button', { name: 'Next turn by Host' }))
  await waitFor(() => assert.equal(document.querySelector(`[data-seg="${T}:s00000"]`).getAttribute('data-flash'), 'true'))
  fireEvent.click(screen.getByRole('button', { name: /^Undo: Merged Participant 2/ }))
  await screen.findByText('3 speakers')
  assert.deepEqual(mutations(b.calls).at(-1).body, { kind: 'undo', target: '', value: 'c3', revision: 4 })
  assert.ok(screen.getByText('Undone'))
  // Toolbar undo targets the most recent change still in effect (the rename).
  fireEvent.click(screen.getByRole('button', { name: /^Undo last change: Named Participant 1/ }))
  await waitFor(() => assert.deepEqual(mutations(b.calls).at(-1).body, { kind: 'undo', target: '', value: 'c4', revision: 5 }))
})

test('turn chip reassigns the speaker; inline text edit saves on Enter', async () => {
  const b = makeBackend()
  await openMeeting(b)
  fireEvent.click(screen.getByRole('button', { name: 'Participant 2 at 0:10. Change speaker' }))
  const picker = screen.getByRole('group', { name: 'Reassign segment at 0:10' })
  fireEvent.click(within(picker).getByRole('button', { name: 'Host' }))
  await screen.findByText('Speaker corrected')
  fireEvent.click(screen.getByRole('button', { name: 'Edit text at 0:10' }))
  const box = screen.getByLabelText('Segment text')
  fireEvent.change(box, { target: { value: 'Sounds good.' } })
  fireEvent.keyDown(box, { key: 'Enter' })
  await screen.findByText('Sounds good.')
  assert.deepEqual(mutations(b.calls).map(c => c.body), [
    { kind: 'speaker', target: `${T}:s00002`, value: '0', revision: 2 },
    { kind: 'text', target: `${T}:s00002`, value: 'Sounds good.', revision: 3 }])
  fireEvent.click(screen.getByRole('button', { name: 'Edit text at 0:05' }))
  fireEvent.keyDown(screen.getByLabelText('Segment text'), { key: 'Escape' })
  assert.equal(screen.queryByLabelText('Segment text'), null)
  assert.equal(mutations(b.calls).length, 2, 'Escape cancels without writing')
})

test('notes show review warnings, unconfirmed dates, saved-source playback and history', async () => {
  const b = makeBackend({ withAnalysis: true })
  await openMeeting(b)
  fireEvent.click(screen.getByRole('tab', { name: /Notes/ }))
  assert.ok(screen.getByText('Notes may be out of date'))
  assert.ok(screen.getByRole('button', { name: 'Start summarization session' }))
  assert.equal(screen.getByLabelText('Notes request to paste into Hermes chat').value, 'Use the meeting_notes:analyze skill to generate and save sourced notes for meeting m1.')
  assert.ok(screen.getByText('Sam'))
  assert.ok(screen.getByText('Speaker of the commitment'))
  assert.ok(screen.getByText('Unconfirmed'))
  assert.ok(screen.getByText(/from “by Friday”/))
  assert.ok(screen.getByText(/Transcript changed since these notes/))
  assert.ok(screen.getByText(/not commitments/))
  assert.ok(!screen.queryByRole('button', { name: /^Generate/ }), 'no fake generate button')
  const before = created.length
  fireEvent.click(screen.getAllByRole('button', { name: /^Play source at 0:05/ })[0])
  await screen.findByRole('region', { name: 'Audio playback' })
  const audioCall = b.calls.filter(c => c.path.includes('/audio?')).at(-1)
  assert.equal(new URL(audioCall.path, 'https://x').searchParams.get('start'), '5.5', 'uses analysis.sources timing')
  fireEvent.click(screen.getAllByRole('button', { name: /^Play source at 0:05/ })[1])
  await waitFor(() => assert.equal(created.length, before + 2))
  assert.ok(revoked.includes(created[before].url), 'previous blob URL is revoked')
  fireEvent.change(screen.getByLabelText('Notes version'), { target: { value: 'a1' } })
  await screen.findByText('Historical synthetic note.')
  assert.ok(screen.getByText('Viewing an earlier version'))
  fireEvent.click(screen.getByRole('button', { name: 'Back to latest notes' }))
  await screen.findByText('Send the draft budget.')
  fireEvent.click(screen.getAllByRole('button', { name: 'Show in transcript' })[0])
  await waitFor(() => assert.equal(screen.getByRole('tab', { name: /Transcript/ }).getAttribute('aria-selected'), 'true'))
  assert.deepEqual(mutations(b.calls), [], 'reading notes never writes')
})

test('tabs support arrow-key navigation', async () => {
  const b = makeBackend()
  await openMeeting(b)
  const first = screen.getByRole('tab', { name: /Transcript/ })
  fireEvent.keyDown(first, { key: 'ArrowRight' })
  assert.equal(screen.getByRole('tab', { name: /Notes/ }).getAttribute('aria-selected'), 'true')
  fireEvent.keyDown(first, { key: 'Home' })
  assert.equal(screen.getByRole('tab', { name: /Transcript/ }).getAttribute('aria-selected'), 'true')
})

test('merges preserve explicit overrides and cannot target an obsolete label', async () => {
  const b = makeBackend()
  b.state.corrections.push({ id: 'c3', revision: 3, kind: 'merge', target: '2', value: '1', transcript: T })
  b.state.corrections.push({ id: 'c4', revision: 4, kind: 'speaker', target: `${T}:s00002`, value: '2', transcript: T })
  b.state.revision = 4
  await openMeeting(b)
  const panel = screen.getByRole('complementary', { name: 'Speakers' })
  assert.equal(within(panel).queryByRole('button', { name: 'Merge Participant 2 into another speaker' }), null)
  fireEvent.click(within(panel).getByRole('button', { name: 'Merge Host into another speaker' }))
  const select = within(panel).getByLabelText('Merge Host into')
  assert.deepEqual([...select.options].map(o => o.value), ['1'])
  assert.ok(within(panel).getByText(/Individual speaker overrides stay unchanged/))
  fireEvent.click(within(panel).getByRole('button', { name: 'Merge' }))
  await screen.findByText('2 speakers')
  assert.equal(effective(b.state.corrections)[2].participant, '2')
})

const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

test('late meeting responses cannot replace a newer selection', async () => {
  const b = makeBackend(), original = b.rest, slow = deferred()
  let hold = false
  const first = await original('/meetings/m1')
  const second = { ...first, id: 'm2', title: 'Second synthetic meeting' }
  b.rest = async (p, opts) => {
    if (p === '/meetings') return [...await original(p, opts), { ...second, state: 'completed' }]
    if (p === '/meetings/m2') return second
    if (p === '/meetings/m1' && hold) { hold = false; return slow.promise }
    return original(p, opts)
  }
  await openMeeting(b)
  hold = true
  fireEvent.click(screen.getByRole('button', { name: /^Synthetic planning sync/ }))
  fireEvent.click(screen.getByRole('button', { name: /^Second synthetic meeting/ }))
  await screen.findByRole('heading', { name: 'Second synthetic meeting' })
  await React.act(async () => slow.resolve(first))
  assert.equal(screen.queryByRole('heading', { name: 'Synthetic planning sync' }), null)
  assert.ok(screen.getByRole('heading', { name: 'Second synthetic meeting' }))
})

test('playback ignores superseded requests and requests resolved after navigation', async () => {
  const b = makeBackend(), original = b.rest, pending = []
  b.rest = async (p, opts) => {
    if (p.includes('/audio?')) { const request = deferred(); pending.push(request); return request.promise }
    return original(p, opts)
  }
  await openMeeting(b)
  const response = { mime: 'audio/wav', base64: Buffer.from('RIFFsynthetic').toString('base64') }
  const before = created.length
  fireEvent.click(screen.getByRole('button', { name: 'Play from 0:00, Host' }))
  fireEvent.click(screen.getByRole('button', { name: 'Play from 0:05, Participant 1' }))
  await React.act(async () => pending[1].resolve(response))
  const url = document.querySelector('audio').getAttribute('src')
  await React.act(async () => pending[0].resolve(response))
  assert.equal(created.length, before + 1)
  assert.equal(document.querySelector('audio').getAttribute('src'), url)
  fireEvent.click(screen.getByRole('button', { name: 'Play from 0:10, Participant 2' }))
  fireEvent.click(screen.getByRole('button', { name: 'New recording' }))
  await React.act(async () => pending[2].resolve(response))
  assert.equal(created.length, before + 1)
  assert.ok(revoked.includes(url))
  assert.equal(screen.queryByRole('region', { name: 'Audio playback' }), null)
})

test('analysis profile selector persists revision and does not run a model', async () => {
  const calls = []
  const rest = async (path, opts = {}) => {
    calls.push({ path, ...opts })
    if (path === '/analysis-profiles') return { current: 'default', profiles: ['default', 'research'] }
    return { id: 'm1', owner_profile: 'default', analysis_profile: 'research', revision: 5 }
  }
  const meeting = { id: 'm1', owner_profile: 'default', analysis_profile: 'default', revision: 4 }
  const act = async (_, fn) => fn(rest)
  const view = render(React.createElement(plugin.AnalysisProfile, { meeting, rest, act, busy: false }))
  await screen.findByRole('option', { name: 'research' })
  assert.equal(calls.length, 1)
  fireEvent.change(screen.getByLabelText('Analysis profile'), { target: { value: 'research' } })
  await waitFor(() => assert.equal(calls.length, 2))
  assert.deepEqual(calls[1], { path: '/meetings/m1/analysis-profile', method: 'PUT', body: { profile: 'research', revision: 4 } })
  assert.match(plugin.notesRequest({ ...meeting, analysis_profile: 'research' }), /source_profile="default".*profile "research"/)
  view.rerender(React.createElement(plugin.AnalysisProfile, { meeting: { ...meeting, analysis_profile: 'deleted' }, rest, act, busy: false }))
  assert.equal(screen.getByLabelText('Analysis profile').value, 'deleted')
  assert.ok(screen.getByText(/saved profile is unavailable/))
})

test('profile chat routing stays on the importing connection and fails closed', async () => {
  const routes = [
    { connectionId: 'host-a', profile: 'research-alias', targetProfile: 'research', mode: 'remote' },
    { connectionId: 'host-b', profile: 'research', targetProfile: 'research', mode: 'remote' }
  ]
  const hostApi = { profileRoutes: async () => routes, newChat() { throw new Error('routing discovery must not open chats') } }
  assert.equal((await plugin.analysisChatRoute(hostApi, 'research', 'host-a')).profile, 'research-alias')
  await assert.rejects(plugin.analysisChatRoute(hostApi, 'research', 'host-missing'), /not uniquely reachable/)
  await assert.rejects(plugin.analysisChatRoute(hostApi, 'research', null), /not uniquely reachable/)
  routes.push({ ...routes[0], profile: 'duplicate' })
  await assert.rejects(plugin.analysisChatRoute(hostApi, 'research', 'host-a'), /not uniquely reachable/)
})

test('combine form orders snapshots and preserves request ID after an uncertain response', async () => {
  const meetings = [1, 2].map(i => ({ id: `part${i}`, title: `Recording ${i}`, date: '2026-11-05T16:00:00-07:00', timezone: 'America/Denver', revision: i, active: `transcript${i}`, state: 'completed' }))
  meetings.push({ ...meetings[0], id: 'combined', title: 'Already combined', combined: true })
  const calls = [], opened = [], busy = []
  const rest = async (path, options) => {
    calls.push({ path, ...options })
    if (calls.length === 1) throw new Error('Connection interrupted')
    return { id: 'new-combined' }
  }
  render(React.createElement(plugin.CombineForm, { meetings, rest, onCreated: id => opened.push(id), onBusy: value => busy.push(value) }))
  const picker = screen.getByLabelText('Add recording in playback order')
  assert.equal(screen.queryByRole('option', { name: /Already combined/ }), null)
  assert.ok(screen.getByRole('button', { name: 'Create combined meeting' }).disabled)
  fireEvent.change(picker, { target: { value: 'part1' } })
  fireEvent.change(picker, { target: { value: 'part2' } })
  assert.equal(screen.getByLabelText('Meeting timezone').value, 'America/Denver')
  fireEvent.click(screen.getByRole('button', { name: 'Move recording 2 up' }))
  assert.match(within(screen.getByRole('list', { name: 'Recording order' })).getAllByRole('listitem')[0].textContent, /^Recording 2/)
  fireEvent.change(screen.getByLabelText('Combined meeting title'), { target: { value: 'Full session' } })
  assert.equal(calls.length, 0, 'selecting and reordering must not write')
  fireEvent.click(screen.getByRole('button', { name: 'Create combined meeting' }))
  await screen.findByRole('alert')
  assert.match(screen.getByRole('alert').textContent, /Connection interrupted/)
  fireEvent.click(screen.getByRole('button', { name: 'Create combined meeting' }))
  await waitFor(() => assert.deepEqual(opened, ['new-combined']))
  assert.deepEqual(calls[1], calls[0], 'a retry must use the same operation ID and source snapshots')
  assert.equal(calls[0].path, '/meetings/combine')
  assert.deepEqual(calls[0].body.sources, [{ id: 'part2', active: 'transcript2', revision: 2 }, { id: 'part1', active: 'transcript1', revision: 1 }])
  assert.deepEqual(busy, [true, false, true, false])
})

test('combine selection can refresh revisions without losing order', async () => {
  const meetings = [1,2].map(i => ({ id: `part${i}`, title: `Part ${i}`, date: '2026-11-05T16:00:00-07:00', timezone: 'America/Denver', revision: 0, active: `tr${i}`, state: 'completed' }))
  const calls=[]
  const rest=async (path,opts={}) => {
    calls.push({path,...opts})
    if (path==='/meetings') return meetings.map(m=>({...m,revision:3}))
    return {id:'combined'}
  }
  render(React.createElement(plugin.CombineForm,{meetings,rest,onCreated:()=>{},onBusy:()=>{}}))
  const picker=screen.getByLabelText('Add recording in playback order')
  fireEvent.change(picker,{target:{value:'part2'}})
  fireEvent.change(picker,{target:{value:'part1'}})
  fireEvent.click(screen.getByRole('button',{name:'Refresh selected transcripts'}))
  await screen.findByText('Part 2 · revision 3')
  fireEvent.click(screen.getByRole('button',{name:'Create combined meeting'}))
  await waitFor(()=>assert.equal(calls.length,2))
  assert.deepEqual(calls[1].body.sources,[{id:'part2',active:'tr2',revision:3},{id:'part1',active:'tr1',revision:3}])
})

test('starting a session fails closed when profile routing is unavailable', async () => {
  const original=Object.getOwnPropertyDescriptor(globalThis,'navigator')
  const copied=[]
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>copied.push(text)}}})
  try {
    render(React.createElement(plugin.RequestBox,{meeting:{id:'m1',owner_profile:'default',analysis_profile:'research'},hasNotes:false,
      hostApi:{profileRoutes:async()=>[],newChat:()=>assert.fail('Unavailable route must not open a chat')}}))
    fireEvent.click(screen.getByRole('button',{name:'Start summarization session'}))
    await screen.findByText(/The selected profile is not uniquely reachable/)
    assert.equal(copied.length,1)
  } finally {
    if(original) Object.defineProperty(globalThis,'navigator',original)
    else delete globalThis.navigator
  }
})

test('new summarization session fills the selected profile', async () => {
  const calls=[]
  const route={connectionId:'host-a',targetProfile:'research',mode:'remote'}
  let finishRoute
  const hostApi={
    state:{connectionId:{get:()=> 'host-a'}},
    profileRoutes:()=>new Promise(resolve=>{finishRoute=()=>resolve([route])}),
    newChat:target=>calls.push(['open',target]),
    composer:{insertText:async (...args)=>{calls.push(['insert',...args]);return true}},
    notify:message=>calls.push(['notify',message])
  }
  render(React.createElement(plugin.RequestBox,{meeting:{id:'m1',owner_profile:'default',analysis_profile:'research'},hostApi}))
  fireEvent.click(screen.getByRole('button',{name:'Start summarization session'}))
  assert.equal(screen.getByRole('button',{name:'Opening…'}).disabled,true)
  await React.act(async()=>finishRoute())
  await screen.findByText('Summarization session ready. Send the request to generate and save notes.')
  assert.deepEqual(calls[0],['open',route])
  assert.equal(calls[1][0],'insert')
  assert.equal(calls[1][1],'new')
  assert.match(calls[1][2],/meeting m1.*source_profile="default".*profile "research"/)
  assert.equal(calls.length,3)
})

test('older hosts open a new session with a clipboard fallback', async () => {
  const original=Object.getOwnPropertyDescriptor(globalThis,'navigator')
  const copied=[],opened=[]
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>copied.push(text)}}})
  try {
    render(React.createElement(plugin.RequestBox,{meeting:{id:'m1'},hostApi:{newChat:target=>opened.push(target)}}))
    fireEvent.click(screen.getByRole('button',{name:'Start summarization session'}))
    await screen.findByText('New session opened. Paste the copied request and send it to generate notes.')
    assert.equal(opened.length,1)
    assert.equal(copied[0],plugin.notesRequest({id:'m1'}))
  } finally {
    if(original) Object.defineProperty(globalThis,'navigator',original)
    else delete globalThis.navigator
  }
})

test('failed composer insertion reports recovery through a host notification', async () => {
  const notices=[]
  render(React.createElement(plugin.RequestBox,{meeting:{id:'m1'},hostApi:{
    newChat:()=>{},composer:{insertText:async()=>false},notify:notice=>notices.push(notice)
  }}))
  fireEvent.click(screen.getByRole('button',{name:'Start summarization session'}))
  await waitFor(()=>assert.equal(notices.length,1),{timeout:15000})
  assert.equal(notices[0].kind,'error')
  assert.match(notices[0].message,/Return to Notes/)
})

test('draft fill waits for mounting and retries a late fresh-draft reset', async () => {
  let tick=0, draft=null, inserts=0
  const hostApi={composer:{
    getDraft:async address=>{assert.equal(address,'new');return draft},
    insertText:async (address,text)=>{assert.equal(address,'new');if(tick<3)return false;inserts++;draft=text;return true}
  }}
  const filled=await plugin.fillNotesDraft(hostApi,'notes',null,{attempts:8,wait:async()=>{tick++;if(tick===4)draft=null}})
  assert.equal(filled,true)
  assert.equal(inserts,2)
  assert.equal(draft,'notes')
})

test('draft fill waits for profile activation and preserves user text', async () => {
  let profile='default', inserts=0, ticks=0, reads=0
  const hostApi={state:{profile:{get:()=>profile}},composer:{getDraft:async()=>{reads++;assert.equal(profile,'research');return 'My own draft'},insertText:async()=>{inserts++;return true}}}
  assert.equal(await plugin.fillNotesDraft(hostApi,'notes',{targetProfile:'research'},{attempts:4,wait:async()=>{if(++ticks===3)profile='research'}}),false)
  assert.equal(ticks,3)
  assert.equal(reads,1)
  assert.equal(inserts,0)
})

test('failed draft fill copies the request on a modern host', async () => {
  const original=Object.getOwnPropertyDescriptor(globalThis,'navigator')
  const copied=[]
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>copied.push(text)}}})
  try {
    render(React.createElement(plugin.RequestBox,{meeting:{id:'m1'},hostApi:{newChat:()=>{},
      composer:{getDraft:async()=> 'User draft',insertText:async()=>assert.fail('Must preserve user draft')}}}))
    fireEvent.click(screen.getByRole('button',{name:'Start summarization session'}))
    await screen.findByText('New session opened. Paste the copied request and send it to generate notes.')
    assert.deepEqual(copied,[plugin.notesRequest({id:'m1'})])
  } finally {
    if(original) Object.defineProperty(globalThis,'navigator',original)
    else delete globalThis.navigator
  }
})

test('transcription settings save audio duration independently from HTTP timeout', async () => {
  const calls=[]
  const rest=async (path,opts={})=>{
    calls.push({path,...opts})
    return opts.body || {endpoint:'http://asr.test/v1',model:'nemotron-3.5-transcribe-30b-a4b',timeout:600,max_audio_seconds:3600}
  }
  render(React.createElement(plugin.SettingsForm,{rest}))
  const duration=await screen.findByLabelText('Maximum audio per request (minutes)')
  assert.equal(duration.value,'60')
  fireEvent.change(duration,{target:{value:'20'}})
  fireEvent.click(screen.getByRole('button',{name:'Save settings'}))
  await screen.findByText(/New transcription plans use them/)
  assert.equal(calls[1].body.max_audio_seconds,1200)
  assert.equal(calls[1].body.timeout,600)
})

test('chunk progress and completed boundary review are visible', async () => {
  const b=makeBackend()
  const original=b.rest
  let completed=false
  b.rest=async (path,opts)=>{
    const result=await original(path,opts)
    if(path==='/meetings/m1') return {...result,active:completed?T:null,
      job:{id:'j1',state:completed?'completed':'in_flight'},
      transcription_chunks:[{index:1,start:0,duration:60,state:'completed',recognized_segments:3},{index:2,start:60,duration:30,state:completed?'completed':'in_flight',recognized_segments:completed?0:null}]}
    return result
  }
  await openMeeting(b)
  assert.ok(await screen.findByText(/1 of 2 audio chunks completed/))
  cleanup(); completed=true
  await openMeeting(b)
  assert.ok(screen.getByText('2 transcription chunks'))
  assert.ok(screen.getByText(/no words returned; review the audio/))
})

let failed = 0
for (const t of tests) {
  try { await t.fn(); console.log(`ok - ${t.name}`) }
  catch (e) { failed++; console.error(`FAIL - ${t.name}\n${e.stack}`) }
  finally { cleanup() }
}
process.exit(failed ? 1 : 0)
