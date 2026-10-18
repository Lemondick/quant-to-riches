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

## 《01》后续进度

- [ ] B：BUG-003 / BUG-004，账本幂等与分红估值日约束。
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
