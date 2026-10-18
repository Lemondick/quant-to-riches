"""Read-only intraday fund valuation from a public external quote endpoint.

The normal fund NAV feed is end-of-day.  This adapter is intentionally kept
outside the portfolio ledger: an estimate is useful for display, but it must
never overwrite the official NAV or be used as a trade fill price.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers._lazy_akshare import ak
from quant_assistant.providers.base import ProviderUnavailable
from quant_assistant.providers.data_quality import ObservationMetadata, metadata
from quant_assistant.providers.public_http import PublicHttp, log_validation

DEFAULT_EASTMONEY_ESTIMATE_URL = "https://fundgz.1234567.com.cn/js/{symbol}.js"
_SYMBOL_RE = re.compile(r"(?<!\d)(\d{6})(?:[.](?:OF|SH|SZ|BJ)|[AC])?(?!\d)", re.I)
_JSONP_RE = re.compile(r"^[^(]*\((?P<body>.*)\)\s*;?\s*$", re.S)
logger = logging.getLogger(__name__)


class FundValuationError(RuntimeError):
    """The external estimate cannot be parsed or trusted."""


class FundValuationProvider(Protocol):
    name: str

    def estimate(self, symbol: str) -> FundIntradayEstimate: ...


@dataclass(frozen=True, slots=True)
class FundIntradayEstimate(ObservationMetadata):
    """A display-only intraday estimate or latest official NAV observation."""

    symbol: str
    estimate_nav: Decimal | None
    previous_nav: Decimal
    change_rate: Decimal | None
    observed_at: datetime | None
    source: str
    nav_date: str | None = None
    valuation_type: Literal["intraday_estimate", "latest_nav"] = "intraday_estimate"
    official_nav: Decimal | None = None
    official_change_rate: Decimal | None = None

    def __post_init__(self):
        if self.valuation_type == "latest_nav":
            # Also normalize injected legacy providers at the model boundary.
            object.__setattr__(
                self,
                "official_nav",
                self.official_nav if self.official_nav is not None else self.estimate_nav,
            )
            object.__setattr__(
                self,
                "official_change_rate",
                self.official_change_rate
                if self.official_change_rate is not None
                else self.change_rate,
            )
            object.__setattr__(self, "estimate_nav", None)
            object.__setattr__(self, "change_rate", None)
            object.__setattr__(self, "observed_at", None)
            object.__setattr__(self, "is_estimate", False)

    @property
    def change_amount(self) -> Decimal | None:
        return None if self.estimate_nav is None else self.estimate_nav - self.previous_nav


class EastmoneyFundValuationProvider:
    """Eastmoney ``fundgz`` JSONP adapter with bounded network behavior."""

    name = "eastmoney_fundgz"

    def __init__(
        self,
        endpoint_template: str = DEFAULT_EASTMONEY_ESTIMATE_URL,
        *,
        timeout: float = 8.0,
        http_get=None,
        now=None,
    ) -> None:
        if not isinstance(endpoint_template, str) or not endpoint_template.strip():
            raise ValueError("estimate endpoint must be a non-empty string")
        if timeout <= 0 or timeout > 30:
            raise ValueError("estimate timeout must be between 0 and 30 seconds")
        self._endpoint_template = endpoint_template.strip()
        self._timeout = float(timeout)
        self._http = PublicHttp() if http_get is None else PublicHttp(http_get)
        self._now = now or (lambda: datetime.now(SHANGHAI_TZ))

    def estimate(self, symbol: str) -> FundIntradayEstimate:
        code = normalize_fund_symbol(symbol)
        url = self._endpoint_template.format(symbol=code)
        response = self._http.get(
            url,
            expected="text",
            module="fund_estimate",
            headers={
                "Accept": "application/javascript, application/json",
                "User-Agent": "quant-assistant/0.1",
            },
            timeout=self._timeout,
        )
        raw = response.content
        try:
            payload = _decode_jsonp(raw.decode("utf-8"))
            result = parse_eastmoney_estimate(payload, code, source=self.name, now=self._now())
            log_validation(
                "fund_estimate",
                "eastmoney",
                count=1,
                source_time=result.source_time,
                quality=result.quality_status,
            )
            return result
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, FundValuationError) as exc:
            log_validation(
                "fund_estimate", "eastmoney", quality="invalid", reason="parse_or_validation_failed"
            )
            raise FundValuationError("外部基金盘中估值返回格式无效") from exc


class AkshareFundNavProvider:
    """Fallback to the latest official NAV when intraday estimates are absent."""

    name = "akshare:eastmoney_latest_nav"

    def __init__(
        self,
        *,
        akshare_module: object = ak,
        now: Callable[[], datetime] | None = None,
        max_nav_age_days: int = 14,
    ) -> None:
        if isinstance(max_nav_age_days, bool) or not 1 <= max_nav_age_days <= 90:
            raise ValueError("max NAV age must be between 1 and 90 days")
        self._ak = akshare_module
        self._now = now or (lambda: datetime.now(SHANGHAI_TZ))
        self._max_nav_age = timedelta(days=max_nav_age_days)

    def estimate(self, symbol: str) -> FundIntradayEstimate:
        code = normalize_fund_symbol(symbol)
        try:
            frame = self._ak.fund_open_fund_info_em(
                symbol=code,
                indicator="单位净值走势",
            )
        except Exception as exc:
            logger.warning(
                "external NAV source unavailable: %s (%s)", type(exc).__name__, self.name
            )
            raise ProviderUnavailable("最新官方基金净值暂不可用") from exc
        required = ("净值日期", "单位净值", "日增长率")
        if not isinstance(frame, pd.DataFrame) or not set(required).issubset(frame.columns):
            raise FundValuationError("最新官方基金净值字段不完整")
        selected = frame[list(required)].copy()
        selected["净值日期"] = pd.to_datetime(selected["净值日期"], errors="coerce")
        selected["单位净值"] = pd.to_numeric(selected["单位净值"], errors="coerce")
        selected["日增长率"] = pd.to_numeric(selected["日增长率"], errors="coerce")
        selected = selected.dropna(subset=["净值日期", "单位净值"]).sort_values("净值日期")
        if len(selected) < 2:
            raise FundValuationError("最新官方基金净值至少需要两个观测")
        previous, latest = selected.iloc[-2], selected.iloc[-1]
        latest_nav = _positive_decimal(latest["单位净值"], "latest official NAV")
        previous_nav = _positive_decimal(previous["单位净值"], "previous official NAV")
        growth = latest["日增长率"]
        change_rate = (
            _decimal(growth, "latest official NAV change rate")
            if not pd.isna(growth)
            else (latest_nav - previous_nav) / previous_nav * Decimal("100")
        )
        observed_at = self._now()
        if not isinstance(observed_at, datetime):
            raise TypeError("NAV clock must return datetime")
        observed_at = (
            observed_at.replace(tzinfo=SHANGHAI_TZ)
            if observed_at.tzinfo is None
            else observed_at.astimezone(SHANGHAI_TZ)
        )
        latest_date = latest["净值日期"].date()
        if not observed_at.date() - self._max_nav_age <= latest_date <= observed_at.date():
            raise FundValuationError("最新官方基金净值已经过期")
        return FundIntradayEstimate(
            symbol=code,
            estimate_nav=None,
            previous_nav=previous_nav,
            change_rate=None,
            observed_at=None,
            source=self.name,
            nav_date=latest_date.isoformat(),
            valuation_type="latest_nav",
            official_nav=latest_nav,
            official_change_rate=change_rate,
            **metadata("official_nav", self.name, latest_date.isoformat(), observed_at),
        )


class LocalFundNavProvider:
    """Read the verified anonymous observations supplied by the native gateway."""

    name = "local_official_nav"

    def __init__(self, observations: Mapping[str, object], *, now=None) -> None:
        self._observations = observations
        self._now = now or (lambda: datetime.now(SHANGHAI_TZ))

    def estimate(self, symbol: str) -> FundIntradayEstimate:
        code = normalize_fund_symbol(symbol)
        record = self._observations.get(code)
        if not isinstance(record, Mapping):
            raise ProviderUnavailable("本地官方净值暂不可用")
        raw = record.get("points")
        if (
            not isinstance(raw, list)
            or len(raw) != 2
            or not all(isinstance(point, Mapping) for point in raw)
        ):
            raise FundValuationError("本地官方净值需要两个完整观测")
        points = sorted(raw, key=lambda point: str(point.get("nav_date", "")))
        now = self._now().astimezone(SHANGHAI_TZ)
        values = []
        for point in points:
            if not isinstance(point, Mapping) or point.get("source") not in {
                "eastmoney",
                "tencent",
                "danjuan",
            }:
                raise FundValuationError("本地净值缺少可信来源")
            day = date.fromisoformat(str(point.get("nav_date", "")))
            if not now.date() - timedelta(days=14) <= day <= now.date():
                raise FundValuationError("本地官方净值日期无效或已过期")
            values.append((day, _positive_decimal(point.get("unit_nav"), "official NAV")))
        if values[0][0] >= values[1][0]:
            raise FundValuationError("本地官方净值日期重复")
        # The repository's observed_at is a collection time, not an official NAV clock.
        fetched = record.get("fetched_at") or record.get("observed_at")
        observed = _parse_observed_at(fetched) if fetched else now
        if observed > now + timedelta(minutes=1):
            raise FundValuationError("本地官方净值获取时间无效")
        previous, latest = values[0][1], values[1][1]
        return FundIntradayEstimate(
            symbol=code,
            estimate_nav=None,
            previous_nav=previous,
            official_change_rate=(
                _decimal(points[-1]["daily_return"], "official NAV daily return")
                if points[-1].get("daily_return") is not None
                else None
            ),
            change_rate=None,
            observed_at=None,
            source=f"local:{points[-1]['source']}",
            nav_date=values[1][0].isoformat(),
            valuation_type="latest_nav",
            official_nav=latest,
            **metadata(
                "official_nav", str(points[-1]["source"]), values[1][0].isoformat(), observed
            ),
        )


class FallbackFundValuationProvider:
    """Prefer intraday estimates; only an injected local NAV may be a fallback."""

    name = "eastmoney_fundgz+local_nav"

    def __init__(
        self,
        primary: FundValuationProvider | None = None,
        fallback: FundValuationProvider | None = None,
    ) -> None:
        self._primary = primary or EastmoneyFundValuationProvider()
        # Full-history Akshare scraping belongs to explicit background imports,
        # never to the request path when the intraday source is unavailable.
        self._fallback = fallback

    def estimate(self, symbol: str) -> FundIntradayEstimate:
        stale = None
        reason = "unavailable"
        try:
            value = self._primary.estimate(symbol)
            if getattr(value, "quality_status", "unknown_time") not in {"stale", "invalid"}:
                return value
            stale, reason = value, value.quality_status
        except (ProviderUnavailable, FundValuationError) as exc:
            reason = type(exc).__name__
            logger.warning(
                "fund valuation primary source unavailable: %s (%s)",
                type(exc).__name__,
                getattr(self._primary, "name", type(self._primary).__name__),
            )
        except Exception as exc:
            logger.warning(
                "fund valuation primary source code/contract failure: %s (%s)",
                type(exc).__name__,
                getattr(self._primary, "name", type(self._primary).__name__),
            )
        if self._fallback is None:
            if stale is not None:
                return stale
            raise ProviderUnavailable("基金盘中估值暂不可用，且未配置本地官方净值")
        try:
            result = self._fallback.estimate(symbol)
            return replace(result, fallback_used=True, fallback_reason=reason)
        except (ProviderUnavailable, FundValuationError) as exc:
            logger.warning(
                "fund valuation fallback unavailable: %s (%s)",
                type(exc).__name__,
                getattr(self._fallback, "name", type(self._fallback).__name__),
            )
            raise
        except Exception as exc:
            logger.warning(
                "fund valuation fallback code/contract failure: %s (%s)",
                type(exc).__name__,
                getattr(self._fallback, "name", type(self._fallback).__name__),
            )
            raise


def normalize_fund_symbol(value: object) -> str:
    """Return the six-digit base fund code, rejecting ambiguous symbols."""

    text = str(value or "").strip().upper()
    match = _SYMBOL_RE.fullmatch(text)
    if match is None:
        match = _SYMBOL_RE.search(text)
    if match is None:
        raise ValueError("基金代码必须包含六位数字")
    return match.group(1)


def parse_eastmoney_estimate(
    payload: dict[str, object],
    symbol: str,
    *,
    source: str = "eastmoney_fundgz",
    now: datetime | None = None,
) -> FundIntradayEstimate:
    """Normalize the public ``fundgz`` fields without silently filling values."""

    if not isinstance(payload, dict):
        raise TypeError("基金估值响应必须是对象")
    code = normalize_fund_symbol(symbol)
    returned_code = str(payload.get("fundcode", payload.get("code")) or "").strip()
    if not re.fullmatch(r"\d{6}", returned_code) or returned_code != code:
        raise FundValuationError("外部基金估值代码与请求代码不一致")
    estimate_nav = _decimal_field(payload, "gsz", "estimate_nav")
    previous_nav = _decimal_field(payload, "dwjz", "previous_nav")
    change_rate = _decimal_field(payload, "gszzl", "change_rate")
    if estimate_nav <= 0 or previous_nav <= 0:
        raise FundValuationError("基金估值净值必须为正")
    observed_at = _parse_observed_at(payload.get("gztime"))
    calculated = (estimate_nav / previous_nav - 1) * 100
    if abs(calculated - change_rate) > Decimal("0.1"):
        raise FundValuationError("估算涨跌幅与净值口径不一致")
    fetched = now or datetime.now(SHANGHAI_TZ)
    precision = (
        "seconds" if re.search(r"[T ]\d{2}:\d{2}:\d{2}", str(payload.get("gztime"))) else "minutes"
    )
    meta = metadata("intraday_estimate", source, observed_at.isoformat(timespec=precision), fetched)
    if meta["quality_status"] == "invalid":
        raise FundValuationError("估值时间无效")
    nav_date = str(payload.get("jzrq", "") or "").strip() or None
    if nav_date is None or date.fromisoformat(nav_date) > observed_at.date():
        raise FundValuationError("正式净值日期无效")
    return FundIntradayEstimate(
        symbol=code,
        estimate_nav=estimate_nav,
        previous_nav=previous_nav,
        change_rate=change_rate,
        observed_at=observed_at,
        source=source.strip() or "external",
        nav_date=nav_date,
        official_nav=previous_nav,
        **meta,
    )


def parse_fund_valuation_response(
    payload: dict[str, object], symbol: str, *, source: str = "eastmoney_fundgz"
) -> FundIntradayEstimate:
    """Backward-compatible descriptive alias for callers outside the provider."""

    return parse_eastmoney_estimate(payload, symbol, source=source)


def _decode_jsonp(raw: str) -> dict[str, object]:
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSONP_RE.match(text)
        if match is None:
            raise ValueError("基金估值响应不是JSON或JSONP") from None
        parsed = json.loads(match.group("body"))
    if not isinstance(parsed, dict):
        raise ValueError("基金估值响应必须是对象")
    return parsed


def _decimal_field(payload: dict[str, object], key: str, label: str) -> Decimal:
    raw = payload.get(key)
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FundValuationError(f"基金估值缺少{label}") from exc
    if not value.is_finite():
        raise FundValuationError(f"基金估值{label}不是有限数字")
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        converted = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FundValuationError(f"{label}不是数字") from exc
    if not converted.is_finite():
        raise FundValuationError(f"{label}不是有限数字")
    return converted


def _positive_decimal(value: object, label: str) -> Decimal:
    converted = _decimal(value, label)
    if converted <= 0:
        raise FundValuationError(f"{label}必须为正")
    return converted


def _parse_observed_at(value: object) -> datetime:
    text = str(value or "").strip()
    if not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}", text):
        raise FundValuationError("基金估值缺少更新时间")
    parsed = datetime.fromisoformat(text.replace("/", "-").replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


__all__ = [
    "DEFAULT_EASTMONEY_ESTIMATE_URL",
    "AkshareFundNavProvider",
    "EastmoneyFundValuationProvider",
    "FallbackFundValuationProvider",
    "FundIntradayEstimate",
    "FundValuationError",
    "FundValuationProvider",
    "LocalFundNavProvider",
    "normalize_fund_symbol",
    "parse_eastmoney_estimate",
    "parse_fund_valuation_response",
]
