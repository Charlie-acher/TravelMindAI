<script setup lang="ts">
/** 页面组件层：编辑当前资料标签，保存成功后通知资料页更新列表与详情。 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { prefillDocumentMetadata, updateDocumentMetadata, type DocumentDetail, type DocumentMetadata } from '../api/documents'
import DocumentMetadataFields from './DocumentMetadataFields.vue'

const props = defineProps<{ document: DocumentDetail; disabled: boolean }>()
const emit = defineEmits<{ saved: [document: DocumentDetail]; busy: [value: boolean] }>()
const metadata = ref<DocumentMetadata>({ city: null, source: null, review_status: null, poi_id: null })
const saving = ref(false)
const message = ref('')
const saved = ref(false)
let active = true
// 同一资料的后台进度刷新不覆盖未保存输入；切换资料时才重新装入标签。
watch(() => props.document.id, () => {
  const value = props.document
  metadata.value = { city: value.city, source: value.source, review_status: value.review_status, poi_id: value.poi_id }
  message.value = ''
  saved.value = false
}, { immediate: true })

/** 保存函数：只提交标签；请求失败保留输入，成功以后端回读值为准。 */
async function save(prefill = false): Promise<void> {
  if (saving.value || props.disabled || props.document.status === 'deleting') return
  saving.value = true
  emit('busy', true)
  message.value = ''
  saved.value = false
  try {
    const result = prefill ? await prefillDocumentMetadata(props.document.id) : await updateDocumentMetadata(props.document.id, metadata.value)
    if (active && props.document.id === result.id) {
      metadata.value = {
        city: prefill ? metadata.value.city?.trim() || result.city : result.city,
        source: prefill ? metadata.value.source?.trim() || result.source : result.source,
        review_status: prefill ? metadata.value.review_status || result.review_status : result.review_status,
        poi_id: prefill ? metadata.value.poi_id?.trim() || result.poi_id : result.poi_id,
      }
      saved.value = true
      message.value = prefill ? '已识别并补全可确定的空标签；原有输入已保留，手动修改后请保存。' : '资料标签已保存。'
      emit('saved', result)
    }
  } catch (cause) {
    if (active) message.value = cause instanceof Error ? cause.message : '标签保存失败，请重试。'
  } finally {
    saving.value = false
    emit('busy', false)
  }
}
onBeforeUnmount(() => { active = false })
</script>

<template>
  <form @submit.prevent="save()">
    <DocumentMetadataFields v-model="metadata" :disabled="disabled || saving || document.status === 'deleting'" />
    <small>城市、来源按文件名和正文明确标注预填，无法确定则留空，请核对后保存。POI编号是地图平台的单个地点标识，多地点攻略通常留空。修改标签无需重建索引。</small>
    <button :disabled="disabled || saving || document.status === 'deleting'">{{ saving ? '正在保存…' : '保存资料标签' }}</button>
    <button type="button" :disabled="disabled || saving || document.status === 'deleting'" @click="save(true)">识别并补全空标签</button>
    <p v-if="message" :role="saved ? 'status' : 'alert'" :class="{ success: saved }">{{ message }}</p>
  </form>
</template>

<style scoped>
small { display: block; color: #63756b; line-height: 1.7; }
button { margin: 10px 0; padding: 8px 14px; border: 0; border-radius: 6px; background: #286c54; color: white; cursor: pointer; font: inherit; }
button:disabled { opacity: .5; cursor: default; }
p { color: #b33434; }
p.success { color: #286c54; }
</style>
