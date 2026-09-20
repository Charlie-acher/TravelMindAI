<script setup lang="ts">
/** 行程展示层：按天展示已保存的安排、地图与原文；撤销交给会话状态处理。 */
import { computed, ref } from 'vue'
import type { PlanSnapshot, PlanPlace } from '../api/requirements'
import { baiduMapLink } from '../mapLink'

const props = defineProps<{ snapshot: PlanSnapshot; origin?: string | null; undoAvailable: boolean; busy: boolean }>()
const cardElement = ref<HTMLElement | null>(null)
// 日期只取本份草稿；未补全时不推算起止日期。
const dateLabel = computed(() => {
  const dates = props.snapshot.plan.days.map(day => day.date)
  if (!dates.some(Boolean)) return '日期待定'
  if (!dates.every(Boolean)) return '部分日期待定'
  return dates.length === 1 ? dates[0] : `${dates[0]} — ${dates.at(-1)}`
})
// 进度条只表达已分配预算的占比，超预算金额仍单独显示。
const budgetPercent = computed(() => {
  const budget = props.snapshot.plan.budget
  return Number(budget.total_budget) > 0 ? Math.min(100, Math.max(0, Number(budget.total) / Number(budget.total_budget) * 100)) : 0
})

/** 按天定位函数：只在当前卡片内滚动，多份历史草稿不会互相跳转。 */
function jumpToDay(day: number): void {
  cardElement.value?.querySelector<HTMLElement>(`[data-day="${day}"]`)?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
}

const emit = defineEmits<{ undo: []; query: [question: string] }>()
const transportLabels = { walk: '步行', transit: '公共交通', taxi: '打车' }
const costLabels: Record<string, string> = { accommodation: '住宿', meals: '餐饮', intercity_transport: '城际交通', local_transport: '市内交通', tickets_and_activities: '门票与活动' }
const operationLabels = { create: '初次安排', modify: '已调整', undo: '已恢复上一版' }

/** 天气提问函数：带上行程日期；尚未定日期时先请用户补充。 */
function queryWeather(): void {
  if (props.busy) return
  const { destination, days } = props.snapshot.plan
  const dates = days.map(day => day.date).filter(Boolean)
  emit('query', dates.length === days.length
    ? `请查询${destination}在${dates.join('、')}的天气，并说明对行程的影响。`
    : `请帮我查询${destination}的天气，出行日期还未确定，请先向我确认具体出行日期。`)
}

/** 路线提问函数：使用当天实际地点顺序，只填入问题，不自动查询。 */
function queryRoute(day: PlanSnapshot['plan']['days'][number]): void {
  if (props.busy) return
  const places = day.activities.map(activity => activity.place.map.matched_name || activity.place.map.name)
  emit('query', `请查询${props.snapshot.plan.destination}第${day.day}天${day.date ? `（${day.date}）` : ''}的路线：${places.join(' → ')}。各站之间怎么走、耗时多久？请注明尚未核实的信息。`)
}

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
  <article ref="cardElement" class="itinerary-card" aria-label="逐日行程草稿">
    <header class="plan-header">
      <div class="header-top"><h3 class="journey"><span>{{ origin || '出发地待定' }}</span><span class="route-arrow" aria-label="前往">→</span><span>{{ snapshot.plan.destination }}</span></h3><span class="version">草稿 v{{ snapshot.version }}<small>{{ operationLabels[snapshot.operation] }}</small></span></div>
      <div class="trip-facts"><span>{{ snapshot.plan.days.length }} 天 · {{ snapshot.plan.budget.nights }} 晚</span><span>{{ snapshot.plan.budget.travelers }} 人同行</span><span class="trip-date">{{ dateLabel }}</span></div>
      <div v-if="snapshot.changes.length" class="plan-changes"><ul><li v-for="change in snapshot.changes" :key="change">{{ change }}</li></ul></div>
    </header>
    <div class="plan-grid">
      <section class="itinerary" aria-label="逐日安排">
        <nav class="day-nav" aria-label="按天查看"><button v-for="day in snapshot.plan.days" :key="day.day" type="button" @click="jumpToDay(day.day)">第 {{ day.day }} 天</button><span>整程展开</span></nav>
        <section v-for="day in snapshot.plan.days" :key="day.day" class="plan-day" :data-day="day.day">
          <header class="day-heading"><span class="day-number">{{ String(day.day).padStart(2, '0') }}</span><div><h4>第 {{ day.day }} 天<small v-if="day.date">{{ day.date }}</small></h4><p>{{ day.activities.map(activity => activity.place.map.matched_name || activity.place.map.name).join(' → ') }}</p></div><button class="route-query" type="button" :aria-label="`第 ${day.day} 天路线`" :disabled="busy" @click="queryRoute(day)">查路线 ↗</button></header>
          <ol class="activities">
            <li v-for="(activity, index) in day.activities" :key="`${activity.place.id}-${index}`">
              <time>{{ activity.start_time }}</time>
              <div class="activity-body"><div class="activity-title"><strong>{{ activity.place.map.matched_name || activity.place.map.name }}</strong><a v-if="mapLink(activity.place)" :href="mapLink(activity.place)" target="_blank" rel="noopener noreferrer">地图 ↗</a></div>
                <p class="activity-meta">建议游玩 {{ activity.duration_minutes }} 分钟</p>
                <details class="activity-sources"><summary>地址与资料依据 · {{ activity.place.sources.length }} 条</summary>
                  <p v-if="activity.place.map.address" class="activity-address">{{ activity.place.map.address }}</p>
                  <div v-for="source in activity.place.sources" :key="source.id" class="source"><span>{{ source.kind === 'attachment' ? '私人附件' : source.kind === 'web' ? '网页' : '知识库' }}</span><a v-if="source.url && /^https?:\/\//i.test(source.url)" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }} ↗</a><strong v-else>{{ source.title }}</strong><p>{{ source.text }}</p></div>
                </details>
                <p v-if="index < day.activities.length - 1" class="transfer">↓ {{ transportLabels[day.activities[index + 1]!.transport] }} · 预留 {{ day.activities[index + 1]!.transfer_minutes }} 分钟 · 待核实</p>
              </div>
            </li>
          </ol>
        </section>
      </section>
      <aside class="trip-aside">
        <section class="plan-budget" aria-label="演示预算估算">
          <div class="budget-heading"><h4>这趟旅行的预算</h4><span>演示估算</span></div><strong class="budget-amount">¥{{ money(snapshot.plan.budget.total) }}</strong><p class="budget-caption">{{ snapshot.plan.budget.travelers }} 人全程合计 · {{ snapshot.plan.budget.nights }} 晚住宿</p>
          <div class="budget-bar" :class="{ over: snapshot.plan.budget.over_budget }" aria-hidden="true"><span :style="{ width: `${budgetPercent}%` }"></span></div>
          <div class="budget-balance"><span>总预算 ¥{{ money(snapshot.plan.budget.total_budget) }}</span><strong :class="{ over: snapshot.plan.budget.over_budget }">{{ snapshot.plan.budget.over_budget ? '超出预算' : '预算余额' }} ¥{{ money(String(Math.abs(Number(snapshot.plan.budget.remaining)))) }}</strong></div>
          <details class="budget-details"><summary>预算明细与假设</summary><dl><div v-for="(amount, key) in snapshot.plan.budget.costs" :key="key"><dt>{{ costLabels[key] || key }}</dt><dd>¥{{ money(amount) }}</dd></div><div><dt>预备金</dt><dd>¥{{ money(snapshot.plan.budget.contingency) }}</dd></div></dl><ul><li v-for="assumption in snapshot.plan.budget.assumptions" :key="assumption">{{ assumption }}</li></ul></details>
          <p class="estimate-note">采用演示单价估算，非实时门票、酒店或车票报价。交通时间仅为预留建议，未核实路线耗时。</p>
        </section>
        <section class="plan-queries" aria-label="继续查询"><h4>出发前，再确认一下</h4><button type="button" class="weather-query" :disabled="busy" @click="queryWeather"><span>查询天气<small>按行程日期确认天气与出行安排</small></span><span>↗</span></button><details class="ticket-details"><summary>门票与往返交通</summary><p>门票、机票、火车票待查询。门票价格与预约请到景区官方核对。</p><div class="ticket-links"><a href="https://www.12306.cn/index/" target="_blank" rel="noopener noreferrer">12306 查火车票 ↗</a><a href="https://www.airchina.com.cn/zh-CN" target="_blank" rel="noopener noreferrer">国航官网查机票 ↗</a></div></details><details v-if="snapshot.plan.warnings.length" class="travel-notes"><summary>行程说明 · {{ snapshot.plan.warnings.length }} 项</summary><ul class="plan-warnings"><li v-for="warning in snapshot.plan.warnings" :key="warning">{{ warning }}</li></ul></details><p class="query-hint">查询会先填入输入框，你可以补充后再发送。</p></section>
      </aside>
    </div>
    <footer><div><strong>让行程更合你心意</strong><p>想调整？直接说「轻松一点」或「不去某个景点」。</p></div><button v-if="undoAvailable" type="button" :disabled="busy" @click="$emit('undo')">撤销这次修改</button></footer>
  </article>
</template>

<style scoped src="./itinerary.css"></style>
