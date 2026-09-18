<script setup lang="ts">
/** 景点展示层：展示本轮已保存的介绍、门票参考和定位，不在浏览器重新查询。 */
import type { AttractionCard } from '../api/documents'
import { baiduMapLink } from '../mapLink'

defineProps<{ items: AttractionCard[] }>()
const ticketLabels = { unknown: '门票待核实', free: '免费范围参考', paid: '收费参考', partial: '部分收费参考' }
const locationLabels = {
  found: '地点已匹配', no_match: '暂未匹配地点', ambiguous: '存在同名地点，待核实',
  unconfigured: '地图定位尚未启用', error: '本次定位查询失败',
  not_requested: '未查询地图',
}

/** 地图链接函数：只使用有效工具坐标，在百度网页显示该位置；入口优先于中心。 */
function mapLink(item: AttractionCard): string | undefined {
  if (item.location.status !== 'found') return undefined
  const point = item.location.entrance ?? item.location.location
  if (!point) return undefined
  return baiduMapLink(point, item.name, item.location.address)
}
</script>

<template>
  <div v-if="items.length" class="attraction-cards" aria-label="本轮推荐景点">
    <article v-for="(item, index) in items" :key="`${item.city}-${item.name}`" class="attraction-card" :class="{ 'attraction-brief': item.location.status === 'not_requested' }">
      <header><h3><span v-if="item.location.status === 'not_requested'" class="place-number">{{ String(index + 1).padStart(2, '0') }}</span>{{ item.name }}<sup v-if="item.source_ids.length" :aria-label="`参考资料 ${item.source_ids.map(id => `[${id}]`).join(' ')}`">{{ item.source_ids.map(id => `[${id}]`).join(' ') }}</sup></h3><span class="place-city">{{ item.city }}</span></header>
      <p class="attraction-description">{{ item.description }}</p>
      <p v-if="item.reason.trim() !== item.description.trim()" class="attraction-reason">{{ item.reason }}</p>
      <div v-if="item.ticket.status === 'paid' || item.ticket.status === 'partial'" class="attraction-ticket">
        <strong>{{ ticketLabels[item.ticket.status] }}</strong>
        <p>{{ item.ticket.summary }}</p>
        <small>公开信息参考，具体以出行日期及适用票种为准。</small>
      </div>
      <footer v-if="item.location.status !== 'not_requested' || item.address_evidence">
        <div><span>详细地点：{{ item.location.address || item.address_evidence?.text || `${item.city} · ${item.name}（详细地址补查未成功）` }}</span>
          <small>{{ item.location.status === 'not_requested' ? '地址来自参考原文，未查询地图' : item.location.match_kind === 'administrative' ? '行政镇中心，非景点入口' : item.address_evidence && !item.location.address ? '地址来自已核对原文；地图定位尚未匹配' : locationLabels[item.location.status] }}</small>
          <small v-if="item.location.matched_name && item.location.matched_name !== item.name">地图名称：{{ item.location.matched_name }}</small></div>
        <a v-if="mapLink(item)" :href="mapLink(item)" target="_blank" rel="noopener noreferrer">查看地图 ↗</a>
      </footer>
    </article>
  </div>
</template>

<style scoped>
/* 卡片随回答排布；长地址换行，窄屏不撑破聊天窗口。 */
.attraction-cards { display: grid; gap: 12px; margin-top: 12px; max-width: 100%; }
.attraction-card { padding: 17px; border: 1px solid #dbe7df; border-radius: 12px; background: #fff; overflow-wrap: anywhere; }
header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
h3 { margin: 0; color: #245846; font-size: 16px; }
header span, small { color: #798b80; font-size: 11px; }
p { margin: 9px 0; line-height: 1.8; font-size: 13px; }
.attraction-description { color: #43594c; }
.attraction-reason { color: #327458; }
.attraction-brief { padding: 16px 0; border: 0; border-bottom: 1px solid #e6ece9; border-radius: 0; background: transparent; }
.attraction-brief:last-child { border-bottom: 0; }
.attraction-brief h3 { color: #293b35; font-size: 16px; line-height: 1.7; }
.attraction-brief p { color: #394943; font-size: 14px; line-height: 1.85; margin: 6px 0 0; }
.attraction-brief .attraction-reason { color: #5d7067; font-size: 13px; }
.place-number { font-size: 12px; color: #74978a; margin-right: 12px; font-weight: 500; }
.place-city { padding: 2px 7px; border-radius: 5px; background: #eef4f0; }
sup { font-size: 10px; color: #718d80; font-weight: 400; margin-left: 6px; }
.attraction-ticket { background: #f6f8f3; padding: 11px 12px; border-radius: 8px; }
.attraction-ticket strong { color: #6c7248; font-size: 12px; }
.attraction-ticket p { margin: 5px 0; }
footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 13px; font-size: 12px; }
footer small { display: block; margin-top: 5px; }
footer a { flex-shrink: 0; color: #246e50; text-decoration: underline; text-underline-offset: 3px; }
footer a:focus-visible { outline: 2px solid #246e50; outline-offset: 4px; }
</style>
