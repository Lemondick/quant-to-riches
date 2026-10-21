# 15项已确认指数与分类证据修复验收 — 2026-10-21

## 1. 完成内容

按用户最新指示“按照15个指数来，16个不要”，覆盖原《02》的暂定候选约定。从唯一注册表移除中证1000；保留其余15项已确认身份、顺序、供应商映射和历史能力。同步修复此前遗留的基金分类证据归属问题。基线7e8eed91d1cb382a94d111d835e12b439852f786，任务分支codex/confirmed-fifteen-indices，最终源码6e56b50a1b63714eb36c83015392901646b71f56，[Linux验收39527288345](https://github.com/Lemondick/quant-to-riches/actions/runs/39527288345)。

## 2. 修改文件与原因

| 文件 | 原因 |
|---|---|
| `web/backend/src/quant_assistant/providers/index_registry.json` | 唯一权威目录删暂定项，版本2026-10-21.1，其余身份/顺序不变。 |
| `web/backend/src/quant_assistant/providers/index_registry.py` | Python校验数量改15，继续身份/时区/代码等严格校验。 |
| `web/backend-dotnet/src/QuantAssistant.Core/Market/IndexRegistry.cs` | 同一嵌入JSON的原生数量校验改15。 |
| `web/backend-dotnet/src/QuantAssistant.Core/Market/FundClassification.cs` | 证据按实际匹配字段生成，规则版本升public-fund-rules.2026-10-21.2。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/IndexRegistryTests.cs` | 15项全确认、删除ID/旧代码拒绝、4个原历史别名不变。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/IndexApiTests.cs` | 15个独立状态；旧快照包含中证1000时不能复活该项。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/GlobalIndexApiTests.cs` | 真实PG新历史输入为15减4=11，继续逐项验证不重复排队。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/FundClassificationTests.cs` | 策略不匹配/为空、策略匹配/两字段均匹配四场景的真实证据字段和原文。 |
| `web/backend/scripts/verify_index_registry.py` | 共享目录15项/明确未知候选及旧别名拒绝。 |
| `web/backend/scripts/verify_index_quotes.py` | 15项完整报价、单项失败余14、美股3项失败余12，保留隔离断言。 |
| `web/worker/tests/test_index_quotes.py` | Worker目录/状态数量与共享15项一致。 |
| `web/worker/tests/test_global_index_history.py` | 全量历史能力回归名称改15，继续遍历全部实际定义。 |
| `web/frontend/tests/index-presentation.test.mjs` | 只显示15个已确认身份；单项缺失仍保留其余14项。 |
| `web/frontend/scripts/verify-index-strip.mjs` | 六宽度15卡/15选择项，没有中证1000和暂定文字，原交互继续。 |
| `web/frontend/scripts/verify-layout-navigation.mjs` | 部分行情场景仍为15个独立身份槽，原88布局/导航检查保留。 |
| `web/frontend/scripts/verify-sector-quality.mjs` | 板块筛选不改变15项独立指数。 |
| `web/frontend/src/views/Market.vue` | 演示页也删除中证1000块，保留原界面。 |
| `web/docs/api.md` | 当前接口数量、版本和移除候选说明同步。 |
| `web/frontend/README.md` | 当前展示、历史和浏览器验收说明同步15项。 |
| `web/docs/refactor/stability-implementation-20261018.md` | 当前用户决定覆盖原暂定候选，保留阶段历史事实。 |

本文件归档验收。旧阶段文档中16项记录对应当时已测源码，保留历史并由本次明确更新，不将旧测试倒改为15项。

## 3. 接口、数据与迁移

没有新增接口、数据库迁移或财务写入。market/snapshot的index_registry和index_statuses为15项；indices仅含实际合格报价，缺失不补零。registry_version更新使前端按新目录读取；原生质量层读取旧快照时过滤已移除项。历史选择/预热/调度继续消费唯一目录，不为cn.csi.1000创建新任务；原cn.csi.1000和000852解析为未知。4项原历史参数继续兼容，其余11项按index_id使用。

分类策略实际匹配复制指数型/指数型时引用strategy_type，否则股票型-标准指数引用fund_type；不再因非空字段而误选证据。资产分类、标签结论和基金名称不参与的新规则保持原有语义。泛用估值模块保留中证1000的真实证券名称映射，供真实基金基准等业务描述；它不注册为第16项，不增加行情卡片/历史选择/刷新任务。既有派生缓存不删除、真实金融历史不修改。

## 4. 来源与规则

15项全部沿用此前已逐项核验的相同映射，只有用户不需要的候选被移除。包括9项A股、2项港股、1项日经现货、3项美股；[既有身份和历史核验记录](global-index-history-acceptance-20261020.md)继续适用于保留项。本次没有替换供应商或编造新的点位/日线；公共源的发布时间、延迟和全覆盖限制不因数量调整消失。指数、基金分类、板块仍独立。

## 5. 测试与构建

RED先实际观察到Python注册表3项失败、Node展示2项失败、原生6/20失败，包含旧快照候选复活及两项错误证据字段。GREEN完整LinuxCI：Python527、真实PostgreSQL18/.NET492、Worker149、工具15、报告5，全通过且0失败/错误/跳过；前端Node60/类型/lint/严格格式/两种构建及浏览器全部通过。

本地.NET284通过/174因缺PG跳过；Worker149、Node60通过。完整Python初跑525通过/1失败/1 Linux专属跳过：美国批失败后旧数量13应改为15-3=12，原隔离断言全部保留；更正后完整重跑526通过、0失败/错误、1项Linux父进程保护跳过，该项在LinuxCI实际执行通过。本地格式使用既有Windows换行兼容，Linux仍用严格原门禁。source verify通过，运行准入仍按真实配置阻断，不冒充生产就绪。

命令：`dotnet test web/backend-dotnet/QuantAssistant.slnx`；后端cwd下`uv run python -B ../../.github/scripts/test_report.py --start scripts --pattern 'verify_*.py'`；Worker unittest discover；前端npm test/lint/typecheck/format:check/build，及既有verify:ui、verify-index-strip、verify-fund-catalog、verify-sector-quality、verify-layout-navigation、verify-chart-pair、verify-market-kline脚本。一次独立只读审查覆盖7e8eed9..6e56b50，无Critical/Important/Minor；审查未重跑测试，实际全量门禁由作者核对上述精确源码CI。

## 6. UI验收

显式本地夹具验证320/390/1024/1440/1920/2560六宽度15卡和15选择项，不出现中证1000/暂定标记；单项缺失余14项保留，错误身份/迟到响应/版本切换/市场时区/真实零值回归继续通过。基金目录16组、板块10组、原演示52检查通过；原11页88布局/导航、两卡32组（分帧0、位移0px）、K线8组通过。保持原Vue主题/导航/布局，无真实账户操作。模拟网络性能不是生产速度承诺。

## 7. 未完成与限制

此前分类证据Minor已修复；第16项候选已按用户指令取消，不再等待确认。本次两项修正没有遗留已知代码问题。独立审查未评价真实供应商连续可用性/生产行为，以明确运行限制保留；真实PG以本次零跳过CI闭环。旧阶段16项文档保留历史、当前说明明确覆盖；泛用基准名称和既有数据库记录保留，不能注册或显示成第16项。

供应商连续可用、未知时间/总数、更多基金直接资料/份额证据、跨2026年份日历、真实运行准入和生产负载依然是原有边界，未知如实保留。没有通过猜测补齐数据。生产部署/运维恢复/真实付费模型验收不在本轮执行范围。

## 8. 安全与交付边界

保留已有独立工作区和原用户未提交改动；仅上传任务分支，不合并main，不部署服务器，不执行生产迁移、真实交易、真实账户关注或付费模型，不提交密钥。本次没有递归删除临时目录。最终源码验收后仅提交Markdown归档，并核对GitHub分支SHA与本地相同、main保持原值。
