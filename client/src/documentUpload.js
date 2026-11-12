import { apiRequest } from './api.js'
import { hashDocument } from './documentHash.js'

export const DOCUMENT_CHUNK_BYTES = 5 * 1024 * 1024
export const DOCUMENT_MAX_BYTES = Number(import.meta.env?.VITE_DOCUMENT_MAX_FILE_BYTES || 209715200)

export function documentUploadLabel(progress = {}) {
  if (progress.phase === 'hashing') return `文件校验 ${progress.percent || 0}%`
  if (progress.phase === 'initializing') return '检查上传记录…'
  if (progress.phase === 'finalizing') return '提交后台处理…'
  if (progress.phase === 'instant') return '已复用相同文件'
  return `上传 ${progress.percent || 0}%`
}

export function validateDocument(file) {
  if (!file || !/\.(docx|pdf)$/i.test(file.name || '')) return '请选择 DOCX 或 PDF'
  if (!file.size || file.size > DOCUMENT_MAX_BYTES) return `文档大小必须在 1 字节至 ${Math.round(DOCUMENT_MAX_BYTES / 1024 / 1024)} MiB 之间`
  return ''
}

async function request(path, options) {
  const response = await apiRequest(path, options)
  if (!response.ok) {
    const error = new Error(await response.text() || '文档请求失败')
    error.status = response.status
    throw error
  }
  return response.json()
}

function key(sha256, size, userId, scope) {
  return `document-upload:${userId}:${scope.knowledgeBaseId || 'default'}:${scope.previousDocumentId || 'new'}:${size}:${sha256}`
}
function write(key, id) { try { localStorage.setItem(key, id) } catch { /* Server-side hash matching works without local storage. */ } }
function remove(key) { try { localStorage.removeItem(key) } catch { /* Optional receipt. */ } }
function aborted(signal) { if (signal?.aborted) throw new DOMException('上传已暂停', 'AbortError') }
function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    aborted(signal)
    const cancel = () => { clearTimeout(timer); reject(new DOMException('上传已暂停', 'AbortError')) }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve() }, ms)
    signal?.addEventListener('abort', cancel, { once: true })
  })
}
function retryable(error) { return !error.status || [408, 429].includes(error.status) || error.status >= 500 }
async function retry(action, signal, { recover, wait = delay } = {}) {
  for (let attempt = 0; ; attempt++) {
    aborted(signal)
    try { return await action() } catch (error) {
      if (signal?.aborted || error.name === 'AbortError' || !retryable(error)) throw error
      // Recover even after the final failed attempt: its ACK could have been lost.
      if (recover && await recover()) return
      if (attempt >= 3) throw error
      await wait(500 * 2 ** attempt + Math.random() * 250, signal)
    }
  }
}

export async function uploadDocument(file, userId, onProgress = () => {}, signal, scope = {}, dependencies = {}) {
  const invalid = validateDocument(file)
  if (invalid) throw new Error(invalid)
  if (!userId) throw new Error('请先登录')
  const hash = dependencies.hashDocument || hashDocument, wait = dependencies.wait || delay
  const emitPhase = phase => onProgress({ phase, completed: 0, total: 0, percent: 0 })
  emitPhase('hashing')
  let fingerprint = await hash(file, { partSize: DOCUMENT_CHUNK_BYTES, signal, onProgress: progress => onProgress({ phase: 'hashing', completed: 0, total: 0, ...progress }) })
  aborted(signal)
  const storageKey = key(fingerprint.sha256, file.size, userId, scope)
  emitPhase('initializing')
  // Always initialize by content: the server can find an interrupted task even after local storage is cleared.
  const initialized = await retry(() => request('/documents/uploads/init', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ filename: file.name, sizeBytes: file.size, sha256: fingerprint.sha256,
      knowledgeBaseId: scope.knowledgeBaseId || null, previousDocumentId: scope.previousDocumentId || null })
  }), signal, { wait })
  aborted(signal)
  if (initialized.mode === 'INSTANT') {
    if (!initialized.document?.id) throw new Error('秒传响应缺少文档信息')
    remove(storageKey)
    onProgress({ phase: 'instant', completed: 0, total: 0, percent: 100 })
    return initialized.document
  }
  const id = initialized.uploadId, chunkSize = initialized.chunkSizeBytes, total = initialized.totalChunks
  if (!id || !['NEW', 'RESUME'].includes(initialized.mode) || !Number.isSafeInteger(chunkSize) || chunkSize < 1 || !Number.isSafeInteger(total) || total !== Math.ceil(file.size / chunkSize)) throw new Error('上传初始化响应无效')
  write(storageKey, id)
  if (fingerprint.partSize !== chunkSize) {
    fingerprint = await hash(file, { partSize: chunkSize, signal, onProgress: progress => onProgress({ phase: 'hashing', completed: 0, total: 0, ...progress }) })
  }
  const done = new Set((initialized.completedChunks || []).filter(n => Number.isInteger(n) && n >= 0 && n < total))
  const emit = phase => onProgress({ phase, completed: done.size, total, percent: Math.round(done.size / total * 100) })
  emit('uploading')
  // Sequential until the server's per-document merge/upload lock supports safe parallel parts.
  for (let index = 0; index < total; index++) {
    if (done.has(index)) continue
    await retry(() => {
      const data = new FormData()
      data.append('uploadId', id)
      data.append('chunkIndex', String(index))
      data.append('sha256', fingerprint.partHashes[index])
      data.append('file', file.slice(index * chunkSize, Math.min((index + 1) * chunkSize, file.size)))
      return request('/documents/upload-chunk', { method: 'POST', body: data, signal })
    }, signal, { wait, recover: async () => {
      try {
        const completed = await request(`/documents/upload-status?${new URLSearchParams({ uploadId: id })}`, { signal })
        return Array.isArray(completed) && completed.includes(index)
      } catch (error) {
        if (signal?.aborted || error.name === 'AbortError' || (error.status && !retryable(error))) throw error
        return false
      }
    } })
    done.add(index); emit('uploading')
  }
  aborted(signal)
  emit('finalizing')
  const result = await retry(() => request(`/documents/complete-upload?${new URLSearchParams({ uploadId: id })}`, { method: 'POST', signal }), signal, { wait })
  remove(storageKey)
  return result
}
