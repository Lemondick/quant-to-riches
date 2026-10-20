# 观澜 · Vue 工作台

当前前端完整采用用户提供的 `量化/VUE` 工程：Vue 3、TypeScript、Vue Router、ECharts、KLineChart 和 lucide。原 Uni-App 页面、临时样式适配层及其预览和验证脚本已退出活动目录。

页面保留提供工程的主题与路由，采用本机字体回退，避免外部字体阻塞首屏。11 个页面已接入原生后端，并保留显式本地演示模式。`src/` 和 `index.html` 不参与批量自动格式化，避免无关改动。

## 本地运行

需要 Node.js 22.18 或更高版本。

```powershell
cd web/frontend
npm ci
npm run dev
```

打开 http://127.0.0.1:57450 。开发模式和构建预览共用这个端口，一次运行其中一个。

```powershell
npm run build
npm run preview:ui
```

也可在仓库根目录运行 `./启动网页版.ps1 -PreviewUI`。原来的 `-Release` 参数继续用于启动完整原生服务。

## 页面与代码

| 目录 | 内容 |
| --- | --- |
| `src/views/` | 资产总览、组合分析、交易账本、自选、行情、AI 选股、基金复核、量化策略、资讯、后台、设置，共 11 个页面 |
| `src/components/` | 导航、页头、统计卡、图表、表格、筛选栏、弹窗和模型设置 |
| `src/composables/` | 会话核验、SWR 数据加载、浏览器偏好和演示状态 |
| `src/data/` | 基金、研究和策略演示样本 |
| `src/services/` | 原生 API、按会话隔离的有界内存缓存及模型配置接口 |
| `src/utils/` | 图表、表格、金额、账户计算及配置校验 |
| `src/styles/theme.css` | 提供的 Vue 工程主题与本机字体回退 |
| `src/styles/responsive.css` | 实际卡片数量对应的响应式列数及切页内容立即显示规则 |
| `tests/` | 计算、筛选、模型配置与数据校验测试 |
| `scripts/` | 原生 Vue 启动、原子构建发布和浏览器验收 |

Vue Router 沿用提供工程的原始路由，使用 `/market`、`/admin`、`/settings` 等正常路径。请使用新版页面地址。静态托管需将页面路径回退至 `index.html`。

统计区按实际卡片数量铺满可用宽度，窄屏自动换行；不再用宽屏固定四列给三张卡留出空位。保留原主题和悬停反馈，页面内容不再重播带延迟的入场动画，图表首次绘制直接显示。路由代码在空闲时最多两路预加载，指针移入、聚焦或按下导航时也可提前加载；省流量或 2G 环境不启动空闲预加载。预加载仅下载代码，不挂载页面、不请求账户数据、不启动研究任务；真实会话仍在页面挂载后核验。

图表卡片外框与相邻卡片同步挂载，ECharts 绘图引擎由 `utils/lineChart.ts` 单独异步加载；加载期间保留绘图区和一行图例空间，历史资料到达后不会把旁边卡片挤走。离开页面后取消初始化并释放已创建图表。首页已有数据时使用原有下方更新提示，不再在标题下插入重复刷新提示造成整页跳动。

指数和基金详情使用 `CandleChartCard.vue` 与 `CandlePlot.vue`，保留现有卡片主题，提供同花顺风格的日、周、月、年 K 线、红涨绿跌、MA5/10/20/30、MACD12/26/9、十字线、缩放和拖动。绘图使用固定版本 KLineChart 10.0.3，引擎在卡片挂载时与数据并行加载，保留空间避免布局跳动；许可与 NOTICE 随静态资源发布。接口按[KLineChart 官方数据接入约定](https://klinecharts.com/en-US/guide/data-integration)设置加载器、标的及周期。

`/market/history?symbol=000300` 等接口读取四个主指数的真实交易日 OHLC，后台独立准备，缓存一小时，最多保留七日旧观察并明确标记。基金继续读取 `/funds/{symbol}/detail` 的官方净值：单日净值为平线，周月 K 按实际净值的首、末、最高、最低聚合，不补造分时价格、停牌日期或成交量。行情榜上的聚焦和指针移入最多两路提前准备基金公开资料，省流量 / 2G 环境关闭这一预取。

基金的上述聚合仅表示公开净值观测区间，不是证券交易所成交 OHLC，也不能表示基金盘中实时价格；《02》的指数扩展与净值展示需求尚待后续阶段。当前指数历史支持范围仍按实际接口能力显示。

仅 `public_snapshot_unavailable` 的 503 在一次公开读取中按有界短间隔等待，不闪红色错误；真实鉴权和数据库错误仍明确显示。指数及基金接口返回的 `stale:true` 快照立即展示，并在后台每 1–3 秒复核、最多 20 次，离开页面或切换标的时停止定时复核；新快照到达后及时更新，避免整段缓存期都显示更新中。其他账户资源默认不启用这一轮询。

浏览器复核读取已有服务端快照。上游刷新由服务端单一调度器控制：每轮完成后默认等待 30 秒，可配置 15–300 秒；整体预算 75 秒、任务预算 55 秒。失败保留原时间与 stale 状态；配置与完整迁移 `0001`–`0023` 见[原生架构](../backend-dotnet/ARCHITECTURE.md)。生产入口为 .NET API，不启动旧 FastAPI 或容器适配层。

## 数据与接口边界

`VITE_API_BASE_URL` 留空时沿用原有显式演示模式，演示操作保存在本浏览器。设置 `/api/v1` 后重新构建，通过页面和 `Live*` 数据组件读取真实接口；连接模式保持所提供 Vue 工程的布局、主题、统计卡、图表分栏、筛选栏、弹窗及设置菜单，接接口时不能另换一组简化界面。图表按需加载，只绘制真实净值历史，缺少历史时保留空图。资产总览和自选通过真实登录读取 `/auth/session` 与 `/dashboard/summary`，行情、资讯和基金详情读取公开快照；组合读取确认净值历史，账本、自选、账户、后台和策略使用实际业务接口。研究页列出账户研究计划及任务，缺失证据时保持阻断状态。原生发布脚本默认启用 `/api/v1`；纯前端开发仍默认演示。

2026-10-20 指数栏由接口 `index_registry/index_statuses` 驱动，16项使用稳定ID匹配，桌面同时显示4–6张并可横向浏览，手机使用滚动吸附，不挤成16列或产生页面级横向溢出。每项独立显示点位、涨跌点数/比例、市场/币种、原始来源时间和质量；零涨跌为中性。无效/缺失项目显示 `—`，其余正常保留。海外来源时间按对应市场时区显示，缺失或非法时间不会用接收时间代替；公开源延迟时长未知。中证1000保留暂定标签。固定迷你柱形图已移除，没有用装饰图冒充真实走势。

历史选择器读取同一注册定义，以 `index_id` 请求并校验响应ID；切换期间不保留另一个指数的曲线。未接通历史明确显示“历史行情尚未接入”；当前历史接入仍为原四项，其余逐项验证属于后续阶段。基金前十榜、搜索、详情/关注与行业/概念/地域板块仍独立。

`node scripts/verify-index-strip.mjs` 必须指向 `/api/v1` 连接模式构建：`QUANT_WEB_OUTPUT_DIR` 为构建根目录（脚本读取其中 `build/`）。它用明确标注的本地合成数据拦截只读请求，验证16项、6个宽度、独立状态、键盘滚动、来源时间、错误ID和迟到响应；不访问真实账户、不发布合成点位。结果在 `output/playwright/index-strip`。CI保留原演示界面门禁，另构建连接模式并执行该检查，两者均须成功；测试脚本从后端权威JSON生成夹具，生产浏览器只从API读取注册表。

已连接页面遇到缺失证据会显示不可用或明确的旧快照，首次公开资料准备中以有界退避重试。切页复用同一聚合请求，后台更新期间保留有效内容。进入页面先核验真实会话；私有读写携带 `X-Session-Scope`，写入另携带 CSRF，后端拒绝与当前会话不匹配的请求。业务提交后使私有缓存变旧并立即刷新已挂载资源，期间保留有效数据；换号、登出或鉴权失败则清空缓存。不将账户响应或密钥存入 localStorage。

真实设置页使用 `/advisor/model-settings`、`/advisor/models/discover` 和 `/advisor/jev-models/discover`，按后端版本和角色保存模型选择。Key 仅在输入内存和当前提交中使用，提交后清空；后端返回是否已保存及模型清单，不返回明文。`docs/model-config-api.md` 的旧适配约定仅用于离线演示，不能直接等同于真实 API。

## 验证与产物

```powershell
npm test
npm run lint
npm run format:check
npm run typecheck
npm run build
npm run verify:ui
```

构建先通过 TypeScript 检查，再暂存、校验资源并原子替换 `../../data/runtime/web/build/`。失败时保留上一版可用构建。`QUANT_WEB_OUTPUT_DIR` 可以指定独立产物根目录，最终文件位于该目录的 `build/`。

浏览器验收使用自有隔离浏览器和临时 HTTP 端口，覆盖 11 个页面、刷新直达、窄屏布局、持仓录入、账本联动、基金关注及偏好保存。结果默认写入仓库的 `output/playwright/vue-native-20261017/`，不修改日常浏览器数据。

`npm run verify:live` 使用本机隔离 PostgreSQL `quant_perf_ui`（端口 55439）、已构建的 Release API 和 `tools/tests/seed_performance_ui.py` 的明确合成夹具。该夹具工具拒绝其他账户存在的数据库，连接目标固定为本机测试库，禁止用于业务数据。测试覆盖真实登录、聚合去重、刷新保留内容、移动布局、换号及登出隔离、自选增删、十进制账本写入与重试幂等、管理员状态和审计、会话及模型配置读取；策略保存和预览运行真实 Python Worker，仅声明本地策略能力，不调用付费模型。

连接构建默认读取 `output/performance-connected-build/build`，可用 `QUANT_LIVE_UI_BUILD` 指向其他产物。静态文件与 API 直接由临时 Kestrel 提供，不包含 Caddy、HTTPS 或生产网络。测试分别保留 Node fetch 与固定 50 个复用 HTTP 连接的并发耗时、服务端分段和持续线程池采样；冷首屏使用空浏览器缓存、现有登录 Cookie，记录真实账户行渲染后两帧的时间。`QUANT_DOTNET` 可指定 .NET 运行时路径；结果在 `output/playwright/performance-live-20261017/`。压测应在全量测试与编译完成后独立运行。

`node scripts/verify-restored-ui.mjs` 将同级 `VUE/` 原版与 `output/ui-restore-20261017/connected/build` 的连接构建并排验收，覆盖 11 页的桌面和手机布局、外壳尺寸与主题、行情标签、模型设置和账本弹窗。布局检查使用明确的本地夹具，真实 API 写入另由上述隔离库工作流验证；夹具不会进入生产构建。截图及结果位于 `output/playwright/ui-restored-20261017/`，正式切换记录见 `../docs/refactor/ui-restoration-live-evidence-20261017.json`。

`node scripts/verify-layout-navigation.mjs` 使用正常动效，覆盖 11 页在 320、390、768、1024、1440、1920、2323、2560px 的 88 项布局，以及空账户和仅有三条指数时的宽屏填充。切页测量从真实点击开始，等待目标标题及主要内容的祖先透明度恢复，再跨过两帧；同时验证空闲代码预加载不增加业务请求。默认读取 `output/ui-responsive-v12/build`，可用 `QUANT_UI_CHECK_BUILD` 覆盖；`QUANT_UI_CHECK_STAGE=before` 用于记录旧构建基线。比较必须使用相同的只读本地夹具和 100ms 模块请求延迟，不能将结果当作公网 API 或已登录首屏时延。结果在 `output/playwright/ui-responsive-20261017/`，上线证据见 `../docs/refactor/ui-responsive-live-evidence-20261017.json`。

`node scripts/verify-chart-pair.mjs` 逐帧检查首页两张图表卡片，使用正常动效、650ms 图表模块延迟和 180ms 历史数据延迟。覆盖 320、390、1440、2323px 的空账户及非空账户，分别检查首次进入、刷新数据、返回首页和整页刷新，共 32 组；要求两卡没有先后出现、没有共同消失、尺寸与位置保持稳定。默认读取 `output/ui-chart-pair-v13/build`，可用 `QUANT_CHART_BUILD` 和 `QUANT_CHART_STAGE` 指定构建与基线。只读夹具不会进入生产构建，结果在 `output/playwright/chart-pair-20261017/`。

`node scripts/verify-market-kline.mjs` 检查 `output/kline-v14/ui/build`，覆盖四个宽度下的实际 OHLC、净值聚合、四周期、键盘、缩放、指标及指数切换；初次 503 不产生假错误，旧快照在后台更新后及时替换，缓存重复打开不等待重新抓取。隔离样本与结果位于 `output/playwright/market-kline-20261018/`；另以服务器返回的真实公开历史重放验证线上静态资源，边界与上线记录见 `../docs/refactor/market-kline-live-evidence-20261018.json`。

`node scripts/verify-market-alignment.mjs` 检查行情页 K 线与右侧卡片的上下边界，覆盖 320–2560px 的十个宽度、日K与周K，并在单次挂载后连续调整窗口。宽屏中行业卡片填满剩余高度，1200px 及以下自然堆叠。默认读取 `output/market-alignment-v15/build`；可用 `QUANT_ALIGNMENT_BUILD`、`QUANT_ALIGNMENT_STAGE` 指定构建和阶段，`QUANT_ALIGNMENT_URL` 可核验线上静态资源。使用上述历史公开数据的只读重放及隔离账户夹具，不访问生产账户 API；结果在 `output/playwright/market-alignment-20261018/`。

行情快照的可选 `breadth` 字段提供两市上涨、下跌、平盘及涨跌停家数；卡片显示原始统计日期，成交额采用同日期沪深指数口径合计，“较前日成交比”为该金额除以前一交易日金额，不称为盘中量比。广度缺失不影响指数或基金，真实 0 值正常显示；金额源缺项时保留家数并显示金额缺项。Worker 在 `QUANT_SITE_ROOT/public-data-cache/market-turnover.json` 原子保存已经结束的交易日金额，只使用与盘面日期匹配的记录，当前交易日仍重新读取上游。

`node scripts/verify-market-breadth.mjs` 默认检查 `output/breadth-v16/ui/build`，覆盖 320、390、1440、2560px 下的完整、零值、部分缺项、旧统计和不可用状态，共 20 组，包含现有左右对齐和横向溢出检查。可用 `QUANT_BREADTH_BUILD`、`QUANT_BREADTH_STAGE` 指定构建和阶段；`QUANT_BREADTH_URL` 配合 `QUANT_BREADTH_DATA` 将真实接口的公开行情响应重放到线上实际静态资源。账户请求仅使用只读夹具；结果在 `output/playwright/market-breadth-20261018/`。
