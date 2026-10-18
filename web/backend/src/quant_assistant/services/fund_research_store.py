"""Append public fund observations without writing ledger or admission evidence."""

import hashlib
import json
import time
from concurrent.futures import Future
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Lock, Thread

from quant_assistant.providers.fund_valuation import normalize_fund_symbol

PUBLIC_SOURCES = {"eastmoney", "sina", "danjuan", "tencent", "eastmoney+danjuan"}


def encode(value):
    return json.dumps(value, default=str, sort_keys=True, ensure_ascii=False, allow_nan=False)


def fingerprint(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def day(value):
    return date.fromisoformat(str(value)[:10])


def positive(value):
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("invalid positive number")
    return number


def persist_public(connection, data, now):
    observed = datetime.fromisoformat(str(data["observed_at"]))
    if (
        data["stale"]
        or data["source"] not in PUBLIC_SOURCES
        or not timedelta(0) <= now - observed <= timedelta(hours=24)
        or data["symbol"] != normalize_fund_symbol(data["symbol"])
    ):
        raise ValueError("unverified public fund observation")
    seen = set()
    for point in data["nav_history"]:
        nav_day = day(point["nav_date"])
        if nav_day in seen or nav_day > observed.date() or point["source"] not in PUBLIC_SOURCES:
            raise ValueError("invalid public NAV dates or source")
        positive(point["unit_nav"])
        if point["accumulated_nav"] is not None:
            positive(point["accumulated_nav"])
        seen.add(nav_day)
    raw = encode(data)
    digest = fingerprint(raw)
    connection.execute(
        "INSERT INTO fund_research_snapshots VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
        [digest, data["symbol"], observed, raw],
    )
    return digest


class FundResearchStore:
    def __init__(self, database, provider, now):
        self.database, self.provider, self.now = database, provider, now
        self.refresh_lock = Lock()

    def save(self, detail):
        with self.database.transaction() as connection:
            return persist_public(connection, asdict(detail), self.now())

    def refresh(self, symbols):
        symbols = list(dict.fromkeys(normalize_fund_symbol(s) for s in symbols))
        if not 1 <= len(symbols) <= 6:
            raise ValueError("at most six public funds")
        if not self.refresh_lock.acquire(blocking=False):
            raise ValueError("public refresh already running")
        results = []
        deadline = time.monotonic() + 60
        try:
            for symbol in symbols:
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    request = Future()

                    def fetch(code=symbol, future=request):
                        try:
                            future.set_result(self.provider.detail(code))
                        except Exception as error:
                            future.set_exception(error)

                    Thread(target=fetch, name="fund-research-public", daemon=True).start()
                    detail = request.result(timeout=min(20, remaining))
                    if detail.symbol != symbol:
                        raise ValueError("fund symbol mismatch")
                    self.save(detail)
                    results.append({"symbol": symbol, "status": "saved"})
                except Exception:
                    results.append(
                        {"symbol": symbol, "status": "unavailable", "message": "公开资料未能核验"}
                    )
        finally:
            self.refresh_lock.release()
        return {"items": results, "research_only": True, "model_calls": 0}

    def snapshots(self, symbol):
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT fingerprint,snapshot_json FROM fund_research_snapshots "
                "WHERE symbol=? AND observed_at<=? "
                "ORDER BY observed_at DESC,fingerprint DESC LIMIT 100",
                [symbol, self.now()],
            ).fetchall()
        items = []
        for digest, raw in rows:
            if fingerprint(raw) != digest:
                raise ValueError("public observation hash mismatch")
            item = json.loads(raw)
            if item["symbol"] != symbol or item["source"] not in PUBLIC_SOURCES or item["stale"]:
                raise ValueError("invalid public observation")
            items.append(item)
        return items

    def navs(self, symbol):
        points = {}
        for item in self.snapshots(symbol):
            observed = datetime.fromisoformat(item["observed_at"])
            for point in item["nav_history"]:
                if (
                    point["source"] not in PUBLIC_SOURCES
                    or day(point["nav_date"]) > observed.date()
                ):
                    raise ValueError("invalid public NAV observation")
                key = point["nav_date"]
                value = (
                    positive(point["unit_nav"]),
                    positive(point["accumulated_nav"])
                    if point["accumulated_nav"] is not None
                    else None,
                )
                if key in points and points[key] != value:
                    raise ValueError("public NAV revision needs review")
                points[key] = value
        return sorted(points.items())
