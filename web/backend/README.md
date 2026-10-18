# 网页后端

> 当前原生业务入口位于 `web/backend-dotnet`，此目录提供复用的 Python 计算库及旧站兼容代码。以下旧站维护说明保留为兼容参考；当前部署以 [原生手册](../docs/refactor/p7-runbook.md) 为准。


Python/FastAPI后端，使用MVC加业务服务层。

```text
src/quant_assistant/controllers/          HTTP接口、输入输出模型、认证限流
src/quant_assistant/models/entities.py    领域实体
src/quant_assistant/models/repositories/  DuckDB与存储契约
src/quant_assistant/services/             账户、基金、报告和调度业务
src/quant_assistant/analysis/             模型与研究证据
src/quant_assistant/providers/            行情、净值和OCR来源
scripts/                                 启动、质量检查、数据维护
```

安装运行依赖（仓库根目录）：

```powershell
uv sync --project web/backend --frozen --no-dev
.\启动网页版.ps1
```

维护检查：

```powershell
uv sync --project web/backend --frozen --dev
web/backend/.venv/Scripts/python.exe -B -X utf8 web/backend/scripts/verify.py
```

只需API时，显式指定账户目录后运行CLI：

```powershell
$env:QUANT_DATA_DIR = (Resolve-Path data).Path
web/backend/.venv/Scripts/python.exe -B -X utf8 -m quant_assistant.cli serve-api --port 8765
```

完整网页启动器自动对齐根目录账户和运行资源，日常首选启动器。
首次启动会提示交互创建管理者；仅启动API时先运行`scripts/manage_users.py init-admin`。所有HTTP入口均要求账号会话，注册总上限30人。详见[登录与用户管理](../docs/登录与用户管理.md)。
系统调度随API启动；休市不提醒，当前不发送微信，不自动交易。
测试源码及测试依赖已移除。静态检查通过与交易准入通过是不同结果。
架构、备份和命令见[维护说明](../docs/维护说明.md)。
