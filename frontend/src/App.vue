<script setup lang="ts">
/** M2简易对话页：负责当前页面的聊天状态和交互，后端负责抽取、合并和校验。 */
import { nextTick, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Card as ACard, Tag as ATag, Textarea as ATextarea } from '@arco-design/web-vue'
import { getModelStatus, sendRequirement, type ChatResponse } from './api/requirements'
import RequirementPanel from './components/RequirementPanel.vue'

interface ChatMessage { id: number; role: 'user' | 'assistant'; text: string; failed?: boolean }
const welcome = '你好，想去哪里旅行？\n告诉我你的时间、人数和预算，也可以先说一个想法，我们一起补充。'
const messages = ref<ChatMessage[]>([{ id: 0, role: 'assistant', text: welcome }])
const input = ref('')
const busy = ref(false)
const error = ref('')
const modelState = ref<'checking' | 'configured' | 'missing' | 'offline'>('checking')
const modelName = ref('DeepSeek')
const response = ref<ChatResponse | null>(null)
const referenceDate = ref<string | null>(null)
const chatArea = ref<HTMLElement | null>(null)
let nextId = 1
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

/** 等Vue完成DOM更新后再滚动，否则新增消息还没有出现在页面高度中。 */
async function scrollToLatest(): Promise<void> {
  await nextTick()
  chatArea.value?.scrollTo({ top: chatArea.value.scrollHeight, behavior: 'smooth' })
}

/** 禁止并发发送；成功后替换需求，失败则保留旧需求和输入内容以便重试。 */
async function send(): Promise<void> {
  const text = input.value.trim()
  if (!text || busy.value || modelState.value !== 'configured') return
  busy.value = true
  error.value = ''
  const message: ChatMessage = { id: nextId++, role: 'user', text }
  messages.value.push(message)
  void scrollToLatest()
  try {
    const result = await sendRequirement(text, response.value?.result.extraction ?? null, referenceDate.value)
    referenceDate.value = result.result.reference_date
    // 闲聊/不支持请求仅显示回复，保持已有需求卡片，避免看起来像修改已成功。
    if (result.status !== 'unsupported') response.value = result
    else if (response.value) response.value = { ...response.value, changed_fields: [] }
    messages.value.push({ id: nextId++, role: 'assistant', text: result.reply })
    input.value = ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '发送失败，请检查网络后重试。'
    // 通过响应式数组取到对应消息再标记，确保Vue能刷新“未成功”的状态。
    const failed = messages.value.find(item => item.id === message.id)
    if (failed) failed.failed = true
  } finally {
    busy.value = false
    void scrollToLatest()
  }
}

/** 中文输入法按回车选词时不发送；普通Enter发送，Shift+Enter换行。 */
function onComposerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    void send()
  }
}

/** 重新开始只清空本页面的临时状态；发送中禁用，避免旧回复落进新对话。 */
function resetConversation(): void {
  if (busy.value) return
  messages.value = [{ id: nextId++, role: 'assistant', text: welcome }]
  response.value = null
  referenceDate.value = null
  input.value = ''
  error.value = ''
}

onMounted(checkModel)
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
        <template #extra><a-button :disabled="busy" @click="resetConversation">重新开始</a-button></template>
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
              <span v-if="message.failed" class="failed-note">本条未成功，原需求已保留</span>
            </div>
          </div>
          <div v-if="busy" class="thinking" role="status"><span class="thinking-dot" />正在理解并检查你的需求…</div>
        </div>
        <div class="composer">
          <a-alert v-if="error" type="error" class="send-error">{{ error }}</a-alert>
          <a-alert v-if="modelState === 'missing'" type="warning" class="send-error">请在后端配置 DeepSeek 密钥并重启服务，然后点击“重新检查”。</a-alert>
          <div class="example-prompts"><span>试着说</span><a-button v-for="example in examples" :key="example" size="mini" :disabled="busy" @click="input = example">{{ example }}</a-button></div>
          <!-- textareaAttrs把可访问标签传给真实textarea，测试和读屏都能找到它。 -->
          <a-textarea v-model="input" :auto-size="{ minRows: 3, maxRows: 6 }" :max-length="6000"
            :disabled="busy" :textarea-attrs="{ 'aria-label': '旅行需求输入' }"
            placeholder="例如：从上海去杭州玩三天，两个人，预算五千元，想轻松一点…"
            @keydown="onComposerKeydown" />
          <div class="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span><a-button type="primary" :loading="busy" :disabled="!input.trim() || modelState !== 'configured'" @click="send">发送</a-button></div>
        </div>
      </a-card>
      <aside class="detail-column"><RequirementPanel :response="response" /><p class="session-notice">当前对话暂存于本页面，刷新会重新开始。<br />本阶段整理旅行需求，尚未生成每日行程。</p></aside>
    </main>
  </div>
</template>
