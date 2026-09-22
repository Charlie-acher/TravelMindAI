<script setup lang="ts">
/** 上传组件层：批量上传后由后端自动切片和索引，页面按每份资料显示处理进度。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { readDocumentJob, startDocumentIndex, uploadDocument, type DocumentDetail, type DocumentJobState } from '../api/documents'
import ChatIcon from './ChatIcon.vue'

const props = defineProps<{ active: boolean; disabled: boolean }>()
const emit = defineEmits<{
  busy: [value: boolean]
  uploaded: [document: DocumentDetail]
  finished: []
  openDocument: [id: string]
}>()

/** 上传条目类型：文件只留在当前页面内；documentId表示后端已确认保存。 */
interface UploadItem {
  file: File
  status: 'pending' | 'uploading' | 'parsed' | 'failed' | 'unconfirmed' | 'invalid' | 'deleting'
  message: string
  documentId?: string
  processing?: boolean
  retrying?: boolean
  job?: DocumentJobState
}

const queue = ref<UploadItem[]>([])
const busy = ref(false)
const stopped = ref(false)
const pending = computed(() => queue.value.filter(item => item.status === 'pending'))
const confirmed = computed(() => queue.value.filter(item => item.documentId).length)
const completed = computed(() => queue.value.filter(item => item.job?.status === 'completed').length)
let mounted = true
let timer: ReturnType<typeof setTimeout> | undefined

/** 批量进度刷新函数：只轮询本批未结束任务，单份请求失败不影响其他资料。 */
async function refreshJobs(): Promise<void> {
  for (const item of queue.value.filter(item => item.processing && item.documentId && !item.retrying)) {
    if (!mounted) break
    try {
      const job = await readDocumentJob(item.documentId!)
      if (!mounted || !queue.value.includes(item)) continue
      item.job = job ?? undefined
      item.processing = Boolean(job && ['queued', 'running'].includes(job.status))
      const labels = { queued: '等待自动切片和索引', running: '正在自动切片和建立索引', completed: '切片和索引已完成', failed: '自动处理失败，可重试', paused: '后台处理已暂停' }
      item.message = job ? `${labels[job.status]}${job.total ? ` · ${job.indexed} / ${job.total} 段` : ''}${job.error_message ? `：${job.error_message}` : ''}` : '资料已保存，未发现后台任务，请重试自动处理。'
    } catch (cause) {
      if (mounted) item.message = `进度暂时无法读取，将继续查询：${cause instanceof Error ? cause.message : '请检查连接。'}`
    }
  }
  if (mounted) timer = setTimeout(() => { void refreshJobs() }, 2000)
}

/** 处理重试函数：只提交已经保存的资料编号，不重复上传文件。 */
async function retryProcessing(item: UploadItem): Promise<void> {
  if (!item.documentId || item.retrying) return
  item.retrying = true
  try {
    item.job = await startDocumentIndex(item.documentId)
    item.processing = true
    item.message = '已提交自动切片和索引，正在等待后台处理。'
  } catch (cause) {
    item.message = cause instanceof Error ? cause.message : '处理提交未确认，请打开资料查看状态。'
  } finally {
    item.retrying = false
  }
}

/** 选择函数：逐份检查格式和大小，坏文件单独标记，不挡住其他有效文件。 */
function choose(event: Event): void {
  if (busy.value || props.disabled) return
  const input = event.target as HTMLInputElement
  queue.value = Array.from(input.files ?? [], file => {
    const message = !/\.(pdf|docx|txt|md|markdown)$/i.test(file.name)
      ? '格式不支持，请换成 PDF、DOCX、TXT 或 Markdown。'
      : !file.size || file.size > 20 * 1024 * 1024 ? '文件必须有内容且不超过 20 MiB。' : ''
    return { file, status: message ? 'invalid' : 'pending', message: message || '等待上传' }
  })
  input.value = '' // 可以再次选择同一文件；是否重复最终由后端按内容确认。
  stopped.value = false
}

/** 上传函数：收到一份结果再发下一份；失败逐项记录，暂停后保留尚未发送的文件。 */
async function run(items = pending.value): Promise<void> {
  if (busy.value || props.disabled || !props.active || !items.length) return
  busy.value = true
  stopped.value = false
  emit('busy', true)
  let lastDocument: DocumentDetail | undefined
  try {
    for (const item of items) {
      if (stopped.value || !mounted) break
      item.status = 'uploading'
      item.message = '正在上传并读取…'
      try {
        const result = await uploadDocument(item.file)
        item.documentId = result.document.id
        item.status = result.document.status
        item.message = result.document.status === 'parsed'
          ? result.processing_error ?? `${result.duplicate ? '内容已存在，沿用原资料。' : '已保存并读取。'}已提交自动切片和索引。`
          : `${result.duplicate ? '已有资料：' : '已保存：'}${result.document.error_message ?? '请打开资料查看状态。'}`
        lastDocument = result.document
        item.processing = result.document.status === 'parsed' && !result.processing_error
      } catch (cause) {
        // 请求失败不等于后端没保存；重试仍使用同一内容，由后端指纹去重。
        item.status = 'unconfirmed'
        item.message = cause instanceof Error ? cause.message : '上传结果未确认，请重试。'
      }
    }
  } finally {
    busy.value = false
    if (mounted) {
      if (lastDocument) emit('uploaded', lastDocument)
      emit('busy', false)
      emit('finished')
    }
  }
}

// 切回聊天继续上传；关闭页面只停止未发送文件，已提交后台的任务继续运行。
onMounted(() => { void refreshJobs() })
onBeforeUnmount(() => { mounted = false; stopped.value = true; clearTimeout(timer) })
</script>

<template>
  <div class="document-upload">
    <div class="upload-heading"><ChatIcon name="file" /><div><strong>添加旅行资料</strong><p>PDF、DOCX、TXT、Markdown · 单份最多 20 MiB</p></div></div>
    <label class="choose-file" for="travel-document-file">选择资料文件（可多选）</label>
    <input id="travel-document-file" type="file" multiple accept=".pdf,.docx,.txt,.md,.markdown"
      :disabled="busy || disabled" @change="choose" />
    <p>上传后自动识别城市与类别、切片并建立索引。扫描版 PDF 暂不支持文字识别。</p>
    <template v-if="queue.length">
      <p role="status">共 {{ queue.length }} 份 · 已确认保存 {{ confirmed }} 份 · 索引完成 {{ completed }} 份 · 待上传 {{ pending.length }} 份</p>
      <div class="upload-actions">
        <button :disabled="busy || disabled || !active || !pending.length" @click="run()">{{ busy ? '正在逐份上传…' : '上传并自动处理' }}</button>
        <button v-if="busy" :disabled="stopped" @click="stopped = true">{{ stopped ? '当前文件完成后暂停' : '暂停后续上传' }}</button>
      </div>
      <ul aria-label="本批上传结果">
        <li v-for="(item, index) in queue" :key="index">
          <strong>{{ item.file.name }}</strong>
          <span :class="{ error: ['failed', 'unconfirmed', 'invalid', 'deleting'].includes(item.status) || item.job?.status === 'failed' }">{{ item.message }}</span>
          <button v-if="item.documentId" :disabled="busy || disabled" @click="emit('openDocument', item.documentId)">查看资料</button>
          <button v-if="item.status === 'unconfirmed'" :disabled="busy || disabled || !active" @click="run([item])">重试这份上传</button>
          <button v-if="item.status === 'parsed' && !item.processing && item.job?.status !== 'completed'" :disabled="item.retrying" @click="retryProcessing(item)">{{ item.retrying ? '正在提交…' : '重试自动切片和索引' }}</button>
        </li>
      </ul>
    </template>
    <p class="upload-note">重新选择会替换本批列表。刷新或关闭页面后，待传文件需重新选择；已保存资料仍在库中。</p>
  </div>
</template>

<style scoped>
.document-upload { padding: 20px; background: #fafcf7; border: 1px dashed #cddcc2; border-radius: 10px; margin-bottom: 20px; }
.upload-heading { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }.upload-heading>.chat-icon { color: #7b9a68; width: 28px; height: 28px; }.upload-heading strong { font-size: 14px; color: #4e7043; }.upload-heading p { margin: 4px 0 0; }
.choose-file { display: inline-block; padding: 8px 12px; border: 1px solid #cddcc2; border-radius: 6px; font-size: 12px; background: white; color: #567d45; cursor: pointer; }
input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.document-upload:has(input:focus-visible) .choose-file { outline: 2px solid #7eaa6d; outline-offset: 3px; }
.document-upload:has(input:disabled) .choose-file { opacity: .5; cursor: default; }
p, li span { font-size: 12px; line-height: 1.8; color: #758869; }
.upload-note { margin-bottom: 0; font-size: 11px; }
.upload-actions { display: flex; gap: 8px; flex-wrap: wrap; }
button { background: #397352; color: white; border: 0; border-radius: 6px; padding: 8px 12px; font: inherit; font-size: 12px; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
button:focus-visible { outline: 3px solid #90b9a5; outline-offset: 2px; }
ul { padding: 0; list-style: none; max-height: 280px; overflow-y: auto; }
li { display: grid; gap: 6px; padding: 12px 0; border-top: 1px solid #dfe8e2; overflow-wrap: anywhere; }
li button { justify-self: start; }
.error { color: #b33434; }
</style>
