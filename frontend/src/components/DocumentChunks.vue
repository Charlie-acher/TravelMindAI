<script setup lang="ts">
/** 前端展示层：查询、生成和分页展示资料片段，由资料预览页传入资料编号。 */
import { computed, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton } from '@arco-design/web-vue'
import { generateDocumentChunks, listDocumentChunks, type DocumentChunkPage } from '../api/documents'

const props = defineProps<{ documentId: string }>()
const result = ref<DocumentChunkPage | null>(null) // null表示尚未成功读取，不能当成没有片段。
const page = ref(0)
const loading = ref(false)
const error = ref('')
const pages = computed(() => Math.ceil((result.value?.total ?? 0) / 50))

/** 片段加载函数：生成时回到首页，翻页成功后才更新页码，失败时保留原内容。 */
async function load(targetPage = 0, generate = false): Promise<void> {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    result.value = generate
      ? await generateDocumentChunks(props.documentId)
      : await listDocumentChunks(props.documentId, targetPage * 50)
    page.value = targetPage
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '片段读取失败，请重试。'
  } finally {
    loading.value = false
  }
}

// 父页面按资料编号重建本组件，较慢的旧请求只能更新旧组件，不会覆盖新资料。
onMounted(() => { void load() })
</script>

<template>
  <section aria-label="资料片段">
    <p class="chunk-help">每段最多 800 个字符，同一原文单元的相邻长片段重叠 100 个字符，方便保留上下文。</p>
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <a-button v-if="error" :disabled="loading" @click="load(page)">重新读取片段</a-button>
    <p v-if="loading" role="status">正在处理片段…</p>
    <template v-if="result?.total === 0">
      <p>这份资料还没有片段，点击后生成并保存，刷新后仍可查看。</p>
      <a-button type="primary" :loading="loading" :disabled="loading" @click="load(0, true)">生成片段</a-button>
    </template>
    <template v-else-if="result">
      <p>共 {{ result.total }} 个片段 · 已保存</p>
      <div v-if="pages > 1" class="chunk-pagination">
        <a-button :disabled="loading || page === 0" @click="load(page - 1)">上一页片段</a-button>
        <span>第 {{ page + 1 }} / {{ pages }} 页</span>
        <a-button :disabled="loading || page + 1 >= pages" @click="load(page + 1)">下一页片段</a-button>
      </div>
      <div class="chunk-list">
        <article v-for="chunk in result.items" :key="chunk.id" class="chunk-item">
          <div class="chunk-location">
            片段 {{ chunk.order }} · 原文单元 {{ chunk.section_order }}
            <span v-if="chunk.page_number"> · 第 {{ chunk.page_number }} 页</span>
            <span v-if="chunk.section_path.length"> · {{ chunk.section_path.join(' / ') }}</span>
            · 第 {{ chunk.start_char + 1 }}–{{ chunk.end_char }} 个字符
          </div>
          <!-- 纯文本展示资料，文件里的HTML不会作为网页执行。 -->
          <pre>{{ chunk.text }}</pre>
        </article>
      </div>
    </template>
    <p class="chunk-help">片段建立索引后，可用于原文搜索和聊天中的证据回答。</p>
  </section>
</template>

<style scoped>
/* 展示层样式：沿用资料页配色，长文字可换行，窄屏翻页按钮可折行。 */
.chunk-help { color: #718179; font-size: 13px; line-height: 1.8; }
.chunk-pagination { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 12px 0; }
.chunk-list { max-height: 65dvh; overflow-y: auto; }
.chunk-item { border-top: 1px solid #e9efeb; padding: 18px 2px; }
.chunk-location { color: #286c54; font-size: 12px; overflow-wrap: anywhere; }
.chunk-item pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.9; margin-bottom: 0; tab-size: 4; }
</style>
