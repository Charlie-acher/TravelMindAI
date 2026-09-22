/** 工作台接口层：仅请求当前用户所选会话的实际调用统计。 */
import { apiFetch, readResponse } from './http'

export interface ContextUsage {
  provider: string; model: string; input_tokens: number | null; context_window: number | null
  ratio: number | null; started_at: string; purpose: string
}
export interface WorkspaceStatusData {
  total_calls: number; known_total_tokens: number; unknown_usage_calls: number; cache_hit_ratio: number | null
  latest_context: ContextUsage | null; contexts?: ContextUsage[]
  models: { provider: string; model: string; calls: number; input_tokens: number | null; output_tokens: number | null; cache_read_tokens: number | null; unknown_usage_calls: number }[]
  recent_calls: { id: string; provider: string; model: string; kind: string; purpose: string; state: string; started_at: string; input_tokens: number | null; output_tokens: number | null; elapsed_seconds: number | null }[]
  unattributed_history: boolean
}

/** 状态读取函数：归属校验和统计口径由服务端统一处理。 */
export async function getWorkspaceStatus(sessionId: string): Promise<WorkspaceStatusData> {
  return readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/workspace-status`, { cache: 'no-store' }))
}
