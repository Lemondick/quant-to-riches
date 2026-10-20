# 模型网络边界与当前部署契约

本文描述独立 `codex/model-network-boundary` 分支的源码，不表示已部署、已合并 main 或已验证实际付费模型。现有自定义兼容 API 功能允许公开 HTTPS 443 域名，未限制为固定供应商；不允许通过模型配置访问内部网络。

## 实际请求边界

- .NET `PublicModelCatalog` 在发现时解析并校验全部目标，固定 TCP 目标地址，原域名由 HTTPS 客户端继续用于证书校验。原生 `web/worker/public_model_http.py` 保留已有公开 IP 连接池、原域名 SNI/Host、无代理与无重定向设计。
- 旧 `integrations/tradingagents_worker/main.py` 虽不再承担原生业务 API，仍可作为兼容研究子进程运行。它在每次实际连接时重新校验全部解析结果；任一非公开地址使整组失败。校验后用数值 sockaddr 直接连接，不再对域名二次解析；TLS 使用默认受信任根及原始主机名。
- 拒绝回环、私网、link-local、共享地址、组播、保留/文档地址和无法确定公开范围的目标；IPv4-mapped IPv6 按嵌入 IPv4 检查。NAT64、6to4、Teredo、已退役 IPv4 过渡中继不作为模型目标。特殊地址分类参照 [IANA IPv4](https://www.iana.org/assignments/iana-ipv4-special-registry) 与 [IANA IPv6](https://www.iana.org/assignments/iana-ipv6-special-registry)；.NET 的 IETF 父前缀按已登记的公开子分配例外处理，不把测试/文档网段视为公网。
- URL 只支持 HTTPS 443、无用户信息/查询/片段、简单 ASCII 路径段；拒绝百分号转义、反斜线、控制字符、点路径及重复斜线。兼容 `/`、`/v1`、`/api/v1` 等简单前缀；旧适配器也保留完整 `/chat/completions` 形式。任意编码路径不属于当前支持范围，不能通过放宽地址检查解决。
- 连接失败只在发送 POST 前尝试下一条已经验证的公开地址，共用一次 TCP/TLS 预算。旧适配器保持最多一次明确 429 重试，每次重试重新验证 DNS；超时或响应失败不重放付费 POST，也不跟随重定向。DNS 返回时间计入本轮剩余预算；操作系统 DNS 调用本身由既有父进程任务期限兜底，未新增绕过监督的解析线程。
- 无密钥时不解析、不连接。错误只返回稳定代码，禁止回显含凭据 URL、Authorization 或 Key；旧适配器同时拒绝模型把实际 Key 回显进输出。测试中的密钥、DNS、TCP/TLS 和响应全部为离线合成输入，没有真实模型费用或内部地址访问。

标准库连接遵循 [Python SSLContext 的原主机名与默认验证](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext.wrap_socket) 及 [socket 的数值地址连接接口](https://docs.python.org/3.12/library/socket.html#socket.socket.connect)。部署网络的额外出口限制可继续收紧访问范围；它不替代连接时验证。地址分配或运行时分类发生变更时须重新核验策略，不能把一次发现结果永久信任。

## 验证记录

源码提交 `8b9a2e3c08a7c5434ee6526c679e99bce740643f`：先复现旧适配器 URL/DNS/TLS 缺陷、原生过渡地址/路径缺陷及 .NET 配置与特殊网段缺陷，再修复。另先复现模型回显密钥进入输出，再增加拒绝。

本地实际通过：网络专项 15/15、.NET 模型目录 30/30、原生 Worker 128/128，source verify、ruff、format、secret scan 通过。旧研究子进程专项为 18 项、17 通过、1 项 Linux parent-death 保护在 Windows 跳过；该跳过不算通过。[Linux CI 39424757711](https://github.com/Lemondick/quant-to-riches/actions/runs/39424757711) 已在该源码提交实际执行：Python 492、.NET/PostgreSQL 385、Worker 128、工具 15、报告 5 全通过且 0 跳过，前端/浏览器/构建通过。最终整体审查见[阶段实施记录](stability-implementation-20261018.md)，审查完成前保持阶段进行中。

## 部署契约

当前原生入口为 ASP.NET Core 10、PostgreSQL 18、Python 3.12 Worker 与原 Vue 3 工作台。旧 FastAPI、容器/exchange 和 `web/deploy/cloud_up.sh` 只保留兼容或历史恢复核验。实际部署使用 `backend-dotnet/deploy`；完整迁移为 `0001`–`0023`，不是只执行历史性能更新的 0020/0021。

后台每轮完成后默认等待 30 秒（范围 15–300），整体预算 75 秒、任务预算 55 秒；失败保留原来源时间与旧有效快照。发布、恢复、回退、文件哈希和财务指纹仍按[原生手册](p7-runbook.md)及[Ubuntu 手册](../../backend-dotnet/deploy/ubuntu-runbook.md)执行。未修改备份/恢复实现，未执行正式服务器迁移、真实账本写入或交易。
