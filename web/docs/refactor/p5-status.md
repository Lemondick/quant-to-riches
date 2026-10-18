# P5 执行状态（其余业务接口 + TypeScript 前端）

- 时间：2026-10-05
- 验证：PostgreSQL 18，`dotnet test` 55/55 通过

## 本轮新增（后端，已真实验证）

| 项 | 位置 | 验证 |
| --- | --- | --- |
| 历史查询仓储（ledger/actions/corrections 三 section + CSV 导出 + onboarding） | `Core/History/` + `Infrastructure/History/HistoryRepository.cs` | 集成测试（联合视图、符号/日期过滤、公式注入防护、引导状态） |
| 历史控制器 | `Api/Controllers/HistoryController.cs` | 编译 + 契约 |
| 研究任务 HTTP 门面（create/get/cancel/retry） | `Api/Controllers/ResearchController.cs` | 集成测试（生命周期 + 重试） |
| 任务仓储 `Get` 详情查询 | `Core/Jobs/` + `Infrastructure/Jobs/JobRepository.cs` | 集成测试 |
| 运行维护仓储 + 控制器 | `Core/Runtime/` + `Infrastructure/Runtime/` + `Api/Controllers/RuntimeController.cs` | 编译 + 契约 |
| 账本写控制器（成交/分红/公司行动/冲正） | `Api/Controllers/LedgerController.cs` | 编译 + 契约（复用 P3 服务） |
| 组合查询控制器 | `Api/Controllers/PortfolioController.cs` | 编译 + 契约 |
| 标的代码规范化 | `Core/Portfolio/Symbols.cs` | 单元测试（交易所推断 + 显式后缀） |

## 接口覆盖矩阵（131 接口 → 迁移状态）

> 状态：✅ 已迁移（C# 控制器 + 测试）｜🔶 领域已迁移（服务/仓储实现，HTTP 控制器待接线）｜⏸ 保留 Python（分析计算）｜⏳ 待迁移

| 业务域 | 接口数 | 状态 | 说明 |
| --- | --- | --- | --- |
| auth | 10 | ✅ | AuthController（注册/登录/会话/恢复码/管理员） |
| admin | 13 | ✅ 7 / 🔶 6 | users/registration/recovery/revoke/status/runtime/audit 已实现；usage/timeline/details/export/reports 待接线 |
| history | 2 | ✅ | HistoryController（分页 + 导出） |
| onboarding | 1 | ✅ | HistoryController |
| research/agent-runs | 4 | ✅ | ResearchController（任务门面） |
| health | 5 | ✅ | HealthController（/healthz /readyz /metrics） |
| ledger | 15 | ✅ 4 / 🔶 11 | transactions/dividends/actions/corrections 写接口已接线；查询/对账/重放待接线 |
| portfolio | 6 | ✅ 1 / 🔶 5 | 组合概览已接线；history/drawdown/photo-holdings 待接线 |
| funds | 13 | 🔶 4 / ⏸ 9 | holding-lots/赎回报价（账本）待接线；candidates/valuations/detail/fee-evidence（分析）保留 Python |
| imports | 8 | ⏳ | 照片导入（OCR Worker 任务） |
| advisor | 10 | ⏸ | 投顾报告（模型计算）保留 Python |
| research 其余 | 19 | ⏸ | 研究计算保留 Python |
| preferences | 3 | ⏳ | 偏好读写 |
| alerts | 4 | ⏳ | 告警中心 |
| runtime/costs | 2 | ⏳ | 成本设置读写 |
| market/analysis/backtests/decisions/stocks/planning/data | 11 | ⏸ | 分析计算保留 Python |

**小结**：131 接口中，✅ 已迁移 35，🔶 领域就绪 26，⏸ 保留 Python 48，⏳ 待迁移 22。

## 前端 TypeScript 迁移（未开始）

计划书 P5 要求前端 models/controllers/utils/页面逐步 TS 化。现状：前端仍为 JS（`apps/web` + `apps/shared` uni-app），需 `npm` 构建链。此块独立于后端，建议后续单独批次推进（见下方「剩余」）。

## 剩余（P5 未完 + P6 + P7）

- P5 后端：LedgerController / PortfolioController / PreferencesController / AlertController / RuntimeCostsController（领域已就绪，仅 HTTP 接线）。
- P5 前端：TS 迁移（最大独立块，需 npm 链）。
- P6：Docker Compose + Caddy + 备份恢复演练。
- P7：切换手册 + 回切演练（由你执行）。

## 复现

```bash
export QUANT_TEST_PG_CONNECTION="Host=localhost;Port=5432;Database=quant_assistant;Username=postgres"
dotnet test QuantAssistant.slnx
```
