<script setup lang="ts">
/** 表单组件层：资料编辑和筛选只使用城市、类别两个业务标签。 */
import type { DocumentMetadata } from '../api/documents'

defineProps<{ disabled?: boolean; filtering?: boolean; compact?: boolean }>()
const metadata = defineModel<DocumentMetadata>({ required: true })
</script>

<template>
  <fieldset :disabled="disabled" class="metadata-fields" :class="{ compact }">
    <legend>{{ filtering ? '按已填写标签精确筛选' : '资料标签（可修改）' }}</legend>
    <label>城市<input v-model="metadata.city" maxlength="100" placeholder="如：杭州" /></label>
    <label>类别<select v-model="metadata.category"><option :value="null">{{ filtering ? '不限' : '未分类' }}</option>
      <option value="住宿">住宿</option><option value="景点">景点</option><option value="餐馆">餐馆</option></select></label>
  </fieldset>
</template>

<style scoped>
.metadata-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; width: 100%; box-sizing: border-box; border: 1px solid #dfe8e2; border-radius: 6px; padding: 12px; margin: 8px 0; }
legend { color: #63756b; font-size: 13px; }
label { display: grid; gap: 5px; font-size: 13px; }
input, select { min-width: 0; width: 100%; box-sizing: border-box; padding: 8px; border: 1px solid #bccbc2; border-radius: 5px; font: inherit; background: white; }
input:focus-visible, select:focus-visible { outline: 3px solid #90b9a5; outline-offset: 2px; }
@media (max-width: 500px) { .metadata-fields { grid-template-columns: minmax(0, 1fr); } }
.metadata-fields.compact { display: flex; width: auto; gap: 10px; border: 0; padding: 0; margin: 0; }
.compact legend { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.compact label { font-size: 12px; color: #718663; }
.compact input, .compact select { width: 120px; padding: 9px; border-color: #dfe7d8; color: #526b47; border-radius: 6px; }
@media (max-width: 500px) { .compact input, .compact select { width: 105px; } }
</style>
