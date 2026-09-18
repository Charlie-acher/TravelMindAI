<script setup lang="ts">
/** 行程展示层：按天展示已保存的安排、地图与原文；撤销交给会话状态处理。 */
import type { PlanSnapshot, PlanPlace } from '../api/requirements'
import { baiduMapLink } from '../mapLink'

defineProps<{ snapshot: PlanSnapshot; undoAvailable: boolean; busy: boolean }>()
defineEmits<{ undo: [] }>()
const transportLabels = { walk: '步行', transit: '公共交通', taxi: '打车' }
const costLabels: Record<string, string> = { accommodation: '住宿', meals: '餐饮', intercity_transport: '城际交通', local_transport: '市内交通', tickets_and_activities: '门票与活动' }
const operationLabels = { create: '初次安排', modify: '已调整', undo: '已恢复上一版' }

/** 地图链接函数：在百度网页展示已核对坐标，有入口时优先标记入口。 */
function mapLink(place: PlanPlace): string | undefined {
  if (place.map.status !== 'found') return undefined
  const point = place.map.entrance ?? place.map.location
  if (!point) return undefined
  return baiduMapLink(point, place.map.matched_name || place.map.name, place.map.address)
}

/** 金额显示函数：预算单位固定为人民币，保留两位小数。 */
function money(value: string): string {
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
</script>

<template>
  <article class="itinerary-card" aria-label="逐日行程草稿">
    <header class="plan-header">
      <div><span class="plan-eyebrow">{{ snapshot.plan.destination }} · {{ snapshot.plan.days.length }} 日旅程</span><h3>{{ snapshot.plan.title }}</h3></div>
      <span class="version">草稿 v{{ snapshot.version }}<small>{{ operationLabels[snapshot.operation] }}</small></span>
    </header>
    <div v-if="snapshot.changes.length" class="plan-changes"><strong>本次安排</strong><ul><li v-for="change in snapshot.changes" :key="change">{{ change }}</li></ul></div>
    <section v-for="day in snapshot.plan.days" :key="day.day" class="plan-day">
      <h4><span class="day-number">{{ String(day.day).padStart(2, '0') }}</span>第 {{ day.day }} 天<small v-if="day.date">{{ day.date }}</small></h4>
      <ol class="activities">
        <li v-for="(activity, index) in day.activities" :key="`${activity.place.id}-${index}`">
          <time>{{ activity.start_time }}</time>
          <div class="activity-body"><div class="activity-title"><strong>{{ activity.place.map.matched_name || activity.place.map.name }}</strong><a v-if="mapLink(activity.place)" :href="mapLink(activity.place)" target="_blank" rel="noopener noreferrer">地图 ↗</a></div>
            <p class="activity-meta">游玩 {{ activity.duration_minutes }} 分钟 · <template v-if="index">{{ transportLabels[activity.transport] }}预留 {{ activity.transfer_minutes }} 分钟</template><template v-else>当天首站</template></p>
            <p v-if="activity.place.map.address" class="activity-address">{{ activity.place.map.address }}</p>
            <details class="activity-sources"><summary>查看依据 · {{ activity.place.sources.length }} 条</summary>
              <div v-for="source in activity.place.sources" :key="source.id" class="source"><span>{{ source.kind === 'web' ? '网页' : '知识库' }}</span><a v-if="source.url && /^https?:\/\//i.test(source.url)" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }} ↗</a><strong v-else>{{ source.title }}</strong><p>{{ source.text }}</p></div>
            </details>
          </div>
        </li>
      </ol>
    </section>
    <section class="plan-budget" aria-label="演示预算估算">
      <div class="budget-total"><div><span>全团费用 · 演示估算</span><strong>¥{{ money(snapshot.plan.budget.total) }}</strong></div><p :class="{ over: snapshot.plan.budget.over_budget }">{{ snapshot.plan.budget.travelers }} 人 · {{ snapshot.plan.budget.nights }} 晚<br />{{ snapshot.plan.budget.over_budget ? '超出预算' : '预算余额' }} ¥{{ money(String(Math.abs(Number(snapshot.plan.budget.remaining)))) }}</p></div>
      <details><summary>预算明细与假设</summary><dl><div v-for="(amount, key) in snapshot.plan.budget.costs" :key="key"><dt>{{ costLabels[key] || key }}</dt><dd>¥{{ money(amount) }}</dd></div><div><dt>预备金</dt><dd>¥{{ money(snapshot.plan.budget.contingency) }}</dd></div><div><dt>总预算</dt><dd>¥{{ money(snapshot.plan.budget.total_budget) }}</dd></div></dl><ul><li v-for="assumption in snapshot.plan.budget.assumptions" :key="assumption">{{ assumption }}</li></ul></details>
      <p class="estimate-note">采用演示单价估算，非实时门票、酒店或车票报价。交通时间仅为预留建议，未核实路线耗时。</p>
    </section>
    <ul v-if="snapshot.plan.warnings.length" class="plan-warnings"><li v-for="warning in snapshot.plan.warnings" :key="warning">{{ warning }}</li></ul>
    <footer><p>想调整？直接说「第二天轻松一点」或「不去某个景点」。</p><button v-if="undoAvailable" type="button" :disabled="busy" @click="$emit('undo')">撤销这次修改</button></footer>
  </article>
</template>

<style scoped>
/* 行程属于当前回答；卡片与消息一起滚动，窄屏改为纵向排列。 */
.itinerary-card { width: min(660px, 100%); margin-top: 14px; border: 1px solid #dceae8; border-radius: 18px; background: #fff; overflow: hidden; overflow-wrap: anywhere; color: #34565c; font-size: 12px; }
.plan-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding: 22px; background: linear-gradient(120deg, #e0f5ed, #edf4ff); }
.plan-eyebrow { font-size: 11px; color: #608a85; letter-spacing: 1px; } h3 { margin: 8px 0 0; font-size: 20px; line-height: 1.5; color: #234c53; }
.version { flex-shrink: 0; border: 1px solid #fff; border-radius: 10px; background: #ffffff88; padding: 8px 10px; text-align: right; color: #48767a; font-size: 11px; }.version small { display: block; margin-top: 4px; font-size: 10px; }
.plan-changes { margin: 16px 22px 0; padding: 12px 14px; border-radius: 9px; background: #f4f9f7; color: #53786f; }.plan-changes strong { font-size: 11px; }.plan-changes ul { margin: 5px 0 0; padding-left: 16px; line-height: 1.8; }
.plan-day { padding: 20px 22px 0; } h4 { display: flex; align-items: center; gap: 9px; margin: 0 0 17px; color: #254f54; font-size: 14px; } h4 small { margin-left: auto; font-size: 11px; font-weight: 400; color: #819598; }
.day-number { font-size: 11px; border-radius: 8px; padding: 7px; background: #edf6f5; color: #518781; }
.activities { list-style: none; padding: 0; margin: 0; }.activities > li { display: flex; gap: 14px; padding-bottom: 20px; } time { flex-shrink: 0; padding-top: 1px; font-size: 12px; font-variant-numeric: tabular-nums; color: #51837f; }
.activity-body { flex: 1; min-width: 0; border-left: 1px solid #ddece8; padding-left: 15px; }.activity-title { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }.activity-title strong { font-size: 14px; color: #284f56; }
a { color: #2d827b; text-decoration: underline; text-underline-offset: 3px; }.activity-title a { flex-shrink: 0; font-size: 11px; }.activity-meta, .activity-address { margin: 7px 0 0; font-size: 11px; line-height: 1.6; color: #7c9093; }
summary { cursor: pointer; font-size: 11px; color: #568480; line-height: 1.8; }.activity-sources { margin-top: 9px; }.source { margin-top: 8px; background: #f5f8f8; border-radius: 7px; padding: 10px; font-size: 11px; }.source > span { color: #7f9897; margin-right: 8px; }.source p { white-space: pre-wrap; line-height: 1.8; margin: 7px 0 0; color: #718689; }
.plan-budget { margin: 0 22px 18px; border: 1px solid #e0ebe9; border-radius: 12px; padding: 15px; }.budget-total { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.budget-total span { font-size: 11px; color: #7b9293; }.budget-total strong { display: block; margin-top: 7px; font-size: 22px; font-weight: 600; color: #2b6265; }.budget-total p { font-size: 11px; line-height: 1.9; color: #6e8789; text-align: right; }.budget-total .over { color: #ad6c41; }.plan-budget details { margin-top: 13px; border-top: 1px solid #edf2f1; padding-top: 10px; }
dl { margin: 9px 0; } dl > div { display: flex; justify-content: space-between; margin: 6px 0; color: #6e8587; } dd { margin: 0; }.plan-budget ul { padding-left: 17px; line-height: 1.8; color: #7c9091; }.estimate-note { font-size: 10px; color: #899b9d; line-height: 1.8; margin: 10px 0 0; }
.plan-warnings { margin: 0 22px 18px; padding-left: 17px; font-size: 11px; color: #937c52; line-height: 1.9; } footer { border-top: 1px solid #edf2f0; padding: 15px 22px; display: flex; align-items: center; justify-content: space-between; gap: 12px; background: #fafcfc; } footer p { margin: 0; font-size: 11px; line-height: 1.8; color: #839697; } button { flex-shrink: 0; padding: 8px 10px; background: #fff; border: 1px solid #cddfdc; border-radius: 8px; color: #497c77; font-size: 11px; cursor: pointer; } button:disabled { opacity: .5; cursor: wait; }
@media (max-width: 600px) { .plan-header { padding: 16px; } h3 { font-size: 17px; }.plan-day { padding: 18px 14px 0; }.plan-changes { margin: 14px 14px 0; }.plan-budget { margin: 0 14px 14px; }.activities > li { gap: 9px; }.activity-body { padding-left: 10px; }.activity-title { flex-wrap: wrap; } footer { padding: 14px; align-items: flex-start; flex-direction: column; } }
</style>
