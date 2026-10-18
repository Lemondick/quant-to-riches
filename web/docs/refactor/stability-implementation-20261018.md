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

## 《01》阶段 C：BUG-005 至 BUG-009 — 进行中

计划：`docs/superpowers/plans/2026-10-18-public-data-quality.md`，按已授权顺序在 `codex/public-data-quality` 分支执行。

### 日历证据链 — 子任务 1 PASS

- 先在 `b0d7f97` 新增真实 HTTP/PG 回归：[红灯 39121190851](https://github.com/Lemondick/quant-to-riches/actions/runs/39121190851)，274 实际执行，272 通过、2 失败、0 跳过，复现 HK 误用 CN 与 Worker 缺少权威日历。
- `e169b5fe43b99c6448ce903a3a59c69d35830295`：[绿灯 39121691596](https://github.com/Lemondick/quant-to-riches/actions/runs/39121691596)，.NET/PG 276、Worker 109、Python 446、数据库工具 15、报告 5 均通过且 0 跳过；前端和独立编译通过。
- `TradingCalendarRepository` 保持权威来源。旧 `calendar:<year>` 键继续代表 CN，HK/US/JP 独立键；官方来源域名按市场校验，保存保留 revision 冲突检查和管理员审计，并使快照缓存失效。
- 原 CN 2026 内置兜底移至唯一 `exchange_calendars.json`，C# 嵌入资源和 Python wheel 复用；wheel 已实测包含同一资源，不为其他市场或年份编造假日。
- 已认证 Worker claim 注入只读 `_calendars`（市场、年份、来源、修订和休市日），覆盖任务自带的同名内容，不改变原查询缓存键，不提供数据库凭据。

### 时段与时间解析 — 子任务 2 等待 Linux 验证

- 共用 `tests/fixtures/public-quality-cases.json`：本地先复现 C# 23 项边界/解析失败、Python 无时区误判，以及 Worker 未使用任务日历；均保留 RED 日志。
- 实现六类时段与严格 ISO 偏移解析，删除 quality 中重复的假日表；明确偏移按 UTC instant 比较，无时区为 `unknown_time`。缺少日历或已核验市场时段时保持未知，不能套用 A 股规则。
- 竞价能力缺省 false。能力随 Observation 与只读快照传递，两端使用一致判断；当前公开来源未被证实支持竞价，没有把测试中的合成能力声明当作真实供应商能力。
- provider 实例按不可变日历快照使用有界缓存；并发任务不改进程全局日历。缓存读取重新评估时效，保持原 source_time 和 fetched_at。
- 本地最终结果：.NET 128 通过、155 数据库项跳过；Worker 114/114；前端 43/43，lint/typecheck/build 通过；Python source verify 通过（运行准入仍按原配置阻断，不代表已接入生产）。
- 前端只补充时段标签并兼容旧 `trading`/`break` 值，不调整布局、CSS 或主题。

正式净值发布频率策略和板块目录完整性仍在后续子任务中；阶段 C 整体尚未宣告完成。

## 《01》后续进度

- [x] B：BUG-003 / BUG-004，账本幂等与分红估值日约束。
- [ ] C：BUG-005 至 BUG-009，行情时效、日历、时间解析与目录完整性。
- [ ] D：BUG-010，刷新器调度。
- [ ] E：BUG-011 至 BUG-013，回测指标与未来价格边界。
- [ ] F：BUG-014、文档与部署契约一致性。

## 《02》后续进度

- [ ] 指数注册表、供应商身份与 API 契约。
- [ ] 16 项卡片及真实质量状态，保留既有主题和页面。
- [ ] 按市场验证历史 K 线。
- [ ] 基金多维分类及独立榜单、搜索和账户关注。
- [ ] 行业/概念/地域板块质量状态及全链路回归。

第 16 项中证1000仍为需求中注明的暂定候选；不将其标记成截图已确认。
