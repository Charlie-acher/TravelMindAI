<script setup lang="ts">
/** 行程展示层：按天展示已保存的安排、地图与原文；撤销交给会话状态处理。 */
import { computed, ref } from 'vue'
import type { PlanSnapshot, PlanPlace } from '../api/requirements'
import { baiduMapLink } from '../mapLink'
import ChatIcon from './ChatIcon.vue'

const props = defineProps<{ snapshot: PlanSnapshot; origin?: string | null; undoAvailable: boolean; busy: boolean; compact?: boolean }>()
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

const emit = defineEmits<{ undo: []; query: [question: string]; openFile: [id: string] }>()
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
  <article v-if="compact" class="plan-summary" aria-label="行程概览">
    <header class="summary-head"><span class="plan-symbol"><ChatIcon name="file" /></span><div><h3>{{ snapshot.plan.title }}</h3><p>{{ snapshot.plan.days.length }} 天 {{ snapshot.plan.budget.nights }} 晚 · {{ snapshot.plan.budget.travelers }} 人出游 · {{ dateLabel }}</p></div><span class="draft-pill">草稿 v{{ snapshot.version }}</span></header>
    <ol class="summary-days"><li v-for="day in snapshot.plan.days" :key="day.day"><span class="day-number">DAY<b>{{ String(day.day).padStart(2, '0') }}</b></span><div><h4>{{ day.date || `${snapshot.plan.destination} · 第${day.day}天` }}</h4><p class="summary-route"><template v-for="(activity, index) in day.activities" :key="activity.place.id"><ChatIcon v-if="index" name="chevron" /><span>{{ activity.place.map.matched_name || activity.place.map.name }}</span></template><span v-if="!day.activities.length">当日安排待补充</span></p></div></li></ol>
    <footer class="summary-bottom"><span>已保存至本对话文件</span><div><button v-if="undoAvailable" :disabled="busy" @click="emit('undo')">撤销本次修改</button><button @click="emit('openFile', snapshot.itinerary_id)">打开行程草稿<ChatIcon name="chevron" /></button></div></footer>
  </article>
  <article v-else ref="cardElement" class="itinerary-card" aria-label="逐日行程草稿">
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
                <p v-if="index < day.activities.length - 1" class="transfer">↓ {{ transportLabels[day.activities[index + 1]!.transport] }} · 预留 {{ day.activities[index + 1]!.transfer_minutes }} 分钟 · <span v-if="day.activities[index + 1]!.route?.status === 'estimated'" :title="`百度查询时间：${day.activities[index + 1]!.route!.checked_at}`">地图估时 {{ day.activities[index + 1]!.route!.duration_minutes }} 分钟</span><span v-else>路线待核实</span></p>
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
          <p class="estimate-note">采用演示单价估算，非实时门票、酒店或车票报价。交通以各段核实状态为准，地图估时反映查询时情况，出发前请再确认。</p>
        </section>
        <section v-if="snapshot.plan.ticket_prices?.length" class="plan-budget" aria-label="官方公布门票价格">
          <h4>门票公开价格</h4>
          <p class="estimate-note">以下为本次查到的官方公布价，未计入上方演示预算；出行日价格、优惠资格和可预约名额仍需确认。</p>
          <details v-for="price in snapshot.plan.ticket_prices" :key="`${price.place}-${price.travel_date}`" class="budget-details">
            <summary>{{ price.place }} · {{ price.status === 'published' && price.amount !== null ? `¥${money(price.amount)} ${price.unit}` : price.status === 'expired' ? '公告已过适用期' : price.status === 'not_applicable' ? '公告不适用所选日期' : '待查询' }}</summary>
            <p>{{ price.conditions || (price.status === 'error' ? '本次查询未完成，稍后可再查。' : '暂未查到可用的官方票价依据。') }}</p><p v-if="price.valid_from || price.valid_until" class="estimate-note">公告适用：{{ price.valid_from || '起始日期未注明' }} 至 {{ price.valid_until || '结束日期未注明' }}</p><p v-if="price.evidence">原文：{{ price.evidence }}</p>
            <a v-if="price.source_url && /^https?:\/\//i.test(price.source_url)" :href="price.source_url" target="_blank" rel="noopener noreferrer">{{ price.source_title || '官方来源' }} ↗</a>
            <p class="estimate-note">查询：{{ new Date(price.checked_at).toLocaleString('zh-CN') }}<br v-if="price.published_date" /><span v-if="price.published_date">资料发布：{{ price.published_date }}</span></p>
          </details>
        </section>
        <section class="plan-queries" aria-label="继续查询"><h4>出发前，再确认一下</h4><button type="button" class="weather-query" :disabled="busy" @click="queryWeather"><span>查询天气<small>按行程日期确认天气与出行安排</small></span><span>↗</span></button><details class="ticket-details"><summary>住宿、门票与往返交通</summary><p>酒店、机票、火车票的指定日期价格待查询。门票价格与预约请到景区官方核对。</p><div class="ticket-links"><a href="https://www.12306.cn/index/" target="_blank" rel="noopener noreferrer">12306 查火车票 ↗</a><a href="https://hrewards.huazhu.com/" target="_blank" rel="noopener noreferrer">华住会官网查酒店 ↗</a><a href="https://www.airchina.com.cn/zh-CN" target="_blank" rel="noopener noreferrer">国航官网查机票 ↗</a></div></details><details v-if="snapshot.plan.warnings.length" class="travel-notes"><summary>行程说明 · {{ snapshot.plan.warnings.length }} 项</summary><ul class="plan-warnings"><li v-for="warning in snapshot.plan.warnings" :key="warning">{{ warning }}</li></ul></details><p class="query-hint">查询会先填入输入框，你可以补充后再发送。</p></section>
      </aside>
    </div>
    <footer><div><strong>让行程更合你心意</strong><p>想调整？直接说「轻松一点」或「不去某个景点」。</p></div><button v-if="undoAvailable" type="button" :disabled="busy" @click="$emit('undo')">撤销这次修改</button></footer>
  </article>
</template>

<style scoped src="./itinerary.css"></style>
<style scoped>
.plan-summary{border:1px solid #dde7df;border-radius:12px;overflow:hidden;margin:20px 0 17px;background:#fff;box-shadow:0 3px 12px #284b3805;color:#36523b}.summary-head{padding:19px 20px 15px;background:#f8faf6;border-bottom:1px solid #e8eee5;display:flex;align-items:flex-start;gap:11px}.summary-head>div{min-width:0}.summary-head h3{font-size:16px;margin:0 0 6px;font-weight:600;overflow-wrap:anywhere}.summary-head p{font-size:11px;color:#8b9888;margin:0;line-height:1.8}.plan-symbol{display:grid;place-items:center;width:34px;height:38px;flex-shrink:0;border:1px solid #dfe8d9;border-radius:8px;background:#eaf2e4;color:#7c996b}.draft-pill{margin-left:auto;font-size:10px;white-space:nowrap;border:1px solid #e2eadd;border-radius:4px;padding:3px 6px;color:#829778}.summary-days{padding:7px 20px;margin:0;list-style:none}.summary-days>li{display:flex;gap:15px;padding:18px 0;border-bottom:1px solid #eef1e9}.summary-days>li:last-child{border:0}.summary-days>li>div{min-width:0}.day-number{width:28px;flex-shrink:0;font-size:10px;color:#95a58e;padding-top:2px}.day-number b{display:block;font-family:Georgia,serif;font-size:20px;color:#547549;font-weight:400;line-height:1.2}.summary-days h4{font-size:13px;font-weight:550;margin:0 0 8px}.summary-route{display:flex;align-items:center;flex-wrap:wrap;gap:7px;font-size:12px;color:#7f8f79;margin:0;line-height:1.7}.summary-route .chat-icon{width:12px;height:12px;transform:rotate(-90deg)}.summary-bottom{border-top:1px solid #e8eee5;padding:11px 18px;display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:10px;color:#8a9988}.summary-bottom>div{display:flex;gap:12px}.summary-bottom button{display:flex;align-items:center;gap:6px;border:0;background:none;color:#50816f;font:inherit;cursor:pointer}.summary-bottom .chat-icon{width:13px;height:13px;transform:rotate(-90deg)}@media(max-width:600px){.summary-head{padding:15px 13px}.summary-head h3{font-size:14px}.summary-head p{font-size:10px}.summary-days{padding-inline:13px}.summary-days>li{gap:10px}.summary-bottom{padding:10px 13px;flex-wrap:wrap}.draft-pill{font-size:9px}}
</style>
