<script setup lang="ts">
/** 费用管理层：管理员查看原币种估算、未知项和逐次调用，不把估算当供应商账单。 */
import { onMounted, ref } from 'vue'
import { apiFetch, readResponse } from '../api/http'

interface UsageRow { id: string; request_id: string; provider: string; model: string; kind: string; success: boolean; started_at: string; units: number | null; input_tokens: number | null; output_tokens: number | null; cache_read_tokens: number | null; cost: { amount: string | null; currency: string | null; status: string; reason: string; source: string | null; rate_version: string } }
interface UsageReport { total_calls: number; known_costs: Record<string, string>; status_counts: Record<string, number>; complete: boolean; items: UsageRow[]; offset: number; limit: number; basis: string }
const report = ref<UsageReport | null>(null)
const busy = ref(false)
const error = ref('')
const days = ref('1')
const requestId = ref('')
const appliedRequestId = ref('')
const period = ref<{ start: string; end: string } | null>(null)

/** 读取函数：翻页固定时间范围，重新查询才更新终点，防止新调用挤乱分页。 */
async function load(offset = 0): Promise<void> {
  if (busy.value) return
  busy.value = true; error.value = ''
  if (!offset || !period.value) {
    appliedRequestId.value = requestId.value.trim()
    const end = new Date()
    period.value = { start: new Date(end.getTime() - Number(days.value) * 86400000).toISOString(), end: end.toISOString() }
  }
  const params = new URLSearchParams({ ...period.value, offset: String(offset), limit: '50' })
  if (appliedRequestId.value) params.set('request_id', appliedRequestId.value)
  try { report.value = await readResponse<UsageReport>(await apiFetch(`/api/v1/admin/usage?${params}`)) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '费用读取失败，请重试' }
  finally { busy.value = false }
}
onMounted(() => { void load() })
</script>

<template>
  <section class="usage-panel" aria-label="调用费用统计">
    <h2>调用费用</h2><p>按官方原价估算，不含免费额度、套餐优惠和本机运行成本。不同币种分别合计；未知费用不会记成零。</p>
    <form @submit.prevent="load()"><label>时间范围 <select v-model="days" :disabled="busy"><option value="1">最近一天</option><option value="7">最近七天</option><option value="30">最近三十天</option></select></label><label>请求编号 <input v-model="requestId" maxlength="100" :disabled="busy" placeholder="留空查看全部" /></label><button :disabled="busy">{{ busy ? '读取中…' : '查询 / 刷新' }}</button></form>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="report">
      <div class="usage-totals"><strong v-for="(amount, currency) in report.known_costs" :key="currency">{{ currency }} {{ Number(amount).toFixed(6) }}</strong><span>{{ report.total_calls }} 次调用</span><span>费用未知 {{ report.status_counts.unknown || 0 }} 次</span><span>本地处理 {{ report.status_counts.local || 0 }} 次</span></div>
      <p v-if="!report.complete">当前合计仅包含已能计算的费用，不能作为完整账单。</p>
      <div class="usage-table"><table><thead><tr><th>时间</th><th>服务 / 模型</th><th>用量（输入 / 缓存 / 输出）</th><th>费用</th><th>状态与依据</th></tr></thead><tbody><tr v-for="item in report.items" :key="item.id"><td>{{ new Date(item.started_at).toLocaleString('zh-CN') }}<details><summary>请求编号</summary>{{ item.request_id }}</details></td><td>{{ item.provider }}<br />{{ item.model }}<small>{{ item.kind }}</small></td><td><template v-if="['map', 'search', 'ocr'].includes(item.kind)">{{ item.units ?? '未知' }} {{ item.kind === 'ocr' ? '页' : '次' }}</template><template v-else>{{ item.input_tokens ?? '未知' }} / {{ item.kind === 'embedding' ? '不适用' : item.cache_read_tokens ?? '未知' }} / {{ item.kind === 'embedding' ? '不适用' : item.output_tokens ?? '未知' }}</template></td><td>{{ item.cost.amount === null ? item.cost.status === 'local' ? '本地处理' : '未知' : `${item.cost.currency} ${Number(item.cost.amount).toFixed(6)}` }}</td><td>{{ item.success ? '调用完成' : '调用未完成' }}<small>{{ item.cost.reason }}</small><a v-if="item.cost.source" :href="item.cost.source" target="_blank" rel="noopener noreferrer">费率来源 ↗</a><small>{{ item.cost.rate_version }}</small></td></tr></tbody></table></div>
      <p v-if="!report.items.length">这段时间还没有调用记录。</p><div class="usage-pages"><button :disabled="busy || report.offset === 0" @click="load(Math.max(0, report.offset - report.limit))">上一页</button><span>{{ report.total_calls ? report.offset + 1 : 0 }}–{{ Math.min(report.offset + report.limit, report.total_calls) }} / {{ report.total_calls }}</span><button :disabled="busy || report.offset + report.limit >= report.total_calls" @click="load(report.offset + report.limit)">下一页</button></div>
    </template>
  </section>
</template>

<style scoped>
.usage-panel { padding: 28px; max-width: 1300px; margin: auto; color: #30534d; }
h2 { margin-top: 0; } p, small { color: #637a74; line-height: 1.6; }
form, .usage-totals, .usage-pages { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; margin: 20px 0; }
input, select, button { font: inherit; padding: 8px 10px; border: 1px solid #d4e2da; border-radius: 8px; background: white; color: inherit; }
button { cursor: pointer; } button:disabled { opacity: .5; cursor: default; }
.usage-totals { background: #f1f7f2; padding: 16px; border-radius: 12px; }
.usage-table { overflow-x: auto; } table { width: 100%; border-collapse: collapse; font-size: 13px; } th, td { text-align: left; padding: 12px; border-bottom: 1px solid #e1eae4; vertical-align: top; } small { display: block; } a { color: #16796c; }
</style>
