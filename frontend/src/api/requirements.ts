/** 对话HTTP契约与请求封装；只访问自己的后端，不在前端保存DeepSeek密钥。 */
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
  status: 'needs_clarification' | 'complete' | 'unsupported'
  changed_fields: string[]
  request_id: string
}

/** 保留HTTP状态，页面遇到409时可以先恢复历史，而不是盲目重复提交旧版本。 */
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

/** 读取HTTP响应；后端失败时只显示统一错误消息，不把整份响应拼进页面。 */
async function readResponse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const message = body?.error?.message || `服务请求失败（HTTP ${response.status}）`
    throw new ApiError(message, response.status)
  }
  if (body === null) throw new Error('服务返回了无法读取的数据，请重试。')
  return body as T
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
  // 给首次抽取和一次修复留出时间；浏览器等待超过90秒时停止等待并提示。
  const timeout = window.setTimeout(() => controller.abort(), 90_000)
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
