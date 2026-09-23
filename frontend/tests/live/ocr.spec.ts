/** 真实OCR整链：浏览器生成可读原件，走上传、MinerU、文字识别及私有快照。 */
import { test, expect, type Page } from '@playwright/test'
import type { AttachmentView } from '../../src/api/attachments'
import { login, send } from './support'

const names = ['苏州博物馆', '平江路']

async function sourcePage(page: Page) {
  await page.setContent(`<html lang="zh"><meta charset="utf-8"><body style="font-family:'Microsoft YaHei',sans-serif;padding:60px;color:#111;background:white"><h1 style="font-size:42px">苏州私人旅行攻略</h1><p style="font-size:32px">第一站：苏州博物馆</p><p style="font-size:32px">第二站：平江路</p><p style="font-size:24px">这只是验收资料，路线需要地图核实。</p></body></html>`)
  await expect(page.getByText('苏州博物馆')).toBeVisible()
}

test('PDF 和图片均通过 MinerU 读取全文，结果留在私人附件', async ({ page, browser }) => {
  const fixture = await browser.newPage({ viewport: { width: 1200, height: 800 } })
  let pdf: Buffer, png: Buffer
  try {
    await sourcePage(fixture)
    pdf = await fixture.pdf({ format: 'A4', printBackground: true })
    png = await fixture.screenshot({ type: 'png' })
  } finally { await fixture.close() }

  await login(page, 'OWNER')
  const files = [
    { name: `苏州攻略-${crypto.randomUUID()}.pdf`, mimeType: 'application/pdf', buffer: pdf },
    { name: `苏州路线-${crypto.randomUUID()}.png`, mimeType: 'image/png', buffer: png },
  ]
  const uploaded: AttachmentView[] = []
  for (const file of files) {
    const receiving = page.waitForResponse(response => response.request().method() === 'POST'
      && /\/sessions\/[^/]+\/attachments$/.test(new URL(response.url()).pathname))
    await page.locator('input[type="file"]').setInputFiles(file)
    const response = await receiving
    expect(response.status()).toBe(201)
    uploaded.push(await response.json())
  }
  const { session, history } = await send(page, '请只读取这两份附件，概括看得到的地点；不生成行程。')
  const snapshots = history.turns.at(-1)!.response.attachments!
  expect(snapshots.map(item => item.id).sort()).toEqual(uploaded.map(item => item.id).sort())
  for (const item of snapshots) {
    expect(item.error_message, item.file_name).toBeNull()
    expect(item.analysis?.parser_version, item.file_name).toBe('mineru-4.0.4-ocr-v1')
    const evidence = JSON.stringify(item.analysis)
    for (const name of names) expect(evidence, item.file_name).toContain(name)
  }
  await page.reload()
  await page.getByRole('button', { name: '开始规划' }).click()
  const restored = await page.request.get(`/api/v1/sessions/${session}/requirement-messages`)
  expect((await restored.json()).turns.at(-1).response.attachments).toEqual(snapshots)
  console.log(`MinerU浏览器验收：session=${session} files=${uploaded.map(item => item.id).join(',')}`)
})
