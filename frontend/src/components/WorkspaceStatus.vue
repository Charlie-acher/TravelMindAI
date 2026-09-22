<script setup lang="ts">
/** 状态展示层：最近输入占用与累计用量分开显示，缺失数据不推算。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getWorkspaceStatus, type WorkspaceStatusData } from '../api/workspace'
import ChatIcon from './ChatIcon.vue'

const props = defineProps<{ sessionId: string | null; refreshKey: number }>()
const data = ref<WorkspaceStatusData | null>(null)
const loading = ref(false)
const error = ref('')
const selectedContext = ref('')
let generation = 0
const contexts = computed(() => data.value?.contexts?.length ? data.value.contexts : data.value?.latest_context ? [data.value.latest_context] : [])
const context = computed(() => contexts.value.find(item => `${item.provider}:${item.model}` === selectedContext.value) ?? data.value?.latest_context ?? null)
const number = (value: number | null | undefined): string => value == null ? '暂不可用' : value.toLocaleString('zh-CN')
const time = (value: string): string => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

/** 状态加载函数：先清空旧会话数据，旧请求返回时不能覆盖新会话。 */
async function load(): Promise<void> {
  const token = ++generation
  data.value = null; error.value = ''; loading.value = !!props.sessionId
  if (!props.sessionId) return
  try {
    const result = await getWorkspaceStatus(props.sessionId)
    if (token === generation) data.value = result
  } catch (cause) { if (token === generation) error.value = cause instanceof Error ? cause.message : '统计暂时无法读取。' }
  finally { if (token === generation) loading.value = false }
}
watch(() => [props.sessionId, props.refreshKey], () => { selectedContext.value = ''; void load() }, { immediate: true })
onBeforeUnmount(() => { ++generation })
</script>

<template>
  <div class="status-panel">
    <p v-if="loading" class="empty" role="status">正在读取会话用量…</p>
    <div v-else-if="error" class="empty" role="alert"><p>{{ error }}</p><button class="retry" @click="load">重新读取</button></div>
    <div v-else-if="!data || !data.total_calls" class="empty"><ChatIcon name="chart" /><p>暂无调用统计</p><small>开始对话后，在这里查看实际用量。</small><p v-if="data?.unattributed_history">旧对话没有可归属的用量记录。</p></div>
    <template v-else>
      <section class="status-card" aria-label="最近调用上下文">
        <div class="card-label"><span>最近调用上下文</span><ChatIcon name="layers" /></div>
        <template v-if="context">
          <select v-if="contexts.length > 1" v-model="selectedContext" aria-label="查看实际调用模型的上下文"><option value="">最近模型调用</option><option v-for="item in contexts" :key="`${item.provider}:${item.model}`" :value="`${item.provider}:${item.model}`">{{ item.model }}</option></select>
          <div class="context-amount"><strong>{{ context.ratio == null ? '暂不可用' : `${(context.ratio * 100).toFixed(1)}%` }}</strong><small v-if="context.context_window != null">{{ number(context.input_tokens) }} / {{ number(context.context_window) }} Token</small></div>
          <div v-if="context.ratio != null" class="meter" role="meter" aria-label="最近输入占模型容量比例" :aria-valuenow="Math.min(100, context.ratio * 100)" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${Math.min(100, context.ratio * 100)}%` }" /></div>
          <p class="context-model">{{ context.model }}</p><p class="muted">输入 {{ number(context.input_tokens) }} Token · {{ time(context.started_at) }}</p>
        </template>
        <p v-else class="muted">暂无可用于上下文统计的模型调用。</p>
        <p class="note">占比 = 最近一次对话相关模型输入 ÷ 模型容量，不是累计消耗。历史按需摘要并受应用输入预算限制；未知容量不显示占比。</p>
      </section>
      <section class="status-card"><div class="card-label">当前会话累计<ChatIcon name="chart" /></div><div class="stats-pair"><div><span>外部调用次数</span><strong>{{ number(data.total_calls) }}</strong></div><div><span>已知 Token 总量</span><strong>{{ number(data.known_total_tokens) }}</strong></div></div><p v-if="data.unknown_usage_calls" class="note">{{ data.unknown_usage_calls }} 次调用未提供 Token 用量（可含地图、搜索服务），未加入已知总量。</p><p class="note">缓存命中 {{ data.cache_hit_ratio == null ? '暂不可用' : `${(data.cache_hit_ratio * 100).toFixed(1)}%` }} · 缓存读取已包含在输入中。</p></section>
      <p v-if="data.unattributed_history" class="note">部分旧记录没有会话归属，无法加入当前统计。</p>
    </template>
  </div>
</template>

<style scoped>
.status-panel{padding:8px 18px 24px;color:#4e6856;font-size:12px}.status-card{border:1px solid #e3eadd;border-radius:10px;padding:17px 16px;margin-bottom:12px;background:#fff}.card-label{display:flex;justify-content:space-between;align-items:center;color:#82927d;font-size:11px}.card-label .chat-icon{width:15px;height:15px}.context-amount{display:flex;gap:8px;align-items:baseline;margin:12px 0}.context-amount strong{font-size:27px;font-weight:550;color:#375b40}.context-amount small{margin-left:auto;color:#8e9c88;font-size:10px}.context-model{overflow-wrap:anywhere;font-size:12px}.muted{font-size:10px;color:#8a9983;line-height:1.7}.meter{height:5px;border-radius:4px;background:#eef3e9;margin:15px 0;overflow:hidden}.meter span{height:100%;display:block;background:#83a674}.note{color:#8c9985;font-size:10px;line-height:1.8;margin:14px 0 0}.stats-pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:18px}.stats-pair span{display:block;font-size:10px;color:#8c9a85;margin-bottom:8px}.stats-pair strong{font-size:18px;font-weight:550}.empty{text-align:center;padding:45px 8px;color:#8b9a84;line-height:1.9}.empty>.chat-icon{height:28px;width:28px}.empty small{font-size:11px}.retry{border:1px solid #d5e3d0;border-radius:6px;padding:7px 12px;background:#f4f8f1;color:#426c3a;cursor:pointer}select{width:100%;margin-top:12px;padding:7px;border:1px solid #dfe8d9;border-radius:5px;background:white;color:#65825a;font:inherit}
</style>
