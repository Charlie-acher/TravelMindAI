/** 浏览器回归：覆盖原生弹层、真实DOM布局、发送与刷新，后端数据为固定夹具。 */
import { test, expect } from './workspace-fixture'

test.beforeEach(async ({ page, scenario }) => {
  void scenario
  await page.goto('/')
  await page.getByRole('button', { name: '开始规划' }).click()
  await page.getByLabel('用户名', { exact: true }).fill('browser-user')
  await page.getByLabel('密码', { exact: true }).fill('test123')
  await page.getByRole('button', { name: '登录并继续' }).click()
  await expect(page.getByLabel('旅行需求输入')).toBeVisible()
  await page.getByLabel('选择模型').selectOption('qwen')
  await page.getByLabel('旅行需求输入').fill('从上海去苏州两天，两人三千元，逛拙政园和留园。')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByRole('button', { name: '打开行程草稿' })).toBeVisible()
})

test('模型选择、过程摘要和行程文件在刷新后恢复', async ({ page, scenario }) => {
  expect(scenario.requests).toHaveLength(1)
  expect(scenario.requests[0].selected_provider).toBe('qwen')
  await expect(page.getByRole('button', { name: /知识库/ })).toHaveCount(0)
  await expect(page.getByRole('log')).not.toContainText('预算明细与假设')
  await page.getByText(/1 次子智能体调用/).click()
  await expect(page.locator('.process-group')).toHaveCount(2)
  await expect(page.locator('.process-group').filter({ hasText: '子智能体' })).toContainText('研究助手')
  await page.reload()
  await page.getByRole('button', { name: '开始规划' }).click()
  await expect(page.getByLabel('选择模型')).toHaveValue('qwen')
  await page.getByRole('button', { name: '打开行程草稿' }).click()
  const preview = page.getByRole('region', { name: '文件预览', exact: true })
  await expect(preview).toContainText('预算明细与假设')
  await expect(preview).toContainText('拙政园')
  await expect(preview).toContainText('留园')
  expect(scenario.requests).toHaveLength(1)
})

test('状态悬浮不挤压聊天，窄屏文件可关闭且页面不横向溢出', async ({ page }) => {
  const chat = page.getByRole('region', { name: '旅行对话', exact: true })
  const before = await chat.boundingBox()
  await page.getByRole('button', { name: '状态', exact: true }).click()
  const status = page.getByRole('dialog', { name: '当前会话状态' })
  await expect(status).toBeVisible()
  await expect(status).toContainText('0.6%')
  await expect(status).toContainText('6,000 / 1,000,000')
  expect((await chat.boundingBox())?.width).toBe(before?.width)
  await page.keyboard.press('Escape')
  await expect(status).not.toBeVisible()
  await page.getByRole('button', { name: '打开行程草稿' }).click()
  await expect(page.getByRole('region', { name: '文件预览', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('#workspace-inspector')).not.toBeVisible()
  const widths = await page.evaluate(() => [document.documentElement.clientWidth, document.documentElement.scrollWidth])
  expect(widths[1]).toBeLessThanOrEqual(widths[0])
})

test('版本冲突恢复已保存行程，保留待发送原话且不自动重复提交', async ({ page, scenario }) => {
  scenario.conflict = true
  const message = '第二天改为十一点出发'
  await page.getByLabel('旅行需求输入').fill(message)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('已恢复最新对话，请核对消息后重新发送。')).toBeVisible()
  await expect(page.getByLabel('旅行需求输入')).toHaveValue(message)
  await expect(page.getByRole('log')).toContainText('另一页面已补充：第二天先休息。')
  await expect(page.getByRole('button', { name: '打开行程草稿' })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '发送消息', exact: true })).toBeEnabled()
  expect(scenario.requests).toHaveLength(2)
  expect(scenario.requests[1].expected_revision).toBe(1)
  scenario.conflict = false
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByLabel('旅行需求输入')).toHaveValue('')
  expect(scenario.requests).toHaveLength(3)
  expect(scenario.requests[2].expected_revision).toBe(2)
  expect(scenario.requests[2].message_id).not.toBe(scenario.requests[1].message_id)
})
