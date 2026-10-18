# P3 执行状态（账本与持仓迁移）

- 时间：2026-10-05
- 验证环境：PostgreSQL 18（本机），`dotnet test` 24/24 通过

## 已完成并验证

| 项 | 位置 | 验证 |
| --- | --- | --- |
| 领域枚举与输入 | `Core/Portfolio/PortfolioTypes.cs` | 编译 |
| 成交规则（纯函数） | `Core/Portfolio/TransactionRules.cs` | 单元测试，与 Python Decimal 逐位一致 |
| 分红规则（纯函数） | `Core/Portfolio/DividendRules.cs` | 单元测试 |
| 冲正规则（纯函数） | `Core/Portfolio/CorrectionRules.cs` | 单元测试 |
| 公司行动规则（纯函数） | `Core/Portfolio/CorporateActionRules.cs` | 单元测试 |
| 账本事务编排（成交/分红/冲正/公司行动/现金流） | `Core/Portfolio/TransactionService.cs` | 集成测试 |
| 账本仓储（PG） | `Infrastructure/Portfolio/LedgerRepository.cs` | 集成测试 |
| 端到端账本六操作 | 成交→分红→冲正→公司行动(拆分/红利再投资)→外部现金流→组合校准→幂等→审计 | 真实 PG 通过（30/30） |

## 关键数值规则（与 Python 逐位一致，已 golden 验证）

- 买入加权平均成本：`(old_qty*old_cost + qty*price + fees) / new_qty`（fees 计入成本）
- 卖出成本不变（「卖出前成本」语义）
- 现金：买入 `-(gross + fees)`、卖出 `gross - fees`
- 股票整股 + 买入 100 股整数倍
- 幂等：`source + external_id` 唯一，重复返回原 ID，内容不同报冲突
- 上海时区校验（traded_at 必须 +08:00）

## 过程中发现并修复的 schema 缺陷（真实 PG 暴露）

1. **增量迁移列丢失**：`transactions` 的 `source/external_id/gross_amount/cash_delta/cash_symbol/reversed_by_correction_id` 等列由 `ALTER TABLE ADD COLUMN` 添加，首版翻译脚本只处理 `CREATE TABLE` 丢失这些列 → 已追加 ALTER 语句（含多行 ALTER 的正则修复）。
2. **instruments 主档**：成交记录 `RequireKnownInstrument` 依赖 instruments，已从 DuckDB 排除列表移入 PG（42 业务表）。

## 未完成（P3 剩余 + P4 起）

- P3 剩余：分红（record_dividend）、公司行动（record_corporate_action）、账本冲正（correct_ledger_entry）、外部现金流、组合校准、批量成交预检 —— 约 1500 行 Python 逻辑待 C# 重写。
- P4 任务协议 + Python Worker、P5 业务接口 + TS 前端、P6 部署、P7 切换手册。

## 复现

```bash
export QUANT_TEST_PG_CONNECTION="Host=localhost;Port=5432;Database=quant_assistant;Username=postgres"
dotnet test QuantAssistant.slnx
```
