"""Dated public Shanghai/Shenzhen breadth and matching index turnover in yuan."""

import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from quant_assistant.common.time import SHANGHAI_TZ


@dataclass(frozen=True, slots=True)
class MarketBreadth:
    advancing: int
    declining: int
    unchanged: int
    limit_up: int
    limit_down: int
    observed_at: datetime
    source: str
    turnover: Decimal | None = None
    previous_turnover: Decimal | None = None
    turnover_ratio: Decimal | None = None
    previous_trade_date: str | None = None
    stale: bool = False
    unavailable: tuple[str, ...] = ()


def _activity(http_get, now):
    response = http_get(
        "https://legulegu.com/stockdata/market-activity",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(3, 6),
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    timestamp = soup.select_one(".market-activity-time")
    if timestamp is None:
        raise ValueError("missing breadth observation time")
    observed = datetime.fromisoformat(timestamp.get_text(strip=True))
    observed = (
        observed.replace(tzinfo=SHANGHAI_TZ)
        if observed.tzinfo is None
        else observed.astimezone(SHANGHAI_TZ)
    )
    if observed > now or observed.year < 2000:
        raise ValueError("invalid breadth observation time")
    labels = {
        "上涨": "advancing",
        "下跌": "declining",
        "平盘": "unchanged",
        "涨停": "limit_up",
        "跌停": "limit_down",
    }
    counts = {}
    for row in soup.select("table tr"):
        cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
        for index in range(0, len(cells) - 1, 2):
            if cells[index] not in labels:
                continue
            key, value = labels[cells[index]], cells[index + 1].replace(",", "")
            if key in counts or not re.fullmatch(r"\d{1,6}", value):
                raise ValueError("invalid breadth count")
            counts[key] = int(value)
    if (
        len(counts) != len(labels)
        or sum(counts[key] for key in ("advancing", "declining", "unchanged")) == 0
    ):
        raise ValueError("incomplete breadth counts")
    if counts["limit_up"] > counts["advancing"] or counts["limit_down"] > counts["declining"]:
        raise ValueError("inconsistent breadth limit counts")
    return counts, observed


def _turnover_history(http_get, secid, now):
    # AKShare's index_zh_a_hist uses f57 for daily turnover in yuan. Fetch only
    # five sessions, then align both markets with the breadth source's date.
    response = http_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid,
            "klt": 101,
            "fqt": 0,
            "lmt": 5,
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        timeout=(3, 6),
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    rows = data.get("klines")
    if (
        payload.get("rc") != 0
        or data.get("code") != secid.split(".")[1]
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= 5
    ):
        raise ValueError("invalid index turnover history")
    result = {}
    for row in rows:
        fields = row.split(",")
        if len(fields) != 7:
            raise ValueError("missing index turnover")
        day = datetime.strptime(fields[0], "%Y-%m-%d").date()
        amount = Decimal(fields[6])
        if (
            day > now.date()
            or day.isoformat() != fields[0]
            or fields[0] in result
            or not amount.is_finite()
            or amount < 0
        ):
            raise ValueError("invalid dated index turnover")
        result[fields[0]] = amount
    return result


def _cached_turnover(path, now):
    if path is None:
        return None
    try:
        if not path.is_file() or path.is_symlink():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(payload["fetched_at"])
        if payload["source"] != "eastmoney" or fetched.tzinfo is None or fetched > now:
            return None
        result = []
        for market in ("shanghai", "shenzhen"):
            rows = payload[market]
            if not isinstance(rows, dict) or not 1 <= len(rows) <= 5:
                return None
            values = {}
            for day, raw in rows.items():
                date = datetime.strptime(day, "%Y-%m-%d").date()
                amount = Decimal(raw)
                if (
                    date.isoformat() != day
                    or date >= now.date()
                    or not amount.is_finite()
                    or amount < 0
                ):
                    return None
                values[day] = amount
            result.append(values)
        return result
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
        return None


def _cache_closed_turnover(path, shanghai, shenzhen, now):
    if path is None:
        return
    days = sorted(day for day in shanghai.keys() & shenzhen.keys() if day < now.date().isoformat())[
        -5:
    ]
    if not days:
        return
    temporary = None
    try:
        if path.is_symlink():
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "source": "eastmoney",
            "fetched_at": now.isoformat(),
            "shanghai": {day: str(shanghai[day]) for day in days},
            "shenzhen": {day: str(shenzhen[day]) for day in days},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".turnover-", delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(payload, file)
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError:
        pass  # The display remains usable when the optional disk cache is unavailable.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_market_breadth(*, http_get=requests.get, now=None, cache_path=None):
    now = now or datetime.now(SHANGHAI_TZ)
    if cache_path is None and os.environ.get("QUANT_SITE_ROOT"):
        cache_path = Path(os.environ["QUANT_SITE_ROOT"]) / "public-data-cache/market-turnover.json"
    cache_path = Path(cache_path) if cache_path is not None else None
    cached = _cached_turnover(cache_path, now)
    used_cache = False
    with ThreadPoolExecutor(max_workers=3) as pool:
        activity = pool.submit(_activity, http_get, now)
        histories = (
            []
            if cached is not None
            else [
                pool.submit(_turnover_history, http_get, secid, now)
                for secid in ("1.000001", "0.399001")
            ]
        )
        counts, observed = activity.result()
        day = observed.date().isoformat()
        if cached is not None and all(day in market for market in cached):
            shanghai, shenzhen = cached
            used_cache = True
        else:
            if not histories:
                histories = [
                    pool.submit(_turnover_history, http_get, secid, now)
                    for secid in ("1.000001", "0.399001")
                ]
            try:
                shanghai, shenzhen = [future.result() for future in histories]
            except Exception:  # noqa: BLE001 - a failed amount source must not erase valid breadth
                shanghai, shenzhen = {}, {}
    day = observed.date().isoformat()
    turnover = previous = ratio = previous_day = None
    unavailable = []
    if day in shanghai and day in shenzhen:
        turnover = shanghai[day] + shenzhen[day]
        shanghai_previous = max((key for key in shanghai if key < day), default=None)
        shenzhen_previous = max((key for key in shenzhen if key < day), default=None)
        if shanghai_previous is not None and shanghai_previous == shenzhen_previous:
            previous_day = shanghai_previous
            previous = shanghai[previous_day] + shenzhen[previous_day]
            if previous > 0:
                ratio = turnover / previous
        if ratio is None:
            unavailable.append("turnover_ratio")
    else:
        unavailable.append("turnover")
    if turnover is not None and not used_cache:
        _cache_closed_turnover(cache_path, shanghai, shenzhen, now)
    return MarketBreadth(
        **counts,
        observed_at=observed,
        source="legulegu+eastmoney" if turnover is not None else "legulegu",
        turnover=turnover,
        previous_turnover=previous,
        turnover_ratio=ratio,
        previous_trade_date=previous_day,
        unavailable=tuple(unavailable),
    )
