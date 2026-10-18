"""External instrument matching and dated fund-NAV helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from quant_assistant.models.entities import AssetType
from quant_assistant.services.portfolio_commands import InstrumentInput

from ._models import (
    MarketInstrumentProvider,
    PhotoImportError,
)
from .ocr_parsing import _provider_date, _provider_decimal


def _fund_rows(frame: object) -> list[dict[str, object]]:
    if frame is None or not hasattr(frame, "to_dict"):
        return []
    result: list[dict[str, object]] = []
    for row in frame.to_dict("records"):
        raw_symbol = _fund_raw_symbol(row.get("symbol"))
        raw_name = row.get("name")
        name = "" if raw_name is None else str(raw_name).strip()
        if raw_symbol is None or not name or name.lower() in {"nan", "none", "<na>"}:
            continue
        result.append(
            {
                "symbol": raw_symbol,
                "name": name,
                "nav_date": row.get("nav_date"),
                "unit_nav": row.get("unit_nav"),
            }
        )
    return result


def _fund_raw_symbol(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    return match.group(1) if match else None


def _normalise_fund_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    return re.sub(r"[\s\(\)（）\[\]【】·•,，:：;；/\\_\-]", "", text)


def _fund_share_class(name: object) -> str | None:
    normalized = _normalise_fund_name(name)
    if normalized.endswith("A") or normalized.endswith("C"):
        return normalized[-1]
    return None


def _fund_name_score(requested: str, matched: str) -> float:
    return SequenceMatcher(
        None,
        _normalise_fund_name(requested),
        _normalise_fund_name(matched),
    ).ratio()


def _match_fund_name_row(
    rows: Sequence[dict[str, object]], requested_name: str
) -> dict[str, object]:
    requested = _normalise_fund_name(requested_name)
    if not requested:
        raise PhotoImportError("图片中的基金名称为空，无法匹配基金代码")
    requested_class = _fund_share_class(requested)
    scored: list[tuple[float, dict[str, object]]] = []
    for row in rows:
        raw_name = row.get("name")
        candidate_name = "" if raw_name is None else str(raw_name).strip()
        candidate = _normalise_fund_name(candidate_name)
        if not candidate:
            continue
        candidate_class = _fund_share_class(candidate)
        if requested_class != candidate_class:
            continue
        score = SequenceMatcher(None, requested, candidate).ratio()
        if requested in candidate or candidate in requested:
            score = max(score, 0.94)
        scored.append((score, row))
    if not scored:
        raise PhotoImportError(
            f"基金名称或A/C份额类别无法确认，请人工核对基金名称与官方代码：{requested_name}"
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_row = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if (
        best_score >= 0.995
        and second_score >= 0.995
        and _fund_raw_symbol(scored[1][1].get("symbol")) != _fund_raw_symbol(best_row.get("symbol"))
    ):
        raise PhotoImportError(f"基金名称对应多个外部代码：{requested_name}")
    if best_score < 0.66 or (best_score < 0.98 and best_score - second_score < 0.035):
        alternatives = "、".join(str(row.get("name") or "") for _, row in scored[:3])
        raise PhotoImportError(f"基金名称无法唯一匹配：{requested_name}（候选：{alternatives}）")
    return best_row


def _fund_nav_as_of(
    provider: MarketInstrumentProvider,
    raw_symbol: str,
    daily_row: dict[str, object],
    as_of_date: date,
) -> tuple[Decimal, date] | None:
    fetch_history = getattr(provider, "fetch_fund_nav", None)
    if callable(fetch_history):
        history = fetch_history(raw_symbol)
        candidates: list[tuple[date, Decimal]] = []
        if history is not None and hasattr(history, "to_dict"):
            for row in history.to_dict("records"):
                nav_date = _provider_date(row.get("nav_date"))
                unit_nav = _provider_decimal(row.get("unit_nav"))
                if (
                    nav_date is not None
                    and nav_date <= as_of_date
                    and unit_nav is not None
                    and unit_nav > 0
                ):
                    candidates.append((nav_date, unit_nav))
        if candidates:
            nav_date, unit_nav = max(candidates, key=lambda item: item[0])
            return unit_nav, nav_date

    nav_date = _provider_date(daily_row.get("nav_date"))
    unit_nav = _provider_decimal(daily_row.get("unit_nav"))
    if nav_date is not None and nav_date <= as_of_date and unit_nav is not None and unit_nav > 0:
        return unit_nav, nav_date
    return None


def _match_stock_metadata(frame, symbols: set[str], as_of_date: date) -> list[InstrumentInput]:
    if frame is None or not hasattr(frame, "to_dict"):
        return []
    result: list[InstrumentInput] = []
    for row in frame.to_dict("records"):
        symbol = str(row.get("symbol", "")).strip().upper()
        if symbol not in symbols:
            continue
        result.append(
            InstrumentInput(
                symbol=symbol,
                asset_type=AssetType.STOCK,
                name=str(row.get("name", symbol)).strip() or symbol,
                exchange=str(row.get("exchange", "")).strip() or None,
                as_of_date=as_of_date,
            )
        )
    return result


def _match_fund_metadata(frame, symbols: set[str], as_of_date: date) -> list[InstrumentInput]:
    if frame is None or not hasattr(frame, "to_dict"):
        return []
    result: list[InstrumentInput] = []
    for row in _fund_rows(frame):
        raw = row["symbol"]
        name = str(row["name"])
        candidate_class = _fund_share_class(name)
        for requested in sorted(symbols):
            match = re.fullmatch(r"(\d{6})([AC])?", requested)
            if match is None or match.group(1) != raw:
                continue
            if match.group(2) is not None and match.group(2) != candidate_class:
                continue
            result.append(
                InstrumentInput(
                    symbol=requested,
                    asset_type=AssetType.FUND,
                    name=name,
                    exchange="OTC",
                    as_of_date=as_of_date,
                )
            )
    return result


__all__ = [
    "_fund_rows",
    "_fund_raw_symbol",
    "_normalise_fund_name",
    "_fund_share_class",
    "_match_fund_name_row",
    "_fund_nav_as_of",
    "_match_stock_metadata",
    "_match_fund_metadata",
]
