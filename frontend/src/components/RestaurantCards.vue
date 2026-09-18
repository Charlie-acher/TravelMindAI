<script setup lang="ts">
/** 餐馆展示层：显示本轮保存的地图商户、公开评分和距离，不补造缺失信息。 */
import type { DiningItem } from '../api/requirements'
import { baiduMapLink } from '../mapLink'

withDefaults(defineProps<{ items: DiningItem[]; category?: 'dining' | 'lodging'; provider?: 'amap' | 'baidu' }>(), { category: 'dining', provider: 'baidu' })

/** 地图链接函数：采用工具返回的坐标，在百度网页打开对应商户的位置。 */
function mapLink(item: DiningItem): string {
  return baiduMapLink(item.location, item.name, item.address)
}
</script>

<template>
  <div class="restaurant-cards" :aria-label="category === 'lodging' ? '本轮推荐住宿' : '本轮推荐餐馆'">
    <article v-for="item in items" :key="item.poi_id" class="restaurant-card">
      <header><h3>{{ item.name }}</h3><span class="restaurant-rating">{{ item.rating.toFixed(1) }} 分</span></header>
      <p>{{ item.address || '详细地址暂未提供' }}</p>
      <div class="restaurant-facts"><span v-if="item.distance_m !== null">距查询地点直线约 {{ Math.round(item.distance_m) }} 米</span><span v-if="item.reference_cost">{{ category === 'lodging' ? '商户参考消费' : '参考人均' }} ¥{{ item.reference_cost }}</span></div>
      <footer><small>{{ provider === 'baidu' ? '百度' : '高德' }}公开评分 · {{ category === 'lodging' ? '参考消费不是每晚房价，房价及空房需向酒店确认' : '人均以到店为准' }}</small><a :href="mapLink(item)" target="_blank" rel="noopener noreferrer">查看地图 ↗</a></footer>
    </article>
  </div>
</template>

<style scoped>
/* 卡片沿用聊天的薄荷色；长店名和地址在手机上自然换行。 */
.restaurant-cards { display: grid; gap: 12px; margin-top: 12px; }
.restaurant-card { border: 1px solid #dbe9e6; border-radius: 12px; padding: 16px; background: #fff; overflow-wrap: anywhere; }
header, footer { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
h3 { margin: 0; color: #245b56; font-size: 16px; }
.restaurant-rating { flex-shrink: 0; background: #edf7f3; color: #30775b; padding: 4px 8px; border-radius: 7px; font-size: 12px; }
p { margin: 10px 0; color: #596e72; line-height: 1.7; font-size: 13px; }
.restaurant-facts { display: flex; flex-wrap: wrap; gap: 7px 14px; color: #527477; font-size: 12px; }
footer { margin-top: 14px; flex-wrap: wrap; }
small { color: #869798; font-size: 11px; }
a { color: #246e68; font-size: 12px; text-underline-offset: 3px; }
</style>
