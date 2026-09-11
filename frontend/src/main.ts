/** 前端启动入口，相当于后端main.py：加载样式、创建应用、挂载页面。 */
import { createApp } from 'vue'
import '@arco-design/web-vue/dist/arco.css'
import './style.css'
import App from './App.vue'

// 组件在各.vue文件按需导入，避免在入口把整个组件库都注册为全局组件。
createApp(App).mount('#app')
