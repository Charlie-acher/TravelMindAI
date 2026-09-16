/** Vite负责本地开发和打包；浏览器访问/api时由开发服务器转发到FastAPI。 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 固定本机端口，端口被占用就报错，避免悄悄换端口后访问错页面。
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 保留浏览器访问的Host，后端才能核对Origin并拒绝跨站写请求。
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
  // 预览打包结果时也可连接本地后端；正式部署需要配置同源反向代理。
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
})
