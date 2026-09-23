/** 私人附件接口层：上传原件和读取元数据；识别只在发送聊天时执行。 */
import { apiFetch, readResponse } from './http'

export interface AttachmentAnalysis {
  city: string | null; summary: string
  waypoints: { name: string; order: number | null; evidence: string; needs_confirmation: boolean }[]
  warnings: string[]; parser_version: string | null
}
export interface AttachmentSnapshot {
  id: string; file_name: string; analysis: AttachmentAnalysis | null; error_message: string | null
  size_bytes?: number | null
}
export interface AttachmentView extends AttachmentSnapshot {
  session_id: string; mime_type: string; size_bytes: number
  status: 'uploaded' | 'ready' | 'needs_confirmation' | 'failed'; created_at: string
}
export const attachmentAccept = '.png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md,.markdown'

/** 原件地址函数：同源Cookie交由服务器验证账号与会话归属。 */
export function attachmentContentUrl(sessionId: string, id: string, preview = false): string {
  return `/api/v1/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(id)}/content${preview ? '?preview=true' : ''}`
}

/** 上传函数：只发送原件，文件内容及最终限制由后端校验。 */
export async function uploadAttachment(sessionId: string, file: File): Promise<AttachmentView> {
  const body = new FormData()
  body.append('file', file)
  return readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/attachments`, {
    method: 'POST', body, signal: AbortSignal.timeout(60_000),
  }))
}

/** 列表函数：刷新后补齐待重试附件的名称，不重复上传或识别。 */
export async function listAttachments(sessionId: string): Promise<AttachmentView[]> {
  return readResponse(await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/attachments`, { cache: 'no-store' }))
}

/** 用途随消息保存，同一文件可以在后续消息中改为另一种用途。 */
export interface AttachmentUse {
  mode: 'read' | 'reference' | 'required' | 'replace' | 'unclear'
  apply_to_plan: boolean
  target_days: number[]
  target_places?: string[]
}
