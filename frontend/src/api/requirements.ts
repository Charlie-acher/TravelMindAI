/** 对话HTTP契约与请求封装；只访问自己的后端，不在前端保存DeepSeek密钥。 */
import type { AnswerResult } from './documents'
import { readResponse } from './http'
export interface TravelRequirement {
  intent: string
  destination: string | null
  origin: string | null
  start_date: string | null
  end_date: string | null
  days: number | null
  travelers: number | null
  total_budget: string | null
  pace: 'relaxed' | 'balanced' | 'intensive' | null
  interests: string[]
  dietary: string[]
  lodging_preferences: string[]
  hard_constraints: string[]
  excluded_items: string[]
  assumptions: string[]
}

/** 一次对话响应：文字回复用于左边聊天，结构化需求用于右边卡片。 */
export interface ChatResponse {
  result: {
    extraction: TravelRequirement
    original_message: string
    reference_date: string
    missing_required_fields: string[]
    clarification: string | null
    message_intent: string | null
  }
  reply: string
  status: 'needs_clarification' | 'complete' | 'unsupported' | 'knowledge'
  changed_fields: string[]
  request_id: string
  knowledge?: AnswerResult | null // 历史响应可没有；新主聊天保存回答与引用原文快照。
}

/** 查询配置状态只读取后端配置标志，不向DeepSeek发送收费请求。 */
export async function getModelStatus(): Promise<{ configured: boolean; model: string }> {
  return readResponse(await fetch('/api/v1/requirements/status'))
}

/** 一轮持久化对话；编号用于安全重试，revision用于防止覆盖更新过的历史。 */
export interface SavedTurn { message_id: string; revision: number; response: ChatResponse }
export interface ConversationHistory { session_id: string; revision: number; turns: SavedTurn[] }
export interface PendingMessage { message: string; message_id: string; expected_revision: number }

/** 沿用M1的会话创建接口；第一次发送时才创建，空白页面不会自动产生记录。 */
export async function createConversation(): Promise<string> {
  const result = await readResponse<{ session: { id: string } }>(await fetch('/api/v1/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '旅行需求对话' }),
  }))
  return result.session.id
}

/** 恢复只读取自己的后端数据库，不调用模型；不缓存历史，避免展示旧版本。 */
export async function readConversation(sessionId: string): Promise<ConversationHistory> {
  return readResponse(await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages`, {
    cache: 'no-store',
  }))
}

/** 仅提交本条消息，旧需求和参考日期都由后端从数据库取得。 */
export async function sendRequirement(
  sessionId: string,
  pending: PendingMessage,
): Promise<SavedTurn> {
  const controller = new AbortController()
  // 给需求抽取、检索和资料回答留出时间；超时后沿用原消息编号查是否已保存。
  const timeout = window.setTimeout(() => controller.abort(), 180_000)
  try {
    return await readResponse(await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pending),
      signal: controller.signal,
    }))
  } catch (error) {
    if (controller.signal.aborted) throw new Error('等待回复超时，请重新读取确认是否已保存，或使用原消息重试。')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}
