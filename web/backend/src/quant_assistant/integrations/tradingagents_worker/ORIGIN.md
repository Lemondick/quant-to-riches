# 来源与修改

上游：https://github.com/simonlin1212/TradingAgents-astock

固定提交：926de6e6308047e02c4d8110838fd2d2125dceb6

参考角色：`tradingagents/agents/researchers/bull_researcher.py`、
`bear_researcher.py`、`tradingagents/agents/managers/research_manager.py`。

2026-09-21：选择性改写多空辩论与研究经理流程，改为三个顺序调用，
仅接收本项目冻结量价事实；删除新闻、估值、资金流、记忆、交易评级和工具调用。
提示词及运行时已经修改，并非完整上游系统。保留 LICENSE 与 NOTICE。

运行环境：Python 3.11/3.12 标准库，零第三方运行依赖。
本机建议 `python -m venv .venv-agent`，将其解释器设置为 QUANT_AGENT_PYTHON。
`requirements.lock` 为空依赖锁；无需安装完整 TradingAgents。
