<script setup lang="ts">
/** 附件卡片层：输入框与消息共用原件链接、文件信息和外观。 */
import { attachmentContentUrl, type AttachmentSnapshot } from '../api/attachments'
import ChatIcon from './ChatIcon.vue'

defineProps<{ sessionId: string; item: AttachmentSnapshot; removable?: boolean; disabled?: boolean }>()
defineEmits<{ remove: [id: string] }>()

/** 大小展示函数：旧快照缺少大小时不猜测，由会话恢复流程补齐。 */
function formatSize(size?: number | null): string {
  if (size == null) return '附件'
  return size >= 1_000_000 ? `${(size / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.ceil(size / 1000))} KB`
}
</script>

<template>
  <div class="attachment-file-card">
    <a class="file-open" :href="attachmentContentUrl(sessionId, item.id, true)" target="_blank" rel="noopener" :aria-label="`打开附件 ${item.file_name}`">
      <span class="file-kind">{{ item.file_name.split('.').pop()?.toUpperCase().slice(0, 8) }}</span>
      <span class="file-copy"><strong :title="item.file_name">{{ item.file_name }}</strong><small>{{ formatSize(item.size_bytes) }} · 已上传</small></span>
    </a>
    <button v-if="removable" class="file-remove" type="button" :aria-label="`移除附件 ${item.file_name}`" :disabled="disabled" @click="$emit('remove', item.id)"><ChatIcon name="close" /></button>
  </div>
</template>

<style scoped>
.attachment-file-card { display: flex; align-items: center; gap: 8px; box-sizing: border-box; width: 285px; max-width: 100%; background: #f5f9f5; border: 1px solid #e0ebe2; border-radius: 12px; padding: 11px 12px; }
.file-open { display: flex; align-items: center; gap: 11px; min-width: 0; flex: 1; text-decoration: none; color: #365b40; border-radius: 5px; }
.file-open:hover strong { color: #0f766e; text-decoration: underline; }.file-open:focus-visible,.file-remove:focus-visible { outline: 2px solid #0f766e; outline-offset: 3px; }
.file-kind { display: grid; place-items: center; flex-shrink: 0; width: 38px; height: 43px; border: 1px solid #d7e6dc; border-radius: 8px; background: #fff; color: #0f766e; font-size: 9px; font-weight: 650; }
.file-copy { min-width: 0; flex: 1; text-align: left; }.file-copy strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 550; line-height: 1.5; }.file-copy small { display: block; font-size: 10px; color: #7b9382; margin-top: 5px; line-height: 1.5; }
.file-remove { display: grid; place-items: center; flex-shrink: 0; width: 25px; height: 28px; border: 0; border-radius: 5px; background: transparent; color: #789084; cursor: pointer; }.file-remove:hover { color: #0f766e; background: #e6f0e9; }.file-remove:disabled { cursor: default; opacity: .5; }.file-remove svg { width: 15px; }
</style>
