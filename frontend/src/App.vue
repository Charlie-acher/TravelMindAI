<script setup lang="ts">
/** M2简易对话页：负责当前页面的聊天状态和交互，后端负责抽取、合并和校验。 */
import { onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Card as ACard, Tag as ATag, Textarea as ATextarea } from '@arco-design/web-vue'
import { getModelStatus } from './api/requirements'
import { useRequirementConversation } from './useRequirementConversation'
import RequirementPanel from './components/RequirementPanel.vue'

// 会话管理负责保存/恢复，页面只处理输入法、模型提示和组件显示。
const { messages, input, response, sessionId, revision, busy, restoring, restoreFailed,
  error, storageWarning, chatArea, initialize, reloadConversation,
  send: sendConversation, resetConversation } = useRequirementConversation()
const modelState = ref<'checking' | 'configured' | 'missing' | 'offline'>('checking')
const modelName = ref('DeepSeek')
const examples = ['想去杭州', '三天，两个人，总预算五千元', '改成三个人', '取消不爬山的限制']

/** 查询配置标志，不调用模型；失败时提示检查后端，不伪装成“模型已连接”。 */
async function checkModel(): Promise<void> {
  modelState.value = 'checking'
  try {
    const status = await getModelStatus()
    modelName.value = status.model
    modelState.value = status.configured ? 'configured' : 'missing'
  } catch {
    modelState.value = 'offline'
  }
}

/** 配置检查通过后才发送；会话模块另外防止恢复期间发送和重复点击。 */
async function send(): Promise<void> {
  if (modelState.value === 'configured') await sendConversation()
}

/** 中文输入法按回车选词时不发送；普通Enter发送，Shift+Enter换行。 */
function onComposerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    void send()
  }
}

// 两项操作互不依赖：检查配置不会调用模型，恢复历史只读数据库。
onMounted(() => { void checkModel(); void initialize() })
</script>

<template>
  <div class="app-shell">
    <header class="page-header">
      <div class="brand"><span class="brand-mark">T</span><div><strong>TravelMindAI</strong><span>从一个想法，开始一段旅行</span></div></div>
      <a-tag color="green">旅行需求对话</a-tag>
    </header>
    <main class="workspace">
      <a-card class="chat-panel" :bordered="false">
        <template #title><div class="chat-title">聊聊你的旅行<span>先说需求，随时调整</span></div></template>
        <template #extra><a-button :disabled="busy" @click="reloadConversation">重新读取</a-button><a-button :disabled="busy" @click="resetConversation">重新开始</a-button></template>
        <div class="model-strip">
          <span class="status-dot" :class="modelState" />
          <span v-if="modelState === 'configured'">{{ modelName }} · 已配置</span>
          <span v-else-if="modelState === 'checking'">正在检查服务配置…</span>
          <span v-else-if="modelState === 'missing'">后端尚未配置模型密钥</span>
          <span v-else>暂时无法连接后端服务</span>
          <a-button v-if="modelState === 'offline' || modelState === 'missing'" type="text" size="mini" @click="checkModel">重新检查</a-button>
        </div>
        <div ref="chatArea" class="chat-messages" role="log" aria-label="旅行对话" aria-live="polite">
          <div v-for="message in messages" :key="message.id" class="message-row" :class="message.role">
            <span class="message-avatar">{{ message.role === 'assistant' ? 'T' : '我' }}</span>
            <div class="message-content"><span class="message-name">{{ message.role === 'assistant' ? '旅行助手' : '你' }}</span>
              <div class="message-bubble">{{ message.text }}</div>
              <span v-if="message.failed" class="failed-note">本条未确认保存，可重试或重新读取</span>
            </div>
          </div>
          <div v-if="busy" class="thinking" role="status"><span class="thinking-dot" />{{ restoring ? '正在恢复已保存的对话…' : '正在理解、检查并保存你的需求…' }}</div>
        </div>
        <div class="composer">
          <a-alert v-if="storageWarning" type="warning" class="send-error">{{ storageWarning }}</a-alert>
          <a-alert v-if="error" type="error" class="send-error">{{ error }}</a-alert>
          <a-alert v-if="modelState === 'missing'" type="warning" class="send-error">请在后端配置 DeepSeek 密钥并重启服务，然后点击“重新检查”。</a-alert>
          <div class="example-prompts"><span>试着说</span><a-button v-for="example in examples" :key="example" size="mini" :disabled="busy" @click="input = example">{{ example }}</a-button></div>
          <!-- textareaAttrs把可访问标签传给真实textarea，测试和读屏都能找到它。 -->
          <a-textarea v-model="input" :auto-size="{ minRows: 3, maxRows: 6 }" :max-length="6000"
            :disabled="busy || restoreFailed" :textarea-attrs="{ 'aria-label': '旅行需求输入' }"
            placeholder="例如：从上海去杭州玩三天，两个人，预算五千元，想轻松一点…"
            @keydown="onComposerKeydown" />
          <div class="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span><a-button type="primary" :loading="busy" :disabled="busy || restoreFailed || !input.trim() || modelState !== 'configured'" @click="send">发送</a-button></div>
        </div>
      </a-card>
      <aside class="detail-column"><RequirementPanel :response="response" /><p class="session-notice"><template v-if="sessionId">当前会话已保存 {{ revision }} 轮，刷新本标签页可恢复。<br /></template><template v-else>发送后自动保存，刷新本标签页可继续。<br /></template>当前支持 2～5 天、1～8 人的单目的地旅行。<br />本阶段整理旅行需求，尚未生成每日行程。</p></aside>
    </main>
  </div>
</template>
