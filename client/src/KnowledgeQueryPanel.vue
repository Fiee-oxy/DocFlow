<template>
  <section class="knowledge-query">
    <h2>知识库问答</h2>
    <p>{{ documentIds.length ? `检索已勾选的 ${documentIds.length} 份文档` : '检索当前知识库，每份文档使用最近可用版本' }}</p>
    <textarea v-model="question" maxlength="2000" rows="3" placeholder="输入问题，可先查看召回原文，再生成回答" :disabled="busy" />
    <div class="actions">
      <button :disabled="busy || !question.trim() || !knowledgeBaseId" @click="run(false)">查看召回原文</button>
      <button :disabled="busy || !question.trim() || !knowledgeBaseId" @click="run(true)">生成回答</button>
    </div>
    <p v-if="stage" role="status">{{ stage }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="streamDraft && !answer" class="answer">{{ streamDraft }}<br><small>生成中的草稿，引用尚未核验。</small></p>
    <article v-if="answer"><p class="answer">{{ answer.answer }}</p>
      <details v-if="answer.citations?.length"><summary>原文出处</summary>
        <blockquote v-for="(citation,i) in answer.citations" :key="i">{{ citation.section }} · {{ citation.pages }}<br>{{ citation.quote }}</blockquote>
      </details>
    </article>
    <template v-if="preview">
      <p>已选入 {{ preview.selectedSourceCount || 0 }} 条证据；{{ preview.ready ? '当前范围可用' : '部分文档尚未就绪' }}。仅检索，不调用聊天模型。</p>
      <article v-for="source in preview.evidence" :key="`${source.doc_id}:${source.id}`">
        <strong>{{ source.filename }} · {{ source.section }} · 物理页 {{ source.pages?.join('、') }}</strong>
        <p class="answer">{{ source.text }}</p>
      </article>
    </template>
  </section>
</template>
<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { apiRequest, captureAuthSession } from './api.js'
import { streamDocumentTask } from './documentTaskEvents.js'
const props = defineProps({ knowledgeBaseId: String, documentIds: { type: Array, default: () => [] }, userId: [String, Number] })
const question = ref(''), busy = ref(false), error = ref(''), stage = ref(''), preview = ref(null), answer = ref(null), streamDraft = ref('')
let generation = 0, controller, pending
watch(() => [props.knowledgeBaseId, props.documentIds.join(','), props.userId], () => {
  generation++; controller?.abort(); busy.value = false; stage.value = ''; error.value = ''; preview.value = null; answer.value = null; streamDraft.value = ''; pending = null
})
onUnmounted(() => { generation++; controller?.abort() })
async function request(path, body) {
  const response = await apiRequest(path, { signal: controller.signal, ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
  if (!response.ok) throw new Error(await response.text() || '请求失败')
  return response.json()
}
async function run(generate) {
  if (busy.value) return
  const current = generation, session = captureAuthSession()
  controller = new AbortController(); busy.value = true; error.value = ''; stage.value = generate ? '正在提交问答任务…' : '正在检索原文…'
  const body = { knowledgeBaseIds: [props.knowledgeBaseId], documentIds: [...props.documentIds], question: question.value.trim(), limit: 100 }
  try {
    if (!generate) {
      const result = await request('/knowledge/search', body)
      if (current === generation && session()) preview.value = result
    } else {
      // Reuse an accepted task after network loss; switching scope or question creates a new request.
      const fingerprint = JSON.stringify(body)
      if (!pending || pending.fingerprint !== fingerprint) pending = { fingerprint, id: crypto.randomUUID(), after: '0', draft: '', accepted: false }
      answer.value = null; streamDraft.value = pending.draft
      if (!pending.accepted) {
        const task = await request('/knowledge/query', { ...body, requestId: pending.id })
        if (current !== generation || !session()) return
        pending.id = task.id || pending.id; pending.accepted = true
      }
      const progress = pending
      const labels = { PREPARING: '正在准备文档范围…', REWRITING_QUERY: '正在优化检索问题…', RETRIEVING: '正在检索相关原文…', GENERATING: '正在生成回答…', RETRYING_MODEL: '模型连接暂时异常，正在重试…', VERIFYING: '正在核对引用…', CACHE_HIT: '正在读取已有回答…', WAITING_RATE_LIMIT: '正在等待模型服务配额…' }
      await streamDocumentTask(progress.id, {
        signal: controller.signal, after: () => progress.after, onCursor: id => { progress.after = id },
        active: () => current === generation && session(),
        onEvent: (type, data) => {
          if (type === 'stage') stage.value = labels[data.stage] || '任务处理中…'
          if (type === 'progress' && data.stage === 'RETRIEVING') stage.value = `已检索 ${data.completed}/${data.total} 份文档…`
          if (type === 'delta') { progress.draft += data.text || ''; streamDraft.value = progress.draft }
          if (type === 'complete') { answer.value = data; pending = null; return true }
          if (type === 'task_status' && data.state === 'COMPLETED') { answer.value = typeof data.result_json === 'string' ? JSON.parse(data.result_json) : data.result_json; pending = null; return true }
          if (type === 'error' || type === 'task_status' && ['FAILED', 'CANCELLED'].includes(data.state)) {
            pending = null; const error = new Error(data.message || data.error_message || '问答未完成'); error.terminal = true; throw error
          }
          return false
        }
      })
    }
    if (current === generation && session()) stage.value = ''
  } catch (e) { if (current === generation && session() && e.name !== 'AbortError') { error.value = e.message; stage.value = '' } }
  finally { if (current === generation && session()) busy.value = false }
}
</script>
<style scoped>
.knowledge-query h2{font-size:18px}.knowledge-query>p{font-size:13px;color:#a6b5c4}.knowledge-query textarea{box-sizing:border-box;width:100%;background:#17252d;color:#e7e9ee;border:1px solid #385447;padding:12px;border-radius:8px;font:inherit}.actions{display:flex;gap:12px;margin:12px 0}.actions button{background:#a6e9cc;color:#10271d;border:0;border-radius:7px;padding:10px 14px;cursor:pointer}.actions button:disabled{opacity:.5}.knowledge-query article{border-top:1px solid #304139;padding:18px 0;font-size:13px}.answer{white-space:pre-wrap;line-height:1.8}.knowledge-query blockquote{white-space:pre-wrap;border-left:2px solid #85c9ad;padding-left:12px}
</style>
