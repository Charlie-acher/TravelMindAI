<script setup lang="ts">
/** 文件预览层：复用保存的行程卡片，原件预览仅在服务器通过归属检查后读取。 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { attachmentContentUrl } from '../api/attachments'
import { apiFetch, readResponse } from '../api/http'
import type { PersonalFileDetail } from '../api/personalFiles'
import ChatIcon from './ChatIcon.vue'
import ItineraryCard from './ItineraryCard.vue'

const props = defineProps<{ detail: PersonalFileDetail }>()
const text = ref('')
const truncated = ref(false)
const loading = ref(false)
const error = ref('')
let generation = 0

/** 清理函数：切换文件或退出页面后，旧正文和在途响应立即失效。 */
function clearPreview(): void {
  generation++
  text.value = ''; error.value = ''; truncated.value = false; loading.value = false
}

watch(() => props.detail.item.id, async () => {
  clearPreview()
  const item = props.detail.item
  if (item.kind !== 'attachment' || !['text/plain', 'text/markdown'].includes(item.mime_type)) return
  const current = generation
  loading.value = true
  try {
    const response = await apiFetch(attachmentContentUrl(item.session_id, item.id), { cache: 'no-store' })
    if (!response.ok) { await readResponse(response); return }
    const value = await response.text()
    if (current === generation) { text.value = value.slice(0, 80_000); truncated.value = value.length > 80_000 }
  } catch (cause) {
    if (current === generation) error.value = cause instanceof Error ? cause.message : '原件预览加载失败'
  } finally { if (current === generation) loading.value = false }
}, { immediate: true })
onBeforeUnmount(clearPreview)
</script>

<template>
  <div class="personal-file-preview">
    <div v-if="detail.itinerary" class="saved-plan-preview"><ItineraryCard :snapshot="detail.itinerary" :origin="detail.extraction?.origin" :undo-available="false" :busy="false" /></div>
    <template v-else-if="detail.attachment">
      <p v-if="loading" role="status">正在读取原件…</p><p v-if="error" role="alert">{{ error }}</p>
      <img v-if="detail.item.mime_type.startsWith('image/')" class="original-image" :src="attachmentContentUrl(detail.item.session_id, detail.item.id, true)" :alt="detail.item.file_name" />
      <a v-if="detail.item.mime_type === 'application/pdf'" class="pdf-preview-link" :href="attachmentContentUrl(detail.item.session_id, detail.item.id, true)" target="_blank" rel="noopener"><ChatIcon name="file" /><strong>打开 PDF 原件预览</strong><span>在新标签页阅读完整文档 ↗</span></a>
      <pre v-if="text" class="original-text">{{ text }}</pre><p v-if="truncated" class="preview-note">此处展示前 80,000 个字符，可下载原件查看全文。</p>
      <details v-if="detail.attachment.analysis" class="file-analysis" :open="!text && !detail.item.mime_type.startsWith('image/')"><summary>附件识别摘要</summary><p>{{ detail.attachment.analysis.summary }}</p><p v-for="warning in detail.attachment.analysis.warnings" :key="warning">{{ warning }}</p></details>
      <p v-else-if="!loading && !text && !detail.item.mime_type.startsWith('image/') && detail.item.mime_type !== 'application/pdf'">{{ detail.attachment.error_message || '此格式暂不支持页内预览，请打开原件查看。' }}</p>
      <a v-if="detail.item.mime_type !== 'application/pdf'" :href="attachmentContentUrl(detail.item.session_id, detail.item.id, true)" target="_blank" rel="noopener">打开原件 ↗</a>
    </template>
  </div>
</template>

<style scoped>
.personal-file-preview { min-width: 0; margin-top: 16px; color: #496653; font-size: 12px; line-height: 1.7; }.original-image { display: block; width: 100%; height: auto; max-height: 680px; object-fit: contain; border-radius: 10px; background: #f6f8f5; }.pdf-preview-link { display: grid; justify-items: center; gap: 9px; padding: 28px; border: 1px solid #dfe7df; border-radius: 10px; background: #f6f8f5; }.pdf-preview-link svg { width: 32px; height: 32px; color: #9cb087; }.pdf-preview-link span { color: #879682; font-size: 11px; }.original-text { white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; max-height: 650px; padding: 18px; background: #f6f8f5; border-radius: 10px; font: inherit; }.file-analysis { margin: 15px 0; }.file-analysis summary { cursor: pointer; color: #315e46; }.preview-note { color: #839083; }a { color: #28735a; text-decoration: none; }
/* 已保存文件只展示快照，继续提问和修改均从来源会话进入。 */
.saved-plan-preview :deep(.route-query),.saved-plan-preview :deep(.weather-query),.saved-plan-preview :deep(.query-hint),.saved-plan-preview :deep(.plan-queries > h4),.saved-plan-preview :deep(.itinerary-card > footer) { display: none; }.saved-plan-preview :deep(.plan-grid) { grid-template-columns: minmax(0, 1fr); }.saved-plan-preview :deep(.trip-aside) { width: 100%; }.saved-plan-preview :deep(.itinerary-card) { max-width: 100%; margin: 0; }
</style>
