# 前端 Bundle 体积分析报告（batch4 流 B #9）

日期：2026-10-21　分支：`fix/2026-10-21-legacy-b`　分析人：流 B

## 1. 测量方法

- 构建命令：`BUNDLE_ANALYZE=1 VITE_API_BASE_URL=/api/v1 npx vite build`
  （`VITE_API_BASE_URL=/api/v1` 与生产发布脚本 `web/backend-dotnet/deploy/publish.sh` 保持一致；
  不带该变量构建的是离线演示模式，路由走另一套 `views/*`，数字不可比。）
- 分析工具：`rollup-plugin-visualizer`（devDependency，仅 `BUNDLE_ANALYZE=1` 时输出，
  不影响正常构建），产物 `docs/bundle-stats.html`（treemap）与 `docs/bundle-stats.json`（raw-data）。
- “首屏 JS”口径：`assets/index-*.js`（entry chunk，首屏 HTML 引用的唯一同步 JS）。

## 2. Before / After（生产配置）

| 指标 | Before（基线） | After（本批） | 变化 |
|---|---|---|---|
| 首屏 JS（minified） | 206,808 B（202.0 KiB） | 139,773 B（136.5 KiB） | **-67,035 B（-32.4%）** |
| 首屏 JS（gzip） | 76,333 B（74.6 KiB） | 52,614 B（51.4 KiB） | **-23,719 B（-31.1%）** |
| 首页路由 chunk | 无（打进首屏） | `LiveAssetOverview-*.js` 21,162 B，按需加载 | 移出首屏 |
| 问答浮窗 chunk | 无（打进首屏） | `AskWidget-*.js` 4,271 B，`defineAsyncComponent` 异步加载 | 移出首屏 |
| dist 总体积 | 1,836,663 B | 1,855,411 B | +1.0%（chunk 切分固定开销，见 §5） |

> 结论：首屏 JS -32.4%（gzip -31.1%）。“减半”目标未完全达成——剩余首屏中
> Vue 运行时（runtime-core/reactivity/runtime-dom/shared）+ vue-router 约占 85%，
> 为框架不可削减部分；应用自身代码已全部移出首屏（见 §4）。

## 3. 主要 chunk 体积（After，minified / gzip）

| Chunk | 体积 | 说明 |
|---|---|---|
| `charts-*.js` | 227.5 KiB / 74.5 KiB | echarts 图表（路由按需） |
| `Axis-*.js` | 225.1 KiB / 77.4 KiB | echarts 坐标轴（路由按需） |
| `components-*.js` | 196.3 KiB / 62.4 KiB | echarts 组件（路由按需） |
| `graphic-*.js` | 146.8 KiB / 49.5 KiB | echarts 图形（路由按需） |
| `index-*.js` | **136.5 KiB / 51.4 KiB** | **首屏 entry** |
| `customGraphicKeyframeAnimation-*.js` | 88.9 KiB / 29.8 KiB | echarts（路由按需） |
| `LiveApiConsole-*.js` | 66.3 KiB / 8.5 KiB | API 控制台页（路由按需） |
| `Market-*.js` | 30.3 KiB / 9.7 KiB | 行情页内含 klinecharts（路由按需） |
| `LiveAssetOverview-*.js` | 21.2 KiB | 首页（路由按需，本批新拆出） |
| `AskWidget-*.js` | 4.3 KiB | 问答浮窗（异步组件，本批新拆出） |

## 4. Top 10 大模块（rendered bytes，全量 chunk 去重后）

| # | 模块 | 体积（rendered） | 所在 chunk |
|---|---|---|---|
| 1 | `node_modules/klinecharts/dist/index.esm.js` | 509,133 | 行情页（按需） |
| 2 | `@vue/runtime-core/dist/runtime-core.esm-bundler.js` | 133,656 | 首屏 entry（框架，不可减） |
| 3 | `src/api/route-snapshot.json` | 67,851 | API 控制台页（按需） |
| 4 | `node_modules/echarts/dist/echarts.js` | 61,834 | 图表 chunks（按需） |
| 5 | `@vue/reactivity/dist/reactivity.esm-bundler.js` | 44,557 | 首屏 entry（框架，不可减） |
| 6 | `node_modules/vue-router/dist/vue-router.mjs` | 44,474 | 首屏 entry（框架，不可减） |
| 7 | echarts `LineView.js` | 33,927 | 图表 chunks（按需） |
| 8 | `src/components/LiveMarket.vue`（script） | 32,248 | 行情页（按需） |
| 9 | echarts `TooltipView.js` | 31,444 | 图表 chunks（按需） |
| 10 | echarts `Element.js` | 31,081 | 图表 chunks（按需） |

首屏 entry 内应用代码 Top：`router.ts`（7.2 KiB rendered，23 个路由的懒加载声明）、
`Sidebar.vue`（5.0 KiB）、`Topbar.vue`（4.3 KiB）、`dataApi.ts`（4.2 KiB，含 #8 ETag 拦截器）、
lucide 图标 22 个合计 15.5 KiB rendered（已按名导入 tree-shaking）。

## 5. 本批改动

1. **首页路由懒加载**（`src/router.ts`）：`LiveAssetOverview` 从顶层静态 `import`
   改为 `() => import('./components/LiveAssetOverview.vue')`——此前它是 23 个路由中
   唯一打进首屏 entry 的页面组件（含图表依赖）。路由预热器 `warm()` 的 `/` 加载器
   同步改为动态 import。
2. **问答浮窗异步化**（`src/App.vue`）：`AskWidget` 改为 `defineAsyncComponent`，
   连带 `batch2Api.ts` 与 `data/batch2.ts` mock 数据移出首屏（其余 `batch2Api`
   引用本就只在懒加载的路由 chunk 内）。
3. **lucide 按名导入**（`src/components/Sidebar.vue`）：`import * as I` 改为 22 个
   图标的具名导入。实测 Rollup 对静态 `I.X` 访问本就能 tree-shake，本次 entry
   未因此再缩小（属预防性规范），如实记录。
4. **分析工具**（`vite.config.ts` + devDependency `rollup-plugin-visualizer`）：
   `BUNDLE_ANALYZE=1` 时输出 `docs/bundle-stats.html/.json`；默认关闭。
   另补 `declare const process` 最小声明以通过 `vue-tsc -b`（不引入 `@types/node`）。

验证：`npm run build`（内含 `vue-tsc -b`）通过；`npm run typecheck` 通过。

## 6. 诚实说明与后续建议

- 首屏 -32.4%，未达“减半”：剩余主要是 Vue 运行时 + vue-router（约 85%），
  再往下只能换框架或做 SSR/服务端渲染，超出本批范围。
- dist 总体积 +1.0%：多 chunk 的固定开销（每个 chunk 的导入包装与 vite preload helper），
  属正常现象；首屏与缓存粒度（路由级缓存）仍是净收益。
- `routePreload.warm()` 会在首屏空闲 800ms 后预加载其他路由 chunk——属空闲时投机加载，
  不影响首屏指标；弱网/省流模式下已自动关闭。
- 后续可选：klinecharts（509 KiB rendered）目前随行情页 chunk 一起加载，
  可进一步拆为图表渲染时才 `import()`；`route-snapshot.json`（68 KiB）可考虑
  压缩或分页。两者都不在首屏，本批未动。
