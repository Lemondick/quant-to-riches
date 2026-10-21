# 《02》阶段 E：板块质量与最终回归验收

## 1. 本阶段完成内容

对应需求9、10、12、13的阶段E：保留行业/概念/地域目录，按来源隔离身份，完善真实零值、缺报价、无效、旧资料及未知时间展示，完成原页面、16指数、基金目录和K线回归。最终审查另修复阶段D目录旧内存快照导致重复强制刷新的性能问题。

基线0c404aabea3db48fcc9d5fc7bca7a0c364700a51，任务分支codex/sector-quality-final，最终源码32d31ad5bc6f11a253e5a5447cce0637ba3485fe；[Linux CI 39524121426](https://github.com/Lemondick/quant-to-riches/actions/runs/39524121426)。验收归档之后只改Markdown；main、服务器和原工作区保留。

## 2. 修改文件清单与原因

| 文件 | 修改原因 |
|---|---|
| `.github/workflows/ci.yml` | 在原前端作业加入板块浏览器验证并保留截图。 |
| `docs/superpowers/plans/2026-10-21-sector-quality-final.md` | 明确阶段E接口、成功标准和最终回归清单。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/PublicDataQuality.cs` | 保留上游拒绝/旧状态，新浪缺来源时钟不以抓取时间替代。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/FundCatalogService.cs` | 同一已观察旧目录只发起一次强制刷新，跨账号共享原子标记，异常可重试。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/MarketFreshnessTests.cs` | 回归invalid/unavailable/stale/未知状态与新浪旧快照错误时钟。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/FundCatalogApiTests.cs` | 真实PG/API/队列完成发布后，两个账户反复读取不能增加刷新任务。 |
| `web/frontend/src/utils/sectorPresentation.ts` | 统一报价校验、质量标签、零值颜色及组合目录状态。 |
| `web/frontend/src/components/LiveMarket.vue` | 复用展示助手，加入逐行质量列、隔离来源和类别，维持原样式。 |
| `web/frontend/tests/sector-presentation.test.mjs` | 校验坏值/真实0、来源时钟、目录覆盖与报价状态独立。 |
| `web/frontend/scripts/verify-sector-quality.mjs` | 六宽度、来源/类别隔离、单源失败及真实质量状态的显式夹具验收。 |
| `web/docs/refactor/sector-quality-decisions-20261021.md` | 保存来源实测、规则、审查取舍与待办。 |
| `web/docs/refactor/stability-implementation-20261018.md` | 更新两份需求的阶段完成状态和实际证据。 |
| `web/docs/refactor/sector-quality-acceptance-20261021.md` | 本验收归档，区分本地、Linux和真实来源证据。 |

## 3. 接口、结构与迁移

本阶段没有新增Provider、HTTP接口、数据库字段或迁移，完整迁移仍到阶段D的0024。原MarketSector/SectorCatalog字段向后兼容；前端识别缺省质量为待核验，源+类别+代码组成行身份。invalid/unavailable不展示为有效报价；0和null分开。目录未知覆盖、旧目录、报价分页接收数、缺报价、源失败与未知时间可同时陈述。

原生读取层不提升上游invalid/unavailable/stale，不认识的质量状态按无效处理；保留原因。无效来源时钟和新浪旧快照伪时钟清空，原fetched_at保留。

目录内部Snapshot增加原子刷新标记：每份观察到的旧快照只尝试一次force=true；入队异常重置标记；60秒重读生成新快照后允许重试。不把force改成false，避免数据库完成时间较新但提供器原抓取时间已旧时无法更新。仅节制派生公开任务，不写财务资料。

## 4. 真实数据源核验

2026-10-21T08:24:59+08:00只读正式适配器探测，实际收到1299条，0.929秒。东方财富行业497（其中1条缺报价，quote_total496）、概念516、地域31；其行情来源时间为2026-10-20T15:39:30+08:00。新浪行业49、概念175、地域31，没有行情时间，因此全部保持unknown_time。所有官方目录总数及完整性保持未知；行情分页总数不能替代官方覆盖总数。

这是当次公开源观测，不能承诺持续可用、无延迟或官方全覆盖。原始来源时钟、抓取时钟、缓存读取时钟分开；源失败保留旧证据和失败状态。16指数本阶段未改变映射，[逐项身份与历史来源验收](global-index-history-acceptance-20261020.md)、[唯一注册表来源记录](index-registry-evidence-20261020.md)继续适用；第16项中证1000仍为产品暂定项，不冒充截图确认。

基金供应商目录实测28107条/33种原始类型，时间和限制见[阶段D验收](fund-catalog-acceptance-20261021.md)。缺正式NAV、分类事实或份额证据时保持未知，不补估计数字。

## 5. 测试结果与命令

最终源码32d31ad5bc6f11a253e5a5447cce0637ba3485fe的上述Linux CI四作业成功：Python业务527、真实PostgreSQL18/.NET488、Worker149、原生工具15、结果报告5，全部通过，失败/错误/跳过均0。Node60、类型、lint、严格Linux格式、原演示及独立连接构建、原页面与指数/基金目录/板块浏览器步骤全部通过。

主要命令：后端工作目录 `uv run python -B ../../.github/scripts/test_report.py --start scripts --pattern 'verify_*.py'`；CI `dotnet test web/backend-dotnet/QuantAssistant.slnx -c Release --no-build`；Worker及数据库工具分别由同一test_report入口发现；前端 `npm test`、`npm run typecheck`、`npm run lint`、`npm run format:check`、`npm run build`，及 `verify-index-strip.mjs`、`verify-fund-catalog.mjs`、`verify-sector-quality.mjs`。

先复现原生5项失败和前端3组缺口，再GREEN：MarketFreshness14、Node60。目录刷新回归提交da242bf的[真实PG RED 39523725528](https://github.com/Lemondick/quant-to-riches/actions/runs/39523725528)实际488项中487通过、1项失败、0跳过：最终持久化任务数预期1、实际3；修复后同一测试和完整套件通过。未删除、跳过或弱化业务断言。

Windows全量Python527项中526通过、1项Linux父进程保护跳过；Worker149通过；修复后的本地.NET280通过、174项PG相关跳过、0失败。本机缺PG，不把跳过计作通过，采用Linux真实数据库结果。source verify通过，但运行准入仍按原配置阻断，不表示生产就绪。

## 6. UI验收

保持原Vue暗色、侧栏、卡片与布局。板块10组、目录16组及16指数独立展示，320/390/1024/1440/1920/2560六宽度通过；查看390/1440截图。覆盖同名同码跨来源/类别隔离、单源失败、旧目录+未知时间+缺报价并存、真实零值/坏值、目录筛选/分页/迟到响应隔离。浏览器数据均显式本地夹具，不导入生产，不作为真实行情证据。

原11页88项布局/导航、两卡32组（分帧0、消失0、位移0px）、K线8组通过。合成接口导航p95约99.7ms；K线模拟503准备1.35–1.37秒、缓存重复25.6–33.0ms，仅验证UI行为，不是生产网络速度。未操作真实账户关注。

## 7. 尚未完成、审查取舍与限制

最终一次独立审查无Critical，一项Important已在同一轮RED→GREEN修复；一项Minor延后：公开资料strategy_type非空但不匹配，而fund_type实际支持指数策略时，证据字段可能误指strategy_type。分类结果有真实fund_type依据；后续应选择实际匹配字段并增加双字段回归。

本阶段取舍（含审查未评价范围）完整记录如下：

1. 技能中途恢复后转换Task标题和账本，不重做已完成工作；误判代价是记录不一致，已有Git/测试证据可核对。
2. 按恢复的技能仅做一次完整独立审查，替代原执行计划“不分派”盘点选择；误判代价是一次有界审查成本。
3. 来源真实性优先于原盘点“不改后端”的假设，追加最小原生质量修复；误判代价是被拒绝报价需等有效刷新恢复。
4. 按观察到的旧快照合并强制刷新，60秒重读允许再试；误判代价是本进程重试可能等待一次重读间隔。
5. 独立审查未重跑完整CI/PG/Worker/Python和六宽度浏览器，采用作者实际运行的精确源码CI及UI证据；误判代价是环境差异仍可能遗漏。
6. 公开源数量/时间只作一次观测，持续可用性和全覆盖不保证；误判代价是源故障时继续显示已标明的缺失或延迟。
7. 生产迁移、部署、负载及真实账户操作按授权不执行；误判代价是生产性能和运行效果尚未验证。
8. 审查范围外的继承实现由既有回归覆盖，不冒充全面审计；误判代价是未覆盖的旧缺陷可能仍在。

免费源延迟未知、第16项名称产品确认、更多基金直接资料/份额证据、真实运行准入及后续发布仍待另行处理。回滚采用派生代码兼容流程，0024回切限制见阶段D/Ubuntu手册；没有执行数据库降级或删除记录。

## 8. 安全边界确认

未合并main、未部署服务器、未执行生产迁移、未改生产账本、未真实交易、未调用付费模型、未提交密钥。真实账户关注/金融写入未执行；所有CI用户/数据库和浏览器写操作均为显式合成夹具。原工作区未提交修改保留，任务分支按精确blob/tree/commit校验上传GitHub。临时目录清理此前被自动审批以策略原因拒绝，保留原目录，不绕过删除。
