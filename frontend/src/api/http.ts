/** HTTP公共层：读取后端JSON和统一错误，供对话及资料接口共用。 */
/** 保留HTTP状态，页面遇到409时可以先恢复历史，而不是盲目重复提交旧版本。 */
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

/** 读取HTTP响应；后端失败时只显示统一错误消息，不把整份响应拼进页面。 */
export async function readResponse<T>(response: Response, allowNull = false): Promise<T> {
  // 后台任务尚未创建时允许JSON null；非法JSON仍必须和这个正常状态区分。
  let validJson = true
  const body = await response.json().catch(() => { validJson = false; return null })
  if (!response.ok) {
    const message = body?.error?.message || `服务请求失败（HTTP ${response.status}）`
    throw new ApiError(message, response.status)
  }
  if (!validJson || (body === null && !allowNull)) throw new Error('服务返回了无法读取的数据，请重试。')
  return body as T
}

