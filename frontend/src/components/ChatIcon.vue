<script setup lang="ts">
/** 对话图标：复用确认稿的线条图标，按钮的文字标签由调用方提供。 */
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  plus: 'M12 5v14M5 12h14', chat: 'M20 11.5a8 8 0 0 1-8 8H5l-3 2v-7a8 8 0 1 1 18-3ZM8 11h.01M12 11h.01M16 11h.01',
  search: 'M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0Zm-1 5.5 4 4',
  more: 'M5 12h.01M12 12h.01M19 12h.01', edit: 'm14 4 6 6M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15Z',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6',
  file: 'M14 3H5v18h14V8ZM14 3v5h5M8 12h8M8 16h6', arrow: 'M12 20V4m-6 6 6-6 6 6',
  panel: 'M6 4h12a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3ZM9 4v16m7-11-3 3 3 3',
  pin: 'M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0ZM14 10a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z',
  books: 'M3 4h5v16H3ZM8 4h5v16H8Zm6 1 5-1 3 15-5 1ZM5 8h1M10 8h1',
  chevron: 'm8 10 4 4 4-4', close: 'm6 6 12 12M18 6 6 18', logout: 'M10 4H4v16h6M9 12h12m-4-4 4 4-4 4',
}
</script>

<template><svg viewBox="0 0 24 24" aria-hidden="true" class="chat-icon"><path :d="paths[name] || paths.chat" /></svg></template>

<style scoped>
.chat-icon { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.65; stroke-linecap: round; stroke-linejoin: round; flex-shrink: 0; }
</style>
