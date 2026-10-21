# 网页接口说明

业务接口清单保留原有契约；账号认证和用户隔离从2026-10-04起适用于所有HTTP启动入口。

默认服务地址：`http://127.0.0.1:8765`。业务接口必须携带登录Cookie；写请求还需要当前会话的`X-CSRF-Token`和允许的`Origin`。全局Bearer Token不能替代账号会话。

日期字段使用`YYYY-MM-DD`；日期时间包含时区，业务以北京时间核对；金额/份额响应通常为十进制字符串。
参数、枚举、必填字段及响应详情以同一服务的[OpenAPI契约](http://127.0.0.1:8765/openapi.json)为准（Development 环境可达；公网按运维手册要求返回404）。版本化产物：`web/backend-dotnet/artifacts/openapi.json`（构建期 `dotnet build -p:ExportOpenApi=true` 自动导出，D 的 API 控制台与 CI 校验入口）。

写操作必须通过页面预览和确认流程。超时先读取已保存记录，避免重复录入或重复付费创建任务。
模型发现保存兼容连接和Key到服务端；公开响应不回显Key。AI创建报告可能计费；读取列表及查询状态不会创建新报告。

## 接口清单

2026-10-20 指数接口增量（原生 .NET 网关）：

| 方法 | 路径 | 参数 / 行为 |
| --- | --- | --- |
| GET | `/api/v1/market/snapshot` | 原有分区、基金榜、板块及广度字段保留；新增 `registry_version`、15项 `index_registry` 与独立 `index_statuses`。`indices` 只包含实际合格报价，不用空值或0伪造15条报价 |
| GET | `/api/v1/market/history` | 优先 `index_id=cn.csi.300`；兼容旧 `symbol=000300/000001/399001/399006`，两参数冲突返回400。原四项仍使用原 `symbol` Worker/缓存输入；返回 `id`、`index_id`、市场、币种及 `index_definition` |
| GET | `/api/v1/market/quotes` | 保留股票/指数六位代码参数；指数必须存在注册表，股票与同代码指数分开匹配，未知指数拒绝进入后台队列 |

报价新增稳定 `id`、`currency`、`currency_basis`、`calendar_id`、`source_symbol`、`raw_source_time`、`change_basis`、`cache_fallback`、`cache_read_at`；原 `price/change/change_rate` 保留十进制字符串。来源未给时间时 `observed_at/source_time` 可以为 null，不能使用接收时间代替。`fetched_at` 是原响应接收时间，读取缓存不重写它。

已登记但尚未接通历史的指数返回200、准确ID、空 `items`、`quality_status=unavailable`、`quality_reason=history_not_connected`，不会创建历史任务、返回500或借用其他指数曲线。注册表 `2026-10-21.1` 已核验15项历史映射：腾讯 CN/HK/US、东方财富北证50、Yahoo 日经现货；实际可用性逐次校验，不承诺公共源持续在线。原四项输入不变，其余项 Worker 使用 `index_id`。身份冲突、空历史、无效质量会逐出 API 成功内存缓存并重新准备，返回 `503 public_snapshot_unavailable`，不会把错误身份改名成当前选择。

历史最多返回640条合格日 OHLC，十进制保持文本；日期为实际交易日，不补缺口。`is_partial`/`last_bar_partial` 表示当日未完成或收盘尚未核验；`omitted_incomplete_bars` 说明剔除的来源末行不完整日线，末日落后时标 stale。`history_start/history_end/history_count` 为实际覆盖，`source_quote_time` 仅是同响应报价时钟，不能代替日线收盘证明。日期型历史的 `source_time` 为空，`trade_date/updated_at` 保留末日，`fetched_at` 与读取缓存时间独立。成交量单位尚未核验，暂省略 `volume` 并返回 `volume_status=omitted_unverified_unit`。

快照增量返回四市场 `market_sessions` 与 `refresh_active_markets`；旧 `market_session` 仍是 CN 兼容字段。海外当前核验2026年日历/半日市，缺失年份保持未知。自动采集根据各市场时段，包含各自收盘后5分钟；主行情完成后每轮最多预热2项历史。主快照的旧证据最多保留14天并标 stale，不能把这个保留期当作基金净值时效或实时行情承诺。

注册表为 Python 打包资源与 .NET 嵌入的同一个 JSON；Vue 从接口读取定义。腾讯响应未提供币种，明确标注注册定义/供应商命名空间依据；日经 Yahoo 核验 INDEX/JPY/东京时区，以相邻真实日收盘计算涨跌，不能将多日 `chartPreviousClose` 当作昨日收盘。海外日历未核验时 `unknown_time`，不使用中国日历。公开源可能延迟或限流，采集/缓存周期不是实时性承诺。2026-10-21按用户确认移除暂定中证1000；当前只包含15项已确认指数。

身份核验、实际观察及能力边界见[指数注册表证据](refactor/index-registry-evidence-20261020.md)。

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
| POST | `/api/v1/admin/calendar/auto-generate` | market、year（默认次年）；调 worker 生成，origin=auto 待确认 |
| POST | `/api/v1/admin/calendar/{market}/{year}/confirm` | 确认自动生成的日历（幂等） |
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
| AI报告/模型/调度 | POST | `/api/v1/admin/advisor/schedule/rerun` | user_id、run_date、kind、confirm；窗口外需 confirm=true，否则 409 |
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


### 基金公共分类目录（阶段D）

`GET /api/v1/funds/categories` 返回独立dimensions及真实目录内计数；`GET /api/v1/funds/catalog` 返回分页基金。均需真实账户会话，与用户关注/私人费率隔离。支持q、page、page_size(1..100)；重复维度参数fund_type、asset_class、investment_scope、strategy_tags、product_structure、benchmark_index_id、share_class、regulatory_category、classification_status。维度内OR、维度间AND；strategy_tags全部满足；__unknown筛待核验。

每页带正式NAV、NAV日期/质量/抓取时间、公布日涨幅、分类逐字段证据与规则版本，以及独立目录来源/抓取时间/质量。缺失为null；source_time无供应商发布时间则为null；money_fund_metric_not_verified不当单位NAV。新fund.catalog任务和0024只写派生公共缓存，不写账户/账本。详见[来源与规则决策](refactor/fund-catalog-decisions-20261021.md)。原日涨幅榜/基金详情/估值/关注契约保留。


## 路由清单（机器可读）

本区为机器可读的路由清单，以 `web/backend-dotnet/artifacts/openapi.json`
（构建期 `dotnet build -p:ExportOpenApi=true` 自动导出；Development 环境另有 `/openapi.json` 端点）
为准生成。**新增、删除或改动路由后必须同步更新本区**——CI（`.github/workflows/api-contract-check.yml`）
会从 openapi.json 重新提取路由清单并与本区逐行对比，不一致则失败并输出 diff。

更新方式：重新导出 openapi.json 后，按 `METHOD 空格 path`、字母序逐行替换
两个清单区标记注释之间的内容。

<!-- routes-start -->
DELETE /api/v1/dca/smart/{id}
DELETE /api/v1/goals/{id}
DELETE /api/v1/research/funds/execution-replays/{id}
GET /api/v1/admin/audit
GET /api/v1/admin/audit/verify
GET /api/v1/admin/calendar
GET /api/v1/admin/calendar/status
GET /api/v1/admin/users
GET /api/v1/admin/users/{userId}/details
GET /api/v1/admin/users/{userId}/export
GET /api/v1/admin/users/{userId}/reports/{source}/{reportId}
GET /api/v1/admin/users/{userId}/timeline
GET /api/v1/admin/users/{userId}/usage
GET /api/v1/advisor/model-settings
GET /api/v1/advisor/notifications
GET /api/v1/advisor/reports
GET /api/v1/advisor/reports/{report_id}
GET /api/v1/advisor/schedule
GET /api/v1/alerts/center
GET /api/v1/alerts/center/settings
GET /api/v1/announcements
GET /api/v1/attribution/reports
GET /api/v1/attribution/reports/{report_id}
GET /api/v1/attribution/reports/{report_id}/export
GET /api/v1/auth/2fa/status
GET /api/v1/auth/session
GET /api/v1/auth/sessions
GET /api/v1/auth/status
GET /api/v1/backtests/latest
GET /api/v1/cost/positions
GET /api/v1/dashboard/summary
GET /api/v1/data/status
GET /api/v1/dca/plans
GET /api/v1/dca/smart
GET /api/v1/dca/smart/{id}
GET /api/v1/dca/tracking
GET /api/v1/decisions/latest
GET /api/v1/funds/candidates
GET /api/v1/funds/catalog
GET /api/v1/funds/categories
GET /api/v1/funds/health/latest
GET /api/v1/funds/lifecycle/events
GET /api/v1/funds/style-drift/events
GET /api/v1/funds/valuations
GET /api/v1/funds/valuations/alerts
GET /api/v1/funds/{symbol}/detail
GET /api/v1/funds/{symbol}/fee-evidence
GET /api/v1/funds/{symbol}/holding-lots
GET /api/v1/goals
GET /api/v1/goals/{id}
GET /api/v1/history/{section}
GET /api/v1/history/{section}/export
GET /api/v1/ledger
GET /api/v1/ledger/attribution
GET /api/v1/ledger/corporate-actions
GET /api/v1/ledger/corrections
GET /api/v1/ledger/dividends/{id}
GET /api/v1/ledger/portfolio-history
GET /api/v1/ledger/reconciliation/accounts
GET /api/v1/ledger/reconciliation/history
GET /api/v1/ledger/reconciliation/{identity}
GET /api/v1/ledger/replay/batch-history
GET /api/v1/ledger/replay/history
GET /api/v1/ledger/replay/{previewId}/receipt
GET /api/v1/ledger/transactions/{id}
GET /api/v1/market/history
GET /api/v1/market/news
GET /api/v1/market/quotes
GET /api/v1/market/snapshot
GET /api/v1/net-worth
GET /api/v1/net-worth/allocation
GET /api/v1/notify/email/log
GET /api/v1/notify/email/settings
GET /api/v1/onboarding
GET /api/v1/operations/readiness
GET /api/v1/operations/trends
GET /api/v1/operations/watchdog
GET /api/v1/peers/funds/{symbol}/ranking
GET /api/v1/peers/groups/{group_id}/rankings
GET /api/v1/plans
GET /api/v1/plans/due
GET /api/v1/portfolio
GET /api/v1/portfolio/drawdown
GET /api/v1/portfolio/photo-holdings
GET /api/v1/preferences
GET /api/v1/rebalance/targets
GET /api/v1/research
GET /api/v1/research/agent-runs
GET /api/v1/research/agent-runs/{job_id}
GET /api/v1/research/benchmarks
GET /api/v1/research/benchmarks/{benchmarkId}/compare
GET /api/v1/research/factors/alerts
GET /api/v1/research/factors/{factor}
GET /api/v1/research/funds/advisor-tracking
GET /api/v1/research/funds/cost-study
GET /api/v1/research/funds/cost-study/curves
GET /api/v1/research/funds/cost-study/update
GET /api/v1/research/funds/execution-replays
GET /api/v1/research/funds/execution-replays/{id}
GET /api/v1/research/funds/execution-replays/{id}/export
GET /api/v1/research/funds/portfolio-risk
GET /api/v1/research/macro/context
GET /api/v1/research/macro/observations
GET /api/v1/research/previews
GET /api/v1/research/strategy-settings
GET /api/v1/risk/metrics
GET /api/v1/risk/stress
GET /api/v1/runtime/costs
GET /api/v1/runtime/status
GET /api/v1/runtime/workers/snapshot
GET /api/v1/stocks/candidates
GET /api/v1/valuation
POST /api/v1/admin/advisor/schedule/rerun
POST /api/v1/admin/calendar/auto-generate
POST /api/v1/admin/calendar/{market}/{year}/confirm
POST /api/v1/admin/registration
POST /api/v1/admin/users/{userId}/recovery-token
POST /api/v1/admin/users/{userId}/revoke-sessions
POST /api/v1/admin/users/{userId}/role
POST /api/v1/admin/users/{userId}/status
POST /api/v1/advisor/jev-models/discover
POST /api/v1/advisor/models/discover
POST /api/v1/advisor/notifications/{notice_id}/acknowledge
POST /api/v1/advisor/preflight
POST /api/v1/advisor/reports
POST /api/v1/alerts/center/{id}/acknowledge
POST /api/v1/analysis/explain
POST /api/v1/announcements/sync
POST /api/v1/ask
POST /api/v1/attribution/reports
POST /api/v1/attribution/reports/automatic
POST /api/v1/attribution/reports/link
POST /api/v1/attribution/reports/{report_id}/calculate
POST /api/v1/auth/2fa/confirm
POST /api/v1/auth/2fa/disable
POST /api/v1/auth/2fa/enable
POST /api/v1/auth/2fa/recovery-codes/regenerate
POST /api/v1/auth/2fa/verify
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/password
POST /api/v1/auth/recovery/redeem
POST /api/v1/auth/register
POST /api/v1/auth/sessions/revoke-others
POST /api/v1/auth/sessions/{session_id}/revoke
POST /api/v1/backtest/portfolio
POST /api/v1/dca/executions
POST /api/v1/dca/executions/skip
POST /api/v1/dca/plans
POST /api/v1/dca/plans/{plan_id}/calculate
POST /api/v1/dca/smart
POST /api/v1/dca/smart/{id}/backtest
POST /api/v1/funds/health/run
POST /api/v1/funds/{symbol}/fee-evidence
POST /api/v1/funds/{symbol}/fee-quote
POST /api/v1/funds/{symbol}/holding-lots
POST /api/v1/funds/{symbol}/holding-redemption-quote
POST /api/v1/funds/{symbol}/redemption-quote
POST /api/v1/goals
POST /api/v1/goals/{id}/project
POST /api/v1/imports/fund-photo/commit
POST /api/v1/imports/fund-photo/confirm
POST /api/v1/imports/fund-photo/holdings-commit
POST /api/v1/imports/fund-photo/holdings-review
POST /api/v1/imports/fund-photo/manual
POST /api/v1/imports/fund-photo/merge
POST /api/v1/imports/fund-photo/preview
POST /api/v1/imports/fund-photo/review
POST /api/v1/ledger/actions
POST /api/v1/ledger/corrections
POST /api/v1/ledger/dividends
POST /api/v1/ledger/reconciliation/compare
POST /api/v1/ledger/reconciliation/{identity}/resolve
POST /api/v1/ledger/replay/batch-preview
POST /api/v1/ledger/replay/confirm
POST /api/v1/ledger/replay/preview
POST /api/v1/ledger/transactions
POST /api/v1/notify/email/settings/test
POST /api/v1/operations/heartbeats
POST /api/v1/plans
POST /api/v1/plans/{id}/status
POST /api/v1/portfolio/drawdown/validate
POST /api/v1/portfolio/fund-nav
POST /api/v1/preferences/news/read
POST /api/v1/rebalance/evaluate
POST /api/v1/research/agent-runs
POST /api/v1/research/agent-runs/{job_id}/cancel
POST /api/v1/research/agent-runs/{job_id}/retry
POST /api/v1/research/benchmarks
POST /api/v1/research/factors/observations
POST /api/v1/research/funds/cost-study/update
POST /api/v1/research/funds/execution-replays
POST /api/v1/research/funds/public-observations
POST /api/v1/research/macro/observations
POST /api/v1/research/refresh
POST /api/v1/research/screener
POST /api/v1/research/strategy-settings/preview
POST /api/v1/runtime/costs/{identity}/bill-evidence
POST /internal/v1/worker/claim
POST /internal/v1/worker/cost-finish
POST /internal/v1/worker/cost-reserve
POST /internal/v1/worker/finish
POST /internal/v1/worker/heartbeat
PUT /api/v1/admin/calendar
PUT /api/v1/advisor/jev-connection
PUT /api/v1/advisor/model-settings
PUT /api/v1/alerts/center/settings
PUT /api/v1/dca/smart/{id}
PUT /api/v1/goals/{id}
PUT /api/v1/notify/email/settings
PUT /api/v1/plans/{id}
PUT /api/v1/preferences/watchlist
PUT /api/v1/rebalance/targets
PUT /api/v1/research/strategy-settings
PUT /api/v1/runtime/costs
<!-- routes-end -->
