<script setup lang="ts">
/** 过程展示层：默认以呼吸灯长条展示最新步骤，按需展开真实过程。 */
defineProps<{ steps: { stage: string; message: string }[]; running?: boolean; seconds?: number }>()
</script>

<template>
  <details class="stream-progress">
    <summary>
      <span class="thinking-dot" :class="{ running }" />
      <span class="progress-label" role="status">{{ running ? steps.at(-1)?.message || '正在连接旅行助手…' : '已完成' }}<span v-if="!running && seconds !== undefined">（用时 {{ seconds }} 秒）</span></span>
      <span class="progress-toggle">查看过程</span>
    </summary>
    <div class="process-content">
      <ol v-if="steps.length"><li v-for="(step, index) in steps" :key="index" :class="{ current: running && index === steps.length - 1 }">{{ step.message }}</li></ol>
      <p v-else>正在连接旅行助手…</p>
    </div>
  </details>
</template>

<style scoped>
/* 样式由组件自己持有，避免父页面的scoped样式无法作用到内部元素。 */
.stream-progress { padding: 11px 13px; border: 1px solid #deebe7; background: #f6fbfa; border-radius: 12px; font-size: 12px; color: #577574; }
summary { display: flex; align-items: center; gap: 9px; cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
.thinking-dot { width: 7px; height: 7px; border-radius: 50%; background: #76a18a; flex-shrink: 0; }
.thinking-dot.running { animation: pulse 1s ease-in-out infinite alternate; }
.progress-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.progress-toggle { margin-left: auto; white-space: nowrap; color: #7c9596; font-size: 11px; }
ol { margin: 15px 0 2px 3px; padding: 0 0 0 17px; border-left: 1px solid #cfe2dc; list-style: none; }
li { position: relative; padding: 0 0 12px; line-height: 1.6; overflow-wrap: anywhere; }
li::before { content: ''; position: absolute; left: -21px; top: 6px; width: 7px; height: 7px; background: #b7d4ca; border-radius: 50%; }
li:last-child { padding-bottom: 0; }
li.current { color: #276e62; }
li.current::before { background: #4b9b88; }
p { margin: 12px 0 0; line-height: 1.6; }
@keyframes pulse { to { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .thinking-dot.running { animation: none; } }
</style>
