# 项目优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复账户问答残留、汇率币种对混用、长回测超时及未支持问答的失效执行路径。

**Architecture:** 在既有组件、请求客户端和服务中修复边界。复用当前认证、存储、计算和测试设施。

**Tech Stack:** Vue 3 / TypeScript / Node test / Playwright / .NET 10 / PostgreSQL 18。

**Spec:** `docs/superpowers/specs/2026-10-23-project-optimization-design.md`

## Global Constraints

- 不新增数据库迁移、依赖或模型供应商，不改写金融历史。
- 其他请求维持 15 秒、策略配置/预览 65 秒；组合回测 605 秒、智能定投回测 305 秒。
- 未支持问答不调用模型、不消耗模型预算；现有模板和鉴权继续有效。
- 原有桌面备份分支与 stash 保留；完成验证后同步桌面目录。

## Review Focus

- 切换账户时，已发出的问答请求成功或失败均不得重新写入旧回答。
- 同一账户仅重新检查会话时，不应丢失已有问答。
- 汇率旧配置迁移和本币来回切换，不能静默更换币种对含义。
- 异步汇率拉取期间切换本币，不能把旧币种对赋给新本币。
- 较长请求仍应可取消，未支持问答仍需鉴权并明确不调用模型。

### Task 1: 问答会话隔离

**Files:** `web/frontend/src/components/AskWidget.vue`, `web/frontend/src/services/dataApi.ts`, `web/frontend/tests/e2e/branch-repair.mjs`

**Interfaces:** 客户端 write 增加可选第六参数 AbortSignal；既有五参数调用兼容。

- [ ] 新增浏览器回归：A 问答后退出并切换 B，历史与草稿清除；A 的迟到成功/失败均被丢弃；同会话 focus 重新核验保留历史。
- [ ] 运行 `node --test --test-name-pattern="ask.*session|ask.*account" tests/e2e/branch-repair.mjs`，观察旧实现出现残留断言失败。
- [ ] 使用会话标识与认证失效事件重置浮窗，取消旧请求并防止旧回调更新新会话。
- [ ] 运行前端全套单元和浏览器接口回归，预期全部通过；提交本任务。

### Task 2: 汇率币种对

**Files:** `web/frontend/src/composables/useFxRates.ts`, `web/frontend/tests/fx-rates.test.mjs`, 必要的汇率状态解析工具与浏览器回归。

**Interfaces:** 汇率条目包含 from/to，convert 仅使用与当前本币匹配的条目；旧格式迁移绑定迁移时的本币。

- [ ] 新增真实状态回归：100 JPY 以合成汇率 0.05 得 5 CNY，改 USD 后不得得到 5 USD；切回 CNY 恢复 5 CNY。覆盖旧配置迁移、不同币种对并存及拉取期间切换本币。
- [ ] 运行对应回归，预期旧实现币种对混用断言失败。
- [ ] 实现显式币种对、旧配置迁移及异步拉取的币种对归属。
- [ ] 运行前端全套单元和浏览器接口回归，预期全部通过；提交本任务。

### Task 3: 长任务请求等待

**Files:** `web/frontend/src/services/dataApi.ts`, `web/frontend/tests/request-timeouts.test.mjs`

**Interfaces:** 保留 Task 1 的可选 AbortSignal 和既有读写签名；仅指定 POST 回测端点延长等待。

- [ ] 使用 Node 受控计时器测试组合回测在 15 秒后仍可完成、605 秒到期中止；智能定投 305 秒；普通读取 15 秒；策略预览 65 秒；长请求外部取消立即生效。
- [ ] 执行 `node --test tests/request-timeouts.test.mjs`，预期旧实现组合回测提前超时。
- [ ] 按端点与方法匹配既定等待时限，保留取消与资源清理。
- [ ] 运行前端全套单元与 lint/typecheck，预期全部通过；提交本任务。

### Task 4: 问答范围与完整验证

**Files:** `web/backend-dotnet/src/QuantAssistant.Api/Services/AskService.cs`, `web/backend-dotnet/tests/QuantAssistant.Api.Tests/BranchIntegrationRepairTests.cs`, `web/frontend/src/services/batch2Api.ts`, `web/frontend/src/components/AskWidget.vue`, 相关浏览器回归及功能说明。

**Interfaces:** 未支持问题返回 matched=false、unsupported=true、model_calls=0 及明确 answer；模板响应兼容，接口路径不变。

- [ ] 新增 PostgreSQL 接口回归：非模板问题给出明确范围、零模型调用、零计算任务；未登录仍返回 401。浏览器显示范围提示而非演示回答，模板标记使用真实响应字段。
- [ ] 执行后端专项，预期旧实现无法返回所需范围响应。
- [ ] 移除未注册任务路径及其模型预算/缓存依赖，前端按真实字段展示提示，更新当前功能说明。
- [ ] 执行 .NET PostgreSQL 全套、前端全套、浏览器接口、lint/typecheck/格式、两种构建，预期全部通过；记录验证并提交。
- [ ] 执行一次独立整分支复审，修复重要问题并补回归；同步已验证版本至桌面目录。
