"""Supplier fund directory, with no name-based classification or account data."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import requests

from quant_assistant.providers.base import ProviderDataError
from quant_assistant.providers.data_quality import MemoryCache
from quant_assistant.providers.public_http import PublicHttp, log_validation

DIRECTORY_URL = "https://fund.eastmoney.com/js/fundcode_search.js"


def _reject_constant(value):
    raise ValueError(value)


def parse_catalog(body: bytes) -> tuple[tuple[tuple[str, str, str | None], ...], int]:
    """Decode only the observed assignment wrapper; quarantine ambiguous identities."""
    try:
        text = body.decode("utf-8-sig", errors="strict")
        match = re.fullmatch(r"\s*var\s+r\s*=\s*(\[.*\])\s*;\s*", text, re.S)
        if match is None:
            raise ValueError("wrapper")
        rows = json.loads(match[1], parse_constant=_reject_constant)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 50000:
            raise ValueError("count")
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ProviderDataError("invalid fund directory envelope") from exc
    items, conflicts, rejected = {}, set(), 0
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 5
            or not isinstance(row[0], str)
            or re.fullmatch(r"[0-9]{6}", row[0]) is None
            or not isinstance(row[2], str)
            or not 1 <= len(row[2].strip()) <= 200
            or not isinstance(row[3], str)
            or len(row[3]) > 100
        ):
            rejected += 1
            continue
        code, item = row[0], (row[0], row[2].strip(), row[3].strip() or None)
        if code in conflicts:
            rejected += 1
        elif code in items and items[code] != item:
            del items[code]
            conflicts.add(code)
            rejected += 2
        else:
            items[code] = item
    if not items:
        raise ProviderDataError("fund directory has no qualified identities")
    return tuple(items[key] for key in sorted(items)), rejected


class FundCatalogProvider:
    def __init__(self, http_get=requests.get, *, now=None, ttl=21600):
        self._now = now or (lambda: datetime.now(UTC))
        self._http = PublicHttp(http_get, now=self._now)
        self._cache = MemoryCache(capacity=1)
        self._ttl = max(60, min(int(ttl), 86400))

    def expire_cache(self):
        self._cache.expire()

    def read(self) -> dict:
        def load():
            response = self._http.get(
                DIRECTORY_URL,
                expected="text",
                module="fund.catalog",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=(3, 10),
            )
            items, rejected = parse_catalog(response.content)
            return items, rejected, response.public_fetched_at.isoformat()

        (items, rejected, fetched_at), stale = self._cache.read("fund.catalog", load, ttl=self._ttl)
        quality = "stale" if stale else "unknown_time"
        log_validation(
            "fund.catalog", "eastmoney", count=len(items), quality=quality, fallback=stale
        )
        return {
            "schema_version": "fund-directory.v1",
            "source": "eastmoney",
            "source_url": DIRECTORY_URL,
            "source_time": None,
            "fetched_at": fetched_at,
            "cache_read_at": self._now().isoformat(),
            "quality_status": quality,
            "quality_reason": "reload_failed" if stale else "supplier_publication_time_missing",
            "stale": stale,
            "is_stale": stale,
            "fallback_used": stale,
            "record_count": len(items),
            "rejected_count": rejected,
            "coverage_status": "supplier_directory_only",
            "unavailable": [],
            "items": [dict(symbol=code, name=name, fund_type=kind) for code, name, kind in items],
        }
