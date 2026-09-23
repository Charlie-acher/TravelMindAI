/** 上传真实整链：唯一原文区分私人附件与共享资料，后台结束后按本次编号清理。 */
import { test, expect } from '@playwright/test'
import type { AttachmentView } from '../../src/api/attachments'
import type { DocumentJobState, DocumentSearchResult, UploadResult } from '../../src/api/documents'
import { headers, login, send } from './support'

test.use({ actionTimeout: 30_000 })

test('私人 TXT 和 Markdown 识别、刷新及文件恢复，不进入公共知识库', async ({ page, browser }) => {
  const token = `private-${crypto.randomUUID()}`
  const files = ['txt', 'md'].map(extension => ({
    name: `${token}.${extension}`, mimeType: extension === 'md' ? 'text/markdown' : 'text/plain',
    buffer: Buffer.from(`# 苏州私人旅行笔记 ${token}\n只供当前会话参考。${extension}路线：先游览拙政园，再前往苏州博物馆，下午到平江路步行。独有备注：${token}-${extension}。`),
  }))
  await login(page, 'OWNER')
  await page.getByLabel('选择模型').selectOption(process.env.TRAVELMIND_E2E_PROVIDER ?? 'deepseek')
  const uploaded: AttachmentView[] = []
  for (const file of files) {
    const receiving = page.waitForResponse(response => response.request().method() === 'POST'
      && /\/sessions\/[^/]+\/attachments$/.test(new URL(response.url()).pathname))
    await page.locator('input[type="file"]').setInputFiles(file)
    const response = await receiving
    expect(response.status()).toBe(201)
    uploaded.push(await response.json())
    await expect(page.getByLabel(`移除附件 ${file.name}`)).toBeVisible()
  }
  const { session, history } = await send(page, '请只读取并概括这两份私人附件里的路线，不生成行程，不查询网页。')
  const snapshots = history.turns.at(-1)!.response.attachments!
  expect(snapshots.map(item => item.id).sort()).toEqual(uploaded.map(item => item.id).sort())
  console.log(`私人附件已发送：session=${session} ids=${uploaded.map(item => item.id).join(',')} token=${token}`)
  await page.reload()
  await page.getByRole('button', { name: '开始规划' }).click()
  await expect(page.getByRole('log', { name: '旅行对话' })).toContainText(files[0]!.name)
  await page.getByRole('button', { name: '文件', exact: true }).click()
  const panel = page.getByRole('region', { name: '本次旅行文件' })
  for (const file of files) {
    await panel.getByRole('button', { name: new RegExp(file.name.replaceAll('.', '\\.')) }).click()
    const preview = page.getByRole('region', { name: '文件预览', exact: true })
    await expect(preview).toContainText(token)
    if (snapshots.find(item => item.file_name === file.name)?.analysis) {
      await expect(preview.getByText('附件识别摘要', { exact: true })).toBeVisible()
    }
  }
  // 读取原件和历史不应重新识别；确认保存的快照与刷新前完全一致。
  const restored = await page.request.get(`/api/v1/sessions/${session}/requirement-messages`)
  expect((await restored.json()).turns.at(-1).response.attachments).toEqual(snapshots)
  const context = await browser.newContext({ baseURL: process.env.TRAVELMIND_E2E_BASE_URL || 'http://127.0.0.1:4176' })
  try {
    const admin = await context.newPage()
    await login(admin, 'ADMIN')
    const listed = await context.request.get(`/api/v1/admin/documents/page?q=${token}`)
    expect(listed.status()).toBe(200)
    expect((await listed.json()).items).toEqual([])
    const searched = await context.request.post('/api/v1/admin/document-search', {
      headers, data: { query: token, limit: 10, document_id: null, city: null, category: null },
    })
    expect(searched.status()).toBe(200)
    const result: DocumentSearchResult = await searched.json()
    expect(result.items.some(hit => hit.chunk.text.includes(token))).toBe(false)
    for (const item of uploaded) {
      expect((await context.request.get(`/api/v1/sessions/${session}/attachments/${item.id}/content`)).status()).toBe(404)
    }
  } finally { await context.close() }
  // 识别失败仍先核对原件恢复和隔离，但最终不能把部分读取当成整链通过。
  for (const item of snapshots) {
    expect(item.error_message, item.file_name).toBeNull()
    expect(item.analysis?.summary, item.file_name).toBeTruthy()
  }
})

test('管理员 TXT 和 Markdown 解析、切片、索引、聊天引用及删除后检索', async ({ page }) => {
  const token = `shared-${crypto.randomUUID()}`
  const documents: { id: string; name: string; query: string }[] = []
  await login(page, 'ADMIN')
  await page.getByLabel('选择模型').selectOption(process.env.TRAVELMIND_E2E_PROVIDER ?? 'deepseek')
  try {
    for (const extension of ['txt', 'md']) {
      const name = `苏州-${token}.${extension}`
      const marker = `${token}-${extension}`
      await page.getByRole('button', { name: /知识库/ }).click()
      if (!await page.getByLabel('选择资料文件（可多选）').isVisible()) {
        await page.getByRole('button', { name: '上传资料', exact: true }).click()
      }
      // 城市列表加载时组件会忽略change；原生setInputFiles不等待enabled，需先确认可选。
      await expect(page.getByLabel('选择资料文件（可多选）')).toBeEnabled()
      await page.getByLabel('选择资料文件（可多选）').setInputFiles({ name,
        mimeType: extension === 'md' ? 'text/markdown' : 'text/plain',
        buffer: Buffer.from(`# 苏州旅行资料 ${marker}\n苏州青苔旅行读书会活动说明，资料编号${marker}。\n本次活动集合口令是青苔松果，集合说明是上午九点在平江路北入口签到。此段为自动验收的虚构活动资料，不能当作真实出行服务。`),
      })
      const receiving = page.waitForResponse(response => response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/v1/admin/documents')
      await page.getByRole('button', { name: '上传并自动处理', exact: true }).click()
      const response = await receiving
      expect(response.ok()).toBe(true)
      const result: UploadResult = await response.json()
      // 只记录本次新建资料；重复内容永不进入删除名单。
      expect(result.duplicate).toBe(false)
      documents.push({ id: result.document.id, name, query: `苏州青苔旅行读书会资料${marker}的集合口令和签到地点是什么？请根据资料回答。` })
      console.log(`共享资料已创建：id=${result.document.id} name=${name}`)
      expect(result.document.status).toBe('parsed')
      expect(result.processing_error).toBeNull()
      expect(result.document.sections.some(section => section.text.includes(marker))).toBe(true)
      await expect(page.getByLabel('本批上传结果')).toContainText('切片和索引已完成', { timeout: 120_000 })
      const jobResponse = await page.request.get(`/api/v1/admin/documents/${result.document.id}/job`)
      expect(jobResponse.status()).toBe(200)
      const job: DocumentJobState = await jobResponse.json()
      expect(job.status).toBe('completed')
      expect(job.total).toBeGreaterThan(0)
      expect(job.indexed).toBe(job.total)
      const chunks = await page.request.get(`/api/v1/admin/documents/${result.document.id}/chunks`)
      expect(chunks.status()).toBe(200)
      expect((await chunks.json()).items.some((chunk: { text: string }) => chunk.text.includes(marker))).toBe(true)
    }
    for (const document of documents) {
      await page.getByRole('button', { name: 'TravelMind 新对话', exact: true }).click()
      const { history } = await send(page, document.query)
      const knowledge = history.turns.at(-1)!.response.knowledge
      expect.soft(knowledge?.status, document.name).toBe('answered')
      expect.soft(knowledge?.sources.some(source => source.hit.chunk.document_id === document.id), document.name).toBe(true)
      await expect.soft(page.getByRole('log', { name: '旅行对话' })).toContainText('青苔松果')
      if (knowledge?.sources.some(source => source.hit.chunk.document_id === document.id)) {
        await page.locator('summary').filter({ hasText: '本轮参考资料' }).click()
        const source = page.locator('summary').filter({ hasText: document.name }).filter({ hasText: '段原文' })
        await source.click()
        await expect(source.locator('..').locator('blockquote').filter({ hasText: token }).first()).toBeVisible()
      }
    }
  } finally {
    // 即使断言失败也清理已确认新建的资料；在途任务保留编号供人工核查，不强删。
    const cleanupFailures: string[] = []
    for (const document of documents) {
      try {
        const jobResponse = await page.request.get(`/api/v1/admin/documents/${document.id}/job`)
        expect(jobResponse.status()).toBe(200)
        const job: DocumentJobState | null = await jobResponse.json()
        if (job && ['queued', 'running'].includes(job.status)) {
          console.log(`共享资料仍在处理，保留：id=${document.id} status=${job.status}`)
          throw new Error(`共享资料后台未结束，禁止清理：${document.id}`)
        }
        const deleted = await page.request.delete(`/api/v1/admin/documents/${document.id}`, { headers })
        expect(deleted.status()).toBe(204)
        expect((await page.request.get(`/api/v1/admin/documents/${document.id}`)).status()).toBe(404)
        const searched = await page.request.post('/api/v1/admin/document-search', {
          headers, data: { query: document.query, limit: 10, document_id: null, city: null, category: null },
        })
        expect(searched.status()).toBe(200)
        const result: DocumentSearchResult = await searched.json()
        expect(result.items.some(hit => hit.chunk.document_id === document.id)).toBe(false)
        console.log(`共享资料已删除并核对检索：id=${document.id}`)
      } catch (error) {
        cleanupFailures.push(document.id)
        console.log(`共享资料清理或检索核对未完成：id=${document.id} ${error instanceof Error ? error.message : '请求失败'}`)
      }
    }
    expect(cleanupFailures, '未完成清理或核对的本次资料编号').toEqual([])
  }
})
