# 多用户认证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成最多 30 人独立使用的登录系统和管理员用户概况后台。

**Architecture:** SQLite 管理账号、名额和服务端会话；认证网关选择按用户隔离的业务应用。保留现有 DuckDB 业务服务，管理员绑定旧目录，其他用户绑定 UUID 子目录。

**Tech Stack:** Python 3.11/3.12、FastAPI、SQLite、Argon2id、Vue 3 / Uni-App H5。

**Spec:** ../specs/2026-10-04-multi-user-auth-design.md

## Global Constraints

- 总账号数最多 30，管理员计入；没有截止日期，公开注册不能创建管理员。
- 密码长度 15–128 字符，会话最多 12 小时。
- 管理员只查看账号和使用数量，不查看其他用户财务或报告明细。
- 现有数据只归本机初始化的管理员；测试使用临时数据，不新增持久化测试目录。
- 不公开部署、不创建默认密码、不调用付费模型、不执行真实交易。

## Review Focus

- 两个数据库连接竞争第 30 个名额必须只成功一个。
- 所有 HTTP 启动入口认证一致，旧 Bearer Token不能绕过用户会话。
- 修改密码、过期和退出后的旧会话都必须失效。
- 普通用户实例不能继承进程内管理员模型密钥或后台任务目录。
- 浏览器历史导航、跨标签会话变化和迟到响应不能显示前一用户缓存。

### Task 1: 账号仓库和密码会话

**Files:** 新增 `models/repositories/auth.py`、`services/auth.py`、`scripts/manage_users.py`；修改 `pyproject.toml` / `uv.lock`。

**Interfaces:** `AuthService(root, clock=time.time)` 提供 `status()`、`bootstrap(username,password)`、`register(username,password)`、`login(username,password)`、`session(token)`、`logout(token)`、`change_password(token,current,password)`、`reset_password(username,password)`；会话返回用户字段、CSRF、过期时间。

- [x] 在 `tmp-check/auth_verify.py` 编写实际密码、未初始化注册、并发名额、重复用户、过期、撤销和修改密码测试，运行确认缺失功能导致失败。
- [x] 以 SQLite `BEGIN IMMEDIATE` 和唯一约束保证名额；Argon2id 验证密码；持久化会话摘要。
- [x] 运行 `web/backend/.venv/Scripts/python.exe -B -X utf8 tmp-check/auth_verify.py auth`，4项通过。

### Task 2: 用户业务实例和只读概况

**Files:** 新增 `services/user_context.py`；修改 `services/composition.py`。

**Interfaces:** `UserContexts(settings, auth)` 提供 `get(user)`、`summary(user)`、`start()`、`close()`；`get` 返回该用户的内部 FastAPI 应用和服务。

- [x] 验证两个用户自选、持仓、模型配置及研究对象独立，管理员继承模拟旧数据。
- [x] 显式注入普通用户禁用的模型配置，保护数据路径，按用户管理服务生命周期。
- [x] 概况用只读 SQL 统计，验证零值、不可用和敏感字段缺失。
- [x] 运行临时验证程序的 `contexts` 部分，包括普通用户自有兼容/Jev密钥配置和重启恢复，通过。

### Task 3: 认证网关和所有启动路径

**Files:** 新增 `controllers/auth_app.py`、`controllers/auth_routes.py`；修改 `controllers/server.py`、`deployment.py`、`scripts/serve_advisor_web.py`、`scripts/start_web.py`、`scripts/run_market_advisor.py`。

**Interfaces:** `create_authenticated_app(settings, public_web_assets=False)` 返回 FastAPI 网关；认证接口位于 `/api/v1/auth`，管理接口 `/api/v1/admin/users`。

- [x] HTTP验证匿名401、管理员200、普通用户403、CSRF和Origin拒绝、注册限额、退出和密码修改失效。
- [x] 公共认证路由和安全响应头；其余业务请求只能转交会话对应实例，Cookie支持HTTPS安全模式。
- [x] 启动探针改用公开实例状态；已初始化后无需匿名读取报告；维护请求显式使用用户会话。
- [x] 运行临时HTTP验证部分，6项通过，包括代理HTTPS来源、网关与业务层配置限流和用户数据库故障隔离。

### Task 4: 登录注册、用户状态和后台页面

**Files:** 新增 `models/auth.js`、`controllers/authNavigation.js`、登录/注册/后台页面及账号设置组件；修改 `main.js`、`App.vue`、`pages.json`、`models/api.js`、设置页、`vite.config.js`。

**Interfaces:** `authState` 保存当前会话；`ensureSession()` 检查登录；`authRequest()`发同源带凭据请求；退出清缓存，业务请求继续验证连接作用域。

- [x] 验证401会话清理、用户切换缓存隔离、登录后导航和迟到响应拒绝。
- [x] 同源请求、导航检查、登录前隐藏业务界面、账号组件、后台用户表和人数概览。
- [x] 浏览器已验证桌面登录、工作台、设置、后台概况及服务重启后的会话失效跳转；前端lint/格式/构建通过。
- [ ] 余下完整注册/退出/普通用户流程及手机布局实测：继续消息后浏览器工具的URL安全策略拒绝临时页访问，已停止，不绕过；API和导航模型覆盖对应权限与状态转换。

### Task 5: 部署文档、检查和独立审查

**Files:** 修改 `web/deploy/` 配置和 `web/docs/` 文档。

- [x] Docker认证无需全局Token；文档提供本机管理员初始化和密码恢复命令，说明所有统计口径。
- [x] 执行后端 `verify.py`、临时完整安全验证、前端检查与构建；源码通过，原有交易准入仍为3/9。
- [x] 独立审查接口权限、启动路径、凭据与用户缓存边界；4项重要问题已修复并复核通过。Compose配置检查通过，未启动容器或公网部署。

用户已指示“开始执行”，在本会话连续完成以上步骤。
