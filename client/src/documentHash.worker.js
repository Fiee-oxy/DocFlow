import { hashDocumentBlob } from './documentHashCore.js'

self.onmessage = async ({ data }) => {
  try {
    const result = await hashDocumentBlob(data.file, {
      partSize: data.partSize,
      onProgress: progress => self.postMessage({ type: 'progress', progress })
    })
    self.postMessage({ type: 'result', result })
  } catch (error) {
    self.postMessage({ type: 'error', message: error.message })
  }
}
