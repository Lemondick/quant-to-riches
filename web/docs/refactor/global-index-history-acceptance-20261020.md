# 《02》阶段 C：真实历史 K 线与市场时段验收

## 1. 完成范围

对应需求2/4/6/7/10–13，完成16项已核验历史映射、各市场2026日历和半日市、稳定ID原生路由、刷新和缓存隔离。保留原Vue主题、侧栏、基金涨幅榜及板块模块；第16项中证1000仍为暂定，未声称截图确认。

基线 `983de4ace7e31e41489137ad2b2136686f430949`，源码 **`5c032b8ccc3e42f5463a8b469cf86cfe7d8e0ef2`**，独立分支 `codex/global-index-history`。最后Linux CI：**[39453954440](https://github.com/Lemondick/quant-to-riches/actions/runs/39453954440)**，四个作业成功。后续归档只允许Markdown，源码与该次CI保持一致。

## 2. 修改文件及原因

| 文件 | 原因 |
|---|---|
| `docs/superpowers/plans/2026-10-20-global-index-history.md` | 分解日历、历史源、原生路由与验收步骤。 |
| `tests/fixtures/global-market-sessions.json` | Python/.NET 共用休市、半日市、DST、竞价与刷新窗口样本。 |
| `web/backend-dotnet/ARCHITECTURE.md` | 同步多市场窗口、任务预留、缓存与保留期契约。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Controllers/MarketController.cs` | 稳定ID历史路由、原四项兼容、坏缓存重新准备。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/ComputeService.cs` | 历史内存60秒和显式移除坏的公开缓存。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/PublicDataQuality.cs` | 按所属市场边界重算，历史失败状态不提升为有效。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/PublicIndexProjection.cs` | 保留供应商拒绝状态，不用时间重分类洗成成功。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/PublicMarketSnapshot.cs` | 四市场状态和14天旧证据保留。 |
| `web/backend-dotnet/src/QuantAssistant.Api/Services/PublicSnapshotRefresher.cs` | 任一活动市场触发、两项历史轮换、辅助18项限额。 |
| `web/backend-dotnet/src/QuantAssistant.Core/Market/IndexRegistry.cs` | 读取同一份扩充历史映射，限制可用报价适配器。 |
| `web/backend-dotnet/src/QuantAssistant.Core/Market/MarketSessionRules.cs` | 嵌入读取共享完整/半日市时段。 |
| `web/backend-dotnet/src/QuantAssistant.Core/Market/MarketSessions.cs` | 各市场开盘、午休、收盘、竞价和收尾窗口。 |
| `web/backend-dotnet/src/QuantAssistant.Core/QuantAssistant.Core.csproj` | 嵌入共享时段资源。 |
| `web/backend-dotnet/src/QuantAssistant.Infrastructure/Jobs/ComputeJobRepository.cs` | 可降低的后台入队上限和主行情领取优先级；总上限不增加。 |
| `web/backend-dotnet/src/QuantAssistant.Infrastructure/Jobs/PublicSnapshotRepository.cs` | 统一主行情14天读取及清理保留期。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/GlobalIndexApiTests.cs` | 新12项隔离、坏身份/质量缓存、周末和真实PG保留期。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/GlobalMarketSessionsTests.cs` | 共享边界样本与未知日历断言。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/IndexApiTests.cs` | 保留原四项兼容，删除空循环测试，补供应商失败状态。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/IndexRegistryTests.cs` | 原四项旧键与16项真实历史能力分别核对。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/MarketCalendarTests.cs` | 已知2026海外日历与未知2028年、原生PG导入分离。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/PublicDataQualityTests.cs` | 以显式空日历保留未知海外状态覆盖。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/PublicRefreshSchedulingTests.cs` | 实际PG验证美国开市/CN闭市及有界预热、预留与优先领取。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/QuantAssistant.Api.Tests.csproj` | 复制共享边界样本至测试输出。 |
| `web/backend-dotnet/tests/QuantAssistant.Api.Tests/RemainingPerformanceTests.cs` | 旧CN边界断言改调用真实共享时段入口。 |
| `web/backend/scripts/verify_global_market_sessions.py` | Python共享时段、日历和刷新边界覆盖。 |
| `web/backend/scripts/verify_index_quotes.py` | 市场隔离、休市TTL、空收盘报价、竞价成功缓存保护。 |
| `web/backend/scripts/verify_index_registry.py` | 核对16历史映射与仍仅4个旧缓存别名。 |
| `web/backend/src/quant_assistant/providers/data_quality.py` | 使用本市场竞价与收盘边界。 |
| `web/backend/src/quant_assistant/providers/exchange_calendars.json` | 官方2026 HK/US/JP现货假日。 |
| `web/backend/src/quant_assistant/providers/exchange_sessions.json` | 带来源、版本、年份与半日市的唯一时段资源。 |
| `web/backend/src/quant_assistant/providers/index_quotes.py` | 按市场分组、长短TTL分离和可靠报价缓存；日经真实metadata处理。 |
| `web/backend/src/quant_assistant/providers/index_registry.json` | 16项历史映射及实测依据；版本2026-10-20.3。 |
| `web/backend/src/quant_assistant/providers/index_registry.py` | 解析历史东方财富映射，保持不可变身份约束。 |
| `web/backend/src/quant_assistant/providers/market_calendar.py` | 各市场交易阶段、最近交易日与边界。 |
| `web/backend/src/quant_assistant/providers/market_session_rules.py` | Python读取同一时段资源，不复制另一份小时表。 |
| `web/docs/api.md` | 增量路由、质量、时间字段、历史范围与兼容说明。 |
| `web/docs/refactor/global-index-history-decisions-20261020.md` | 官方证据、全部取舍、失败与修复经过、代价。 |
| `web/frontend/README.md` | 前端使用方式、60秒历史复查与未完成/缺失日线说明。 |
| `web/frontend/scripts/ui-layout-fixtures.mjs` | 只在测试中生成不同ID的不同OHLC，避免串线断言无效。 |
| `web/frontend/scripts/verify-index-strip.mjs` | 已接入/未接入、部分日线、迟到读数及同页版本变化。 |
| `web/frontend/src/components/IndexHistory.vue` | 来源/币种/缺失与未完成提示，60秒复查且保留旧快照快速替换。 |
| `web/frontend/src/components/LiveMarket.vue` | 显示四市场状态及实际后台间隔，保留原页面。 |
| `web/frontend/src/services/dataApi.ts` | 增量行情状态和历史范围/完整性字段。 |
| `web/worker/public_charts.py` | 腾讯、东方财富、Yahoo分别核验真实日OHLC；不造量或补缺日。 |
| `web/worker/public_operations.py` | 实际Worker接受稳定index_id且兼容symbol。 |
| `web/worker/tests/test_global_index_history.py` | 真实适配器契约、坏值/身份/日期、空末行与Worker入口。 |
| `web/worker/tests/test_market_calendar.py` | 更新真实已知日历前提，保留未知年份覆盖。 |
| `web/worker/tests/test_public_charts.py` | 旧四项入口继续使用完整合成身份和OHLC断言。 |

## 3. API、数据结构及兼容性

- 现有 `/market/history` 优先 `index_id`；原四项继续 `{symbol}` 输入和缓存，其余十二项独立 `{index_id}`。不新增历史平行API、不新增数据库迁移。
- 16项历史每项返回准确身份、市场/币种/来源代码、实际日OHLC及范围。日期型 `source_time=null`，日期在 `trade_date/updated_at`；`source_quote_time` 独立。未完成/未核验收盘用 `is_partial/last_bar_partial`，末行缺字段则剔除并返回 `omitted_incomplete_bars`，不填OHLC。成交量单位未核验而省略，缺失不等于0。
- 快照增量 `market_sessions/refresh_active_markets`；旧 `market_session` 仍CN。API历史内存60秒、持久1小时；前端常规60秒及有界旧快照复查。报价活动默认5秒/休市300秒，活动/休市不同键；主行情旧证据14天，不改变基金净值的披露时效政策。
- 自动根据任一活动市场及本市场收盘5分钟收尾窗口；先完成主快照，每轮最多两项历史。辅助排队18、账户总上限20、运行总上限3；主快照领取优先。首次/手动可以从全清单轮换；普通循环只预热活动市场。
- 坏身份、空历史、invalid或未知质量不会留在成功内存缓存，合并重新准备并返回既有503准备协议；供应商拒绝及不支持竞价不会被时间重算洗成成功或覆盖合格报价。

## 4. 每项真实来源与核验

| 内部ID/名称 | 市场/币种 | 报价映射 | 历史映射 | 报价时间字段/时区 | 历史观测核验 |
|---|---|---|---|---|---|
| cn.sse.composite · 上证指数 | CN / CNY | tencent `sh000001` | tencent `sh000001` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| cn.szse.component · 深证成指 | CN / CNY | tencent `sz399001` | tencent `sz399001` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| cn.szse.chinext · 创业板指 | CN / CNY | tencent `sz399006` | tencent `sz399006` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| cn.csi.300 · 沪深300 | CN / CNY | tencent `sh000300` | tencent `sh000300` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| cn.sse.star50 · 科创50 | CN / CNY | tencent `sh000688` | tencent `sh000688` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| cn.sse.return · 上证收益 | CN / CNY | tencent `sh000888` | tencent `sh000888` | 腾讯f30，14位本地时间；Asia/Shanghai | 538条（保存实测响应） |
| cn.sse.50 · 上证50 | CN / CNY | tencent `sh000016` | tencent `sh000016` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| cn.csi.500 · 中证500 | CN / CNY | tencent `sh000905` | tencent `sh000905` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| cn.bse.50 · 北证50 | CN / CNY | tencent `bj899050` | eastmoney `0.899050` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |
| hk.hsi · 恒生指数 | HK / HKD | tencent `hkHSI` | tencent `hkHSI` | 腾讯f30，斜杠本地时间；Asia/Hong_Kong | 640条（保存实测响应） |
| hk.hstech · 恒生科技 | HK / HKD | tencent `hkHSTECH` | tencent `hkHSTECH` | 腾讯f30，斜杠本地时间；Asia/Hong_Kong | 640条（保存实测响应） |
| jp.nikkei225 · 日经225 | JP / JPY | yahoo `^N225` | yahoo `^N225` | Yahoo regularMarketTime，Unix秒；Asia/Tokyo | 485条完整/剔除1条最新缺收盘/stale（在线） |
| us.dowjones · 道琼斯指数 | US / USD | tencent `us.DJI` | tencent `us.DJI` | 腾讯f30，纽约本地时间；America/New_York | 640条（保存实测响应） |
| us.nasdaq.composite · 纳斯达克综合指数 | US / USD | tencent `us.IXIC` | tencent `us.IXIC` | 腾讯f30，纽约本地时间；America/New_York | 640条（保存实测响应） |
| us.sp500 · 标普500 | US / USD | tencent `us.INX` | tencent `us.INX` | 腾讯f30，纽约本地时间；America/New_York | 640条（保存实测响应） |
| cn.csi.1000 · 中证1000 | CN / CNY | tencent `sh000852` | tencent `sh000852` | 腾讯f30，14位本地时间；Asia/Shanghai | 640条（保存实测响应） |

腾讯按实际代码/名称/类型/命名空间核验；北证历史另核验东方财富code/name/market=0，不用腾讯仅一条的历史。Yahoo按现货INDEX/JPY/OSA/东京时区/名称核验，不用期货替代。腾讯及东方财富没提供币种字段时，依据明确的注册定义与已核验证券身份，字段不假称来自供应商。全部请求地址和核验时间在共享注册表，日历/时段官方地址与代价见[全部裁决](global-index-history-decisions-20261020.md)。

保存的实测响应已走正式解析器，未复制到生产夹具。最新日经两年在线实际取得485条完整记录，末日2026-10-19，缺收盘的最新日被剔除，标stale。报价与历史是独立观测：同源metadata点位有效时不以缺失日线close否定该点位；历史也不拿点位补收盘。最后完整报价探测15项valid、日经该次读超时unavailable；美股另一次历史返回HTML被拒绝。单次成功或缓存TTL均不代表持续在线、交易所逐笔时间或已授权实时数据。

## 5. 测试与复核

| 验证 | 实际结果 |
|---|---|
| Linux `test_report.py --start scripts --pattern verify_*.py` | Python **520/520**，零失败/错误/跳过 |
| Linux真实PostgreSQL18/.NET10 | **460/460**，零跳过；旧4键/新12键、美国开市、预留及旧证据实际执行 |
| Linux Worker | **148/148**，零跳过 |
| 原生数据库工具 / 报告计数器 | **15/15**、**5/5**，零跳过 |
| 前端Node / lint / typecheck / format / build | **55/55**；其余均通过，含独立连接模式构建与浏览器 |
| 本地 `scripts/verify.py` | 源码通过；真实运维准入仍未执行/不算通过 |

本机无PostgreSQL，原生集成跳过由上述Linux真实执行补足；本地Python的Linux parent-death专属跳过也在CI实际执行。曾失败的RED、错误构建路径、属性拼写及边界修复分别保存在裁决记录，不把配置失败算作行为RED或测试通过。

最后整体作者复核单独检查了48文件差异及需求：共享资源/打包、逐源身份/日期/数值、缺失末行、竞价、时区与DST、旧键/新键、内存与持久缓存、队列/预算/取消、前端迟到/版本、私有资料边界。此前发现的缓存/质量及旧快照复查问题已带失败证据修复并进入这次CI；没有未解决的阶段C Critical/Important/Minor。没有使用新的子代理。源码静态审查不能替代真实公开源持续监测，供应商拒绝/超时仍保留为运行限制。

## 6. 桌面与手机UI

- 指数栏320/390/1024/1440/1920/2560px；16项、零值/单项失败/未知时间、日经已接入及显式未接入、部分日线、错误ID/无效值、迟到OHLC读数、同页注册表版本切换全部通过。人工查看1440及390截图，原暗色页面与自适应保持。
- 11页面×8宽度（320/390/768/1024/1440/1920/2323/2560）共88项通过；这是本地隔离夹具布局/导航检查。
- 两卡片8状态32项：拆分帧0、最大位移0；K线四宽度8组验证四周期、键盘、缩放、MA/MACD、503准备与旧快照更新、缓存重访。合成冷准备约1.37–1.44秒/缓存重访47–68ms，不冒充生产API性能。
- 浏览器全用明确只读夹具，不把这些点位当成真实市场证据。输出在 `output/playwright/index-strip`、`ui-responsive-20261017/after`、`chart-pair-20261017/after`、`market-kline-20261018`。

## 7. 仍有的范围与限制

阶段D基金多维分类和阶段E板块全链路验收继续实施。中证1000仍暂定。海外仅核验2026，跨年前需更新日历与半日市；现有管理表单不能编辑半日市时段。公开源无持续可用/实时授权承诺，缺失或延迟继续如实显示；未核验成交量不输出。完整代价和所有取舍见裁决，不以“16卡片/接口接通”代替数据核验。

## 8. 边界与交付状态

没有真实交易、生产账本写入、付费模型调用或密钥提交；未合并main、未部署。已通过不缓存的GitHub引用读取验证任务分支为上述源码提交，main仍 `4311614e0f60086048875f97354d2283c22ed31e`。一次上传后的即时引用GET读到旧值；补充无缓存读取与精确head的CI确认上传成功，没有强推或回退。原始带用户修改的桌面工作区未重置。
