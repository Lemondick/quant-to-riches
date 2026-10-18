# 多期归因与费用采集实施计划

> 按用户既有授权由当前任务直接实施，使用 executing-plans 流程逐项验证；不委派、不提交代码。

**目标：** 连结当前账户已完成的连续单期归因报告，并修复原生模型调用用量采集的字段兼容。

**架构：** 沿用 C# / PostgreSQL 报告事务及 Vue 页面。多期使用 Frongello 递推，冻结所选原报告的输入和来源；费用仍由服务商用量和调用时单价决定。

**技术：** ASP.NET Core 10、精确 decimal、TypeScript、现有 Python worker。

**范围：** 对应本轮对话设计和上一轮未完成清单。单期可使用各期不同权重；不自动从当前持仓倒推历史，不代替现金流中和后的收益证据，不推断基金全仓或指数历史成分。

## 多期归因

补充：新增 `0014_linked_attribution.sql`，扩展0013的来源约束并保持旧数据；多期报告禁止被单期重新计算覆盖。

- [x] 在 `LinkedAttributionTests.cs` 先验证新接口缺失；覆盖复利对账、跨账户、缺期、重叠、重复、草稿、嵌套多期、畸形明细及幂等重试。
- [x] 新增 `BrinsonLinking.cs`，按时间递推行业配置/选择/交叉效应，校验单期收益和已保存效应一致；总效应对齐复利超额收益，拒绝数值溢出。
- [x] 新增 `LinkedAttributionService.cs` 与 `/api/v1/attribution/reports/link`。事务内读取并锁定所选报告，按账户隔离、日期连续和同组合/基准校验；保存完整来源快照，保留原报告。
- [x] 在现有报告历史中增加多期选择及生成入口，允许跨页选择；账户变化清空，迟到结果失效，读取多期报告展示复利收益、行业效应和单期来源。相同输入的写入重试复用报告编号。
- [x] PostgreSQL、实际 Vue 组件、类型及格式回归。

## 费用采集

- [x] 在 worker 测试中复现 `input_tokens` / `output_tokens` 丢失。
- [x] 统一使用现有用量校验入口，支持两组字段、拒绝冲突/布尔/负值/越界，不估算缺失用量。
- [x] 保持旧未知费用和金额预算阻断；验证零用量、失败调用及无价格场景，必要时显示未知原因。

## 最终验收

- [x] C# 完整回归、worker 回归、九组前端回归、TypeScript/ESLint/Prettier/Python检查。
- [x] 创建不可变原生发布包，以本机 HTTPS / 三个 worker 验证新接口及实际网页。
- [x] 清理合成报告，比较财务数据指纹，停止自建服务；更新实施状态与验收记录。

方法参考：[Frongello 实现及递推公式](https://github.com/R-Finance/PortfolioAttribution/blob/master/R/Frongello.R)。费用字段参考：[官方 Token 用量说明](https://developers.openai.com/api/docs/guides/token-counting)。

