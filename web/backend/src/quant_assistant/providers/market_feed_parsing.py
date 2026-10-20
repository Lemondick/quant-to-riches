"""Pure normalization helpers for public market and news observations."""

from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.base import ProviderDataError
from quant_assistant.providers.index_registry import definitions, resolve_legacy_index

if TYPE_CHECKING:
    from quant_assistant.providers.market_feed import MarketQuote

_MAIN_INDEX_IDS = {
    row.exchange_code: row.quote.symbol
    for row in definitions()
    if row.enabled
    and row.market == "CN"
    and row.quote.provider == "tencent"
    and row.quote.verification_status == "verified"
}


def _require_columns(frame: object, required: set[str], label: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise ProviderDataError(f"{label}: provider did not return a data frame")
    missing = required.difference(frame.columns)
    if missing:
        raise ProviderDataError(f"{label}: missing columns {sorted(missing)}")


def _normalize_code(value: object) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or "").strip())
    if match is None:
        raise ValueError("market symbol must contain exactly six digits")
    return match.group(1)


def _tencent_id(symbol: str, asset_type: str) -> str:
    code = _normalize_code(symbol)
    if asset_type == "index":
        definition = resolve_legacy_index(code)
        if definition.quote.provider != "tencent" or not definition.enabled:
            raise ValueError("index mapping is not enabled for this provider")
        return definition.quote.symbol
    if asset_type != "stock":
        raise ValueError("market quote asset type must be stock or index")
    if code.startswith(("4", "8", "92")):
        prefix = "bj"
    elif code.startswith(("5", "6")):
        prefix = "sh"
    else:
        prefix = "sz"
    return f"{prefix}{code}"


def _decimal(value: object, label: str) -> Decimal:
    try:
        converted = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderDataError(f"{label} is not numeric") from exc
    if not converted.is_finite():
        raise ProviderDataError(f"{label} is not finite")
    return converted


def _optional_decimal(value: object) -> Decimal | None:
    try:
        converted = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return converted if converted.is_finite() else None


def _text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        raise ProviderDataError(f"{label} is empty")
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return None if not text or text.lower() == "nan" else text


def _optional_date_text(value: object) -> str | None:
    if value is None or bool(pd.isna(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def _datetime(value: object) -> datetime:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ProviderDataError("news published_at is invalid")
    result = parsed.to_pydatetime()
    if result.tzinfo is None:
        result = result.replace(tzinfo=SHANGHAI_TZ)
    return result.astimezone(SHANGHAI_TZ)


def _quote_datetime(value: object, fallback: datetime) -> datetime:
    text = str(value or "").strip()
    try:
        if len(text) != 14:
            raise ValueError("missing source clock")
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        raise ProviderDataError("quote source time is unknown") from None
    return parsed.replace(tzinfo=SHANGHAI_TZ)


def _instrument_type(symbol: str, asset_type: str) -> str:
    if asset_type == "index":
        return "index"
    if symbol.startswith(
        ("159", "510", "511", "512", "513", "515", "516", "517", "518", "56", "588", "589")
    ):
        return "etf"
    return "stock"


def quote_conflict(primary: MarketQuote, backup: MarketQuote) -> bool:
    """Compare matching instrument/metric and exact clocks, never average ticks."""
    return (
        primary.symbol == backup.symbol
        and primary.asset_type == backup.asset_type
        and primary.source_time is not None
        and primary.source_time == backup.source_time
        and abs(primary.price / backup.price - 1) > Decimal(".005")
    )


def _quote_time_verified(value: object) -> bool:
    try:
        datetime.strptime(str(value or "").strip(), "%Y%m%d%H%M%S")
        return True
    except ValueError:
        return False


def _news_category(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    groups = (
        ("宏观", ("央行", "利率", "通胀", "GDP", "财政", "货币政策", "美联储")),
        ("基金", ("基金", "ETF", "公募", "私募")),
        ("行业", ("半导体", "医药", "新能源", "汽车", "白酒", "地产", "银行", "科技")),
        ("策略", ("策略", "配置", "研报", "机构观点", "展望")),
    )
    for category, keywords in groups:
        if any(keyword in text for keyword in keywords):
            return category
    return "市场"


def _news_plain_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split())


def _important_news(title: str, summary: str) -> bool:
    text = f"{title} {summary}"
    return any(
        keyword in text
        for keyword in ("央行", "证监会", "国务院", "重大", "突发", "降息", "降准", "暂停交易")
    )


def _source_from_label(label: str) -> str:
    """Normalize ``akshare:eastmoney`` style labels to a health key."""

    source = str(label).split(":")[-1].strip().lower()
    return source if source in {"cls", "eastmoney", "sina", "tencent"} else "unknown"


def _source_for_cache_key(key: str) -> str:
    if str(key).startswith("quotes:") or key == "indices":
        return "tencent"
    if key == "sector_rankings":
        return "eastmoney"
    return "eastmoney"
