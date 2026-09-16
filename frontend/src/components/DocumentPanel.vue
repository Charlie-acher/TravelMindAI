<script setup lang="ts">
/** 管理员资料页面：服务端城市计数、城市内分页及单份文件预览。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Card as ACard, Tag as ATag } from '@arco-design/web-vue'
import { deleteDocument, listDocumentCities, pageDocuments, readDocument, retryDocument,
  type DocumentCity, type DocumentDetail, type DocumentFilters, type DocumentMetadata, type DocumentSummary } from '../api/documents'
import DocumentMetadataEditor from './DocumentMetadataEditor.vue'
import DocumentMetadataFields from './DocumentMetadataFields.vue'
import DocumentChunks from './DocumentChunks.vue'
import DocumentJob from './DocumentJob.vue'
import DocumentUpload from './DocumentUpload.vue'

defineProps<{ active: boolean }>()
interface CityGroup { city: string | null; total: number; expanded: boolean; files: DocumentSummary[]; cursor: string | null; loaded: boolean; busy: boolean; error: string }
const groups = ref<CityGroup[]>([])
const detail = ref<DocumentDetail | null>(null)
const fileQuery = ref('')
const filter = ref<DocumentMetadata>({ city: null, category: null })
const applied = ref<DocumentFilters>({})
const loading = ref(false)
const uploading = ref(false)
const reading = ref(false)
const retrying = ref(false)
const deleting = ref(false)
const savingMetadata = ref(false)
const changing = computed(() => uploading.value || retrying.value || deleting.value || savingMetadata.value)
const error = ref('')
const notice = ref('')
const confirmingDelete = ref(false)
const previewMode = ref<'chunks' | 'original'>('chunks')
const sectionPage = ref(0)
const chunkVersion = ref(0) // 后台切片完成后重建当前文件的片段预览。
const visibleSections = computed(() => detail.value?.sections.slice(sectionPage.value * 50, (sectionPage.value + 1) * 50) ?? [])
const sectionPages = computed(() => Math.ceil((detail.value?.sections.length ?? 0) / 50))
let alive = true
let request = 0
let groupsRequest = 0

/** 先取完整城市计数；点击城市才读取该城市的文件页。 */
async function refreshGroups(useForm = false): Promise<void> {
  if (loading.value) return
  const filters: DocumentFilters = useForm ? { q: fileQuery.value.trim(), city: filter.value.city?.trim() || null, category: filter.value.category } : applied.value
  const token = ++groupsRequest
  loading.value = true; error.value = ''
  try {
    const cities: DocumentCity[] = await listDocumentCities(filters)
    if (!alive || token !== groupsRequest) return
    applied.value = filters
    groups.value = cities.map(item => ({ city: item.city, total: item.total, expanded: false, files: [], cursor: null, loaded: false, busy: false, error: '' }))
    if (groups.value.length === 1) void expand(groups.value[0])
  } catch (cause) {
    if (alive && token === groupsRequest) error.value = cause instanceof Error ? cause.message : '城市列表读取失败，请重试。'
  } finally { if (alive && token === groupsRequest) loading.value = false }
}

/** 城市组只展示该城市的服务端分页，不用当前页在浏览器拼计数。 */
async function loadGroup(group: CityGroup): Promise<void> {
  if (group.busy || (group.loaded && !group.cursor)) return
  group.busy = true; group.error = ''
  const token = groupsRequest
  try {
    const page = await pageDocuments({ ...applied.value, city: group.city, unclassified_city: group.city === null }, group.cursor)
    if (!alive || token !== groupsRequest || !groups.value.includes(group)) return
    group.files.push(...page.items); group.cursor = page.next_cursor; group.loaded = true
  } catch (cause) {
    if (alive && token === groupsRequest) group.error = cause instanceof Error ? cause.message : '文件列表读取失败，请重试。'
  } finally { group.busy = false }
}
function expand(group: CityGroup): void { group.expanded = !group.expanded; if (group.expanded && !group.loaded) void loadGroup(group) }

/** 点击资料清空旧预览，慢请求不可覆盖后选资料。 */
async function preview(id: string): Promise<void> {
  if (changing.value) return
  const token = ++request
  reading.value = true; detail.value = null; error.value = ''; notice.value = ''; confirmingDelete.value = false
  sectionPage.value = 0; previewMode.value = 'chunks'
  try {
    const result = await readDocument(id)
    if (alive && token === request) detail.value = result
  } catch (cause) { if (alive && token === request) error.value = cause instanceof Error ? cause.message : '资料读取失败，请重试。' }
  finally { if (alive && token === request) reading.value = false }
}

function uploaded(document: DocumentDetail): void { if (alive) { detail.value = document; previewMode.value = 'chunks'; sectionPage.value = 0 } }
function metadataSaved(document: DocumentDetail): void { if (detail.value?.id === document.id) detail.value = document; notice.value = '资料标签已保存。'; void refreshGroups() }
async function jobCompleted(): Promise<void> {
  const id = detail.value?.id
  if (!id) return
  ++chunkVersion.value
  try { const result = await readDocument(id); if (alive && detail.value?.id === id) detail.value = result; await refreshGroups() }
  catch (cause) { if (alive) error.value = cause instanceof Error ? cause.message : '后台完成但资料刷新失败。' }
}

async function retry(): Promise<void> {
  const id = detail.value?.id
  if (!id || retrying.value) return
  retrying.value = true; error.value = ''
  try { const result = await retryDocument(id); if (alive && detail.value?.id === id) detail.value = result; await refreshGroups() }
  catch (cause) { if (alive) error.value = cause instanceof Error ? cause.message : '重试未确认，请刷新资料。' }
  finally { retrying.value = false }
}

async function remove(): Promise<void> {
  const id = detail.value?.id
  if (!id || deleting.value) return
  deleting.value = true; confirmingDelete.value = false; error.value = ''
  try { await deleteDocument(id); if (alive) { ++request; detail.value = null; notice.value = '资料已删除，历史回答保持原样。'; await refreshGroups() } }
  catch (cause) { if (alive) error.value = cause instanceof Error ? cause.message : '删除结果未确认，请刷新后重试。' }
  finally { deleting.value = false }
}
function fileSize(bytes: number): string { return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB` }
onMounted(() => { void refreshGroups() })
onBeforeUnmount(() => { alive = false; ++request; ++groupsRequest })
</script>

<template>
  <section class="documents-workspace" aria-label="旅行资料库">
    <a-card class="document-library" :bordered="false"><template #title>旅行资料</template>
      <DocumentUpload :active="active" :disabled="changing || loading" @busy="uploading = $event" @uploaded="uploaded" @finished="refreshGroups()" @open-document="preview" />
      <a-alert v-if="error" type="error" class="document-feedback">{{ error }}</a-alert><a-alert v-if="notice" type="info" class="document-feedback">{{ notice }}</a-alert>
      <form class="document-filter" @submit.prevent="refreshGroups(true)"><label for="document-name-query">按文件名查找</label>
        <input id="document-name-query" v-model="fileQuery" maxlength="255" placeholder="文件名关键词" :disabled="changing || loading" />
        <DocumentMetadataFields v-model="filter" filtering :disabled="changing || loading" />
        <a-button html-type="submit" :disabled="changing || loading">查找</a-button><a-button :disabled="changing || loading" @click="fileQuery = ''; filter = { city: null, category: null }; refreshGroups(true)">清空</a-button></form>
      <p v-if="loading" role="status">正在读取城市列表…</p><p v-else-if="!groups.length && !error" class="document-empty">{{ applied.q || applied.city || applied.category ? '没有符合条件的资料。' : '还没有资料，选择文件开始上传。' }}</p>
      <div v-for="group in groups" :key="group.city ?? 'unclassified'" class="city-group">
        <button type="button" class="city-toggle" :disabled="changing" :aria-expanded="group.expanded" @click="expand(group)">{{ group.expanded ? '▾' : '▸' }} {{ group.city || '未标注城市' }}（{{ group.total }}）</button>
        <template v-if="group.expanded"><p v-if="group.busy" role="status">正在读取文件…</p><p v-if="group.error" role="alert">{{ group.error }} <a-button size="mini" @click="loadGroup(group)">重试加载</a-button></p>
          <ul class="document-list"><li v-for="document in group.files" :key="document.id"><button type="button" :class="{ selected: detail?.id === document.id }" :disabled="changing" @click="preview(document.id)">
            <strong>{{ document.file_name }}</strong><span>{{ document.category || '未分类' }} · {{ fileSize(document.size_bytes) }}</span>
            <a-tag :color="document.status === 'parsed' ? 'green' : 'red'">{{ document.status === 'parsed' ? '已读取' : document.status === 'deleting' ? '待清理' : '读取失败' }}</a-tag></button></li></ul>
          <a-button v-if="group.cursor" :disabled="group.busy || changing" @click="loadGroup(group)">加载更多文件</a-button></template>
      </div>
    </a-card>
    <a-card class="document-preview" :bordered="false"><template #title>资料预览</template>
      <p v-if="deleting" role="status">正在删除资料…</p><p v-else-if="reading" role="status">正在读取资料…</p>
      <template v-else-if="detail"><h2>{{ detail.file_name }}</h2><p class="document-help">{{ fileSize(detail.size_bytes) }} · {{ detail.section_count }} 个原文单元 · {{ new Date(detail.created_at).toLocaleString('zh-CN') }}</p>
        <DocumentMetadataEditor :key="detail.id" :document="detail" :disabled="changing || loading" @busy="savingMetadata = $event" @saved="metadataSaved" />
        <DocumentJob v-if="detail.status !== 'deleting'" :key="detail.id" :document-id="detail.id" :parsed="detail.status === 'parsed'" @completed="jobCompleted" />
        <a-button v-if="detail.status === 'failed'" :loading="retrying" :disabled="changing" @click="retry">重新解析</a-button>
        <a-button v-if="!confirmingDelete" status="danger" :disabled="changing" @click="confirmingDelete = true">删除资料</a-button>
        <div v-else role="alert" class="document-feedback">确认删除“{{ detail.file_name }}”？<a-button status="danger" @click="remove">确认删除</a-button><a-button @click="confirmingDelete = false">取消</a-button></div>
        <a-alert v-if="detail.error_message" type="error">{{ detail.error_message }}</a-alert>
        <a-alert v-for="warning in detail.warnings" :key="warning" type="warning" class="document-feedback">{{ warning }}</a-alert>
        <div v-if="detail.status === 'parsed'" class="document-pagination" aria-label="预览内容选择"><a-button :aria-pressed="previewMode === 'chunks'" @click="previewMode = 'chunks'">查看片段</a-button><a-button :aria-pressed="previewMode === 'original'" @click="previewMode = 'original'">查看原文</a-button></div>
        <DocumentChunks v-if="detail.status === 'parsed' && previewMode === 'chunks'" :key="`${detail.id}-${chunkVersion}`" :document-id="detail.id" />
        <template v-if="detail.status === 'parsed' && previewMode === 'original'"><div v-if="sectionPages > 1" class="document-pagination"><a-button :disabled="sectionPage === 0" @click="sectionPage--">上一组正文</a-button><span>第 {{ sectionPage + 1 }} / {{ sectionPages }} 组</span><a-button :disabled="sectionPage + 1 >= sectionPages" @click="sectionPage++">下一组正文</a-button></div>
          <div class="document-sections"><article v-for="section in visibleSections" :key="section.order" class="document-section"><div class="document-location">原文单元 {{ section.order }}<span v-if="section.page_number"> · 第 {{ section.page_number }} 页</span><span v-if="section.section_path.length"> · {{ section.section_path.join(' / ') }}</span></div><pre>{{ section.text }}</pre></article></div></template>
      </template><div v-else class="document-empty">选择左侧文件，在这里核对片段与原文。</div>
    </a-card>
  </section>
</template>

<style scoped>
.documents-workspace { display: grid; grid-template-columns: minmax(280px, .85fr) minmax(0, 1.65fr); gap: 22px; align-items: start; }
.document-help { color: #718179; font-size: 13px; line-height: 1.8; }
.document-filter { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0; }
.document-filter label { width: 100%; font-weight: 600; }
.document-filter input { min-width: 0; flex: 1 1 100%; padding: 9px; font: inherit; border: 1px solid #bccbc2; border-radius: 6px; }
.document-feedback { margin: 12px 0; }.city-group { border-top: 1px solid #dfe8e2; padding: 8px 0; }
.city-toggle { width: 100%; text-align: left; background: none; border: 0; padding: 10px; cursor: pointer; font: inherit; font-weight: 600; color: #286c54; }
.document-list { list-style: none; padding: 0; max-height: 390px; overflow-y: auto; }.document-list li { margin: 6px 0; }
.document-list button { display: flex; flex-wrap: wrap; gap: 8px; width: 100%; padding: 12px; border: 1px solid #e2eae4; border-radius: 9px; text-align: left; background: white; cursor: pointer; color: inherit; }
.document-list button.selected { border-color: #286c54; background: #f0f7f2; }.document-list strong { width: 100%; overflow-wrap: anywhere; }
.document-list button > span { font-size: 12px; color: #718179; }.document-preview { min-height: 500px; min-width: 0; }.document-preview h2 { font-size: 19px; overflow-wrap: anywhere; }
.document-empty { color: #718179; padding: 24px 10px; line-height: 1.8; }.document-sections { max-height: 65dvh; overflow-y: auto; }.document-section { border-top: 1px solid #e9efeb; padding: 18px 2px; }
.document-location { color: #286c54; font-size: 12px; overflow-wrap: anywhere; }.document-section pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.9; tab-size: 4; }
.document-pagination { display: flex; align-items: center; gap: 10px; margin: 12px 0; }
@media (max-width: 800px) { .documents-workspace { grid-template-columns: minmax(0, 1fr); } }
</style>
