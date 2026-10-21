"""Named computation functions, not an HTTP proxy to the legacy business app."""

from __future__ import annotations

from advisor_preflight import execute_preflight
from attribution_operations import ATTRIBUTION_CAPABILITIES, execute_attribution
from calendar_generation import CALENDAR_CAPABILITIES, execute_calendar
from smart_dca_backtest_operations import DCA_CAPABILITIES, execute_smart_dca
from portfolio_backtest_operations import PORTFOLIO_CAPABILITIES, execute_portfolio_backtest
from catalog_validate_operations import CATALOG_VALIDATE_CAPABILITIES, execute_catalog_validate
from nav_operations import NAV_CAPABILITIES, execute_nav
from paid_operations import PAID_CAPABILITIES, execute_paid
from photo_operations import PHOTO_CAPABILITIES, execute_photo
from public_operations import IN_PROCESS_CAPABILITIES, execute_public
from read_operations import READ_CAPABILITIES, execute_read
from redemption_operations import REDEMPTION_CAPABILITIES, execute_redemption
from replay_operations import REPLAY_CAPABILITIES, execute_replay
from research_operations import RESEARCH_CAPABILITIES, execute_research
from risk_operations import RISK_CAPABILITIES, execute_risk
from strategy_operations import STRATEGY_CAPABILITIES, execute_strategy

from quant_assistant.reporting.fund_execution_study import replay as replay_fund_orders

CAPABILITIES = (
    "advisor.preflight",
    "fund.execution_replay",
    *IN_PROCESS_CAPABILITIES,
    *READ_CAPABILITIES,
    *STRATEGY_CAPABILITIES,
    *PAID_CAPABILITIES,
    *RESEARCH_CAPABILITIES,
    *PHOTO_CAPABILITIES,
    *NAV_CAPABILITIES,
    *REPLAY_CAPABILITIES,
    *ATTRIBUTION_CAPABILITIES,
    *CALENDAR_CAPABILITIES,
    *REDEMPTION_CAPABILITIES,
    *DCA_CAPABILITIES,
    *PORTFOLIO_CAPABILITIES,
    *CATALOG_VALIDATE_CAPABILITIES,
    *RISK_CAPABILITIES,
)


def execute(kind: str, payload: dict) -> dict:
    if kind not in CAPABILITIES:
        raise ValueError("unsupported_operation")
    if kind in IN_PROCESS_CAPABILITIES:
        return execute_public(kind, payload)
    if kind == "advisor.preflight":
        return execute_preflight(payload)
    if kind == "fund.execution_replay":
        return replay_fund_orders(payload["bundle"])
    if kind in READ_CAPABILITIES:
        return execute_read(kind, payload)
    if kind in STRATEGY_CAPABILITIES:
        return execute_strategy(kind, payload)
    if kind in PAID_CAPABILITIES:
        return execute_paid(kind, payload)
    if kind in RESEARCH_CAPABILITIES:
        return execute_research(kind, payload)
    if kind in PHOTO_CAPABILITIES:
        return execute_photo(kind, payload)
    if kind in NAV_CAPABILITIES:
        return execute_nav(kind, payload)
    if kind in REPLAY_CAPABILITIES:
        return execute_replay(kind, payload)
    if kind in ATTRIBUTION_CAPABILITIES:
        return execute_attribution(kind, payload)
    if kind in CALENDAR_CAPABILITIES:
        return execute_calendar(kind, payload)
    if kind in REDEMPTION_CAPABILITIES:
        return execute_redemption(kind, payload)
    if kind in DCA_CAPABILITIES:
        return execute_smart_dca(kind, payload)
    if kind in PORTFOLIO_CAPABILITIES:
        return execute_portfolio_backtest(kind, payload)
    if kind in CATALOG_VALIDATE_CAPABILITIES:
        return execute_catalog_validate(kind, payload)
    if kind in RISK_CAPABILITIES:
        return execute_risk(kind, payload)
    raise ValueError("unsupported_operation")
