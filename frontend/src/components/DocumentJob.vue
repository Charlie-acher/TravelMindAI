<script setup lang="ts">
/** 资料后台任务组件：只轮询状态，关闭页面不取消后台任务，暂停须明确点击。 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { apiFetch, readResponse } from '../api/http'

const props = defineProps<{ documentId: string; parsed: boolean }>()
const emit = defineEmits<{ completed: [] }>()
type Job = {
  run_id: string; kind: 'parse' | 'index'
  status: 'queued' | 'running' | 'completed' | 'failed' | 'paused'
  indexed: number; total: number; error_message: string | null
}
const job = ref<Job | null>(null)
const busy = ref(false)
const error = ref('')
const labels = { queued: '等待处理', running: '正在处理', completed: '已完成', failed: '未完成，可重试', paused: '已暂停' }
let alive = true
let generation = 0 // 暂停、重试或刷新后，旧请求不能覆盖新状态。
let timer: ReturnType<typeof setTimeout> | undefined

/** 状态刷新函数：一次请求结束后再安排下次，错误时保留旧状态并提供手动刷新。 */
async function refresh(): Promise<void> {
  clearTimeout(timer)
  if (!alive || busy.value) return
  const token = ++generation
  try {
    const previous = job.value?.status
    const result = await readResponse<Job | null>(await apiFetch(`/api/v1/admin/documents/${props.documentId}/job`), true)
    if (!alive || token !== generation) return
    job.value = result
    error.value = ''
    // 初次打开已完成资料无需刷新目录；只有观察到处理结束时更新片段。
    if (result?.status === 'completed' && previous && previous !== 'completed') emit('completed')
    if (result && ['queued', 'running'].includes(result.status)) timer = setTimeout(() => { void refresh() }, 1500)
  } catch (cause) {
    if (alive && token === generation) error.value = cause instanceof Error ? cause.message : '任务状态读取失败，请刷新。'
  }
}

/** 任务操作函数：启动或暂停由服务器保存；重试索引会复用已经确认的片段。 */
async function submit(pause = false): Promise<void> {
  if (busy.value) return
  clearTimeout(timer)
  ++generation
  busy.value = true
  error.value = ''
  try {
    const response = await apiFetch(`/api/v1/admin/documents/${props.documentId}/job${pause ? '/pause' : ''}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      ...(pause ? {} : { body: JSON.stringify({ kind: props.parsed ? 'index' : 'parse' }) }),
    })
    const result = await readResponse<Job | null>(response, true)
    if (alive) job.value = result
  } catch (cause) {
    if (alive) error.value = cause instanceof Error ? cause.message : '任务提交结果未确认，请刷新状态。'
  } finally {
    busy.value = false
    if (alive) void refresh()
  }
}
onMounted(() => { void refresh() })
onBeforeUnmount(() => { alive = false; ++generation; clearTimeout(timer) })
</script>

<template>
  <section class="document-job" aria-label="资料后台任务">
    <p v-if="job" role="status">{{ job.kind === 'parse' ? '读取' : '索引' }}：{{ labels[job.status] }}<span v-if="job.total"> · {{ job.indexed }} / {{ job.total }} 段</span></p>
    <p v-else>尚无后台任务。</p>
    <p v-if="job?.error_message" role="alert">{{ job.error_message }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <button v-if="error || job?.status === 'failed' || job?.status === 'paused'" :disabled="busy" @click="refresh">刷新状态</button>
    <button v-if="job && ['queued', 'running'].includes(job.status)" :disabled="busy" @click="submit(true)">暂停处理</button>
    <button v-else-if="!job || ['failed', 'paused'].includes(job.status)" :disabled="busy" @click="submit()">{{ parsed ? '重试索引' : '重试读取' }}</button>
  </section>
</template>

<style scoped>
.document-job { border-top: 1px solid #dfe8e2; padding: 8px 0; margin: 8px 0; }
p { font-size: 13px; line-height: 1.5; margin: 5px 0; }
button { margin: 4px 8px 4px 0; padding: 8px 12px; cursor: pointer; }
</style>
