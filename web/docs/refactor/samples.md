# P0 验收样本与接口基线说明（samples）

- 基线：commit `29568a8a0a8aa9def7241d5d199bdf5d8a111adc`
- 生成时间：2026-10-05
- 原则：合成/脱敏，不含真实账户、真实账本、私有密钥；可独立复现。

## 1. 机器可读基线产物（`web/docs/refactor/`）

| 文件 | 内容 | 生成方式 |
| --- | --- | --- |
| `api-inventory.json` | 131 个 HTTP 路由（方法/路径/请求响应模型名/权限依赖），含 6 个 app 级端点 | `tools/extract_api_inventory.py`（AST 静态提取） |
| `storage-inventory.json` | 7 张 SQLite 表 + 54 张 DuckDB 表（列/约束）全量 | `tools/extract_storage_inventory.py`（DDL 解析） |
| `schema-contract.json` | 103 个 Pydantic 请求/响应模型的 JSON Schema | `tools/dump_schema_contract.py`（运行时 introspect） |

三个脚本均为只读分析工具，不改产品代码、不读写真实数据。

## 2. 合成账本样本机制

现有 6 个验收脚本（`verify_multi_user.py` 等）内部用 `tempfile.TemporaryDirectory()` + 合成账号（alice/bob 等公开测试密码）在临时目录创建完整站点结构（账号库 + 用户 DuckDB + 账本事件），覆盖事务回滚、并发、备份恢复、冲正、对账等合成场景；测试结束即销毁。本次复跑已全量执行（见 `test-report.md`）。

这些脚本即为「合成账本样本 + 旧接口响应基线」的生成器与验证器：P1 起可直接复用它们作为 .NET 契约测试的期望来源（HTTP 状态码、错误码、字段语义），无需另造样本。

## 3. 脱敏边界（P0 遵守）

- 未读取 `data/` 真实账本内容（仅统计目录名与体积）；`deploy/research-secrets/`、`server-secrets/` 完全未触碰。
- 隔离复跑的数据目录指向 `tmp-check/p0-verify-20261005/`（空目录），与真实 `data/` 物理隔离。
- 所有产物不含密码、Key、Cookie、私有模型配置或完整账户正文。

## 4. 复现命令

```powershell
# 后端源码检查（数据重定向到隔离空目录）
$env:QUANT_DATA_DIR = (Resolve-Path ../tmp-check/p0-verify-20261005/data).Path
$env:QUANT_ADVISOR_CONFIG = (Resolve-Path ../tmp-check/p0-verify-20261005/advisor.json).Path
web/backend/.venv/Scripts/python.exe -B -X utf8 web/backend/scripts/verify.py
# 后端离线验收
web/backend/.venv/Scripts/python.exe -B -X utf8 -m unittest discover -s web/backend/scripts -p 'verify_*.py' -v
# 前端
npm --prefix web/frontend run lint
npm --prefix web/frontend run format:check
node web/frontend/scripts/verify-multi-user.mjs
node web/frontend/scripts/verify-account-maintenance.mjs
node web/frontend/scripts/verify-ledger-review.mjs
node web/frontend/scripts/verify-runtime-controls.mjs
```
