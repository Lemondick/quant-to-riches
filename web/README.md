# 观澜 · 网页项目

当前原生版本采用 C# / ASP.NET Core 10、PostgreSQL 18、Python 3.12 计算 worker、Vue 3 与严格 TypeScript。部署到单台服务器，不使用 Docker。

11 个页面已接入真实认证及业务接口，保留提供的 Vue Router 工作台布局和主题。纯前端开发默认显式演示模式；生产发布设置 `VITE_API_BASE_URL=/api/v1`。截至 2026-10-18 已部署 `quality-v18-1`，数据时间、正式净值与估值边界及公开源限制见[最新验收](docs/refactor/public-data-quality-20261018.md)。运行与验证命令见[前端说明](frontend/README.md)。

| 目录 | 职责 |
| --- | --- |
| [`backend-dotnet/`](backend-dotnet/ARCHITECTURE.md) | 业务 API、认证、账本、数据库与任务管理 |
| [`worker/`](worker/) | 隔离的数据和量化计算进程 |
| [`frontend/`](frontend/README.md) | TypeScript 页面、组件与数据模型 |
| [`backend/`](backend/README.md) | 复用的 Python 计算库与旧站兼容代码 |
| [`backend-dotnet/deploy/`](backend-dotnet/deploy/) | 原生发布、Windows supervisor、systemd、Caddy |
| [`docs/refactor/`](docs/refactor/native-migration-progress.md) | 迁移状态、测试证据、部署及回切手册 |

从仓库根目录运行 `.\启动网页版.ps1 -Release C:\Quant\releases\当前版本`。需要先发布版本并配置 PostgreSQL、站点目录和内部 worker 凭据。生产通过 Caddy HTTPS 访问，API 默认只监听 `127.0.0.1:8765`。

完整安装、冻结导入和备份恢复步骤见[原生部署及恢复手册](docs/refactor/p7-runbook.md)。代码与隔离测试交付情况见[迁移状态](docs/refactor/native-migration-progress.md)。原有部署与阶段文档保留为历史记录，当前运行方式以原生手册为准。
