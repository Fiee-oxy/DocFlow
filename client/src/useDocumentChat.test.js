import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { reactive, nextTick } from 'vue'
import { useDocumentChat } from './useDocumentChat.js'
import { setAuthToken } from './api.js'
const json = data => Response.json({ code: 0, message: 'success', data })
const response = { answer: '依据原文的回答', citations: [] }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setTimeout(resolve, 0)) }
beforeEach(() => {
  const storage = new Map()
  globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
  setAuthToken('owner')
})
function setup(options) {
  const props = reactive({ document: { id: 'doc-one', totalBlocks: 1 }, user: { id: 7 }, uploading: false })
  const chat = useDocumentChat(props, () => {}, options)
  return { props, chat }
}
test('a late answer cannot enter another document conversation', async () => {
  const answer = deferred()
  globalThis.fetch = async (_url, options) => options.method === 'POST' ? answer.promise : json([])
  const { props, chat } = setup(); await flush()
  chat.draft.value = '原文中的限制是什么？'; const sending = chat.send()
  props.document = { id: 'doc-two', totalBlocks: 1 }; await nextTick(); await flush()
  answer.resolve(json(response)); await sending
  assert.equal(chat.turns.value.length, 0); assert.equal(chat.sending.value, false)
  chat.dispose()
})

test('streaming chat submits once and replays only new events after a dropped connection', async () => {
  let posts = 0, streams = 0; const cursors = []
  const frame = (id, type, data) => `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') { posts++; assert.deepEqual(JSON.parse(options.body).documentIds, ['doc-one']); return json({}) }
    if (!url.includes('/events?')) return json([])
    cursors.push(new URL(url, 'http://local').searchParams.get('after')); streams++
    return new Response(streams === 1 ? frame(1, 'delta', { text: '依据' }) :
      frame(1, 'delta', { text: '依据' }) + frame(2, 'delta', { text: '原文' }) + frame(3, 'complete', response),
      { headers: { 'Content-Type': 'text/event-stream' } })
  }
  const { chat } = setup({ streaming: true }); await flush(); chat.draft.value = '问题'
  await chat.send()
  assert.equal(posts, 1); assert.equal(streams, 2); assert.deepEqual(cursors, ['0', '1'])
  assert.deepEqual(chat.turns.value[0].response, response); assert.equal(chat.error.value, '')
  chat.dispose()
})

test('terminal SSE failure permits a new request instead of reusing a failed task', async () => {
  const ids = []
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') { ids.push(JSON.parse(options.body).requestId); return json({}) }
    if (!url.includes('/events?')) return json([])
    return new Response('id: 1\nevent: error\ndata: {"message":"引用未通过"}\n\n')
  }
  const { chat } = setup({ streaming: true }); await flush(); chat.draft.value = '问题'
  await chat.send(); assert.ok(chat.error.value.includes('引用未通过')); assert.equal(chat.pending.value, null)
  await chat.send(); assert.notEqual(ids[0], ids[1]); assert.equal(chat.turns.value.length, 0)
  chat.dispose()
})
test('retry after a lost response reuses request ID and retains the question', async () => {
  const requests = []; let attempt = 0
  globalThis.fetch = async (_url, options) => {
    if (options.method !== 'POST') return json([])
    requests.push(JSON.parse(options.body)); if (++attempt === 1) throw new Error('connection lost')
    return json(response)
  }
  const { chat } = setup(); await flush(); chat.draft.value = '问题一'
  await chat.send(); assert.equal(chat.draft.value, '问题一'); assert.ok(chat.error.value)
  await chat.send(); assert.equal(requests[0].requestId, requests[1].requestId)
  assert.equal(chat.turns.value.length, 1); assert.equal(chat.draft.value, '')
  chat.dispose()
})
test('a late history response cannot replace another document history', async () => {
  const history = deferred()
  globalThis.fetch = async url => url.includes('doc-one') ? history.promise : json([])
  const { props, chat } = setup(); props.document = { id: 'doc-two', totalBlocks: 1 }; await nextTick(); await flush()
  history.resolve(json([{ requestId: 'old', question: '旧文档问题', response }])); await flush()
  assert.equal(chat.turns.value.length, 0); assert.equal(chat.historyLoading.value, false)
  chat.dispose()
})
test('questions wait for parsed blocks and typed draft survives attachment completion', async () => {
  let calls = 0; globalThis.fetch = async () => { calls++; return json([]) }
  const { props, chat } = setup(); await flush()
  props.document = null; props.uploading = true; await nextTick(); chat.draft.value = '上传后想问的问题'
  await chat.send(); assert.equal(calls, 1)
  props.document = { id: 'new-upload', totalBlocks: 0 }; await nextTick(); await flush()
  assert.equal(chat.draft.value, '上传后想问的问题'); assert.equal(Boolean(chat.ready.value), false)
  props.uploading = false; props.document.totalBlocks = 2; assert.equal(Boolean(chat.ready.value), true)
  chat.dispose()
})
test('status polling of the same document preserves the draft and in-flight answer', async () => {
  const answer = deferred(); let historyReads = 0
  globalThis.fetch = async (_url, options) => {
    if (options.method === 'POST') return answer.promise
    historyReads++; return json([])
  }
  const { props, chat } = setup(); await flush(); chat.draft.value = '正在提问'
  const sending = chat.send()
  props.document = { id: 'doc-one', totalBlocks: 2 }; await nextTick()
  assert.equal(chat.sending.value, true); assert.equal(chat.draft.value, '正在提问')
  assert.equal(historyReads, 1)
  answer.resolve(json(response)); await sending
  assert.equal(chat.turns.value.length, 1); chat.dispose()
})
