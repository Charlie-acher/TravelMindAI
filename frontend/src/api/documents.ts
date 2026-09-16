/** 资料接口合同：文件交给自己的后端，保存后由后台自动切片并调用向量服务建立索引。 */
import { apiFetch, readResponse } from './http'

export interface ParsedSection {
  text: string
  page_number: number | null
  section_path: string[]
  order: number
}

/** 列表摘要不包含全文；只有点击资料后才请求sections，减少列表传输体积。 */
export interface DocumentMetadata {
  city: string | null
  category: '住宿' | '景点' | '餐馆' | null
}

export interface DocumentSummary extends DocumentMetadata {
  id: string
  file_name: string
  mime_type: string
  size_bytes: number
  content_hash: string
  status: 'parsed' | 'failed' | 'deleting'
  error_message: string | null
  section_count: number
  warnings: string[]
  created_at: string
}

export interface DocumentDetail extends DocumentSummary { sections: ParsedSection[] }
export interface UploadResult { document: DocumentDetail; duplicate: boolean; processing_error: string | null }
export interface DocumentCity { city: string | null; total: number }
export interface DocumentPage { items: DocumentSummary[]; next_cursor: string | null }
export interface DocumentFilters { q?: string; city?: string | null; category?: DocumentMetadata['category']; unclassified_city?: boolean }
/** 后端按全部匹配文件计数，展开后才读取该城市的游标页。 */
export async function listDocumentCities(filters: DocumentFilters): Promise<DocumentCity[]> {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.city) params.set('city', filters.city)
  if (filters.category) params.set('category', filters.category)
  const result = await readResponse<{ items: DocumentCity[] }>(await apiFetch(`/api/v1/admin/documents/cities?${params}`, { cache: 'no-store' }))
  return result.items
}
export async function pageDocuments(filters: DocumentFilters, cursor: string | null = null): Promise<DocumentPage> {
  const params = new URLSearchParams({ limit: '30' })
  if (cursor) params.set('cursor', cursor)
  if (filters.q) params.set('q', filters.q)
  if (filters.city) params.set('city', filters.city)
  if (filters.category) params.set('category', filters.category)
  if (filters.unclassified_city) params.set('unclassified_city', 'true')
  return readResponse(await apiFetch(`/api/v1/admin/documents/page?${params}`, { cache: 'no-store' }))
}

/** 后台任务类型：已保存的任务状态可在离开页面后继续查询。 */
export interface DocumentJobState {
  run_id: string; kind: 'parse' | 'index'
  status: 'queued' | 'running' | 'completed' | 'failed' | 'paused'
  indexed: number; total: number; error_message: string | null
}

/** 批量进度查询函数：只读任务，不触发新的模型调用。 */
export async function readDocumentJob(id: string): Promise<DocumentJobState | null> {
  return readResponse(await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}/job`, { cache: 'no-store' }), true)
}

/** 索引重试函数：续建已有片段，后端保证同一资料不会重复执行。 */
export async function startDocumentIndex(id: string): Promise<DocumentJobState> {
  return readResponse(await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}/job`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'index' }),
  }))
}

/** 片段数据类型：包含固定编号和原文位置；字符位置按后端Unicode计数。 */
export interface DocumentChunk extends ParsedSection {
  id: string
  document_id: string
  section_order: number
  start_char: number
  end_char: number
}

/** 片段分页类型：total为全部数量，items只包含当前页。 */
export interface DocumentChunkPage { total: number; items: DocumentChunk[] }

/** 取得某份资料的已保存正文；encodeURIComponent只负责URL编码，不替代后端归属检查。 */
export async function readDocument(id: string): Promise<DocumentDetail> {
  return readResponse(await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}`, { cache: 'no-store' }))
}

/** 标签更新函数：空白清除人工标签，未提交字段保持原值，不触发向量模型。 */
export async function updateDocumentMetadata(id: string, metadata: DocumentMetadata): Promise<DocumentDetail> {
  return readResponse(await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}/metadata`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(metadata),
  }))
}

/** 重新解析接口函数：让后端重读已保存的原文件；正文是否读出仍以返回状态为准。 */
export async function retryDocument(id: string): Promise<DocumentDetail> {
  return readResponse(await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}/retry`, { method: 'POST' }))
}

/** 删除接口函数：204表示清理完成；失败保留后端说明，重试沿用同一资料编号。 */
export async function deleteDocument(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (response.status !== 204) await readResponse(response)
}

/** FormData交给浏览器生成multipart边界，不能手写application/json或缺边界的Content-Type。 */
export async function uploadDocument(file: File): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  return readResponse(await apiFetch('/api/v1/admin/documents?auto_process=true', { method: 'POST', body: form }))
}

/** 片段查询函数：每页读取50段，已保存的片段可以在刷新后恢复。 */
export async function listDocumentChunks(id: string, offset = 0): Promise<DocumentChunkPage> {
  return readResponse(await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}/chunks?limit=50&offset=${offset}`, { cache: 'no-store' }))
}

/** 片段生成函数：请求后端切分已存正文；重复请求返回原有片段的首页。 */
export async function generateDocumentChunks(id: string): Promise<DocumentChunkPage> {
  return readResponse(await apiFetch(`/api/v1/admin/documents/${encodeURIComponent(id)}/chunks`, { method: 'POST' }))
}

/** 命中类型：正文和位置来自数据库，相似度不代表内容正确率。 */
export interface SearchHit { score: number; file_name: string; chunk: DocumentChunk }

/** 地图坐标类型：来自后端验证过的高德数据，不能由页面或模型猜测。 */
export interface GeoPoint { longitude: number; latitude: number; coordinate_system: 'GCJ-02' }
export interface MapLookup {
  city: string; name: string; provider: 'amap'; checked_at: string
  status: 'found' | 'no_match' | 'ambiguous' | 'unconfigured' | 'error'
  poi_id: string | null; address: string | null; reference_cost: string | null
  matched_name?: string | null; match_kind?: 'poi' | 'administrative' | null
  location?: GeoPoint | null; entrance?: GeoPoint | null
}

/** 景点卡片类型：自然介绍与结构化位置随同一轮保存，旧历史可没有卡片。 */
export interface AttractionCard {
  city: string; name: string; description: string; reason: string; source_ids: number[]
  ticket: {
    status: 'unknown' | 'free' | 'paid' | 'partial'; summary: string
    amount: string | null; currency: 'CNY'; ticket_type: string | null
    applicable_date: string | null; basis: 'unknown' | 'reference'
  }
  location: MapLookup
  address_evidence?: { text: string; source_id: number; quote: string } | null
}

/** 问答结果类型：证据保留在后台，页面展示自然回答、景点介绍与查询状态。 */
export interface AnswerResult {
  status: 'answered' | 'insufficient'
  clarification?: string | null
  points: { text: string; source_ids: number[] }[]
  sources: { id: number; hit: SearchHit }[]
  map_lookups?: MapLookup[]
  attractions?: AttractionCard[]
  web_search?: {
    status: 'found' | 'empty' | 'unconfigured' | 'error' | 'not_requested'
    items: { id: number; title: string; url: string; content: string
      fetched_at: string; published_date: string | null }[]
  }
}
