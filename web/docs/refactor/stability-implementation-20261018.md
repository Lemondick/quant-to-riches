# 稳定性整改与功能补充实施记录

依据用户提供的《01 当前问题修复与稳定性整改需求》和《02 功能优化与功能补充实施需求》分阶段执行。

## 基线与边界

- 审查基线：`4311614e0f60086048875f97354d2283c22ed31e`。
- 独立工作区：由 Codex 管理的 `stability-market` worktree；原工作区的未提交修改保留。
- 不合并 main，不部署生产，不修改生产数据库、账本或真实账户，不触发交易或付费模型。
- CI 使用 GitHub 临时 runner 上的原生 PostgreSQL 18、独立 `quant_test` 数据库和合成夹具。

## 《01》阶段 A：BUG-001 / BUG-002 — PASS

### 根因与修改

- Worker 测试启动子进程时未提供真实父 PID，Linux 在读取任务前拒绝启动。
  修改 `web/backend/scripts/verify_research_worker.py` 的测试环境，保留生产 `prctl` 和父 PID 保护；增加错误/缺失父 PID 拒绝与无密钥不联网测试。
- PostgreSQL 的 `quant-ci` 名称与 systemd 路径转义不兼容，改为 `quantci`。
  `.github/workflows/ci.yml` 增加有限就绪等待、真实 SQL 连通检查和故障诊断，并拆分四个独立作业。
- `.github/scripts/test_report.py` 和 `test_report_tests.py` 区分总数、实际执行、通过、失败、错误与跳过，并记录跳过原因；空测试、全跳过、所需数据库测试被门控时不判成功。
- 首轮发现自建报告入口缺少 `python -m unittest` 的当前目录导入路径；已先新增失败回归，再修复路径，保留失败与通过记录。
- `.gitignore` 排除生成的 `test-results/`。

### 实际命令与结果

对应修复提交：`dca03748d936e20f28ec6ecd5cfbe6e21c0b36a9`。
Linux 验收：[Actions 39118797518](https://github.com/Lemondick/quant-to-riches/actions/runs/39118797518)，四项作业全部成功。

| 验证 | 实际命令或作业 | 通过 | 失败/错误 | 跳过 |
| --- | --- | ---: | ---: | ---: |
| Python 业务 | `test_report.py --start scripts --pattern 'verify_*.py'` | 446 | 0 | 0 |
| 结果报告回归 | `test_report.py --start .github/scripts --pattern 'test_*tests.py'` | 5 | 0 | 0 |
| .NET / PostgreSQL | `dotnet test QuantAssistant.slnx -c Release --no-build --logger trx` | 226 | 0 | 0 |
| 原生数据库工具 | `test_report.py --start web/backend-dotnet/tools/tests` | 15 | 0 | 0 |
| Python Worker | `test_report.py --start web/worker/tests` | 105 | 0 | 0 |
| .NET 编译 | 独立 `dotnet-build` 作业 | PASS | 0 | 0 |
| 前端 | 独立 lint、format、typecheck、tests、production build、浏览器验收 | PASS | 0 | 0 |

本地另执行 `actionlint`、Ruff、无密钥扫描、`.NET build` 和报告回归。
Windows 的 Worker 组为 17 通过、1 项 Linux 专属测试跳过；最终验收采用上述 Linux 全量结果，不把 Windows 跳过计作通过。

### 未解决范围

此阶段只恢复 CI，不等同于生产业务验收，也不修复账本、行情日历或回测的后续问题。
GitHub required checks 的管理员配置未修改；独立作业的实际结果可见于 Actions。

## 《01》阶段 B：BUG-003 / BUG-004 — PASS

### 根因、修改和兼容边界

- 五类事件查到重复键即返回成功，成交路径的冲突又使用 422，造成同编号修改被误报成功和错误语义不一致。
- `TransactionService` 六类写入现在都在现有账户事务锁内验证请求；同键同内容只读返回原 ID，不同内容抛出 `ApplicationFailure(idempotency_conflict, 409)`。保持原唯一约束，没有新增迁移。
- `LedgerRequests` 生成规范化业务字段；`LedgerIdempotency` 按对象键排序、十进制精确值、UTC instant 计算指纹，新审计事件增加 `request_fingerprint`。校准快照按规范化证券代码排序；持仓内容、估值日、净外部流和原因都参加比较。
- `ILedgerRepository` / `LedgerRepository.ReadRecordedRequest` 在同一事务内按账户、事件类型和 ID 读取原事件与审计指纹；表名来自固定白名单，参数值绑定。
- 旧事件没有指纹时逐字段核验，不更新历史行。旧校准支持原 SHA256 算法；旧公司行动中省略的派生数量不能还原成原始输入，按已记录数量及其他完整业务字段核验。无法证明一致的内容拒绝为冲突。
- 缺省现金币种使用原事件实际币种核验，后续增加币种不影响重试。成交显示名称不属于资金字段，也不会在重试时改写 instrument；保留原迁移成交经前端补带名称的兼容行为。
- 分红首次写入按上海到账日检查 `paidDate <= latestValuation`，返回稳定的 `dividend_date_invalid`（422），同时保留现金账本日期检查。完全相同的旧分红重试先返回原 ID，不受后来推进的估值日影响。

### 调用链

- `LedgerController` 成交、分红、公司行动和冲正接口调用上述 Core 服务，错误经现有异常翻译器返回。
- 外部资金流、组合校准的 Core 入口也覆盖同一语义；本阶段没有把其他独立导入/照片确认工作流误称为这些方法的调用方，也没有重写其历史事件。
- `portfolio_events` 仍保留原账户内 event_id 共享命名空间；跨类型占用同一 ID 时拒绝冲突，不拆分历史主键。

### 实际测试证据

- 回归测试先行提交 `d3ee922`：[Linux 红灯 39119875881](https://github.com/Lemondick/quant-to-riches/actions/runs/39119875881)，266 项实际执行，251 通过、15 失败、0 跳过；明确复现六类内容冲突、并发冲突、分红日期边界和 HTTP 201 误报。
- 实现首轮 `00eb27c`：272 项中 271 通过、1 失败，原迁移成交兼容测试发现显示名称误入指纹。修复后保留该原测试并补充显示名称不改写数据的用例。
- 最终修复提交 `9fd1dfd7e2cc6c29fa37241b381d74462d836dba`：[Linux 验收 39120733964](https://github.com/Lemondick/quant-to-riches/actions/runs/39120733964)，四作业成功。

| 验证 | 实际通过 | 失败/错误 | 跳过 |
| --- | ---: | ---: | ---: |
| .NET / PostgreSQL | 272 | 0 | 0 |
| Python 业务 | 446 | 0 | 0 |
| Python Worker | 105 | 0 | 0 |
| 原生数据库工具 | 15 | 0 | 0 |
| 结果报告回归 | 5 | 0 | 0 |

前端 lint、format、typecheck、tests、build、浏览器回归和独立 .NET build 均 PASS。新增 `LedgerIdempotencyTests` 共 46 个 Linux 用例，逐字段冲突循环不额外夸大为独立用例数；并发测试另与单次执行的实际持仓/现金结果比较。拒绝及重试前后使用九类金融表快照核验没有部分写入。

本地编译零警告/错误；非数据库测试 85 通过、152 因缺少 PostgreSQL 跳过。通过结论采用上面的真实 Linux 全量结果。无密钥扫描通过。未修改生产环境或原工作区。

## 《01》阶段 C：BUG-005 至 BUG-009 — PASS

计划：`docs/superpowers/plans/2026-10-18-public-data-quality.md`，按已授权顺序在 `codex/public-data-quality` 分支执行。

### 日历证据链 — 子任务 1 PASS

- 先在 `b0d7f97` 新增真实 HTTP/PG 回归：[红灯 39121190851](https://github.com/Lemondick/quant-to-riches/actions/runs/39121190851)，274 实际执行，272 通过、2 失败、0 跳过，复现 HK 误用 CN 与 Worker 缺少权威日历。
- `e169b5fe43b99c6448ce903a3a59c69d35830295`：[绿灯 39121691596](https://github.com/Lemondick/quant-to-riches/actions/runs/39121691596)，.NET/PG 276、Worker 109、Python 446、数据库工具 15、报告 5 均通过且 0 跳过；前端和独立编译通过。
- `TradingCalendarRepository` 保持权威来源。旧 `calendar:<year>` 键继续代表 CN，HK/US/JP 独立键；官方来源域名按市场校验，保存保留 revision 冲突检查和管理员审计，并使快照缓存失效。
- 原 CN 2026 内置兜底移至唯一 `exchange_calendars.json`，C# 嵌入资源和 Python wheel 复用；wheel 已实测包含同一资源，不为其他市场或年份编造假日。
- 已认证 Worker claim 注入只读 `_calendars`（市场、年份、来源、修订和休市日），覆盖任务自带的同名内容，不改变原查询缓存键，不提供数据库凭据。

### 时段与时间解析 — 子任务 2 PASS

- 共用 `tests/fixtures/public-quality-cases.json`：本地先复现 C# 23 项边界/解析失败、Python 无时区误判，以及 Worker 未使用任务日历；均保留 RED 日志。
- 实现六类时段与严格 ISO 偏移解析，删除 quality 中重复的假日表；明确偏移按 UTC instant 比较，无时区为 `unknown_time`。缺少日历或已核验市场时段时保持未知，不能套用 A 股规则。
- 竞价能力缺省 false。能力随 Observation 与只读快照传递，两端使用一致判断；当前公开来源未被证实支持竞价，没有把测试中的合成能力声明当作真实供应商能力。
- provider 实例按不可变日历快照使用有界缓存；并发任务不改进程全局日历。缓存读取重新评估时效，保持原 source_time 和 fetched_at。
- 本地最终结果：.NET 128 通过、155 数据库项跳过；Worker 114/114；前端 43/43，lint/typecheck/build 通过；Python source verify 通过（运行准入仍按原配置阻断，不代表已接入生产）。
- 前端只补充时段标签并兼容旧 `trading`/`break` 值，不调整布局、CSS 或主题。

最终 `8006a8051c98aa2f5a8c0ed88712d9e8f91143de`：[Linux 验收 39123091682](https://github.com/Lemondick/quant-to-riches/actions/runs/39123091682)，.NET/PG 317、Worker 114、Python 446、数据库工具 15、报告 5 全部通过，0 跳过；前端浏览器和编译通过。

### 正式净值发布策略 — 子任务 3 PASS

- 用例先复现 C# 14 项策略失败与 Python 3 项旧净值丢失/误报；另验证了视图白名单丢失策略、周频忽略发布滞后的问题，均先 RED 后修复。
- 共用 `nav_freshness_policies.json` 配置应用阈值：日频 `daily_t1` 最多 1 个已完成交易日，QDII `qdii_t3` 最多 3 个；周频需明确估值星期，另计可配置发布滞后（默认 1 个交易日）。这些是应用时效容忍规则，不代表公开源保证的更新时间。
- 配置 JSON 同时嵌入 C# 和 Python 包。基金类型取真实 profile 的结构化类型字段，不根据基金名称推断；货币基金、FOF、泛“指数型”和缺少发布信息的情况保持时效未知。未知日历不能靠工作日猜测；旧日期仍保留并显式标为旧证据。
- 两个 provider 取消默认 14 自然日拒绝。保留原始值、来源与日期，正式 NAV 不写入 estimate_nav；没有源日增长率时不再把两个非连续日期之差称作官方日涨跌幅。
- 旧 `max_nav_age_days` 参数只在调用方显式提供时增加更严格的旧证据标记，不丢弃历史观测；缺省不再使用该自然日规则。
- 权威净值资料的类型、策略和原采集时间经 gateway/local provider/只读视图传递；接口读取重新评价净值时效，不修改历史账本。
- 现有基金资料状态标签和图表说明显示“旧官方净值”或“时效待确认”，没有改动布局、主题或 CSS。
- 本地全量：.NET 150 通过 / 155 PG 跳过；Worker 120/120；Python 445 通过 / 1 Linux 专属跳过；前端 43/43，typecheck/lint 通过。真实 PG 和 Linux 专属门禁仍等待本次 CI，不把本地跳过计为通过。

最终 `e28b050872850c053f57cc73e95cb1b0eaa8c4ca`：[Linux 验收 39124567531](https://github.com/Lemondick/quant-to-riches/actions/runs/39124567531)，.NET/PG 339、Worker 120、Python 446、数据库工具 15、报告 5 全部通过，0 跳过；前端浏览器和编译通过。

### 板块目录证据 — 子任务 4 PASS

- 先复现“收到 25 行就声称供应商总数 25”、HTTP 200 被当作解析成功、旧缓存伪总数升级等问题；新增 Python 5 项回归和 C# 旧快照修正回归，保留 RED 日志。
- 东方财富注册目录及新浪接口未提供权威目录总数，`total`、`complete` 保持 null，`catalog_status=unknown`。东方财富行情分页 `quote_total` 单独保留，`quote_payload_complete` 只说明行情分页完成；没有报价的注册条目仍显示 null。
- 独立提供 `http_ok`、`parse_ok`、`quotes_complete`、`source_time_known`、`cache_used`，旧缓存保留原始行情/采集时间及本次失败状态。缓存格式升级为 v2；旧版 `total` 只用于校验缓存行数，不能当作供应商总数。
- 2026-10-18 只读探测：东方财富注册接口实际接收 1044 条，新浪行业接口接收 49 条，均无权威目录总数字段。这些是当次接收数量，不是全市场总数，也不代表当前实时行情。
- Native gateway 修正已存的旧版公开快照，不再通过它展示伪完整状态。前端仅调整现有状态文案与可用来源选择，不改布局、主题或 CSS。
- 本地 .NET 151 通过、155 PG 跳过；Worker 125/125；前端 43/43 与 typecheck/lint/build 通过；source verify、Worker ruff 通过。真实数据库及 Linux 专属用例以随后 CI 为准。

`d14a6b75efa2ff12b3ba6cee186f28d9e076ad39`：[Linux 验收 39385382795](https://github.com/Lemondick/quant-to-riches/actions/runs/39385382795)，.NET/PG 340、Worker 125、Python 446、数据库工具 15、报告 5 全部通过，0 跳过；前端浏览器和编译通过。

### 阶段 C 整体审查与补修 — PASS

独立只读审查发现两项 Important，无 Critical、无 Minor；都在同一轮先 RED 后修复：

- Python `fromisoformat` 会接受并归一化非法 `+08:60` 偏移。解析前明确限制分钟 0–59、偏移范围 ±14:00，与 .NET 契约一致。共享 fixture 新增非法正负分钟、越界与合法范围边界；Python 先有 5 项失败，修复后通过。
- 目录内存缓存回退标记被丢弃，最新报价掩盖注册目录刷新失败。新增独立 `catalog_fetched_at`、`catalog_http_ok`、`catalog_parse_ok`、`catalog_cache_used`、`catalog_stale`、`catalog_error`；保留报价状态与报价原始时钟。失败目录沿用原采集时间，无报价条目也不再使用新报价采集时间。界面明确“目录刷新失败，保留旧目录；报价按原时间展示”。传输失败与 HTTP 200 解析失败两个回归均先失败后通过。
- 本地最终 .NET 158 通过 / 155 PG 跳过，Worker 126/126，source verify 与 Worker lint/format 通过；修正 Python 业务测试的运行目录后为 445 通过 / 1 Linux 专属跳过。最初从仓库根运行的 2 项 import 错误没有计为通过。
- 审查未评价的刷新器调度属于下一阶段 D；缺少海外日历、竞价能力和目录总数时继续保持未知，不编造证据。

最终 `f82a329e28550c3998c87571d00c8bcd53f60c08`：[Linux 验收 39386644058](https://github.com/Lemondick/quant-to-riches/actions/runs/39386644058)，.NET/PG 347、Worker 126、Python 446、数据库工具 15、报告 5 全部通过，0 跳过；前端浏览器和独立编译通过。两项审查补修均已覆盖，未留下 Minor。实现取舍及不确定性见 `public-data-quality-decisions-20261020.md`。

## 《01》阶段 D：BUG-010 — PASS

计划 `docs/superpowers/plans/2026-10-20-public-refresh-scheduling.md`；独立分支 `codex/public-refresh-scheduling`。

- 根因：按本轮开始时刻计算下次刷新，抓取超过 30 秒后仅等待 1ms；历史任务先于核心行情入队；没有整体 deadline；全空行情既能覆盖内存，也能通过数据库触发器覆盖持久化缓存。
- 先在 `bad90a5` 跑出 [Linux RED 39388178477](https://github.com/Lemondick/quant-to-riches/actions/runs/39388178477)：353 实际执行，347 通过、6 失败、0 跳过。随后 `7b892c4` 的 [持久化 RED 39388681677](https://github.com/Lemondick/quant-to-riches/actions/runs/39388681677)：354 实际执行，347 通过、7 失败、0 跳过，确认旧快照时间被空结果更新。
- 改为每轮完成后等待配置间隔，默认 30 秒（15–300）；整体 deadline 默认 75 秒（10–180）；子任务默认 55 秒（5–120 且不超过整体剩余时间）。TimeProvider 驱动等待与取消，单循环不重叠，不补跑错过的轮次。显式请求合并到下一轮，可在休市及自动预热关闭时执行，也遵守间隔；代价是手动请求可能等待一个配置间隔。
- 优先 await `market.snapshot`；成功发布后才排队历史、报价及基金。失败停止本轮辅助排队，按轮次记录警告；数据和 completed_at 使用同一次持久化读取，保留来源原始时钟。
- 内存和新增迁移 `0023_public_market_snapshot_guard.sql` 拒绝全空行情覆盖。迁移只保护派生公开缓存，没有修改已有金融事件或财务历史。架构文档、配置示例与 API `refresh_interval_seconds` 统一复用新配置；公开快照超过两个间隔标 stale。
- 以可控时钟模拟 35/60 秒、子任务与整体超时、失败恢复、休市显式刷新；停止服务的 finally 同时验证取消等待。补充配置边界和内存/数据库两层旧证据保留。
- 本地最终 .NET 164 通过、163 PG 项跳过；source verify/secret scan 通过。实际数据库和整个 Linux 门禁尚未通过前，不将这些跳过计入成功。
- Git HTTPS 连接重置/端口连接失败时，通过已授权的 GitHub Git 数据接口上传，逐个校验 blob、tree 与 commit SHA 和本地完全一致；没有改写 main。

最终 `93fc4db8cf92bde943c4a382c58fa7ae501a777e`：[Linux 验收 39390147290](https://github.com/Lemondick/quant-to-riches/actions/runs/39390147290)，.NET/PG 361、Worker 126、Python 446、数据库工具 15、报告 5 全部通过，0 跳过；前端浏览器和独立编译通过。独立审查无 Critical、Important、Minor。首轮门禁发现测试辅助配置键重复及旧 `Warm(...)` 文本断言，已修复测试配置覆盖，并以真实 PG 行为用例代替过时的方法名依赖，没有减少原有业务断言。

取舍：新建任务期限受当前轮次预算限制；如果队列合并复用了其他调用方已存在的任务，保留它的原 deadline。本轮等待仍及时取消，下一轮同内容继续合并，不缩短其他调用方的任务生命周期；代价是已存在的共享任务可能运行至原截止时间。默认手动刷新也等待完成后间隔，避免绕过失败退避。迁移 0023 为此阶段新增的派生缓存保护，不修改财务历史。

## 《01》阶段 E：BUG-011 至 BUG-013 — PASS

计划 `docs/superpowers/plans/2026-10-20-backtest-correctness.md`，分支 `codex/backtest-correctness`。

### BUG-011 样本外曲线 — PASS

- 先运行 9 项数学/证据回归，11 个断言失败；累计回撤、不同长度折的波动率/夏普、缺失收益等均已复现。另有历史手工报告的 4 个误标失败、缺失预期交易日期的失败，均先 RED 后 GREEN。
- 新增 `backtest/oos.py` 保存每折实际起点、调整后净值和基准收盘、逐日策略及基准收益。原生 BacktestResult 已提供的 `trading_dates` 现为 OOS 必需信息；它与实际 NAV、benchmark 必须逐日一致。只提供摘要、只有一个收盘、缺失观测或日期越界都拒绝，不能推测日收益。
- 拼接有效收盘观测之间的收益后复用 `calculate_performance` 重算整体收益、年化、波动、夏普、最大回撤和基准/超额；不再平均各折统计量。每折首条收盘是起点，不编造窗口前价格；折间区间明确列出并省略，未观测收益不填零，年化按有效收益观测数。
- 费用为各折实际费用合计，换手为各折双边换手合计，保留各折原分母；报告明确其口径，不冒充连续资本账户的全期换手。JSON 暴露逐日观测与方法，Markdown 展示起点、观测数及排除区间；旧手工结果缺少证据时保持口径未知并标研究用途。
- 专项 11/11 通过。此前同轮全量 Python 为 455 通过、1 Linux 专属跳过（456 项，最后新增的日期声明用例另在专项执行）；source verify/ruff/format/secret scan 通过。尚未把本地跳过项计为 Linux 通过。BUG-012 / BUG-013 待后续任务。

`6e9982b1eb74bd75ca0e136893072be02204b39b`：[Linux 39392460995](https://github.com/Lemondick/quant-to-riches/actions/runs/39392460995)，Python 457、.NET/PG 361、Worker 126、数据库工具 15、报告 5 全通过且 0 跳过，前端浏览器及构建通过。

### BUG-012 同日冲突 — PASS

- 原来 `keep=last` 会按输入顺序静默选用同日净值、外流、费用、换手和基准值；先以 7 项用例复现 12 个失败断言，另复现缺失索引日期被当成观测。
- 在规范化日期后、float 转换前比较原始整条观测。只有相同记录去重；冲突返回 `conflicting_duplicate_date`，不自行合并多笔流水。原始日期标签以规范日键表示，因此同日不同时刻但数值完全相同的日观测可去重。
- 日期列与 DatetimeIndex 共用规则；缺失日期/索引拒绝。随机重排 8 组记录保持全部指标一致，Decimal 微差在丢失精度前被识别。指标 8 项与 OOS 11 项共 19/19 通过，source verify/ruff/format/secret scan 通过。


`f06eb728f65651eed4912c88e0ce836b1623c93c`：[Linux 39393855472](https://github.com/Lemondick/quant-to-riches/actions/runs/39393855472)，Python 465、.NET/PG 361、Worker 126、工具 15、报告 5 全通过且 0 跳过，前端浏览器与构建通过。

### BUG-013 策略未来价格边界 — PASS

- 盘点确认 `prices_all` 唯一生产消费者为股票特征适配器，已删除字段与传参。原前缀为 pandas2 浅拷贝，测试确实改写底层价格；现在价格与证券资料在决策上下文入口各深拷贝一次，策略之间仍按只读约定共享，避免每个策略重复复制。
- 先 RED：未来字段可读、特征算子收到未来数据、策略污染源；修复后前缀只含截至日，未来字段不可读。保留价格、证券资料时点、上市/退市与 snapshot ID 准入检查。
- 直接逐日前缀重算特征造成明显成本：100 只合成证券、500 个交易日、60 次决策/6000 行特征，原 0.5399 秒、前缀重算 16.5474 秒。按需求允许的非信号准备边界隔离：引擎独享全量因果特征表，策略仅收到当日/上一周末的独立切片、截至当日交易日与无回链的 revision token。相同合成规模实测 0.6828 秒；这是特征阶段本机测量，不是 API 延迟或生产负载承诺。
- 前缀路径保留实例缓存，持有源对象与 snapshot ID，避免只用可复用 `id()`；引擎准备视图每次回测使用新 revision，跨回测不复用旧数据。缺失特征列继续产生原来的 `strategy_error:ValueError` 限制，不伪造特征。
- 实际内置策略的双路径比较又复现旧买入候选循环把 dict 交给 Series 专用函数的错误，导致 `dict.index` 异常；仅将该循环改为 `iterrows()`，恢复原辅助函数接口。回归要求确实产生信号，防止空信号一致掩盖问题。
- 专项 12 项覆盖未来扰动、字段/日期拒绝、源隔离、准备/前缀实际信号相同、停牌及上一周切片、跨回测缓存、缺失列、证券准入；与 OOS/冲突共 31/31 通过，source verify/ruff/format/secret scan 通过。前一轮全量本地 475 项、474 通过、1 Linux 专属跳过；新增的双路径等最后 2 项已另跑专项，完整现态等待 Linux CI，未把本地跳过计成通过。

最终源码 `e07aa833012fcb508e81e47de6c14eb2e73b3f63`：[Linux 39395377198](https://github.com/Lemondick/quant-to-riches/actions/runs/39395377198)，Python 477、.NET/PG 361、Worker 126、工具 15、报告 5 全通过，0 跳过；前端、浏览器回归、构建通过。独立整体审查无 Critical / Important / Minor，另独立复跑 31 项专项全过；[全部取舍记录](backtest-decisions-20261020.md)。没有合并 main、部署或改写真实财务数据。

## 《01》阶段 F：BUG-014 与文档 — PASS

分支 `codex/model-network-boundary`，继承阶段 E 文档提交 `99ae143`。实际源码 `8b9a2e3c08a7c5434ee6526c679e99bce740643f`：[Linux 39424757711](https://github.com/Lemondick/quant-to-riches/actions/runs/39424757711)，Python 492、.NET/PG 385、Worker 128、数据库工具 15、报告 5 全通过，0 跳过；前端、浏览器和构建通过。

- 兼容研究子进程在每次实际连接时校验全部 DNS 结果后固定数值 TCP 目标，默认可信 TLS 保留原域名的 SNI/证书/Host；混合非公开地址整组拒绝，不会再次解析域名或使用代理。每次重试重新验证，读失败不重放 POST，父进程原有 deadline/parent PID 保护继续保留。
- 支持公开自定义 HTTPS443 域名，URL 不含凭据、查询、片段、编码/点/反斜线/重复斜线路径；没有擅自改成固定供应商 allowlist。原生 Worker 复用同一标准库地址/路径规则，保留既有连接池；.NET 修正歧义路径、过渡、IETF 非公开和文档网段遗漏，保留 IANA 已登记公开子分配。
- 先 RED 后 GREEN 的证据包含 DNS rebind/mixed/private、URL/端口、IPv4-mapped/IPv6、TLS fallback、总连接预算、禁止 read retry、无 key 零联网、provider key echo。专项网络15、目录30、原生Worker128通过；Windows旧子进程18项中1项Linux专属跳过，最终Linux已真实执行且无跳过。
- README、架构、前端说明、原生和Ubuntu手册统一当前入口、0001–0023迁移、默认30秒及75/55秒预算；删除过时14天净值统一阈值说明。升级步骤补迁移后才启动，保留恢复到新空库/新目录与财务指纹流程。对照阶段E确认6个备份/恢复文件Git blob未改；Windows工作区换行规范化后内容一致，未把CRLF字节差异误认为源码变化。
- [完整边界和主来源依据](model-network-boundary-20261020.md)。未调用真实模型、访问真实内部网络、合并main或部署；真实服务器运维验收继续未执行。

最终审查的完整提交 `b3d54e63db77f91de6c1b09741db7948b9b69089`：[Linux 39425425785](https://github.com/Lemondick/quant-to-riches/actions/runs/39425425785) 四 job 成功，仍为 Python492/.NETPG385/Worker128/工具15/报告5全通过且0跳过，前端/浏览器/构建成功。独立审查无 Critical/Important/Minor，未判定项为空；另复跑网络15/15。[全部取舍](model-network-decisions-20261020.md)与[《01》PASS / SKIPPED逐项清单](stability-doc01-acceptance-20261020.md)已归档。阶段F完成，继续《02》，不要求重复批准。

## 《01》后续进度

- [x] B：BUG-003 / BUG-004，账本幂等与分红估值日约束。
- [x] C：BUG-005 至 BUG-009，行情时效、日历、时间解析与目录完整性。
- [x] D：BUG-010，刷新器调度。
- [x] E：BUG-011 至 BUG-013，回测指标与未来价格边界。
- [x] F：BUG-014、文档与部署契约一致性。

## 《02》后续进度

- [x] 指数注册表、供应商身份与 API 契约；完整HEAD09fc5b1 Linux39433893185，Python511/PG.NET404/Worker134/工具15/报告5全通过且0跳过，前端/浏览器/构建成功，独立审查无问题。全部取舍与阶段边界见[记录](index-registry-decisions-20261020.md)。
- [x] 16 项卡片及真实质量状态，保留既有主题和页面；7c91277 Linux39442442603全通过，Node55及连接模式浏览器通过，审查Important已修复、1项Minor延后，见[记录](index-strip-ui-decisions-20261020.md)。
- [x] 按市场验证历史 K 线；5c032b8 Linux39453954440，Python520/真实PG.NET460/Worker148/工具15/报告5全通过且0跳过，前端Node55及连接模式浏览器通过。16历史映射、独立时段、轮换/预留、缺失日线和原生缓存修复见[完整验收](global-index-history-acceptance-20261020.md)；公开源超时/HTML/字段缺失如实保留，第16项仍暂定。
- [ ] 基金多维分类及独立榜单、搜索和账户关注。
- [ ] 行业/概念/地域板块质量状态及全链路回归。

第 16 项中证1000仍为需求中注明的暂定候选；不将其标记成截图已确认。
