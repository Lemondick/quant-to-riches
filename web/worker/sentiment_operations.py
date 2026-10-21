"""Market sentiment snapshot (``sentiment.fetch``).

Contract (batch5): payload ``{markets: ["CN"]}`` →
``{items: [{name, value, unit, updated_at, source}], warning?}``.

Compliance first: only public, free, no-auth data sources (exchange public
pages/APIs and public market-data endpoints). Every source's reachability
is verified at fetch time; a source that is down or returns an unexpected
shape marks its metric missing — the worker never fabricates, estimates or
backfills market data. When no compliant source answers at all the op
returns ``{items: [], warning: "数据源未接入"}``.

Public sources (verified reachable 2026-10-21):
- ``eastmoney_index_quotes`` — 东方财富公开行情接口（延时）:
  ``https://push2delay.eastmoney.com/api/qt/stock/get`` with
  ``secid=1.000001`` (上证指数) / ``secid=0.399001`` (深证成指),
  fields ``f48`` (成交额，元) and ``f168`` (换手率，%).
  → 两市成交额（两市 f48 相加）, 上证换手率, 深证换手率.
- ``eastmoney_market_breadth`` — same host, ``/api/qt/ulist.np/get``
  with ``secids=1.000001,0.399001``, fields ``f104/f105/f106``
  (上涨/下跌/平盘家数). → A股上涨家数, A股下跌家数, A股涨跌家数比.
- ``sse_margin`` — 上交所公开接口:
  ``https://query.sse.com.cn/marketdata/tradedata/queryMargin.do``
  (field ``rzye``, 融资余额，元). → 融资余额（沪市）.

Known gaps (documented, not hidden):
- 深交所融资余额：其公开 ``/api/market/rzrq`` 接口返回 404，东方财富数据中心
  无对应聚合报表配置；故仅提供沪市融资余额，并在部分缺失时于 warning 说明。
- 东方财富为延时行情，来源标注中明确。

Per-source terminal outcomes are reported to the F3
``PublicSourceFailureTracker`` (``public_source_failures``) when a tracker
is available, so stream A keeps the consecutive-failure alerting working.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from public_source_failures import PublicSourceFailureTracker, alert_rounds_from_env

SENTIMENT_CAPABILITIES = frozenset({"sentiment.fetch"})

_USER_AGENT = "Mozilla/5.0"
_TIMEOUT = (3, 10)

#: A-share index secids: 1.000001 = 上证指数, 0.399001 = 深证成指.
_SH_SEC_ID = "1.000001"
_SZ_SEC_ID = "0.399001"

_QUOTE_URL = "https://push2delay.eastmoney.com/api/qt/stock/get"
_BREADTH_URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
_SSE_MARGIN_URL = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"

_EASTMONEY_UT = "fa5fd1943c7b386f172bc3b111db38b"

_JSONP_RE = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.DOTALL)

_AUTO_TRACKER = object()

SOURCE_LABEL_QUOTES = "东方财富公开行情（延时）"
SOURCE_LABEL_BREADTH = "东方财富公开行情（延时）"
SOURCE_LABEL_SSE = "上海证券交易所"


def _get(http_get, url, params, referer=None):
    headers = {"User-Agent": _USER_AGENT}
    if referer:
        headers["Referer"] = referer
    response = http_get(url, params=params, headers=headers, timeout=_TIMEOUT)
    response.raise_for_status()
    return response


def _as_number(value: object, field: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric: {value!r}") from exc
    if number != number:  # NaN guard
        raise ValueError(f"{field} is NaN")
    return number


def _item(name, value, unit, updated_at, source):
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "updated_at": updated_at,
        "source": source,
    }


def _fetch_index_quotes(http_get) -> list[dict]:
    """两市成交额 + 上证/深证换手率 (eastmoney delayed index quotes)."""
    turnover = 0.0
    turnover_rates: dict[str, float] = {}
    for sec_id, label in ((_SH_SEC_ID, "上证"), (_SZ_SEC_ID, "深证")):
        payload = _get(
            http_get,
            _QUOTE_URL,
            {
                "secid": sec_id,
                "fields": "f48,f168",
                "ut": _EASTMONEY_UT,
                "fltt": 2,
                "invt": 2,
                "_": round(time.time() * 1000),
            },
        ).json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("unexpected index quote payload")
        turnover += _as_number(data.get("f48"), f"f48({sec_id})")
        turnover_rates[label] = _as_number(data.get("f168"), f"f168({sec_id})")
    today = date.today().isoformat()
    return [
        _item("两市成交额", round(turnover / 1e8, 2), "亿元", today, SOURCE_LABEL_QUOTES),
        _item("上证换手率", round(turnover_rates["上证"], 2), "%", today, SOURCE_LABEL_QUOTES),
        _item("深证换手率", round(turnover_rates["深证"], 2), "%", today, SOURCE_LABEL_QUOTES),
    ]


def _fetch_market_breadth(http_get) -> list[dict]:
    """A股涨跌家数与涨跌家数比 (eastmoney index breadth fields)."""
    payload = _get(
        http_get,
        _BREADTH_URL,
        {
            "fltt": 2,
            "secids": f"{_SH_SEC_ID},{_SZ_SEC_ID}",
            "fields": "f104,f105,f106",
            "ut": _EASTMONEY_UT,
            "invt": 2,
            "_": round(time.time() * 1000),
        },
    ).json()
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("diff") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("unexpected market breadth payload")
    up = down = flat = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        up += int(_as_number(row.get("f104"), "f104"))
        down += int(_as_number(row.get("f105"), "f105"))
        flat += int(_as_number(row.get("f106"), "f106"))
    ratio = round(up / down, 4) if down > 0 else None
    today = date.today().isoformat()
    items = [
        _item("A股上涨家数", up, "家", today, SOURCE_LABEL_BREADTH),
        _item("A股下跌家数", down, "家", today, SOURCE_LABEL_BREADTH),
    ]
    items.append(_item("A股涨跌家数比", ratio, "上涨家数/下跌家数", today, SOURCE_LABEL_BREADTH))
    return items


def _fetch_sse_margin(http_get) -> list[dict]:
    """沪市融资余额 (SSE public margin API; rzye in 元)."""
    today = date.today()
    callback = f"jsonpCallback{round(time.time() * 1000)}"
    text = _get(
        http_get,
        _SSE_MARGIN_URL,
        {
            "jsonCallBack": callback,
            "isPagination": "false",
            "inBeginDate": (today - timedelta(days=30)).strftime("%Y%m%d"),
            "inEndDate": today.strftime("%Y%m%d"),
            "_": round(time.time() * 1000),
        },
        referer="https://www.sse.com.cn/",
    ).text.strip()
    match = _JSONP_RE.match(text)
    if not match:
        raise ValueError("unexpected SSE margin payload")
    payload = json.loads(match.group(1))
    page_help = payload.get("pageHelp") if isinstance(payload, dict) else None
    rows = page_help.get("data") if isinstance(page_help, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("unexpected SSE margin payload")
    latest = max(
        (row for row in rows if isinstance(row, dict) and row.get("opDate")),
        key=lambda row: str(row["opDate"]),
        default=None,
    )
    if latest is None:
        raise ValueError("unexpected SSE margin payload")
    balance = _as_number(latest.get("rzye"), "rzye")
    raw_date = str(latest["opDate"])
    try:
        updated_at = date(int(raw_date[0:4]), int(raw_date[4:6]), int(raw_date[6:8])).isoformat()
    except ValueError as exc:
        raise ValueError(f"bad opDate {raw_date!r}") from exc
    return [
        _item("融资余额（沪市）", round(balance / 1e8, 2), "亿元", updated_at, SOURCE_LABEL_SSE),
    ]


_SENTIMENT_SOURCES = {
    "eastmoney_index_quotes": (
        "push2delay.eastmoney.com",
        "指数行情（成交额/换手率）",
        _fetch_index_quotes,
    ),
    "eastmoney_market_breadth": (
        "push2delay.eastmoney.com",
        "市场宽度（涨跌家数）",
        _fetch_market_breadth,
    ),
    "sse_margin": ("query.sse.com.cn", "沪市融资余额", _fetch_sse_margin),
}

_DEFAULT_SOURCES = tuple(_SENTIMENT_SOURCES)


def _resolve_tracker(failure_tracker):
    if failure_tracker is not _AUTO_TRACKER:
        return failure_tracker
    root = os.environ.get("QUANT_SITE_ROOT")
    if not root:
        return None
    try:
        return PublicSourceFailureTracker(
            Path(root) / ".public-source-failures.json",
            alert_rounds=alert_rounds_from_env(),
        )
    except Exception:
        return None


def fetch_sentiment(
    markets,
    *,
    sources=None,
    http_get=None,
    failure_tracker=_AUTO_TRACKER,
) -> dict:
    """Core computation; ``http_get`` injectable for deterministic tests.

    Each source is fetched independently: a failure marks only its own
    metric missing (warning), never the whole op. ``failure_tracker``
    reuses the F3 framework; pass ``None`` to disable recording.
    """
    if not isinstance(markets, (list, tuple)) or not markets:
        raise ValueError("markets must be a non-empty list")
    clean_markets = []
    for position, raw in enumerate(markets):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"markets[{position}] must be a non-empty string")
        clean_markets.append(raw.strip().upper())

    if sources is None:
        wanted = list(_DEFAULT_SOURCES)
    else:
        if not isinstance(sources, (list, tuple)) or isinstance(sources, (str, bytes)):
            raise ValueError("sources must be a list of source names")
        wanted = [str(name) for name in sources]

    tracker = _resolve_tracker(failure_tracker)
    http_get = http_get or requests.get
    warnings: list[str] = []
    items: list[dict] = []
    sources_used: list[str] = []

    supported = "CN" in clean_markets
    for other in clean_markets:
        if other != "CN":
            warnings.append(f"暂不支持的市场 {other!r}（仅支持 CN），已跳过")

    if supported:
        for name in wanted:
            entry = _SENTIMENT_SOURCES.get(name)
            if entry is None:
                warnings.append(
                    f"未知情绪来源 {name!r}，已跳过（可用：{sorted(_SENTIMENT_SOURCES)}）"
                )
                continue
            host, label, fetcher = entry
            try:
                fetched = fetcher(http_get)
            except Exception as exc:  # noqa: BLE001 - per-source degradation
                warnings.append(f"{label}：抓取失败，相关指标缺失（{type(exc).__name__}）")
                if tracker is not None:
                    with contextlib.suppress(Exception):
                        tracker.record(host, ok=False, error=type(exc).__name__)
                continue
            if tracker is not None:
                with contextlib.suppress(Exception):
                    tracker.record(host, ok=True)
            sources_used.append(name)
            items.extend(fetched)

    if not supported:
        warnings.append("markets 中无 CN，A股情绪指标无可用来源")
    # 深市融资余额：经验证无合规公开源（深交所 rzrq 接口 404、东财数据中心无聚合报表），
    # 如实声明缺失，绝不以沪市数据冒充两市。
    if supported and "sse_margin" in sources_used:
        warnings.append("深市融资余额暂无合规公开源，仅提供沪市融资余额")

    if not items:
        result = {"items": [], "warning": "数据源未接入"}
        if warnings:
            result["warnings"] = warnings
        return result

    result = {"items": items, "sources_used": sources_used, "warnings": warnings}
    if warnings:
        result["warning"] = "；".join(warnings)
    return result


def execute_sentiment(kind: str, payload: dict) -> dict:
    if kind not in SENTIMENT_CAPABILITIES:
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    return fetch_sentiment(
        payload.get("markets", ["CN"]),
        sources=payload.get("sources"),
    )
