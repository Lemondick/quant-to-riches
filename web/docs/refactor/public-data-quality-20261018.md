# 公开行情质量修复与验收（2026-10-18）

本次执行用户附件中的数据质量要求。前端可见修改限于既有模块标题附近的“更新时间”；未修改样式块、路由、页面结构、表格/图表交互和组件库。后端数值语义修正会使不可信数字变为空值。

## 发现与修复

| 原问题 | 修复后行为 |
| --- | --- |
| 正式净值进入 `estimate_nav`，参与今日估算收益 | `estimate_nav/change_rate/change_amount` 为 null；正式值进入 `official_nav/official_change_rate`，日期进入 `nav_date/updated_at` |
| 腾讯缺少行情时间时用获取时间补齐 | 不接受该行作为有效主源报价；尝试同证券新浪备用。无法验证时不可用/未知，不填当前时间 |
| 沪市 ETF 510/588 被映射到 sz | 沪市 5 开头证券请求 sh；159 ETF 请求 sz。保留既有 stock/index API 类型并新增 `instrument_type=etf` |
| 腾讯基金字段 6 被当作前日单位净值 | 实测为累计净值；单点只返回实际净值日期、单位/累计净值、源日涨幅，不反推前日净值 |
| 单位净值直接相除生成所有区间收益，单点产生 0% | 给出真实起止日期、方法和质量；覆盖不足、缺交易日、未知日历缺口、异常跳变、分红/拆分条款无法核实的区间收益为 null |
| 不同板块来源可能被误报为同一容灾源 | 厂商与 industry/concept/region 分开，ID 为 `source:category:code`；按源展示，不按名称合并或混排 |
| 无界/并发重复内存读取、目录随行情重复请求 | 有界 LRU/同键单飞；分类 24h 与报价 15s 分开；主源失败保留原值与原时间 |
| 可选资料瞬时失败的空结果可能缓存 24h | 失败空值不进入成功缓存；已有值可保留并标为字段过期，没有值时保持空，外层详情 5min 后重试 |
| 一条新浪备用坏记录丢弃整批 | 按行验证与隔离，保留同批其他合格证券 |

旧持久快照读取也做适配。新前端继续访问原路径并带 `separateNav=true`；无该参数的旧标签页会省略只有正式净值的估值项并放入 unavailable，避免旧代码把 null 转为 0。不会为了向后兼容而重新把正式净值装回估值字段。

## 时间和数据质量契约

- `source_time` 只接受可信、带时区的上游时刻。正式净值、历史 K 线只有日期时，`source_time=null`，`updated_at` 保留 YYYY-MM-DD 精度。
- `fetched_at` 是成功接收数据的时间；资料子缓存保存自己的获取时间。根 `observed_at` 在旧接口可能是采集时间，不能作为新更新时间。
- `cache_read_at` 单独记录读取时刻；缓存读取不改变 `updated_at/source_time/trade_date/fetched_at`。
- `data_type` 分为 realtime_quote、intraday_estimate、official_nav、historical_quote、fund_profile；同时给出 source_family、instrument_type、is_estimate、is_stale、fallback_used/reason、quality_status。
- 未知时间显示“更新时间：未知”。日期不补成 00:00:00，估值分钟精度不增加秒数。
- 2026 年沪深 A 股日历沿用现有网关 SSE 休市回退表；周末和节假日保留最近交易日收盘值，不因自然时间经过而判失效。非 2026 年交易时效判定为未知，历史工作日日期缺口保守阻断收益验证。此回退表需要随年度维护，不能宣称覆盖未来所有年份。
- 股价超过当前场景的涨跌限制并未自动截断；价格与前收/源涨跌幅不一致、非有限值、身份不符、未来时间按不合格行处理。精确同一时刻且同指标的两源价格差超过 0.5% 记冲突，不取平均；不同时刻不作为同一 tick 比较。

## 实际来源、健康与依赖

原始 HTTP 元数据和可解析公开响应保存于 `output/data-quality-v18/data-quality-v18-probe/`。首轮 35 个请求，后续仅针对 Content-Type 误标的合法文本接口做定点验证，再补 3 类板块与广度样本；不是压力测试。所有请求均禁止重定向，未绕过登录、验证码或访问限制。

| 模块/接口 | 实测和使用方式 | 来源家族与独立性 |
| --- | --- | --- |
| 腾讯 `qt.gtimg.cn/q=` | 10 个指数/ETF 可解析，code 字段与 wrapper 校验，包含可信行情时刻 | tencent；独立的传输入口，不保证它与其他门户底层交易所数据采购独立 |
| 新浪 `hq.sinajs.cn/list=` | 同 10 个代码完整行情文本可解析，有日期/时间；用作通过相同验证的备用 | sina；与腾讯入口独立 |
| 东方财富 `push2.../ulist.np/get` | HTTP 302，未跟随；本次不启用该候选备用 | eastmoney；不当作可用容灾 |
| 东方财富 `push2.../clist/get` | HTTP 502；保留已验证目录，报价空值，不伪造涨跌 | eastmoney |
| 东方财富 `sidemenu_new.json` | 1044 条分类，497 行业（含多级）、516 概念、31 地域；仅分类含义 | eastmoney |
| 新浪 newSinaHy/newFLJK | 49 行业、175 概念、31 地域，原始涨幅与成交额；源不提供行情时间 | sina；`updated_at=null`，延迟未知。不是东方财富板块指数的等价替代 |
| 天天基金 fundgz JSONP | 5 只基金全部返回 HTML；失败估值 null，不认为 HTTP200 就有效 | eastmoney；天天基金和 AKShare 的 EM 适配均非新增独立源 |
| EM pingzhongdata | 股票、混合、债券、QDII 4 类有正式净值历史；`fS_code` 校验。货币基金没有普通单位净值序列 | eastmoney；单位/累计净值独立 |
| 腾讯 jj 基金报价 | 4 只与 EM 最新单位净值及日期一致；货币基金为零值且日期空，判不可用 | tencent；只校验正式净值，不当作估值 |
| Danjuan 基础资料 | 4 只有效且 `fd_code` 一致，货币基金无资料，切新浪 | danjuan；持仓/经理等扩展接口未回显代码，元数据标明 request_bound_no_echo，不宣称已验证代码回显 |
| 新浪基金资料 | 5 只均有 symbol 身份字段；按相同语义字段补齐 | sina；资料更新时间无可信总时间，显示未知 |
| EM 费率 HTML | 000001 标题含基金代码、费率表可解析；保留条件、百分数/固定金额、渠道折扣含义 | eastmoney；申赎/费率抓取日期独立记录，未宣称实时 |
| EM rankhandler | 请求 10 条指数型基金日涨幅前十，真实 wrapper/字段验证 | eastmoney；替代原 AKShare 30000 条且未设置超时的请求，非新增数据源 |
| 腾讯指数历史 | 真实交易日期和 OHLC 校验，最大 640 根；无伪造盘中 K 线 | tencent |
| 乐咕乐股广度 | 解析真实发布时间与涨跌家数；成交额匹配同日沪深指数公开日线，不跨日期拼接 | legulegu + eastmoney；各字段来源不同，不伪装为一个原子实时快照 |

`data.diff` 对象形式没有本次可用的真实上游样本，所以未按猜测启用对象解析；现有数组解析有本地覆盖，EM 当前失败被如实暴露。这项限制不以虚构格式“完成”。

## 单位和收益方法

| 字段 | 统一含义 |
| --- | --- |
| change_rate / change_pct / gszzl / f3 | 1.25 代表 1.25%，不是比例 0.0125 |
| 腾讯 ETF volume | 手数 ×100 为份；手数有整手精度，不宣称与逐份总量完全相等 |
| 腾讯 turnover | 实测字段57 ×10000，单位元；字段37的万元整数舍入更粗，不选它 |
| 新浪股票/ETF volume、turnover | 份/股、人民币元；指数成交量因源跨交易所口径不同保留未知 |
| 新浪板块成交额字段7 | 人民币元；涨幅为该源板块平均涨幅，不能混作 EM 板块指数收益 |
| 腾讯 jj 字段5/6/7/8 | 单位净值/累计净值/正式日涨幅/净值日期 |
| 历史净值 | 官方单位净值、累计净值、源日收益分开；估值不插入历史 |
| 本地验证区间收益 | 仅完整覆盖且无未验证公司行动的区间计算单位净值比；返回 start_date/end_date/methodology。缺分红金额、拆分因子或源收益与净值比不一致则该区间 null，不声称复权收益 |

## 自动验证与真实验收

- Worker 全套 105 项；新增质量矩阵含重定向、403/429/500、连接/超时、空/HTML、坏格式、身份、有限数、百分比、主源过期/备用新鲜、两源过期、同钟冲突、单飞/容量/原时间、周末/节假日、正式净值分离、null、重复/缺日与未知日历。
- 原估值回退 9 项、资料契约 2 项另行执行；语义断言已更新为正式净值独立字段。
- 前端 43 项、类型检查、lint、生产构建。C# 选定 24 项通过，19 项依赖隔离 PostgreSQL 的测试跳过，不能记为通过；生产只读核验另记录。
- 390/1440 宽度 × 指数、基金榜、板块、资产总览、基金复核（含详情）、自选共 12 组截图；移除新增时间文字并清除旧“观测时间”标题文字后，卡片几何一致且无横向页面溢出。所有基线 Vue 样式块保持逐字相同。截图有浏览器绘制/动画栅格差异，不宣称所有截图逐像素 0 差异。
- `sample-acceptance.json` 完整记录 5 指数、5ETF、5 板块、5 基金。4 对正式净值日期/数值相同，货币基金如实不可用。指数与 ETF 门户时刻不同，价格舍入相符仅是数值核对，不能证明同 tick；板块未知时间不能做跨源同钟验证。
- 当前是周日 2026-10-18，不能据此证明周一开市后的实盘时效 SLA，公开接口没有授权源服务保证。

## 资源边界

不新增数据库、迁移或常驻服务，不保存逐秒行情历史。保留既有 Postgres 公共快照和每日收盘缓存；板块采集不再隐式写每轮文件缓存。缓存容量：行情128、分类1、详情8、NAV4、资料子项64、HTTP源健康64、估值既有512。行情本地5s、板块15s、估值60s、基本资料/费率24h、NAV12h（只有最新单点则60s）、历史K线既有1h；后台公共行情采集仍30s，前端可见页检查15s，非交易自动采集已有暂停策略。TTL 是节流参数，不是供应商实时承诺。

HTTP 进程并发上限4，等待槽位最多2s；连接/读取均限时，响应最大8MiB，瞬时故障最多重试1次并退避0.2s；403/429不在请求内重试，失败熔断60s。三个 Worker 各有独立内存，最大并发不是集群全局4。资料与净值备用源路由也记录 fallback_used/reason；公共日志只含固定模块、host、耗时、状态/错误类型、计数、时刻、缓存与回退；输出到既有 journald，使用系统轮转，不创建无界新日志文件。API 既有 Serilog 文件轮转保持。

## 修改文件与发布证据

Python：providers/data_quality.py、public_http.py、fund_valuation.py、market_feed.py、market_sectors.py、fund_detail.py；controllers/schema_common.py、schemas.py、fund_schemas.py；services/api_queries/views.py、local_portfolio.py；services/fund_valuation.py、valuation_alerts.py；Worker public_operations.py、public_charts.py。

.NET：新增 PublicDataQuality.cs；PublicMarketSnapshot.cs、DashboardSummaryService.cs、MarketController.cs、DashboardController.cs 只读适配。

前端：utils/dataQuality.ts、services/dataApi.ts/fundData.ts、useDashboardSummary.ts/usePortfolioPresentation.ts；既有 LiveMarket/LiveAssetOverview/LiveFundReview/LiveFundDetail/LiveWatchlist、IndexHistory、CandleChartCard、ChartCard、StatCard 的时间文字和空值计算。路由与 CSS 未改。

基线、请求健康、规范化前后样本、测试输出、审查失败到通过、截图与发布包哈希位于 `output/data-quality-v18/`。发布流程克隆 v17-1 为 quality-v18，先预检再在既有备份锁下完整备份、核对财务指纹，切换后检查服务与带认证 API；生产核验结果写入同目录。不会在文档或日志中保存会话 Token、Cookie、密码。


2026-10-18 首次线上核验通过：quality-v18 的认证行情接口本机10次读取中位数34.91ms、最慢47.15ms；服务器访问公网域名157.68ms。7指数、5ETF、1299条板块、1只正式净值分离样本验证通过，临时会话已撤销，9张财务表及用户指纹未变。随后 quality-v18-1 仅补齐资料与净值备用路由标记；发布包及最终核验数据以 `quality-v18-1-*` 为准。


最终发布：`20261018-quality-v18-1`，UTC `2026-10-18T05:15:26Z`（北京时间 2026-10-18 13:15:26；未把该部署时刻当行情时刻）。包 SHA256 `ae2fd9b7b16ba10fe5e364fbd01d3c0609bf726aaa5e51f9eacb6319804cc9c2`。新版本已完成带认证 API、11个页面、静态文件哈希和旧资源保留验证；临时验证会话撤销。

最终服务器本机行情读取10次中位数 33.18ms，样本P95/最大 47.44ms；服务器访问公网域名单次 120.3ms，均非国内用户端延迟。解压后 JSON 789136字节，公网 gzip；没有 Content-Length，未声称准确压缩线速大小。API及3个Worker系统cgroup内存合计约 491.6MiB；未采受控旧版基线，不声称RSS降低。journald现有归档约2.2GiB，采用系统默认轮转，未修改全局日志配额。磁盘剩余约 102.1GiB。

本轮保守决定：保留用户现有未提交工作与基线，不迁移或清理工作区；未实测的EM diff对象形态不启用；未知年份缺口不生成区间收益；旧客户端省略仅正式净值估值项。这些决定分别避免丢失本地工作、误读响应、虚构收益和假0，但会保留旧版本/暂缺部分格式支持/暂缺部分收益/使旧标签页显示不可用。没有延期的代码审查小项。
