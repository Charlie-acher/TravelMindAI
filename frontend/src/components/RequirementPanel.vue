<script setup lang="ts">
/** 右侧需求卡片只负责展示，不修改数据、不调用模型。 */
import { computed } from 'vue'
import { Card as ACard, Empty as AEmpty, Tag as ATag } from '@arco-design/web-vue'
import type { ChatResponse, TravelRequirement } from '../api/requirements'

const props = defineProps<{ response: ChatResponse | null }>()
// 字段名和后端一致，标签只负责把技术名字转换成容易理解的中文。
const fields: { key: keyof TravelRequirement; label: string }[] = [
  { key: 'destination', label: '目的地' }, { key: 'origin', label: '出发地' },
  { key: 'days', label: '旅行天数' }, { key: 'travelers', label: '出行人数' },
  { key: 'total_budget', label: '全团预算' }, { key: 'pace', label: '旅行节奏' },
  { key: 'start_date', label: '出发日期' }, { key: 'end_date', label: '结束日期' },
  { key: 'interests', label: '兴趣' }, { key: 'dietary', label: '饮食要求' },
  { key: 'lodging_preferences', label: '住宿偏好' },
  { key: 'hard_constraints', label: '必须遵守' }, { key: 'excluded_items', label: '明确不要' },
]
const requirement = computed(() => props.response?.result.extraction)

/** 将空值、枚举和单位转换成可读文字；“未提及”与“没有限制”含义不同。 */
function displayValue(key: keyof TravelRequirement): string {
  const value = requirement.value?.[key]
  if (value === null || value === undefined) return '未提及'
  if (Array.isArray(value)) return value.length ? value.join('、') : '未提及'
  if (key === 'pace') return { relaxed: '轻松', balanced: '均衡', intensive: '紧凑' }[value as string] || String(value)
  if (key === 'days') return `${value} 天`
  if (key === 'travelers') return `${value} 人`
  if (key === 'total_budget') return `¥ ${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}`
  return String(value)
}
</script>

<template>
  <a-card class="requirement-panel" :bordered="false">
    <template #title>当前旅行需求</template>
    <template #extra>
      <a-tag v-if="response" :color="response.status === 'complete' ? 'green' : 'orange'">
        {{ response.status === 'complete' ? '信息已齐' : '等待补充' }}
      </a-tag>
    </template>
    <div v-if="!response" class="requirement-empty">
      <a-empty description="先聊聊你想去哪儿" />
      <p>提到的条件会出现在这里，<br />没说的信息会留空。</p>
    </div>
    <template v-else>
      <p class="panel-hint">根据对话更新 · 浅绿色标出本轮变化</p>
      <dl class="field-grid">
        <div v-for="field in fields" :key="field.key" class="requirement-field"
          :class="{ changed: response.changed_fields.includes(field.key), wide: Array.isArray(requirement?.[field.key]) }">
          <dt>{{ field.label }}<span v-if="response.result.missing_required_fields.includes(field.key)" class="missing-mark">待补充</span></dt>
          <dd :class="{ muted: displayValue(field.key) === '未提及' }">{{ displayValue(field.key) }}</dd>
        </div>
      </dl>
      <!-- 推导依据单独显示，不能把模型的解释混作用户明确说过的事实。 -->
      <div v-if="requirement?.assumptions.length" class="assumptions">
        <strong>本轮推导说明</strong>
        <ul><li v-for="item in requirement.assumptions" :key="item">{{ item }}</li></ul>
      </div>
      <p class="panel-footnote">参考日期 {{ response.result.reference_date }} · 人民币总预算</p>
      <!-- 原生details为开发验收保留完整结果，默认收起，不干扰普通对话。 -->
      <details class="json-details">
        <summary>查看结构化结果</summary>
        <pre>{{ JSON.stringify(response.result, null, 2) }}</pre>
      </details>
    </template>
  </a-card>
</template>
