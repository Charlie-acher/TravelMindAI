/** 对话HTTP契约与请求封装；只访问自己的后端，不在前端保存DeepSeek密钥。 */
import type { AnswerResult, GeoPoint, MapLookup } from './documents'
import { ApiError, apiFetch, readResponse } from './http'
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
  dining?: DiningResult | null // 餐馆及评分来自地图查询，随最终回答一起保存。
}

/** 餐馆结果类型：旧历史可没有；距离是地图直线距离，人均仅作参考。 */
export interface DiningItem {
  poi_id: string; name: string; address: string | null; location: GeoPoint
  rating: number; distance_m: number | null; reference_cost: string | null
}
export interface DiningResult {
  status: 'found' | 'empty' | 'ratings_unavailable' | 'unconfigured' | 'error' | 'needs_clarification'
  items: DiningItem[]; anchor: MapLookup | null; radius_m: number
  preference: string | null; clarification: string | null
  checked_at: string; provider: 'amap'; rating_missing_count: number
}

/** 过程事件类型：只展示后端实际阶段和公开回答草稿，不接收推理或原始模型JSON。 */
export type RequirementUpdate =
  | { event: 'progress'; data: { stage: string; message: string } }
  | { event: 'draft'; data: { text: string } }
  | { event: 'reset'; data: Record<string, never> }

/** 流读取函数：拼接UTF-8字节和完整事件；只有done才代表本轮已经保存。 */
export async function readRequirementStream(
  body: ReadableStream<Uint8Array>,
  onUpdate: (update: RequirementUpdate) => void,
): Promise<SavedTurn> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = ''
  let event = ''
  let data: string[] = []
  let saved: SavedTurn | null = null

  /** 事件分发函数：多行data以换行连接，心跳注释不产生界面进度。 */
  function dispatch(): void {
    if (!data.length || saved) { event = ''; data = []; return }
    if (!['progress', 'draft', 'reset', 'done', 'error'].includes(event)) { event = ''; data = []; return }
    let payload
    try { payload = JSON.parse(data.join('\n')) }
    catch { throw new Error('服务返回了无法读取的回复，请重新读取对话后重试。') }
    if (!payload || typeof payload !== 'object') throw new Error('回复事件格式不正确，请重新读取对话后重试。')
    if (event === 'done') {
      if (typeof payload.message_id !== 'string' || !Number.isInteger(payload.revision) || !payload.response?.result || typeof payload.response.reply !== 'string') throw new Error('完整回复缺少必要信息，请重新读取对话后重试。')
      saved = payload as SavedTurn
    }
    else if (event === 'error') throw new ApiError(payload.message, payload.status)
    else if (event === 'progress' || event === 'draft' || event === 'reset') onUpdate({ event, data: payload } as RequirementUpdate)
    event = ''; data = []
  }

  try {
    while (!saved) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value, { stream: !chunk.done })
      // 先按完整行处理；块末尾的CR等待下一块，避免把CRLF误认作两次换行。
      let newline: RegExpExecArray | null
      while ((newline = /\r\n|\r|\n/.exec(buffer))) {
        if (!chunk.done && newline[0] === '\r' && newline.index === buffer.length - 1) break
        const line = buffer.slice(0, newline.index)
        buffer = buffer.slice(newline.index + newline[0].length)
        if (!line) dispatch()
        else if (!line.startsWith(':')) {
          const colon = line.indexOf(':')
          const field = colon < 0 ? line : line.slice(0, colon)
          const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
          if (field === 'event') event = value
          else if (field === 'data') data.push(value)
        }
        if (saved) break
      }
      if (chunk.done) break
    }
    if (!saved) throw new Error('回复连接已中断，尚未确认保存。请重新读取对话，或使用原消息重试。')
    return saved
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

/** 查询配置状态只读取后端配置标志，不向DeepSeek发送收费请求。 */
export async function getModelStatus(): Promise<{ configured: boolean; model: string }> {
  return readResponse(await apiFetch('/api/v1/requirements/status'))
}

/** 一轮持久化对话；编号用于安全重试，revision用于防止覆盖更新过的历史。 */
export interface SavedTurn { message_id: string; revision: number; response: ChatResponse }
export interface ConversationHistory { session_id: string; revision: number; turns: SavedTurn[] }
export interface PendingMessage { message: string; message_id: string; expected_revision: number }
export interface SessionSummary { id: string; thread_id: string; title: string; status: string; created_at: string; updated_at: string }
export interface SessionPage { items: SessionSummary[]; next_cursor: string | null }
/** 历史列表来自当前账号的服务端分页，浏览器不负责用户隔离。 */
export async function listSessions(cursor: string | null = null): Promise<SessionPage> {
  const params = new URLSearchParams({ limit: '30' })
  if (cursor) params.set('cursor', cursor)
  return readResponse(await apiFetch(`/api/v1/sessions?${params}`, { cache: 'no-store' }))
}

/** 沿用M1的会话创建接口；第一次发送时才创建，空白页面不会自动产生记录。 */
export async function createConversation(): Promise<string> {
  const result = await readResponse<{ session: { id: string } }>(await apiFetch('/api/v1/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '新建对话' }),
  }))
  return result.session.id
}

/** 恢复只读取自己的后端数据库，不调用模型；不缓存历史，避免展示旧版本。 */
export async function readConversation(sessionId: string): Promise<ConversationHistory> {
  return readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages`, {
    cache: 'no-store',
  }))
}

/** 仅提交本条消息，旧需求和参考日期都由后端从数据库取得。 */
export async function sendRequirement(
  sessionId: string,
  pending: PendingMessage,
  onUpdate: (update: RequirementUpdate) => void,
): Promise<SavedTurn> {
  const controller = new AbortController()
  // 给需求抽取、检索和资料回答留出时间；超时后沿用原消息编号查是否已保存。
  const timeout = window.setTimeout(() => controller.abort(), 180_000)
  try {
    const response = await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement-messages/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(pending),
      signal: controller.signal,
    })
    if (!response.ok) return await readResponse(response)
    if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('服务没有返回可读取的回复流，请重新读取对话后重试。')
    return await readRequirementStream(response.body, onUpdate)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('等待回复超时，请重新读取确认是否已保存，或使用原消息重试。')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}
