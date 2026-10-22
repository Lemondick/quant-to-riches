# 依赖更新整合审查（2026-10-23）

以远程 main `ac78813` 为基线，整合本批 Dependabot PR #1–#25。保留全部原始头提交及合并历史；原工作目录的 571 项未提交状态不变。本地验收完成后，先推送独立整合分支，远程完整 CI 通过才合入 main。

## 范围与处理

| 范围 | PR | 更新 |
| --- | --- | --- |
| GitHub Actions | #1、#2、#4、#6、#8 | setup-python 6、checkout 7、setup-node 7、setup-dotnet 6、setup-uv 7 |
| 前端运行依赖 | #5、#10、#11 | Lucide 1.0.0、KLineCharts 10.1.0、ECharts 6.1.0 |
| 前端工具 | #3、#7、#9、#12、#13、#14、#16 | Vite 8.3.4、globals 17.13.0、@eslint/js 10.0.1、Prettier 3.9.10、plugin-vue 6.0.10、ESLint 10.13.0、TypeScript parser 8.71.2 |
| .NET / 测试 / 可观测性 | #15、#17–#25 | 4 项 Microsoft 包 10.0.13、Test SDK 18.11.0、3 项 OTel 1.20.1、xUnit 2.9.3、测试适配器 4.0.1 |

独立复审确认了两组必须配套的升级：Vite 8 与 plugin-vue 6 同时合入；ESLint 10、@eslint/js 10、TypeScript parser 与 eslint-plugin 8.71.2 同时合入。额外升级 `@typescript-eslint/eslint-plugin`，避免旧插件声明的 ESLint 8/9 限制。依据：[Vue 插件兼容声明](https://github.com/vitejs/vite-plugin-vue/blob/plugin-vue@6.0.10/packages/plugin-vue/package.json)、[TypeScript ESLint 插件元数据](https://registry.npmjs.org/@typescript-eslint/eslint-plugin/8.71.2)。

重新生成 npm 锁文件和整套 .NET 项目锁文件，严格检查 peer 依赖并执行 locked restore。新的 ESLint 规则发现模型连接错误丢失原始异常原因；补充 `Error.cause`，保持原有用户提示，取消和网络失败的回归先失败后通过。未放宽 lint 规则或使用 peer 依赖绕过参数。

ECharts 6.1.0 修复此前的 [GHSA-fgmj-fm8m-jvvx](https://github.com/advisories/GHSA-fgmj-fm8m-jvvx) 依赖公告。原应用使用 `line` 系列；本次未声称已复现该公告涉及的 `lines` 系列利用路径。Lucide 1.0.0 提示未来改用 `@lucide/vue`，当前使用的 72 个图标导出均保留，包更名留作维护后续事项。

## 本地验证

| 检查 | 结果 |
| --- | --- |
| .NET 完整 restore / locked restore / Release build | 通过，构建 0 警告、0 错误 |
| .NET PostgreSQL 全套 | 571/571 通过，0 跳过；新测试适配器实际执行数据库用例 |
| npm 严格安装 | `npm ci --strict-peer-deps` 通过 |
| 前端 lint / format / 类型检查 / 单元 | 全部通过；129/129，0 跳过 |
| 浏览器接口与账户回归 | 17/17 通过，含新增非空风险净值/负回撤曲线及桌面、手机悬浮提示 |
| 页面检查 | 52 项，0 浏览器错误；指数条、基金目录、板块质量脚本通过 |
| KLineCharts 实际交互 | 320/390/1440/2323 四种宽度共 8 项通过，含 OHLC、基金净值、周期、键盘、缩放、指标与缓存；0 浏览器错误 |
| 构建 | 演示、连接后端及 `BUNDLE_ANALYZE=1` 构建通过；可视化分析产物已生成 |
| PWA 产物 | 演示 94 个、连接后端 95 个预缓存引用全部存在，无 API 路径 |
| API 契约 | 新框架包导出的 217 条路由与已发布清单一致；未改迁移 SQL |
| 依赖审计 / 密钥扫描 | npm 0 漏洞；.NET 未报告易受攻击包；密钥扫描通过 |
| Git 整合 | 25 个 PR 头提交均为整合版本祖先，所有请求的 npm/NuGet 版本逐项核对保留 |

日志、TRX、截图及分析产物位于忽略目录 `.superpowers/dependency-review/`。测试使用独立回环 PostgreSQL 和合成浏览器数据，没有访问用户应用数据库、发送真实邮件或调用付费模型。生产 Windows Service 托管及真实遥测输出仍未部署实测。

被本批整合结果取代的 Dependabot 重复 CI 已取消；最终以整合提交的完整 GitHub Actions 为合并门槛。
