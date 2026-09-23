/** 真实服务验收：必须由后端命令创建专用账号，不使用接口拦截或保存登录trace。 */
import { defineConfig } from '@playwright/test'

if (!process.env.TRAVELMIND_E2E_PASSWORD) throw new Error('请从backend运行 python -m scripts.evaluate_browser --live')
const externalURL = process.env.TRAVELMIND_E2E_BASE_URL

export default defineConfig({
  testDir: './tests/live',
  outputDir: '../temp/runtime/m6-live-browser',
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 420_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  use: { baseURL: externalURL || 'http://127.0.0.1:4176', viewport: { width: 1440, height: 1000 },
    browserName: 'chromium', trace: 'off', screenshot: 'only-on-failure' },
  webServer: externalURL ? undefined : { command: 'npm run build -- --outDir ../temp/runtime/m6-live-dist --emptyOutDir && npm run preview -- --port 4176 --outDir ../temp/runtime/m6-live-dist',
    url: 'http://127.0.0.1:4176', reuseExistingServer: false, timeout: 60_000 },
})
