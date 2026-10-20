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

日经 Yahoo 多日 `chartPreviousClose` 是整个区间之前的收盘，不等于上一交易日；后续适配器须使用相邻真实日观测，并与 regularMarketTime 的本地日期对齐。来源时间、接收时间、缓存读取时间分别保留；海外日历未核验时保持 unknown，不套中国日历。

## 注册表专项

先 RED：未知指数被推断到上海、北证50误用 `sh899050`；新注册表模块与.NET类型缺失。修复后 Python6/6、.NET4/4通过；旧懒加载2/2通过，source verify/ruff/format/secret scan通过。实际 wheel 包含同一16项JSON与读取器，.NET测试确认嵌入资源；投影视图不能修改存储定义。完整阶段仍需后续逐项报价、API兼容及最终 Linux门禁，不把本专项当作16项接口已经完成。
