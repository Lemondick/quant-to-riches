# 归档（archive）

本目录存放已停用的旧 Docker 部署文件，仅保留 git 历史备查，
**不要用于部署**。当前生产部署为单机原生（C# / ASP.NET Core 10 +
PostgreSQL 18 + Python worker + Caddy），不使用 Docker（见 `web/README.md`）。

| 文件 | 原位置 | 说明 |
|---|---|---|
| `Dockerfile` | `web/Dockerfile` | 旧 Python API（`web/backend`）的容器构建文件 |
| `docker-compose.yml` | `web/deploy/docker-compose.yml` | 旧栈编排（PostgreSQL + 旧 Python API + Caddy） |

以下旧文档中的 `docker compose ... -f web/deploy/docker-compose.yml` 示例
已随文件一并作废，阅读时请注意：
`web/docs/登录与用户管理.md:88`、`web/docs/refactor/p6-status.md:11`、
`web/deploy/README.md:34`。
