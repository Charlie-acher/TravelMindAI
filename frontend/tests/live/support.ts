/** 真实浏览器公共步骤：复用页面登录和已保存消息核对，不拦截接口。 */
import { expect, type Page } from '@playwright/test'
import type { ConversationHistory } from '../../src/api/requirements'

export const headers = { 'X-Requested-With': 'TravelMindAI' }

export async function login(page: Page, role: 'OWNER' | 'OTHER' | 'ADMIN') {
  await page.goto('/')
  await page.getByRole('button', { name: '开始规划' }).click()
  await page.getByLabel('用户名', { exact: true }).fill(process.env[`TRAVELMIND_E2E_${role}`]!)
  // Playwright的fill失败日志会包含参数；密码框异常只抛固定提示，不带原始call log。
  try { await page.getByLabel('密码', { exact: true }).fill(process.env.TRAVELMIND_E2E_PASSWORD!) }
  catch { throw new Error('验收登录密码输入失败，请检查密码框是否可编辑。') }
  await page.getByRole('button', { name: '登录并继续' }).click()
  await expect(page.getByLabel('旅行需求输入')).toBeVisible()
}

/** 只从真实响应读回会话编号，最终历史必须有保存的草稿。 */
export async function send(page: Page, message: string) {
  await page.getByLabel('旅行需求输入').fill(message)
  const receiving = page.waitForResponse(response => response.request().method() === 'POST'
    && /\/sessions\/[^/]+\/requirement-messages\/stream$/.test(new URL(response.url()).pathname))
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const response = await receiving
  expect(response.status()).toBe(200)
  // 页面收到done后主动取消SSE reader；Chromium不保证还能通过调试协议读取完整响应体。
  // 等页面解锁，再用独立HTTP回读核实本条原话确实保存，不能把旧草稿当成本轮成功。
  await expect(page.getByLabel('旅行需求输入')).toBeEnabled({ timeout: 180_000 })
  const session = new URL(response.url()).pathname.split('/')[4]!
  const historyResponse = await page.request.get(`/api/v1/sessions/${session}/requirement-messages`)
  expect(historyResponse.status()).toBe(200)
  const history: ConversationHistory = await historyResponse.json()
  expect(history.turns.at(-1)?.response.result.original_message).toBe(message)
  return { session, history }
}
