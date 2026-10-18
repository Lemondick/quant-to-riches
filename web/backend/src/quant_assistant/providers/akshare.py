"""AKShare adapter that normalizes provider-specific response columns."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import TypeVar

import pandas as pd
from requests import exceptions as requests_exceptions

from quant_assistant.providers._lazy_akshare import ak, demjson
from quant_assistant.providers.akshare_retry import call_with_retry as _call_with_retry
from quant_assistant.providers.akshare_symbols import (
    akshare_index_symbol as _akshare_index_symbol,
)
from quant_assistant.providers.akshare_symbols import (
    bare_symbol as _bare_symbol,
)
from quant_assistant.providers.akshare_symbols import (
    date_from_fund_column as _date_from_fund_column,
)
from quant_assistant.providers.akshare_symbols import (
    find_fund_column as _find_fund_column,
)
from quant_assistant.providers.akshare_symbols import (
    first_matching_column as _first_column,
)
from quant_assistant.providers.akshare_symbols import (
    normalize_a_share_symbol as _normalize_a_share_symbol,
)
from quant_assistant.providers.akshare_symbols import (
    normalize_index_symbol as _normalize_index_symbol,
)
from quant_assistant.providers.akshare_symbols import (
    stock_board as _stock_board,
)
from quant_assistant.providers.akshare_symbols import (
    stock_exchange as _stock_exchange,
)
from quant_assistant.providers.akshare_symbols import (
    tx_symbol as _tx_symbol,
)
from quant_assistant.providers.base import (
    ProviderDataError,
    ProviderError,
    ProviderUnavailable,
)

from .akshare_parsers import (
    _coerce_prices,
    _extract_alpha_stability,
    _extract_win_rate,
    _fetch_tencent_price_history,
    _fetch_tencent_stock_daily,
    _normalize_index_valuation,
    _normalize_stock_spot,
    _source_for_function,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
_TRANSIENT_ERRORS = (
    ConnectionError,
    TimeoutError,
    requests_exceptions.RequestException,
)

# Tencent's public endpoints expose positional fields and hard response caps;
# keep those protocol details named and tested instead of scattering literals.

TENCENT_QUOTE_BATCH_SIZE = 500
TENCENT_KLINE_LIMIT = 640
TENCENT_QUOTE_DATE_FIELD = 30
TENCENT_QUOTE_HIGH_FIELD = 33
TENCENT_QUOTE_LOW_FIELD = 34
TENCENT_QUOTE_SUMMARY_FIELD = 35
TENCENT_KLINE_CACHE_BUSTER = "0.8205512681390605"
TENCENT_VOLUME_SCALE = 100
TENCENT_AMOUNT_SCALE = 10_000
PERCENT_VALUE_THRESHOLD = 100
INDUSTRY_MAP_CACHE_SECONDS = 6 * 60 * 60
INDUSTRY_MAP_MAX_WORKERS = 8

_STOCK_PRICE_COLUMNS = {
    "日期": "trade_date",
    "date": "trade_date",
    "开盘": "open",
    "open": "open",
    "最高": "high",
    "high": "high",
    "最低": "low",
    "low": "low",
    "收盘": "close",
    "close": "close",
    "成交量": "volume",
    "volume": "volume",
    "成交额": "amount",
    "amount": "amount",
}
_PRICE_FIELDS = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]

_INDEX_VALUATION_LABELS = {
    "000016.SH": "上证50",
    "000300.SH": "沪深300",
    "000688.SH": "科创50",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "399006.SZ": "创业板指",
    "000510.SH": "中证500",
}


class AkshareProvider:
    """Free-data provider with bounded retry and strict response validation."""

    name = "akshare"
    history_snapshot_source_prefix = "akshare-"

    def __init__(self, *, sleep: Callable[[float], None] = time.sleep):
        self._sleep = sleep
        self._last_stock_history_source: str | None = None
        self._last_stock_daily_source: str | None = None
        self._eastmoney_quotes_available: bool | None = None
        self._active_source: str | None = None
        self._source_state: dict[str, dict[str, object]] = {
            source: {
                "failures": 0,
                "last_error": None,
                "last_success_at": None,
            }
            for source in ("eastmoney", "sina", "tencent", "exchange_official")
        }
        self._industry_map_cache: tuple[float, dict[str, str]] | None = None

    @property
    def active_source(self) -> str | None:
        """Return the last successful upstream source, if one is known."""

        return self._active_source

    @property
    def last_stock_history_source(self) -> str | None:
        return self._last_stock_history_source

    @property
    def last_stock_daily_source(self) -> str | None:
        return self._last_stock_daily_source

    def source_status(self) -> pd.DataFrame:
        """Expose source health for the system-status page."""

        return pd.DataFrame(
            [
                {
                    "source": source,
                    "available": int(state["failures"]) == 0,
                    "consecutive_failures": int(state["failures"]),
                    "last_error": state["last_error"],
                    "last_success_at": state["last_success_at"],
                    "cooldown_until": None,
                }
                for source, state in self._source_state.items()
            ]
        )

    def fetch_stock_universe(self) -> pd.DataFrame:
        try:
            if self._eastmoney_quotes_available is False:
                raise ProviderUnavailable("Eastmoney quote circuit is open")
            raw = self.call_with_retry(ak.stock_zh_a_spot_em)
            self._eastmoney_quotes_available = True
        except ProviderUnavailable:
            self._eastmoney_quotes_available = False
            raw = self._fetch_official_stock_universe()
        required = {"代码", "名称"}
        self._require_columns(raw, required, "stock_universe")
        codes = raw["代码"].astype("string").str.zfill(6)
        industry = raw.get("行业", pd.Series(pd.NA, index=raw.index, dtype="string"))
        if industry.isna().any() or industry.astype(str).str.strip().eq("").any():
            industry_map = self._fetch_industry_map()
            industry = industry.replace("", pd.NA).fillna(codes.map(industry_map))
        if "listed_on" in raw:
            listed_on = pd.to_datetime(raw["listed_on"], errors="coerce").dt.date
        else:
            listed_on = codes.map(self._fetch_listing_date_map())
        result = pd.DataFrame(
            {
                "symbol": codes.map(_normalize_a_share_symbol),
                "asset_type": "stock",
                "name": raw["名称"].astype("string").str.strip(),
                "exchange": codes.map(_stock_exchange),
                "industry": industry,
                "board": codes.map(_stock_board),
                "listed_on": listed_on,
                "is_st": raw["名称"].astype(str).str.upper().str.contains("ST", regex=False),
                "is_suspended": pd.to_numeric(raw.get("最新价"), errors="coerce").isna()
                if "最新价" in raw
                else False,
            }
        )
        return result.reset_index(drop=True)

    def _fetch_official_stock_universe(self) -> pd.DataFrame:
        sh_main = self.call_with_retry(ak.stock_info_sh_name_code, symbol="主板A股")
        sh_star = self.call_with_retry(ak.stock_info_sh_name_code, symbol="科创板")
        sz = self.call_with_retry(ak.stock_info_sz_name_code, symbol="A股列表")
        bj = self.call_with_retry(ak.stock_info_bj_name_code)
        self._require_columns(sh_main, {"证券代码", "证券简称", "上市日期"}, "sh_universe")
        self._require_columns(sh_star, {"证券代码", "证券简称", "上市日期"}, "star_universe")
        self._require_columns(
            sz,
            {"A股代码", "A股简称", "A股上市日期", "所属行业"},
            "sz_universe",
        )
        self._require_columns(bj, {"证券代码", "证券简称", "上市日期", "所属行业"}, "bj_universe")
        frames = [
            sh_main.rename(
                columns={"证券代码": "代码", "证券简称": "名称", "上市日期": "listed_on"}
            ).assign(行业=pd.NA),
            sh_star.rename(
                columns={"证券代码": "代码", "证券简称": "名称", "上市日期": "listed_on"}
            ).assign(行业=pd.NA),
            sz.rename(
                columns={
                    "A股代码": "代码",
                    "A股简称": "名称",
                    "A股上市日期": "listed_on",
                    "所属行业": "行业",
                }
            ),
            bj.rename(
                columns={
                    "证券代码": "代码",
                    "证券简称": "名称",
                    "上市日期": "listed_on",
                    "所属行业": "行业",
                }
            ),
        ]
        raw = pd.concat(
            [frame.loc[:, ["代码", "名称", "listed_on", "行业"]] for frame in frames],
            ignore_index=True,
        )
        try:
            quotes = self.call_with_retry(ak.stock_zh_a_spot_tx)
            self._require_columns(quotes, {"code", "zxj"}, "stock_universe_quotes")
            quote_codes = quotes["code"].astype("string").map(_bare_symbol).str.zfill(6)
            latest = pd.Series(
                pd.to_numeric(quotes["zxj"], errors="coerce").to_numpy(),
                index=quote_codes,
            )
            raw["最新价"] = raw["代码"].astype("string").str.zfill(6).map(latest)
        except ProviderUnavailable:
            raw["最新价"] = pd.NA
        return raw

    def _fetch_listing_date_map(self) -> dict[str, date]:
        sources = (
            (ak.stock_info_sh_name_code, {"symbol": "主板A股"}, "证券代码", "上市日期"),
            (ak.stock_info_sh_name_code, {"symbol": "科创板"}, "证券代码", "上市日期"),
            (ak.stock_info_sz_name_code, {"symbol": "A股列表"}, "A股代码", "A股上市日期"),
            (ak.stock_info_bj_name_code, {}, "证券代码", "上市日期"),
        )
        mapping: dict[str, date] = {}
        for function, kwargs, code_column, date_column in sources:
            frame = self.call_with_retry(function, **kwargs)
            self._require_columns(frame, {code_column, date_column}, "stock_listing_dates")
            codes = frame[code_column].astype("string").str.zfill(6)
            dates = pd.to_datetime(frame[date_column], errors="coerce").dt.date
            mapping.update(
                {
                    str(code): listed_on
                    for code, listed_on in zip(codes, dates, strict=True)
                    if pd.notna(listed_on)
                }
            )
        return mapping

    def _fetch_industry_map(self) -> dict[str, str]:
        cached = self._industry_map_cache
        if cached is not None and time.time() - cached[0] < INDUSTRY_MAP_CACHE_SECONDS:
            return dict(cached[1])
        if self._eastmoney_quotes_available is False:
            mapping = self._fetch_sina_industry_map()
            self._industry_map_cache = (time.time(), mapping)
            return dict(mapping)
        try:
            mapping = self._fetch_eastmoney_industry_map()
        except ProviderUnavailable:
            self._eastmoney_quotes_available = False
            mapping = self._fetch_sina_industry_map()
        self._industry_map_cache = (time.time(), mapping)
        return dict(mapping)

    def _fetch_eastmoney_industry_map(self) -> dict[str, str]:
        boards = self.call_with_retry(ak.stock_board_industry_name_em)
        self._require_columns(boards, {"板块名称", "板块代码"}, "stock_industries")
        board_rows = [
            (str(board_name).strip(), str(board_code).strip())
            for board_name, board_code in boards[["板块名称", "板块代码"]].itertuples(
                index=False, name=None
            )
        ]

        def fetch_board(item: tuple[str, str]) -> tuple[str, pd.DataFrame]:
            board_name, board_code = item
            return board_name, self.call_with_retry(
                ak.stock_board_industry_cons_em, symbol=board_code
            )

        mapping: dict[str, str] = {}
        # Industry constituents are independent requests; bounded parallelism
        # keeps the bootstrap latency reasonable without creating an unbounded
        # request burst against a public endpoint.
        with ThreadPoolExecutor(max_workers=INDUSTRY_MAP_MAX_WORKERS) as executor:
            for board_name, constituents in executor.map(fetch_board, board_rows):
                if "代码" not in constituents:
                    continue
                for code in constituents["代码"].astype("string").str.zfill(6):
                    mapping.setdefault(str(code), board_name)
        return mapping

    def _fetch_sina_industry_map(self) -> dict[str, str]:
        boards = self.call_with_retry(ak.stock_sector_spot, indicator="新浪行业")
        self._require_columns(boards, {"label", "板块"}, "sina_stock_industries")

        def fetch_sector(item: tuple[object, object]) -> tuple[str, str, pd.DataFrame]:
            label, board_name = item
            return (
                str(label).strip(),
                str(board_name).strip(),
                self.call_with_retry(ak.stock_sector_detail, sector=str(label)),
            )

        mapping: dict[str, str] = {}
        sector_rows = list(boards[["label", "板块"]].itertuples(index=False, name=None))
        # Sina exposes one constituent endpoint per sector as well.  Keep the
        # same bounded worker pool as Eastmoney so a fallback does not turn
        # the history bootstrap back into an 80+ request serial bottleneck.
        with ThreadPoolExecutor(max_workers=INDUSTRY_MAP_MAX_WORKERS) as executor:
            sector_results = executor.map(fetch_sector, sector_rows)
            for _, board_name, constituents in sector_results:
                if "code" not in constituents:
                    continue
                for code in constituents["code"].astype("string").str.zfill(6):
                    mapping.setdefault(str(code), board_name)
        return mapping

    def fetch_stock_daily(self, trade_date: date) -> pd.DataFrame:
        self._last_stock_daily_source = None
        try:
            if self._eastmoney_quotes_available is False:
                raise ProviderUnavailable("Eastmoney quote circuit is open")
            raw = self.call_with_retry(ak.stock_zh_a_spot_em)
            self._eastmoney_quotes_available = True
            source = "akshare-eastmoney-stock-zh-a-spot-em"
        except ProviderUnavailable:
            self._eastmoney_quotes_available = False
            try:
                raw = self.call_with_retry(ak.stock_zh_a_spot)
            except ProviderUnavailable:
                quotes = self.call_with_retry(ak.stock_zh_a_spot_tx)
                self._require_columns(quotes, {"code"}, "tencent_stock_symbols")
                normalized = self.call_with_retry(
                    _fetch_tencent_stock_daily,
                    symbols=quotes["code"].astype(str).tolist(),
                    trade_date=trade_date,
                )
                source = "akshare-tencent-direct-quote"
            else:
                normalized = _normalize_stock_spot(raw, self._latest_stock_market_date(trade_date))
                source = "akshare-sina-stock-zh-a-spot"
        else:
            normalized = _normalize_stock_spot(raw, self._latest_stock_market_date(trade_date))

        prices = _coerce_prices(normalized, "daily_prices")
        numeric = prices[["open", "high", "low", "close", "volume", "amount"]]
        valid = numeric[["open", "high", "low", "close"]].gt(0).all(axis=1)
        valid &= numeric[["volume", "amount"]].ge(0).all(axis=1)
        result = prices.loc[valid].reset_index(drop=True)
        self._last_stock_daily_source = source
        return result

    def fetch_stock_history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self._last_stock_history_source = None
        try:
            if self._eastmoney_quotes_available is False:
                raise ProviderUnavailable("Eastmoney quote circuit is open")
            raw = self.call_with_retry(
                ak.stock_zh_a_hist,
                symbol=_bare_symbol(symbol),
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
            )
            self._eastmoney_quotes_available = True
            source = "akshare-eastmoney-stock-zh-a-hist"
        except ProviderUnavailable:
            self._eastmoney_quotes_available = False
            raw = self.call_with_retry(
                _fetch_tencent_price_history,
                symbol=_tx_symbol(symbol),
                start_date=start,
                end_date=end,
            )
            source = "akshare-tencent-direct-day"
        normalized = raw.rename(columns=_STOCK_PRICE_COLUMNS).copy()
        normalized.insert(0, "symbol", _normalize_a_share_symbol(symbol))
        result = _coerce_prices(normalized, "stock_history")
        self._last_stock_history_source = source
        return result

    def fetch_index_history(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            try:
                if self._eastmoney_quotes_available is False:
                    raise ProviderUnavailable("Eastmoney quote circuit is open")
                raw = self.call_with_retry(
                    ak.stock_zh_index_daily_em,
                    symbol=_akshare_index_symbol(symbol),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                self._eastmoney_quotes_available = True
            except ProviderUnavailable:
                self._eastmoney_quotes_available = False
                try:
                    raw = self.call_with_retry(
                        ak.stock_zh_index_daily,
                        symbol=_akshare_index_symbol(symbol),
                    )
                except ProviderUnavailable:
                    raw = self.call_with_retry(
                        ak.stock_zh_index_daily_tx,
                        symbol=_tx_symbol(symbol),
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                    )
            normalized = raw.rename(columns=_STOCK_PRICE_COLUMNS).copy()
            if "volume" not in normalized:
                normalized["volume"] = 0.0
            if "amount" not in normalized:
                normalized["amount"] = 0.0
            normalized.insert(0, "symbol", _normalize_index_symbol(symbol))
            prices = _coerce_prices(normalized, "index_history")
            trade_dates = pd.to_datetime(prices["trade_date"], errors="coerce")
            frames.append(
                prices.loc[
                    trade_dates.ge(pd.Timestamp(start)) & trade_dates.le(pd.Timestamp(end))
                ].reset_index(drop=True)
            )
        if not frames:
            return pd.DataFrame(columns=_PRICE_FIELDS)
        return pd.concat(frames, ignore_index=True)

    def fetch_fund_daily(self) -> pd.DataFrame:
        try:
            raw = self.call_with_retry(ak.fund_open_fund_daily_em)
        except ProviderUnavailable:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "name",
                    "nav_date",
                    "unit_nav",
                    "purchase_status",
                    "redemption_status",
                    "accumulated_nav",
                ]
            )
        self._require_columns(raw, {"基金代码", "基金简称"}, "fund_daily")
        unit_column = _find_fund_column(raw, "单位净值")
        accumulated_column = _find_fund_column(raw, "累计净值", required=False)
        nav_date = _date_from_fund_column(unit_column)
        result = pd.DataFrame(
            {
                "symbol": raw["基金代码"].astype("string").str.zfill(6),
                "name": raw["基金简称"].astype("string").str.strip(),
                "nav_date": nav_date,
                "unit_nav": pd.to_numeric(raw[unit_column], errors="coerce"),
                "purchase_status": raw.get("申购状态", pd.Series(pd.NA, index=raw.index)),
                "redemption_status": raw.get("赎回状态", pd.Series(pd.NA, index=raw.index)),
            }
        )
        result["accumulated_nav"] = (
            pd.to_numeric(raw[accumulated_column], errors="coerce") if accumulated_column else pd.NA
        )
        return result.reset_index(drop=True)

    def fetch_fund_nav(self, symbol: str) -> pd.DataFrame:
        raw = self.call_with_retry(
            ak.fund_open_fund_info_em,
            symbol=_bare_symbol(symbol),
            indicator="单位净值走势",
        )
        mapping = {"净值日期": "nav_date", "单位净值": "unit_nav", "累计净值": "accumulated_nav"}
        self._require_columns(raw, {"净值日期", "单位净值"}, "fund_nav")
        result = raw.rename(columns=mapping).copy()
        result.insert(0, "symbol", _bare_symbol(symbol).zfill(6))
        if "accumulated_nav" not in result:
            accumulated = self.call_with_retry(
                ak.fund_open_fund_info_em,
                symbol=_bare_symbol(symbol),
                indicator="累计净值走势",
            )
            self._require_columns(accumulated, {"净值日期", "累计净值"}, "fund_nav")
            accumulated = accumulated.rename(columns=mapping).loc[
                :, ["nav_date", "accumulated_nav"]
            ]
            result["nav_date"] = pd.to_datetime(result["nav_date"], errors="coerce").dt.date
            accumulated["nav_date"] = pd.to_datetime(
                accumulated["nav_date"], errors="coerce"
            ).dt.date
            if accumulated["nav_date"].isna().any() or accumulated["nav_date"].duplicated().any():
                raise ProviderDataError("fund_nav: invalid or duplicate accumulated NAV dates")
            result = result.merge(accumulated, on="nav_date", how="left", validate="one_to_one")
        result = result.loc[:, ["symbol", "nav_date", "unit_nav", "accumulated_nav"]]
        result["nav_date"] = pd.to_datetime(result["nav_date"], errors="coerce").dt.date
        for column in ("unit_nav", "accumulated_nav"):
            result[column] = pd.to_numeric(result[column], errors="coerce")
        return result.reset_index(drop=True)

    def fetch_valuation_history(
        self, symbols: Sequence[str], start: date, end: date
    ) -> pd.DataFrame:
        """Fetch dated index PE/PB observations from the public valuation feeds."""

        if start > end:
            raise ValueError("start must be on or before end")
        frames: list[pd.DataFrame] = []
        for raw_symbol in tuple(dict.fromkeys(str(item).strip().upper() for item in symbols)):
            symbol = _normalize_index_symbol(raw_symbol)
            label = _INDEX_VALUATION_LABELS.get(symbol)
            if label is None:
                raise ProviderDataError(f"valuation_history: unsupported index symbol {symbol}")
            pe_fetcher = getattr(ak, "stock_index_pe_lg", None)
            pb_fetcher = getattr(ak, "stock_index_pb_lg", None)
            if not callable(pe_fetcher) or not callable(pb_fetcher):
                raise ProviderDataError(
                    "valuation_history: PE/PB AKShare endpoints are unavailable"
                )
            pe_raw = self.call_with_retry(pe_fetcher, symbol=label)
            pb_raw = self.call_with_retry(pb_fetcher, symbol=label)
            frames.append(
                _normalize_index_valuation(
                    pe_raw,
                    pb_raw,
                    symbol=symbol,
                    start=start,
                    end=end,
                )
            )
        if not frames:
            raise ProviderDataError("valuation_history: no symbols requested")
        result = pd.concat(frames, ignore_index=True)
        if result.empty:
            raise ProviderDataError("valuation_history: provider returned no rows")
        return result.reset_index(drop=True)

    def fetch_manager_history(self, symbols: Sequence[str], as_of_date: date) -> pd.DataFrame:
        """Fetch current manager records and derive auditable win-rate evidence."""

        if not isinstance(as_of_date, date):
            raise TypeError("as_of_date must be a date")
        selected = tuple(
            dict.fromkeys(_bare_symbol(str(item)).zfill(6) for item in symbols if str(item).strip())
        )
        if not selected:
            raise ProviderDataError("manager_history: no fund symbols requested")
        manager_fetcher = getattr(ak, "fund_manager_em", None)
        if not callable(manager_fetcher):
            raise ProviderDataError("manager_history: manager endpoint is unavailable")
        manager_frame = self.call_with_retry(manager_fetcher)
        code_column = _first_column(
            manager_frame, ("现任基金代码", "fund_code", "symbol", "基金代码")
        )
        name_column = _first_column(manager_frame, ("姓名", "manager_name", "基金经理"))
        tenure_column = _first_column(
            manager_frame, ("累计从业时间", "manager_tenure_days", "tenure_days")
        )
        if code_column is None or name_column is None or tenure_column is None:
            raise ProviderDataError("manager_history: manager response is missing code/name/tenure")
        manager_frame = manager_frame.rename(
            columns={
                code_column: "_fund_code",
                name_column: "_manager_name",
                tenure_column: "_tenure",
            }
        )
        codes = manager_frame["_fund_code"].astype("string").str.extract(r"(\d{6})", expand=False)
        manager_frame = manager_frame.assign(_symbol=codes).loc[codes.isin(selected)].copy()
        if manager_frame.empty:
            raise ProviderDataError("manager_history: selected funds were not found")
        rows: list[dict[str, object]] = []
        for symbol, group in manager_frame.groupby("_symbol", sort=True):
            names = tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in group["_manager_name"].tolist()
                    if str(value).strip()
                )
            )
            tenure_days = pd.to_numeric(group["_tenure"], errors="coerce").max()
            win_rate = float("nan")
            alpha_stability = float("nan")
            probability_fetcher = getattr(ak, "fund_individual_profit_probability_xq", None)
            if callable(probability_fetcher):
                try:
                    probability = self.call_with_retry(
                        probability_fetcher,
                        symbol=str(symbol),
                    )
                    win_rate = _extract_win_rate(probability)
                except (ProviderError, KeyError, TypeError, ValueError) as exc:
                    # These optional manager metrics are allowed to be absent,
                    # but the source failure must remain observable.
                    logger.warning(
                        "optional manager probability unavailable for %s: %s",
                        symbol,
                        type(exc).__name__,
                    )
            achievement_fetcher = getattr(ak, "fund_individual_achievement_xq", None)
            if callable(achievement_fetcher):
                try:
                    achievement = self.call_with_retry(
                        achievement_fetcher,
                        symbol=str(symbol),
                    )
                    alpha_stability = _extract_alpha_stability(achievement)
                except (ProviderError, KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "optional manager achievement unavailable for %s: %s",
                        symbol,
                        type(exc).__name__,
                    )
            rows.append(
                {
                    "symbol": str(symbol),
                    "as_of_date": as_of_date,
                    "manager_name": "、".join(names) or None,
                    "manager_tenure_years": (
                        float(tenure_days) / 365.25 if pd.notna(tenure_days) else float("nan")
                    ),
                    "manager_win_rate": win_rate,
                    "alpha_stability": alpha_stability,
                    "drawdown_recovery_days": float("nan"),
                }
            )
        return pd.DataFrame(rows)

    def call_with_retry(self, function: Callable[..., T], /, **kwargs: object) -> T:
        return _call_with_retry(
            function,
            sleep=self._sleep,
            transient_errors=(*_TRANSIENT_ERRORS, demjson.JSONDecodeError),
            source=_source_for_function(function),
            mark_success=self._mark_source_success,
            mark_failure=self._mark_source_failure,
            logger=logger,
            **kwargs,
        )

    def _mark_source_success(self, source: str) -> None:
        if source not in self._source_state:
            return
        self._active_source = source
        self._source_state[source]["failures"] = 0
        self._source_state[source]["last_error"] = None
        self._source_state[source]["last_success_at"] = time.time()

    def _mark_source_failure(self, source: str, error: str) -> None:
        if source not in self._source_state:
            return
        self._source_state[source]["failures"] = int(self._source_state[source]["failures"]) + 1
        self._source_state[source]["last_error"] = error

    def _latest_stock_market_date(self, requested_date: date) -> date:
        start = requested_date - timedelta(days=14)
        try:
            if self._eastmoney_quotes_available is False:
                raise ProviderUnavailable("Eastmoney quote circuit is open")
            history = self.call_with_retry(
                ak.stock_zh_a_hist,
                symbol="600000",
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=requested_date.strftime("%Y%m%d"),
                adjust="",
            )
            self._eastmoney_quotes_available = True
        except ProviderUnavailable:
            self._eastmoney_quotes_available = False
            history = self.call_with_retry(
                ak.stock_zh_a_hist_tx,
                symbol="sh600000",
                start_date=start.strftime("%Y%m%d"),
                end_date=requested_date.strftime("%Y%m%d"),
                adjust="",
            )
        date_column = "日期" if "日期" in history else "date"
        self._require_columns(history, {date_column}, "market_calendar")
        dates = pd.to_datetime(history[date_column], errors="coerce").dropna()
        if dates.empty:
            raise ProviderDataError("market_calendar: no completed trading date returned")
        return dates.max().date()

    @staticmethod
    def _require_columns(frame: pd.DataFrame, required: set[str], dataset: str) -> None:
        missing = required.difference(frame.columns)
        if missing:
            formatted = ", ".join(sorted(missing))
            raise ProviderDataError(f"{dataset}: missing provider columns: {formatted}")
