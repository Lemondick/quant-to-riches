# Backtest correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成《01》阶段 E / BUG-011 至 BUG-013；整体样本外指标可由日期收益重算，同日冲突拒绝，策略上下文不能取得未来价格。

**Architecture:** 保留现有引擎和 `calculate_performance` 的现金流调整及统计定义。各折保存实际净值观测之间的收益与基准收益，首条实际收盘观测作为可核验起点，不编造窗口前价格。拼接非重叠的有效收益观测，不在折间填零；明确列出排除的区间，按实际收益观测数年化。指标计算复用同一实现；费用为各折真实费用之和，换手为各折双边换手之和并明确命名，不能声称使用连续资本的全期平均资产分母。

**Tech Stack:** 现有 Python/pandas/numpy/dataclasses、现有 Markdown/JSON 报告、Python business tests 与全量 Linux CI。

**Spec:** 用户桌面 `01-%E5%BD%93%E5%89%8D%E9%97%AE%E9%A2%98%E4%BF%AE%E5%A4%8D%E4%B8%8E%E7%A8%B3%E5%AE%9A%E6%80%A7%E6%95%B4%E6%94%B9%E9%9C%80%E6%B1%82.md` 第 6 节。按已授权顺序继续；不合并 main、不部署生产。

## Global Constraints

- 不修改生产财务历史、真实订单或交易。
- 保留研究限制、费用与数据快照 ID；不把缺失收益补零或用折均值冒充整体指标。
- 保留上市/退市、`prices_through(signal_date)`、snapshot ID 检查。
- 不改 Vue 页面布局和主题；报告只调整数据与必要的口径说明。

## Review Focus

- 短折与长折拼接后达到年化最低观测数时，不能继续平均各折 NaN 或有限指标。
- 基准缺少某个净值日期时必须拒绝，不通过首尾相交掩盖缺失日收益。
- 相邻折的起点是独立实际观测，不能以新初始资本的跳变或人工零收益连接。
- 同日极小 Decimal 差异必须在 float 转换前拒绝；输入顺序不能决定采用哪条。
- 内置策略的特征缓存使用因果前缀；移除未来价格后不得误用另一快照或破坏过去日期的特征。

## Task 1: 真正的 OOS 收益拼接和指标

**Files:** `backtest/walk_forward.py`；新增 `backtest/oos.py`（有日期的观测和拼接）；`reporting/backtest_report.py`；新增 `scripts/verify_backtest_oos.py`。

**Interfaces:** 每折 `oos` 保存 baseline 日期、调整后起点净值、基准起点及逐日 `date/strategy_return/benchmark_return/adjusted_unit_nav/benchmark_close`。`WalkForwardResult` 保存聚合方法、`omit_unobserved_returns` 策略、排除区间和统计观测数。实际 NAV 与 benchmark 在测试窗口中逐日对齐；引擎必须声明 trading_dates 并与实际 NAV / benchmark 匹配。测试输出只有指标摘要或不足两个有效 NAV 时返回可识别错误，不伪造曲线。训练/验证仍允许既有指标输出以选择参数。

- [x] 新增失败测试：跨折累计回撤、不同长度折的年化/波动/Sharpe 独立数学断言、折间空档不补零、重叠拒绝、NAV/benchmark/声明交易日期缺失拒绝、摘要不能充当日收益。
- [x] 运行并保存 RED；最小实现 OOS 数据结构、严格对齐和拼接，复用 calculate_performance。
- [x] 用实际测试 NAV 重算每折指标；费用/换手保留明确的各折合计口径，报告 JSON/Markdown 暴露观测、空档和方法，旧手工构造结果显示口径未知。
- [x] 运行 OOS、报告及 Python 全量必要检查，保存实测结果并提交。

## Task 2: 同日冲突数据 fail-closed

**Files:** `backtest/metrics.py`；新增 `scripts/verify_backtest_metrics.py`。

- [ ] RED：NAV、外部资金流、benchmark、费用/换手同日冲突；完全重复允许；Decimal 微差和随机重排；date 列与 DatetimeIndex 使用同一日规范化。
- [ ] 在浮点计算前，按规范日期拒绝非相同记录，错误前缀 `conflicting_duplicate_date`；只去除真正重复，不自行合并多笔现金流。
- [ ] 运行指标和 Task 1 OOS 回归、source verify；提交。

## Task 3: 移除未来价格上下文入口

**Files:** `backtest/engine.py`、`engine_components.py`、`production.py`；新增 `scripts/verify_backtest_causality.py`。

- [ ] 盘点全部 BacktestContext / prices_all 调用者，保留已有数据准入检查。
- [ ] RED：策略尝试读取未来字段/日期失败；扰动未来价格不影响过去决策；前缀输入包含截止日且不会修改底层源。
- [ ] 删除 prices_all；生产特征只读截至 as_of 的价格，保留实例缓存，不把全量未来帧放进其他公开上下文字段。
- [ ] 运行内置策略和引擎回归，记录有代表性数据的性能成本；如需优化，只在非决策准备边界内进行，不能恢复未来价格入口。提交。

## Task 4: 阶段 E 验收

- [ ] 完整 Linux CI，PG/Worker 不得跳过；所有测试、前端和构建通过。
- [ ] 一次独立整体审查，重要问题先 RED 后 GREEN 并完整回归。
- [ ] 更新阶段验收文档与取舍、命令、实际计数；展示后继续阶段 F。
