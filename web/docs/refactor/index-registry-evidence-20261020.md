# 指数注册表身份核验 — 2026-10-20

范围是《02》阶段 A 的注册表与接口；当前没有改造 Vue 布局、扩展全部历史或部署生产。唯一权威为 `web/backend/src/quant_assistant/providers/index_registry.json`，Python 通过资源加载，.NET Core 嵌入同一文件。前端后续读取 API 的注册定义，不维护另一份标的清单。

原设计 `C:/Users/admin/Desktop/量化/VUE` 及当前仓库仅找到 4 个主卡片与中证1000等次级条目，没有能确认原始第16项的完整清单。按用户需求保留 `cn.csi.1000` 可配置暂定；其行情身份核验通过不能证明它已被原截图确认。

## 实际公开源观察

2026-10-20T12:54:09Z 实测：腾讯15项返回独立代码、名称、类型及来源时间；Yahoo日经返回 `symbol=^N225`、`instrumentType=INDEX`、`currency=JPY`、`exchangeTimezoneName=Asia/Tokyo`。这些是抓取当时的身份观察，不是当前永久实时价格或稳定性承诺。没有把捕获的实际点位写进生产默认值或测试中的实时行情。

| ID | 展示名 | 市场/币种 | 行情 Provider/标识 | 产品确认 | 已核验历史 |
| --- | --- | --- | --- | --- | --- |
| cn.sse.composite | 上证指数 | CN/CNY | tencent/sh000001 | 需求清单确认 | sh000001 |
| cn.szse.component | 深证成指 | CN/CNY | tencent/sz399001 | 需求清单确认 | sz399001 |
| cn.szse.chinext | 创业板指 | CN/CNY | tencent/sz399006 | 需求清单确认 | sz399006 |
| cn.csi.300 | 沪深300 | CN/CNY | tencent/sh000300 | 需求清单确认 | sh000300 |
| cn.sse.star50 | 科创50 | CN/CNY | tencent/sh000688 | 需求清单确认 | 尚未接入 |
| cn.sse.return | 上证收益 | CN/CNY | tencent/sh000888 | 需求清单确认 | 尚未接入 |
| cn.sse.50 | 上证50 | CN/CNY | tencent/sh000016 | 需求清单确认 | 尚未接入 |
| cn.csi.500 | 中证500 | CN/CNY | tencent/sh000905 | 需求清单确认 | 尚未接入 |
| cn.bse.50 | 北证50 | CN/CNY | tencent/bj899050 | 需求清单确认 | 尚未接入 |
| hk.hsi | 恒生指数 | HK/HKD | tencent/hkHSI | 需求清单确认 | 尚未接入 |
| hk.hstech | 恒生科技 | HK/HKD | tencent/hkHSTECH | 需求清单确认 | 尚未接入 |
| jp.nikkei225 | 日经225 | JP/JPY | yahoo/^N225 | 需求清单确认 | 尚未接入 |
| us.dowjones | 道琼斯指数 | US/USD | tencent/us.DJI | 需求清单确认 | 尚未接入 |
| us.nasdaq.composite | 纳斯达克综合指数 | US/USD | tencent/us.IXIC | 需求清单确认 | 尚未接入 |
| us.sp500 | 标普500 | US/USD | tencent/us.INX | 需求清单确认 | 尚未接入 |
| cn.csi.1000 | 中证1000 | CN/CNY | tencent/sh000852 | 暂定 | 尚未接入 |

腾讯响应本身没有币种字段；注册表保留 `response_currency:null`，`currency_basis=registry_definition_and_provider_namespace`，币种按已明确的市场/指数定义标注，绝不冒称由响应提供。US 三指数还以 Yahoo 对应 INDEX/USD/纽约时区元数据交叉核验，未将两源不同时间的数值拼成一个报价。恒生科技的 Yahoo 样本缺币种，不能把该缺项当成 HKD 证据；币种定义依据[恒生公司官方单张](https://www.hsi.com.hk/static/uploads/contents/en/dl_centre/factsheets/hsteche.pdf)及腾讯已核验港股标识。

`000888` 的身份由[上交所发布公告](https://www.sse.com.cn/market/sseindex/diclosure/c/c_20240715_10759849.shtml)确认：上证综合全收益指数，简称上证收益；不是另一个普通上证指数。日经使用[日经公司现金股票指数定义](https://indexes.nikkei.co.jp/en/nkave/index/profile?cid=7&idx=nk225)，Yahoo 实测明确 INDEX/JPY，不能用日经期货代替。纳斯达克选 `us.IXIC` 对应 NASDAQ Composite，不是纳斯达克100。

四条原历史源另于 2026-10-20 实测各640条真实交易日记录，范围2024-02-28至2026-10-20，返回 `qt` 标识/名称与请求一致。其余12项暂不声明历史能力，等待阶段C独立接入；有行情不等于已有合格OHLC。旧四 `symbol` 将继续映射原缓存输入。

日经 Yahoo 多日 `chartPreviousClose` 是整个区间之前的收盘，不等于上一交易日；适配器现使用相邻真实日观测，并与 regularMarketTime 的本地日期对齐。来源时间、接收时间、缓存读取时间分别保留；海外日历未核验时保持 unknown，不套中国日历。

2026-10-20T13:27:32Z（纽约09:27:32）腾讯美股三项返回09:30:00未来时间，质量校验拒绝该样本；同期日经429被标为不可用。没有放宽未来时间规则或绕过限流。13:32:32Z自然开盘后重新观察，两个供应商均返回，得到16个真实报价与16个状态，其中A股10项时效通过，海外6项交易日历仍未核验，明确为 `unknown_time/calendar_or_session_unavailable`。这一观察不构成所有时段、所有公开接口稳定实时的保证。

## 逐项报价与接口实现

- 腾讯注册15项批量请求，Yahoo日经独立请求，最多2个供应商并发；复用 PublicHttp 的超时、大小限制、重定向禁止与熔断。检查准确代码/类型/名称/命名空间、有限正点位、涨跌一致性、时区时间；同一来源数值一起使用，不拼接不同来源的tick。
- 按稳定ID保留各自最近有效报价。失败只影响该项：保留原source_time/fetched_at并标记回退、过期；重新正常响应可恢复。空失败批次不记为成功，重复/乱序/额外行不能串ID；缺失时间为null。
- Python响应与原生读取层保留16项定义和状态；旧快照按注册身份校验后投影，冲突行不赋予错误的权威ID。读取不把回退行重新标为新鲜。股票000001与上证指数000001使用不同身份键。
- 历史接口新增稳定index_id并兼容原四symbol，使用同一旧缓存输入；未接通12项返回明确空能力，不排队、不借用曲线。历史与辅助刷新清单由注册能力派生，无新增数据库迁移或平行接口。
- 此阶段仅为Vue补充类型与缺失时间提示；16卡片、动态颜色、移除假迷你图、注册表图表选择器仍由阶段B实现。

报价提交16ec70c：[Linux 39431683614](https://github.com/Lemondick/quant-to-riches/actions/runs/39431683614) 四job成功，Python511/.NETPG389/Worker132/工具15/报告5全部通过、0跳过，前端类型/浏览器/构建成功。接口新增测试及最终全阶段审查结果另行追加，不将报价提交门禁当成后续提交已验证。

## 注册表专项

先 RED：未知指数被推断到上海、北证50误用 `sh899050`；新注册表模块与.NET类型缺失。修复后 Python6/6、.NET4/4通过；旧懒加载2/2通过，source verify/ruff/format/secret scan通过。实际 wheel 包含同一16项JSON与读取器，.NET测试确认嵌入资源；投影视图不能修改存储定义。完整阶段仍需后续逐项报价、API兼容及最终 Linux门禁，不把本专项当作16项接口已经完成。

接口源提交e544287：[Linux39433164115](https://github.com/Lemondick/quant-to-riches/actions/runs/39433164115) Python511/.NET真实PG404/Worker134/工具15/报告5全部通过，0跳过，四job与前端/浏览器/构建成功。PG测试确认原四个ID与旧symbol共用原缓存输入且只产生4个原始任务。
