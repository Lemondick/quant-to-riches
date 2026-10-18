# 网页部署

日常本机启动使用仓库根目录`启动网页版.ps1`。
这里保留API、网页Caddy代理和可选研究进程配置，不包含旧客户端部署。

| 文件 | 用途 |
| --- | --- |
| `docker-compose.yml` | API及网页Caddy，持久化数据卷 |
| `docker-compose.research.yml` | 可选研究进程，默认不叠加 |
| `Dockerfile.web` | 构建网页并复制到Caddy |
| `Dockerfile.research-worker` | 可选研究进程 |
| `Caddyfile` | HTTPS、API转发、静态网页 |
| `../Dockerfile` | 后端运行镜像 |
| `.env.example` | 非敏感部署模板 |
| `cloud_up.sh` | Linux配置校验和启动 |

Compose配置检查通过。当前电脑Docker服务未启动，未验证镜像构建、容器启动及公网访问。
本机网页版的构建和启动结果不能代替容器上线验证。

## 准备与校验

Docker/Linux主机上复制`.env.example`到本目录`.env`，设置真实域名。
所有业务接口使用账号会话；不再配置共享访问Token。Compose默认允许当前域名的精确HTTPS来源，自定义`QUANT_CORS_ORIGINS`时不能使用`*`。
模型配置及Key由各用户在登录后的设置页提供，不写入源码或镜像。

```sh
sh web/deploy/cloud_up.sh --check
```

此入口只校验配置，不启动容器。
只检查已有Compose配置时使用：

```sh
docker compose --env-file web/deploy/.env -f web/deploy/docker-compose.yml config --quiet
```

准备好部署环境后再构建、启动并检查健康、认证、数据持久化及恢复。
启动入口为`sh web/deploy/cloud_up.sh --apply`，本次任务未执行部署。

## 数据与模型

账号库与全部用户数据在`quant-data`卷。容器启动后必须在服务器终端交互初始化管理者，随后最多允许30个总账号；命令见[登录与用户管理](../docs/登录与用户管理.md)。
管理者保留原根目录数据；普通用户使用隔离子目录，在设置页分别保存兼容API和Jev官方Key。旧管理者私有配置仍可由受控文件提供，基础Compose不会复制本机密钥。
OCR配置需按实际环境传入容器；`.env`字段只有被Compose引用/传递后才影响服务。
迁移账户前停服务并备份，不要以空卷覆盖已有数据。

可选研究配置使用交换模式，默认示例是mock。mock不能证明实际模型或Jev可用。
系统09:30/14:30调度由API负责，不再添加另一套外部Cron任务。
检查`/healthz`、`/readyz`和受保护API，备份步骤见[维护说明](../docs/维护说明.md)。
