import test from 'node:test'
import assert from 'node:assert/strict'
import { streamDocumentTask } from './documentTaskEvents.js'
globalThis.localStorage = { getItem: () => null }
const response = frames => new Response(new ReadableStream({ start(controller) { for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame)); controller.close() } }), { headers: { 'Content-Type': 'text/event-stream' } })

test('document event reconnect resumes cursor and suppresses replayed deltas', async () => {
  let attempts = 0, cursor = '0', draft = '', completed = false
  const paths = []
  globalThis.fetch = async path => {
    paths.push(path)
    return ++attempts === 1 ? response(['id: 1\nevent: delta\ndata: {"text":"第一"}\n\n']) : response([
      'id: 1\nevent: delta\ndata: {"text":"第一"}\n\nid: 2\nevent: delta\ndata: {"text":"部分"}\n\n',
      'id: 3\nevent: complete\ndata: {"answer":"完整回答"}\n\n'
    ])
  }
  await streamDocumentTask('task', { signal: new AbortController().signal, after: () => cursor, onCursor: id => { cursor = id }, onEvent: (type, data) => {
    if (type === 'delta') draft += data.text
    if (type === 'complete') { completed = true; return true }
    return false
  } })
  assert.equal(draft, '第一部分'); assert.equal(cursor, '3'); assert.equal(completed, true)
  assert.ok(paths[1].endsWith('after=1')); assert.ok(paths.every(path => path.includes('/events?')))
})

test('expired identity cannot consume or reconnect to document events', async () => {
  let requests = 0, callbacks = 0
  globalThis.fetch = async () => { requests++; return response(['event: complete\ndata: {}\n\n']) }
  await streamDocumentTask('task', { signal: new AbortController().signal, active: () => false, onEvent: () => { callbacks++ } })
  assert.equal(requests, 0); assert.equal(callbacks, 0)
})
