# E2E 冒烟测试（batch4 流 B #6）

通过标准：页面能渲染、无控制台报错（`console.error` / `pageerror` 任一出现即失败）。
截图留档：`tests/e2e/screenshots/<demo|live>/*.png`。

## 前置

```bash
npx playwright install chromium   # 约 150MB；沙箱下载需在权限卡上批准 storage.googleapis.com
```

## demo 模式（离线演示构建）

```bash
npx vite build --outDir /tmp/smoke-demo --emptyOutDir
npx vite preview --outDir /tmp/smoke-demo --port 57450 --strictPort &
SMOKE_MODE=demo npx playwright test
```

覆盖：`/` 仪表盘、`/ledger` 持仓页、`/risk` 风险看板、`/portfolio-backtest` 组合回测页。

## live 模式（登录页）

```bash
node tests/e2e/mock-api.mjs 57451 &   # /auth/session -> 401，驱动登录表单渲染
VITE_API_BASE_URL=http://127.0.0.1:57451 npx vite build --outDir /tmp/smoke-live --emptyOutDir
npx vite preview --outDir /tmp/smoke-live --port 57450 --strictPort &
SMOKE_MODE=live npx playwright test
```

覆盖：`/` 未登录时显示“登录后端账户”表单（`ApiSessionGate`）。

## 2026-10-21 执行记录

- Playwright chromium 下载被权限卡拦截（`storage.googleapis.com` 待用户批准），
  本机无可用 chromium，本次未执行到浏览器步骤——**无截图产出**，未伪造。
- 降级验证（已通过）：`npm run build`（含 `vue-tsc -b`）+ `npm test` 97/97。
- 无浏览器冒烟：preview 服务 + curl 逐路由 200 检查（见完工报告）。

## 2026-10-21 执行记录（补）
- `storage.googleapis.com` 权限批准后，`npx playwright install chromium` 成功（headless shell）。
- 另需 `npm install -D @playwright/test`（冒烟测试运行器，之前缺失）。
- demo 模式 4/4 通过（仪表盘、持仓、风险看板、组合回测），live 模式登录页 1/1 通过。
- 修了 3 个冒烟脚手架问题：mock 未登录数据接口应返回 401（原 503 不真实）、mock 缺 CORS 头（含 `X-Trace-Id`）、live 模式 401 资源报错属预期已过滤。
- 截图：`tests/e2e/screenshots/demo/*.png`、`tests/e2e/screenshots/live/*.png`。
