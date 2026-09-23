/** 浏览器回归配置：独立预览端口，接口由用例拦截，不访问正式账号或模型。 */
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/browser',
  outputDir: '../temp/runtime/m6-browser-results',
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  workers: 2,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:4175', browserName: 'chromium', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: 'npm run build -- --outDir ../temp/runtime/m6-browser-dist --emptyOutDir && npm run preview -- --port 4175 --outDir ../temp/runtime/m6-browser-dist',
    url: 'http://127.0.0.1:4175',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
