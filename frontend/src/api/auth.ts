/** 账号接口层：登录态只保存在HttpOnly Cookie，页面只读取角色。 */
import { apiFetch, readResponse } from './http'

export interface Account { id: string; username: string; role: 'admin' | 'user' }
export async function currentAccount(): Promise<Account> {
  return readResponse(await apiFetch('/api/v1/auth/me', { cache: 'no-store' }))
}
export async function login(username: string, password: string): Promise<Account> {
  return readResponse(await apiFetch('/api/v1/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
  }))
}
export async function register(username: string, password: string): Promise<Account> {
  return readResponse(await apiFetch('/api/v1/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
  }))
}
export async function logout(): Promise<void> {
  const response = await apiFetch('/api/v1/auth/logout', { method: 'POST' })
  if (response.status !== 204) await readResponse(response)
}
