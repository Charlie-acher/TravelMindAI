<script setup lang="ts">
/** 检索测试页面：提交问题与资料范围，展示后端返回的原文及混合排序分。 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { searchDocuments, type DocumentMetadata, type DocumentSearchResult, type DocumentSummary } from '../api/documents'
import ChatIcon from './ChatIcon.vue'
import DocumentMetadataFields from './DocumentMetadataFields.vue'

const props = defineProps<{ document: DocumentSummary | null }>()
const emit = defineEmits<{ openDocument: [id: string] }>()
const query = ref('')
const limit = ref(5)
const metadata = ref<DocumentMetadata>({ city: null, category: null })
const onlySelected = ref(false)
const searching = ref(false)
const error = ref('')
const result = ref<DocumentSearchResult | null>(null)
const submittedQuery = ref('')
let alive = true

// 删除或切换资料后重置范围，避免保留已经不可用的文件编号。
watch(() => props.document?.id, () => { onlySelected.value = false })

/** 查询函数：先清空旧结果；无命中、读取失败、尚未查询分别显示。 */
async function search(): Promise<void> {
  if (searching.value || !query.value.trim()) return
  searching.value = true; error.value = ''; result.value = null
  submittedQuery.value = query.value.trim()
  try {
    const response = await searchDocuments({
      query: submittedQuery.value, limit: limit.value,
      document_id: onlySelected.value ? props.document?.id ?? null : null,
      city: metadata.value.city?.trim() || null, category: metadata.value.category,
    })
    if (alive) result.value = response
  } catch (cause) {
    if (alive) error.value = cause instanceof Error ? cause.message : '检索失败，请重试。'
  } finally { if (alive) searching.value = false }
}
onBeforeUnmount(() => { alive = false })
</script>

<template>
  <section class="retrieval-test" aria-label="知识库检索测试">
    <form @submit.prevent="search">
      <div class="search-box">
        <label for="document-search-query">输入想查找的问题</label>
        <textarea id="document-search-query" v-model="query" maxlength="800" required :disabled="searching"
          placeholder="例如：苏州有哪些适合慢慢逛的园林？" />
        <div class="search-box-bottom"><span>检索共享资料中的原文片段，核对回答的依据。</span>
          <button class="search-submit" :disabled="searching || !query.trim()"><ChatIcon name="search" />{{ searching ? '正在检索…' : '开始检索' }}</button>
        </div>
      </div>
      <div class="search-filters">
        <DocumentMetadataFields v-model="metadata" filtering compact :disabled="searching" />
        <label>返回条数<select v-model="limit" :disabled="searching"><option v-for="count in 10" :key="count" :value="count">{{ count }} 条</option></select></label>
        <label class="scope-field">资料范围<select v-model="onlySelected" :disabled="searching">
          <option :value="false">全部共享资料</option>
          <option v-if="document" :value="true">{{ document.file_name }}</option>
        </select></label>
      </div>
      <p v-if="!document" class="search-note">需要限定某份资料时，先在文件管理中打开该文件。</p>
    </form>
    <p v-if="error" class="search-error" role="alert">{{ error }}</p>
    <div v-if="searching" class="search-empty" role="status"><ChatIcon name="search" /><p>正在查找相关原文…</p></div>
    <template v-else-if="result">
      <div class="results-heading"><span>本次返回 <b>{{ result.items.length }}</b> 个片段</span><span>混合排序分越大，排序越靠前</span></div>
      <p class="search-note">检索问题：{{ submittedQuery }}</p>
      <div v-if="!result.items.length" class="search-empty" role="status"><ChatIcon name="search" /><p>没有找到符合条件的片段。试试调整问题或筛选范围。</p></div>
      <article v-for="(hit, index) in result.items" :key="hit.chunk.id" class="search-hit">
        <div class="hit-heading"><b>{{ String(index + 1).padStart(2, '0') }}</b><span>混合排序分 {{ hit.score.toFixed(4) }}</span></div>
        <h3>{{ hit.chunk.section_path.at(-1) || hit.file_name }}</h3>
        <p class="hit-text">{{ hit.chunk.text }}</p>
        <footer><span><ChatIcon name="file" />{{ hit.file_name }}</span><span>片段 {{ hit.chunk.order }}</span><span v-if="hit.chunk.page_number">第 {{ hit.chunk.page_number }} 页</span>
          <button type="button" @click="emit('openDocument', hit.chunk.document_id)">查看原文<ChatIcon name="chevron" /></button>
        </footer>
      </article>
      <p v-if="result.items.length" class="search-note">混合排序分用于片段排序，不代表相似度或内容正确率。</p>
    </template>
    <div v-else-if="!error" class="search-empty"><ChatIcon name="search" /><p>输入一个问题，查看知识库能找到哪些原文。</p></div>
  </section>
</template>

<style scoped>
.retrieval-test { max-width: 1080px; }
.search-box { border: 1px solid #dce6d4; border-radius: 10px; padding: 20px; background: #fff; }
.search-box>label { display: block; margin-bottom: 10px; font-size: 13px; font-weight: 600; color: #526e46; }
textarea { width: 100%; min-height: 80px; border: 0; resize: vertical; font: inherit; line-height: 1.8; background: transparent; color: #43583d; box-sizing: border-box; }
textarea::placeholder { color: #899780; }
.search-box-bottom { display: flex; justify-content: space-between; gap: 12px; align-items: center; font-size: 12px; color: #7b8d6e; }
button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; cursor: pointer; font: inherit; }
.search-submit { padding: 10px 14px; background: #397352; color: white; border: 0; border-radius: 7px; flex-shrink: 0; }
button:disabled { opacity: .5; cursor: default; }
.chat-icon { width: 16px; height: 16px; }
.search-filters { display: flex; align-items: end; flex-wrap: wrap; gap: 12px; margin: 18px 0 10px; }
.search-filters>label { display: grid; gap: 6px; font-size: 12px; color: #718663; }
select { max-width: 300px; min-width: 90px; border: 1px solid #dfe7d8; background: #fff; border-radius: 6px; padding: 9px; color: #526b47; font: inherit; }
.search-note { font-size: 12px; color: #7b8d6e; line-height: 1.8; overflow-wrap: anywhere; }
.results-heading { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px; border-top: 1px solid #e5ebdf; padding-top: 22px; margin-top: 26px; font-size: 12px; color: #718663; }
.results-heading b { color: #477142; }
.search-hit { border: 1px solid #e1e9d8; border-radius: 9px; margin: 12px 0; padding: 19px 20px; }
.hit-heading { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.hit-heading b { font-size: 13px; color: #668651; }
.hit-heading>span { font-size: 11px; padding: 4px 7px; background: #f1f5eb; border-radius: 4px; color: #668151; }
h3 { font-size: 15px; font-weight: 550; color: #4d693e; margin: 0 0 9px; overflow-wrap: anywhere; }
.hit-text { white-space: pre-wrap; overflow-wrap: anywhere; color: #607651; font-size: 13px; line-height: 1.9; }
footer { border-top: 1px solid #edf1e6; padding-top: 12px; display: flex; gap: 12px; align-items: center; font-size: 11px; color: #778d68; flex-wrap: wrap; }
footer>span { display: inline-flex; align-items: center; gap: 6px; overflow-wrap: anywhere; }
footer button { margin-left: auto; border: 0; background: transparent; color: #568047; }
footer button .chat-icon { transform: rotate(-90deg); }
.search-empty { padding: 65px 20px; text-align: center; color: #819176; font-size: 13px; line-height: 1.9; }
.search-empty>.chat-icon { width: 32px; height: 32px; color: #9db78d; }
.search-error { color: #a13b3b; padding: 12px; background: #fff4f3; border-radius: 7px; }
:is(button, textarea, select):focus-visible { outline: 2px solid #7eaa6d; outline-offset: 3px; }
@media (max-width: 600px) { .search-box { padding: 15px; }.search-box-bottom { align-items: flex-end; }.search-box-bottom>span { max-width: 60%; }.search-filters { gap: 10px; }.scope-field { max-width: 100%; }.scope-field select { max-width: 100%; }.search-hit { padding: 15px; } }
</style>
