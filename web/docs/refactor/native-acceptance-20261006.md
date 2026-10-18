# 原生迁移隔离验收记录

日期：2026-10-06。执行环境：Windows，.NET SDK 10.0.401，PostgreSQL 18，Python 3.12，Node.js 24.12.0。业务测试使用独立端口 55439；发布联动使用 loopback 55440。所有账户与凭据均为合成测试数据。

## 执行结果

| 命令或场景 | 结果 |
| --- | --- |
| `dotnet test QuantAssistant.slnx --no-restore`，设置隔离 `QUANT_TEST_PG_CONNECTION` | 113 passed，0 failed，0 skipped |
| 对空库运行 API `--migrate migrations`，再次运行 | 0001–0012 应用，重复跳过 |
| Python `unittest discover -s scripts -p 'verify_*.py'` | 410 passed |
| `unittest discover -s web/worker/tests` | 8 passed |
| `unittest discover -s web/backend-dotnet/tools/tests`，设置隔离 `QUANT_TEST_PG_DSN` | 6 passed |
| 前端 `npm run typecheck` / `lint` / `format:check` | passed |
| 五组 `verify-*.mjs` 组件脚本 | passed |
| 前端 `npm run build`；原生 `publish.ps1` | passed |
| Python Ruff、11 个限定严格 mypy 文件、源码规模门禁 | passed |
| PowerShell/Bash 解析、网页源码凭据扫描 | passed |

C# 场景覆盖账本事务、精确 JSON、账户隔离、CSRF、账户停用与恢复、注册上限、幂等冲正、任务期限与过期租约、模型预算及未知用量、原生静态资源和全部基线路由。新增正式净值场景确认来源文件变化会回滚，恢复正确来源后仅提交一次。

worker 测试覆盖原生 API 配置目录、原生/服务凭据环境过滤、大输入管道阻塞时的期限退出、完整账户快照与金额精度、正式净值的内容及文件哈希、私有及混合 DNS 拒绝、TLS 主机名与公网地址绑定、预览变更发生于费用预留和模型调用之前。

工具测试覆盖冻结 WAL、精度边界、证据账户归属、运行站点排他锁、两账户正反向导入和后续编号、PostgreSQL 转储与文件备份恢复、中断续恢复及新写入后拒绝续恢复。

## 原生发布联动

通过默认启动器运行真正发布目录中的 C# API 和三个 Python worker。访问 H5 静态入口，使用合成管理者登录，核验 Secure Cookie、数据库和 worker 就绪状态，调用数据状态、策略设置、收益归因和基金生命周期四类接口。任务由 worker 领取并通过 C# 提交，未启动旧 FastAPI 业务服务。

发现并修复了发布目录相对路径、ASP.NET WebRoot 初始配置、快照中的基金批次表名、子进程日志继承和轮询日志量等问题。生产默认关闭逐 span 控制台导出；可用 `QUANT_TRACE_CONSOLE=1` 临时诊断。正常内部轮询降低到 Debug，框架正常请求日志默认不输出，错误仍保留。

最终发布包 `native-release-delivered` 再次通过上述联动。Windows supervisor 将 API 工作目录设为 `api/`，确保加载发布版 `appsettings.json`；保留三个 worker 运行并空闲观察 10 秒，日志增量为 0 字节。该短时检查只证明正常轮询不再刷日志，长期日志保留和磁盘容量仍需现场观察。验收后已关闭本轮拥有的 API、worker 和隔离 PostgreSQL。

## 依赖与现场边界

npm 审计目前仍有 19 项告警：18 high、1 moderate。兼容修补包括 `proxy-addr 2.0.8` 和 `source-map-js 1.2.2`。UniApp 将部分 Vue 依赖固定在 3.4.21，强制换版本会超出已验证组合；解析工具也有未修补版本。braces 告警目前无已发布修补版本：[上游告警](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)。Vue SSR 告警：[上游记录](https://github.com/advisories/GHSA-g2v6-rqmx-r4w6)。本次没有把审计报告误写成零漏洞。

未验证正式域名与 HTTPS、真实账户迁移、真实付费模型、OCR/行情供应商的完整现场行为、生产服务器长期运行。发布包和合成回归是代码交付证据；生产切换后的数据核对、备份以及长期观察仍需执行。[现场步骤](p7-runbook.md)。
