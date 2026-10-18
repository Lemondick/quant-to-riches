import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { once } from 'node:events'
import { open } from 'node:fs/promises'
import path from 'node:path'

export async function verifyLiveWorkflows({ page, context, base, backend, repo, output, passed, workerSecret }) {
  const navigate = async (name) => page.locator('.sidebar').getByRole('link', { name, exact: true }).click()
  await navigate('组合分析')
  await page.getByRole('table', { name: '持仓收益贡献' }).getByText('Synthetic UI Fund', { exact: true }).waitFor()
  await page.getByText('尚无已确认净值记录', { exact: true }).waitFor()
  await navigate('基金复核')
  await page
    .getByRole('table', { name: '账户基金复核' })
    .getByRole('button', { name: 'Synthetic UI Fund', exact: true })
    .click()
  await page
    .getByRole('region', { name: '基金公开资料' })
    .getByRole('heading', { name: 'Synthetic UI Fund', exact: true })
    .waitFor()
  passed('Portfolio and fund review use owned holdings and explicit missing official NAV history')

  await navigate('我的自选')
  await page.getByRole('button', { name: '添加自选', exact: true }).click()
  const watch = page.getByRole('form', { name: '添加账户自选' })
  await watch.getByLabel('名称', { exact: true }).fill('Synthetic temporary index')
  await watch.getByLabel('代码', { exact: true }).fill('000300')
  await watch.getByLabel(/^类型/).selectOption('index')
  await watch.getByRole('button', { name: '保存自选', exact: true }).click()
  const watchRow = page
    .getByRole('table', { name: '账户自选列表' })
    .getByRole('row')
    .filter({ hasText: 'Synthetic temporary index' })
  await watchRow.waitFor()
  const persistedWatch = await (await context.request.get(`${base}/api/v1/preferences`)).json()
  assert.ok(persistedWatch.watchlist.some((item) => item.symbol === '000300' && item.asset_type === 'index'))
  await watchRow.getByRole('button', { name: '移除', exact: true }).click()
  await watchRow.waitFor({ state: 'detached' })
  passed('Watchlist additions and removals persist through authenticated CSRF-protected writes')

  const before = await (await context.request.get(`${base}/api/v1/portfolio`)).json()
  await navigate('交易账本')
  await page.getByRole('button', { name: '新增记录', exact: true }).click()
  const entry = page.getByRole('form', { name: '新增账户账本记录' })
  await entry.getByLabel('标的代码', { exact: true }).fill('510300')
  await entry.getByLabel('标的名称', { exact: true }).fill('Synthetic UI Fund')
  await entry.getByLabel('成交数量', { exact: true }).fill('1.25')
  await entry.getByLabel('成交价格', { exact: true }).fill('1.20')
  await entry.getByLabel('费用', { exact: true }).fill('0.10')
  const transaction = page.waitForResponse(
    (response) => response.url().endsWith('/api/v1/ledger/transactions') && response.request().method() === 'POST',
  )
  await entry.getByRole('button', { name: '保存到当前账户', exact: true }).click()
  const written = await transaction
  assert.equal(written.status(), 201)
  const saved = await written.json()
  assert.equal(Number(saved.quantity), 1.25)
  assert.equal(Number(saved.fees), 0.1)
  assert.equal(written.request().headers()['x-csrf-token']?.length > 0, true)
  assert.equal(written.request().headers()['x-session-scope']?.length > 0, true)
  await page.getByText('记录已保存到当前账户。', { exact: true }).waitFor()
  const repeat = await context.request.post(`${base}/api/v1/ledger/transactions`, {
    data: written.request().postDataJSON(),
    headers: {
      'X-CSRF-Token': written.request().headers()['x-csrf-token'],
      'X-Session-Scope': written.request().headers()['x-session-scope'],
    },
  })
  assert.equal(repeat.status(), 201)
  assert.equal((await repeat.json()).id, saved.id)
  const after = await (await context.request.get(`${base}/api/v1/portfolio`)).json()
  assert.equal(Math.round((Number(before.cash) - Number(after.cash)) * 100), 160)
  await navigate('资产总览')
  await page.getByRole('table', { name: '账户持有明细' }).getByText('101.25000000', { exact: true }).waitFor()
  passed('Decimal ledger writes update the account immediately and retries reuse the recorded transaction')

  await navigate('账号与设置')
  await page.getByRole('button', { name: '模型配置', exact: true }).click()
  await page.getByRole('region', { name: '账户模型连接' }).getByText('当前配置 尚未就绪', { exact: true }).waitFor()
  await page.getByRole('button', { name: '登录与安全', exact: true }).click()
  await page.getByRole('table', { name: '账户登录会话' }).waitFor()
  await page.getByRole('button', { name: '撤销其他设备会话', exact: true }).click()
  await page.getByText('其他设备会话已撤销。', { exact: true }).waitFor()
  const stores = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
  assert.doesNotMatch(
    JSON.stringify(stores),
    /Synthetic UI Fund|SyntheticPerformancePassword|csrf_token|cache_scope|api_key/,
  )
  passed('Settings read real sessions and model state while private records and keys stay out of browser storage')

  await navigate('管理者后台')
  const bobRow = page.getByRole('table', { name: '站点真实账户' }).getByRole('row').filter({ hasText: 'perf_ui_bob' })
  await bobRow.getByRole('button', { name: '切换启用状态', exact: true }).click()
  await bobRow.getByText('disabled', { exact: true }).waitFor()
  await bobRow.getByRole('button', { name: '切换启用状态', exact: true }).click()
  await bobRow.getByText('active', { exact: true }).waitFor()
  await page.getByRole('button', { name: '读取审计', exact: true }).click()
  await page.getByRole('table', { name: '站点审计日志' }).waitFor()
  passed('Administrator status changes are persisted and visible in the real audit log')

  const workerLog = await open(path.join(output, 'workflow-worker.log'), 'w')
  const worker = spawn(
    'uv',
    [
      'run',
      '--project',
      path.join(repo, 'web/backend'),
      'python',
      path.join(repo, 'web/worker/tests/run_ui_workflow_worker.py'),
      '--api',
      backend,
      '--worker-id',
      'synthetic-ui-strategy-worker',
    ],
    {
      cwd: repo,
      windowsHide: true,
      stdio: ['ignore', workerLog.fd, workerLog.fd],
      env: { ...process.env, QUANT_WORKER_SECRET: workerSecret, QUANT_SITE_ROOT: path.join(output, 'site') },
    },
  )
  try {
    await navigate('量化策略')
    await page.getByRole('button', { name: '编辑参数', exact: true }).click()
    const settings = page.getByRole('form', { name: '账户策略参数' })
    await settings.waitFor({ timeout: 70000 })
    const parameters = JSON.parse(await settings.getByLabel('参数配置', { exact: true }).inputValue())
    assert.ok(Object.keys(parameters).length > 0)
    await settings.getByRole('button', { name: '保存参数', exact: true }).click()
    await page.getByText('策略参数已保存。', { exact: true }).waitFor({ timeout: 70000 })
    await settings.getByRole('button', { name: '预览候选与证据', exact: true }).click()
    await page.getByText('预览已完成，请核对候选与阻断原因。', { exact: true }).waitFor({ timeout: 70000 })
    const evidence = JSON.parse(await page.locator('details pre').innerText())
    assert.equal(evidence.status, 'blocked')
    assert.ok(Array.isArray(evidence.research_blockers))
    assert.ok(evidence.research_blockers.length > 0)
    await page
      .getByRole('dialog', { name: '编辑策略参数', exact: true })
      .getByRole('button', { name: '关闭', exact: true })
      .click()
    await navigate('AI选股')
    await page.getByRole('form', { name: '创建账户研究任务' }).waitFor()
    await page.getByText('当前账户暂无研究任务', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '开始研究', exact: true }).isDisabled(), true)
    passed(
      'Real Python strategy validation, persistence and preview retain missing-evidence blockers without paid research',
    )
  } finally {
    if (worker.exitCode === null) {
      if (process.platform === 'win32')
        execFileSync('taskkill', ['/pid', String(worker.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' })
      else worker.kill()
      await once(worker, 'exit')
    }
    await workerLog.close()
  }
  await navigate('资产总览')
  await page.getByRole('table', { name: '账户持有明细' }).waitFor()
}
