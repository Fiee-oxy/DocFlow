<template>
  <section class="chat" aria-label="文档聊天">
    <div v-if="!document" class="welcome"><h2>发来一份文档，开始提问。</h2><p>支持 DOCX / 文本型 PDF。回答会附上当前文档的原文出处。</p></div>
    <p v-else class="hint">当前对话：{{ document.filename }} · {{ document.totalBlocks ? (document.parseComplete ? '可根据解析原文提问' : '仅覆盖已解析部分，后续内容处理中') : '正在等待解析，可先输入问题' }}</p>
    <div class="messages" ref="messagesEl" role="log" aria-label="聊天记录">
      <p v-if="historyLoading" class="hint">正在读取对话…</p>
      <p v-else-if="document && !turns.length" class="hint">可以问：“这份文档讨论了什么？”“某个概念有哪些限制？”</p>
      <template v-for="turn in turns" :key="turn.requestId">
        <article class="bubble user"><span>你</span><p>{{ turn.question }}</p></article>
        <article class="bubble assistant"><span>文档助手</span>
          <p>{{ turn.response.answer }}</p>
          <details v-if="turn.response.citations?.length" class="citation">
            <summary>查看原文出处（{{ turn.response.citations.length }}）</summary>
            <div v-for="(source, index) in turn.response.citations" :key="index">
              <p>[{{ index + 1 }}] {{ source.section }} · {{ source.pages?.length ? `物理页 ${source.pages.join('、')}` : '章节 / 段落' }}</p>
              <blockquote>{{ source.quote }}</blockquote>
            </div>
          </details>
        </article>
      </template>
      <article v-if="sending && pending" class="bubble user"><span>你</span><p>{{ pending.question }}</p></article>
      <article v-if="sending && streamDraft" class="bubble assistant"><span>文档助手 · 生成中，引用待核验</span><p>{{ streamDraft }}</p></article>
      <p v-if="sending" class="hint" role="status">{{ stage || '正在检索原文并生成回答…' }}</p>
    </div>
    <p v-if="error" class="chat-error" role="alert">{{ error }}</p>
    <form class="composer" @submit.prevent="send">
      <textarea v-model="draft" rows="3" maxlength="2000" :disabled="sending" placeholder="输入针对这份文档的问题，可继续追问…" aria-label="文档问题" @keydown.ctrl.enter.prevent="send" />
      <div class="actions">
        <label class="attach" :class="{ disabled: uploading || sending }">{{ uploading ? documentUploadLabel(progress) : '＋ 发送文档' }}<input type="file" accept=".docx,.pdf" :disabled="uploading || sending" @change="attach" /></label>
        <button v-if="uploading" type="button" class="transfer-button" :disabled="progress.phase === 'finalizing'" @click="emit('pause')">{{ progress.phase === 'hashing' ? '暂停校验' : '暂停上传' }}</button>
        <button v-else-if="uploadPaused" type="button" class="transfer-button" @click="emit('resume')">继续上传</button>
        <span class="hint">{{ ready ? 'Ctrl + Enter 发送' : '选择文档，内容块解析完成后即可发送问题' }}</span>
        <button type="submit" :disabled="!ready || sending || historyLoading || !draft.trim()">{{ sending ? '回答中…' : '发送问题' }}</button>
      </div>
      <div v-if="uploading || uploadPaused" class="transfer-status" role="status">
        <p class="hint">{{ uploadFilename }} · {{ uploadPaused ? '已暂停' : documentUploadLabel(progress) }}<span v-if="progress.phase === 'uploading'"> · 分片 {{ progress.completed || 0 }}/{{ progress.total || 0 }}</span></p>
        <progress :value="progress.percent || 0" max="100" aria-label="文档校验与上传进度" />
        <p v-if="!uploadPaused && progress.phase === 'hashing'" class="hint">正在本地校验文件内容，完成后自动检查是否可续传或复用。</p>
        <p v-if="uploadPaused" class="hint">已完成分片会保留。点击“继续上传”即可续传；刷新页面后重新选择同一文件也能找回进度。</p>
      </div>
    </form>
  </section>
</template>
<script setup>
import { onUnmounted } from 'vue'
import { useDocumentChat } from './useDocumentChat.js'
import { documentUploadLabel } from './documentUpload.js'
const props = defineProps({ document: Object, user: Object, uploading: Boolean, uploadPaused: Boolean, uploadFilename: String, progress: { type: Object, default: () => ({}) } })
const emit = defineEmits(['file', 'login', 'pause', 'resume'])
const { turns, draft, error, sending, historyLoading, messagesEl, pending, ready, attach, send, dispose, streamDraft, stage } = useDocumentChat(props, emit, { streaming: true })
onUnmounted(dispose)
</script>
<style scoped>
.actions button.transfer-button{background:transparent;border:1px solid #79a994;color:#b5dbc9}.transfer-status progress{width:100%;height:7px;accent-color:#a6e9cc}
.chat{display:flex;flex-direction:column;min-height:560px}.welcome{text-align:center;padding:42px 15px}.welcome h2{font-size:25px}.welcome p,.hint{font-size:12px;color:#9aa4b5;line-height:1.8}.messages{flex:1;max-height:560px;overflow:auto;padding:12px 4px;overscroll-behavior:contain}.bubble{padding:16px 18px;border-radius:12px;margin:12px 0;max-width:92%;overflow-wrap:anywhere}.bubble>span{font-size:11px;color:#9bc8b9}.bubble p{white-space:pre-wrap;font-size:14px;line-height:1.9;margin:8px 0}.user{background:#243b36;margin-left:auto}.assistant{background:#18232e;border:1px solid #2a3b4a}.citation{font-size:12px;color:#a6d9c6;line-height:1.8;margin:12px 0}.citation summary{cursor:pointer}.citation blockquote{white-space:pre-wrap;margin:10px 0;padding:12px;border-left:2px solid #73b69b;background:#111c25}.composer{border:1px solid #3a5149;border-radius:12px;background:#16232b;padding:14px;margin-top:20px}.composer textarea{box-sizing:border-box;width:100%;resize:vertical;background:transparent;border:0;color:#e7e9ee;font:inherit;font-size:14px;line-height:1.8;outline:none}.composer textarea:focus{outline:1px solid #85c9ad;border-radius:4px}.actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.actions .hint{flex:1}.attach{font-size:13px;color:#b5dbc9;cursor:pointer}.attach input{display:none}.actions button{border:0;border-radius:7px;padding:10px 16px;background:#a6e9cc;color:#10271d;font:inherit;cursor:pointer}.actions button:disabled,.disabled{opacity:.5;cursor:default}.chat-error{color:#ffc0c0;background:#382227;padding:12px;border-radius:8px;line-height:1.8;font-size:13px}
</style>
