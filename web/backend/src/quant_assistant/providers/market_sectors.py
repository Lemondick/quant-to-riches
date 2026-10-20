"""Public board catalogs. Never mix vendors' taxonomies or quote clocks."""

import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from pathlib import Path

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.public_http import PublicHttp, PublicHttpError

CATEGORIES = {"industry": 2, "concept": 3, "region": 1}


class _Taxonomy(dict):
    fetched_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SectorRanking:
    name: str
    code: str
    change_rate: Decimal | None
    leader: str | None
    category: str = "industry"
    source: str = "unknown"
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    turnover: Decimal | None = None
    advancing: int | None = None
    declining: int | None = None
    members: int | None = None


@dataclass(frozen=True, slots=True)
class SectorCatalog:
    category: str
    source: str
    total: int | None
    received: int
    complete: bool | None
    fetched_at: datetime | None
    observed_at: datetime | None = None
    stale: bool = False
    error: str | None = None
    listed_total: int | None = None
    unquoted: int = 0
    catalog_status: str = "unknown"
    quote_total: int | None = None
    quote_payload_complete: bool | None = None
    quotes_complete: bool = False
    http_ok: bool | None = None
    parse_ok: bool | None = None
    source_time_known: bool = False
    cache_used: bool = False
    listed_received: int | None = None
    coverage_version: int = 2
    catalog_fetched_at: datetime | None = None
    catalog_http_ok: bool | None = None
    catalog_parse_ok: bool | None = None
    catalog_cache_used: bool = False
    catalog_stale: bool = False
    catalog_error: str | None = None


@dataclass(frozen=True, slots=True)
class _CatalogRows:
    rows: tuple[SectorRanking, ...]
    quote_total: int | None = None
    quote_payload_complete: bool | None = None
    listed_received: int | None = None
    catalog_fetched_at: datetime | None = None


@dataclass
class _ReadState:
    http_ok: bool | None = None
    parse_ok: bool | None = None

    def get(self, http_get, url, **kwargs):
        try:
            response = http_get(url, **kwargs)
            self.http_ok = True
            return response
        except PublicHttpError as failure:
            if failure.http_status is not None:
                self.http_ok = failure.http_status == 200
            elif str(failure) in {"circuit_open", "concurrency_limit"}:
                self.http_ok = None
            else:
                self.http_ok = False
            if failure.http_status == 200:
                self.parse_ok = False
            raise
        except Exception:
            self.http_ok = False
            raise


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("missing numeric observation")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("nonfinite observation")
    return number


def _count(value):
    number = _number(value)
    if number < 0 or number != int(number):
        raise ValueError("invalid count")
    return int(number)


def _optional(value, parse):
    return None if value in (None, "", "-") else parse(value)


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\ufffd" in value:
        raise ValueError("invalid text")
    return value.strip()


def _taxonomy(http_get):
    response = http_get("https://quote.eastmoney.com/center/api/sidemenu_new.json", timeout=(3, 6))
    response.raise_for_status()
    catalog = response.json()["bklist"]
    result = _Taxonomy({category: {} for category in CATEGORIES})
    result.fetched_at = getattr(response, "public_fetched_at", None)
    for item in catalog:
        for category, number in CATEGORIES.items():
            if item.get("type") == number:
                code = _text(item["code"])
                if code in result[category]:
                    raise ValueError("duplicate taxonomy code")
                result[category][code] = _text(item["name"])
    if any(not values for values in result.values()):
        raise ValueError("incomplete taxonomy")
    return result


def _eastmoney(http_get, category, now, taxonomy, catalog_fetched_at):
    rows, total = [], None
    # Stable code order prevents changing percentage ranks from moving between pages.
    for page in range(1, 51):
        response = http_get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": page,
                "pz": 100,
                "po": 0,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f12",
                "fs": f"m:90+t:{CATEGORIES[category]}+f:!50",
                "fields": "f2,f3,f6,f12,f14,f124,f104,f105,f128",
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/center/boardlist.html",
            },
            timeout=(3, 6),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rc") != 0 or not isinstance(payload.get("data"), dict):
            raise ValueError("invalid board response")
        data = payload["data"]
        current_total = _count(data.get("total"))
        if not 0 < current_total <= 5000 or total not in (None, current_total):
            raise ValueError("catalog count changed")
        total = current_total
        items = data.get("diff")
        if not isinstance(items, list) or not items or len(items) > 100:
            raise ValueError("truncated board page")
        for item in items:
            code = _text(item.get("f12"))
            if not re.fullmatch(r"BK\d{4,8}", code):
                raise ValueError("invalid board code")
            if taxonomy.get(code) != item.get("f14"):
                raise ValueError("board does not match official taxonomy")
            observed = datetime.fromtimestamp(_count(item.get("f124")), SHANGHAI_TZ)
            if observed > now + timedelta(seconds=30) or observed.year < 2000:
                raise ValueError("invalid quote timestamp")
            rows.append(
                SectorRanking(
                    name=_text(item.get("f14")),
                    code=code,
                    change_rate=_number(item.get("f3")),
                    leader=_optional(item.get("f128"), _text),
                    category=category,
                    source="eastmoney",
                    observed_at=observed,
                    fetched_at=getattr(response, "public_fetched_at", now),
                    turnover=_optional(item.get("f6"), _number),
                    advancing=_optional(item.get("f104"), _count),
                    declining=_optional(item.get("f105"), _count),
                )
            )
        if len({row.code for row in rows}) != len(rows) or len(rows) > total:
            raise ValueError("duplicate board page")
        if len(rows) == total:
            quoted = {row.code for row in rows}
            # Registered boards without a quote stay visible with null values.
            # Never fill missing observations with zero or a sibling's return.
            rows.extend(
                SectorRanking(
                    name=name,
                    code=code,
                    change_rate=None,
                    leader=None,
                    category=category,
                    source="eastmoney",
                    fetched_at=catalog_fetched_at,
                )
                for code, name in taxonomy.items()
                if code not in quoted
            )
            return _CatalogRows(tuple(rows), total, True, len(taxonomy), catalog_fetched_at)
        if len(items) < 100:
            raise ValueError("truncated catalog")
    raise ValueError("catalog exceeds page limit")


def _sina(http_get, category, now):
    url = (
        "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
        if category == "industry"
        else "https://money.finance.sina.com.cn/q/view/newFLJK.php"
    )
    response = http_get(
        url,
        params={}
        if category == "industry"
        else {"param": "class" if category == "concept" else "area"},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/stock/sl/"},
        timeout=(3, 6),
    )
    response.raise_for_status()
    if len(response.content) > 5_000_000:
        raise ValueError("board response too large")
    text = response.content.decode("gb18030", errors="strict")
    data = json.loads(text[text.index("{") :].rstrip(";\r\n "))
    if not isinstance(data, dict) or not 0 < len(data) <= 5000:
        raise ValueError("invalid board catalog")
    rows = []
    for code, raw in data.items():
        fields = raw.split(",")
        if len(fields) != 13 or fields[0] != code:
            raise ValueError("invalid board record")
        rows.append(
            SectorRanking(
                name=_text(fields[1]),
                code=_text(code),
                change_rate=_number(fields[5]),
                leader=_optional(fields[12], _text),
                category=category,
                source="sina",
                fetched_at=getattr(response, "public_fetched_at", now),
                turnover=_optional(fields[7], _number),
                members=_count(fields[2]),
            )
        )
    # This endpoint does not publish a quote timestamp. A response Date header is
    # not a substitute; keep observed_at null even when fetched just now.
    return _CatalogRows(
        tuple(rows),
        listed_received=len(rows),
        catalog_fetched_at=getattr(response, "public_fetched_at", now),
    )


def _cache_read(path, source, category, now):
    data = json.loads(path.read_text(encoding="utf-8"))
    fetched = datetime.fromisoformat(data["fetched_at"])
    if fetched.tzinfo is None or not 0 <= (now - fetched).total_seconds() <= 7 * 86400:
        raise ValueError("expired catalog")
    rows = []
    for item in data["rows"]:
        if item["source"] != source or item["category"] != category:
            raise ValueError("mixed cached taxonomy")
        item["change_rate"] = _optional(item["change_rate"], _number)
        item["turnover"] = _optional(item["turnover"], _number)
        for key in ("observed_at", "fetched_at"):
            item[key] = datetime.fromisoformat(item[key]) if item[key] else None
        rows.append(SectorRanking(**item))
    version = data.get("version")
    if version not in (None, 1, 2):
        raise ValueError("unknown cache version")
    received = data["row_count"] if version == 2 else data["total"]
    if len(rows) != received or not rows or len({r.code for r in rows}) != len(rows):
        raise ValueError("incomplete cached catalog")
    # Old 'total' was merely the serialized row count, never a supplier total.
    total = (
        _optional(data.get("quote_total"), _count)
        if version == 2 and source == "eastmoney"
        else None
    )
    complete = (
        data.get("quote_payload_complete") if version == 2 and source == "eastmoney" else None
    )
    if complete is not None and type(complete) is not bool:
        raise ValueError("invalid cached quote completeness")
    if complete and (total is None or sum(row.change_rate is not None for row in rows) != total):
        raise ValueError("cached quote count mismatch")
    listed = _optional(data.get("listed_received"), _count) if version == 2 else None
    catalog_fetched = data.get("catalog_fetched_at") if version == 2 else None
    catalog_fetched = datetime.fromisoformat(catalog_fetched) if catalog_fetched else None
    if catalog_fetched and (catalog_fetched.tzinfo is None or catalog_fetched > now):
        raise ValueError("invalid cached registry clock")
    return _CatalogRows(tuple(rows), total, complete, listed, catalog_fetched), fetched


def _cache_write(path, batch, now):
    rows = batch.rows
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="sector-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(
                {
                    "version": 2,
                    "row_count": len(rows),
                    "quote_total": batch.quote_total,
                    "quote_payload_complete": batch.quote_payload_complete,
                    "listed_received": batch.listed_received,
                    "catalog_fetched_at": batch.catalog_fetched_at,
                    "fetched_at": now.isoformat(),
                    "rows": [asdict(row) for row in rows],
                },
                output,
                default=lambda value: (
                    value.isoformat() if isinstance(value, datetime) else str(value)
                ),
                ensure_ascii=False,
            )
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_sector_catalogs(*, http_get, now, cache_dir=None, taxonomy_cache=None, transport=None):
    client = transport or PublicHttp(http_get, now=lambda: now)

    def get(url, **kwargs):
        return client.get(
            url, expected="text" if "sina" in url else "data", module="market_sectors", **kwargs
        )

    registry = _ReadState()
    registry_attempted = False
    registry_stale = False

    def read_taxonomy():
        nonlocal registry_attempted
        registry_attempted = True
        try:
            value = _taxonomy(lambda url, **kwargs: registry.get(get, url, **kwargs))
            registry.parse_ok = True
            return value
        except Exception:
            if registry.http_ok is True:
                registry.parse_ok = False
            raise

    try:
        if taxonomy_cache is not None:
            taxonomy, registry_stale = taxonomy_cache.read("taxonomy", read_taxonomy, ttl=86400)
        else:
            taxonomy = read_taxonomy()
    except Exception:
        taxonomy, registry_stale = None, True
    registry_cached = taxonomy is not None and (not registry_attempted or registry_stale)
    registry_error = (
        ("taxonomy_parse_failed" if registry.http_ok is True else "taxonomy_source_unavailable")
        if registry_stale
        else None
    )

    def load(job):
        source, category = job
        path = Path(cache_dir) / f"{source}-{category}.json" if cache_dir else None
        stale, error, fetched, cache_used = False, None, now, False
        quote = _ReadState()
        batch = _CatalogRows(())

        def checked_get(url, **kwargs):
            return quote.get(get, url, **kwargs)

        try:
            if source == "eastmoney":
                if taxonomy is None:
                    raise ValueError("official taxonomy unavailable")
                batch = _eastmoney(
                    checked_get, category, now, taxonomy[category], taxonomy.fetched_at
                )
            else:
                batch = _sina(checked_get, category, now)
            quote.parse_ok = True
            if path:
                with suppress(OSError):
                    _cache_write(path, batch, now)
        except Exception:
            error, stale = "source_unavailable", True
            if quote.http_ok is True:
                quote.parse_ok, error = False, "source_parse_failed"
            elif source == "eastmoney" and taxonomy is None:
                error = "taxonomy_unavailable"
            fetched = None
            if path:
                with suppress(OSError, ValueError, KeyError, TypeError, DecimalException):
                    batch, fetched = _cache_read(path, source, category, now)
                    cache_used = True
        rows = batch.rows
        if not rows and source == "eastmoney" and taxonomy:
            rows = tuple(
                SectorRanking(
                    name=name,
                    code=code,
                    change_rate=None,
                    leader=None,
                    category=category,
                    source=source,
                    fetched_at=taxonomy.fetched_at,
                )
                for code, name in taxonomy[category].items()
            )
            fetched = taxonomy.fetched_at
            batch = _CatalogRows(
                rows,
                quote_payload_complete=False,
                listed_received=len(taxonomy[category]),
                catalog_fetched_at=taxonomy.fetched_at,
            )
        elif rows and not stale:
            fetched = max((row.fetched_at for row in rows if row.fetched_at), default=fetched)
        observed = [row.observed_at for row in rows if row.observed_at]
        unquoted = sum(row.change_rate is None for row in rows)
        em = source == "eastmoney"
        catalog_stale = registry_stale or cache_used if em else stale
        catalog_error = (
            (registry_error or ("catalog_snapshot_cached" if cache_used else None)) if em else error
        )
        # Neither registry endpoint publishes an authoritative catalog total.
        # Eastmoney's clist total proves quote pagination only, not registry coverage.
        return rows, SectorCatalog(
            category=category,
            source=source,
            total=None,
            received=len(rows),
            complete=None,
            fetched_at=fetched,
            observed_at=min(observed) if observed else None,
            stale=stale or catalog_stale,
            error=error or catalog_error,
            unquoted=unquoted,
            quote_total=batch.quote_total,
            quote_payload_complete=batch.quote_payload_complete,
            quotes_complete=bool(rows) and unquoted == 0,
            http_ok=quote.http_ok,
            parse_ok=quote.parse_ok,
            source_time_known=bool(rows) and len(observed) == len(rows),
            cache_used=cache_used or (em and registry_cached),
            listed_received=batch.listed_received,
            catalog_fetched_at=batch.catalog_fetched_at,
            catalog_http_ok=registry.http_ok if em else quote.http_ok,
            catalog_parse_ok=registry.parse_ok if em else quote.parse_ok,
            catalog_cache_used=(registry_cached or cache_used) if em else cache_used,
            catalog_stale=catalog_stale,
            catalog_error=catalog_error,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = tuple(
            pool.map(
                load,
                ((source, category) for source in ("eastmoney", "sina") for category in CATEGORIES),
            )
        )
    return tuple(row for rows, _ in results for row in rows), tuple(
        catalog for _, catalog in results
    )
