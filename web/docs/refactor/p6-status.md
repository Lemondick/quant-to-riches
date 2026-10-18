# P6 执行状态（部署与恢复演练）

- 更新时间：2026-10-06
- 状态：代码修复和本地验证完成；**镜像构建、容器运行和真实备份恢复尚待演练**。本机 Docker 引擎未运行。

## 交付物（`web/backend-dotnet/` 下）

| 文件 | 说明 |
| --- | --- |
| `Dockerfile` | .NET 10 生产镜像：多阶段构建、非 root、`read_only` 配合、诊断关闭 |
| `deploy/docker-compose.yml` | PostgreSQL 18 → 版本化迁移 → API → Caddy，按健康状态依次启动 |
| `deploy/Caddyfile` | HTTPS 自动证书、`/api/*` 反向代理、安全响应头 |
| `deploy/.env.example` | 环境变量模板（PG 密码、域名、CORS） |
| `deploy/backup.sh` | `pg_dump` 备份 + 保留 14 份自动清理 |
| `deploy/restore.sh` | 校验压缩包 → 停写 → 重建库 → 事务恢复 → 成功后重启 |
| `deploy/tests/verify-backup-restore.sh` | 模拟 Docker 命令验证失败处理，不接触真实数据库 |

## 部署拓扑（与旧站点同构，计划书 §6「相同拓扑」）

```
浏览器 ──HTTPS──> Caddy :443 ──/api/*──> .NET API :8080 ──> PostgreSQL 18
                  │                        (read_only, non-root)
                  └──/srv/quant-web 静态前端（web 构建产物）
```

## 部署步骤（Linux 服务器）

```bash
cd web/backend-dotnet/deploy
cp .env.example .env           # 填写 POSTGRES_PASSWORD + QUANT_DOMAIN
docker compose up -d --build   # migrate 服务先应用 migrations/*.sql，成功后 API 才启动
docker compose logs migrate    # 首次应用当前七个 SQL 版本；重复执行跳过已记录版本
docker compose ps -a           # migrate 应成功退出，api/db 应健康，caddy 应运行
```

## 故障演练清单（计划书 §6 验收，需在服务器执行）

| 场景 | 演练动作 | 预期 |
| --- | --- | --- |
| 备份恢复 | `./backup.sh` → 改数据 → `./restore.sh` | 数据回到备份点 |
| 备份写入失败 | 在隔离环境限制备份目录空间 | 非零退出，不生成看似完整的备份；运维接收失败告警 |
| DB 不可达 | `docker compose stop db` | `/readyz` 返回 503，业务数据库异常返回统一错误响应 |
| Worker 崩溃 | 杀 Worker 进程 | 任务 `recover` → interrupted，不重复执行 |
| 模型超时 | 模拟模型无响应 | 任务 deadline 过期 → interrupted |
| 部署中断 | 构建到一半中断 | 重跑 `up -d --build` 幂等恢复 |

## 安全加固（已内建）

- API 与 DB 容器 `read_only: true` + `tmpfs:/tmp`
- `cap_drop: ALL` + `no-new-privileges:true`
- API 使用 .NET 官方基础镜像的非 root `app` 用户（`APP_UID`）
- Caddy 仅 `NET_BIND_SERVICE` 特权（绑定 80/443）
- HSTS + 安全响应头双层兜底

## 2026-10-06 修复与验证

- 认证：移除可伪造的用户请求头；业务接口校验有效会话，管理员接口校验角色，写入校验 CSRF 与浏览器 Origin；停用账户拒绝登录及旧会话；密码恢复只允许一次兑付，撤销旧会话并清除浏览器 Cookie。
- 账本：六类写操作共享数据库事务和用户锁，审计失败整体回滚；交易幂等键补唯一约束；修复 UTC 记录时间及现金重复统计；拒绝拆分/校准后直接冲正、早于原记录的冲正和非正数公司行动参数。
- 接口：登录恢复 `csrf_token`/`expires_at`，组合恢复日期与权重字段；交易录入支持原有字符串金额、标的名称与来源键，新增账本分页查询和完整成交/分红响应。
- 任务及缓存：并发入队去重、账号额度及全局并发门控；拒收过期 Worker 结果；因子和同类排名写后读取最新值。
- 部署：迁移入口可执行，健康探针检查 PostgreSQL；修复 PG 18 卷路径和前端构建接入；备份先校验临时文件再发布，恢复遇到 SQL 错误停止且不重启 API。

| 验证 | 结果 |
| --- | --- |
| .NET + 隔离 PostgreSQL 18 | 104 项通过，0 失败、0 跳过 |
| .NET 未配置测试数据库 | 43 项通过，61 项明确跳过，避免将未执行的集成测试报告为通过 |
| 原 Python 后端离线测试 | 410 项通过；源码、格式、类型和密钥扫描通过 |
| 前端 | lint、格式检查、五组行为验收及 H5 构建通过 |
| 空库迁移 | 七个版本应用成功，重复执行成功且无重复版本 |
| Docker Compose | 配置检查通过；容器启动尚未实测 |
| 备份恢复脚本 | Bash 语法及模拟故障测试通过，包括失败不发布备份、保留 14 份、路径含空格、坏压缩包拒绝和恢复失败不重启 |

集成测试用 `QUANT_TEST_PG_CONNECTION` 指定专用测试库；其中包含并发写入和临时数据库故障触发器，只能对隔离测试库执行。CI 已配置独立 PostgreSQL 服务，先应用迁移再执行测试。

## 剩余部署前置工作

- 在可用 Docker 引擎中构建镜像、运行此 Compose，并执行真实 PostgreSQL 备份恢复演练。模拟脚本测试不代表数据库恢复已验收。
- 补齐旧数据导入、反向导入、尚未接入的新 API 和 Python Worker 的 PostgreSQL 协议。当前 C# 后端仍不能完整替代原系统。
- `0007_ledger_integrity.sql` 遇到已有重复幂等键会停止迁移，须先核对重复记录；迁移不会自动删除账本数据。
- 恢复脚本会重建目标数据库；SQL 失败后 API 保持停止，不能承诺自动恢复失败前的数据。仅在隔离演练通过后用于生产。
