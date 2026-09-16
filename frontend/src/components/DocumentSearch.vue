<script setup lang="ts">
/** 页面组件层：展示当前资料索引进度，并搜索所有已建索引的资料。 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { buildIndexBatch, readIndex, searchDocuments, type DocumentMetadata, type IndexProgress, type SearchHit } from '../api/documents'
import DocumentMetadataFields from './DocumentMetadataFields.vue'

const props = defineProps<{ documentId?: string; active: boolean; progressVersion?: number }>()
defineEmits<{ openDocument: [id: string] }>()
const progress = ref<IndexProgress | null>(null)
const indexError = ref('')
const loading = ref(false)
const building = ref(false)
const query = ref('')
const metadata = ref<DocumentMetadata>({ city: null, source: null, review_status: null, poi_id: null })
const searching = ref(false)
const searched = ref(false)
const searchError = ref('')
const hits = ref<SearchHit[]>([])
let active = true
let generation = 0 // 资料切换后，旧请求只能在服务端完成，不能覆盖新资料进度。
let stopRequested = false

/** 暂停函数：让正在处理的一批完成，随后停止发送新的请求。 */
function pause(): void { stopRequested = true }

/** 进度刷新函数：保留服务端真实结果，失败时明确提示，不当作零进度。 */
async function refresh(): Promise<void> {
  if (!props.documentId || building.value) return
  const token = ++generation
  const id = props.documentId
  loading.value = true
  indexError.value = ''
  try {
    const result = await readIndex(id)
    if (active && token === generation) progress.value = result
  } catch (cause) {
    if (active && token === generation) indexError.value = cause instanceof Error ? cause.message : '进度读取失败。'
  } finally {
    if (active && token === generation) loading.value = false
  }
}

/** 连续建立函数：每批收到确认再发下一批，暂停、切换资料和关闭页面都会停止后续批次。 */
async function build(): Promise<void> {
  if (!props.active || !props.documentId || !progress.value?.total || loading.value || building.value) return
  const token = ++generation
  const id = props.documentId
  building.value = true
  stopRequested = false
  indexError.value = ''
  try {
    while (active && token === generation && !stopRequested && !progress.value?.complete) {
      const previous = progress.value?.indexed ?? 0
      const result = await buildIndexBatch(id)
      if (!active || token !== generation) return
      progress.value = result
      if (!result.complete && result.indexed <= previous) throw new Error('本批进度未确认，请刷新后继续。')
    }
  } catch (cause) {
    if (active && token === generation) indexError.value = cause instanceof Error ? cause.message : '建立索引失败，请刷新后继续。'
  } finally {
    if (active && token === generation) building.value = false
  }
}

/** 搜索函数：只展示原文；每次提交清空上次结果，避免误认。 */
async function search(): Promise<void> {
  if (!query.value.trim() || searching.value) return
  searching.value = true
  searched.value = false
  searchError.value = ''
  hits.value = []
  try {
    const result = await searchDocuments(query.value.trim(), metadata.value)
    if (active) { hits.value = result.items; searched.value = true }
  } catch (cause) {
    if (active) searchError.value = cause instanceof Error ? cause.message : '资料查询失败，请重试。'
  } finally {
    if (active) searching.value = false
  }
}

// 切换资料清空索引状态，但保留跨资料搜索结果，方便继续点开其他命中。
watch(() => [props.documentId, props.progressVersion], () => {
  ++generation
  stopRequested = true
  building.value = false
  progress.value = null
  loading.value = false
  indexError.value = ''
  void refresh()
}, { immediate: true })
onBeforeUnmount(() => { active = false; stopRequested = true; ++generation })
// v-show不会卸载组件，切回聊天也要停，重新打开后由用户手动继续。
watch(() => props.active, enabled => { if (!enabled) pause() })
</script>

<template>
  <section class="knowledge-search" aria-label="资料语义搜索">
    <h2>搜索旅行资料</h2>
    <p>用一句话查找相关原文。只搜索已经建立索引的部分，结果保留资料中的原始说法。</p>
    <div v-if="documentId" class="index-box">
      <strong>当前选中资料的搜索索引</strong>
      <p v-if="loading" role="status">正在读取进度…</p>
      <p v-else-if="progress" role="status">已完成 {{ progress.indexed }} / {{ progress.total }} 个片段<span v-if="progress.complete"> · 可以搜索</span></p>
      <p v-if="progress?.total === 0">请先点击“查看片段”，生成片段后再刷新进度。</p>
      <progress v-if="progress?.total" :value="progress.indexed" :max="progress.total" aria-label="索引进度" />
      <div class="actions">
        <button :disabled="loading || building" @click="refresh">刷新索引进度</button>
        <button :disabled="loading || building || !progress?.total || progress.complete" @click="build">{{ progress?.indexed ? '继续建立索引' : '建立搜索索引' }}</button>
        <button v-if="building" @click="pause">当前批次后暂停</button>
      </div>
      <small>建立索引会调用向量服务；暂停或离开页面后，已完成部分会保留。</small>
      <p v-if="indexError" role="alert" class="error">{{ indexError }}</p>
    </div>
    <p v-else>先在资料列表选择一份资料，可以为它建立搜索索引。</p>
    <form @submit.prevent="search()" class="query-form">
      <label for="knowledge-query">想从资料中查找什么？</label>
      <textarea id="knowledge-query" v-model="query" maxlength="800" rows="2" placeholder="例如：杭州有哪些适合散步的景点？" :disabled="searching" />
      <DocumentMetadataFields v-model="metadata" filtering :disabled="searching" />
      <small>筛选只影响本次资料搜索；留空不限，不改变聊天检索范围。</small>
      <button type="submit" :disabled="!query.trim() || searching">{{ searching ? '正在搜索…' : '搜索已建索引的资料' }}</button>
    </form>
    <p v-if="searchError" role="alert" class="error">{{ searchError }}</p>
    <p v-if="searched && !hits.length">没有符合条件的已索引片段，请检查筛选条件及资料索引进度。</p>
    <article v-for="hit in hits" :key="hit.chunk.id" class="hit">
      <button @click="$emit('openDocument', hit.chunk.document_id)">{{ hit.file_name }}</button>
      <p class="location">片段 {{ hit.chunk.order }} · 相似度 {{ hit.score.toFixed(3) }}<span v-if="hit.chunk.page_number"> · 第 {{ hit.chunk.page_number }} 页</span></p>
      <p v-if="hit.chunk.section_path.length" class="location">{{ hit.chunk.section_path.join(' / ') }}</p>
      <!-- 插值显示纯文本，资料中的脚本和HTML不会执行。 -->
      <pre>{{ hit.chunk.text }}</pre>
    </article>
    <small v-if="hits.length">相似度用于排序，不代表信息正确率；价格和开放时间等请核实最新信息。</small>
  </section>
</template>

<style scoped>
.knowledge-search { padding: 20px; border-radius: 12px; background: #fff; grid-column: 1 / -1; min-width: 0; }
h2 { margin-top: 0; font-size: 19px; }
p, small { line-height: 1.7; color: #63756b; }
.index-box { padding: 15px; background: #f3f7f4; border-radius: 8px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0; }
button { background: #286c54; color: white; border: 0; border-radius: 6px; padding: 9px 14px; font: inherit; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
button:focus-visible, textarea:focus-visible { outline: 3px solid #90b9a5; outline-offset: 2px; }
.query-form { display: grid; gap: 10px; margin-top: 18px; }
textarea { width: 100%; box-sizing: border-box; padding: 10px; font: inherit; border: 1px solid #bccbc2; border-radius: 6px; resize: vertical; }
.query-form button { justify-self: start; }
.error { color: #b33434; }
.hit { border-top: 1px solid #dfe8e2; padding: 16px 0; }
.hit button { background: none; color: #286c54; text-align: left; padding-left: 0; overflow-wrap: anywhere; }
.location { margin: 5px 0; font-size: 12px; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.8; }
progress { width: min(100%, 420px); accent-color: #286c54; }
</style>
