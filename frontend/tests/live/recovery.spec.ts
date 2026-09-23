/** 真实缺项补问：由页面连续发送两轮，核对服务保存的字段与问题。 */
import { test, expect } from '@playwright/test'
import { login, send } from './support'

test('缺人数和预算时集中补问，下一轮保留已知目的地与天数', async ({ page }) => {
  await login(page, 'OWNER')
  const first = await send(page, '想去苏州玩两天，看看园林。请先帮我规划。')
  const initial = first.history.turns.at(-1)!.response.result
  expect(initial.extraction.destination).toContain('苏州')
  expect(initial.extraction.days).toBe(2)
  expect(initial.missing_required_fields).toEqual(expect.arrayContaining(['travelers', 'total_budget']))
  expect(initial.clarification).toBeTruthy()
  await expect(page.getByRole('log', { name: '旅行对话' })).toContainText(/人数|几个人/)

  const second = await send(page, '我们两个人，总预算3000元。仍去苏州玩两天，喜欢园林。')
  const completed = second.history.turns.at(-1)!.response.result
  expect(second.session).toBe(first.session)
  expect(second.history.revision).toBe(2)
  expect(completed.extraction.destination).toContain('苏州')
  expect(completed.extraction.days).toBe(2)
  expect(completed.extraction.travelers).toBe(2)
  expect(Number(completed.extraction.total_budget)).toBe(3000)
  expect(completed.missing_required_fields).toEqual([])
  console.log(`缺项补问验收：session=${second.session} revision=2`)
})
