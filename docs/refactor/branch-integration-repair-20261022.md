# GitHub 分支本地整合与修复

日期：2026-10-22。仓库：Lemondick/quant-to-riches。

六个待合并分支已整合到本地 `codex/branch-integration-fixes`。审查确认的登录、账户隔离、任务协议、计算及页面契约问题已修复，并通过下面的验证。此次没有推送或修改 GitHub 分支。

## 工作区与整合范围

- 修复目录：`C:/Users/admin/.codex/worktrees/branch-integration-fixes/量化`。
- 原目录 `C:/Users/admin/Desktop/量化/量化` 保持在 `codex/stability-fixes`，原有 571 项未提交/未跟踪状态保留。
- 起点为 batch5 `24b4b46`，已包含 batch2 `0c9270d`、batch3 `6de683d` 和 legacy `36da648`。
- 本地合入 optimize `9262a61`、TradingAgents `450d7ea`，整合提交为 `d083335`、`9b5b4d3`。
- 0027 迁移冲突保留 batch5 已可执行的等效修复；模型连接服务同时保留账户非账本写入及 Jev 模型目录校验。修复阶段未修改已发布迁移 SQL。
- 最终通过 GitHub 连接器复核，远程 main 仍为 `955babd`，上述六个远程头提交均未变化。

## 修复内容

| 范围 | 修复后的行为 |
| --- | --- |
| 登录与会话 | 未登录的二步验证请求可到达控制器；保留 Origin、过期、一次性挑战及限流检查。页面支持动态验证码与恢复码。挑战创建使用可预编译的单条 PostgreSQL 语句。 |
| 账户隔离 | Service Worker 不再持久化 API 响应，升级清除旧私有缓存；账户切换期间隐藏旧数据。模型缓存键包含账户身份，内存回退也验证活跃用户。风险缓存按认证账户分区。 |
| Worker 协议 | 完整 51 项能力可领取/入队；新增公开任务的执行与缓存配置一致。风险请求带账户快照，DuckDB 查询遵守账户隔离库的真实结构。 |
| 回测与定投 | 每天记录当日持仓价值，每笔投入仅累计一次；支持接口已接受的可选再平衡、每周定投。曲线提供扣除外部现金流的单位净值，页面正确转换百分比、金额及空 XIRR。保存的定投比例与 Worker 百分位仅在边界转换一次。 |
| 研究发布 | 完整 TradingAgents 路径返回 `{report, usage}`；上游导入前建立每任务 HOME，图执行显式传递预算回调。真实 LangGraph 工具事件触发次数上限。 |
| 业务数据 | 修复已解析 JSONB 的定投规则读取及 Ask 子节点重复挂载；新增明确的系统维护写入路径，普通写入仍拒绝不存在/停用用户。邮件游标按时间、用户、通知 ID 推进，失败时停在可重试位置。 |
| 告警与维护 | 恢复管理员 runtime 兼容接口；按账户价格日期评估告警；评估仅关闭自身负责的告警类型，保留定投/赎回/基金体检等定时提醒。赎回提醒带已核验费率和市值口径。审计追加使用事务级锁防止并发分叉。 |
| 页面契约 | 对齐再平衡、估值、基金体检、公告、风险、成本、目标、净资产、情绪及定投跟踪响应；真实请求失败显示错误，不冒充演示账户数据。本币持仓不再解引用空汇率；API 控制台不再重复添加前缀。 |
| 数据库与 CI | dry-run 不创建元数据或回填校验和；回滚执行前校验全部所需脚本。原生回退导出明确处理新增元数据，遇到无法保留的二步验证/只读权限在创建暂存目录前拒绝。迁移冒烟测试失败不再被 CI 当作成功。 |

NYSE 周六元旦不向前占用上一年 12 月 31 日交易日的边界已纠正。API 文档、OpenAPI 和前端路由快照同步为 217 条路由。格式调整仅用于满足现有检查；未增加新的产品功能或替换框架。

交叉复审补充复现并修复了两个遗漏：成功回测响应会使页面读到未定义指标，以及 GET 告警中心会清除定时提醒。两项均先记录失败，再通过专项及完整测试。原生导出/迁移修复由未编写该实现的审查者检查；后端、Worker、前端进行了交叉检查。

## 验证结果

测试使用回环地址上的独立 PostgreSQL 18 集群、合成账户和数据；没有访问用户应用数据库、发送真实邮件或调用付费模型。

| 检查 | 最终结果 |
| --- | --- |
| .NET Release 构建 | 成功，0 警告、0 错误 |
| .NET PostgreSQL 测试 | 568/568 通过，0 跳过；CI 计数器要求数据库测试已执行 |
| Python 业务测试 | 527 项，526 通过，1 跳过：Linux 父进程死亡保护在 Windows 不适用 |
| Python Worker | 331/331 通过，0 跳过；外部行情替身保证原有分发断言实际执行 |
| TradingAgents runner | 3/3 通过，包含真实 LangGraph 回调上限与导入时目录隔离 |
| 原生 PostgreSQL 工具 | 17/17 通过，0 跳过，含 Git Bash 部署保护、备份恢复、回退导出及认证语义保护 |
| 迁移 | 48 个正向迁移从空库执行；dry-run 留下 0 张业务/元数据表；48 个校验和齐全，重复执行无变更；69 个正向/回滚 SQL 解析通过；3 项迁移回归通过 |
| 前端单元测试 | 127/127 通过，0 跳过 |
| 接口浏览器回归 | 13/13 通过，含二步登录、账户切换、本币持仓、真实响应形状、错误状态与成功回测 |
| 现有页面浏览器检查 | 52 项通过，0 浏览器错误；指数条、基金目录、板块质量的现有 CI 脚本均通过 |
| 源码门禁 | Python verify、Ruff lint/format、前端 lint/format、TypeScript 检查、演示/连接后端两种生产构建通过 |
| API 契约 | 217 条 OpenAPI 路由与 api.md 清单一致，前端快照重新生成 |
| 依赖检查 | .NET 未报告已知易受攻击包；npm high 阈值通过，仍报告现有 ECharts 的 1 项 moderate 公告 |

完整日志和 TRX/JSON 位于本工作区的 `.superpowers/branch-repair/`（忽略目录）。主要文件：`dotnet-final.trx`、`dotnet-final.json`、`python-business-final.json`、`worker-final.json`、`native-tools-final.json`、`frontend-tests.log`、`frontend-browser-final.log`、`browser-demo.log`、`browser-index.log`、`browser-catalog.log`、`browser-sector.log`。

## 验证边界

此次完成本地修复与验证，尚未在 GitHub 对修复提交运行远程 CI，也未部署。Linux 专用父进程保护需要由 Linux CI 执行；真实付费模型与 SMTP 投递未实测。源码检查通过并不代表生产准入通过，本地合成安装仍缺少真实策略/部署证据。

`npm audit --audit-level=high` 返回成功，但 ECharts 5.6.0 仍受 [GHSA-fgmj-fm8m-jvvx](https://github.com/advisories/GHSA-fgmj-fm8m-jvvx) 的 moderate 公告影响。审计建议升级到具有破坏性变更的 6.1.0；本次未扩大为图表库主版本迁移。
