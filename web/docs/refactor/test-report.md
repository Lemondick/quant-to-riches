# P0 离线验收复跑报告（test-report）

- 基线：commit `29568a8a0a8aa9def7241d5d199bdf5d8a111adc`，分支 `codex/multi-user-auth`
- 复跑时间：2026-10-05
- 隔离：数据目录重定向至 `C:/Users/admin/Desktop/量化/tmp-check/p0-verify-20261005/data`（空目录）；模型配置指向空 `advisor.json`；未启动真实服务、未调用付费模型、未改写真实 `data/`。
- 环境：Python 3.12.12（项目 `.venv`）、uv 0.10.6、Node v22.22.2。

## 1. 后端源码检查（verify.py）

命令：`QUANT_DATA_DIR=<隔离根>/data QUANT_ADVISOR_CONFIG=<隔离根>/advisor.json python -B -X utf8 scripts/verify.py`

| 检查项 | 结果 |
| --- | --- |
| 源码规模 `check_source_size.py` | 通过 |
| `ruff check --no-cache src scripts` | 通过 |
| `ruff format --check src scripts` | 通过 |
| `mypy`（entities / common / analysis.models 等核心类型） | 通过 |
| 密钥扫描 `check_no_secrets.py` | 通过 |
| 运行准入 `production_readiness.py` | 按预期**准入阻塞**（隔离空目录无有效模拟会话/备份），非严格模式下判为通过 |

**结论：源码检查全通过，退出码 0；运行准入仍阻塞（预期，非失败）。**

## 2. 后端离线验收（unittest discover）

命令：`./.venv/Scripts/python.exe -B -X utf8 -m unittest discover -s scripts -p 'verify_*.py' -v`

| 模块 | 用例数 | 通过 | 失败 |
| --- | ---: | ---: | ---: |
| verify_account_maintenance（账号 11） | 11 | 10 | **1** |
| verify_evidence_admission（证据 17） | 17 | 17 | 0 |
| verify_ledger_review（账本 14） | 14 | 14 | 0 |
| verify_multi_user（原多用户 7） | 7 | 7 | 0 |
| verify_runtime_controls（运行 14） | 14 | 14 | 0 |
| **合计** | **63** | **62** | **1** |

> 注：`verify_research_evidence.py` 是独立脚本（`-m scripts.verify_research_evidence --root ...`，需研究证据数据目录），不属于 unittest discover 的 63 用例；本轮未重跑该证据复现（见 §5）。

### 失败用例

**`test_recovery_is_atomic_single_use_and_revokes_all_sessions`**（verify_account_maintenance.py:215）

- 场景：两线程并发兑付同一个一次性恢复码，期望 `[200, 400]`（一次成功、一次因"已使用"返回 400）。
- 实际：`[200, 503]`——第二个并发请求返回 503（`database_unavailable`）。
- **可复现**：隔离重跑仍失败（`Ran 1 test ... FAILED`），非偶发。
- **根因**：账号库 SQLite（WAL 模式）`connection(write=True)` 用 `BEGIN IMMEDIATE` 抢写锁；并发兑付时第二个线程的写锁冲突以 `sqlite3.OperationalError: database is locked` 冒泡，被 `app.py` 的 `_database_unavailable()` 误判为"数据库不可用"映射为 503，而非按业务语义返回 400「恢复码已使用」的冲突。
- **影响与定位**：这正是计划书 §7 验收表「同请求重复或并发提交 → 只形成一次业务效果或明确冲突」、以及决定 D1（"仅换数据库不能避免并发更新错误，需配合行锁/唯一约束/冲突处理"）与 D3（并发注册/恢复语义）要解决的原子性边界。当前实现对该并发路径的冲突识别不足，把数据库层锁竞争误报为服务不可用。
- **P0 处置**：P0 不改产品代码，仅登记为验收失败与迁移时必须保留/修正的业务语义；建议在 P2（账号）或 P3 固定事务规则时以显式冲突处理修复（SQLite 侧可设 busy_timeout 重试或先 SELECT 校验 consumed 再 UPDATE 并捕获冲突映射 400；PG 侧用唯一约束 + 冲突处理）。

## 3. 前端检查

命令：`npm run lint`、`npm run format:check`、`node scripts/verify-*.mjs`

| 检查 | 结果 |
| --- | --- |
| ESLint（src scripts --quiet） | 通过（0 错误） |
| Prettier --check | 通过（All matched files use Prettier code style） |
| verify-multi-user.mjs | 通过（41 基金报价、去重、部分失败、账号切换、分页与私有数据清除） |
| verify-account-maintenance.mjs | 通过（会话控制、登录恢复、管理员时间线、token 清除） |
| verify-ledger-review.mjs | 通过（选定快照对比、显式确认、陈旧表单隔离、回放阻断、分页历史） |
| verify-runtime-controls.mjs | 通过（账号清除、迟到响应拒绝、错误与 Vue 编译） |

**前端全通过。** H5 build 未在本轮执行（见 §5 未验证范围）。

## 4. 结论

- 后端源码检查：通过。
- 后端离线验收：**62/63 通过，1 失败**（并发恢复码兑付返回 503 而非 400）。
- 前端组件验收 + lint + format：通过。
- 与 STATUS.md 自报「后端 63/63」不符：本轮独立复跑发现 1 例确定性失败，需按 §2 处置。

## 5. 未验证 / 未执行范围（P0 明确不覆盖）

- H5 构建（`npm run build`）：未执行，避免在只读盘点阶段生成运行产物。
- 真实外部模型 / OCR / 付费模型调用：未执行（P0 禁止）。
- Docker 镜像构建与容器运行、公网部署：未执行。
- 浏览器视觉验收：未执行（组件验收不替代真实浏览器）。
- `verify_research_evidence.py --reproduce` 研究证据重放：未执行（依赖 `data/evidence-completion-20261005` 研究档案，属研究准入域，非语言重构 P0 验收入口）。
