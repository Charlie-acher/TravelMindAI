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

/** 读取HTTP响应；后端失败时只显示统一错误消息，不把整份响应拼进页面。 */
async function readResponse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const message = body?.error?.message || `服务请求失败（HTTP ${response.status}）`
    throw new Error(message)
  }
  if (body === null) throw new Error('服务返回了无法读取的数据，请重试。')
  return body as T
}

/** 查询配置状态只读取后端配置标志，不向DeepSeek发送收费请求。 */
export async function getModelStatus(): Promise<{ configured: boolean; model: string }> {
  return readResponse(await fetch('/api/v1/requirements/status'))
}

/** 带上上一轮需求；失败时调用方保留旧表格，只有成功响应才替换页面状态。 */
export async function sendRequirement(
  message: string,
  previous: TravelRequirement | null,
  referenceDate: string | null,
): Promise<ChatResponse> {
  const controller = new AbortController()
  // 给首次抽取和一次修复留出时间；浏览器等待超过90秒时停止等待并提示。
  const timeout = window.setTimeout(() => controller.abort(), 90_000)
  try {
    return await readResponse(await fetch('/api/v1/requirements/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, previous, reference_date: referenceDate }),
      signal: controller.signal,
    }))
  } catch (error) {
    if (controller.signal.aborted) throw new Error('等待回复超时，原需求已保留，请稍后重试。')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}
