<template>
  <main class="document-workspace">
    <section class="doc-intro">
      <span class="doc-eyebrow">DOCUMENT KNOWLEDGE</span>
      <h1>发来文档，带着问题一起读。</h1>
      <p>在对话中发送 Word 或文本型 PDF，针对原文提问，也可以查看保留来源的原文内容块。</p>
    </section>
    <section class="doc-upload" aria-label="文档上传">
      <div>
        <h2>上传文档</h2>
        <p>支持 DOCX / PDF · 最大 {{ formatBytes(DOCUMENT_MAX_BYTES) }} · 支持中英文扫描 PDF OCR</p>
        <p v-if="!user" class="doc-muted">登录后即可上传与查看自己的文档。</p>
      </div>
      <label class="doc-primary" :class="{ disabled: uploading }">
        {{ uploading ? documentUploadLabel(progress) : '选择文档' }}
        <input type="file" accept=".docx,.pdf" :disabled="uploading" @change="choose" />
      </label>
      <button v-if="uploading" class="doc-secondary" :disabled="progress.phase === 'finalizing'" @click="cancelUpload">{{ progress.phase === 'hashing' ? '暂停校验' : '暂停上传' }}</button>
      <button v-else-if="uploadPaused" class="doc-secondary" @click="resumeUpload">继续上传</button>
      <p v-if="uploading" class="doc-upload-note" role="status">{{ filename }} · {{ documentUploadLabel(progress) }}<span v-if="progress.phase === 'uploading'"> · 分片 {{ progress.completed || 0 }}/{{ progress.total || 0 }}</span><span v-if="progress.phase === 'hashing'">；正在本地校验文件内容，完成后自动查找可续传任务。</span><span v-else-if="progress.phase !== 'finalizing'">；暂停后点击“继续上传”可续传。</span></p>
      <p v-else-if="uploadPaused" class="doc-upload-note" role="status">{{ filename }} · 上传已暂停，已完成分片会保留。</p>
    </section>
    <p v-if="message" class="doc-notice" role="status">{{ message }}</p>
    <p v-if="error" class="doc-error" role="alert">{{ error }}</p>
    <div class="doc-layout">
      <section class="doc-library" aria-label="文档列表">
        <label class="doc-muted">知识库 <select v-model="baseId" :disabled="uploading || uploadPaused" @change="changeBase"><option v-for="base in bases" :key="base.id" :value="base.id">{{ base.name }}</option></select></label>
        <form class="doc-base-create" @submit.prevent="createBase"><input v-model="baseName" maxlength="100" placeholder="新知识库名称" /><button class="doc-link" :disabled="!baseName.trim() || uploading || uploadPaused">创建</button></form>
        <div class="doc-section-head"><h2>我的文档</h2><button class="doc-link" :disabled="loading || !user" @click="refresh">刷新</button></div>
        <p v-if="loading && !documents.length" class="doc-muted">正在加载文档…</p>
        <p v-else-if="!documents.length" class="doc-muted">还没有文档，上传后会自动开始解析和分块。</p>
        <div v-for="doc in documents" :key="doc.id" class="doc-card" :class="{ selected: selected?.id === doc.id }" role="button" tabindex="0" @click="select(doc)" @keydown.enter.self="select(doc)">
          <label @click.stop><input type="checkbox" :value="doc.id" v-model="queryIds" :disabled="!doc.totalBlocks" />加入检索范围 · v{{ doc.versionNumber || 1 }}</label>
          <span class="doc-format">{{ doc.filename.toLowerCase().endsWith('.pdf') ? 'PDF' : 'WORD' }}</span>
          <strong>{{ doc.filename }}</strong>
          <span :class="['doc-state', { failed: doc.status === 'FAILED' }]">{{ stateLabel(doc) }}</span>
          <span class="doc-muted">{{ formatBytes(doc.sizeBytes) }} · {{ formatDate(doc.createdAt) }}</span>
          <progress v-if="active(doc) && doc.totalBlocks" :value="doc.parsedPages" :max="doc.totalPages || 1" />
        </div>
      </section>
      <section class="doc-report" aria-label="文档聊天与解析内容">
        <div class="doc-tabs"><button :class="{ chosen: panel === 'chat' }" @click="panel = 'chat'">文档聊天</button><button :class="{ chosen: panel === 'knowledge' }" @click="panel = 'knowledge'">知识库问答</button><button :class="{ chosen: panel === 'report' }" @click="panel = 'report'">解析内容</button></div>
        <KnowledgeQueryPanel v-if="panel === 'knowledge'" :knowledge-base-id="baseId" :document-ids="queryIds" :user-id="user?.id" />
        <label v-if="selected && panel !== 'knowledge'" class="doc-muted">上传 {{ selected.filename }} 的新版本 <input type="file" accept=".pdf,.docx" :disabled="uploading || uploadPaused" @change="chooseRevision" /></label>
        <div v-if="panel === 'chat' && selected && active(selected)" class="doc-notice" role="status">{{ stageLabel(selected.stage) }}<span v-if="selected.totalPages"> · 已解析 {{ selected.parsedPages }}/{{ selected.totalPages }} 页</span><span v-if="selected.totalBlocks"> · 已生成 {{ selected.totalBlocks }} 块；{{ selected.parseComplete ? '原文已全部就绪' : '当前问答仅覆盖已解析部分' }}</span><span v-else> · 首批内容块生成后即可发送问题</span></div>
        <div v-if="panel === 'chat' && selected?.status === 'FAILED'" class="doc-error"><p>{{ selected.errorMessage || '文档处理失败' }}</p><button class="doc-secondary" :disabled="retrying" @click="retrySelected">从失败处重试</button><p v-if="selected.totalBlocks">已解析的原文仍可用于提问。</p></div>
        <DocumentChat v-show="panel === 'chat'" :document="selected" :user="user" :uploading="uploading" :upload-paused="uploadPaused" :upload-filename="filename" :progress="progress" @pause="cancelUpload" @resume="resumeUpload" @login="emit('login')" @file="choose({ target: { files: [$event], value: '' } })" />
        <div v-show="panel === 'report'">
        <template v-if="selected">
          <div class="doc-section-head"><h2>{{ selected.filename }}</h2><span class="doc-state">{{ stateLabel(selected) }}</span></div>
          <div v-if="selected.status === 'FAILED'" class="doc-error">
            <p>{{ selected.errorMessage || '处理失败，已解析的内容块会保留。' }}</p>
            <button class="doc-secondary" :disabled="retrying" @click="retrySelected">{{ retrying ? '正在提交…' : '从失败处重试' }}</button>
          </div>
          <div v-else-if="active(selected)" class="doc-processing">
            <h3>{{ stageLabel(selected.stage) }}</h3>
            <p>任务在后台运行，可以关闭页面，回来后继续查看。</p>
            <p v-if="selected.totalPages">已解析 {{ selected.parsedPages }} / {{ selected.totalPages }} 页</p>
            <p v-if="selected.totalBlocks">已生成 {{ selected.totalBlocks }} 个内容块</p>
            <progress v-if="selected.totalBlocks" :value="selected.parsedPages" :max="selected.totalPages || 1" />
            <p v-if="selected.status === 'RETRYING'">服务暂时不可用，正在等待自动重试。</p>
          </div>
          <p v-else-if="selected.status === 'UPLOADING'" class="doc-muted">上传尚未完成。重新选择同一文件，可从已上传分片继续。</p>
          <p v-else-if="reportLoading" class="doc-muted">正在读取内容块…</p>
          <template v-if="report">
            <div class="doc-report-meta"><strong>{{ report.totalBlocks }} 个原文内容块</strong></div>
            <p class="doc-muted">解析、分块和检索准备不调用聊天模型；发送问题时才调用模型。保留章节、物理页码和原文位置。</p>
            <details v-if="report.warnings?.length" class="doc-warnings"><summary>解析提醒</summary><ul><li v-for="(warning, index) in report.warnings" :key="index">{{ warning }}</li></ul></details>
            <div v-if="pageCount > 1" class="doc-pagination"><button :disabled="page === 1 || reportLoading" @click="page--">上一页</button><span>第 {{ page }} / {{ pageCount }} 页</span><button :disabled="page === pageCount || reportLoading" @click="page++">下一页</button></div>
            <article v-for="chunk in report.chunks" :key="chunk.index" class="doc-knowledge-point">
              <h4>内容块 {{ chunk.index + 1 }} · {{ chunk.section }}</h4>
              <p class="doc-explanation">{{ chunk.text }}</p>
              <details class="doc-source"><summary>来源位置</summary><div v-for="source in chunk.sources" :key="source.id">{{ sourceLabel(source) }} · {{ source.ref }}</div></details>
            </article>
          </template>
        </template>
        <div v-else class="doc-empty"><h2>查看原文与来源。</h2><p>选择左侧文档，查看处理进度或检查解析内容。</p></div>
        </div>
      </section>
    </div>
  </main>
</template>

<script setup>
import { computed, ref, shallowRef, watch, onUnmounted } from 'vue'
import { apiRequest, captureAuthSession } from './api.js'
import { uploadDocument, validateDocument, DOCUMENT_MAX_BYTES, documentUploadLabel } from './documentUpload.js'
import { formatBytes } from './chunkUpload.js'
import DocumentChat from './DocumentChat.vue'
import KnowledgeQueryPanel from './KnowledgeQueryPanel.vue'

const props = defineProps({ user: { type: Object, default: null } })
const emit = defineEmits(['login'])
const documents = ref([]), selected = ref(null), report = ref(null)
const bases = ref([]), baseId = ref(''), baseName = ref(''), queryIds = ref([])
let uploadScope = {}
const panel = ref('chat')
const loading = ref(false), uploading = ref(false), reportLoading = ref(false), retrying = ref(false)
const message = ref(''), error = ref(''), filename = ref(''), progress = ref({})
const uploadPaused = ref(false), pendingFile = shallowRef(null)
const PAGE_SIZE = 10
const page = ref(1)
const pageCount = computed(() => Math.max(1, Math.ceil((report.value?.totalBlocks || 0) / PAGE_SIZE)))
watch(page, () => { if (selected.value) loadReport(selected.value) })
let timer, uploadController, generation = 0, selectionVersion = 0, disposed = false
const active = doc => ['QUEUED', 'PROCESSING', 'RETRYING'].includes(doc.status)
const labels = { UPLOADING: '待完成上传', QUEUED: '排队中', PROCESSING: '处理中', RETRYING: '等待重试', FAILED: '处理失败', COMPLETED: '已完成' }
const stages = { MERGING: '正在合并并校验文档', PARSING: '正在解析文档内容', PIPELINING: '正在解析并保存原文内容块', EXTRACTING: '正在准备检索内容', SUMMARIZING: '正在准备检索内容', READY: '原文已就绪，可提问', UPLOADING: '等待后台处理' }
const stateLabel = doc => labels[doc.status] || doc.status
const stageLabel = stage => stages[stage] || '正在处理文档'
const formatDate = value => value ? value.replace('T', ' ').slice(0, 16) : ''
const sourceLabel = source => source.pages?.length ? `物理页 ${source.pages.join('、')}` : '章节 / 段落定位'
async function request(path, options) {
  const response = await apiRequest(path, options)
  if (!response.ok) throw new Error(await response.text() || '文档请求失败')
  return response.json()
}
function current(version, session) { return !disposed && generation === version && session() }
async function refresh() {
  if (!props.user || loading.value) return
  const version = generation, session = captureAuthSession()
  loading.value = true
  try {
    if (!baseId.value) {
      const availableBases = await request('/knowledge-bases')
      if (!current(version, session)) return
      bases.value = availableBases; baseId.value = availableBases[0]?.id || ''
    }
    if (!baseId.value) return
    const list = await request(`/knowledge-bases/${baseId.value}/documents`)
    if (!current(version, session)) return
    documents.value = list
    if (selected.value) {
      const updated = list.find(doc => doc.id === selected.value.id)
      if (updated) {
        selected.value = updated
        if (updated.totalBlocks > 0 && !reportLoading.value && (!report.value || report.value.totalBlocks !== updated.totalBlocks)) await loadReport(updated)
      }
    }
  } catch (e) { if (current(version, session)) error.value = e.message }
  finally {
    if (current(version, session)) {
      loading.value = false
      clearTimeout(timer)
      timer = setTimeout(refresh, documents.value.some(active) ? 3000 : 15000)
    }
  }
}
async function loadReport(doc) {
  const version = generation, selection = ++selectionVersion, session = captureAuthSession()
  reportLoading.value = true
  try {
    const result = await request(`/documents/${doc.id}/chunks?page=${page.value}&size=${PAGE_SIZE}`)
    if (current(version, session) && selection === selectionVersion && selected.value?.id === doc.id) report.value = result
  } catch (e) { if (current(version, session) && selection === selectionVersion) error.value = e.message }
  finally { if (current(version, session) && selection === selectionVersion) reportLoading.value = false }
}
function select(doc) {
  selectionVersion++
  selected.value = doc
  page.value = 1
  report.value = null
  reportLoading.value = false
  error.value = ''
  if (doc.totalBlocks > 0) loadReport(doc)
}
function changeBase() {
  generation++; selectionVersion++; clearTimeout(timer); loading.value = false
  documents.value = []; selected.value = null; report.value = null; queryIds.value = []; error.value = ''; refresh()
}
async function createBase() {
  if (!props.user) { emit('login'); return }
  if (uploading.value || uploadPaused.value) return
  const version = generation, session = captureAuthSession()
  try {
    const base = await request('/knowledge-bases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: baseName.value, sourceModule: 'personal' }) })
    if (!current(version, session)) return
    bases.value.push(base); baseId.value = base.id; baseName.value = ''; changeBase()
  } catch(e) { if (current(version, session)) error.value = e.message }
}
function chooseRevision(event) {
  const file = event.target.files?.[0]; event.target.value = ''
  if (file && selected.value) { uploadScope = { knowledgeBaseId: baseId.value, previousDocumentId: selected.value.id }; startUpload(file) }
}
async function choose(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!props.user) { emit('login'); return }
  if (!file) return
  uploadScope = { knowledgeBaseId: baseId.value }
  return startUpload(file)
}
async function startUpload(file) {
  if (uploading.value || !props.user) return
  const invalid = validateDocument(file)
  if (invalid) { error.value = invalid; return }
  const version = generation, session = captureAuthSession()
  pendingFile.value = file; uploadPaused.value = false
  error.value = ''; message.value = ''; uploading.value = true; filename.value = file.name; progress.value = {}
  uploadController = new AbortController()
  try {
    const doc = await uploadDocument(file, props.user.id, value => {
      if (current(version, session)) progress.value = value
    }, uploadController.signal, uploadScope)
    if (!current(version, session)) return
    pendingFile.value = null
    message.value = progress.value.phase === 'instant' ? '已复用相同文件，可查看处理进度与已有内容。' : '上传完成，已进入后台校验与解析队列。'
    panel.value = 'chat'
    select(doc)
    await refresh()
  } catch (e) {
    if (current(version, session)) {
      if (e.name === 'AbortError') {
        uploadPaused.value = true
        message.value = '已暂停。继续时会校验文件并跳过已完成分片；刷新页面后重新选择同一文件也可找回上传进度。'
      }
      else error.value = `${e.message}；重新选择同一文件可继续上传。`
    }
  } finally { if (current(version, session)) uploading.value = false }
}
function cancelUpload() { uploadController?.abort() }
function resumeUpload() { if (pendingFile.value && uploadPaused.value) return startUpload(pendingFile.value) }
async function retrySelected() {
  if (!selected.value) return
  const version = generation, session = captureAuthSession(), id = selected.value.id
  retrying.value = true; error.value = ''
  try {
    const doc = await request(`/documents/${id}/retry`, { method: 'POST' })
    if (!current(version, session)) return
    if (selected.value?.id === id) select(doc)
    await refresh()
  } catch (e) { if (current(version, session)) error.value = e.message }
  finally { if (current(version, session)) retrying.value = false }
}
watch(() => props.user?.id, () => {
  generation++; selectionVersion++; clearTimeout(timer); cancelUpload()
  documents.value = []; selected.value = null; report.value = null
  bases.value = []; baseId.value = ''; baseName.value = ''; queryIds.value = []; uploadScope = {}
  pendingFile.value = null; uploadPaused.value = false; filename.value = ''; progress.value = {}
  error.value = ''; message.value = ''; loading.value = false; uploading.value = false; reportLoading.value = false; retrying.value = false
  if (props.user) refresh()
}, { immediate: true })
onUnmounted(() => { disposed = true; generation++; clearTimeout(timer); cancelUpload(); pendingFile.value = null })
</script>

<style scoped>
.doc-tabs{display:flex;gap:10px;margin-bottom:20px;border-bottom:1px solid #263240;padding-bottom:14px}.doc-tabs button{background:transparent;border:1px solid #32473f;color:#b5d9cf;border-radius:7px;padding:9px 15px;font:inherit;font-size:13px;cursor:pointer}.doc-tabs button.chosen{background:#a6e9cc;color:#10271d}
.doc-toc{font-size:13px;margin:24px 0;color:#b5d9cf}.doc-toc summary{cursor:pointer}.doc-outline button,.doc-pagination button{font:inherit;font-size:12px;background:transparent;color:#b5d9cf;border:1px solid #32473f;padding:7px 10px;border-radius:6px;cursor:pointer}.doc-pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px;color:#9aa4b5;margin:24px 0}
.document-workspace{max-width:1360px;margin:0 auto;padding:52px 28px 90px;color:var(--text-main,#e7e9ee)}
.doc-intro{margin-bottom:32px}.doc-eyebrow{font-size:11px;letter-spacing:3px;color:#80d8bb}.doc-intro h1{font-size:clamp(28px,4vw,44px);margin:14px 0}.doc-intro p,.doc-upload p,.doc-muted{color:#9aa4b5;font-size:13px;line-height:1.8}
.doc-upload{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:24px;background:#131b25;border:1px solid #283848;border-radius:16px}.doc-upload>div{flex:1;min-width:220px}.doc-upload h2,.doc-section-head h2{font-size:17px;margin:0}.doc-upload p{margin:8px 0 0}.doc-primary,.doc-secondary{border:1px solid #385447;border-radius:8px;padding:12px 18px;cursor:pointer;font:inherit}.doc-primary{background:#a6e9cc;color:#10271d;font-weight:600}.doc-primary input{display:none}.doc-secondary{background:#1c2932;color:#dce5ec}.doc-primary.disabled,button:disabled{opacity:.5;cursor:wait}.doc-upload-note{width:100%}
.doc-layout{display:grid;grid-template-columns:310px minmax(0,1fr);gap:24px;margin-top:28px}.doc-library,.doc-report{background:#101720;border:1px solid #263240;border-radius:14px;padding:22px;min-width:0}.doc-section-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:22px}.doc-section-head h2{overflow-wrap:anywhere}.doc-link{background:none;border:0;color:#a6e9cc;cursor:pointer}.doc-card{display:flex;flex-direction:column;gap:10px;text-align:left;width:100%;border:1px solid #2a3544;background:#151e29;color:inherit;border-radius:10px;padding:16px;margin-bottom:12px;cursor:pointer}.doc-card.selected{border-color:#85c9ad;background:#1a2b2b}.doc-card strong{font-size:14px;overflow-wrap:anywhere}.doc-format{font-size:10px;letter-spacing:1px;color:#9bd5c0}.doc-state{font-size:12px;color:#a6e9cc;white-space:nowrap}.doc-state.failed{color:#f3a6a6}
.doc-notice,.doc-error{padding:14px 18px;border-radius:8px;font-size:13px;line-height:1.8}.doc-notice{background:#19302a;color:#a6e9cc}.doc-error{background:#382227;color:#ffc0c0}.doc-report{min-height:420px}.doc-empty,.doc-processing{padding:40px 10px;color:#a8b4c4;line-height:1.9}.doc-empty h2{font-size:22px;color:#e2e9ee}.doc-report-meta{display:flex;gap:18px;align-items:center;font-size:13px;margin-bottom:14px;flex-wrap:wrap}.doc-report-meta strong{color:#a6e9cc;font-size:20px}.doc-warnings{background:#2a2920;color:#d9cfaa;padding:14px;border-radius:8px;font-size:12px;line-height:1.9}.doc-warnings summary,.doc-source summary{cursor:pointer}.doc-outline{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0}.doc-outline a{font-size:12px;color:#b5d9cf;border:1px solid #32473f;padding:7px 10px;border-radius:6px;text-decoration:none}.doc-knowledge-section{scroll-margin-top:30px}.doc-knowledge-section h3{font-size:20px;border-bottom:1px solid #2a3a43;padding-bottom:14px;margin:32px 0 8px}.doc-knowledge-point{padding:16px 0;border-bottom:1px solid #202c38}.doc-knowledge-point h4{font-size:16px;margin:0 0 10px;color:#e3ece9}.doc-explanation{white-space:pre-wrap;line-height:1.9;color:#c0cbd4;font-size:14px}.doc-source{font-size:12px;color:#85bdac;line-height:1.8}.doc-source blockquote{white-space:pre-wrap;margin:12px 0;border-left:2px solid #688f81;padding:10px 14px;background:#19232b;color:#b8c4ca}progress{width:100%;height:7px;accent-color:#a6e9cc}
@media(max-width:850px){.doc-layout{grid-template-columns:1fr}.document-workspace{padding:32px 16px}.doc-library{max-height:340px;overflow:auto}.doc-upload{padding:18px}.doc-report{padding:18px}}
</style>
