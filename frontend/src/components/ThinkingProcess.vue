<script setup lang="ts">
/** 公开过程展示层：四类工具与子智能体分组显示，展开后查看每次调用结果。 */
import { computed, ref, watch } from 'vue'
import type { ProcessStep } from '../api/requirements'
import ChatIcon from './ChatIcon.vue'

const props = defineProps<{ steps: ProcessStep[]; running?: boolean; interrupted?: boolean; seconds?: number; timings?: Record<string, number> }>()
const expanded = ref(!!props.running)
const tools = computed(() => new Set(props.steps.filter(step => step.call_id && step.stage !== 'subagent').map(step => step.call_id)).size)
const failed = computed(() => props.steps.some(step => step.status === 'failed'))
const cancelled = computed(() => props.steps.some(step => step.status === 'cancelled'))
const label = computed(() => props.running ? props.steps.at(-1)?.message || '正在连接旅行助手…' : cancelled.value ? '已停止' : props.interrupted ? '执行未完成' : failed.value ? '已结束 · 部分工具失败' : '已完成')
const statusNames = { running: '进行中', completed: '已完成', failed: '失败', cancelled: '已取消' }
// 原文读取先于知识库匹配，避免knowledge_read被归为检索；准备阶段不计入工具。
const categories = [
  { name: '子智能体', icon: 'atom', match: /subagent|research_start|research_done/ },
  { name: '读取资料', icon: 'file', match: /read|attachment|parse/ },
  { name: '检索知识库', icon: 'book', match: /knowledge|retriev|search_document/ },
  { name: '联网搜索', icon: 'globe', match: /web|search|transport/ },
  { name: '地图查询', icon: 'map', match: /map|route|nearby|geo/ },
]
// 旧对话只有研究开始/结束阶段，按记录配对；没有结束事件就保留未完成。
const calls = computed(() => {
  const items = props.steps.filter(step => step.call_id)
  if (items.some(step => step.stage === 'subagent')) return items
  let current: ProcessStep | undefined
  for (const [index, step] of props.steps.entries()) {
    if (step.stage === 'research_start') {
      current = { ...step, call_id: `legacy-research-${index}`, message: '研究助手', status: 'running' }
      items.push(current)
    } else if (step.stage === 'research_done' && current) {
      current.status = 'completed'
      current.summary = step.message
      current = undefined
    }
  }
  return items
})
const groups = computed(() => categories.map(category => {
  const members = calls.value.filter(step => categories.find(item => item.match.test(step.stage)) === category)
  const status: NonNullable<ProcessStep['status']> = members.some(step => step.status === 'running') ? 'running'
    : members.some(step => step.status === 'failed') ? 'failed'
    : members.some(step => step.status === 'cancelled') ? 'cancelled' : 'completed'
  return { ...category, calls: members, status,
    legacy: members.some(step => step.call_id?.startsWith('legacy-research-')),
    summary: category.name === '子智能体' ? members.at(-1)?.message : members.at(-1)?.summary }
}).filter(group => group.calls.length))
const agents = computed(() => groups.value.find(group => group.name === '子智能体')?.calls.length ?? 0)
watch(() => props.running, running => { expanded.value = !!running })
</script>

<template>
  <details class="stream-progress" :open="expanded" @toggle="expanded = ($event.target as HTMLDetailsElement).open">
    <summary>
      <ChatIcon name="atom" :class="{ pulsing: running }" />
      <span class="progress-label" role="status">{{ label }}</span>
      <span v-if="!running" class="process-count"><template v-if="tools">· {{ tools }} 次工具调用 </template><template v-if="agents">· {{ agents }} 次子智能体调用 </template><template v-if="seconds != null">· {{ seconds.toFixed(1) }} 秒</template></span>
      <ChatIcon name="chevron" class="progress-chevron" />
    </summary>
    <ol v-if="groups.length" class="process-steps">
      <li v-for="group in groups" :key="group.name" :class="['process-group', group.status]">
        <ChatIcon :name="group.icon" /><details class="tool-detail step-text"><summary><strong>{{ group.name }}</strong><span class="group-count">{{ group.calls.length }} 次</span><span class="group-preview">{{ group.summary }}</span><span class="step-state">{{ group.status === 'running' && !running ? '未完成' : group.legacy && group.status === 'completed' ? '已返回' : statusNames[group.status] }}</span><ChatIcon name="chevron" /></summary>
          <ul class="call-details"><li v-for="step in group.calls" :key="step.call_id"><div>{{ step.message }}<p v-if="step.summary">{{ step.summary }}</p><small v-if="step.elapsed_seconds != null">工具用时 {{ step.elapsed_seconds.toFixed(1) }} 秒</small></div><span>{{ step.status === 'running' && !running ? '未完成' : group.legacy && step.status === 'completed' ? '已返回' : step.status ? statusNames[step.status] : '' }}</span></li></ul>
        </details>
      </li>
    </ol>
    <p v-else class="empty">{{ running ? '正在整理问题与已有信息…' : '本轮没有调用外部工具或子智能体。' }}</p>
    <p v-if="interrupted && !cancelled" class="empty">当前过程尚未确认保存，重新读取对话可核对最终结果。</p>
  </details>
</template>

<style scoped>
.stream-progress{margin-bottom:20px;color:#748179;font-size:11px}summary{display:flex;align-items:center;gap:8px;cursor:pointer;list-style:none;min-width:0}summary::-webkit-details-marker{display:none}summary>.chat-icon{height:16px;width:16px}.progress-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.process-count{font-size:10px;color:#87958a;white-space:nowrap}.progress-chevron{color:#a0ada0;transition:transform .18s}.stream-progress[open] .progress-chevron{transform:rotate(180deg)}.process-steps{margin:13px 0 0 8px;padding:0 0 0 19px;border-left:1px solid #e1e9e0;display:grid;gap:12px;list-style:none}.process-steps li{display:flex;align-items:flex-start;gap:9px;line-height:1.7}.process-steps .chat-icon{width:16px;height:16px;margin-top:2px;color:#7e9582}.step-text{min-width:0;flex:1;overflow-wrap:anywhere}.step-text small{display:block;color:#95a08e;font-size:10px;margin-top:3px}.step-state{margin-left:auto;white-space:nowrap;font-size:10px;color:#7c9373}.failed .step-state{color:#ad6655}.cancelled .step-state{color:#9c8972}.running .step-state,.current{color:#3d8165}.empty{font-size:11px;line-height:1.8;color:#8e9b89;margin:13px 0 0 28px}.pulsing{animation:pulse 1.2s ease-in-out infinite alternate}@keyframes pulse{to{opacity:.4}}@media(prefers-reduced-motion:reduce){.pulsing{animation:none}}@media(max-width:600px){summary{flex-wrap:wrap;gap:6px}.process-count{font-size:9px}}
.tool-detail>summary{line-height:1.7;flex-wrap:nowrap;min-height:25px}.tool-detail>summary strong{font-size:12px;font-weight:500;white-space:nowrap}.group-count{font-size:10px;white-space:nowrap;color:#91a08b}.group-preview{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8d9b87}.tool-detail>summary .chat-icon{width:12px;height:12px;margin:0}.tool-detail[open]>summary .chat-icon{transform:rotate(180deg)}.tool-detail p{margin:5px 0 0;color:#879784;font-size:11px;line-height:1.8;white-space:pre-wrap}.call-details{max-height:220px;overflow:auto;padding:8px 0 0;list-style:none}.call-details li{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #edf1e8}.call-details li>span{white-space:nowrap;color:#8d9b87;font-size:10px}.process-steps{gap:10px}@media(max-width:600px){.group-preview{display:none}.tool-detail>summary .step-state{margin-left:auto}}
</style>
