import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { hashDocumentBlob } from './documentHashCore.js'
import { hashDocument } from './documentHash.js'

const sha256 = value => createHash('sha256').update(value).digest('hex')

test('incremental SHA-256 matches standard vectors and hashes original bytes, not joined part hashes', async () => {
  const blob = new Blob(['abc'])
  const result = await hashDocumentBlob(blob, { partSize: 2, readSize: 1 })
  assert.equal(result.sha256, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  assert.deepEqual(result.partHashes, [sha256('ab'), sha256('c')])
  assert.notEqual(result.sha256, sha256(result.partHashes.join('')))
  assert.equal((await hashDocumentBlob(new Blob())).sha256, sha256(''))
})

test('binary data is read in bounded buffers and part boundaries stay exact', async () => {
  const bytes = Uint8Array.from({ length: 251 }, (_, i) => i)
  const blob = new Blob([bytes]), reads = [], progress = []
  const file = { size: blob.size, slice(start, end) { reads.push(end - start); return blob.slice(start, end) } }
  const result = await hashDocumentBlob(file, { partSize: 77, readSize: 31, onProgress: value => progress.push(value.percent) })
  assert.equal(result.sha256, sha256(bytes))
  assert.deepEqual(result.partHashes, [0, 77, 154, 231].map(start => sha256(bytes.slice(start, start + 77))))
  assert.ok(reads.every(size => size <= 31))
  assert.equal(progress.at(-1), 100)
})

test('hashing abort stops reading further data and never returns a partial digest', async () => {
  const controller = new AbortController(), blob = new Blob(['abcdef'])
  let reads = 0
  const file = { size: blob.size, slice(start, end) { reads++; return blob.slice(start, end) } }
  await assert.rejects(hashDocumentBlob(file, {
    readSize: 1, signal: controller.signal,
    onProgress: value => { if (value.bytesHashed === 2) controller.abort() }
  }), { name: 'AbortError' })
  assert.equal(reads, 2)
})

test('browser worker cancellation terminates work immediately', async () => {
  let terminated = 0, posted = 0
  const controller = new AbortController()
  const worker = { terminate() { terminated++ }, postMessage() { posted++ } }
  const result = hashDocument(new Blob(['abc']), { signal: controller.signal, workerFactory: () => worker })
  controller.abort()
  await assert.rejects(result, { name: 'AbortError' })
  assert.equal(posted, 1)
  assert.equal(terminated, 1)
})

test('worker transfer failure terminates the worker and rejects cleanly', async () => {
  let terminated = 0
  await assert.rejects(hashDocument(new Blob(['abc']), { workerFactory: () => ({
    terminate() { terminated++ }, postMessage() { throw new Error('file unavailable') }
  }) }), /file unavailable/)
  assert.equal(terminated, 1)
})

test('successful worker results are cached only for the same File and partition size', async () => {
  let created = 0, terminated = 0
  const blob = new Blob(['abc'])
  const workerFactory = () => {
    created++
    return { terminate() { terminated++ }, postMessage() {
      queueMicrotask(() => this.onmessage({ data: { type: 'result', result: { sha256: sha256('abc'), partHashes: [sha256('abc')], partSize: 5 } } }))
    } }
  }
  const first = await hashDocument(blob, { partSize: 5, workerFactory })
  assert.equal(await hashDocument(blob, { partSize: 5, workerFactory }), first)
  assert.equal(created, 1)
  assert.equal(terminated, 1)
})
