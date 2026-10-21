# 归档（archive）

本目录存放已从 `web/backend/src/quant_assistant/` 移出的**无人引用**旧模块，
仅保留 git 历史备查，**不参与构建、不随生产包发布**。

- 移入前已做全仓库引用扫描（`web/worker`、`web/backend/src`、`web/backend/scripts`、
  `web/backend-dotnet/tools`，含相对导入与字符串引用解析），确认无任何引用。
- `publish.ps1` 发布生产包时明确排除本目录。
- 如需恢复：`git mv web/backend/archive/quant_assistant/<模块>.py web/backend/src/quant_assistant/<模块>.py`，
  并补回引用与测试。

## 移入记录

| 日期 | 模块 | 说明 |
|---|---|---|
| 2026-10-21 | `quant_assistant/cli_account.py` | 旧 CLI 账号子命令，无人引用（入口为 `quant_assistant.cli`） |
| 2026-10-21 | `quant_assistant/cli_analysis.py` | 旧 CLI 分析子命令，无人引用 |
| 2026-10-21 | `quant_assistant/cli_data.py` | 旧 CLI 数据子命令，无人引用 |
| 2026-10-21 | `quant_assistant/cli_system.py` | 旧 CLI 系统子命令，无人引用 |
