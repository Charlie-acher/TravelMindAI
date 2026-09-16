<script setup lang="ts">
/** 景点展示层：展示本轮已保存的介绍、门票参考和定位，不在浏览器重新查询。 */
import type { AttractionCard } from '../api/documents'

defineProps<{ items: AttractionCard[] }>()
const ticketLabels = { unknown: '门票待核实', free: '免费范围参考', paid: '收费参考', partial: '部分收费参考' }
const locationLabels = {
  found: '地点已匹配', no_match: '暂未匹配地点', ambiguous: '存在同名地点，待核实',
  unconfigured: '地图定位尚未启用', error: '本次定位查询失败',
}

/** 地图链接函数：只使用有效工具坐标，在高德网页显示该位置；入口优先于中心。 */
function mapLink(item: AttractionCard): string | undefined {
  if (item.location.status !== 'found') return undefined
  const point = item.location.entrance ?? item.location.location
  if (!point) return undefined
  const params = new URLSearchParams({
    position: `${point.longitude},${point.latitude}`, name: item.name,
    coordinate: 'gaode', callnative: '0', src: 'TravelMindAI',
  })
  return `https://uri.amap.com/marker?${params}`
}
</script>

<template>
  <div v-if="items.length" class="attraction-cards" aria-label="本轮推荐景点">
    <article v-for="item in items" :key="`${item.city}-${item.name}`" class="attraction-card">
      <header><h3>{{ item.name }}</h3><span>{{ item.city }}</span></header>
      <p class="attraction-description">{{ item.description }}</p>
      <p class="attraction-reason">{{ item.reason }}</p>
      <div v-if="item.ticket.status === 'paid' || item.ticket.status === 'partial'" class="attraction-ticket">
        <strong>{{ ticketLabels[item.ticket.status] }}</strong>
        <p>{{ item.ticket.summary }}</p>
        <small>公开信息参考，具体以出行日期及适用票种为准。</small>
      </div>
      <footer>
        <div><span>详细地点：{{ item.location.address || item.address_evidence?.text || `${item.city} · ${item.name}（详细地址补查未成功）` }}</span>
          <small>{{ item.location.match_kind === 'administrative' ? '行政镇中心，非景点入口' : item.address_evidence && !item.location.address ? '地址来自已核对原文；地图定位尚未匹配' : locationLabels[item.location.status] }}</small>
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
.attraction-ticket { background: #f6f8f3; padding: 11px 12px; border-radius: 8px; }
.attraction-ticket strong { color: #6c7248; font-size: 12px; }
.attraction-ticket p { margin: 5px 0; }
footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 13px; font-size: 12px; }
footer small { display: block; margin-top: 5px; }
footer a { flex-shrink: 0; color: #246e50; text-decoration: underline; text-underline-offset: 3px; }
footer a:focus-visible { outline: 2px solid #246e50; outline-offset: 4px; }
</style>
