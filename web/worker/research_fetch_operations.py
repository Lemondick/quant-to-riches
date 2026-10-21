"""Research-report and deep-news aggregation (``research.fetch``).

Contract (batch3): payload ``{symbols: [], kinds: [report, news]}`` →
``{items: [{title, summary, source, url, published_at, symbol}], deduped: true}``.

Design mirrors ``announcements.fetch``: a pluggable ``_RESEARCH_SOURCES``
registry (name → fetcher), injectable ``http_get`` for deterministic tests,
URL-based dedup, per-source failures degrade to warnings, never errors.

Default sources (all public and free, no API key, verified 2026-10-21):
- ``eastmoney_reports`` (kind=report): 东方财富研报中心公开接口
  ``https://reportapi.eastmoney.com/report/list``（个股研报，按 ``stockCodes``
  查询；基金代码无对应研报时自然返回空，不虚构）。
  详情页 URL 形如 ``https://data.eastmoney.com/report/info/{infoCode}.html``。
- ``eastmoney_news`` (kind=news): 东方财富资讯搜索公开接口
  ``https://search-api-web.eastmoney.com/search/jsonp``（按关键词查询财经资讯）。

The default set is documented in ``DEFAULT_RESEARCH_SOURCES`` and can be
overridden with the op's ``sources`` argument (unknown names → warning).
``kinds`` filters sources by kind (``report`` / ``news``); default both.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import date
from html import unescape

import requests

RESEARCH_FETCH_CAPABILITIES = ("research.fetch",)

_REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list"
_REPORT_INFO_URL = "https://data.eastmoney.com/report/info/{info_code}.html"
_NEWS_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_PAGE_SIZE = 10
_USER_AGENT = "Mozilla/5.0"

DEFAULT_RESEARCH_SOURCES = ("eastmoney_reports", "eastmoney_news")

_SOURCE_KIND = {
    "eastmoney_reports": "report",
    "eastmoney_news": "news",
}

_TAG_RE = re.compile(r"</?em>")


def _clean(text: str) -> str:
    return unescape(_TAG_RE.sub("", text or "")).strip()


def _fetch_reports(symbols: Sequence[str], http_get) -> list[dict]:
    items: list[dict] = []
    for symbol in symbols:
        response = http_get(
            _REPORT_LIST_URL,
            params={
                "industryCode": "*",
                "pageSize": _PAGE_SIZE,
                "beginTime": "",
                "endTime": "",
                "fields": "",
                "qType": "0",
                "orgCode": "",
                "pageNo": 1,
                "pageNum": _PAGE_SIZE,
                "term": "",
                "stockCodes": symbol,
                "sort": "publishDate desc",
                "_": round(time.time() * 1000),
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=(3, 10),
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, str):
            raise ValueError("unexpected report payload")
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            title = str(row.get("title") or "").strip()
            info_code = str(row.get("infoCode") or "").strip()
            if not (title and info_code):
                continue
            org = str(row.get("orgSName") or row.get("orgName") or "").strip()
            rating = str(row.get("emRatingName") or "").strip()
            published_raw = str(row.get("publishDate") or "").strip()
            try:
                published_at = date.fromisoformat(published_raw[:10]).isoformat()
            except ValueError:
                published_at = None
            summary_bits = [part for part in (org, f"评级：{rating}" if rating else "", title) if part]
            items.append(
                {
                    "title": title,
                    "summary": " | ".join(summary_bits),
                    "source": "东方财富研报中心" + (f"·{org}" if org else ""),
                    "url": _REPORT_INFO_URL.format(info_code=info_code),
                    "published_at": published_at,
                    "symbol": symbol,
                    "kind": "report",
                }
            )
    return items


def _fetch_news(symbols: Sequence[str], http_get) -> list[dict]:
    items: list[dict] = []
    for symbol in symbols:
        callback = f"jQuery_research_{round(time.time() * 1000)}"
        param = {
            "uid": "",
            "keyword": symbol,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": _PAGE_SIZE,
                }
            },
        }
        response = http_get(
            _NEWS_SEARCH_URL,
            params={"cb": callback, "param": json.dumps(param, ensure_ascii=False)},
            headers={"User-Agent": _USER_AGENT},
            timeout=(3, 10),
        )
        response.raise_for_status()
        text = response.text.strip()
        match = re.match(r"^[^(]*\((.*)\)\s*;?\s*$", text, re.DOTALL)
        if not match:
            raise ValueError("unexpected news payload")
        payload = json.loads(match.group(1))
        result = payload.get("result") if isinstance(payload, Mapping) else None
        rows = result.get("cmsArticleWebOld") if isinstance(result, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, str):
            raise ValueError("unexpected news payload")
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            title = _clean(str(row.get("title") or ""))
            url = str(row.get("url") or "").strip()
            if not (title and url):
                continue
            summary = _clean(str(row.get("content") or ""))[:200]
            published_raw = str(row.get("date") or "").strip()
            try:
                published_at = date.fromisoformat(published_raw[:10]).isoformat()
            except ValueError:
                published_at = None
            items.append(
                {
                    "title": title,
                    "summary": summary or title,
                    "source": str(row.get("mediaName") or "东方财富资讯").strip(),
                    "url": url,
                    "published_at": published_at,
                    "symbol": symbol,
                    "kind": "news",
                }
            )
    return items


_RESEARCH_SOURCES = {
    "eastmoney_reports": _fetch_reports,
    "eastmoney_news": _fetch_news,
}


def _normalise_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def fetch_research(
    symbols: Sequence[str],
    kinds: Sequence[str] | None = None,
    *,
    sources: Sequence[str] | None = None,
    http_get=None,
) -> dict:
    """Core computation; ``http_get`` injectable for deterministic tests."""
    if (
        not isinstance(symbols, Sequence)
        or isinstance(symbols, (str, bytes))
        or not symbols
    ):
        raise ValueError("symbols must be a non-empty list")
    clean_symbols = []
    for position, raw in enumerate(symbols):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"symbols[{position}] must be a non-empty string")
        clean_symbols.append(raw.strip())
    if kinds is None:
        kinds = ("report", "news")
    if (
        not isinstance(kinds, Sequence)
        or isinstance(kinds, (str, bytes))
        or not kinds
        or any(k not in ("report", "news") for k in kinds)
    ):
        raise ValueError("kinds must be a non-empty list of 'report'/'news'")
    if sources is None:
        wanted = list(DEFAULT_RESEARCH_SOURCES)
    else:
        if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
            raise ValueError("sources must be a list of source names")
        wanted = [str(name) for name in sources]

    http_get = http_get or requests.get
    warnings: list[str] = []
    items: list[dict] = []
    seen: set[str] = set()
    sources_used: list[str] = []
    for name in wanted:
        fetcher = _RESEARCH_SOURCES.get(name)
        if fetcher is None:
            warnings.append(f"未知研报来源 {name!r}，已跳过（可用：{sorted(_RESEARCH_SOURCES)}）")
            continue
        if _SOURCE_KIND[name] not in kinds:
            continue
        sources_used.append(name)
        try:
            fetched = fetcher(clean_symbols, http_get)
        except Exception:
            warnings.append(f"{name}: 抓取失败，已跳过")
            continue
        for item in fetched:
            key = _normalise_url(item["url"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    items.sort(
        key=lambda item: (item.get("published_at") or "", item["source"], item["title"]),
        reverse=True,
    )
    if not items and not warnings:
        warnings.append("所选来源在指定范围内无研报/资讯")
    return {
        "as_of": date.today().isoformat(),
        "items": items,
        "deduped": True,
        "sources_used": sources_used,
        "warnings": warnings,
    }


def execute_research_fetch(kind: str, payload: dict) -> dict:
    if kind not in RESEARCH_FETCH_CAPABILITIES:
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    return fetch_research(
        payload.get("symbols", ()),
        payload.get("kinds"),
        sources=payload.get("sources"),
    )
