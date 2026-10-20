# Public data quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成《01》阶段 C（BUG-005 至 BUG-009），让行情时段、日历、时间解析、正式净值时效和目录完整性可核验。

**Architecture:** 日历仍以 `TradingCalendarRepository` 和管理员维护流程为权威。现有 CN 2026 内置兜底移至唯一 JSON 资源，两种语言复用；运行中的 Python Worker 从已认证 claim 响应取得只读日历快照，不接触数据库。质量函数使用不可变快照和明确的市场、来源能力与发布时间策略；缺失信息不以本机时间或其他市场数据补齐。

**Tech Stack:** .NET 10、Npgsql/PostgreSQL 18、Python 3.12、现有 Pydantic/Vue/TypeScript。

**Spec:** 用户桌面《01 当前问题修复与稳定性整改需求.md》第 4 节；执行授权为“阶段 A 完成后按文档顺序继续”，保持独立工作区、不合并 main、不部署生产。

## Global Constraints

- 不要顺手重构无关页面。
- 不修改真实用户账本，不重置数据库，不执行真实交易。
- 未维护的年份或未知休市状态必须 fail-closed。
- 不得把 fetched_at 或 cache_read_at 充当 observed_at/source_time。
- 正式净值 latest_nav 与盘中估值 intraday_estimate 必须严格分离。
- 每个任务先运行能复现问题的测试，再改实现；数据库结果以 GitHub Linux CI 的实际执行为准。

## Review Focus

- 跨年往前查找最近交易日时遇到缺失年份，必须返回 unknown，不能跳过未知日历继续找。
- 多个 resident Worker 任务并发执行时，日历修订不能通过进程全局变量串到另一任务。
- 同一供应商的 CN 与 US 数据不能共享假日、时区或休市日判断。
- 新目录读取失败且旧缓存仍可用时，读取失败、历史完整性和旧时钟必须分别保留。
- 无时区的合法日期时间与非法偏移不是同一状态：前者 unknown_time，后者 invalid。

## Task 1: 唯一日历资源、市场身份和受控 Worker 快照

**Files:**
- Create: `web/backend/src/quant_assistant/providers/exchange_calendars.json`（仅迁出现有 CN 2026 数据，不推测其他市场/年份）。
- Create: `web/backend/src/quant_assistant/providers/market_calendar.py`（不可变只读快照）。
- Create: `web/backend-dotnet/src/QuantAssistant.Core/Market/MarketCalendars.cs`（只读快照及纯查询）。
- Modify: `web/backend-dotnet/src/QuantAssistant.Infrastructure/Runtime/TradingCalendarRepository.cs`、项目资源声明、`Controllers/CalendarController.cs`、`Controllers/WorkerController.cs`。
- Modify: `web/backend/src/quant_assistant/services/trading_calendar.py`（旧入口读取同一内置资源，不保留另一张假日表）。
- Test: 新增 `MarketCalendarTests.cs`、`web/worker/tests/test_market_calendar.py`，扩展现有管理员/Worker 集成测试。

**Interfaces:**
- `TradingCalendarRepository.SnapshotAsync(int year, CancellationToken) -> MarketCalendars`，取得前一年、当年、下一年 CN/HK/US/JP 的只读记录；一次批量读取，短期内存缓存，Save 后失效。
- `MarketCalendars.TradingDay(string market, DateOnly) -> bool?`；Python `MarketCalendars.trading_day(day, market='CN') -> bool | None`。
- claim input 增加 `_calendars`（版本、market、year、source、revision、holidays）；不进入原查询缓存键，不允许普通查询参数覆盖。
- 旧 `calendar:<year>` 仍表示 CN；其他市场键为 `calendar:<market>:<year>`。现有管理员接口缺省 market=CN；来源域名按市场验证。

- [x] 写失败测试：CN 临时休市覆盖内置值；2099 未导入返回 None；同日 CN/HK 结果互不替代；跨年碰到未知日历返回 None；两个并发任务各用自己的修订。
- [x] 运行上述测试，保存失败证据；现有管理员鉴权、revision 冲突测试保持有效。
- [x] 最小实现资源、仓储快照、claim 注入和 Python 只读类型。生产 Worker 缺少快照时使用空快照；旧本地入口可读取同一内置资源。
- [x] 运行 Python calendar tests、.NET calendar tests；Linux PG 验证管理员保存和已认证 claim，确认响应没有数据库连接信息。
- [x] 提交独立变更。

## Task 2: 一致的市场时段与严格时间解析

**Files:**
- Modify: `web/backend/src/quant_assistant/providers/data_quality.py`、`fund_detail.py`、`fund_valuation.py`、`market_feed.py`。
- Modify: `web/worker/public_operations.py`，按日历修订缓存有界 provider 实例，显式传入快照以覆盖 provider 线程池调用。
- Modify: `web/backend-dotnet/src/QuantAssistant.Api/Services/PublicDataQuality.cs`、`PublicMarketSnapshot.cs` 及调用它们的 controller/dashboard service。
- Test: `PublicDataQualityTests.cs`、`web/worker/tests/test_data_quality.py`，新增共享 `tests/fixtures/public-quality-cases.json`。

**Interfaces:**
- `metadata(..., calendar, market='CN', auction_supported=False)`；C# Stamp/Apply 接收同一结构的只读日历和来源能力。
- `market_session` 值：`pre_market`、`auction`、`continuous_trading`、`lunch_break`、`closed`、`unknown`。
- CN 09:15–09:25 为 auction；09:25–09:30 为 pre_market，最近有效场次可含已经完成的开盘竞价；09:30–11:30、13:00–14:57 为 continuous_trading；11:30–13:00 为 lunch_break；14:57–15:00 为 auction；15:00 后 closed。
- 所有来源能力缺省 false；只有可核验的来源契约才能启用集合竞价，不因需要消除 stale 而启用。
- `Z`、`+08:00`、`-04:00` ISO 字符串按 UTC instant 比较；无时区为 unknown_time，非法偏移/日期为 invalid。保留原始 source_time，展示时区另取 market。

- [x] 写失败测试：需求中的全部时段边界、闭市/周末/临时休市、支持与不支持 auction 两种来源、停留在昨日的报价仍 stale。
- [x] 写失败测试：等价 Z/+08/-04 时间、DST 两个不同明确 instant、无时区、非法偏移、未来一分钟以上；共享 fixture 两种语言断言相同。
- [x] 运行测试并确认失败后，删除 quality 中重复假日表和年份常量，接入 Task 1 快照及严格解析。
- [x] 未配置海外日历/时段返回 unknown，不使用 CN 规则；保留已知价格和原始时间。
- [x] 运行两种语言质量测试、现有公开数据 provider/Worker 测试并提交。

## Task 3: 正式净值发布时间策略

**Files:** `data_quality.py`、`fund_valuation.py`、`PublicDataQuality.cs`，相应测试及 Observation DTO。

**Interfaces:** 正式净值携带 `nav_freshness_policy`（`daily_t1` / `qdii_t3` / `weekly` / `unknown`）、`nav_age_sessions` 和策略理由。策略来自已验证基金类型/发布频率，未知类型或日历保持时效未知；不按基金名称猜类型。每日与 QDII 策略分别允许 1 / 3 个已完成交易日延迟；周频按明确发布周期判断，不能套用日频；货币基金缺少对应净值发布契约时 unknown。

- [x] 写失败测试：日频缺失多个交易日 stale；跨国庆长假不按自然日误判；未知年份/基金策略 unknown；QDII 独立宽限；周频独立周期；正式净值始终不写入 estimate_nav。
- [x] 运行测试确认失败；移除两个 provider 的 14 自然日统一拒绝，将旧正式净值保留为带 stale 的真实证据。
- [x] Python 和 C# 共用测试参数；运行基金估值、资料、公开缓存测试并提交。

## Task 4: 目录覆盖、报价覆盖和时钟状态分别表达

**Files:** `market_sectors.py`、`controllers/schemas.py`、`web/frontend/src/services/dataApi.ts`，sector tests 和必要的现有目录状态文案。

**Interfaces:** `total` 仅为供应商权威目录总数；Sina 未提供时 null。`received` 为接收行数。`complete` 为 bool|null，另给 `catalog_status=complete|incomplete|unknown`；`http_ok`、`parse_ok`、`quotes_complete`、`source_time_known`、`cache_used` 独立。Eastmoney 的目录总数与行情接口 quote_total 分离；无报价的注册板块保留 null。

- [x] 写失败测试：Sina 有 25 行但 total/complete 未知；HTTP 成功解析失败；Eastmoney 多页截断/重复；目录完整但有未报价板块；旧缓存保留原时间、原目录证据和本轮失败状态；旧版缓存的自填 total 不升级为权威 total。
- [x] 运行失败测试；只扩展缓存版本和质量契约，不更换板块身份，不混合供应商分类，不调整页面布局。
- [x] 运行 sector/provider/schema 测试及前端 typecheck/tests/build；提交。

## Task 5: 阶段 C 验收

- [ ] 在 `web/docs/refactor/stability-implementation-20261018.md` 记录每项根因、文件、接口、真实证据及未知来源能力。
- [ ] 运行 `.NET build`、Python verify/ruff、secret scan、前端全部必要检查和 Git diff 检查。
- [ ] 推送当前独立分支，等待 GitHub Linux 四作业通过；PG/Worker skipped 不能计通过。
- [ ] 展示 C 结果后继续《01》阶段 D；不合并 main，不部署生产。

Reference: [深交所开盘、连续竞价和收盘时段](https://www.szse.cn/www/investor/knowledge/stock/deal/t20190626_568129.html)。这些时段是交易所规则，不证明任何公开 HTTP 来源支持竞价报价。
