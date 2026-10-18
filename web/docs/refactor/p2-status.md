# P2 执行状态（PostgreSQL 存储迁移 + 账号认证）

- 基线：commit `29568a8a0a8aa9def7241d5d199bdf5d8a111adc`
- 时间：2026-10-05
- 验证环境：本机 PostgreSQL 18.0（免安装二进制，`C:/Users/admin/pg18-fast/pgsql`，数据目录 `C:/Users/admin/pg18-data`，端口 5432）

## 已完成并验证

| 项 | 状态 | 证据 |
| --- | --- | --- |
| Argon2id 跨语言兼容 | ✅ 验证 | C# Isopoh 与 Python argon2-cffi 逐字节一致（ASCII + Unicode 密码） |
| Argon2PasswordHasher（PHC 解析/生成） | ✅ 验证 | 往返 + Python PHC 校验通过 |
| PG 账号域 schema `migrations/0001_auth.sql` | ✅ 应用 | 7 表 + 部分唯一索引（单管理员） |
| PG 业务域 schema `migrations/0002_business.sql` | ✅ 应用 | 41 表 + user_id 隔离 + 14 全局序列 |
| 迁移执行器 `MigrationRunner` | ✅ 编译 | 版本化 + schema_migrations 幂等 |
| 账号仓储/服务/控制器 | ✅ 编译+集成 | 14/14 测试，含真实 PG 注册→登录→会话→恢复链路 |

## Schema 决策（补 decisions.md U3）

- **user_id 隔离维度**：41 张业务表全部加 `user_id TEXT NOT NULL REFERENCES users(id)` 首列，主键/唯一约束改为复合 `(user_id, ...)`（计划书 §4「目标键必须包含用户维度」）。
- **共享 vs 每用户拆分**：13 张纯市场参考/回测表保留在 DuckDB/Parquet（Python 分析域，计划书表1「分析存储」）——`instruments`、`instrument_history`、`daily_prices`、`fund_nav`、`fund_valuation_history`、`fund_manager_history`、`fund_profile_history`、`fund_lifecycle_events`、`fund_holding_history`、`fund_style_drift_events`、`backtest_runs`、`backtest_nav`、`stock_rankings`。
- **全局序列**：14 个自增序列跨用户共享，导入后校准到 `max(id)+1`（计划书 §4）。

## 验证中发现的真实缺陷（已修正）

1. **序列创建顺序**：DuckDB `schema.sql` 序列在表前，首版翻译脚本把序列放到表后，导致 `data_snapshots` 建表时 `nextval('data_snapshot_id_seq')` 引用失败。已修正为序列先于建表（真实 PG 执行暴露）。
2. **UNIQUE 缺 user_id**：`cache_key`、`idempotency_key`、`request_fingerprint` 等原单列 UNIQUE 在合并后跨用户冲突，已改为 `UNIQUE (user_id, ...)`。
3. **DOUBLE 类型**：DuckDB `DOUBLE` → PG `DOUBLE PRECISION`。

## 未完成（P2 残留 + P3 起）

- 数据迁移 ETL（冻结导出/重复导入保护/差异报告/空环境恢复）——需读旧 SQLite/DuckDB 写 PG。
- 管理员接口、完整 CSRF/Origin 中间件、会话 Cookie 全链路。
- P3 账本与持仓迁移、P4 任务协议与 Worker、P5 业务接口+TS 前端、P6 部署演练、P7 切换手册。

## 复现

```bash
# 启动 PG（本机免安装）
C:/Users/admin/pg18-fast/pgsql/bin/postgres.exe -D C:/Users/admin/pg18-data -p 5432 &
# 应用迁移
psql -h localhost -U postgres -d quant_assistant -f migrations/0001_auth.sql
psql -h localhost -U postgres -d quant_assistant -f migrations/0002_business.sql
# 构建+测试（含 PG 集成测试）
export QUANT_TEST_PG_CONNECTION="Host=localhost;Port=5432;Database=quant_assistant;Username=postgres"
dotnet build QuantAssistant.slnx && dotnet test QuantAssistant.slnx
```
