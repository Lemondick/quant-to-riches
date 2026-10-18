# 原生常驻运行与发布回滚

本次只完成本机隔离短时验收。正式Windows服务需要管理员安装、独立身份和服务器证书；未在本机注册生产服务，也未完成24小时连续运行。Linux继续使用本目录的systemd单元。

## Windows

1. 用`publish.ps1`发布至新的不可变目录。产物包含`api`、`service`、`worker`、`backend`、`migrations`和`deploy`。安装前端依赖会执行固定版本的兼容安全补丁；发布脚本还会验证恶意SSR属性名和深层brace输入的拦截，补丁或验证失败会阻止发布，不能使用`--ignore-scripts`绕过。
2. 在源码和普通站点备份之外建立受保护的配置目录；参考`native-service.example.json`，填写绝对路径、真实连接和独立日志目录。配置不进入Git。
3. 设置系统范围的`DOTNET_ROOT`指向服务使用的.NET 10安装目录，确保服务身份可读取.NET和Python基础运行时；不要依赖交互用户的PATH。
4. 管理员运行`install-native-service.ps1 -Config <配置文件>`。安装器先校验配置，使用`NT SERVICE\QuantAssistantNative`虚拟身份，设置目录权限、延迟自动启动、5/15/60秒失败恢复；安装后不会立即启动。
5. PostgreSQL必须独立常驻并使用专用数据库账号。执行`Start-Service QuantAssistantNative`，检查`/readyz`、执行器心跳、日志和管理页面。
6. Windows DPAPI密钥绑定机器与进程身份。原交互账号的密钥不能直接复用；在新服务运行后通过各账户的连接设置重新提交密钥，模型发现成功后自行选择角色模型，再启用报告。不得为了复用密钥改用LocalSystem或降低目录权限。
7. 外部HTTPS可用`Caddyfile.native`自动证书和反向代理；API保持本机监听，代理隐藏`/internal/*`，证书/域名按正式环境配置。
8. 正常停止用`Stop-Service`；适配器终止它拥有的完整进程树。进行中的付费请求费用可能未知，恢复后会中断旧任务，不自动重复扣费请求。发布前先等待队列空闲。

卸载时先停止，再`sc.exe delete QuantAssistantNative`；站点、密钥、备份和日志保留，不自动删除。

## 发布切换及回滚

先完成备份及恢复验证，并确认当前队列空闲。记录受保护配置与`Get-CimInstance Win32_Service`中的原`PathName`；旧发布目录始终保留。

停止服务，修改受保护JSON中的`release`到新产物；通过`sc.exe config`同时更新服务程序路径为新产物`service/QuantAssistant.NativeService.exe --config <原配置文件>`。使用`--validate-config`先校验，然后启动并检查HTTPS、`/readyz`、登录、账本读取及运行状态。迁移失败会阻止启动，不会先启动worker。

新版本验收失败时停止服务，恢复原受保护配置与服务程序路径，再启动旧版本并核对上述功能。**数据库不自动降级**：0015–0019仅新增运维/费用字段、扩展归因来源和预览索引；0019增加可空的账单证据JSON列，旧版读取原有字段仍兼容，但旧版页面不会展示新账单依据。之后的迁移必须单独确认旧代码兼容性。若存在不兼容迁移，恢复已验证的完整备份至隔离环境，核对新增业务记录后再安排切换，不能直接覆盖当前财务库。

Linux切换`/opt/quant/current`到已验收的发布目录，停/启API和worker单元；回滚同样保留数据库兼容性边界。

## 上线条件检查

账户设置中的“上线与策略验收”读取`GET /api/v1/operations/readiness`。`ready_for_hosting`检查备份恢复、交易边界、实际HTTPS请求与配置域名一致、worker就绪；`ready_for_strategy`检查账户基线、费用、历史数据、回测、回撤和模拟运行。严格的`ready_for_deployment`还要求AI费用证据等全部检查通过，保留原有判定。

本机loopback验收不满足正式域名条件。正式服务安装身份、开机恢复及持续运行仍需现场验收，接口的通过结果不能替代这些步骤。历史调用缺费用依据时，可在费用面板逐笔录入真实服务商账单并明确确认；后补未来单价不会自动计算历史费用，没有账单继续保持未知。

## 保留和监控

API与宿主日志按日及20MB轮转，各保留最多30个文件。API配置`QUANT_LOG_DIR`启用；Linuxworker标准输出由journald保留策略管理。

运行维护每分钟采样、每小时最多清理1000份过期一天的未确认预览和1000份30天前的公开行情查询任务；监控采样保留14天。确认凭据、交易/分红/资金事件、行情来源、付费任务、研究/归因报告及重放任务保留。`QUANT_DISABLE_MAINTENANCE=true`可停用维护。

管理页面P95使用最近5分钟最多4096个公开请求，排除内部worker轮询；进程重启清空窗口。数据库计数取当前数据库的统计视图，部分活动统计受数据库权限影响，缺少权限时不能当成零故障证据。正式环境的数据库角色需要`pg_read_all_stats`用于完整活动统计，此权限只授给运行账号，不授给Web用户。
