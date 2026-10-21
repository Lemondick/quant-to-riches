# 观澜 · 投资研究工作台

当前只维护网页端。前端、后端、部署与文档统一放在[`web/`](web/README.md)。其他客户端已归档到桌面。

```text
web/       网页项目：frontend、backend-dotnet、worker、backend、deploy、docs
data/      账户、账本、证据及网页运行资源
deploy/    本机私有模型配置与密钥
.github/   源码检查与构建流程
.git/      版本历史
```

当前原生版本：C# / ASP.NET Core 10 + PostgreSQL 18，Python 负责隔离计算，前端为 Vue 3 与 TypeScript；单机部署，不使用 Docker。

11 个页面已接入原生业务后端，保留提供的 Vue 工作台布局和主题。前端默认开发模式为显式演示；连接真实 API 时设置 `VITE_API_BASE_URL=/api/v1`，原生发布脚本默认启用该设置。浏览器演示数据不用于生产行情。连接方式及验证命令见[前端说明](web/frontend/README.md)。

仓库保留的 2026-10-18 历史上线记录为 `quality-v18-1`，见[当时的数据质量验收](web/docs/refactor/public-data-quality-20261018.md)。2026-10-20 的稳定性修复最初在独立 `codex/` 分支验证；截至 2026-10-21 核对，`codex/` 系列分支本身未合并 main（`codex/test-scheduler-isolation` 除外，其分支顶即当前 main），但以下分支的修复内容已与 main 快照一致：`ci-stability`（CI 修复）、`stability-market`（账本幂等/分红截止，BUG-003/004）、`model-network-boundary`（模型网络边界）、`backtest-correctness`（回测四项修复）、`confirmed-fifteen-indices`、`sector-quality-final`。其余分支内容尚未进入 main：`public-data-quality`、`public-refresh-scheduling`、`global-index-history`、`index-registry-api`、`index-strip-ui` 的代码未在 main 出现；`fund-catalog-taxonomy` 仅部分进入（`FundCatalogService.cs`、`FundClassification.cs` 等仍为分支独有版本）。各提交的实际 CI、范围与待完成项见[稳定性实施记录](web/docs/refactor/stability-implementation-20261018.md)。公开行情保留来源时间、延迟与不可用状态，正式净值与盘中估值分别处理，不补造缺失数据。

仓库包含前端、.NET API、Python Worker、数据库迁移、测试和部署脚本。数据库、账户账本、密钥、运行日志、依赖目录及构建产物不随源码上传；新环境需安装依赖并按示例配置数据库与服务凭据。历史验收截图、导出文件、原始证据和内部研究文档保留在本地，文档中的历史产物链接可能仅适用于原工作区。

```powershell
.\启动网页版.ps1 -Release C:\Quant\releases\当前版本
```

先按[原生部署及恢复手册](web/docs/refactor/p7-runbook.md)发布版本并配置数据库。业务 API 默认监听 `127.0.0.1:8765`，生产浏览器通过 Caddy HTTPS 访问。迁移和验收状态见[实施记录](web/docs/refactor/native-migration-progress.md)。

当前迁移目录为 `0001`–`0023`，发布前须执行完整迁移。活动入口是 `web/backend-dotnet/deploy/` 的原生发布及监督程序；`web/backend/` 内旧 FastAPI、`web/deploy/cloud_up.sh` 和旧容器/exchange 入口仅用于兼容及历史恢复核验，不是当前生产入口。模型请求的公开网络边界及源码验证见[网络边界说明](web/docs/refactor/model-network-boundary-20261020.md)。

| 文档 | 内容 |
| --- | --- |
| [功能说明](web/docs/功能说明.md) | 页面模块、数据口径、模型流程和交易边界 |
| [使用手册](web/docs/使用手册.md) | 截图导入、净值K线、账本、费率、批次、AI复核操作 |
| [登录与用户管理](web/docs/登录与用户管理.md) | 管理者初始化、30人上限、独立账号、后台与密码恢复 |
| [维护说明](web/docs/维护说明.md) | MVC目录、启动、安装、备份恢复、检查与部署 |
| [接口说明](web/docs/api.md) | 当前接口清单与参数查阅入口 |
| [清理记录](web/docs/清理记录.md) | 删除范围、桌面备份及验证结果 |

系统支持基金持仓管理、公开数据研究、用户模型选择及系统定时报告；不执行真实交易。
原有归档准入报告为3/9；当前结果须按新的前向证据与完整站点恢复校验重新检查，缺少的真实证据不会因代码修改而自动通过。
