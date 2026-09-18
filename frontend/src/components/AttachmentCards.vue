<script setup lang="ts">
/** 附件展示层：直接显示本轮保存的识别快照，原件由受保护接口下载。 */
import type { AttachmentSnapshot } from '../api/attachments'
import AttachmentFileCard from './AttachmentFileCard.vue'
defineProps<{ sessionId: string; items: AttachmentSnapshot[]; originalsOnly?: boolean }>()
</script>

<template>
  <div class="attachment-cards">
    <p v-if="!originalsOnly" class="attachment-note">本轮仅识别和解释附件，原行程未修改；地点和道路可通行情况尚未核对。</p>
    <article v-for="item in items" :key="item.id" class="attachment-card">
      <AttachmentFileCard :session-id="sessionId" :item="item" />
      <template v-if="!originalsOnly">
        <p v-if="item.error_message" role="status">识别失败：{{ item.error_message }}</p>
        <details v-if="item.analysis" open>
          <summary>识别结果<span v-if="item.analysis.city"> · {{ item.analysis.city }}</span></summary>
          <p>{{ item.analysis.summary }}</p>
          <ul v-if="item.analysis.waypoints.length"><li v-for="(point, index) in item.analysis.waypoints" :key="index">
            <strong>{{ point.order === null ? '顺序未知' : `第${point.order}处` }} · {{ point.name }}</strong><span v-if="point.needs_confirmation" class="uncertain">待确认</span>
            <p>原文依据：{{ point.evidence }}</p>
          </li></ul>
          <p v-for="(warning, index) in item.analysis.warnings" :key="index" class="uncertain">待确认：{{ warning }}</p>
        </details>
      </template>
    </article>
  </div>
</template>

<style scoped>
.attachment-cards { margin-top: 12px; font-size: 13px; line-height: 1.7; }
.attachment-note { color: #67716d; }
.attachment-card { margin: 8px 0; overflow-wrap: anywhere; }
summary { cursor: pointer; margin-top: 8px; font-weight: 600; }
p { margin: 6px 0; white-space: pre-wrap; }
ul { padding-left: 20px; }
.uncertain { color: #94621a; }
strong + .uncertain { margin-left: 8px; }
</style>
