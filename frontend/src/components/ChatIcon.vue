<script setup lang="ts">
/** 对话图标：复用确认稿的线条图标，按钮的文字标签由调用方提供。 */
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  drive: 'm4 4-2 9v6a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1v-6l-2-9ZM2 13h20M6 17h.01M10 17h.01',
  folder: 'M3 7V4h6l2 3h10v13H3ZM3 7h18',
  status: 'M9 5h12M9 12h12M9 19h12M2 4l2 2 2-3M2 11l2 2 2-3M2 18l2 2 2-3',
  atom: 'M12 12h.01M20 12c0 3-3.6 5.5-8 5.5S4 15 4 12 7.6 6.5 12 6.5s8 2.5 8 5.5ZM16 5c2.6 1.5 2.9 5.9.7 9.7s-6.2 5.8-8.8 4.3S5 13.1 7.2 9.3 13.4 3.5 16 5ZM8 5c2.6-1.5 6.6.5 8.8 4.3s1.9 8.2-.7 9.7-6.6-.5-8.8-4.3S5.4 6.5 8 5Z',
  book: 'M12 5C8 3 5 3 2 5v15c4-2 7-2 10 0 3-2 6-2 10 0V5c-3-2-6-2-10 0ZM12 5v15',
  map: 'm2 6 6-3 8 3 6-3v15l-6 3-8-3-6 3ZM8 3v15M16 6v15',
  route: 'M4 7h14m-4-4 4 4-4 4M20 17H6m4-4-4 4 4 4',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c-5 5-5 13 0 18 5-5 5-13 0-18Z',
  chart: 'M3 3v18h18M7 16v-5M12 16V7M17 16V4',
  layers: 'm12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 16l10 5 10-5',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 6v6l4 2',
  check: 'm5 12 4 4L19 6',
  download: 'M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6',
  upload: 'M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6',
  expand: 'M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5',
  back: 'M20 12H4m6-6-6 6 6 6',
  refresh: 'M20 7a8 8 0 1 0 .5 9M20 3v5h-5',
  external: 'M14 3h7v7M10 14 21 3M10 3H3v18h18v-7',
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
