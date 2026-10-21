# 观澜 · 网页项目

当前原生版本采用 C# / ASP.NET Core 10、PostgreSQL 18、Python 3.12 计算 worker、Vue 3 与严格 TypeScript。部署到单台服务器，不使用 Docker。

11 个页面已接入真实认证及业务接口，保留提供的 Vue Router 工作台布局和主题。纯前端开发默认显式演示模式；生产发布设置 `VITE_API_BASE_URL=/api/v1`。2026-10-18 的历史部署 `quality-v18-1` 及公开源限制见[当时的验收](docs/refactor/public-data-quality-20261018.md)。当前稳定性分支尚未合并 main 或部署，按提交记录实际测试，见[实施记录](docs/refactor/stability-implementation-20261018.md)。运行与验证命令见[前端说明](frontend/README.md)。

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

完整迁移目前为 `0001`–`0024`；`0023`/`0024` 只调整公开派生缓存。`backend/` 的 FastAPI、`deploy/cloud_up.sh` 与旧容器/exchange 进程保留兼容用途，生产不通过它们转发业务请求。后台刷新默认在每轮完成后等待 30 秒（可配置 15–300 秒），整体/任务预算默认 75/55 秒，详见[架构说明](backend-dotnet/ARCHITECTURE.md)。浏览器短暂复核快照不代表后台再次抓取上游。
