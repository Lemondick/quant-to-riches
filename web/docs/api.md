# 网页接口说明

业务接口清单保留原有契约；账号认证和用户隔离从2026-10-04起适用于所有HTTP启动入口。

默认服务地址：`http://127.0.0.1:8765`。业务接口必须携带登录Cookie；写请求还需要当前会话的`X-CSRF-Token`和允许的`Origin`。全局Bearer Token不能替代账号会话。

日期字段使用`YYYY-MM-DD`；日期时间包含时区，业务以北京时间核对；金额/份额响应通常为十进制字符串。
参数、枚举、必填字段及响应详情以同一服务的[OpenAPI契约](http://127.0.0.1:8765/openapi.json)为准。

写操作必须通过页面预览和确认流程。超时先读取已保存记录，避免重复录入或重复付费创建任务。
模型发现保存兼容连接和Key到服务端；公开响应不回显Key。AI创建报告可能计费；读取列表及查询状态不会创建新报告。

## 接口清单

2026-10-05新增：

| 方法 | 路径 | 参数 / 行为 |
| --- | --- | --- |
| GET | `/api/v1/auth/sessions` | 当前用户有效会话及设备标签 |
| POST | `/api/v1/auth/sessions/{session_id}/revoke` | 仅撤销本用户指定会话 |
| POST | `/api/v1/auth/sessions/revoke-others` | 保留当前会话，撤销其他会话 |
| POST | `/api/v1/auth/recovery/redeem` | username、token、new_password；匿名可用，仍要求允许的Origin |
| POST | `/api/v1/admin/users/{id}/recovery-token` | 管理员签发15分钟一次性恢复码 |
| GET | `/api/v1/admin/users/{id}/timeline` | category、start/end、page、limit；真实事件及30天趋势 |
| GET | `/api/v1/admin/runtime` | 账户任务、业务状态、并发和备份；管理员敏感读取审计 |
| GET / PUT | `/api/v1/runtime/costs` | 用量、费用、单价和每日预算；写入使用revision冲突保护 |
| GET / PUT | `/api/v1/alerts/settings` | revision、stale_after_days、drawdown_threshold、task_failure_days |
| GET | `/api/v1/alerts` | 当前用户活跃提醒，证据不足保持未知 |
| POST | `/api/v1/alerts/{id}/acknowledge` | 当前用户确认提醒；不更改风险事实 |
| GET | `/api/v1/ledger/reconciliation/accounts` | 可选平台账户 |
| POST | `/api/v1/ledger/reconciliation/compare` | account_ids、complete_scope、expected_fees、start/end、evidence |
| GET | `/api/v1/ledger/reconciliation/history` | 已保存对账及处理说明；page、limit |
| POST | `/api/v1/ledger/reconciliation/{id}/resolve` | resolution：explained/follow_up/matched；note；证据未齐不能matched |
| POST | `/api/v1/ledger/replay/preview` | symbol、asset_type、action、quantity、price、fees、occurred_at、external_id、reason；可选cash_symbol、note |
| POST | `/api/v1/ledger/replay/confirm` | preview_id及显式confirmed=true；15分钟有效、源指纹校验、原子确认 |
| GET | `/api/v1/ledger/replay/history` | 本用户已完成补录；page、limit |

账户隔离由登录会话选定业务数据库，不接收用于切换账本的user_id。预览编号及提醒编号不能跨账户使用。冲突返回409及安全错误消息。`/healthz`检测服务存活，`/readyz`检测启用账户业务库可用性；公开健康响应不披露用户身份。

| 方法 | 路径 | 参数 / 行为 |
| --- | --- | --- |
| GET | `/api/v1/history/{section}` | ledger/actions/corrections；page、limit（1—500）、symbol、start/end；total和has_more |
| GET | `/api/v1/history/{section}/export` | symbol、start/end；完整筛选结果，最多10万条 |
| GET | `/api/v1/onboarding` | 当前账号账户、基线、模型、首份报告完成状态 |
| POST | `/api/v1/admin/users/{id}/status` | status为active或disabled；状态、会话撤销与审计原子执行 |
| POST | `/api/v1/admin/users/{id}/revoke-sessions` | 强制退出普通用户全部会话 |
| POST | `/api/v1/admin/registration` | enabled布尔值；不修改30人硬上限 |
| PUT | `/api/v1/admin/calendar` | year、source官方HTTPS链接、holidays ISO日期列表 |
| GET | `/api/v1/admin/audit` | page、limit；管理员、目标、内容、时间 |
| GET | `/api/v1/admin/users/{id}/usage` | 最近活动、7天请求/错误、报告与任务耗时及异常 |
| GET | `/api/v1/admin/users/{id}/export` | section、symbol、start/end；完整筛选明细，最多10万条 |

管理员详情GET `/api/v1/admin/users/{id}/details` 新增symbol、start/end筛选。管理员接口只授权当前管理员会话，写入需Origin和CSRF。导出API返回JSON明细，由前端生成CSV，不包含报告正文、私有连接或Key。

| 模块 | 方法 | 路径 | 请求体契约 |
| --- | --- | --- | --- |
| AI报告/模型/调度 | GET | `/api/v1/advisor/model-settings` | `—` |
| AI报告/模型/调度 | PUT | `/api/v1/advisor/model-settings` | `SaveAdvisorModels` |
| AI报告/模型/调度 | POST | `/api/v1/advisor/models/discover` | `ProviderInput` |
| AI报告/模型/调度 | PUT | `/api/v1/advisor/jev-connection` | `JevInput`（当前账号官方Key和配置revision） |
| AI报告/模型/调度 | GET | `/api/v1/advisor/notifications` | `—` |
| AI报告/模型/调度 | POST | `/api/v1/advisor/notifications/{notice_id}/acknowledge` | `—` |
| AI报告/模型/调度 | GET | `/api/v1/advisor/reports` | `—` |
| AI报告/模型/调度 | POST | `/api/v1/advisor/reports` | `CreateAdvisorReport` |
| AI报告/模型/调度 | GET | `/api/v1/advisor/reports/{report_id}` | `—` |
| AI报告/模型/调度 | GET | `/api/v1/advisor/schedule` | `—` |
| 分析 | POST | `/api/v1/analysis/explain` | `AnalysisExplainBody` |
| 历史回放 | GET | `/api/v1/backtests/latest` | `—` |
| 数据状态 | GET | `/api/v1/data/status` | `—` |
| 决策记录 | GET | `/api/v1/decisions/latest` | `—` |
| 基金资料/证据 | GET | `/api/v1/funds/candidates` | `—` |
| 基金资料/证据 | GET | `/api/v1/funds/lifecycle/events` | `—` |
| 基金资料/证据 | GET | `/api/v1/funds/style-drift/events` | `—` |
| 基金资料/证据 | GET | `/api/v1/funds/valuations` | `—` |
| 基金资料/证据 | GET | `/api/v1/funds/valuations/alerts` | `—` |
| 基金资料/证据 | GET | `/api/v1/funds/{symbol}/detail` | `—` |
| 基金资料/证据 | GET | `/api/v1/funds/{symbol}/fee-evidence` | `—` |
| 基金资料/证据 | POST | `/api/v1/funds/{symbol}/fee-evidence` | `FundFeeEvidenceBody` |
| 基金资料/证据 | POST | `/api/v1/funds/{symbol}/fee-quote` | `FundFeeQuoteBody` |
| 基金资料/证据 | GET | `/api/v1/funds/{symbol}/holding-lots` | `—` |
| 基金资料/证据 | POST | `/api/v1/funds/{symbol}/holding-lots` | `LotsBody` |
| 基金资料/证据 | POST | `/api/v1/funds/{symbol}/holding-redemption-quote` | `QuoteBody` |
| 基金资料/证据 | POST | `/api/v1/funds/{symbol}/redemption-quote` | `FundRedemptionQuoteBody` |
| 健康状态 | GET | `/api/v1/health` | `—` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/commit` | `CommitBody` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/confirm` | `ConfirmBody` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/holdings-commit` | `CommitBody` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/holdings-review` | `HoldingsReviewBody` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/manual` | `ManualBody` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/merge` | `MergeBody` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/preview` | `PreviewBody` |
| 截图/账户导入 | POST | `/api/v1/imports/fund-photo/review` | `ReviewBody` |
| 账本 | GET | `/api/v1/ledger` | `—` |
| 账本 | GET | `/api/v1/ledger/actions` | `—` |
| 账本 | POST | `/api/v1/ledger/actions` | `CorporateActionBody` |
| 账本 | GET | `/api/v1/ledger/attribution` | `—` |
| 账本 | GET | `/api/v1/ledger/corrections` | `—` |
| 账本 | POST | `/api/v1/ledger/corrections` | `LedgerCorrectionBody` |
| 账本 | POST | `/api/v1/ledger/dividends` | `LedgerDividendBody` |
| 账本 | POST | `/api/v1/ledger/transactions` | `LedgerTradeBody` |
| 行情 | GET | `/api/v1/market/news` | `—` |
| 行情 | GET | `/api/v1/market/quotes` | `—` |
| 行情 | GET | `/api/v1/market/snapshot` | `—` |
| 运行维护 | GET | `/api/v1/operations/readiness` | `—` |
| 运行维护 | GET | `/api/v1/operations/trends` | `—` |
| 运行维护 | GET | `/api/v1/operations/watchdog` | `—` |
| 运行维护 | POST | `/api/v1/operations/watchdog/heartbeat` | `WatchdogHeartbeatBody` |
| 资金计划 | GET | `/api/v1/planning/cash-flows` | `—` |
| 资金计划 | POST | `/api/v1/planning/cash-flows` | `CashFlowPlanBody` |
| 资金计划 | GET | `/api/v1/planning/cash-flows/due` | `—` |
| 账户/持仓 | GET | `/api/v1/portfolio` | `—` |
| 账户/持仓 | GET | `/api/v1/portfolio/drawdown` | `—` |
| 账户/持仓 | POST | `/api/v1/portfolio/drawdown/validate` | `DrawdownValidationBody` |
| 账户/持仓 | POST | `/api/v1/portfolio/fund-nav` | `FundPortfolioNavBody` |
| 账户/持仓 | GET | `/api/v1/portfolio/history` | `—` |
| 账户/持仓 | GET | `/api/v1/portfolio/photo-holdings` | `—` |
| 用户偏好 | GET | `/api/v1/preferences` | `—` |
| 用户偏好 | POST | `/api/v1/preferences/news/read` | `NewsReadBody` |
| 用户偏好 | PUT | `/api/v1/preferences/watchlist` | `WatchlistReplaceBody` |
| 研究/策略 | POST | `/api/v1/research/agent-runs` | `CreateResearch` |
| 研究/策略 | GET | `/api/v1/research/agent-runs/{job_id}` | `—` |
| 研究/策略 | POST | `/api/v1/research/agent-runs/{job_id}/cancel` | `—` |
| 研究/策略 | POST | `/api/v1/research/agent-runs/{job_id}/retry` | `RetryResearch` |
| 研究/策略 | GET | `/api/v1/research/benchmarks` | `—` |
| 研究/策略 | POST | `/api/v1/research/benchmarks` | `BenchmarkConfigBody` |
| 研究/策略 | GET | `/api/v1/research/benchmarks/{benchmark_id}/compare` | `—` |
| 研究/策略 | GET | `/api/v1/research/factors/alerts` | `—` |
| 研究/策略 | POST | `/api/v1/research/factors/observations` | `FactorObservationBody` |
| 研究/策略 | GET | `/api/v1/research/factors/{factor}` | `—` |
| 研究/策略 | GET | `/api/v1/research/funds/advisor-tracking` | `—` |
| 研究/策略 | GET | `/api/v1/research/funds/cost-study` | `—` |
| 研究/策略 | GET | `/api/v1/research/funds/cost-study/curves` | `—` |
| 研究/策略 | GET | `/api/v1/research/funds/cost-study/update` | `—` |
| 研究/策略 | POST | `/api/v1/research/funds/cost-study/update` | `—` |
| 研究/策略 | GET | `/api/v1/research/funds/portfolio-risk` | `—` |
| 研究/策略 | POST | `/api/v1/research/funds/public-observations` | `RefreshFundResearch` |
| 研究/策略 | GET | `/api/v1/research/macro/context` | `—` |
| 研究/策略 | GET | `/api/v1/research/macro/observations` | `—` |
| 研究/策略 | POST | `/api/v1/research/macro/observations` | `MacroObservationBody` |
| 研究/策略 | GET | `/api/v1/research/strategy-settings` | `—` |
| 研究/策略 | PUT | `/api/v1/research/strategy-settings` | `StrategyParameters` |
| 研究/策略 | POST | `/api/v1/research/strategy-settings/preview` | `StrategyParameters` |
| 股票研究 | GET | `/api/v1/stocks/candidates` | `—` |

共86个方法/路径组合。完整契约直接从当前服务获取，本文不保存个人账户响应或私有配置。

## 主要操作对应

| 页面行为 | 主要接口 |
| --- | --- |
| 手动账户草稿→差异核对→确认 | `POST /api/v1/imports/fund-photo/manual`、`review`、`commit` |
| 截图识别 | `POST /api/v1/imports/fund-photo/preview` |
| 正式净值预览/确认 | `POST /api/v1/portfolio/fund-nav` |
| 本地成交/分红/冲正 | `/api/v1/ledger/transactions`、`dividends`、`corrections` |
| 实际费率 | `/api/v1/funds/{symbol}/fee-evidence` |
| 剩余批次及费用试算 | `/api/v1/funds/{symbol}/holding-lots`、`holding-redemption-quote` |
| 用户模型发现 | `POST /api/v1/advisor/models/discover` |
| 模型角色读取/选择 | `GET/PUT /api/v1/advisor/model-settings` |
| AI报告 | `GET/POST /api/v1/advisor/reports` |
| 系统定时/通知 | `/api/v1/advisor/schedule`、`notifications` |

所有HTTP入口的`/api/v1/operations/readiness`均读取当前用户的统一准入检查。

## 错误处理

- 参数无效、现金/份额不足、费率或批次缺失：按提示核对，失败操作不变更账户。
- 模型配置版本冲突：重新读取再修改，旧页面不能覆盖新设置。
- 正在执行的AI报告：先查看状态，不重复提交。
- 内部错误：记录请求编号与时间，排查对应服务日志；不要输出私有Key。

架构及维护见[维护说明](维护说明.md)，页面操作见[使用手册](使用手册.md)。
## 登录和用户管理

所有业务API要求账号会话。网页登录与写请求使用同源Cookie和CSRF校验；全局Bearer Token不能替代用户身份。

| 接口 | 权限 | 用途 |
| --- | --- | --- |
| GET /api/v1/auth/status | 公开 | 注册状态、30人上限与剩余名额 |
| POST /api/v1/auth/register | 公开 | 用户名、密码注册普通账号 |
| POST /api/v1/auth/login | 公开 | 登录并设置HttpOnly会话Cookie |
| GET /api/v1/auth/session | 登录 | 当前账号、CSRF校验数据和到期时间 |
| POST /api/v1/auth/logout | 登录 | 撤销当前会话 |
| POST /api/v1/auth/password | 登录 | 修改密码并撤销全部旧会话 |
| GET /api/v1/admin/users | 管理者 | 用户列表和使用数量，支持search筛选 |
| GET /api/v1/admin/users/{user_id}/details | 管理者 | 指定用户的持仓、账本、自选、报告列表或研究任务；只读 |
| GET /api/v1/admin/users/{user_id}/reports/{source}/{report_id} | 管理者 | 指定用户已保存的历史报告正文；只读 |

详情参数：`section=holdings/ledger/watchlist/reports/tasks`（默认holdings）、`page`（从1开始）、`limit`（1至100，默认50）；响应包含用户公开信息、主账本和独立账户快照概况、`total`及当前页`items`，金额与份额保持十进制字符串。报告来源`source=advisor/research`，研究正文通过`attempt_no`选择尝试（默认1）。所有查阅操作使用指定用户可信目录，普通用户返回403，账号或报告不存在返回404，用户数据无法读取返回503。正文不包含输入快照、私有模型配置或密钥。

写请求需正确Origin；已登录写请求还需X-CSRF-Token。注册人数满或用户名重复返回409，未登录401，越权403，参数错误422，请求过频429。账号初始化、统计口径及恢复见[登录与用户管理](登录与用户管理.md)。
