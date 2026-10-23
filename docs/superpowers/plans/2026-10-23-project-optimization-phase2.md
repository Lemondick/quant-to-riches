# 资产口径与后台计算 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 未知币种与未登记负债明确展示，长计算可查询、恢复和取消。

**Architecture:** 沿用 compute_jobs、入队去重与 Worker 租约，新增受限的账户任务服务和查询/取消控制器；原端点通过 background=true 选择后台返回。Vue composable 管理轮询与会话隔离，仅在 sessionStorage 保存任务 ID。

**Tech Stack:** Vue 3 / TypeScript、ASP.NET Core 10、PostgreSQL 18、现有 Python Worker、Node test / Playwright / xUnit。

**Spec:** docs/superpowers/specs/2026-10-23-project-optimization-phase2-design.md

## Global Constraints

- 不增加依赖或数据库迁移；不访问生产数据库、真实账户、SMTP 或付费模型。
- 保持原同步接口与旧客户端数字字段兼容；只暴露三类任务。
- 任务存储包含用户与会话 scope；浏览器只存任务 ID。
- 用户已授权继续修复、推送、合并 main 和同步本地；执行者沿用本会话的逐任务实现方式。

## Review Focus

- 同代码/不同资产类型的行情不能混用币种；未知币种不得汇总为一个有意义的金额（Task 1 浏览器用例）。
- 取消与完成竞争时保持已提交终态，旧 Worker 不得覆盖取消（Task 2 PostgreSQL 用例）。
- 已过期或失去租约的任务不能永远运行中（Task 2 PostgreSQL 用例）。
- 页面销毁、会话核验及切换后旧轮询不能展示账户结果（Task 3 浏览器用例）。
- 查询断网不能重复提交；存储拒绝不能阻止本次运行（Task 3 浏览器用例）。

### Task 1: 明确资产口径

**Files:** useFxRates.ts、LiveAssetOverview.vue、NetWorthService.cs、batch5Api.ts、NetWorthPanel.vue、NetWorthWidget.vue、LiveNetWorth.vue；fx-rates.test.mjs、branch-repair.mjs、ProjectOptimizationPhase2Tests.cs。

**Interfaces:** `Fx.convert` 的未知币种返回 missing=true；净资产响应增加 `liabilities_recorded:false` 与 `calculation_scope:positions_only`，保留原数字字段。适配器传递 liabilities_recorded，旧连接响应缺此字段视为未登记；演示 snapshot 未指定则保持演示行为。

- [x] 添加未知币种/空币种/已知本币单元与浏览器回归，以及负债范围字段测试。
- [x] 运行专项测试并观察缺失行为导致失败。
- [x] 最小实现：未知币种按数量说明并排除，缺汇率排除，明确折算仅含持仓；净资产页面和卡片显示范围及未计算状态。
- [x] 运行 `npm test`、浏览器专项、后端专项、types/lint。Expected: 全部通过。
- [x] 提交 `fix: make asset conversion and unrecorded liabilities explicit`。

### Task 2: 账户后台任务协议

**Files:** 新建 UserComputeTasks.cs、ComputeTasksController.cs；Program.cs、PortfolioBacktestController.cs、ValuationController.cs、DcaSmartController.cs、SmartDcaService.cs、api.md；ProjectOptimizationPhase2Tests.cs。

**Interfaces:** `UserComputeTasks.StartAsync(userId,kind,JsonNode,CancellationToken)`、`ReadAsync(userId,id,CancellationToken)`、`CancelAsync(userId,id,CancellationToken)` 返回 spec 中的 JsonObject。仅 portfolio.backtest / valuation.percentiles / dca.smart_plan_backtest。后台端点返回 202 和 Location/Retry-After；同步响应不变。

- [x] 添加后台创建、无效参数零任务、去重、跨账户/内部类型404、匿名401、CSRF、排队及运行取消、迟到发布被拒、完成保留、期限/租约失效和三端点集成测试。
- [x] 运行 `dotnet test --filter FullyQualifiedName~ProjectOptimizationPhase2Tests`。Expected: 新协议用例失败且原同步契约继续通过。
- [x] 实现持久化状态/取消服务，复用既有队列与租约；控制器先验证和鉴权再入队，返回固定形状且无内部凭据。
- [x] 更新机器可读路由清单并导出 OpenAPI 比对。Expected: 新增 GET/DELETE 两路由且完全一致。
- [x] 运行后端专项并提交 `feat: expose owned background compute task status and cancellation`。

### Task 3: 长任务界面恢复与验证

**Files:** 新建 useComputeTask.ts、ComputeTaskStatus.vue；LivePortfolioBacktest.vue、PortfolioBacktestPanel.vue、LiveValuation.vue、batch2Api.ts；branch-repair.mjs、功能说明.md、使用手册.md、验收报告。

**Interfaces:** `useComputeTask<T>(kind, verifiedSessionRef, normalize, autoRequest?)` 提供 task/result/error/busy/start/cancel/refresh，每个网络请求限定当前 scope 和 generation。保留原同步结果适配。任务 ID 持久化含 userId/scope/kind，不保存输入或结果；估值完成任务在重新进入时重新查询有效缓存。

- [ ] 添加真实 Vue 浏览器回归：后台回测、刷新恢复、取消、暂时失败后继续查询、401失效、跨账户迟到响应、存储拒绝、估值后台与旧200兼容。
- [ ] 运行浏览器用例，观察新功能断言失败。
- [ ] 实现轮询、状态展示与清理；仅本地停止轮询不会发取消，取消由明确按钮发 DELETE。刷新/查询失败不重复 POST。
- [ ] 运行前端143项基线及新增项、浏览器全套、真实 PG .NET 全套、lint/format/types、双构建和既有页面脚本。Expected: 全部通过且无跳过冒充成功。
- [ ] 提交实现与用户文档，完成独立整分支审查；逐项裁定审查边界，保留证据。
- [ ] 推送分支，等待精确提交的 Linux CI；正常快进合并 main，桌面干净时快进同步并核对 SHA。Expected: 本地与远程 main 相同，原备份仍在。
