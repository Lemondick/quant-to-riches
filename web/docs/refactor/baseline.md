# 量化宝语言重构 P0 基线报告（baseline）

- 版本：1.0
- 日期：2026-10-05
- 依据：`docs/research/量化宝语言重构计划书20261005.docx` 第 6/10 节「P0 固定基线」
- 阶段：**仅 P0 盘点与验收，未改产品代码、未碰真实数据、未调付费模型**

## 1. 基线固定

| 项 | 值 |
| --- | --- |
| 仓库 HEAD | `29568a8a0a8aa9def7241d5d199bdf5d8a111adc`（与计划书一致） |
| 分支 | `codex/multi-user-auth`（与计划书一致） |
| 工作区状态 | 仅 2 个未跟踪文件（计划书 docx + 其 md 副本），无产品代码改动 |

## 2. 现状概览（实测，替代计划书旧数值）

| 指标 | 计划书 | 实测 |
| --- | --- | --- |
| 后端 Python 文件（src） | 227 | 227 |
| 后端脚本（scripts） | — | 31 |
| 前端 Vue 文件 | 56 | 56 |
| 前端 JS/mjs 文件 | 20 | **26**（计划书数值待更新） |
| 后端 Python 行数 | — | 6,725 |
| 前端 src 行数 | — | 21,526 |
| DuckDB 建表语句（schema.sql） | 54 | 54 |
| SQLite 账号表（auth.py） | — | 7 |
| HTTP 路由（静态提取） | — | 131（125 路由级 + 6 应用级） |

技术栈：后端 Python 3.12 / FastAPI / Uvicorn；账号库 SQLite（`data/auth/accounts.sqlite3`）；业务库每用户 DuckDB（`users/<id>/quant_assistant.duckdb`）；前端 JavaScript + Vue 3 + Uni-App H5；部署 Docker Compose + Caddy。

## 3. 模块迁移矩阵（P0 初版，P1 起细化）

| 模块 | 职责 | 迁移方向 |
| --- | --- | --- |
| `controllers/` | HTTP 路由、请求/响应模型、认证限流 | → C# `QuantAssistant.Api`（保持 `/api/v1` 契约） |
| `models/`（entities/repositories） | 领域实体与持久化 | → C# `Core`（业务规则/接口）+ `Infrastructure`（PostgreSQL/文件/Worker 适配） |
| `services/` | 账户/基金/AI报告/调度等业务服务 | → C# `Core` |
| `analysis/` | 模型接入、研究证据、输出校验 | → 部分 C#（授权/状态），模型调用适配保留 Python Worker |
| `providers/` | 行情/基金/OCR 外部数据适配 | → Python Worker（采集）+ C# 适配层 |
| `data/` | 数据采集、校验、快照（Parquet） | → Python Worker / 分析域 |
| `funds/`、`portfolio/` | 基金费用、组合计算 | → C# `Core` |
| `features/`、`strategies/` | 因子、策略规则 | → Python 量化 Worker（复用，不复制实现） |
| `backtest/` | 历史回放引擎 | → Python 量化 Worker |
| `reporting/` | 查询与报告 | → C# `Core`/`Api` |
| `brokers/` | 账户导入与交易安全边界 | → C#（授权/校验）+ Python（解析） |
| `integrations/`、`common/` | 外部适配能力、共享基础类型 | → 按功能拆分 |

新增结构（计划书 §2）：`web/backend-dotnet/`（QuantAssistant.Api / Core / Infrastructure / tests）、`web/worker/`（复用现有 Python 包）、`web/frontend/`（逐步 TS 化）、`web/deploy/`、`web/docs/refactor/`。

## 4. 交付清单

| 交付物 | 路径 | 内容 |
| --- | --- | --- |
| 接口清单 | `web/docs/refactor/api-inventory.json` | 131 路由（方法/路径/请求响应模型/权限依赖） |
| 接口契约 | `web/docs/refactor/schema-contract.json` | 103 个 Pydantic 模型 JSON Schema |
| 存储映射 | `web/docs/refactor/storage-map.md` | SQLite 7 表 + DuckDB 54 表 + 文件，逐表迁移目标 |
| 存储清单 | `web/docs/refactor/storage-inventory.json` | 机器可读全量表结构 |
| 决定记录 | `web/docs/refactor/decisions.md` | D1–D6（PG/数值/账号/指纹/Worker/回切）+ U1–U4 未决项 |
| 测试报告 | `web/docs/refactor/test-report.md` | 后端 62/63、前端全通过、1 例失败根因 |
| 样本说明 | `web/docs/refactor/samples.md` | 合成样本机制 + 复现命令 |
| 提取工具 | `web/docs/refactor/tools/*.py` | 三个只读分析脚本，可复现 |

## 5. 验收结论（摘要）

- 后端源码检查（Ruff/mypy/规模/密钥）：**通过**，运行准入按预期阻塞（隔离空目录）。
- 后端离线验收：**62/63 通过，1 失败**（`test_recovery_is_atomic_single_use_and_revokes_all_sessions`：并发兑付恢复码返回 503 而非 400，SQLite 写锁冲突误映射；详见 test-report §2）。
- 前端：ESLint + Prettier + 4 组组件验收 **全部通过**。
- 未执行：H5 构建、Docker 运行、真实浏览器视觉验收、真实外部模型/OCR、研究证据重放（见 test-report §5）。

## 6. 与计划书的差异 / 需确认项

| 编号 | 事项 | 建议 |
| --- | --- | --- |
| U1 | 仓库无 `AGENTS.md`（计划书 §10 要求阅读） | 是否补建；已用 README + 维护说明 + STATUS 替代 |
| U2 | PostgreSQL 是否正式目标（计划书标注「新增建议」） | 确认后 P2 按 PG 建 schema |
| U3 | 行情大表（daily_prices/instrument_history）去向 | PG 主档 vs Python DuckDB/Parquet 边界，P2 定稿 |
| U4 | 实测 JS 文件 26 个 ≠ 计划书 20 个 | 以实测为准 |

## 7. 关键风险提示

1. **并发原子性缺陷**：并发恢复码兑付、同账户并发写已暴露 SQLite 锁竞争被误报为 503。迁移时（D1/D3）必须以显式冲突处理 + 唯一约束/行锁实现「一次成功或明确冲突」，不能仅靠换 PostgreSQL。
2. **历史指纹**：不得用 .NET 默认序列化重算覆盖旧指纹（D4），否则账本校验与回放会失配。
3. **数值精度**：C# decimal 28–29 位上限，Python Decimal 超范围字段须导入前列清单映射（D2），禁止 double 中转。

## 8. 下一步（P1 起，非本轮）

P1 建立 .NET 10 骨架（controller-based Web API + Core + Infrastructure，nullable + 编译警告，异常转换/请求ID/认证管线/健康接口/OpenAPI），保持旧响应契约；接入 `dotnet build`/`dotnet test` CI。按阶段分支 `codex/csharp-refactor-p1` 交付，逐阶段交 Codex 审查。
