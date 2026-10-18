# 原生部署、迁移与恢复手册

更新时间：2026-10-09。当前方案为单台服务器、ASP.NET Core 10、PostgreSQL 18、Python 3.12、Vue 3 / TypeScript 和 Caddy，不使用 Docker。用户已授权直接实施和测试修复；本文替代初版容器切换流程。

Ubuntu 已有域名和证书时，按发布包内的 [Ubuntu 部署说明](../../backend-dotnet/deploy/ubuntu-runbook.md)执行环境检查、证书权限检查及 systemd 安装。Windows 可用 `publish.ps1 -Target linux` 生成 Linux 便携包；Python 环境在目标机的最终版本目录创建，不携带 Windows 虚拟环境。

源码交付与隔离测试不能替代正式服务器切换和长期观察。实际账户导入、密钥迁移、域名及 HTTPS 配置仍需在正式环境的维护窗口执行。

## 1. 环境和目录

安装 .NET 10 SDK/运行时、PostgreSQL 18 服务及客户端、Node.js 22.18+ 或 24、uv 与 Python 3.12。构建时需要 SDK、npm 与 uv，正式运行使用发布目录中的 Python 虚拟环境。PostgreSQL 仅允许本机服务账号访问。

采用独立目录，例如：

| 用途 | Windows 示例 | Linux 示例 |
| --- | --- | --- |
| 新版本 | `C:/Quant/releases/20261006` | `/opt/quant/releases/20261006` |
| 站点数据 | `C:/Quant/site` | `/var/lib/quant/site` |
| 加密密钥及解密材料 | `C:/Quant/private` | `/var/lib/quant/private` |
| 普通备份 | `C:/Quant/backups` | `/var/lib/quant/backups` |
| 旧站冻结副本 | `C:/Quant/frozen/20261006` | `/var/lib/quant/frozen/20261006` |

密钥目录须在站点和静态资源目录之外。示例配置在 `web/backend-dotnet/deploy/environment.example`，真实配置仅给服务账号读取。

.NET 使用 `QUANT_PG_CONNECTION`；Python 工具使用 `QUANT_PG_DSN`，或 PostgreSQL 客户端的 `PGHOST/PGPORT/PGDATABASE/PGUSER`。优先用受保护的 `.pgpass` / `PGPASSFILE` 保存 PostgreSQL 密码。设置 `QUANT_SITE_ROOT`、`QUANT_SECRET_ROOT`、`QUANT_BACKUP_DIR`、`QUANT_DOMAIN` 和随机的 `QUANT_WORKER_SECRET`（至少 32 字符）。不要把真实凭据写进命令参数或仓库。

## 2. 发布新版本

从仓库根目录运行，目标必须是尚不存在、位于源码目录之外的新目录：

```powershell
.\web\backend-dotnet\deploy\publish.ps1 -Destination C:\Quant\releases\20261006
```

Linux：

```bash
bash web/backend-dotnet/deploy/publish.sh /opt/quant/releases/20261006
```

发布会构建 Release API，执行 `npm ci`、严格类型检查和 H5 构建，再用 `uv sync --frozen --no-dev --python 3.12` 安装锁定环境。包内含 `api/`、`frontend/`、`backend/`、`worker/`、`migrations/` 和 `tools/`。失败时保留失败目录用于排查，下次使用新目录。

2026-10-17 性能更新包含迁移 `0020_public_snapshots_and_notifications.sql` 和 `0021_account_display_revision.sql`；先运行下方迁移命令再启动新版 API 和 Worker。0021 让账户写入和公开数据提交在同一事务中更新展示版本，确保短缓存立即失效。发布脚本默认将前端 `VITE_API_BASE_URL` 设为 `/api/v1`，11 个页面接入真实账户和业务接口。生产环境默认开启交易时段预热；开关、缺失快照语义和追踪配置见 [架构说明](../../backend-dotnet/ARCHITECTURE.md)。首次没有快照时返回明确不可用并后台准备，不能填入演示行情。

## 3. 新建站点或迁移旧站

两种情况均先创建由业务服务账号拥有的 PostgreSQL 空库，再设置连接环境。数据库迁移按文件版本记录，可重复执行：

```powershell
dotnet C:\Quant\releases\20261006\api\QuantAssistant.Api.dll --migrate C:\Quant\releases\20261006\migrations
```

全新站点在本机初始化管理者，密码由控制台隐藏输入：

```powershell
dotnet C:\Quant\releases\20261006\api\QuantAssistant.Api.dll --init-admin 管理者用户名
```

旧站迁移时不要先初始化管理者。先停止旧 API、定时任务及所有 worker，确认数据库连接已关闭，再保留完整冻结副本（包括 SQLite WAL 与各用户 DuckDB、原始证据）。旧站不知道新站的锁协议，不能只依赖新工具的 `.site.lock` 来停止旧站写入。

使用发布环境导入冻结副本，目标站点目录也须不存在：

```powershell
& C:\Quant\releases\20261006\backend\.venv\Scripts\python.exe C:\Quant\releases\20261006\tools\migrate_site.py --frozen-source C:\Quant\frozen\20261006 --destination C:\Quant\site
```

Linux 使用发布环境的 `backend/.venv/bin/python` 与相同参数。

导入保留账户标识、密码哈希、用户内记录编号、时间、金额和证据；未知字段或无法精确存储的金额会阻止导入。来源和复制文件均校验 SHA256，数据库记录迁移清单；重复运行同一来源不会再次导入。数据库已提交而目录发布中断时，同一命令核验暂存文件后完成发布。

旧登录会话及恢复凭据作废；未完成研究、报告及费用预留转为中断，不自动重复付费调用。管理者与普通用户的文件布局保持兼容。

普通导入排除模型密钥。需要保留旧模型连接时，单独执行：

```powershell
dotnet C:\Quant\releases\20261006\api\QuantAssistant.Api.dll --import-model-secrets C:\Quant\frozen\20261006
```

命令先核验同一冻结来源的迁移清单，再把当前账户的旧密钥加密保存到独立目录。此步骤需要真实密钥的现场授权和受保护目录；本次隔离验收未执行真实密钥导入。

## 4. 启动原生服务

Windows 的默认入口已改为 C# API 与 Python compute supervisor：

```powershell
.\启动网页版.ps1 -Release C:\Quant\releases\20261006 -Workers 3
```

也可设置 `QUANT_RELEASE_DIR` 后直接运行默认启动器。它只运行已发布版本；兼容原快捷方式的 `-SkipBuild`。首次启动及重启都会核验数据库迁移。服务退出会停止其拥有的进程树。长期运行时通过 Windows 任务计划程序或现有进程管理器调用受保护的启动脚本，并配置开机启动、失败重启与日志保存。

Linux 将新版本设为 `/opt/quant/current`，安装 `quant-api.service` 与 `quant-worker@.service`，并放置受保护的 `/etc/quant/environment`。示例启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now quant-api
sudo systemctl enable --now quant-worker@1 quant-worker@2 quant-worker@3
```

Caddy 配置使用 `Caddyfile.native` 和正式域名。浏览器通过 HTTPS 访问，业务 API 默认仅监听 `127.0.0.1:8765`。生产 Cookie 设置 Secure；内部 worker 接口及 OpenAPI 不通过反向代理公开。

`/healthz` 检查 API 与数据库；配置 worker 后，`/readyz` 要求达到 `QUANT_EXPECTED_WORKERS` 个有效心跳（30秒内且未停止）。Windows 启动入口按 `-Workers` 自动设置期望数量，Linux 环境文件须与实际 worker 数量一致。管理页同时显示排队、超期任务、磁盘空间、归档年龄、自动备份结果及日历提醒；业务可用与运维需要处理分别展示。默认队列等待阈值120秒、磁盘可用比例10%、归档年龄36小时，可在环境示例中调整。

备份年龄使用归档清单内的创建时间，复制旧文件不能刷新年龄；管理接口最多核验最近10份归档，未变化的校验结果缓存60秒，文件或恢复证明变化会立即失效。`/api/v1/operations/readiness` 还会报告数据证据、部署配置、实际备份恢复等条件，研究证据不完整时保持阻断状态。

### 年度交易日历

管理者后台的“年度交易日历”可按年份读取、导入和更新公告地址及休市日期。只接受上交所、深交所、北交所官方 HTTPS 地址，日期须属于所选年份。周末自动休市，调休上班不会使周末成为交易日。页面保存带读取版本；并发修改返回409，保留草稿，需重新读取后核对。

内置2026年日历；每年10月起提醒准备次年日历。官方公告发布后由管理者导入，不能把缺失日历当成全年交易日。跨年未导入时，自动报告继续暂停；不要使用验收中的2099年合成日期作为正式日历。

## 5. 普通备份与空环境恢复

### Windows 定时备份

在受保护的启动配置中显式启用，再运行原生 supervisor：

```powershell
$env:QUANT_NATIVE_BACKUP_TIME = '02:00' # Asia/Shanghai
$env:QUANT_BACKUP_RETAIN = '14'
$env:QUANT_BACKUP_TIMEOUT_SECONDS = '1800'
```

同时设置 `QUANT_BACKUP_DIR` 与准确的 `PGHOST/PGPORT/PGDATABASE/PGUSER`，PostgreSQL 客户端不在 PATH 时设置 `QUANT_PG_BIN`。标准 PG 变量必须指向 API 使用的业务库；普通归档不包含私有密钥。

到时先查询队列和费用预留，有任务或数据库不可读时延后，每60秒再检查。空闲后停止本 supervisor 拥有的 API 和 worker，调用现有完整站点归档工具，完整校验成功后才清理超额旧备份。备份完成或失败都会恢复服务，并等待三个（或实际配置数量的）worker 达到就绪条件。备份会有短暂维护中断，请选择低使用时段。

结果原子写入备份目录的 `.native-backup-status.json`，失败只记录诊断代码，同一天不会在重启后反复执行失败备份。supervisor 异常退出遗留的 `running` 状态在重启时标为中断；损坏或无法写入状态文件会提示并跳过备份，已有服务继续运行。归档超时会清理所属 PostgreSQL 子进程树，服务恢复失败则退出，交给已配置的进程管理器重启。Windows 开机启动与故障重启仍需由正式部署的任务计划或服务管理器承担。

归档子进程的路径输出固定为 UTF-8，支持中文备份目录，不依赖 Windows 当前控制台编码。该设置仅作用于归档子进程。

本功能接入 Windows 原生 supervisor。Linux 继续使用下面的停服备份命令及 systemd 运维流程；本机 Windows 验收不代表已在 Linux 上执行定时维护。备份成功也不等于恢复已验证，应定期在新空库、新目录实际恢复，管理页随后展示归档哈希绑定的恢复证明。

### 手工备份与恢复

备份前停 API 和 worker，避免数据库及文件跨时刻不一致。工具拒绝在运行站点锁持有期间备份。Linux 示例：

```bash
export QUANT_PYTHON=/opt/quant/current/backend/.venv/bin/python
bash web/backend-dotnet/deploy/backup.sh /var/lib/quant/backups
```

Windows 可直接调用发布包中的工具：

```powershell
& C:\Quant\releases\20261006\backend\.venv\Scripts\python.exe C:\Quant\releases\20261006\tools\site_archive.py backup --site-root C:\Quant\site --output C:\Quant\backups --retain 14
```

普通归档含 PostgreSQL custom dump、站点文件与清单，排除私有密钥。新归档完整校验后才清理超过保留数量的旧归档。加密密钥和 ASP.NET 解密材料需另行备份，否则恢复后必须重新配置模型连接。

恢复时停所有新旧写入方，将 `PGDATABASE` 明确指向新建空库，并选择新目录：

```powershell
& C:\Quant\releases\20261006\backend\.venv\Scripts\python.exe C:\Quant\releases\20261006\tools\site_archive.py restore C:\Quant\backups\选定归档.zip --destination C:\Quant\restored-site
```

工具不删除现有库或目录。转储、恢复标记和数据库指纹在同一 PostgreSQL 事务提交。会话作废、运行任务中断、路径重定位和目录发布随后执行，完成后生成与归档哈希绑定的 `.restore.json` 证据。

如果数据库恢复成功但后续步骤中断，仍保持所有服务停止，运行：

```powershell
& C:\Quant\releases\20261006\backend\.venv\Scripts\python.exe C:\Quant\releases\20261006\tools\site_archive.py complete-restore C:\Quant\backups\选定归档.zip --destination C:\Quant\restored-site
```

数据库或文件在中断后已变化时，工具拒绝续恢复。此时保留现场证据，重新选择空库和新目录。

恢复证据路径按账户命名空间重定位，用户根目录下的快照也能保留；不会通过猜测同名文件替代缺失证据。快照的业务内容哈希与归档文件的字节哈希各自保留并校验。

## 6. 回切旧站

停止原生 API 和 worker，先确认所有任务已完成、取消或中断。不要直接重启旧副本，否则会遗漏新站期间的业务写入。

```powershell
& C:\Quant\releases\20261006\backend\.venv\Scripts\python.exe C:\Quant\releases\20261006\tools\export_legacy_site.py --site-root C:\Quant\site --destination C:\Quant\rollback-site
```

反向工具从 PostgreSQL 当前业务状态重建旧 SQLite 和每用户 DuckDB，保留原分析表与证据，重置后续记录序列，撤销旧会话，排除模型密钥。目标必须是新独立目录。超出旧库可表示精度的金额会阻止导出，而不会静默舍入。

先核对清单、账号数、关键账本、证据路径和后续新增编号，再用该目录启动旧站。旧站启动方式属于历史兼容入口，当前默认启动器始终运行原生版。

## 7. 现场验收

隔离环境已覆盖空库建表、重复导入、两个账户的重复历史编号、金额保留、新写入反向导出、备份恢复、中断续恢复及修改后拒绝恢复。最新结果见[迁移实施状态](native-migration-progress.md)。

正式切换仍需核对实际冻结样本，确认域名与证书、磁盘容量、服务账号权限、开机恢复、日志轮转、普通及密钥备份；观察真实负载至少 24–72 小时。本次未执行正式账户迁移、付费模型调用、真实服务器切换或长期运行验收。
