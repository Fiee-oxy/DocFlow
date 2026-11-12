import { createSHA256 } from 'hash-wasm'

export const HASH_READ_BYTES = 1024 * 1024

export function checkHashAbort(signal) {
  if (signal?.aborted) throw new DOMException('文件校验已暂停', 'AbortError')
}

/** Incremental hashing: only one bounded read buffer is held, never the complete file. */
export async function hashDocumentBlob(file, { partSize = 5 * 1024 * 1024, readSize = HASH_READ_BYTES, signal, onProgress = () => {} } = {}) {
  if (!Number.isSafeInteger(partSize) || partSize < 1 || !Number.isSafeInteger(readSize) || readSize < 1) throw new Error('无效的文件校验分片大小')
  checkHashAbort(signal)
  const whole = await createSHA256(), part = await createSHA256()
  checkHashAbort(signal)
  whole.init(); part.init()
  const partHashes = []
  let offset = 0, partBytes = 0
  onProgress({ bytesHashed: 0, totalBytes: file.size, percent: 0 })
  while (offset < file.size) {
    checkHashAbort(signal)
    const end = Math.min(offset + readSize, offset + partSize - partBytes, file.size)
    const bytes = new Uint8Array(await file.slice(offset, end).arrayBuffer())
    checkHashAbort(signal)
    whole.update(bytes); part.update(bytes)
    partBytes += bytes.length; offset = end
    if (partBytes === partSize || offset === file.size) {
      partHashes.push(part.digest('hex')); part.init(); partBytes = 0
    }
    onProgress({ bytesHashed: offset, totalBytes: file.size, percent: Math.round(offset / file.size * 100) })
  }
  checkHashAbort(signal)
  return { sha256: whole.digest('hex'), partHashes, partSize }
}
