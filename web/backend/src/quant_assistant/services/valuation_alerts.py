"""Fail-closed fund valuation anomaly scanning.

The scanner prefers a read-only intraday estimate and falls back to the latest
official NAV.  It never writes either value into the ledger, never creates an
order, and keeps unavailable data distinct from a normal observation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from quant_assistant.models.entities import AssetType
from quant_assistant.providers.fund_valuation import (
    FallbackFundValuationProvider,
    FundIntradayEstimate,
    FundValuationProvider,
)
from quant_assistant.services.notifications import (
    NotificationResult,
    NotificationStatus,
    format_valuation_alert_digest,
    send_optional_notification,
)
from quant_assistant.services.operations import OperationalAlertService
from quant_assistant.services.portfolio_commands import PositionInput


class ValuationAlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ValuationAlertConfig:
    """Thresholds and notification policy expressed in percentage points."""

    warning_change_rate_percent: Decimal = Decimal("1.50")
    critical_change_rate_percent: Decimal = Decimal("3.00")
    info_change_rate_percent: Decimal | None = None
    silence_minutes: int = 30

    def __post_init__(self) -> None:
        for name in ("warning_change_rate_percent", "critical_change_rate_percent"):
            value = _decimal(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.info_change_rate_percent is not None:
            info = _decimal(self.info_change_rate_percent, "info_change_rate_percent")
            if info <= 0:
                raise ValueError("info_change_rate_percent must be positive")
            if info > self.warning_change_rate_percent:
                raise ValueError("info threshold must not exceed warning threshold")
            object.__setattr__(self, "info_change_rate_percent", info)
        if self.warning_change_rate_percent > self.critical_change_rate_percent:
            raise ValueError("warning threshold must not exceed critical threshold")
        if isinstance(self.silence_minutes, bool) or not isinstance(self.silence_minutes, int):
            raise ValueError("silence_minutes must be an integer")
        if not 0 <= self.silence_minutes <= 1440:
            raise ValueError("silence_minutes must be between 0 and 1440")

    @property
    def silence_window(self) -> timedelta:
        return timedelta(minutes=self.silence_minutes)


@dataclass(frozen=True, slots=True)
class FundValuationAlert:
    """One threshold breach tied to a provider observation."""

    symbol: str
    change_rate: Decimal
    estimate_nav: Decimal
    previous_nav: Decimal
    observed_at: datetime
    source: str
    severity: ValuationAlertSeverity
    alert_key: str

    @property
    def direction(self) -> str:
        return "上行" if self.change_rate > 0 else "下行"

    @property
    def fingerprint(self) -> str:
        """Stable event identity used to suppress repeated notifications."""

        return f"{self.symbol}:{self.severity.value}:{self.direction}"

    @property
    def message(self) -> str:
        return (
            f"基金{self.symbol}估值/净值{self.direction}{abs(self.change_rate):.2f}%"
            f"，达到{self.severity.value}级阈值；仅供参考"
        )


@dataclass(frozen=True, slots=True)
class ValuationScanResult:
    """Structured result used by the CLI and scheduler."""

    alerts: tuple[FundValuationAlert, ...]
    unavailable: tuple[str, ...]
    notification: NotificationResult
    suppressed: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return not self.unavailable and self.notification.status.value != "failed"


def detect_valuation_alerts(
    estimates: Iterable[FundIntradayEstimate],
    *,
    config: ValuationAlertConfig | None = None,
) -> tuple[FundValuationAlert, ...]:
    """Return deterministic alerts for estimates at or above a threshold."""

    selected = config or ValuationAlertConfig()
    alerts: list[FundValuationAlert] = []
    seen: set[str] = set()
    for estimate in estimates:
        if not isinstance(estimate, FundIntradayEstimate):
            raise TypeError("估值扫描必须使用FundIntradayEstimate")
        if (
            estimate.valuation_type != "intraday_estimate"
            or estimate.estimate_nav is None
            or estimate.change_rate is None
            or estimate.observed_at is None
            or estimate.quality_status != "valid"
        ):
            continue
        symbol = estimate.symbol.strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rate = _decimal(estimate.change_rate, "change_rate")
        magnitude = abs(rate)
        if magnitude >= selected.critical_change_rate_percent:
            severity = ValuationAlertSeverity.CRITICAL
        elif magnitude >= selected.warning_change_rate_percent:
            severity = ValuationAlertSeverity.WARNING
        elif (
            selected.info_change_rate_percent is not None
            and magnitude >= selected.info_change_rate_percent
        ):
            severity = ValuationAlertSeverity.INFO
        else:
            continue
        alerts.append(
            FundValuationAlert(
                symbol=symbol,
                change_rate=rate,
                estimate_nav=estimate.estimate_nav,
                previous_nav=estimate.previous_nav,
                observed_at=estimate.observed_at,
                source=estimate.source,
                severity=severity,
                alert_key=f"fund-valuation-tail:{symbol}",
            )
        )
    priority = {
        ValuationAlertSeverity.CRITICAL: 0,
        ValuationAlertSeverity.WARNING: 1,
        ValuationAlertSeverity.INFO: 2,
    }
    return tuple(sorted(alerts, key=lambda item: (priority[item.severity], item.symbol)))


class FundValuationAlertService:
    """Scan current local fund positions and persist operational alerts."""

    def __init__(
        self,
        operational_alerts: OperationalAlertService,
        *,
        provider: FundValuationProvider | None = None,
        notifier: object | None = None,
    ) -> None:
        if not all(
            callable(getattr(operational_alerts, name, None)) for name in ("raise_alert", "resolve")
        ):
            raise TypeError("operational_alerts must provide raise_alert() and resolve()")
        self._alerts = operational_alerts
        self._provider = provider or FallbackFundValuationProvider()
        self._notifier = notifier

    def scan(
        self,
        positions: Iterable[PositionInput],
        *,
        config: ValuationAlertConfig | None = None,
        notify: bool = True,
    ) -> ValuationScanResult:
        """Scan positive fund quantities; non-fund rows are ignored."""

        estimates: list[FundIntradayEstimate] = []
        unavailable: list[str] = []
        fund_symbols: list[str] = []
        for position in positions:
            if not isinstance(position, PositionInput):
                raise TypeError("估值扫描持仓必须使用PositionInput")
            if position.asset_type is not AssetType.FUND or position.quantity <= 0:
                continue
            symbol = position.symbol.strip().upper()
            if symbol in fund_symbols:
                continue
            fund_symbols.append(symbol)
            try:
                estimates.append(self._provider.estimate(symbol))
            except Exception:
                unavailable.append(symbol)

        selected_config = config or ValuationAlertConfig()
        alerts = detect_valuation_alerts(estimates, config=selected_config)
        alert_symbols = {item.symbol for item in alerts}
        estimate_symbols = {item.symbol.strip().upper() for item in estimates}
        for symbol in unavailable:
            self._alerts.raise_alert(
                f"fund-valuation-unavailable:{symbol}",
                "warning",
                f"基金{symbol}估值及最新净值暂不可用",
                {"symbol": symbol, "reason": "provider_unavailable"},
            )
        for symbol in estimate_symbols:
            if symbol not in unavailable:
                self._alerts.resolve(f"fund-valuation-unavailable:{symbol}")
        notification_candidates: list[FundValuationAlert] = []
        suppressed: list[str] = []
        for item in alerts:
            self._alerts.raise_alert(
                item.alert_key,
                item.severity.value,
                item.message,
                {
                    "symbol": item.symbol,
                    "change_rate_percent": str(item.change_rate),
                    "estimate_nav": str(item.estimate_nav),
                    "previous_nav": str(item.previous_nav),
                    "observed_at": item.observed_at.isoformat(),
                    "source": item.source,
                    "fingerprint": item.fingerprint,
                    "silence_minutes": selected_config.silence_minutes,
                },
            )
            due = getattr(self._alerts, "notification_due", None)
            if callable(due) and not due(
                item.alert_key,
                item.fingerprint,
                silence_window=selected_config.silence_window,
            ):
                suppressed.append(item.symbol)
            else:
                notification_candidates.append(item)
        for estimate in estimates:
            if estimate.symbol.strip().upper() not in alert_symbols:
                self._alerts.resolve(f"fund-valuation-tail:{estimate.symbol.strip().upper()}")

        notification = NotificationResult("operator", NotificationStatus.SKIPPED, "未请求通知")
        if notify and alerts and not notification_candidates:
            notification = NotificationResult(
                "operator",
                NotificationStatus.SKIPPED,
                "所有估值预警仍处于静默窗口，未重复发送",
            )
        elif notify:
            notification = send_optional_notification(
                self._notifier,
                format_valuation_alert_digest(notification_candidates or alerts),
                title="量化助手 基金估值/净值异动预警",
            )
            if notification.status is NotificationStatus.SENT:
                mark_notified = getattr(self._alerts, "mark_notified", None)
                if callable(mark_notified):
                    for item in notification_candidates:
                        mark_notified(
                            item.alert_key,
                            item.fingerprint,
                            silence_window=selected_config.silence_window,
                        )
            notification_key = "notification:operator:valuation-tail"
            if notification.status is NotificationStatus.FAILED:
                self._alerts.raise_alert(
                    notification_key,
                    "warning",
                    "基金估值/净值预警通知失败",
                    {"channel": notification.channel},
                )
            else:
                self._alerts.resolve(notification_key)
        return ValuationScanResult(
            tuple(alerts),
            tuple(sorted(unavailable)),
            notification,
            tuple(sorted(set(suppressed))),
        )


def _decimal(value: object, label: str) -> Decimal:
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是有限数字") from exc
    if not converted.is_finite():
        raise ValueError(f"{label}必须是有限数字")
    return converted


__all__ = [
    "FundValuationAlert",
    "FundValuationAlertService",
    "ValuationAlertConfig",
    "ValuationAlertSeverity",
    "ValuationScanResult",
    "detect_valuation_alerts",
]
