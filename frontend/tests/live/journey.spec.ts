/** 真实整链：页面操作→HTTP/SSE→模型/工具→数据库→文件，失败不自动重跑收费请求。 */
import { test, expect, type APIRequestContext } from '@playwright/test'
import { headers, login, send } from './support'
import { readFile } from 'node:fs/promises'
import type { PlanSnapshot } from '../../src/api/requirements'

let seed: { client: APIRequestContext; document: string | null } | undefined

/** 新库也准备明确的规划依据，不依赖开发者既有知识库；不填写实时票价等事实。 */
test.beforeAll(async ({ playwright, baseURL }) => {
  const client = await playwright.request.newContext({ baseURL, extraHTTPHeaders: headers })
  seed = { client, document: null }
  const logged = await client.post('/api/v1/auth/login', { data: {
    username: process.env.TRAVELMIND_E2E_ADMIN, password: process.env.TRAVELMIND_E2E_PASSWORD,
  } })
  expect(logged.status()).toBe(200)
  const token = crypto.randomUUID()
  const uploaded = await client.post('/api/v1/admin/documents?auto_process=true', { multipart: {
    file: { name: `苏州-M6行程验收-${token}.txt`, mimeType: 'text/plain',
      buffer: Buffer.from(`苏州两天行程验收资料，编号${token}。\n第一天拙政园：苏州园林主题，可留出两小时慢慢参观。\n第二天留园：苏州园林主题，可留出两小时慢慢参观。\n这只是测试用户的行程偏好及时间建议，不代表真实票价、开放时间或可购门票；这些信息都待核实。`) },
  } })
  expect(uploaded.status()).toBe(201)
  const body = await uploaded.json()
  expect(body.duplicate).toBe(false)
  seed.document = body.document.id
  console.log(`行程验收资料已创建：id=${seed.document}`)
  expect(body.document.status).toBe('parsed')
  expect(body.processing_error).toBeNull()
  await expect.poll(async () => {
    const response = await client.get(`/api/v1/admin/documents/${seed!.document}/job`)
    expect(response.status()).toBe(200)
    return (await response.json())?.status
  }, { timeout: 120_000, intervals: [1000, 2000] }).toBe('completed')
})

test.afterAll(async () => {
  if (!seed) return
  try {
    if (seed.document) {
      const response = await seed.client.get(`/api/v1/admin/documents/${seed.document}/job`)
      expect(response.status()).toBe(200)
      expect(['queued', 'running']).not.toContain((await response.json())?.status)
      expect((await seed.client.delete(`/api/v1/admin/documents/${seed.document}`)).status()).toBe(204)
    }
  } finally { await seed.client.dispose() }
})

test('真实规划、指定日修改、文件下载、刷新及双角色权限', async ({ page, browser }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await login(page, 'OWNER')
  await expect(page.getByRole('button', { name: /知识库/ })).toHaveCount(0)
  const provider = process.env.TRAVELMIND_E2E_PROVIDER ?? 'deepseek'
  await page.getByLabel('选择模型').selectOption(provider)
  const first = await send(page, '从苏州出发去苏州玩两天，一个人，总预算3000元，第一天拙政园，第二天留园。每天九点开始，请生成并保存完整逐日行程草稿。')
  const v1 = first.history.turns.at(-1)!.response.itinerary
  expect(v1, '真实首轮必须生成可保存草稿，纯文字回退不算通过').toBeTruthy()
  expect(v1!.version).toBe(1)
  await expect(page.getByRole('article', { name: '行程概览' })).toHaveCount(1)
  const second = await send(page, '只把第二天的开始时间改成上午十一点，第一天和预算保持不变。请保存修改后的行程。')
  expect(second.session).toBe(first.session)
  expect(second.history.revision).toBe(2)
  const v2 = second.history.turns.at(-1)!.response.itinerary as PlanSnapshot
  expect(v2, '局部修改必须生成新版本').toBeTruthy()
  expect(v2.version).toBe(2)
  expect(v2.plan.days[0]).toEqual(v1!.plan.days[0])
  expect(v2.plan.budget).toEqual(v1!.plan.budget)
  expect(v2.plan.days[1]!.activities[0]!.start_time).toBe('11:00')

  await page.reload()
  await page.getByRole('button', { name: '开始规划' }).click()
  await expect(page.getByLabel('选择模型')).toHaveValue(provider)
  await expect(page.getByRole('article', { name: '行程概览' })).toHaveCount(2)
  await page.getByRole('button', { name: '打开行程草稿' }).last().click()
  const preview = page.getByRole('region', { name: '文件预览', exact: true })
  await expect(preview).toContainText('草稿 v2')
  await expect(preview).toContainText('11:00')
  const file = `/api/v1/personal-files/itinerary/${v2.itinerary_id}`
  const downloading = page.waitForEvent('download')
  await page.getByRole('link', { name: /^下载 / }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toMatch(/\.md$/)
  expect(await download.failure()).toBeNull()
  const markdown = await readFile((await download.path())!, 'utf8')
  for (const text of ['拙政园', '留园', '11:00', '预算明细']) expect(markdown).toContain(text)
  await preview.getByRole('button', { name: 'v1', exact: true }).click()
  await expect(preview).toContainText('草稿 v1')

  // 普通账号与管理员均不能读别人的私人会话；管理员仅多出共享知识库权限。
  for (const role of ['OTHER', 'ADMIN'] as const) {
    const context = await browser.newContext({ baseURL: process.env.TRAVELMIND_E2E_BASE_URL || 'http://127.0.0.1:4176' })
    try {
      const visitor = await context.newPage()
      await login(visitor, role)
      const docs = await context.request.get('/api/v1/documents')
      expect(docs.status()).toBe(role === 'ADMIN' ? 200 : 403)
      if (role === 'ADMIN') await expect(visitor.getByRole('button', { name: /知识库/ })).toBeVisible()
      for (const path of [file, `${file}/download`, `/api/v1/sessions/${first.session}/requirement-messages`,
        `/api/v1/sessions/${first.session}/workspace-status`]) {
        expect((await context.request.get(path)).status()).toBe(404)
      }
    } finally { await context.close() }
  }
  // 真实409由旧revision触发；不得新增一轮或多写行程版本。
  const conflict = await page.request.post(`/api/v1/sessions/${first.session}/requirement-messages`, {
    headers, data: { message: '这是一条过期页面请求', message_id: crypto.randomUUID(), expected_revision: 0, selected_provider: provider },
  })
  expect(conflict.status()).toBe(409)
  const reloaded = await page.request.get(`/api/v1/sessions/${first.session}/requirement-messages`)
  expect((await reloaded.json()).revision).toBe(2)
  const attachmentName = `第二天调整-${crypto.randomUUID()}.txt`
  const receiving = page.waitForResponse(response => response.request().method() === 'POST'
    && /\/sessions\/[^/]+\/attachments$/.test(new URL(response.url()).pathname))
  await page.locator('input[type="file"]').setInputFiles({ name: attachmentName,
    mimeType: 'text/plain', buffer: Buffer.from('苏州第二天调整：下午一点从留园开始参观。第一天拙政园保持不变。') })
  expect((await receiving).status()).toBe(201)
  const third = await send(page, '请依据刚才上传的私人附件，只把第二天的留园改为下午一点开始，第一天与总预算不变，并保存新版本。')
  const v3 = third.history.turns.at(-1)!.response.itinerary as PlanSnapshot
  expect(third.history.revision).toBe(3)
  expect(third.history.turns.at(-1)!.response.attachments?.[0]?.analysis?.summary).toBeTruthy()
  expect(v3, '附件指定日调整必须保存新版本').toBeTruthy()
  expect(v3.version).toBe(3)
  expect(v3.plan.days[0]).toEqual(v2.plan.days[0])
  expect(v3.plan.budget).toEqual(v2.plan.budget)
  expect(v3.plan.days[1]!.activities[0]!.start_time).toBe('13:00')
  expect(errors).toEqual([])
  console.log(`真实服务验收通过：3轮，v1→v2→附件v3，首选${provider}，实际${third.history.turns.at(-1)!.response.used_providers?.join('→')}`)
})
