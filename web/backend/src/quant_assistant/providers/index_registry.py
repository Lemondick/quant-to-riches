"""One packaged, immutable authority for index identity and verified capabilities."""

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from quant_assistant.providers.market_calendar import ZONES


@dataclass(frozen=True, slots=True)
class IndexProviderMapping:
    provider: str
    symbol: str
    response_symbol: str
    response_names: tuple[str, ...]
    response_type: str
    response_market: str
    response_currency: str | None
    currency_basis: str
    source_timezone: str
    verification_status: str
    verified_at: str
    evidence_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    id: str
    name: str
    official_name: str
    market: str
    currency: str
    timezone: str
    calendar_id: str
    exchange_code: str
    display_order: int
    enabled: bool
    product_status: str
    product_evidence: str
    quote: IndexProviderMapping
    history: IndexProviderMapping | None
    legacy_history_symbol: str | None


def _mapping(value):
    result = IndexProviderMapping(
        **{
            **value,
            "response_names": tuple(value["response_names"]),
            "evidence_urls": tuple(value["evidence_urls"]),
        }
    )
    if (
        result.provider not in {"tencent", "yahoo"}
        or result.verification_status not in {"verified", "pending", "unavailable"}
        or not result.symbol
        or not result.response_symbol
        or not result.response_names
        or not all(result.response_names)
        or not result.evidence_urls
        or not all(url.startswith("https://") for url in result.evidence_urls)
        or not result.currency_basis
        or not result.verified_at
    ):
        raise ValueError("invalid index provider mapping")
    return result


@cache
def _registry():
    root = json.loads(files(__package__).joinpath("index_registry.json").read_text("utf-8"))
    if root["format_version"] != 1 or not root["registry_version"]:
        raise ValueError("invalid index registry format")
    rows = tuple(
        IndexDefinition(
            **{
                **row,
                "quote": _mapping(row["quote"]),
                "history": _mapping(row["history"]) if row["history"] is not None else None,
            }
        )
        for row in root["definitions"]
    )
    currencies = {"CN": "CNY", "HK": "HKD", "JP": "JPY", "US": "USD"}
    if (
        len(rows) != 16
        or len({row.id for row in rows}) != len(rows)
        or len({row.display_order for row in rows}) != len(rows)
        or len({row.exchange_code for row in rows if row.market == "CN"})
        != sum(row.market == "CN" for row in rows)
    ):
        raise ValueError("index registry identities or order are not unique")
    for row in rows:
        if (
            row.market not in currencies
            or not re.fullmatch(r"[a-z][a-z0-9]*(?:\.[a-z0-9]+)+", row.id)
            or not row.id.startswith(row.market.lower() + ".")
            or row.currency != currencies[row.market]
            or row.timezone != ZONES[row.market]
            or row.calendar_id != row.market
            or row.quote.source_timezone != row.timezone
            or row.product_status not in {"confirmed", "provisional"}
            or not row.name
            or not row.official_name
            or not row.exchange_code
            or row.enabled
            and row.quote.verification_status != "verified"
            or row.history is not None
            and row.history.source_timezone != row.timezone
            or row.legacy_history_symbol is not None
            and (
                row.market != "CN"
                or row.legacy_history_symbol != row.exchange_code
                or row.history is None
                or row.history.verification_status != "verified"
            )
        ):
            raise ValueError("invalid index registry definition")
    return root["registry_version"], tuple(sorted(rows, key=lambda row: row.display_order))


def registry_version() -> str:
    return _registry()[0]


def definitions() -> tuple[IndexDefinition, ...]:
    return _registry()[1]


def get_index(index_id: str) -> IndexDefinition:
    for row in definitions():
        if row.id == index_id:
            return row
    raise ValueError("unknown index id")


def resolve_legacy_index(symbol: str) -> IndexDefinition:
    for row in definitions():
        if row.market == "CN" and row.exchange_code == symbol:
            return row
    raise ValueError("unknown legacy index symbol")
