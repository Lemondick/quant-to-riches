import { defineConfig, devices } from '@playwright/test'

/**
 * batch4 流 B（#6）：前端冒烟测试配置。
 * 两种模式（环境变量 SMOKE_MODE）：
 * - demo：不带 VITE_API_BASE_URL 构建，页面用本地演示数据渲染；
 * - live：带 VITE_API_BASE_URL 构建并配合 tests/e2e/mock-api.mjs，
 *   mock 对 /auth/session 返回 401，验证登录页（ApiSessionGate）渲染。
 * 运行前：先构建对应产物，再用 `npx vite preview --outDir <dir> --port 57450` 起服务。
 * 详见 tests/e2e/README.md。
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.SMOKE_BASE_URL ?? 'http://127.0.0.1:57450',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
