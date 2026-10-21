# 《02》阶段 D：真实基金目录与多维分类验收

## 1. 完成范围

对应需求8、10–13：真实供应商目录、独立多维分类、搜索/组合筛选/分页、原榜单/详情/账户关注保留；目录不与16指数或行业/概念/地域结构混用。基线3a57384b9159c082c8b18eb91490663581304872，独立分支codex/fund-catalog-taxonomy；最终源码5c167a3e31d4d49b5c91f2f4af52c4dd8427c0a9，[Linux CI39520665779](https://github.com/Lemondick/quant-to-riches/actions/runs/39520665779)四作业成功。归档只改Markdown，不合并main、不部署。

## 2. 修改文件与原因

| 文件 | 原因 |
|---|---|
| `.github/workflows/ci.yml` | 加入连接目录浏览器验收并保存截图，沿用原四作业CI。 |
| `docs/superpowers/plans/2026-10-21-fund-catalog-taxonomy.md` | 按来源事实、分类接口、UI和整体验收逐步落实并完成清单。 |
| `web/README.md` | 同步源码迁移范围与现有运行说明。 |
| `web/backend-dotnet/ARCHITECTURE.md` | 目录单飞/派生迁移/调度与正式运行入口同步。 |
| `web/backend-dotnet/deploy/ubuntu-runbook.md` | 更新完整迁移到0024和只读派生回滚边界，不执行部署。 |
| `web/backend-dotnet/migrations/0024_public_fund_catalog.sql` | 只扩展派生公共目录、发布校验及合格延迟历史防倒退共享。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Controllers/FundCatalogController.cs` | 新增无等价的只读分类/目录路由和有界组合分页参数。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Program.cs` | 注册单例目录服务，原接口和身份中间件保留。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/FundCatalogQuery.cs` | 统一分页/稳定排序/组合AND和OR/未知筛选、真实目录内分类计数。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/FundCatalogService.cs` | 共享有界单飞目录、逐页公共NAV、独立时钟与货币/QDII/校验状态。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/PublicSnapshotRefresher.cs` | 核心行情完成后辅助预热目录，沿用配额和超时。 |
| `web/backend-dotnet/src/QuantAssistant.Core/Market/FundClassification.cs` | 唯一确定性分类，资产/制度/结构/策略/基准分开，保留证据及未知。 |
| `web/backend-dotnet/src/QuantAssistant.Infrastructure/Jobs/ComputeJobRepository.cs` | 声明公共能力/6小时TTL，仅合格共享目录参与完成任务去重。 |
| `web/backend-dotnet/src/QuantAssistant.Infrastructure/Jobs/PublicSnapshotRepository.cs` | 目录保留期与公共资料事实投影；不查询私人费率或基金族。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/FundCatalogApiTests.cs` | 真实账户权限、分页/空搜索/跨账户公共共享、无爬虫、NAV口径。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/FundCatalogQueryTests.cs` | 组合筛选/搜索/页号/隔离/参数边界与28000条性能预算。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/FundCatalogStorageTests.cs` | 真实PG共享校验、坏结果恢复和迟到历史防倒退。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/FundClassificationTests.cs` | 无名称推断、错误对比列表、真实基准成分、类型冲突和坏可选资料。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/PublicReadCacheTests.cs` | 新目录能力用合格公共夹具继续验证通用内存隔离。 |
| `web/backend/scripts/verify_fund_catalog.py` | 严格JS/JSON、身份/重复隔离、未知类型、原时钟和缓存不可变。 |
| `web/backend/scripts/verify_public_fund_detail.py` | 真实字段别名/来源事实/排除通用对比和Pydantic透传。 |
| `web/backend/src/quant_assistant/controllers/fund_schemas.py` | 向后兼容可选公共分类事实字段。 |
| `web/backend/src/quant_assistant/providers/fund_catalog.py` | 东方财富真实目录适配，有界HTTP/缓存，只保留公共身份类型。 |
| `web/backend/src/quant_assistant/providers/fund_detail.py` | 保留已实测公开字段及来源名，不改变现有详情流程。 |
| `web/backend/src/quant_assistant/providers/fund_detail_parsing.py` | 公开事实契约/纯构造器，排除名称/私人资料/通用基准列表。 |
| `web/docs/api.md` | 公开目录/分类API和正式NAV/未知字段契约。 |
| `web/docs/refactor/fund-catalog-decisions-20261021.md` | 保存来源实测、规则理由、失败修复和复核依据。 |
| `web/docs/refactor/stability-implementation-20261018.md` | 持续记录阶段D已完成、E仍待验收。 |
| `web/frontend/README.md` | 更新连接目录使用与来源限制说明。 |
| `web/frontend/scripts/fund-catalog-fixtures.mjs` | 明确本地浏览器数据，不被应用或生产Provider导入。 |
| `web/frontend/scripts/verify-fund-catalog.mjs` | 六宽度/组合/分页/状态/迟到隔离和模拟账户请求边界。 |
| `web/frontend/src/components/FundCatalog.vue` | 新增懒加载目录UI，显示多维证据、正式NAV和未知，不另写分类。 |
| `web/frontend/src/components/LiveMarket.vue` | 保留默认日榜，增加目录入口与原详情/关注复用，行情源失败不阻塞目录。 |
| `web/frontend/src/services/fundCatalog.ts` | 类型契约、完整请求身份、响应校验、null/0与北京时间展示。 |
| `web/frontend/tests/fund-catalog.test.mjs` | 请求参数隔离、坏响应、来源时钟与真实零值展示。 |
| `web/worker/public_operations.py` | 轻量常驻fund.catalog能力，拒绝账户/标的输入。 |
| `web/worker/tests/test_public_worker.py` | 公开能力租约执行、目录输入边界和提供器复用。 |

## 3. 接口与数据结构

新增GET /api/v1/funds/categories和GET /api/v1/funds/catalog，盘点确认无等价接口。真实账户鉴权；q/page/page_size(1..100)和重复维度fund_type、asset_class、investment_scope、strategy_tags、product_structure、benchmark_index_id、share_class、regulatory_category、classification_status。维度间AND、同维度OR，策略标签全部满足；__unknown筛未核验；代码稳定排序。根返回实际目录内计数和分类版本，单页最多100条；前端使用50。

逐行保留原始类型、独立分类字段/状态/逐字段证据、正式单位NAV/日期/日涨跌、NAV质量与原抓取时间；目录source_time为空、fetched_at/cache_read_at独立。public-fund-facts.v1为兼容可选字段；旧详情/榜单/估值和账户关注API保留。目录单飞/60秒重读，完整目录留服务端。新fund.catalog常驻能力及0024仅写派生公共缓存，不写私有费率、基金族或财务表。

## 4. 真实来源与分类规则

正式适配器2026-10-20T23:55:34Z实测东方财富fundcode_search.js共28107条、33原始类型、0隔离，0.303秒；是供应商目录，不是官方全市场完整性承诺。源没有目录发布时间，因此延迟未知，6小时TTL只节流。蛋卷023530实测公开类型/投资描述/业绩基准可用；benchmark_index是通用对比列表，航空基金不能因此关联沪深300。新浪110020实际Type/NewType/tzfw/bjjz等字段透传并保留来源名。510300蛋卷不销售时保持原fallback，不造资料。

public-fund-rules.2026-10-21.1仅采用明确供应商字段/公开资料和保守规则，资产/结构/制度独立；不凭名称猜A/C、ETF、地域、主题或跟踪。QDII不等于单一市场；QDII-混合债资产口径待核验。实际业绩公式成分可关联已注册指数，关系是performance_benchmark_component，不等于跟踪；未注册的基准保留真实原文、ID为空。范围叙述不猜，份额无直接证据留空；冲突保留证据并置空。缺少资料不阻止其余基金展示。

## 5. 测试与构建

最终5c167a3e31d4d49b5c91f2f4af52c4dd8427c0a9：真实PG18/.NET482、Python527、Worker149、原生工具15、报告5，全部通过、0失败/错误/跳过。Node57、类型、lint、严格Linux格式、原演示构建/11页交互、独立连接构建、指数与目录浏览器验收全部通过。Windows无PG，本地跳过不算数据库通过；真实PG结果来自上述CI。

RED覆盖新解析器/公共事实缺失和原生能力/分类/筛选未实现；D2第一次CI479/480，测试params数组协变错误如实记录，519a0af修正明确双参数后480全通过。最终补充坏目录不延长6小时缓存、延迟历史非fallback共享/防日期倒退、空默认搜索、真实0/货币/既有QDII T3策略、异常日涨幅留空。28000条明确夹具CPU分页预算通过；不把它当生产网络延迟。

## 6. UI与交互验收

保持原暗色、字体、侧栏、卡片、默认指数型基金日涨幅榜及原搜索/涨跌/详情/关注；只增加分类入口。目录16组浏览器检查，320/390/1024/1440/1920/2560无页面横溢出，表格内部滚动。资料/净值/抓取日期分开，未知/旧/源不可用/真实零值各自展示。关注唯一模拟PUT验证CSRF/会话范围，未写真实账户；本地夹具不导入生产。查看390和1440截图，原11页88布局/导航、两卡32项split0/位移0、K线8组全部通过。

本地合成接口导航p95约93.1ms，K线准备约1.35–1.37秒、重复29–44ms；这些仅为UI夹具回归，非生产速度或实时性承诺。

## 7. 迁移与回滚

0024只扩展public_compute_snapshots种类/TTL和发布函数：空、invalid、fallback、账户行数据不发布；合格迟到历史保留stale共享且最后日期不可倒退。原0023全空行情保护、隐私条件和完成通知保留。未在服务器运行。

回切新Worker前需处理fund.catalog派生行，恢复0022发布函数和原kind约束，保留0023；必须在经验证备份及兼容性流程下执行，数据库不自动降级。禁止删除真实NAV/账本/交易记录。操作手册仅更新文档，不表示部署完成。

## 8. 未知、限制与后续

目录发布时间/上游延迟不可证，免费公开接口仍会失败；目录覆盖、正式NAV资料齐全程度、市场实时性是三件事。当前只补充已缓存公共详情的正式NAV和分类事实，不为每页50只发50个网络爬虫；无公共资料的结构/范围/策略/份额保持待核验。未付费/未调用模型，不用生成数据补空，第16指数仍暂定；未更改原工作区未提交修改、main或服务器。下一阶段E完成板块逐行质量展示和全链路验收。
