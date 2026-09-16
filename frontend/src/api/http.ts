/** HTTP公共层：读取后端JSON和统一错误，供对话及资料接口共用。 */
/** 保留HTTP状态，页面遇到409时可以先恢复历史，而不是盲目重复提交旧版本。 */
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

// 退出或新登录后，旧账号的在途请求不能撤销新账号的登录态。
let authGeneration = 0
/** 身份切换函数：让身份切换前发送的401失效。 */
export function advanceAuthGeneration(): void { ++authGeneration }

/** 请求入口：写入来源标记；登录失效立即通知页面清除私人内容。 */
export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const generation = authGeneration
  const headers = new Headers(init.headers)
  if (init.method && !['GET', 'HEAD'].includes(init.method.toUpperCase())) headers.set('X-Requested-With', 'TravelMindAI')
  const response = await fetch(input, { ...init, headers, credentials: 'same-origin' })
  if (response.status === 401 && generation === authGeneration) window.dispatchEvent(new Event('travelmind:unauthorized'))
  return response
}

/** 读取HTTP响应；后端失败时只显示统一错误消息，不把整份响应拼进页面。 */
export async function readResponse<T>(response: Response, allowNull = false): Promise<T> {
  // 后台任务尚未创建时允许JSON null；非法JSON仍必须和这个正常状态区分。
  if (response.status === 204) return null as T
  let validJson = true
  const body = await response.json().catch(() => { validJson = false; return null })
  if (!response.ok) {
    const message = body?.error?.message || `服务请求失败（HTTP ${response.status}）`
    throw new ApiError(message, response.status)
  }
  if (!validJson || (body === null && !allowNull)) throw new Error('服务返回了无法读取的数据，请重试。')
  return body as T
}

