import { computed, nextTick, ref, watch } from 'vue'
import { apiRequest, captureAuthSession } from './api.js'
import { streamDocumentTask } from './documentTaskEvents.js'

export function useDocumentChat(props, emit, { streaming = false } = {}) {
  const turns = ref([]), draft = ref(''), error = ref(''), sending = ref(false), historyLoading = ref(false), messagesEl = ref(null)
  const pending = ref(null)
  const streamDraft = ref(''), stage = ref('')
  const ready = computed(() => props.user && props.document?.totalBlocks > 0 && !props.uploading)
  let version = 0, controller
  async function request(path, options) {
    const response = await apiRequest(path, options)
    if (!response.ok) throw new Error(await response.text() || '问答请求失败')
    return response.json()
  }
  async function scroll() { await nextTick(); if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight }
  function attach(event) { const file = event.target.files?.[0]; event.target.value = ''; if (!props.user) emit('login'); else if (file) emit('file', file) }
  watch([() => props.document?.id, () => props.user?.id], async () => {
    const current = ++version, session = captureAuthSession(); controller?.abort()
    turns.value = []; error.value = ''; sending.value = false; historyLoading.value = false; pending.value = null; streamDraft.value = ''; stage.value = ''
    if (!props.uploading || !props.user) draft.value = ''
    if (!props.document || !props.user) return
    const id = props.document.id
    historyLoading.value = true
    try {
      const history = await request(`/documents/${id}/chat`)
      if (version === current && session()) { turns.value = history; scroll() }
    } catch (e) { if (version === current && session()) error.value = e.message }
    finally { if (version === current && session()) historyLoading.value = false }
  }, { immediate: true })
  async function send() {
    if (!props.user) { emit('login'); return }
    if (!ready.value || sending.value || historyLoading.value || !draft.value.trim()) return
    const current = version, session = captureAuthSession(), id = props.document.id, question = draft.value.trim()
    // Reuse the request ID after a lost response so the server can return the saved turn.
    if (!pending.value || pending.value.question !== question) pending.value = { question, requestId: crypto.randomUUID(), after: '0', draft: '', accepted: false }
    sending.value = true; error.value = ''; controller = new AbortController(); scroll()
    try {
      let response
      if (streaming) {
        const progress = pending.value
        streamDraft.value = progress.draft; stage.value = '正在提交问答任务…'
        if (!progress.accepted) {
          await request('/documents/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ documentIds: [id], question, requestId: progress.requestId }), signal: controller.signal })
          if (version !== current || !session()) return
          progress.accepted = true
        }
        const labels = { PREPARING: '正在准备文档…', REWRITING_QUERY: '正在优化检索问题…', RETRIEVING: '正在检索相关原文…', GENERATING: '正在生成回答…', VERIFYING: '正在核对引用…', RETRYING_MODEL: '连接暂时异常，正在重试…', WAITING_RATE_LIMIT: '正在等待模型配额…', CACHE_HIT: '正在读取已有回答…' }
        await streamDocumentTask(progress.requestId, { signal: controller.signal, active: () => version === current && session(), after: () => progress.after,
          onCursor: cursor => { progress.after = cursor }, onEvent: (type, data) => {
            if (type === 'stage') stage.value = labels[data.stage] || '任务处理中…'
            if (type === 'delta') { progress.draft += data.text || ''; streamDraft.value = progress.draft; scroll() }
            if (type === 'complete') { response = data; return true }
            if (type === 'task_status' && data.state === 'COMPLETED') { response = typeof data.result_json === 'string' ? JSON.parse(data.result_json) : data.result_json; return true }
            if (type === 'error' || type === 'task_status' && ['FAILED', 'CANCELLED'].includes(data.state)) {
              pending.value = null; const failure = new Error(data.message || data.error_message || '问答未完成'); failure.terminal = true; throw failure
            }
            return false
          } })
        if (!response && version === current && session()) throw new Error('回答尚未完成，可继续接收')
      } else response = await request(`/documents/${id}/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(pending.value), signal: controller.signal })
      if (version !== current || !session()) return
      if (!turns.value.some(t => t.requestId === pending.value.requestId)) turns.value.push({ question, requestId: pending.value.requestId, response })
      draft.value = ''; pending.value = null; scroll()
    } catch (e) { if (version === current && session() && e.name !== 'AbortError') error.value = `${e.message}；问题已保留，可以重新发送。` }
    finally { if (version === current && session()) { sending.value = false; stage.value = ''; streamDraft.value = ''; scroll() } }
  }
  function dispose() { version++; controller?.abort() }
  return { turns, draft, error, sending, historyLoading, messagesEl, pending, ready, attach, send, dispose, streamDraft, stage }
}
