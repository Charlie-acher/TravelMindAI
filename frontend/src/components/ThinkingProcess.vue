<script setup lang="ts">
/** 公开过程展示层：按实际工具编号展示状态，普通阶段不冒充工具调用。 */
import { computed, ref, watch } from 'vue'
import type { ProcessStep } from '../api/requirements'
import ChatIcon from './ChatIcon.vue'

const props = defineProps<{ steps: ProcessStep[]; running?: boolean; interrupted?: boolean; seconds?: number; timings?: Record<string, number> }>()
const expanded = ref(!!props.running)
const tools = computed(() => new Set(props.steps.filter(step => step.call_id).map(step => step.call_id)).size)
const failed = computed(() => props.steps.some(step => step.status === 'failed'))
const cancelled = computed(() => props.steps.some(step => step.status === 'cancelled'))
const label = computed(() => props.running ? props.steps.at(-1)?.message || '正在连接旅行助手…' : cancelled.value ? '已停止' : props.interrupted ? '执行未完成' : failed.value ? '已结束 · 部分工具失败' : '已完成')
const statusNames = { running: '进行中', completed: '已完成', failed: '失败', cancelled: '已取消' }
/** 图标选择函数：工具与阶段使用同一组细线图标，不改变事件含义。 */
function icon(stage: string): string {
  if (/knowledge|retriev|search_document/.test(stage)) return 'book'
  if (/read|attachment|parse/.test(stage)) return 'file'
  if (/map|route|nearby|geo/.test(stage)) return 'map'
  if (/web|search|transport/.test(stage)) return 'globe'
  if (/valid|review|check/.test(stage)) return 'status'
  if (/plan|itinerary|draft/.test(stage)) return 'route'
  return 'atom'
}
watch(() => props.running, running => { expanded.value = !!running })
</script>

<template>
  <details class="stream-progress" :open="expanded" @toggle="expanded = ($event.target as HTMLDetailsElement).open">
    <summary>
      <ChatIcon name="atom" :class="{ pulsing: running }" />
      <span class="progress-label" role="status">{{ label }}</span>
      <span v-if="!running" class="process-count"><template v-if="tools">· {{ tools }} 次工具调用 </template><template v-if="seconds != null">· {{ seconds.toFixed(1) }} 秒</template></span>
      <ChatIcon name="chevron" class="progress-chevron" />
    </summary>
    <ol v-if="steps.length" class="process-steps">
      <li v-for="(step, index) in steps" :key="step.call_id || `stage-${index}`" :class="[step.status, { current: running && index === steps.length - 1 }]">
        <ChatIcon :name="icon(step.stage)" /><div class="step-text"><details v-if="step.summary" class="tool-detail"><summary>{{ step.message }}<ChatIcon name="chevron" /></summary><p>{{ step.summary }}</p></details><span v-else>{{ step.message }}</span><small v-if="step.elapsed_seconds != null">工具用时 {{ step.elapsed_seconds.toFixed(1) }} 秒</small></div>
        <span v-if="step.status" class="step-state">{{ step.status === 'running' && !running ? '未完成' : statusNames[step.status] }}</span>
      </li>
    </ol>
    <p v-else class="empty">{{ running ? '正在连接旅行助手…' : '未记录公开执行步骤。' }}</p>
    <p v-if="interrupted && !cancelled" class="empty">当前过程尚未确认保存，重新读取对话可核对最终结果。</p>
  </details>
</template>

<style scoped>
.stream-progress{margin-bottom:20px;color:#748179;font-size:11px}summary{display:flex;align-items:center;gap:8px;cursor:pointer;list-style:none;min-width:0}summary::-webkit-details-marker{display:none}summary>.chat-icon{height:16px;width:16px}.progress-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.process-count{font-size:10px;color:#87958a;white-space:nowrap}.progress-chevron{color:#a0ada0;transition:transform .18s}.stream-progress[open] .progress-chevron{transform:rotate(180deg)}.process-steps{margin:13px 0 0 8px;padding:0 0 0 19px;border-left:1px solid #e1e9e0;display:grid;gap:12px;list-style:none}.process-steps li{display:flex;align-items:flex-start;gap:9px;line-height:1.7}.process-steps .chat-icon{width:16px;height:16px;margin-top:2px;color:#7e9582}.step-text{min-width:0;flex:1;overflow-wrap:anywhere}.step-text small{display:block;color:#95a08e;font-size:10px;margin-top:3px}.step-state{margin-left:auto;white-space:nowrap;font-size:10px;color:#7c9373}.failed .step-state{color:#ad6655}.cancelled .step-state{color:#9c8972}.running .step-state,.current{color:#3d8165}.empty{font-size:11px;line-height:1.8;color:#8e9b89;margin:13px 0 0 28px}.pulsing{animation:pulse 1.2s ease-in-out infinite alternate}@keyframes pulse{to{opacity:.4}}@media(prefers-reduced-motion:reduce){.pulsing{animation:none}}@media(max-width:600px){summary{flex-wrap:wrap;gap:6px}.process-count{font-size:9px}}
.tool-detail>summary{white-space:normal;line-height:1.7}.tool-detail>summary .chat-icon{width:12px;height:12px;margin:0}.tool-detail[open]>summary .chat-icon{transform:rotate(180deg)}.tool-detail p{margin:7px 0 0;color:#879784;font-size:11px;line-height:1.8;white-space:pre-wrap}
</style>
