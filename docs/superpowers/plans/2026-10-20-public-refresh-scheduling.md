# Public refresh scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 完成《01》阶段 D / BUG-010；慢刷新不会忙循环，核心行情优先，失败保留真实旧证据。

**Architecture:** 保留单个 BackgroundService 和现有队列、租约、并发限制。每轮完成后统一等待配置间隔；显式请求合并后在下一轮消费，也遵守该间隔。使用现有 TimeProvider 驱动等待和取消，不新增运行依赖。整体 deadline 包含数据库读取及排队；子任务同时受自身超时和整体取消约束。

**Tech Stack:** .NET 10、Npgsql、现有 xUnit/PostgreSQL Linux CI。

**Spec:** 用户桌面 `01-%E5%BD%93%E5%89%8D%E9%97%AE%E9%A2%98%E4%BF%AE%E5%A4%8D%E4%B8%8E%E7%A8%B3%E5%AE%9A%E6%80%A7%E6%95%B4%E6%94%B9%E9%9C%80%E6%B1%82.md` 第 5 节。已授权按文档顺序继续；不合并 main、不部署生产。

## Global Constraints

- 不修改页面布局、真实账本、生产配置或生产数据库。
- 页面读取继续立即返回快照或明确不可用，不等待公网爬虫。
- 不重置 source_time / observed_at；空结果和失败不能覆盖有效快照。
- 不绕过现有 Worker 鉴权、账户权限、队列配额或租约。

## Review Focus

- 自动预热关闭时，显式刷新仍受间隔约束且能够在休市时执行。
- 停止服务时取消等待和活动轮次，不能留下第二个未等待的后台循环。
- 核心行情超时后不继续排队辅助任务；队列任务 deadline 不超过本轮剩余期限。
- 有效旧快照恢复与刚完成的快照保持成对的数据和 completed_at，不混用不同结果的时间。
- 持续失败每轮最多一条警告，恢复后正常刷新；无账户时也不能忙循环。

## Task 1: 完成后间隔、截止时间、核心优先与旧证据

**Files:** `PublicSnapshotRefresher.cs`、`PublicMarketSnapshot.cs`；新增 `PublicRefreshSettings.cs`（单一配置解析）、`migrations/0023_public_market_snapshot_guard.sql`（派生公开缓存保护）；`PublicRefreshSchedulingTests.cs`、测试用 `ManualClock.cs`；架构文档及环境示例。

**Interfaces:** `QUANT_PUBLIC_REFRESH_INTERVAL_SECONDS` 默认 30，限制 15–300；`QUANT_PUBLIC_REFRESH_DEADLINE_SECONDS` 默认 75，限制 10–180；`QUANT_PUBLIC_REFRESH_TASK_TIMEOUT_SECONDS` 默认 55，限制 5–120 且不超过整体 deadline。无效值使用默认，数字越界夹取。接口 `refresh_interval_seconds` 和手动节流复用同一配置。公开快照读取 2 个间隔后标 stale，最大保留 1 天不变。

- [x] 新增真实 PG 回归：模拟 35/60 秒后仍等待完整间隔；冷启动第一项为 market.snapshot；整体/子任务超时后取消等待、保留旧时钟；连续失败请求有界并能恢复。新增纯单元回归：全空结果不覆盖有效快照。
- [x] 本地先确认全空回归 RED，提交测试并在 Linux CI 确认真正的调度/优先级 RED；本地 PG 跳过不算通过。
- [x] 最小实现完成后间隔和合并请求，配置有界，使用 TimeProvider 超时。先 await 核心行情，再排队辅助历史、报价及基金；发布数据与持久化 completed_at 成对读取。
- [x] 运行 .NET 全套；修正依赖空行情夹具的测试使其包含真实形状的合成观测，不降低行为断言。补充配置边界、服务停止、连续失败恢复用例。
- [x] 文档统一为完成后等待配置间隔，解释显式请求也受间隔约束；提交独立变更。

## Task 2: 阶段 D 验收

- [x] 运行完整 Linux CI；核实 .NET/PG、Worker、Python、前端与原生构建全部通过，失败及 skipped 为零。
- [x] 一次独立整体审查；Important/Critical 在一轮 RED→GREEN 后完整回归，Minor 记录不顺手扩大范围。
- [x] 更新 `stability-implementation-20261018.md`，记录根因、命令、实际计数、链接与取舍，展示阶段 D 结果后继续阶段 E。
