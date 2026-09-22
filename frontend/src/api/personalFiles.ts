/** 个人文件接口层：跨会话查询当前账号文件，详情和下载仍由服务端逐次检查归属。 */
import type { AttachmentView } from './attachments'
import { attachmentContentUrl } from './attachments'
import type { PlanSnapshot, TravelRequirement } from './requirements'
import { apiFetch, readResponse } from './http'

export type PersonalFileKind = 'attachment' | 'itinerary'
export interface PersonalFileItem {
  id: string; kind: PersonalFileKind; file_name: string; mime_type: string
  size_bytes: number | null; session_id: string; session_title: string; created_at: string
  version: number | null; version_count: number | null
}
export interface PersonalFilePage { items: PersonalFileItem[]; total: number; offset: number; limit: number }
export interface PersonalFileDetail {
  item: PersonalFileItem; attachment: AttachmentView | null
  itinerary: PlanSnapshot | null; extraction: TravelRequirement | null; versions: PersonalFileItem[]
}

/** 分页查询函数：不把全部文件先下载到浏览器，筛选与总数都来自数据库。 */
export async function listPersonalFiles(options: {
  sessionId?: string | null; kind?: PersonalFileKind | ''; q?: string; offset?: number; limit?: number
}): Promise<PersonalFilePage> {
  const query = new URLSearchParams({ offset: String(options.offset ?? 0), limit: String(options.limit ?? 30) })
  if (options.sessionId) query.set('session_id', options.sessionId)
  if (options.kind) query.set('kind', options.kind)
  if (options.q?.trim()) query.set('q', options.q.trim())
  return readResponse(await apiFetch(`/api/v1/personal-files?${query}`, { cache: 'no-store' }))
}

/** 文件详情函数：版本编号始终使用行程UUID，不用标题或文件名匹配。 */
export async function getPersonalFile(item: Pick<PersonalFileItem, 'id' | 'kind'>): Promise<PersonalFileDetail> {
  return readResponse(await apiFetch(`/api/v1/personal-files/${item.kind}/${encodeURIComponent(item.id)}`, { cache: 'no-store' }))
}

/** 下载地址函数：原件沿用原有受保护地址，行程由服务器按所选版本即时生成。 */
export function personalFileDownloadUrl(item: PersonalFileItem): string {
  return item.kind === 'attachment' ? attachmentContentUrl(item.session_id, item.id)
    : `/api/v1/personal-files/itinerary/${encodeURIComponent(item.id)}/download`
}
