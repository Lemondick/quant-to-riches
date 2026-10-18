# P0 存储与文件映射（storage-map）

- 基线：commit `29568a8a0a8aa9def7241d5d199bdf5d8a111adc`，分支 `codex/multi-user-auth`
- 生成时间：2026-10-05
- 方法：静态解析 `models/repositories/auth.py`（SQLite DDL）与 `models/repositories/schema.sql`（DuckDB DDL），源码引用扫描；未打开任何真实数据库、未读取任何用户数据内容。原始数据见 `storage-inventory.json`，工具见 `tools/extract_storage_inventory.py`。
- 约定：迁移目标按计划书第 2/4 节口径分四类——**PG**（迁入 PostgreSQL 业务库）、**文件**（保留为受版本管理的业务文件）、**Python**（保留给 Python 分析进程单独拥有）、**不迁移**（运行时可再生的缓存/锁）。

## 1. 账号库（SQLite：`data/auth/accounts.sqlite3`）

由 `models/repositories/auth.py` 在启动时 `CREATE TABLE IF NOT EXISTS` 建立，共 **7 张表**。

| 表 | 用途（依据源码） | 迁移目标 |
| --- | --- | --- |
| `users` | 账号 ID、用户名、Argon2id 密码摘要、角色、停用状态 | **PG**（账号与权限域） |
| `sessions` | `quant_session` 不透明会话摘要、到期；索引 `user_sessions` | **PG**（切换时撤销重建，见 §5 决定 D4） |
| `site_settings` | 注册开关等站点级设置 | **PG** |
| `admin_audit` | 管理员敏感读取审计 | **PG** |
| `usage_daily` | 按用户按日使用统计 | **PG** |
| `recovery_tokens` | 15 分钟一次性恢复码 | **PG** |
| `account_activity` | 用户实际活动时间线；索引 `account_activity_user` | **PG** |

说明：正式 `data/auth/accounts.sqlite3` 当前**尚未初始化**（STATUS.md：首次启动按提示创建管理员）；表结构是全量，无历史数据负担，迁移工具仍须以结构为准。

## 2. 每用户业务库（DuckDB：`<data>/users/<user-id>/quant_assistant.duckdb`）

`schema.sql` 共 **54 张 `CREATE TABLE IF NOT EXISTS`**，与管理员根目录 `data/quant_assistant.duckdb` 同构。按业务域分组及迁移目标：

### 2.1 账本与持仓（计划书 P3 迁移范围，差异要求为零）

| 表 | 用途 | 迁移目标 |
| --- | --- | --- |
| `portfolio_accounts` | 平台账户（可选账户，对账用） | **PG** |
| `account_positions` | 账户级持仓快照 | **PG** |
| `account_events` | 账户级事件（含顺序） | **PG** |
| `positions` | 主持仓视图 | **PG** |
| `transactions` | 成交记录（含卖出前成本） | **PG** |
| `dividend_events` | 分红事件 | **PG** |
| `corporate_action_events` | 公司行动 | **PG** |
| `ledger_corrections` | 冲正记录（保留历史证据） | **PG** |
| `portfolio_events` | 组合事件 | **PG** |
| `portfolio_valuations` | 组合估值历史 | **PG** |
| `cash_flow_plans` | 资金计划 | **PG** |
| `drawdown_validations` | 回撤验证 | **PG** |

### 2.2 基金域（费用证据时效、批次）

| 表 | 用途 | 迁移目标 |
| --- | --- | --- |
| `fund_nav` | 基金净值 | **PG** |
| `fund_holding_lots` / `fund_lot_snapshots` | 基金批次与快照 | **PG** |
| `fund_valuation_history` | 估值历史 | **PG** |
| `fund_manager_history` / `fund_profile_history` | 基金经理与档案历史 | **PG** |
| `fund_lifecycle_events` | 基金生命周期事件 | **PG** |
| `fund_holding_history` | 持仓历史 | **PG** |
| `fund_style_drift_events` | 风格漂移事件 | **PG** |
| `fund_fee_schedules` | 费率证据（30 日时效） | **PG** |
| `fund_share_families` | 份额类别 | **PG** |

### 2.3 市场数据与研究证据

| 表 | 用途 | 迁移目标 |
| --- | --- | --- |
| `instruments` / `instrument_history` | 证券主档与历史（股票/基金同代码区分） | **PG**（主档）＋**Python**（历史分析沿用 DuckDB/Parquet） |
| `daily_prices` | 日线行情 | **Python**（分析用；API 读取路径经 PG 缓存或直读，P2 定稿） |
| `data_snapshots` | 数据快照登记（Parquet 引用） | **PG**（登记表）＋**文件**（Parquet 本体） |
| `stock_rankings` | 股票排名 | **Python** |
| `factor_observations` / `factor_alerts` | 因子观察与提醒 | **PG**（用户可读部分）/ **Python**（计算中间） |
| `benchmark_configs` / `macro_observations` | 基准与宏观观察 | **PG** |

### 2.4 策略、回测与模拟

| 表 | 用途 | 迁移目标 |
| --- | --- | --- |
| `strategy_runs` / `signals` / `proposed_orders` | 策略运行、信号与拟议订单 | **PG** |
| `backtest_runs` / `backtest_nav` | 回测运行与净值 | **Python**（研究证据，冻结快照随文件保留）；运行状态入 **PG** |
| `paper_orders` / `paper_fills` / `paper_reconciliations` | 模拟订单、成交、对账 | **PG** |

### 2.5 任务与运行状态（P4 任务协议范围）

| 表 | 用途 | 迁移目标 |
| --- | --- | --- |
| `agent_research_jobs` / `agent_research_attempts` / `agent_preview_snapshots` | 研究任务、尝试、输入快照 | **PG**（任务协议核心表） |
| `job_heartbeats` | 任务心跳 | **PG** |
| `operation_locks` | 运行锁 | **PG**（或改用 PG 顾问锁，P2 决定） |
| `operational_alerts` | 运行提醒 | **PG** |
| `audit_log` | 业务审计 | **PG** |

### 2.6 AI 报告与模型配置

| 表 | 用途 | 迁移目标 |
| --- | --- | --- |
| `market_advisor_reports` | AI 报告正文与状态 | **PG** |
| `advisor_model_settings` | 模型角色选择（revision 冲突保护） | **PG** |
| `advisor_decision_snapshots` | 决策快照 | **PG** |
| `advisor_schedule_runs` | 调度运行记录 | **PG** |
| `advisor_provider_selection` | 供应商选择 | **PG** |
| `advisor_notifications` | 系统内通知 | **PG** |
| `fund_research_snapshots` | 基金研究快照 | **PG**（引用）＋**文件**（快照本体） |

> 逐表列清单（含列名、类型、约束）见 `storage-inventory.json`；该 JSON 为机器可读全量清单，本表为业务分组视图。

## 3. 文件存储（保留为业务文件或 PG 引用）

| 位置 | 内容 | 迁移目标 |
| --- | --- | --- |
| `<user>/preferences.json` | 用户偏好 | **PG**（P2 决定单表 JSONB 或结构化；保持文件兼容读取） |
| `<user>/watchlist.json` | 自选（legacy，admin_user_details 引用） | **PG** |
| `<user>/strategy-settings.json` | 策略参数 | **PG** |
| `<root>/calendars/` | 年度交易日历（服务 `trading_calendar.py`，当前 2026） | **文件**（随备份迁移；来源为上交所公告） |
| `<user>/raw/` | 原始证据与导入原件 | **文件**（PG 存引用与 SHA-256） |
| `<root>/backups/` | 站点备份归档 | **文件**（备份域，不迁 PG） |
| `<root>/server-secrets/` | 网页保存的兼容模型连接（Key 不回显） | **文件**（受限权限单独迁移，不入清单/日志；见计划书 §4 私有密钥规则） |
| `deploy/research-secrets/advisor.json` | 管理员模型私有配置（`QUANT_ADVISOR_CONFIG` 可改路径） | **文件**（本机受控，不迁移、不入 Git） |
| Parquet：`data/snapshots.py`、`forward_sessions.py`、`ledger_replay_engine.py` | 数据快照/前向会话/回放中间数据 | **文件**（Python 分析域拥有） |
| `<root>/runtime/` | 网页构建产物、锁、缓存 | **不迁移**（可重建） |
| `<root>/logs/` | 运行日志 | **不迁移** |
| `evidence-completion-20261005/` 等研究档案 | 已冻结研究证据（含哈希清单） | **文件**（只读归档，完整站点备份覆盖） |

## 4. 动态建表核查结论

全源码扫描 `CREATE TABLE`/`to_sql`：

- 业务库全部建表集中在 `schema.sql`（54 张），启动时 `CREATE TABLE IF NOT EXISTS` 幂等执行；
- SQLite 账号库全部建表集中在 `auth.py`（7 张）；
- 无 pandas `to_sql` 动态建表；无 f-string 拼接建表；
- 特殊路径：`/api/v1/operations/readiness` 路由由 `services/user_context.py` 在每个用户上下文动态注册（非控制器路由），P1 骨架需显式迁移该挂载点。

## 5. 与计划书的差异/未决项

1. **AGENTS.md 不存在**：计划书 §10 要求"阅读仓库 AGENTS.md"，仓库（基线 commit）无此文件；已用 `README.md`＋`web/docs/维护说明.md`＋`STATUS.md` 替代。**需项目负责人确认**是否补建 AGENTS.md。
2. `data/` 当前未初始化账号库/用户目录（仅管理员研究材料），存储映射基于源码结构，P2 迁移工具开发时须以"空库 → 首次初始化"路径为主、真实数据演练为辅。
3. `positions` 与 `account_positions`、`portfolio_events` 与 `account_events` 的主/账户双轨关系需在 P3 逐字段核对（本轮仅登记，未逐列验证语义）。
