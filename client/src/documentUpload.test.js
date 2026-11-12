import test from 'node:test'
import assert from 'node:assert/strict'

const values = new Map()
globalThis.localStorage = {
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: key => values.delete(key)
}
const { uploadDocument, validateDocument, DOCUMENT_CHUNK_BYTES } = await import('./documentUpload.js')
const envelope = (data, status = 200, message = 'success') => new Response(JSON.stringify({ code: status >= 400 ? status : 0, message, data }), { status, headers: { 'Content-Type': 'application/json' } })
const hashValue = 'a'.repeat(64), partHash = 'b'.repeat(64)
const dependencies = {
  wait: async () => {},
  hashDocument: async (file, { partSize, signal, onProgress }) => {
    if (signal?.aborted) throw new DOMException('paused', 'AbortError')
    onProgress({ bytesHashed: file.size, totalBytes: file.size, percent: 100 })
    return { sha256: hashValue, partSize, partHashes: Array(Math.ceil(file.size / partSize)).fill(partHash) }
  }
}
function file(size = 12, name = '教材.docx') {
  const blob = new Blob([new Uint8Array(size)])
  Object.defineProperties(blob, { name: { value: name }, lastModified: { value: 1 } })
  return blob
}
function init(target, overrides = {}) {
  return { mode: 'NEW', uploadId: 'upload-id', chunkSizeBytes: DOCUMENT_CHUNK_BYTES, totalChunks: Math.ceil(target.size / DOCUMENT_CHUNK_BYTES), completedChunks: [], ...overrides }
}
function upload(target, progress = () => {}, signal, scope = {}) {
  return uploadDocument(target, 7, progress, signal, scope, dependencies)
}

test('pause preserves content receipt; resume finds server task and sends only missing parts', async () => {
  values.clear()
  const controller = new AbortController(), sent = [], phases = []
  const target = file(DOCUMENT_CHUNK_BYTES + 1)
  let initialized = 0
  globalThis.fetch = async (url, options) => {
    if (url === '/documents/uploads/init') {
      const body = JSON.parse(options.body)
      assert.equal(body.sha256, hashValue); assert.equal(body.sizeBytes, target.size)
      return envelope(init(target, initialized++ ? { mode: 'RESUME', completedChunks: [0] } : {}))
    }
    if (url === '/documents/upload-chunk') {
      sent.push(Number(options.body.get('chunkIndex')))
      assert.equal(options.body.get('sha256'), partHash)
      return envelope(null)
    }
    if (url.startsWith('/documents/complete-upload')) return envelope({ id: 'upload-id', status: 'QUEUED' }, 202)
    throw new Error(`Unexpected ${url}`)
  }
  await assert.rejects(upload(target, state => {
    phases.push(state.phase)
    if (state.phase === 'uploading' && state.completed === 1) controller.abort()
  }, controller.signal), { name: 'AbortError' })
  assert.deepEqual(sent, [0])
  assert.ok([...values.keys()].some(key => key.endsWith(`:${hashValue}`)))
  const result = await upload(target)
  assert.deepEqual(sent, [0, 1]); assert.equal(result.status, 'QUEUED'); assert.equal(values.size, 0)
  assert.ok(phases.includes('hashing')); assert.ok(phases.includes('initializing'))
})

test('document validation rejects unsupported formats and oversized files', () => {
  assert.match(validateDocument({ name: 'video.mp4', size: 10 }), /DOCX/)
  assert.match(validateDocument({ name: 'book.pdf', size: 0 }), /大小/)
  assert.equal(validateDocument({ name: 'BOOK.PDF', size: 10 }), '')
})

test('content matching resumes a renamed file without any browser receipt', async () => {
  values.clear()
  const target = file(DOCUMENT_CHUNK_BYTES + 1, 'renamed.pdf'), sent = []
  globalThis.fetch = async (url, options) => {
    if (url === '/documents/uploads/init') return envelope(init(target, { mode: 'RESUME', completedChunks: [0] }))
    if (url === '/documents/upload-chunk') { sent.push(Number(options.body.get('chunkIndex'))); return envelope(null) }
    if (url.startsWith('/documents/complete-upload')) return envelope({ id: 'upload-id', status: 'QUEUED' })
    throw new Error(`Unexpected ${url}`)
  }
  await upload(target)
  assert.deepEqual(sent, [1])
})

test('INSTANT returns document without sending any part or completion request', async () => {
  values.clear()
  const phases = [], paths = []
  globalThis.fetch = async url => {
    paths.push(url)
    return envelope({ mode: 'INSTANT', document: { id: 'existing-document', status: 'COMPLETED' } })
  }
  assert.equal((await upload(file(), state => phases.push(state.phase))).id, 'existing-document')
  assert.deepEqual(paths, ['/documents/uploads/init'])
  assert.equal(phases.at(-1), 'instant')
})

test('lost part ACK is recovered by querying status without transmitting the same part again', async () => {
  values.clear()
  const target = file(), paths = []
  let sent = 0
  globalThis.fetch = async url => {
    paths.push(url)
    if (url === '/documents/uploads/init') return envelope(init(target))
    if (url === '/documents/upload-chunk') { sent++; throw new TypeError('ACK lost') }
    if (url.startsWith('/documents/upload-status')) return envelope([0])
    if (url.startsWith('/documents/complete-upload')) return envelope({ id: 'upload-id', status: 'QUEUED' })
    throw new Error(`Unexpected ${url}`)
  }
  await upload(target)
  assert.equal(sent, 1)
  assert.ok(paths.some(path => path.startsWith('/documents/upload-status')))
})

test('failed MinIO write leaves part incomplete and frontend retries after status check', async () => {
  values.clear()
  const target = file()
  let sent = 0, statusChecks = 0
  globalThis.fetch = async url => {
    if (url === '/documents/uploads/init') return envelope(init(target))
    if (url === '/documents/upload-chunk') return ++sent === 1 ? envelope(null, 503, 'storage temporarily unavailable') : envelope(null)
    if (url.startsWith('/documents/upload-status')) { statusChecks++; return envelope([]) }
    if (url.startsWith('/documents/complete-upload')) return envelope({ id: 'upload-id', status: 'QUEUED' })
    throw new Error(`Unexpected ${url}`)
  }
  await upload(target)
  assert.equal(sent, 2); assert.equal(statusChecks, 1)
})

test('content conflict is not blindly retried', async () => {
  values.clear()
  const target = file()
  let sent = 0
  globalThis.fetch = async url => {
    if (url === '/documents/uploads/init') return envelope(init(target))
    if (url === '/documents/upload-chunk') { sent++; return envelope(null, 409, 'part hash conflict') }
    throw new Error(`Unexpected ${url}`)
  }
  await assert.rejects(upload(target), /part hash conflict/)
  assert.equal(sent, 1); assert.equal(values.size, 1)
})

test('retry exhaustion preserves receipt and does not submit incomplete upload', async () => {
  values.clear()
  const target = file()
  let sent = 0
  globalThis.fetch = async url => {
    if (url === '/documents/uploads/init') return envelope(init(target))
    if (url === '/documents/upload-chunk') { sent++; return envelope(null, 503, 'storage down') }
    if (url.startsWith('/documents/upload-status')) return envelope([])
    throw new Error(`Unexpected ${url}`)
  }
  await assert.rejects(upload(target), /storage down/)
  assert.equal(sent, 4); assert.equal(values.size, 1)
})

test('completion ACK loss retries idempotent completion and never resends uploaded parts', async () => {
  values.clear()
  const target = file()
  let sent = 0, completeAttempts = 0
  globalThis.fetch = async url => {
    if (url === '/documents/uploads/init') return envelope(init(target))
    if (url === '/documents/upload-chunk') { sent++; return envelope(null) }
    if (url.startsWith('/documents/complete-upload')) {
      if (++completeAttempts === 1) throw new TypeError('completion ACK lost')
      return envelope({ id: 'upload-id', status: 'QUEUED' }, 202)
    }
    throw new Error(`Unexpected ${url}`)
  }
  await upload(target)
  assert.equal(sent, 1); assert.equal(completeAttempts, 2); assert.equal(values.size, 0)
})

test('init retries are content-based and isolated by knowledge base and version scope', async () => {
  values.clear()
  const target = file(), initializations = [], sent = []
  globalThis.fetch = async (url, options) => {
    if (url === '/documents/uploads/init') {
      const body = JSON.parse(options.body)
      initializations.push(body)
      if (initializations.length === 1) throw new TypeError('initialization ACK lost')
      return envelope(init(target, { uploadId: `upload-${body.knowledgeBaseId}`, mode: 'RESUME' }))
    }
    if (url === '/documents/upload-chunk') { sent.push(options.body.get('uploadId')); return envelope(null) }
    if (url.startsWith('/documents/complete-upload')) return envelope({ id: 'upload-b', status: 'QUEUED' })
    throw new Error(`Unexpected ${url}`)
  }
  await upload(target, () => {}, undefined, { knowledgeBaseId: 'b', previousDocumentId: 'previous' })
  assert.equal(initializations.length, 2)
  assert.deepEqual(initializations[0], initializations[1])
  assert.equal(initializations[0].previousDocumentId, 'previous')
  assert.deepEqual(sent, ['upload-b'])
})

test('aborting during hashing makes no HTTP request', async () => {
  const controller = new AbortController()
  globalThis.fetch = async () => { throw new Error('Must not upload') }
  await assert.rejects(upload(file(), state => {
    if (state.phase === 'hashing' && state.percent === 100) controller.abort()
  }, controller.signal), { name: 'AbortError' })
})
