import { apiRequest } from './api.js'

/** Reconnect only the event subscription; this function never submits a model task. */
export async function streamDocumentTask(id, { signal, after = () => '0', onCursor = () => {}, onEvent, active = () => true }) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (signal.aborted || !active()) return
    try {
      const response = await apiRequest(`/documents/query-tasks/${id}/events?after=${encodeURIComponent(after())}`, { signal, headers: { Accept: 'text/event-stream' } })
      if (!response.ok) {
        const error = new Error(await response.text() || '事件流连接失败')
        error.terminal = response.status >= 400 && response.status < 500 && ![409, 429].includes(response.status)
        throw error
      }
      if (!response.body) throw new Error('事件流不可用')
      const reader = response.body.getReader(), decoder = new TextDecoder()
      let buffer = ''
      const cancel = () => { reader.cancel().catch(() => {}) }
      signal.addEventListener('abort', cancel, { once: true })
      try {
        while (!signal.aborted && active()) {
          const { value, done } = await reader.read()
          if (signal.aborted || !active()) return
          buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
          const frames = buffer.split(/\r?\n\r?\n/); buffer = frames.pop() || ''
          for (const frame of frames) {
            const lines = frame.split(/\r?\n/)
            const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
            if (!data) continue
            const eventId = lines.find(line => line.startsWith('id:'))?.slice(3).trim()
            if (eventId && /^\d+$/.test(eventId) && BigInt(eventId) <= BigInt(after())) continue
            const type = lines.find(line => line.startsWith('event:'))?.slice(6).trim() || 'message'
            const payload = JSON.parse(data)
            const terminal = await onEvent(type, payload)
            if (eventId && /^\d+$/.test(eventId)) onCursor(eventId)
            if (terminal) return
          }
          if (done) throw new Error('连接中断，已保留任务和接收进度')
        }
        return
      } finally {
        signal.removeEventListener('abort', cancel)
        await reader.cancel().catch(() => {}); reader.releaseLock()
      }
    } catch (error) {
      if (signal.aborted || !active()) return
      if (error.terminal || attempt === 2) throw error
    }
  }
}
