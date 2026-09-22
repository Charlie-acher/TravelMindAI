<script setup lang="ts">
/** 个人文件展示层：个人中心查看全部会话，工作台侧栏只传入当前会话编号。 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { getPersonalFile, listPersonalFiles, personalFileDownloadUrl } from '../api/personalFiles'
import type { PersonalFileDetail, PersonalFileItem, PersonalFileKind } from '../api/personalFiles'
import ChatIcon from './ChatIcon.vue'
import PersonalFilePreview from './PersonalFilePreview.vue'

const props = defineProps<{ sessionId?: string | null; refreshKey?: number }>()
const emit = defineEmits<{ openSession: [id: string] }>()
const items = ref<PersonalFileItem[]>([])
const total = ref(0)
const offset = ref(0)
const limit = 20
const query = ref('')
const kind = ref<PersonalFileKind | ''>('')
const loading = ref(false)
const error = ref('')
const detail = ref<PersonalFileDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const dialog = ref<HTMLDialogElement | null>(null)
const expanded = ref(false)
const selectedItem = ref<PersonalFileItem | null>(null)
let listRequest = 0
let detailRequest = 0

/** 列表加载函数：序号隔离旧请求，切换会话后旧响应不能覆盖当前列表。 */
async function load(start = 0): Promise<void> {
  const request = ++listRequest
  loading.value = true
  error.value = ''
  offset.value = start
  items.value = []
  try {
    const page = await listPersonalFiles({ sessionId: props.sessionId, kind: kind.value, q: query.value, offset: start, limit })
    if (request !== listRequest) return
    items.value = page.items
    total.value = page.total
  } catch (cause) {
    if (request === listRequest) error.value = cause instanceof Error ? cause.message : '文件加载失败，请重试'
  } finally {
    if (request === listRequest) loading.value = false
  }
}

/** 详情加载函数：打开原生对话框，版本切换仍重新查询服务器的归属检查。 */
async function openDetail(item: PersonalFileItem, showExpanded = !props.sessionId): Promise<void> {
  const request = ++detailRequest
  selectedItem.value = item
  expanded.value = showExpanded
  detail.value = null
  detailLoading.value = true
  detailError.value = ''
  await nextTick()
  if (request !== detailRequest) return
  if (showExpanded && !dialog.value?.open) dialog.value?.showModal()
  try {
    const result = await getPersonalFile(item)
    if (request === detailRequest) detail.value = result
  } catch (cause) {
    if (request === detailRequest) detailError.value = cause instanceof Error ? cause.message : '文件详情加载失败'
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}

/** 关闭函数：使仍在途中返回的详情失效，不保留已关闭文件的内容。 */
function closeDetail(): void {
  detailRequest++
  dialog.value?.close()
  detail.value = null
  detailLoading.value = false
  selectedItem.value = null
  expanded.value = false
}

/** 放大函数：保留已经加载的快照，宽屏阅读仍使用同一原件和版本。 */
async function expandDetail(): Promise<void> {
  expanded.value = true
  await nextTick()
  if (expanded.value && selectedItem.value) dialog.value?.showModal()
}

/** 收起函数：会话面板回到页内预览，个人空间关闭详情。 */
function collapseDetail(): void {
  if (!props.sessionId) { closeDetail(); return }
  dialog.value?.close()
  expanded.value = false
}

/** 类型切换函数：每次切换从第一页读取服务器总数。 */
function changeKind(value: PersonalFileKind | ''): void { kind.value = value; closeDetail(); void load() }

/** 来源跳转函数：关闭详情后由上层恢复对应会话，保留统一的切换保护。 */
function openSource(id: string): void {
  closeDetail()
  emit('openSession', id)
}

/** 文件大小函数：行程尚未生成下载文件时显示类型，不虚构文件大小。 */
function sizeLabel(item: PersonalFileItem): string {
  if (item.size_bytes === null) return 'Markdown'
  return item.size_bytes >= 1_000_000 ? `${(item.size_bytes / 1_000_000).toFixed(1)} MB`
    : `${Math.max(1, Math.ceil(item.size_bytes / 1000))} KB`
}

/** 日期展示函数：使用当前浏览器时区展示真实保存时间。 */
function dateLabel(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

watch(() => [props.sessionId, props.refreshKey], () => {
  closeDetail()
  total.value = 0
  void load()
}, { immediate: true })
onBeforeUnmount(() => { listRequest++; closeDetail() })
</script>

<template>
  <section class="personal-files" :class="{ 'personal-files-space': !sessionId, 'personal-files-session': !!sessionId }" :aria-label="sessionId ? '本次旅行文件' : '我的文件'">
    <div class="files-heading"><div><p v-if="!sessionId" class="files-eyebrow">PERSONAL SPACE</p><h2><ChatIcon :name="sessionId ? 'folder' : 'drive'" />{{ sessionId ? '本次旅行文件' : '我的文件' }}</h2><p>{{ sessionId ? '附件与行程，随时回看' : '收好每一份旅行灵感与行程，下一次出发时随时找到。' }}</p></div><span class="file-total">{{ total }} 个文件</span></div>
    <div class="file-toolbar"><div class="file-tabs" aria-label="文件类型"><button :class="{ active: kind === '' }" :aria-pressed="kind === ''" @click="changeKind('')"><ChatIcon name="layers" />全部文件</button><button :class="{ active: kind === 'attachment' }" :aria-pressed="kind === 'attachment'" @click="changeKind('attachment')"><ChatIcon name="upload" />上传附件</button><button :class="{ active: kind === 'itinerary' }" :aria-pressed="kind === 'itinerary'" @click="changeKind('itinerary')"><ChatIcon name="map" />行程文件</button></div>
      <form class="file-filters" @submit.prevent="load()"><span class="file-search"><ChatIcon name="search" /><input v-model="query" aria-label="搜索文件" maxlength="200" placeholder="搜索文件或旅行标题" type="search" /></span><button class="file-button" type="submit" :disabled="loading">搜索</button></form>
    </div>
    <p v-if="loading" class="file-empty" role="status">正在读取文件…</p>
    <div v-else-if="error" class="file-empty" role="alert"><p>{{ error }}</p><button class="file-button" @click="load(offset)">重试</button></div>
    <div v-else-if="!items.length" class="file-empty"><ChatIcon name="folder" /><strong>{{ query || kind ? '没有匹配的文件' : '还没有文件' }}</strong><p>{{ query || kind ? '换个关键词或文件类型试试。' : '上传的附件和保存的逐日行程会显示在这里。' }}</p></div>
    <div v-else-if="!sessionId" class="file-table-wrap"><table class="file-table"><thead><tr><th>文件名称</th><th>类型</th><th>来源会话</th><th>保存时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in items" :key="`${item.kind}:${item.id}`"><td><button class="file-main" @click="openDetail(item)"><span class="file-badge"><ChatIcon :name="item.kind === 'itinerary' ? 'map' : 'file'" /></span><span class="file-name"><strong>{{ item.file_name }}</strong><small>{{ sizeLabel(item) }}<template v-if="item.version_count"> · {{ item.version_count }} 个版本</template></small></span></button></td><td><span class="type-pill" :class="{ itinerary: item.kind === 'itinerary' }">{{ item.kind === 'itinerary' ? '行程' : '附件' }}</span></td><td><button class="file-source" :title="item.session_title" @click="openSource(item.session_id)">{{ item.session_title }} ↗</button></td><td class="file-time">{{ dateLabel(item.created_at) }}</td><td><div class="table-actions"><button class="file-button" @click="openDetail(item)">预览</button><a :href="personalFileDownloadUrl(item)" :aria-label="`下载 ${item.file_name}`"><ChatIcon name="download" /></a></div></td></tr></tbody></table></div>
    <ul v-else class="file-list"><li v-for="item in items" :key="`${item.kind}:${item.id}`" class="file-row" :class="{ selected: selectedItem?.id === item.id }"><button class="file-main" @click="openDetail(item)"><span class="file-badge"><ChatIcon :name="item.kind === 'itinerary' ? 'map' : 'file'" /></span><span class="file-name"><strong>{{ item.file_name }}</strong><small>{{ sizeLabel(item) }}<template v-if="item.version_count"> · {{ item.version_count }} 个版本</template></small></span></button><div class="file-actions"><span class="file-time">{{ dateLabel(item.created_at) }}</span><a :href="personalFileDownloadUrl(item)" :aria-label="`下载 ${item.file_name}`"><ChatIcon name="download" />下载</a></div></li></ul>
    <nav v-if="total > limit" class="file-pages" aria-label="文件分页"><button class="file-button" :disabled="loading || offset === 0" @click="load(Math.max(0, offset - limit))">上一页</button><span>{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(total / limit) }}</span><button class="file-button" :disabled="loading || offset + limit >= total" @click="load(offset + limit)">下一页</button></nav>
    <section v-if="sessionId && selectedItem && !expanded" class="inline-file-detail" aria-label="文件预览"><header class="inline-preview-heading"><h3>{{ selectedItem.file_name }}</h3><div><button class="file-icon-button" aria-label="放大文件预览" title="放大预览" @click="expandDetail"><ChatIcon name="expand" /></button><button class="file-icon-button" aria-label="关闭文件预览" @click="closeDetail"><ChatIcon name="close" /></button></div></header><p v-if="detailLoading" role="status">正在读取详情…</p><p v-else-if="detailError" role="alert">{{ detailError }}</p><template v-else-if="detail"><div v-if="detail.versions.length" class="file-versions" aria-label="行程历史版本"><button v-for="version in detail.versions" :key="version.id" class="file-button" :class="{ selected: version.id === detail.item.id }" :aria-pressed="version.id === detail.item.id" @click="openDetail(version, false)">v{{ version.version }}</button></div><PersonalFilePreview :detail="detail" /></template></section>
    <dialog ref="dialog" class="file-dialog" aria-labelledby="file-detail-title" @cancel.prevent="collapseDetail()"><header class="file-detail-header"><h2 id="file-detail-title">{{ selectedItem?.file_name || '文件详情' }}</h2><button class="file-button" aria-label="关闭文件详情" @click="collapseDetail()">{{ sessionId ? '收起' : '关闭' }}</button></header><p v-if="detailLoading" role="status">正在读取详情…</p><p v-else-if="detailError" role="alert">{{ detailError }}</p><template v-else-if="detail && expanded"><div class="detail-meta"><button class="file-source" @click="openSource(detail.item.session_id)">来源：{{ detail.item.session_title }}</button><span>{{ dateLabel(detail.item.created_at) }}</span><a :href="personalFileDownloadUrl(detail.item)"><ChatIcon name="download" />下载{{ detail.itinerary ? ' Markdown' : '原件' }}</a></div><div v-if="detail.versions.length" class="file-versions" aria-label="行程历史版本"><button v-for="version in detail.versions" :key="version.id" class="file-button" :class="{ selected: version.id === detail.item.id }" :aria-pressed="version.id === detail.item.id" @click="openDetail(version, true)">版本 {{ version.version }}</button></div><PersonalFilePreview :detail="detail" /></template></dialog>
  </section>
</template>

<style scoped>
.personal-files { padding: 24px; min-width: 0; color: #29453b; }.personal-files-space { max-width: 1240px; margin: 0 auto; padding: 38px 40px; }
.files-heading,.file-detail-header,.detail-meta,.file-actions,.file-pages,.table-actions { display: flex; align-items: center; gap: 12px; }.files-heading,.file-detail-header { justify-content: space-between; }.files-heading h2 { display: flex; align-items: center; gap: 10px; font-size: 20px; margin: 0; }.personal-files-space .files-heading h2 { font-size: 28px; letter-spacing: -.6px; }.personal-files-space .files-heading h2 svg { width: 28px; height: 28px; }.files-heading p { margin: 8px 0 0; color: #7a8c84; font-size: 12px; }.files-heading .files-eyebrow { font-size: 9px; letter-spacing: 2px; color: #96a78f; margin: 0 0 12px; }.file-total { flex-shrink: 0; color: #668071; font-size: 11px; background: #edf4ec; padding: 8px 11px; border-radius: 20px; }
.file-toolbar { margin: 25px 0 18px; }.file-tabs { display: flex; align-items: center; gap: 5px; border-bottom: 1px solid #e5ece4; padding-bottom: 10px; }.file-tabs button { display: flex; gap: 6px; align-items: center; border: 0; border-radius: 8px; background: none; padding: 9px 11px; color: #819180; font: inherit; font-size: 12px; cursor: pointer; white-space: nowrap; }.file-tabs button svg { width: 15px; height: 15px; }.file-tabs button.active { color: #356e4c; background: #e9f1e6; font-weight: 600; }.file-filters { display: flex; gap: 8px; margin-top: 15px; }.file-search { display: flex; align-items: center; gap: 9px; flex: 1; min-width: 0; padding: 0 11px; background: #fff; border: 1px solid #e0e8dc; border-radius: 9px; color: #8ba083; }.file-search svg { width: 15px; height: 15px; }.file-search input { width: 100%; min-width: 0; border: 0; padding: 10px 0; color: #3c5d45; background: transparent; font: inherit; font-size: 12px; outline: none; }.file-search:focus-within { outline: 2px solid #759c71; outline-offset: 1px; }.file-button { border: 1px solid #dce7df; background: #fff; border-radius: 8px; color: #355c48; font: inherit; font-size: 11px; padding: 8px 10px; cursor: pointer; }.file-button:hover { background: #f1f7f2; }.file-button:disabled { opacity: .45; cursor: default; }.file-button.selected { background: #e5f1e8; color: #21644b; border-color: #8fbea0; }
.file-table-wrap { overflow-x: auto; border: 1px solid #e1e9de; border-radius: 14px; background: #fff; }.file-table { width: 100%; border-collapse: collapse; text-align: left; }.file-table th { background: #f6f8f3; color: #879480; font-weight: 500; font-size: 11px; padding: 14px 18px; white-space: nowrap; }.file-table td { padding: 18px; border-top: 1px solid #edf0e8; font-size: 12px; }.file-table tr:hover td { background: #fbfcf9; }.file-table th:first-child { width: 39%; }.file-table .file-source { max-width: 180px; }.file-table .file-time { white-space: nowrap; }.type-pill { display: inline-block; color: #7b8e87; background: #f0f4f2; padding: 5px 8px; border-radius: 6px; font-size: 10px; white-space: nowrap; }.type-pill.itinerary { color: #668445; background: #eff4e5; }.table-actions { gap: 11px; }.table-actions a svg { width: 15px; height: 15px; }
.file-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 9px; }.file-row { padding: 14px; border: 1px solid #e4eade; border-radius: 11px; background: #fff; }.file-row.selected { border-color: #96b88d; background: #f5f8f0; }.file-main { display: flex; gap: 12px; align-items: center; width: 100%; padding: 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }.file-badge { flex-shrink: 0; display: grid; place-items: center; width: 38px; height: 43px; border: 1px solid #dfe8d9; border-radius: 9px; background: #f0f5e9; color: #7c9a65; }.file-badge svg { width: 20px; height: 20px; }.file-name { min-width: 0; }.file-name strong { display: block; overflow-wrap: anywhere; font-size: 12px; font-weight: 550; line-height: 1.55; }.file-name small { display: block; margin-top: 5px; color: #9aa696; font-size: 10px; line-height: 1.6; }.file-actions { justify-content: space-between; margin: 10px 0 0 50px; }.file-actions a,.detail-meta a { display: inline-flex; align-items: center; gap: 5px; }.file-actions svg,.detail-meta a svg { width: 13px; height: 13px; }.file-time { color: #9ba596; font-size: 10px; }.file-source { min-width: 0; max-width: 75%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0; border: 0; background: none; color: #6e8979; font: inherit; font-size: 11px; cursor: pointer; }.file-source:hover { color: #0f766e; text-decoration: underline; }a { color: #28735a; text-decoration: none; font-size: 11px; }a:hover { text-decoration: underline; }.file-empty { padding: 60px 10px; text-align: center; color: #8a9885; font-size: 12px; line-height: 1.8; }.file-empty > svg { display: block; width: 37px; height: 37px; margin: 0 auto 14px; color: #b7c5aa; }.file-empty strong { display: block; color: #71836b; font-size: 14px; font-weight: 500; }.file-pages { justify-content: center; margin-top: 22px; font-size: 12px; }
.inline-file-detail { margin-top: 24px; border-top: 1px solid #dfe8d9; padding-top: 18px; }.inline-preview-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }.inline-preview-heading h3 { font-size: 12px; font-weight: 550; overflow-wrap: anywhere; margin: 7px 0; }.inline-preview-heading > div { display: flex; flex-shrink: 0; gap: 2px; }.file-icon-button { display: grid; place-items: center; border: 0; background: none; border-radius: 6px; width: 27px; height: 28px; color: #7e9473; cursor: pointer; }.file-icon-button:hover { background: #eaf1e3; }.file-icon-button svg { width: 15px; height: 15px; }
.file-dialog { width: min(960px, calc(100vw - 32px)); max-height: calc(100dvh - 40px); padding: 26px; border: 1px solid #dce7df; border-radius: 18px; color: #29453b; box-shadow: 0 20px 80px #183f3529; }.file-dialog::backdrop { background: #19372d55; backdrop-filter: blur(3px); }.file-detail-header { align-items: flex-start; margin-bottom: 15px; }.file-detail-header h2 { font-size: 19px; margin: 5px 0; overflow-wrap: anywhere; }.file-detail-header button { flex-shrink: 0; }.detail-meta { flex-wrap: wrap; font-size: 11px; color: #809088; padding-bottom: 18px; border-bottom: 1px solid #e9efea; }.detail-meta .file-source { max-width: 100%; }.file-versions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 15px; }
.personal-files-session :deep(.itinerary-card .plan-header) { padding: 16px 12px; }.personal-files-session :deep(.itinerary-card .journey) { font-size: 17px; }.personal-files-session :deep(.itinerary-card .header-top) { flex-wrap: wrap; }.personal-files-session :deep(.itinerary-card .plan-grid) { padding: 0; }.personal-files-session :deep(.itinerary-card .trip-facts) { gap: 7px; }.personal-files-session :deep(.itinerary-card .plan-day) { padding: 14px 10px; }
@media (min-width: 1100px) { .personal-files-space .file-toolbar { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e5ece4; padding-bottom: 14px; margin-top: 32px; }.personal-files-space .file-tabs { border-bottom: 0; padding-bottom: 0; }.personal-files-space .file-filters { margin: 0; width: 340px; } }
@media (max-width: 600px) { .personal-files,.personal-files-space { padding: 18px; }.file-dialog { padding: 19px; }.personal-files-space .files-heading h2 { font-size: 23px; }.file-total { font-size: 10px; }.file-tabs button { padding: 8px; font-size: 11px; }.file-tabs button svg { width: 13px; height: 13px; }.file-table { min-width: 660px; }.file-table td,.file-table th { padding: 13px; } }
</style>
