<script setup lang="ts">
/** 表单组件层：资料编辑和筛选共用标签输入框，预填值来自后端，不猜测地图编号。 */
import type { DocumentMetadata } from '../api/documents'

defineProps<{ disabled?: boolean; filtering?: boolean }>()
const metadata = defineModel<DocumentMetadata>({ required: true })
</script>

<template>
  <fieldset :disabled="disabled" class="metadata-fields">
    <legend>{{ filtering ? '按已填写标签精确筛选' : '资料标签（可修改）' }}</legend>
    <label>城市<input v-model="metadata.city" maxlength="100" placeholder="如：杭州" /></label>
    <label>来源<input v-model="metadata.source" maxlength="255" placeholder="来源名称或链接" /></label>
    <label>审核状态<select v-model="metadata.review_status">
      <option :value="null">{{ filtering ? '不限' : '未标注' }}</option>
      <option value="pending">待审核</option><option value="approved">已通过</option><option value="rejected">未通过</option>
    </select></label>
    <label>POI编号（可选）<input v-model="metadata.poi_id" maxlength="100" placeholder="单个地点的地图编号，多地点资料留空" /></label>
  </fieldset>
</template>

<style scoped>
.metadata-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; width: 100%; box-sizing: border-box; border: 1px solid #dfe8e2; border-radius: 6px; padding: 12px; margin: 8px 0; }
legend { color: #63756b; font-size: 13px; }
label { display: grid; gap: 5px; font-size: 13px; }
input, select { min-width: 0; width: 100%; box-sizing: border-box; padding: 8px; border: 1px solid #bccbc2; border-radius: 5px; font: inherit; background: white; }
input:focus-visible, select:focus-visible { outline: 3px solid #90b9a5; outline-offset: 2px; }
@media (max-width: 500px) { .metadata-fields { grid-template-columns: minmax(0, 1fr); } }
</style>
