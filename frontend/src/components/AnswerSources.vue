<script setup lang="ts">
/** 资料展示层：展开本轮实际提供给回答的参考原文，不把模型常识标为逐句引用。 */
import type { AnswerResult } from '../api/documents'
import { computed } from 'vue'

const props = defineProps<{ knowledge: AnswerResult }>()
// 按真实文件编号合并，只改变显示方式，片段编号与页码仍各自保留。
const documents = computed(() => {
  const groups = new Map<string, { name: string; sources: AnswerResult['sources'] }>()
  for (const source of props.knowledge.sources) {
    const key = source.hit.chunk.document_id ?? source.hit.file_name
    if (!groups.has(key)) groups.set(key, { name: source.hit.file_name, sources: [] })
    groups.get(key)!.sources.push(source)
  }
  return [...groups.entries()].map(([id, group]) => ({ id, ...group }))
})

/** 来源链接函数：旧历史也只允许打开HTTP网页，原文始终按纯文本展示。 */
function sourceLink(value: string): string | undefined {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}
</script>

<template>
  <details v-if="knowledge.sources.length || knowledge.web_search?.items.length" class="answer-sources">
    <summary>本轮参考资料 · {{ documents.length }} 份文件<span v-if="knowledge.web_search?.items.length"> · {{ knowledge.web_search.items.length }} 条历史网页</span></summary>
    <p class="source-note">可展开查看原文；回答也可能包含模型补充的一般建议。</p>
    <details v-for="document in documents" :key="document.id" class="source-item">
      <summary>{{ document.name }} · {{ document.sources.length }} 段原文</summary>
      <div v-for="source in document.sources" :key="source.id" class="source-excerpt">
        <small>[{{ source.id }}]<span v-if="source.hit.chunk.page_number"> · 第 {{ source.hit.chunk.page_number }} 页</span><span v-if="source.hit.chunk.section_path.length"> · {{ source.hit.chunk.section_path.join(' / ') }}</span></small>
        <blockquote>{{ source.hit.chunk.text }}</blockquote>
      </div>
    </details>
    <details v-for="source in knowledge.web_search?.items ?? []" :key="`web-${source.id}`" class="source-item">
      <summary>[{{ source.id }}] {{ source.title }} · 网页</summary>
      <a v-if="sourceLink(source.url)" :href="sourceLink(source.url)" target="_blank" rel="noopener noreferrer">查看原网页 ↗</a>
      <blockquote>{{ source.content }}</blockquote>
    </details>
  </details>
</template>

<style scoped>
.answer-sources { margin-top: 14px; font-size: 12px; color: #577574; }
summary { cursor: pointer; line-height: 1.8; overflow-wrap: anywhere; }
.source-note, small { color: #798b80; font-size: 11px; }
.source-excerpt { margin-top: 12px; }
.source-item { padding: 9px 12px; margin-top: 8px; border: 1px solid #deebe7; border-radius: 9px; background: #f6fbfa; }
blockquote { margin: 10px 0 0; padding-left: 12px; border-left: 2px solid #cfe2dc; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; max-height: 280px; overflow-y: auto; }
a { display: inline-block; margin-top: 8px; color: #246e50; }
</style>
