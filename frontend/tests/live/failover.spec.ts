/** 独立Compose故障注入：首选模型连不上时，浏览器能收到备用模型的完整答复。 */
import { test, expect } from '@playwright/test'
import { login, send } from './support'

test('Kimi 连接失败后切至 DeepSeek，原话只保存一轮', async ({ page }) => {
  await login(page, 'OWNER')
  await page.getByLabel('选择模型').selectOption('kimi')
  const { session, history } = await send(page, '请介绍苏州博物馆适合怎样参观，具体开放时间如未核实就说明待查询。')
  const turn = history.turns.at(-1)!
  expect(history.revision).toBe(1)
  expect(turn.response.selected_provider).toBe('kimi')
  expect(turn.response.used_providers).toContain('deepseek')
  expect(turn.response.used_providers).not.toContain('kimi')
  expect(turn.response.reply).toContain('苏州')
  expect(turn.response.reply.length).toBeGreaterThan(20)
  await page.reload()
  await page.getByRole('button', { name: '开始规划' }).click()
  await expect(page.getByRole('log', { name: '旅行对话' })).toContainText('苏州博物馆')
  console.log(`故障切换验收：session=${session} 首选kimi 实际${turn.response.used_providers?.join('→')}`)
})
