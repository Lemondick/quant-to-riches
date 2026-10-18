# P4 执行状态（后台任务协议 + Python Worker）

- 时间：2026-10-05
- 验证：PostgreSQL 18，`dotnet test` 39/39 通过

## 已完成并验证

| 项 | 位置 | 验证 |
| --- | --- | --- |
| 租约协调器（排他租约 + 释放） | `Core/Operations/OperationCoordinator.cs` | 集成测试（同锁冲突抛错、释放可重入） |
| 运行仓储（锁 + 心跳） | `Infrastructure/Operations/OperationRepository.cs` | 集成测试 |
| 任务协议（入队/领取/结果提交/取消/恢复） | `Core/Jobs/` + `Infrastructure/Jobs/JobRepository.cs` | 集成测试（幂等入队、结果幂等、重启恢复） |

## 关键机制（对应计划书 §5）

- **租约**：`operation_locks` 表 + 唯一约束；删除过期锁 → 插入（冲突抛 `operation_already_running`）→ 释放按 owner_token。
- **结果幂等**：`finish` 仅当 `attempt_no` 匹配且状态为 active 才提交（`FOR UPDATE` 行锁），旧 Worker 不能覆盖新状态。
- **领取**：`SELECT ... FOR UPDATE SKIP LOCKED` 原子领取下一个 queued 任务。
- **重启恢复**：`recover` 将 queued/running/cancel_requested → interrupted（状态可核对，不重复执行）。
- **配额**：按日 attempts 计数，超限抛 `quota_exceeded`。

## Mock 模型故障覆盖（计划书 §7 验收表，`WorkerFaultCoverageTests.cs`）

| 故障场景 | 验证语义 |
| --- | --- |
| 旧租约 | 过期租约被删除，新 Worker 可绕过旧租约重新获取 |
| 模型超时 | running 任务 deadline 过期 → recover 标记 interrupted，不重复执行 |
| 未知计费 | finish 时 usage=null 正常提交，不崩溃 |
| DB 中断 | 配额超限事务回滚，不产生半成品 job/attempt |
| Worker 退出 | cancel_requested 任务 finish 时强制转 cancelled |

## 剩余（P5 起）

- P5 业务接口 + TS 前端、P6 部署、P7 切换手册。

## 复现

```bash
export QUANT_TEST_PG_CONNECTION="Host=localhost;Port=5432;Database=quant_assistant;Username=postgres"
dotnet test QuantAssistant.slnx
```
