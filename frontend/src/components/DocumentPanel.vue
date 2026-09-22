<script setup lang="ts">
/** 管理员资料页面：服务端城市计数、城市内分页及单份文件预览。 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton } from '@arco-design/web-vue'
import { deleteDocument, listDocumentCities, pageDocuments, readDocument, retryDocument,
  type DocumentCity, type DocumentDetail, type DocumentFilters, type DocumentMetadata, type DocumentSummary } from '../api/documents'
import DocumentMetadataEditor from './DocumentMetadataEditor.vue'
import DocumentMetadataFields from './DocumentMetadataFields.vue'
import DocumentChunks from './DocumentChunks.vue'
import DocumentJob from './DocumentJob.vue'
import DocumentUpload from './DocumentUpload.vue'
import DocumentSearch from './DocumentSearch.vue'
import ChatIcon from './ChatIcon.vue'

defineProps<{ active: boolean }>()
interface CityGroup { city: string | null; total: number; expanded: boolean; files: DocumentSummary[]; cursor: string | null; loaded: boolean; busy: boolean; error: string }
const groups = ref<CityGroup[]>([])
const tab = ref<'files' | 'search'>('files')
const showUpload = ref(false)
const matchingTotal = computed(() => groups.value.reduce((total, group) => total + group.total, 0))
const detail = ref<DocumentDetail | null>(null)
const previewElement = ref<HTMLElement | null>(null)
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
async function preview(id: string, mode: 'chunks' | 'original' = 'chunks'): Promise<void> {
  if (changing.value) return
  tab.value = 'files'
  const token = ++request
  reading.value = true; detail.value = null; error.value = ''; notice.value = ''; confirmingDelete.value = false
  sectionPage.value = 0; previewMode.value = mode
  try {
    const result = await readDocument(id)
    if (alive && token === request) {
      detail.value = result
      await nextTick()
      previewElement.value?.scrollIntoView({ block: 'nearest' })
    }
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
/** 收起预览时使在途读取失效；再次点击文件仍会读取最新数据。 */
function closePreview(): void { ++request; reading.value = false; detail.value = null; confirmingDelete.value = false }
onMounted(() => { void refreshGroups() })
onBeforeUnmount(() => { alive = false; ++request; ++groupsRequest })
</script>

<template>
  <section class="documents-workspace" aria-label="旅行资料库">
    <header class="library-heading"><div><span class="library-eyebrow"><ChatIcon name="books" />共享旅行资料</span><h1>知识库</h1><p>管理旅行资料，让每一次回答都有据可循。</p></div>
      <button v-if="tab === 'files'" class="primary-button" :aria-expanded="showUpload" @click="showUpload = !showUpload"><ChatIcon name="plus" />上传资料</button>
    </header>
    <div class="library-tabs" role="tablist" aria-label="知识库功能">
      <button id="library-files-tab" role="tab" :aria-selected="tab === 'files'" aria-controls="library-files" :class="{ active: tab === 'files' }" @click="tab = 'files'"><ChatIcon name="file" />文件管理</button>
      <button id="library-search-tab" role="tab" :aria-selected="tab === 'search'" aria-controls="library-search" :class="{ active: tab === 'search' }" @click="tab = 'search'"><ChatIcon name="search" />检索测试</button>
    </div>
    <div id="library-search" v-show="tab === 'search'" role="tabpanel" aria-labelledby="library-search-tab"><DocumentSearch :document="detail" @open-document="preview($event, 'original')" /></div>
    <div id="library-files" v-show="tab === 'files'" role="tabpanel" aria-labelledby="library-files-tab">
      <DocumentUpload v-show="showUpload" :active="active" :disabled="changing || loading" @busy="uploading = $event" @uploaded="uploaded" @finished="refreshGroups()" @open-document="preview" />
      <a-alert v-if="error" type="error" class="document-feedback">{{ error }}</a-alert><a-alert v-if="notice" type="info" class="document-feedback">{{ notice }}</a-alert>
      <form class="document-filter" @submit.prevent="refreshGroups(true)"><label class="filename-search" for="document-name-query"><span>文件名</span><div><ChatIcon name="search" />
        <input id="document-name-query" v-model="fileQuery" maxlength="255" placeholder="文件名关键词" :disabled="changing || loading" />
        </div></label><DocumentMetadataFields v-model="filter" filtering compact :disabled="changing || loading" />
        <button class="secondary-button" :disabled="changing || loading">查找</button><button type="button" class="text-button" :disabled="changing || loading" @click="fileQuery = ''; filter = { city: null, category: null }; refreshGroups(true)">清空</button></form>
      <div class="library-body" :class="{ 'with-preview': detail || reading || deleting }">
      <div class="document-library">
      <p v-if="loading" role="status">正在读取城市列表…</p><div v-else-if="!groups.length && !error" class="document-empty"><ChatIcon name="books" /><p>{{ applied.q || applied.city || applied.category ? '没有符合条件的资料。' : '还没有资料，点击上传资料开始建立知识库。' }}</p></div>
      <div v-for="group in groups" :key="group.city ?? 'unclassified'" class="city-group">
        <button type="button" class="city-toggle" :disabled="changing" :aria-expanded="group.expanded" @click="expand(group)"><ChatIcon name="chevron" :class="{ collapsed: !group.expanded }" /><ChatIcon name="books" />{{ group.city || '未标注城市' }}<span>{{ group.total }} 份资料</span></button>
        <template v-if="group.expanded"><p v-if="group.busy" role="status">正在读取文件…</p><p v-if="group.error" role="alert">{{ group.error }} <a-button size="mini" @click="loadGroup(group)">重试加载</a-button></p>
          <div class="table-wrap"><table class="document-table"><thead><tr><th scope="col">文件名称</th><th scope="col">类别</th><th scope="col">读取状态</th><th scope="col">添加日期</th><th scope="col">操作</th></tr></thead>
            <tbody><tr v-for="document in group.files" :key="document.id" :class="{ selected: detail?.id === document.id }">
              <td><button class="document-filename" :disabled="changing" @click="preview(document.id)"><span class="file-icon"><ChatIcon name="file" /></span><span><strong>{{ document.file_name }}</strong><small>{{ fileSize(document.size_bytes) }} · {{ document.section_count }} 个原文单元</small></span></button></td>
              <td>{{ document.category || '未分类' }}</td><td><span class="document-status" :class="{ failed: document.status !== 'parsed' }">{{ document.status === 'parsed' ? '已读取' : document.status === 'deleting' ? '待清理' : '读取失败' }}</span></td>
              <td>{{ new Date(document.created_at).toLocaleDateString('zh-CN') }}</td><td><button class="text-button" :disabled="changing" @click="preview(document.id)">查看</button></td>
            </tr></tbody></table></div>
          <div class="group-footer"><span>已显示 {{ group.files.length }} / {{ group.total }} 份</span><button v-if="group.cursor" class="secondary-button" :disabled="group.busy || changing" @click="loadGroup(group)">加载更多文件</button></div></template>
      </div>
      <p v-if="!loading && !error && groups.length" class="library-total">{{ applied.q || applied.city || applied.category ? '当前筛选' : '共享资料库' }}共 {{ matchingTotal }} 份资料 · {{ groups.length }} 个城市分组</p>
      </div>
    <aside v-if="detail || reading || deleting" ref="previewElement" class="document-preview" aria-label="资料预览"><header class="preview-heading"><span><ChatIcon name="file" />资料预览</span><button class="close-preview" aria-label="关闭资料预览" :disabled="changing" @click="closePreview"><ChatIcon name="close" /></button></header>
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
    </aside>
    </div>
    </div>
  </section>
</template>

<style scoped>
.documents-workspace { width: 100%; max-width: 1500px; margin: 0 auto; color: #526b47; box-sizing: border-box; }
.library-heading { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 24px; }
.library-eyebrow { display: flex; align-items: center; gap: 7px; color: #789369; font-size: 11px; margin-bottom: 10px; }
.library-heading h1 { margin: 0 0 8px; font-size: 24px; font-weight: 550; color: #3f5f36; }
.library-heading p { font-size: 13px; margin: 0; color: #7b8e6e; line-height: 1.8; }
.library-tabs { display: flex; gap: 26px; border-bottom: 1px solid #e3eadd; margin-bottom: 24px; }
.library-tabs button { display: flex; align-items: center; gap: 7px; padding: 12px 2px; color: #7f9173; background: none; border: 0; border-bottom: 2px solid transparent; font: inherit; font-size: 13px; cursor: pointer; }
.library-tabs button.active { color: #42703b; border-color: #56894b; }
.chat-icon { width: 17px; height: 17px; }
.primary-button, .secondary-button, .text-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; padding: 9px 13px; border-radius: 6px; border: 1px solid #e0e7d9; background: #fff; color: #658451; font: inherit; font-size: 12px; cursor: pointer; white-space: nowrap; }
.primary-button { background: #397352; color: white; border-color: #397352; }
.text-button { border: 0; background: none; padding: 8px; }
button:disabled { opacity: .5; cursor: default; }
button:focus-visible, input:focus-visible { outline: 2px solid #7eaa6d; outline-offset: 3px; }
.library-body { display: grid; grid-template-columns: minmax(0, 1fr); gap: 24px; align-items: start; }
.library-body.with-preview { grid-template-columns: minmax(0, 1.2fr) minmax(360px, 1fr); }
.document-library { min-width: 0; }
.document-help { color: #718179; font-size: 13px; line-height: 1.8; }
.document-filter { display: flex; align-items: end; flex-wrap: wrap; gap: 10px; margin: 22px 0; }
.filename-search { display: grid; gap: 6px; color: #718663; font-size: 12px; }
.filename-search>div { display: flex; align-items: center; gap: 7px; padding: 0 10px; border: 1px solid #dfe7d8; border-radius: 6px; background: white; }
.filename-search input { min-width: 0; width: 180px; padding: 9px 0; font: inherit; border: 0; background: transparent; color: #526b47; }
.document-feedback { margin: 12px 0; }.city-group { border: 1px solid #e4eadd; border-radius: 9px; margin-bottom: 12px; overflow: hidden; }
.city-toggle { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; background: #fafcf7; border: 0; padding: 14px 16px; cursor: pointer; font: inherit; font-size: 13px; font-weight: 550; color: #527347; }
.city-toggle>span { margin-left: auto; font-size: 11px; color: #7e9170; font-weight: 400; }
.city-toggle .collapsed { transform: rotate(-90deg); }
.table-wrap { overflow-x: auto; }
.document-table { width: 100%; border-collapse: collapse; white-space: nowrap; text-align: left; font-size: 12px; }
.document-table th { padding: 13px 16px; color: #7f9173; font-size: 11px; font-weight: 400; border-block: 1px solid #e8ecdf; background: #fbfcf9; }
.document-table td { padding: 15px 16px; border-bottom: 1px solid #edf0e6; color: #768968; }
.document-table tr:last-child td { border-bottom: 0; }.document-table tr.selected { background: #f0f6e9; }.document-table tbody tr:hover { background: #f7faf2; }
.document-filename { display: flex; align-items: center; gap: 11px; width: 100%; padding: 0; text-align: left; border: 0; background: none; cursor: pointer; color: #526b47; font: inherit; }
.document-filename strong { display: block; max-width: 320px; white-space: normal; overflow-wrap: anywhere; font-size: 12px; font-weight: 550; }
.document-filename small { display: block; font-size: 10px; color: #829573; margin-top: 5px; }
.file-icon { display: grid; place-items: center; width: 30px; height: 35px; border-radius: 5px; background: #edf4e6; color: #7f9a65; flex-shrink: 0; }
.document-status { color: #61894d; font-size: 11px; }.document-status.failed { color: #a46745; }
.group-footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 16px; border-top: 1px solid #edf0e6; font-size: 11px; color: #809473; }
.library-total { font-size: 12px; color: #7c8f6f; margin-top: 20px; }
.document-preview { min-width: 0; border: 1px solid #e1e9d8; border-radius: 10px; padding: 18px; background: #fff; }.document-preview h2 { font-size: 19px; overflow-wrap: anywhere; }
.preview-heading { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: #668151; border-bottom: 1px solid #e8eddf; padding-bottom: 12px; }
.preview-heading>span { display: flex; gap: 7px; align-items: center; }.close-preview { display: grid; place-items: center; padding: 5px; border: 0; background: transparent; color: #7a9268; cursor: pointer; }
.document-empty { color: #819176; padding: 65px 20px; line-height: 1.8; text-align: center; font-size: 13px; }.document-empty>.chat-icon { width: 32px; height: 32px; color: #9db78d; }.document-sections { max-height: 65dvh; overflow-y: auto; }.document-section { border-top: 1px solid #e9efeb; padding: 18px 2px; }
.document-location { color: #286c54; font-size: 12px; overflow-wrap: anywhere; }.document-section pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.9; tab-size: 4; }
.document-pagination { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 12px 0; }
@media (max-width: 1100px) { .library-body.with-preview { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 600px) { .library-heading { gap: 10px; align-items: start; }.library-heading h1 { font-size: 22px; }.library-heading p { font-size: 12px; }.primary-button { padding: 9px; }.document-filter { gap: 10px; }.filename-search { width: 100%; }.filename-search input { width: 100%; }.document-table th, .document-table td { padding: 12px; }.document-filename strong { max-width: 180px; min-width: 140px; }.document-preview { padding: 14px; } }
</style>
