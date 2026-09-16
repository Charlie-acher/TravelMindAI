<script setup lang="ts">
/** 资料页面：管理选择、上传、列表和正文预览；切换页签不会重置旅行对话。 */
import { computed, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Card as ACard, Tag as ATag } from '@arco-design/web-vue'
import { deleteDocument, listDocuments, readDocument, retryDocument, type DocumentDetail, type DocumentMetadata, type DocumentSummary } from '../api/documents'
import DocumentMetadataEditor from './DocumentMetadataEditor.vue'
import DocumentMetadataFields from './DocumentMetadataFields.vue'
import DocumentChunks from './DocumentChunks.vue'
import DocumentJob from './DocumentJob.vue'
import DocumentSearch from './DocumentSearch.vue'
import DocumentUpload from './DocumentUpload.vue'

// App使用v-show保留页面；活动状态让检索组件在切回聊天时停止后续收费批次。
defineProps<{ active: boolean }>()

const documents = ref<DocumentSummary[]>([])
const detail = ref<DocumentDetail | null>(null)
const uploading = ref(false)
const loadingList = ref(false)
const reading = ref(false)
const deleting = ref(false)
const retrying = ref(false)
const savingMetadata = ref(false)
const changingDocument = computed(() => uploading.value || deleting.value || retrying.value || savingMetadata.value)
const deleteConfirmation = ref(false)
const libraryVersion = ref(0) // 删除时重建搜索组件，清除旧命中并停止后续索引批次。
const indexVersion = ref(0) // 后台完成后只刷新索引进度，保留用户已输入的搜索条件。
const error = ref('')
const notice = ref('')
const hasMore = ref(false)
const fileQuery = ref('')
const appliedQuery = ref('') // 加载下一页沿用已提交的条件，避免混入输入框里尚未提交的新词。
const metadataFilter = ref<DocumentMetadata>({ city: null, source: null, review_status: null, poi_id: null })
const appliedMetadata = ref<DocumentMetadata>({ ...metadataFilter.value })
const hasActiveFilters = computed(() => Boolean(appliedQuery.value) || Object.values(appliedMetadata.value).some(value => value?.trim()))
const sectionPage = ref(0)
const previewMode = ref<'original' | 'chunks'>('original') // 新选资料先看原文，再按需打开片段。
// 每次只渲染50个原文单元，长资料仍能翻页查看，避免一次创建数千DOM节点。
const visibleSections = computed(() => detail.value?.sections.slice(sectionPage.value * 50, (sectionPage.value + 1) * 50) ?? [])
const sectionPages = computed(() => Math.ceil((detail.value?.sections.length ?? 0) / 50))
// 编号只用于防止较慢的旧预览覆盖新选择，不是数据库里的document.id。
let previewRequest = 0

/** 列表刷新失败时保留原列表，并明确显示错误；不能把服务异常伪装成“暂无资料”。 */
async function refreshList(append = false, query = appliedQuery.value, metadata = appliedMetadata.value): Promise<void> {
  if (loadingList.value) return
  loadingList.value = true
  error.value = ''
  try {
    const selectedMetadata = { ...metadata }
    const page = await listDocuments(append ? documents.value.length : 0, query, selectedMetadata)
    documents.value = append ? [...documents.value, ...page] : page
    appliedQuery.value = query
    appliedMetadata.value = selectedMetadata
    hasMore.value = page.length === 50
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '资料列表读取失败，请重试。'
  } finally {
    loadingList.value = false
  }
}

/** 读取新资料前清空旧正文；连续点选时只接收最后一次请求的响应。 */
async function preview(id: string): Promise<void> {
  // 上传和重读时不接收跳转；删除失败后，remove仍需要调用这里恢复待清理详情。
  if (uploading.value || retrying.value || savingMetadata.value) return
  const request = ++previewRequest
  reading.value = true
  detail.value = null
  deleteConfirmation.value = false
  sectionPage.value = 0
  previewMode.value = 'original'
  error.value = ''
  notice.value = '' // 切换资料后清掉上一份的重试提示，避免被当成新资料的状态。
  try {
    const result = await readDocument(id)
    if (request === previewRequest) detail.value = result
  } catch (cause) {
    if (request === previewRequest) error.value = cause instanceof Error ? cause.message : '正文读取失败。'
  } finally {
    if (request === previewRequest) reading.value = false
  }
}

/** 上传状态函数：上传期间禁用冲突操作，并让之前尚未返回的预览失效。 */
function uploadBusy(value: boolean): void {
  uploading.value = value
  if (!value) return
  error.value = ''
  notice.value = ''
  ++previewRequest
  reading.value = false
  deleteConfirmation.value = false
}

/** 上传预览函数：一批结束后打开最后一份已保存资料，各文件的结果留在上传组件。 */
function uploaded(document: DocumentDetail): void {
  detail.value = document
  sectionPage.value = 0
  previewMode.value = 'original'
}

/** 标签保存回调：显示服务端结果，刷新当前筛选列表并清除旧搜索命中。 */
function metadataSaved(document: DocumentDetail): void {
  if (detail.value?.id === document.id) detail.value = document
  notice.value = '资料标签已保存，筛选立即生效，无需重建索引。'
  ++libraryVersion.value
  void refreshList()
}

/** 后台完成回调：只刷新仍选中的资料，不重建任务组件，避免重复完成通知。 */
async function jobCompleted(): Promise<void> {
  const id = detail.value?.id
  if (!id) return
  try {
    const result = await readDocument(id)
    if (detail.value?.id === id) {
      detail.value = result
      ++indexVersion.value
    }
    await refreshList()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务完成，但资料刷新失败，请重试。'
  }
}

/** 筛选清空函数：同步清空表单和已应用条件，重新读取全部资料。 */
function clearFilters(): void {
  fileQuery.value = ''
  metadataFilter.value = { city: null, source: null, review_status: null, poi_id: null }
  void refreshList(false, '', metadataFilter.value)
}

/** 删除函数：确认后清空旧预览和搜索；失败重新读取停用状态，让用户继续清理。 */
async function remove(): Promise<void> {
  if (!detail.value || changingDocument.value || loadingList.value) return
  const id = detail.value.id
  deleting.value = true
  deleteConfirmation.value = false
  error.value = ''
  notice.value = ''
  ++previewRequest
  detail.value = null
  ++libraryVersion.value
  try {
    await deleteDocument(id)
    await refreshList()
    notice.value = '资料已删除，后续检索不再使用它。已保存的历史聊天保持原样。'
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : '删除结果尚未确认，请刷新后重试。'
    await refreshList()
    // 列表可能带筛选条件，不能用“列表里没有”判断资料已删除；直接回读当前编号。
    await preview(id)
    error.value = message
  } finally {
    deleting.value = false
  }
}

/** 重新解析函数：保留当前失败资料，收到最新结果后更新正文；网络错误仍可再次尝试。 */
async function retry(): Promise<void> {
  if (!detail.value || detail.value.status !== 'failed' || changingDocument.value || loadingList.value) return
  const id = detail.value.id
  retrying.value = true
  deleteConfirmation.value = false
  error.value = ''
  notice.value = ''
  const request = ++previewRequest
  try {
    const result = await retryDocument(id)
    if (request !== previewRequest) return
    detail.value = result
    sectionPage.value = 0
    previewMode.value = 'original'
    notice.value = result.status === 'parsed'
      ? '重新解析成功，可以查看正文并生成片段。'
      : '仍未能读出正文，请查看原因；若原文件损坏或为扫描件，需要上传可读取的版本。'
    await refreshList()
  } catch (cause) {
    if (request === previewRequest) error.value = cause instanceof Error ? cause.message : '重新解析结果尚未确认，请重试。'
  } finally {
    retrying.value = false
  }
}

/** 人类可读的文件大小；原始size_bytes仍由后端按收到的真实字节计算。 */
function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

// 页面首次创建时恢复资料列表；资料属于本地资料库，不随“重新开始对话”清空。
onMounted(() => { void refreshList() })
</script>

<template>
  <section class="documents-workspace" aria-label="旅行资料库">
    <DocumentSearch :key="libraryVersion" :progress-version="indexVersion" :active="active && !changingDocument" :document-id="detail?.status === 'parsed' ? detail.id : undefined" @open-document="preview" />
    <a-card class="document-library" :bordered="false">
      <template #title>旅行资料</template>
      <template #extra><a-button :disabled="changingDocument || loadingList" @click="refreshList()">刷新列表</a-button></template>
      <p class="document-help">上传你的攻略，先看看系统读到了什么。</p>
      <DocumentUpload :active="active" :disabled="deleting || retrying || savingMetadata || loadingList"
        @busy="uploadBusy" @uploaded="uploaded" @finished="refreshList()" @open-document="preview" />
      <a-alert v-if="error" type="error" class="document-feedback">{{ error }}</a-alert>
      <a-alert v-if="notice" type="info" class="document-feedback">{{ notice }}</a-alert>
      <form class="document-filter" @submit.prevent="refreshList(false, fileQuery.trim(), metadataFilter)">
        <label for="document-name-query">按文件名查找资料</label>
        <input id="document-name-query" v-model="fileQuery" maxlength="255" placeholder="输入城市、景点或来源关键词"
          :disabled="changingDocument || loadingList" />
        <DocumentMetadataFields v-model="metadataFilter" filtering :disabled="changingDocument || loadingList" />
        <a-button html-type="submit" :disabled="changingDocument || loadingList">查找资料</a-button>
        <a-button :disabled="changingDocument || loadingList" @click="clearFilters">清空筛选</a-button>
      </form>
      <p v-if="appliedQuery" class="document-help">当前列表：文件名包含“{{ appliedQuery }}”。此条件不改变聊天检索范围。</p>
      <p v-if="Object.values(appliedMetadata).some(value => value?.trim())" class="document-help">已按提交的城市、来源、审核状态或POI编号精确筛选；这些条件不改变聊天检索范围。</p>
      <p v-if="loadingList" role="status">正在读取资料列表…</p>
      <p v-else-if="!documents.length && !error" class="document-empty">{{ hasActiveFilters ? '没有符合筛选条件的资料，请调整条件。' : '还没有资料，选择一份攻略开始吧。' }}</p>
      <ul class="document-list" aria-label="已保存资料">
        <li v-for="document in documents" :key="document.id">
          <button type="button" :class="{ selected: detail?.id === document.id }" :disabled="changingDocument" @click="preview(document.id)">
            <strong>{{ document.file_name }}</strong>
            <span>{{ fileSize(document.size_bytes) }} · {{ document.section_count }} 个原文单元</span>
            <span v-if="document.city || document.source">{{ document.city || '城市未标注' }} · {{ document.source || '来源未标注' }}</span>
            <a-tag :color="document.status === 'parsed' ? 'green' : 'red'">{{ document.status === 'deleting' ? '待清理' : document.status === 'parsed' ? '已读取' : '读取失败' }}</a-tag>
          </button>
        </li>
      </ul>
      <a-button v-if="hasMore" :disabled="loadingList || changingDocument" @click="refreshList(true)">加载更多资料</a-button>
      <p class="document-help">资料保存在本地资料库，刷新后仍可查看。当前为本地单用户演示。</p>
    </a-card>
    <a-card class="document-preview" :bordered="false">
      <template #title>资料预览</template>
      <p v-if="deleting" role="status">正在删除资料并清理索引…</p>
      <p v-else-if="reading" role="status">正在读取正文…</p>
      <template v-else-if="detail">
        <h2>{{ detail.file_name }}</h2>
        <p class="document-help">{{ fileSize(detail.size_bytes) }} · {{ detail.section_count }} 个原文单元 · {{ new Date(detail.created_at).toLocaleString('zh-CN') }}</p>
        <DocumentMetadataEditor :key="detail.id" :document="detail" :disabled="changingDocument || loadingList"
          @busy="savingMetadata = $event" @saved="metadataSaved" />
        <DocumentJob v-if="detail.status !== 'deleting'" :key="detail.id" :document-id="detail.id"
          :parsed="detail.status === 'parsed'" @completed="jobCompleted" />
        <a-button v-if="detail.status === 'failed'" :loading="retrying" :disabled="changingDocument || loadingList" @click="retry">
          {{ retrying ? '正在重新解析…' : '重新解析' }}
        </a-button>
        <a-button v-if="!deleteConfirmation" status="danger" :disabled="changingDocument || loadingList" @click="deleteConfirmation = true">
          {{ detail.status === 'deleting' ? '重试删除' : '删除资料' }}
        </a-button>
        <div v-else class="document-feedback" role="alert">
          <p>确认删除“{{ detail.file_name }}”？将清理原文件、正文和搜索索引，删除后无法恢复。历史聊天仍保留。</p>
          <div class="document-pagination">
            <a-button status="danger" :disabled="changingDocument || loadingList" @click="remove">确认删除</a-button>
            <a-button @click="deleteConfirmation = false">取消</a-button>
          </div>
        </div>
        <a-alert v-if="detail.error_message" type="error">{{ detail.error_message }}</a-alert>
        <a-alert v-for="warning in detail.warnings" :key="warning" type="warning" class="document-feedback">{{ warning }}</a-alert>
        <div v-if="detail.status === 'parsed'" class="document-pagination" aria-label="预览内容选择">
          <a-button :aria-pressed="previewMode === 'original'" @click="previewMode = 'original'">查看原文</a-button>
          <a-button :aria-pressed="previewMode === 'chunks'" @click="previewMode = 'chunks'">查看片段</a-button>
        </div>
        <!-- 只有选择片段时才查询；资料编号变化会重建组件，隔离不同资料的请求结果。 -->
        <DocumentChunks v-if="detail.status === 'parsed' && previewMode === 'chunks'" :key="detail.id" :document-id="detail.id" />
        <template v-if="detail.status === 'parsed' && previewMode === 'original'">
        <div v-if="sectionPages > 1" class="document-pagination">
          <a-button :disabled="sectionPage === 0" @click="sectionPage--">上一组正文</a-button>
          <span>第 {{ sectionPage + 1 }} / {{ sectionPages }} 组</span>
          <a-button :disabled="sectionPage + 1 >= sectionPages" @click="sectionPage++">下一组正文</a-button>
        </div>
        <div class="document-sections">
          <article v-for="section in visibleSections" :key="section.order" class="document-section">
            <div class="document-location">原文单元 {{ section.order }}<span v-if="section.page_number"> · 第 {{ section.page_number }} 页</span><span v-if="section.section_path.length"> · {{ section.section_path.join(' / ') }}</span></div>
            <!-- Vue插值按纯文本显示；上传的HTML/Markdown脚本不会在页面执行。 -->
            <pre>{{ section.text }}</pre>
          </article>
        </div>
        <p class="document-help">上传后请先核对正文，再生成片段、建立索引；已建立索引的内容可用于聊天检索。</p>
        </template>
      </template>
      <div v-else class="document-empty">选择左侧资料，在这里查看正文、页码和标题。</div>
    </a-card>
  </section>
</template>

<style scoped>
/* 双栏资料页沿用现有绿色和Arco卡片；窄屏变为上下两块，文件名和正文均可换行。 */
.documents-workspace { display: grid; grid-template-columns: minmax(280px, 0.85fr) minmax(0, 1.65fr); gap: 22px; align-items: start; }
.document-help { color: #718179; font-size: 13px; line-height: 1.8; }
.document-filter { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
.document-filter label { width: 100%; font-weight: 600; }
.document-filter input { min-width: 0; flex: 1 1 100%; padding: 9px; font: inherit; border: 1px solid #bccbc2; border-radius: 6px; }
.document-feedback { margin: 12px 0; }
.document-list { list-style: none; padding: 0; margin: 18px 0; max-height: 430px; overflow-y: auto; }
.document-list li { margin: 8px 0; }
.document-list button { display: flex; flex-wrap: wrap; gap: 8px; width: 100%; padding: 14px; border: 1px solid #e2eae4; border-radius: 9px; text-align: left; background: white; cursor: pointer; color: inherit; }
.document-list button.selected { border-color: #286c54; background: #f0f7f2; }
.document-list button:disabled { cursor: wait; opacity: .6; }
.document-list strong { width: 100%; overflow-wrap: anywhere; }
.document-list button > span { font-size: 12px; color: #718179; }
.document-preview { min-height: 500px; min-width: 0; }
.document-preview h2 { font-size: 19px; overflow-wrap: anywhere; }
.document-empty { color: #718179; padding: 32px 10px; line-height: 1.8; }
.document-sections { max-height: 65dvh; overflow-y: auto; }
.document-section { border-top: 1px solid #e9efeb; padding: 18px 2px; }
.document-location { color: #286c54; font-size: 12px; overflow-wrap: anywhere; }
.document-section pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.9; margin-bottom: 0; tab-size: 4; }
.document-pagination { display: flex; align-items: center; gap: 10px; margin: 12px 0; }
@media (max-width: 800px) { .documents-workspace { grid-template-columns: minmax(0, 1fr); } }
</style>
