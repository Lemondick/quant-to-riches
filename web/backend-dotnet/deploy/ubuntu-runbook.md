# 观澜 Ubuntu 部署说明

本包面向单台 Ubuntu 服务器，以 systemd 管理 ASP.NET Core 10 API 和三个 Python 3.12 worker，PostgreSQL 18 本机运行，Caddy 提供 HTTPS。包不含业务数据、模型密钥、数据库密码、证书或 Windows Python 环境。下面是通用部署步骤；2026-10-10 的 Ubuntu 24.04 ARM64 实际部署见[服务器部署记录](../../docs/refactor/ubuntu-server-deployment-20261010.md)。其他服务器仍须分别完成现场验收。

## 1. 先识别服务器，再安装依赖

以下命令只显示系统版本和架构，可以帮助找回遗忘的服务器信息：

```bash
cat /etc/os-release
uname -m
dotnet --list-runtimes
psql --version
systemctl --version
```

需要 `Microsoft.AspNetCore.App 10.x` 和 `Microsoft.NETCore.App 10.x`、PostgreSQL 18 服务和客户端、uv、curl、systemd 和 Caddy。运行发布包不需要 Node.js 或 .NET SDK。x86_64 / aarch64 的 Python 二进制依赖须在对应目标机安装。

按实际 Ubuntu 版本使用 [Microsoft 的安装说明](https://learn.microsoft.com/en-us/dotnet/core/install/linux-ubuntu-install)，不要混用 .NET 软件源；按 [PostgreSQL 的 Ubuntu 说明](https://www.postgresql.org/download/linux/ubuntu/)安装 18。包中检查程序不自动改动软件源、不升级已有数据库。已有服务器应先核实它承载的其他应用。

## 2. 解包和检查

首次部署创建独立 `quant` 服务账号，HOME 为 `/var/lib/quant`，由 quant 拥有并可写，以保存 uv 的 Python 运行时和缓存。uv 放在该身份可使用的公共工具路径。版本目录使用新的名字；不要覆盖已运行的版本。以管理员创建 `/opt/quant/releases/版本号`，将发布包内容解压到该目录，再将这个新版本目录的所有者设置为 `quant:quant`。

```bash
cd /opt/quant/releases/版本号
sha256sum -c SHA256SUMS
bash deploy/prepare-linux.sh --check
sudo -H -u quant bash deploy/prepare-linux.sh --sync
sudo chown -R root:quant /opt/quant/releases/版本号
sudo chmod -R g+rX,g-w,o-rwx /opt/quant/releases/版本号
```

`--check` 只检查文件和运行工具，`--sync` 用 `uv.lock` 在当前版本的最终路径安装 Python 3.12。首次安装需要访问 Python/软件包下载源；无网络时须先准备对应架构的缓存。不要把其他机器或其他路径的 `.venv` 搬过来。准备好后将版本代码归 root 所有、目录可供 quant 读取和进入、文件只读，运行时数据始终放在独立数据目录。

systemd 示例使用 `/usr/bin/dotnet`。检查 `command -v dotnet`；路径不同须先调整 unit 中的 `ExecStart` 和 `ExecStartPre`。`QUANT_PG_BIN` 可指向 PostgreSQL 客户端目录。`--check` 通过不表示数据库连接、证书或 systemd 故障恢复已经通过。

## 3. 数据库、目录及受保护配置

新站点创建专用 PostgreSQL 角色 `quant` 和空库 `quant_assistant`；迁移旧站点遵照原生迁移手册，不覆盖已有业务库。数据库仅监听本机，密码通过 PostgreSQL 交互命令设置，禁止直接放在命令参数中。

```bash
sudo install -d -o root -g quant -m 0750 /etc/quant
sudo install -d -o quant -g quant -m 0700 /var/lib/quant/site /var/lib/quant/private /var/lib/quant/backups /var/log/quant
sudo install -o root -g quant -m 0640 deploy/environment.example /etc/quant/environment
sudoedit /etc/quant/environment
```

填写实际数据库连接、正式域名（主机名，不带协议/路径）和随机的至少 32 字符 worker 密钥。`.NET` 连接串须具备真实认证方式；标准 PG 工具推荐使用 quant 拥有的 `0600` 权限 `.pgpass`，并设置 `PGPASSFILE`。两套连接配置必须指向同一个业务库。系统服务不依赖交互用户的 HOME。

`/etc/quant/environment` 是 **systemd EnvironmentFile**，含分号的连接串不能直接当 shell 脚本 `source`。下面用 `systemd-run` 读取受保护配置。内部接口和 worker 认证不公开到互联网。

## 4. 初始化并安装服务

在服务尚未安装启动时，将 `/opt/quant/current` 指向准备好的版本目录。升级时先按第 7 节停服务并备份，再切换链接。

```bash
sudo ln -s /opt/quant/releases/版本号 /opt/quant/current
sudo systemd-run --wait --pipe --collect -p User=quant -p Group=quant -p EnvironmentFile=/etc/quant/environment /usr/bin/dotnet /opt/quant/current/api/QuantAssistant.Api.dll --migrate /opt/quant/current/migrations
```

全新站点才初始化管理员，密码由控制台隐藏输入；导入旧站账户时跳过：

```bash
sudo systemd-run --wait --pty --collect -p User=quant -p Group=quant -p EnvironmentFile=/etc/quant/environment /usr/bin/dotnet /opt/quant/current/api/QuantAssistant.Api.dll --init-admin 管理者用户名
sudo install -m 0644 deploy/quant-api.service deploy/quant-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quant-api quant-worker@1 quant-worker@2 quant-worker@3
curl --fail http://127.0.0.1:8765/readyz
```

API unit 拉起三个 worker；worker 跟随 API 的停止和重启。单个 worker 异常退出由自身 `Restart=on-failure` 恢复。修改 worker 数量时，必须同时修改 API 的 `Wants`、已启用的 worker 实例和 `QUANT_EXPECTED_WORKERS`，不能只改其中一个。数据库服务 unit 若不是 `postgresql.service`，须相应调整 API 的依赖。

## 5. 使用现有域名和证书

使用 `Caddyfile.existing-cert`，替换示例域名和证书路径。证书及私钥放到版本目录之外：例如 root 拥有的 `/etc/caddy/quant-tls`，目录 `0750`、私钥 `0640`，组为 Caddy 的服务组，使 Caddy 能读取而普通用户不能读取；同时检查所有父目录可供 Caddy 进入。不要把证书放入仅允许 quant 进入的私密配置目录。若已有 Caddy 站点，应将新站点配置合并到原配置，不能覆盖其他站点。

先以 Caddy 的实际服务身份验证可读性和配置，成功后再 reload；域名 DNS 必须指向目标服务器，80/443 按实际网络配置开放。参考 [Caddy 手动证书配置](https://caddyserver.com/docs/caddyfile/directives/tls)。手动证书需接入已有续期流程，续期后校验并 reload Caddy。

```bash
sudo -u caddy test -r /etc/caddy/quant-tls/fullchain.pem
sudo -u caddy test -r /etc/caddy/quant-tls/privkey.pem
sudo openssl x509 -in /etc/caddy/quant-tls/fullchain.pem -noout -enddate
sudo openssl x509 -in /etc/caddy/quant-tls/fullchain.pem -noout -checkend 604800
sudo -u caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
curl --fail https://实际域名/readyz
```

HTTPS 登录后核实安全 Cookie、普通用户隔离以及管理页就绪报告。公网请求 `/internal/v1/worker/...` 和 `/openapi.json` 应返回 404。现有证书是否有效、匹配域名、完整链和自动续期仍须在该服务器核实。

## 6. 备份与真实恢复

先等待任务与费用预留结束，再停服务。停止 API 会同步停止三个 worker。工具要求站点停止，以避免数据库和文件跨时刻不一致。

```bash
sudo systemctl stop quant-api
sudo systemd-run --wait --pipe --collect -p User=quant -p Group=quant -p EnvironmentFile=/etc/quant/environment --setenv=QUANT_PYTHON=/opt/quant/current/backend/.venv/bin/python /usr/bin/bash /opt/quant/current/deploy/backup.sh /var/lib/quant/backups
sudo systemctl start quant-api
curl --fail http://127.0.0.1:8765/readyz
```

备份失败也应检查原因并恢复原服务；维护操作期间有人值守。2026-10-09 发布包提供手工停服归档。当前源码另有 `backup-linux.py`、`quant-backup.service` 和 `quant-backup.timer`，旧包启用定时备份时须补充这三个文件：

```bash
sudo install -d -o root -g root -m 0755 /usr/local/lib/quant
sudo install -o root -g root -m 0644 deploy/backup-linux.py /usr/local/lib/quant/backup-linux.py
sudo install -o root -g root -m 0644 deploy/quant-backup.service deploy/quant-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo /opt/quant/current/backend/.venv/bin/python /usr/local/lib/quant/backup-linux.py --force
sudo systemctl enable --now quant-backup.timer
sudo systemctl start quant-backup.service
sudo systemctl list-timers quant-backup.timer
```

此补充脚本适用于本手册的固定服务名和目录，配置文件须为 root 所有的受限文件，使用未加引号的单行 `KEY=value`，不支持完整 shell/systemd 引号语法。每天北京时间 02、03、04 点为维护窗口，8 小时内已成功备份则跳过；存在排队/运行任务或费用预留时退出，下一窗口重试。归档时短暂停止 API 和 worker，正常失败及终止信号会尝试恢复服务；服务启动失败须人工处理。额外的私密恢复材料保存在 `/var/lib/quant/private-backups` 的 root 专用归档中，保留 14 份。这些本机私密归档尚未加密或复制到异地，不能替代加密异地备份。启用时必须实际验证服务恢复、归档权限和定时器状态。

创建由 quant 拥有的**另一个空库**和**尚不存在的目录**，用下面的命令演练恢复（替换归档文件名和目标库）：

```bash
sudo systemd-run --wait --pipe --collect -p User=quant -p Group=quant -p EnvironmentFile=/etc/quant/environment /usr/bin/env PGDATABASE=quant_restore_check /opt/quant/current/backend/.venv/bin/python /opt/quant/current/tools/site_archive.py restore /var/lib/quant/backups/选定归档.zip --destination /var/lib/quant/restored-check
```

`EnvironmentFile` 的值会覆盖 `systemd-run --setenv` 中同名变量，因此恢复目标使用 `/usr/bin/env PGDATABASE=...` 在启动工具时明确覆盖；执行前确认库名和目标目录均为演练专用。

归档不包含模型密钥与 Data Protection 解密材料，独立私密目录另行加密备份。恢复后核实记录数、关键账本、文件哈希、重新登录、三个 worker 和归档绑定的 `.restore.json`；不要把恢复演练库自动切为正式库。

## 7. 升级、回退与现场验收

当前源码包含完整迁移 `0001`–`0023`，最近的 `0023_public_market_snapshot_guard.sql` 保护公开派生缓存。旧 FastAPI、`web/deploy/cloud_up.sh` 与容器/exchange 入口仅用于兼容和历史核验，不是本手册的生产入口。后台刷新在每轮结束后默认等待 30 秒（15–300 秒），整体/任务预算默认 75/55 秒，详见[架构配置](../ARCHITECTURE.md)。当前独立修复分支尚未部署，本文提供操作契约，不表示服务器验收已完成。

先准备新目录并完成第 2 节，再停服务、备份，记录当前链接目标，然后原子切换：

```bash
readlink -f /opt/quant/current
sudo systemctl stop quant-api
# 完成并核验备份后执行；目标版本号须是已准备好的目录。
sudo ln -s /opt/quant/releases/新版本号 /opt/quant/current.next
sudo mv -T /opt/quant/current.next /opt/quant/current
sudo systemd-run --wait --pipe --collect -p User=quant -p Group=quant -p EnvironmentFile=/etc/quant/environment /usr/bin/dotnet /opt/quant/current/api/QuantAssistant.Api.dll --migrate /opt/quant/current/migrations
sudo systemctl start quant-api
curl --fail http://127.0.0.1:8765/readyz
```

迁移成功后才启动新服务；迁移失败时保留现场，按所涉及迁移的兼容性决定恢复或回切。同结构且已经验证兼容的版本可用相同步骤切回已记录的旧版本。包含不向后兼容的数据库迁移时，不能只切程序链接；先将备份恢复到新空库和目录后再决定回切。当前迁移目录已至 0023，不能沿用旧版本“没有新增迁移”的结论；升级及恢复验收须对旧版→新版→恢复副本核对财务指纹，公开缓存保护迁移不替代该核验。

目标服务器仍需完成：实际域名 HTTPS、服务账号权限、数据库重启、API/worker 崩溃恢复、开机启动、日志和磁盘容量、备份恢复、证书续期及至少 24 小时观察。本包只交付部署准备，本机短时测试不替代这些结果。管理页的主机部署就绪和策略证据就绪分别评估，不完整的真实研究证据继续保持阻断。
