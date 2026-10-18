"""Value contracts and pure parsers for public fund detail."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from bs4 import BeautifulSoup

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.base import ProviderDataError
from quant_assistant.providers.data_quality import (
    ObservationMetadata,
)


@dataclass(frozen=True, slots=True)
class FundNavPoint:
    nav_date: date
    unit_nav: Decimal
    accumulated_nav: Decimal | None
    daily_return: Decimal | None
    source: str | None = None
    corporate_action: str | None = None
    return_verified: bool = False
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FundPeriodReturn:
    period: str
    return_rate: Decimal | None
    start_date: date | None = None
    end_date: date | None = None
    methodology: str = "unit_nav_ratio_without_corporate_actions"
    quality_status: str = "unavailable"
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FundHolding:
    symbol: str
    name: str
    asset_type: str
    weight: Decimal
    report_date: date | None


@dataclass(frozen=True, slots=True)
class FundManager:
    name: str
    work_years: Decimal | None
    tenure_start: date | None
    tenure_return: Decimal | None
    resume: str | None


@dataclass(frozen=True, slots=True)
class FundFee:
    fee_type: str
    condition: str
    rate: Decimal | None
    fixed_amount: Decimal | None = None
    discounted_rate: Decimal | None = None
    discount_channel: str | None = None
    source: str = "danjuan"
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class FundRuleField:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class FundTradingRules:
    fields: tuple[FundRuleField, ...]
    source: str
    source_url: str
    arrival_description: str | None = None


@dataclass(frozen=True, slots=True)
class FundAnnouncement:
    id: str
    title: str
    published_at: date
    url: str


@dataclass(frozen=True, slots=True)
class FundSourceHealth:
    """Observable status for each public fund-data source."""

    name: str
    available: bool
    consecutive_failures: int
    last_error: str | None
    last_success_at: datetime | None
    status: Literal["unchecked", "available", "unavailable"] = "unchecked"


@dataclass(frozen=True, slots=True)
class FundDetail(ObservationMetadata):
    symbol: str
    name: str
    full_name: str | None
    fund_type: str | None
    company: str | None
    established_on: date | None
    latest_scale: str | None
    benchmark: str | None
    investment_target: str | None
    nav_history: tuple[FundNavPoint, ...]
    period_returns: tuple[FundPeriodReturn, ...]
    holdings: tuple[FundHolding, ...]
    managers: tuple[FundManager, ...]
    fees: tuple[FundFee, ...]
    announcements: tuple[FundAnnouncement, ...]
    holdings_report_date: date | None
    observed_at: datetime
    source: str = "eastmoney+danjuan"
    stale: bool = False
    source_health: tuple[FundSourceHealth, ...] = ()
    purchase_status: str | None = None
    redemption_status: str | None = None
    trading_rules: FundTradingRules | None = None
    status_source: str | None = None
    field_metadata: dict | None = None
    nav_metadata: dict | None = None
    conflicts: tuple[str, ...] = ()


def _holdings(position: Mapping[str, object], report_date: date | None) -> tuple[FundHolding, ...]:
    items: list[FundHolding] = []
    for key, asset_type in (("stock_list", "stock"), ("bond_list", "bond")):
        rows = position.get(key)
        if not isinstance(rows, Sequence):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = _optional_text(row.get("code"))
            name = _optional_text(row.get("name"))
            weight = _optional_decimal(row.get("percent"))
            if symbol and name and weight is not None:
                items.append(FundHolding(symbol, name, asset_type, weight, report_date))
    items.sort(key=lambda item: item.weight, reverse=True)
    return tuple(items[:10])


def _managers(value: object, code: str) -> tuple[FundManager, ...]:
    if not isinstance(value, Sequence):
        return ()
    managers: list[FundManager] = []
    for row in value:
        if not isinstance(row, Mapping):
            continue
        tenure_start: date | None = None
        tenure_return: Decimal | None = None
        achievements = row.get("achievement_list")
        if isinstance(achievements, Sequence):
            current = next(
                (
                    item
                    for item in achievements
                    if isinstance(item, Mapping)
                    and str(item.get("fund_code") or "").strip() == code
                    and not item.get("resi_date")
                ),
                None,
            )
            if isinstance(current, Mapping):
                tenure_start = _optional_date(current.get("post_date"))
                tenure_return = _optional_decimal(current.get("cp_rate"))
        name = _optional_text(row.get("name"))
        if name:
            managers.append(
                FundManager(
                    name,
                    _optional_decimal(row.get("work_year")),
                    tenure_start,
                    tenure_return,
                    _optional_text(row.get("resume")),
                )
            )
    return tuple(managers[:5])


def _public_rules(
    raw: bytes, code: str
) -> tuple[tuple[FundFee, ...], FundTradingRules, dict[str, str]]:
    """Read titled public tables without assuming a fixed HTML table index."""

    soup = BeautifulSoup(raw.decode("utf-8-sig", errors="strict"), "html.parser")
    fees: list[FundFee] = []
    fields: list[FundRuleField] = []
    statuses: dict[str, str] = {}
    source_url = f"https://fundf10.eastmoney.com/jjfl_{code}.html"
    found = False
    for heading in soup.select("h4.t"):
        title = heading.get_text(strip=True)
        tables = []
        for sibling in heading.find_next_siblings():
            if sibling.name == "h4":
                break
            if sibling.name == "table":
                tables.append(sibling)
            else:
                tables.extend(sibling.find_all("table"))
        if not tables:
            continue
        if title in {"交易状态", "申购与赎回金额", "交易确认日"}:
            found = True
            for table in tables:
                for row in table.select("tr"):
                    cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                    for index in range(0, len(cells) - 1, 2):
                        label, value = cells[index : index + 2]
                        if label and value:
                            unknown = re.fullmatch(r"[-—]+(?:份|元|天)?", value) is not None
                            fields.append(
                                FundRuleField(label, "未知（公开页面未披露）" if unknown else value)
                            )
                            if label in {"申购状态", "赎回状态"} and not unknown:
                                statuses[label] = value
        elif title == "运作费用":
            found = True
            for row in tables[0].select("tr"):
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                for index in range(0, len(cells) - 1, 2):
                    label, value = cells[index : index + 2]
                    rate = _percent_rate(value)
                    if label:
                        fees.append(
                            FundFee(
                                label,
                                "按基金财产计提的年费率",
                                rate,
                                source="eastmoney",
                                source_url=source_url,
                            )
                        )
        elif title in {"申购费率", "申购费率（前端）", "赎回费率", "赎回费率（前端）"}:
            found = True
            for row in tables[0].select("tr"):
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                if len(cells) < 2:
                    continue
                # Preserve conditions and fixed CNY fees. The second displayed
                # price is Tiantian's channel price, never the user's Alipay fee.
                value = cells[-1]
                parts = value.replace("｜", "|").split("|")
                fixed = re.search(r"(?:每笔|每次)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", parts[0])
                discount = _percent_rate(parts[1]) if len(parts) > 1 else None
                fees.append(
                    FundFee(
                        "申购费" if title.startswith("申购") else "赎回费",
                        " · ".join(cells[:-1]),
                        _percent_rate(parts[0]),
                        Decimal(fixed.group(1)) if fixed else None,
                        discount,
                        "天天基金" if discount is not None else None,
                        "eastmoney",
                        source_url,
                    )
                )
    if not found:
        raise ProviderDataError("fund trading-rule page has no recognized tables")
    return tuple(fees), FundTradingRules(tuple(fields), "eastmoney", source_url), statuses


def _percent_rate(value: str) -> Decimal | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*%(?:（每年）)?\s*", value)
    return Decimal(match.group(1)) if match else None


def _fees(value: object) -> tuple[FundFee, ...]:
    if not isinstance(value, Mapping):
        return ()
    labels = (
        ("declare_rate_table", "申购费"),
        ("withdraw_rate_table", "赎回费"),
        ("other_rate_table", "其他费率"),
    )
    fees: list[FundFee] = []
    for key, fee_type in labels:
        rows = value.get(key)
        if not isinstance(rows, Sequence):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            condition = _optional_text(row.get("name")) or "标准费率"
            fees.append(FundFee(fee_type, condition, _optional_decimal(row.get("value"))))
    return tuple(fees)


def _period_returns(history: Sequence[FundNavPoint]) -> tuple[FundPeriodReturn, ...]:
    if not history:
        return tuple(
            FundPeriodReturn(label, None, reason="no_official_nav_history")
            for label in ("近1月", "近3月", "近6月", "近1年", "今年以来", "成立以来")
        )
    latest = history[-1]
    targets = (
        ("近1月", latest.nav_date - timedelta(days=30)),
        ("近3月", latest.nav_date - timedelta(days=91)),
        ("近6月", latest.nav_date - timedelta(days=183)),
        ("近1年", latest.nav_date - timedelta(days=365)),
        ("今年以来", date(latest.nav_date.year, 1, 1)),
        ("成立以来", history[0].nav_date),
    )
    result = []
    for label, target in targets:
        candidates = [i for i, point in enumerate(history) if point.nav_date <= target]
        index = candidates[-1] if candidates else None
        base = history[index] if index is not None else None
        reason = None
        if base is None or base is latest:
            reason = "insufficient_history"
        elif any(not point.return_verified for point in history[index + 1 :]):
            reason = "unverified_return_or_corporate_action_or_gap"
        rate = (latest.unit_nav / base.unit_nav - 1) * 100 if reason is None else None
        result.append(
            FundPeriodReturn(
                label,
                rate,
                base.nav_date if base else None,
                latest.nav_date,
                quality_status="valid" if reason is None else "unavailable",
                reason=reason,
            )
        )
    return tuple(result)


def _return_since(
    history: Sequence[FundNavPoint], target: date, latest_nav: Decimal
) -> Decimal | None:
    candidates = [index for index, item in enumerate(history) if item.nav_date <= target]
    index = candidates[-1] if candidates else None
    base = history[index] if index is not None else None
    if (
        base is None
        or base is history[-1]
        or base.unit_nav <= 0
        or any(not point.return_verified for point in history[index + 1 :])
    ):
        return None
    return (latest_nav / base.unit_nav - Decimal("1")) * Decimal("100")


def _stale(
    value: FundDetail,
    source_health: tuple[FundSourceHealth, ...] | None = None,
) -> FundDetail:
    values = {field: getattr(value, field) for field in value.__dataclass_fields__}
    values["stale"] = True
    if source_health is not None:
        values["source_health"] = source_health
    return FundDetail(**values)


def _optional_decimal(value: object) -> Decimal | None:
    try:
        converted = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return converted if converted.is_finite() else None


def _required_text(value: object, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ProviderDataError(f"{label} is missing")
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return None if not text or text.lower() in {"none", "nan"} else text


def _first_text(value: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        if key in value:
            text = _optional_text(value.get(key))
            if text:
                return text
    return None


def _optional_date(value: object) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _millisecond_date(value: object) -> date | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, SHANGHAI_TZ).date()
    except (TypeError, ValueError, OSError):
        return None
