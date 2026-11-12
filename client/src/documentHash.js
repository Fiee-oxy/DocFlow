const cachedHashes = new WeakMap()

/** The browser always hashes off the UI thread. Abort terminates a pending read/hash. */
export function hashDocument(file, { partSize = 5 * 1024 * 1024, signal, onProgress = () => {}, workerFactory = () => new Worker(new URL('./documentHash.worker.js', import.meta.url), { type: 'module' }) } = {}) {
  if (signal?.aborted) return Promise.reject(new DOMException('文件校验已暂停', 'AbortError'))
  const cached = cachedHashes.get(file)
  if (cached?.partSize === partSize) {
    onProgress({ bytesHashed: file.size, totalBytes: file.size, percent: 100 })
    return Promise.resolve(cached)
  }
  return new Promise((resolve, reject) => {
    const worker = workerFactory()
    let finished = false
    const finish = (error, value) => {
      if (finished) return
      finished = true
      signal?.removeEventListener('abort', cancel)
      worker.terminate()
      if (error) reject(error)
      else resolve(value)
    }
    const cancel = () => finish(new DOMException('文件校验已暂停', 'AbortError'))
    worker.onmessage = ({ data }) => {
      if (finished) return
      if (data.type === 'progress') onProgress(data.progress)
      else if (data.type === 'error') finish(new Error(data.message || '文件校验失败'))
      else if (data.type === 'result') {
        const result = Object.freeze({ ...data.result, partHashes: Object.freeze(data.result.partHashes) })
        cachedHashes.set(file, result)
        finish(null, result)
      }
    }
    worker.onerror = event => finish(new Error(event.message || '文件校验线程启动失败'))
    signal?.addEventListener('abort', cancel, { once: true })
    if (signal?.aborted) { cancel(); return }
    try { worker.postMessage({ file, partSize }) }
    catch (error) { finish(error) }
  })
}
