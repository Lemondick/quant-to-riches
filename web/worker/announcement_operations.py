"""Held-fund announcement aggregation (``announcements.fetch``).

Contract (batch2 §2): payload ``{fund_codes: [], since?: date}`` →
``{announcements: [{fund_code, title, kind, published_at, source_url, summary}]}``,
``kind`` ∈ ``dividend`` | ``manager_change`` | ``liquidation_risk`` | ``other``.

Data source (documented assumption): the primary source is Eastmoney's
public fund-announcement JSON endpoint
(``api.fund.eastmoney.com/f10/JJGG``) — the same stable endpoint the
existing ``PublicFundDetailProvider._announcements`` already uses, so no
new scraping surface is introduced. The source is configurable via the
``source`` argument of :func:`fetch_announcements` (``_SOURCES`` registry);
any fetch/parse failure degrades to an empty list + warning, never an
error, per the contract.

Kind mapping is keyword-based on the announcement title (documented):
- dividend: 分红/分派/收益分配
- manager_change: 基金经理变更/增聘/解聘/离任
- liquidation_risk: 清盘/清算/终止运作/合同终止/连续N个工作日触发
- other: everything else
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from datetime import date

import requests

ANNOUNCEMENT_CAPABILITIES = ("announcements.fetch",)

_JJGG_URL = "https://api.fund.eastmoney.com/f10/JJGG"
_PAGE_SIZE = 20

_KIND_PATTERNS = (
    ("dividend", re.compile(r"分红|分派|收益分配")),
    (
        "manager_change",
        re.compile(r"基金经理.*(变更|增聘|解聘|离任)|增聘.*基金经理|解聘.*基金经理"),
    ),
    (
        "liquidation_risk",
        re.compile(r"清盘|清算|终止运作|合同终止|连续\d+个工作日"),
    ),
)


def classify_kind(title: str) -> str:
    """Map an announcement title to the contract kind (documented heuristic)."""
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(title):
            return kind
    return "other"


def _fetch_eastmoney(code: str, http_get) -> list[dict]:
    response = http_get(
        _JJGG_URL,
        params={
            "fundcode": code,
            "pageIndex": 1,
            "pageSize": _PAGE_SIZE,
            "type": 3,
            "_": round(time.time() * 1000),
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://fundf10.eastmoney.com/jjgg_{code}_3.html",
        },
        timeout=(3, 10),
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("Data") if isinstance(payload, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        raise ValueError("unexpected announcement payload")
    items = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        identifier = str(row.get("ID") or "").strip()
        title = str(row.get("TITLE") or "").strip()
        published_raw = str(row.get("PUBLISHDATE") or "").strip()
        if not (identifier and title and published_raw):
            continue
        try:
            published_at = date.fromisoformat(published_raw[:10])
        except ValueError:
            continue
        items.append(
            {
                "fund_code": code,
                "title": title,
                "kind": classify_kind(title),
                "published_at": published_at.isoformat(),
                "source_url": f"https://fund.eastmoney.com/gonggao/{code},{identifier}.html",
                "summary": title,
            }
        )
    return items


_SOURCES = {"eastmoney": _fetch_eastmoney}


def fetch_announcements(
    fund_codes: Sequence[str],
    since: date | None = None,
    *,
    source: str = "eastmoney",
    http_get=None,
) -> dict:
    """Core computation; ``http_get`` injectable for deterministic tests."""
    if (
        not isinstance(fund_codes, Sequence)
        or isinstance(fund_codes, (str, bytes))
        or not fund_codes
    ):
        raise ValueError("fund_codes must be a non-empty list")
    if since is not None and not isinstance(since, date):
        raise ValueError("since must be a date")
    fetcher = _SOURCES.get(source)
    if fetcher is None:
        raise ValueError(f"unsupported announcement source: {source!r}")
    http_get = http_get or requests.get
    announcements: list[dict] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for position, raw_code in enumerate(fund_codes):
        if not isinstance(raw_code, str) or not raw_code.strip():
            raise ValueError(f"fund_codes[{position}] must be a non-empty string")
        code = raw_code.strip()
        try:
            items = fetcher(code, http_get)
        except Exception:
            warnings.append(f"{code}: 公告源抓取失败，已跳过")
            continue
        for item in items:
            if since is not None and item["published_at"] < since.isoformat():
                continue
            key = (code, item["source_url"])
            if key in seen:
                continue
            seen.add(key)
            announcements.append(item)
    announcements.sort(key=lambda item: (item["published_at"], item["fund_code"]), reverse=True)
    if not announcements and not warnings:
        warnings.append("所选基金在指定时间范围内无公告")
    return {
        "as_of": date.today().isoformat(),
        "announcements": announcements,
        "warnings": warnings,
    }


def _parse_since(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("since must be an ISO date") from exc
    raise ValueError("since must be an ISO date string")


def execute_announcements(kind: str, payload: dict) -> dict:
    if kind != "announcements.fetch":
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    return fetch_announcements(payload.get("fund_codes", ()), _parse_since(payload.get("since")))
