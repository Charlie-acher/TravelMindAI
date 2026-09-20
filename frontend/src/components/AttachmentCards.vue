<script setup lang="ts">
/** 附件展示层：显示可打开的原件和采用状态，识别证据留给规划服务。 */
import type { AttachmentSnapshot, AttachmentUse } from '../api/attachments'
import AttachmentFileCard from './AttachmentFileCard.vue'
defineProps<{ sessionId: string; items: AttachmentSnapshot[]; originalsOnly?: boolean; use?: AttachmentUse | null; planned?: boolean }>()
const useLabels = { read: '读取附件', reference: '作为参考', required: '必经地点', replace: '替换指定日', unclear: '用途待确认' }
</script>

<template>
  <div class="attachment-cards">
    <p v-if="!originalsOnly && planned" class="attachment-note"><template v-if="use">{{ useLabels[use.mode] }}<template v-if="use.target_days.length"> · 第 {{ use.target_days.join('、') }} 天</template> · </template>已用于本轮行程草稿</p>
    <article v-for="item in items" :key="item.id" class="attachment-card">
      <AttachmentFileCard :session-id="sessionId" :item="item" />
    </article>
  </div>
</template>

<style scoped>
.attachment-cards { margin-top: 12px; font-size: 13px; line-height: 1.7; }
.attachment-note { color: #67716d; }
.attachment-card { margin: 8px 0; overflow-wrap: anywhere; }
p { margin: 6px 0; white-space: pre-wrap; }
</style>
