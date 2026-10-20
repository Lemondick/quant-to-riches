"""Deterministic daily event engine shared by research and paper workflows."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Protocol

import pandas as pd

from quant_assistant.backtest.fund_execution import (
    FundTradingStatus,
    PendingFundOrder,
)
from quant_assistant.backtest.models import (
    DailyBar,
    ExecutionState,
)
from quant_assistant.backtest.repository import canonical_json
from quant_assistant.backtest.stock_features import StockFeatureView
from quant_assistant.common.serialization import decimal_coerce, enum_text, flag
from quant_assistant.funds.fees import FundFeeSchedule
from quant_assistant.models.entities import ProposedOrder, Signal
from quant_assistant.portfolio.models import PortfolioState, RiskPolicy

ZERO = Decimal("0")
CENT = Decimal("0.01")
ZERO = Decimal("0")
CENT = Decimal("0.01")
MAIN_BOARD_RISK_WARNING_TEN_PERCENT_FROM = date(2026, 7, 6)


def _decimal(value: object, field_name: str, *, positive: bool = False) -> Decimal:
    return decimal_coerce(value, field_name, positive=positive)


def _date_value(value: object, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{field_name} contains an invalid date")
    return parsed.date()


def _date_column(frame: pd.DataFrame, column: str) -> pd.Series:
    parsed = pd.to_datetime(frame[column], errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"{column} contains invalid dates")
    return parsed.dt.date


def _flag(value: object, *, default: bool = False) -> bool:
    parsed = flag(value, default=default)
    return default if parsed is None else parsed


def _missing_value(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _has_complete_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> bool:
    return all(column in frame.columns and frame[column].notna().all() for column in columns)


def _enum_text(value: object) -> str:
    return enum_text(value)


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=str)}
    if isinstance(value, (set, frozenset)):
        items = [_jsonable(item) for item in value]
        return sorted(items, key=canonical_json)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if hasattr(value, "item"):
        return _jsonable(value.item())
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _freeze(value: object) -> object:
    """Recursively detach and freeze JSON-like strategy parameters."""

    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("parameter mapping keys must be strings")
        return MappingProxyType(
            {key: _freeze(item) for key, item in sorted(value.items(), key=lambda pair: pair[0])}
        )
    if isinstance(value, (set, frozenset)):
        frozen = [_freeze(item) for item in value]
        return tuple(sorted(frozen, key=lambda item: canonical_json(_jsonable(item))))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, Enum):
        return _freeze(value.value)
    if value is None or isinstance(value, (str, bool, int, date, datetime)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal parameters must be finite")
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("float parameters must be finite")
        return value
    if hasattr(value, "item"):
        return _freeze(value.item())
    raise TypeError(f"unsupported mutable parameter type: {type(value).__name__}")


class BacktestDataLoader(Protocol):
    """Minimal point-in-time data boundary consumed by :class:`BacktestEngine`."""

    limitations: tuple[str, ...]
    fund_fee_schedules: Mapping[str, FundFeeSchedule]

    def trading_dates(self, start: date, end: date) -> tuple[date, ...]: ...

    def previous_trading_date(self, value: date) -> date | None: ...

    def prices_through(self, as_of_date: date) -> pd.DataFrame: ...

    def instruments_as_of(self, as_of_date: date) -> pd.DataFrame: ...

    def snapshot_id(self, as_of_date: date) -> int: ...

    def bars_on(self, trade_date: date) -> Mapping[str, DailyBar]: ...

    def marks_as_of(self, as_of_date: date) -> Mapping[str, Decimal]: ...

    def fund_nav_for(self, symbol: str, nav_date: date, known_on: date) -> Decimal | None: ...

    def fund_statuses_on(self, status_date: date) -> Mapping[str, FundTradingStatus]: ...


@dataclass(frozen=True, slots=True)
class FrameBacktestData:
    """In-memory, point-in-time-safe loader for normalized historical frames.

    Explicit historical suspension and price-limit columns are preferred.  If
    they are absent, ordinary exchange limits are inferred and the loader adds
    a research-only limitation so the approximation is never hidden.
    """

    prices: pd.DataFrame = field(repr=False, compare=False)
    instruments: pd.DataFrame = field(repr=False, compare=False)
    snapshot_ids: Mapping[date, int] = field(default_factory=dict)
    fund_nav: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False, compare=False)
    fund_status: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False, compare=False)
    fund_fee_schedules: Mapping[str, FundFeeSchedule] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    _trading_date_index: tuple[date, ...] = field(init=False, repr=False, compare=False)
    _price_bounds: Mapping[date, tuple[int, int]] = field(init=False, repr=False, compare=False)
    _snapshot_date_index: tuple[date, ...] = field(init=False, repr=False, compare=False)
    _instrument_cache: dict[date, pd.DataFrame] = field(init=False, repr=False, compare=False)
    _marks_cache: dict[date, Mapping[str, Decimal]] = field(init=False, repr=False, compare=False)
    _snapshot_cache: dict[date, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.prices, pd.DataFrame):
            raise TypeError("prices must be a pandas DataFrame")
        required = {"symbol", "trade_date", "open", "high", "low", "close"}
        missing = sorted(required.difference(self.prices.columns))
        if missing:
            raise ValueError(f"prices missing required columns: {', '.join(missing)}")
        prices = self.prices.copy(deep=True)
        prices["symbol"] = prices["symbol"].astype("string").str.strip()
        prices["trade_date"] = _date_column(prices, "trade_date")
        if prices["symbol"].eq("").any():
            raise ValueError("prices contains an empty symbol")
        if prices.duplicated(["symbol", "trade_date"]).any():
            raise ValueError("prices contains duplicate symbol/date rows")
        prices = prices.sort_values(["trade_date", "symbol"], kind="mergesort")
        if "previous_close" not in prices.columns:
            prices["previous_close"] = prices.groupby("symbol", sort=False)["close"].shift(1)
        object.__setattr__(self, "prices", prices.reset_index(drop=True))
        normalized_prices = self.prices
        trading_dates: list[date] = []
        bounds: dict[date, tuple[int, int]] = {}
        start_index = 0
        for index, value in enumerate(normalized_prices["trade_date"].tolist()):
            if not trading_dates or value != trading_dates[-1]:
                if trading_dates:
                    bounds[trading_dates[-1]] = (start_index, index)
                trading_dates.append(value)
                start_index = index
        if trading_dates:
            bounds[trading_dates[-1]] = (start_index, len(normalized_prices))
        object.__setattr__(self, "_trading_date_index", tuple(trading_dates))
        object.__setattr__(self, "_price_bounds", MappingProxyType(bounds))

        if not isinstance(self.instruments, pd.DataFrame):
            raise TypeError("instruments must be a pandas DataFrame")
        instruments = self.instruments.copy(deep=True)
        if not instruments.empty and "symbol" not in instruments.columns:
            raise ValueError("instruments missing symbol")
        if "symbol" not in instruments.columns:
            instruments["symbol"] = pd.Series(dtype="string")
        instruments["symbol"] = instruments["symbol"].astype("string").str.strip()
        for column in ("as_of_date", "listed_on", "delisted_on"):
            if column in instruments.columns:
                parsed = pd.to_datetime(instruments[column], errors="coerce")
                instruments[column] = parsed.map(
                    lambda value: None if pd.isna(value) else value.date()
                ).astype("object")
        sort_columns = [
            column for column in ("symbol", "as_of_date") if column in instruments.columns
        ]
        if sort_columns:
            instruments = instruments.sort_values(sort_columns, kind="mergesort")
        object.__setattr__(self, "instruments", instruments.reset_index(drop=True))

        nav = self.fund_nav.copy(deep=True)
        if not nav.empty:
            nav_column = "unit_nav" if "unit_nav" in nav.columns else "nav"
            nav_required = {"symbol", "nav_date", nav_column}
            if not nav_required.issubset(nav.columns):
                raise ValueError("fund_nav must contain symbol, nav_date, and unit_nav/nav")
            nav["symbol"] = nav["symbol"].astype("string").str.strip()
            nav["nav_date"] = _date_column(nav, "nav_date")
            if "available_on" in nav.columns:
                nav["available_on"] = _date_column(nav, "available_on")
            nav = nav.sort_values(["nav_date", "symbol"], kind="mergesort")
            if nav.duplicated(["symbol", "nav_date"]).any():
                raise ValueError("fund_nav contains duplicate symbol/date rows")
        object.__setattr__(self, "fund_nav", nav.reset_index(drop=True))

        status = self.fund_status.copy(deep=True)
        if not status.empty:
            if not {"symbol", "status_date"}.issubset(status.columns):
                raise ValueError("fund_status must contain symbol and status_date")
            status["symbol"] = status["symbol"].astype("string").str.strip()
            status["status_date"] = _date_column(status, "status_date")
            for column in ("latest_nav_date", "nav_date"):
                if column in status.columns:
                    status[column] = _date_column(status, column)
            if status.duplicated(["symbol", "status_date"]).any():
                raise ValueError("fund_status contains duplicate symbol/date rows")
            status = status.sort_values(["status_date", "symbol"], kind="mergesort")
        object.__setattr__(self, "fund_status", status.reset_index(drop=True))

        normalized_snapshots: dict[date, int] = {}
        for raw_date, raw_identifier in self.snapshot_ids.items():
            identifier = int(raw_identifier)
            if isinstance(raw_identifier, bool) or identifier <= 0:
                raise ValueError("snapshot ids must be positive integers")
            normalized_snapshots[_date_value(raw_date, "snapshot date")] = identifier
        object.__setattr__(
            self,
            "snapshot_ids",
            MappingProxyType(dict(sorted(normalized_snapshots.items()))),
        )
        object.__setattr__(self, "_snapshot_date_index", tuple(self.snapshot_ids))
        object.__setattr__(
            self,
            "fund_fee_schedules",
            MappingProxyType(dict(sorted(self.fund_fee_schedules.items()))),
        )
        object.__setattr__(self, "_instrument_cache", {})
        object.__setattr__(self, "_marks_cache", {})
        object.__setattr__(self, "_snapshot_cache", {})

        limitations = [str(item) for item in self.limitations if str(item)]
        if not {"listed_on", "delisted_on"}.issubset(instruments.columns):
            limitations.append("missing_historical_universe_membership")
        if "as_of_date" not in instruments.columns:
            limitations.append("instrument_metadata_not_point_in_time")
        stock_prices = prices
        if "asset_type" in instruments.columns:
            non_stocks = instruments.loc[
                instruments["asset_type"].map(_enum_text).isin(("index", "fund", "cash")),
                "symbol",
            ]
            stock_prices = prices.loc[~prices["symbol"].isin(non_stocks)]
        if not _has_complete_columns(stock_prices, ("upper_limit", "lower_limit")):
            limitations.append("estimated_historical_price_limits")
        if not any(
            _has_complete_columns(stock_prices, (column,))
            for column in ("suspended", "is_suspended")
        ):
            limitations.append("missing_historical_suspension_flags")
        object.__setattr__(self, "limitations", tuple(dict.fromkeys(limitations)))

    def trading_dates(self, start: date, end: date) -> tuple[date, ...]:
        left = bisect_left(self._trading_date_index, start)
        right = bisect_right(self._trading_date_index, end)
        return self._trading_date_index[left:right]

    def previous_trading_date(self, value: date) -> date | None:
        index = bisect_left(self._trading_date_index, value)
        return self._trading_date_index[index - 1] if index else None

    def prices_through(self, as_of_date: date) -> pd.DataFrame:
        end = bisect_right(self._trading_date_index, as_of_date)
        if end == 0:
            return self.prices.iloc[0:0].copy(deep=False)
        _, last = self._price_bounds[self._trading_date_index[end - 1]]
        return self.prices.iloc[:last].copy(deep=False)

    def instruments_as_of(self, as_of_date: date) -> pd.DataFrame:
        cached = self._instrument_cache.get(as_of_date)
        if cached is None:
            cached = self._compute_instruments_as_of(as_of_date)
            self._instrument_cache[as_of_date] = cached
        return cached.copy(deep=False)

    def _compute_instruments_as_of(self, as_of_date: date) -> pd.DataFrame:
        frame = self.instruments.copy(deep=False)
        if "as_of_date" in frame.columns:
            frame = frame.loc[frame["as_of_date"].notna() & frame["as_of_date"].le(as_of_date)]
            frame = frame.sort_values(["symbol", "as_of_date"], kind="mergesort")
            frame = frame.groupby("symbol", sort=False, as_index=False).tail(1)
        if "listed_on" in frame.columns:
            frame = frame.loc[frame["listed_on"].isna() | frame["listed_on"].le(as_of_date)]
        if "delisted_on" in frame.columns:
            frame = frame.loc[frame["delisted_on"].isna() | frame["delisted_on"].gt(as_of_date)]
        return frame.sort_values("symbol", kind="mergesort").reset_index(drop=True)

    def snapshot_id(self, as_of_date: date) -> int:
        cached = self._snapshot_cache.get(as_of_date)
        if cached is not None:
            return cached
        known_index = bisect_right(self._snapshot_date_index, as_of_date)
        if known_index:
            result = int(self.snapshot_ids[self._snapshot_date_index[known_index - 1]])
            self._snapshot_cache[as_of_date] = result
            return result
        history = self.prices_through(as_of_date)
        result = 0
        for column in ("source_snapshot_id", "data_snapshot_id"):
            if column not in history.columns:
                continue
            values = pd.to_numeric(history[column], errors="coerce").dropna()
            if not values.empty and int(values.iloc[-1]) > 0:
                result = int(values.iloc[-1])
                break
        self._snapshot_cache[as_of_date] = result
        return result

    def bars_on(self, trade_date: date) -> Mapping[str, DailyBar]:
        bounds = self._price_bounds.get(trade_date)
        rows = self.prices.iloc[0:0] if bounds is None else self.prices.iloc[slice(*bounds)]
        instruments = self.instruments_as_of(trade_date).set_index("symbol", drop=False)
        bars: dict[str, DailyBar] = {}
        for row in rows.to_dict(orient="records"):
            symbol = str(row["symbol"])
            try:
                previous_close = _decimal(
                    row.get("previous_close"), "previous_close", positive=True
                )
                lower, upper = self._limits(row, instruments, previous_close)
                suspended_value = row.get("suspended")
                if _missing_value(suspended_value):
                    suspended_value = row.get("is_suspended")
                if _missing_value(suspended_value) and symbol in instruments.index:
                    instrument = instruments.loc[symbol]
                    suspended_value = instrument.get("is_suspended", False)
                bars[symbol] = DailyBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=_decimal(row["open"], "open", positive=True),
                    high=_decimal(row["high"], "high", positive=True),
                    low=_decimal(row["low"], "low", positive=True),
                    close=_decimal(row["close"], "close", positive=True),
                    previous_close=previous_close,
                    suspended=_flag(suspended_value),
                    upper_limit=upper,
                    lower_limit=lower,
                )
            except (TypeError, ValueError):
                # A malformed execution row is deliberately omitted.  The engine
                # records a missing-bar rejection instead of manufacturing a fill.
                continue
        return MappingProxyType(dict(sorted(bars.items())))

    @staticmethod
    def _limits(
        row: Mapping[str, object],
        instruments: pd.DataFrame,
        previous_close: Decimal,
    ) -> tuple[Decimal, Decimal]:
        lower_limit = row.get("lower_limit")
        upper_limit = row.get("upper_limit")
        if not _missing_value(lower_limit) and not _missing_value(upper_limit):
            return (
                _decimal(lower_limit, "lower_limit", positive=True),
                _decimal(upper_limit, "upper_limit", positive=True),
            )
        symbol = str(row["symbol"])
        is_st = False
        if symbol in instruments.index:
            is_st = _flag(instruments.loc[symbol].get("is_st", False))
        code = symbol.split(".", maxsplit=1)[0]
        trade_date = _date_value(row["trade_date"], "trade_date")
        if code.startswith(("300", "301", "688", "689")):
            rate = Decimal("0.20")
        elif code.startswith(("4", "8")):
            rate = Decimal("0.30")
        elif is_st and trade_date < MAIN_BOARD_RISK_WARNING_TEN_PERCENT_FROM:
            rate = Decimal("0.05")
        else:
            rate = Decimal("0.10")
        lower = (previous_close * (Decimal("1") - rate)).quantize(CENT)
        upper = (previous_close * (Decimal("1") + rate)).quantize(CENT)
        return lower, upper

    def marks_as_of(self, as_of_date: date) -> Mapping[str, Decimal]:
        cached = self._marks_cache.get(as_of_date)
        if cached is not None:
            return cached
        marks = self._compute_marks_as_of(as_of_date)
        self._marks_cache[as_of_date] = marks
        return marks

    def _compute_marks_as_of(self, as_of_date: date) -> Mapping[str, Decimal]:
        marks: dict[str, Decimal] = {}
        history = self.prices_through(as_of_date)
        if not history.empty:
            latest = history.sort_values(["symbol", "trade_date"], kind="mergesort")
            latest = latest.groupby("symbol", sort=False, as_index=False).tail(1)
            for row in latest.to_dict(orient="records"):
                try:
                    marks[str(row["symbol"])] = _decimal(row["close"], "close", positive=True)
                except (TypeError, ValueError):
                    continue
        if not self.fund_nav.empty:
            nav = self.fund_nav.loc[self.fund_nav["nav_date"].le(as_of_date)].copy(deep=False)
            if "available_on" in nav.columns:
                nav = nav.loc[nav["available_on"].le(as_of_date)]
            latest_nav = nav.sort_values(["symbol", "nav_date"], kind="mergesort")
            latest_nav = latest_nav.groupby("symbol", sort=False, as_index=False).tail(1)
            nav_column = "unit_nav" if "unit_nav" in latest_nav.columns else "nav"
            for row in latest_nav.to_dict(orient="records"):
                try:
                    marks[str(row["symbol"])] = _decimal(row[nav_column], nav_column, positive=True)
                except (TypeError, ValueError):
                    continue
        return MappingProxyType(dict(sorted(marks.items())))

    def fund_nav_for(self, symbol: str, nav_date: date, known_on: date) -> Decimal | None:
        if self.fund_nav.empty:
            return None
        rows = self.fund_nav.loc[
            self.fund_nav["symbol"].eq(symbol) & self.fund_nav["nav_date"].eq(nav_date)
        ]
        if "available_on" in rows.columns:
            rows = rows.loc[rows["available_on"].le(known_on)]
        if rows.empty:
            return None
        column = "unit_nav" if "unit_nav" in rows.columns else "nav"
        try:
            return _decimal(rows.iloc[-1][column], column, positive=True)
        except (TypeError, ValueError):
            return None

    def fund_statuses_on(self, status_date: date) -> Mapping[str, FundTradingStatus]:
        if self.fund_status.empty:
            return MappingProxyType({})
        rows = self.fund_status.loc[self.fund_status["status_date"].eq(status_date)]
        statuses: dict[str, FundTradingStatus] = {}
        for row in rows.to_dict(orient="records"):
            symbol = str(row["symbol"])
            latest_nav_date = row.get("latest_nav_date", row.get("nav_date"))
            try:
                statuses[symbol] = FundTradingStatus(
                    symbol=symbol,
                    status_date=status_date,
                    latest_nav_date=_date_value(latest_nav_date, "latest_nav_date"),
                    purchase_open=_flag(
                        row.get("purchase_open", row.get("is_purchasable")), default=False
                    ),
                    redemption_open=_flag(
                        row.get("redemption_open", row.get("is_redeemable")), default=False
                    ),
                )
            except (TypeError, ValueError):
                continue
        return MappingProxyType(dict(sorted(statuses.items())))


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    start: date
    end: date
    initial_cash: Decimal
    strategy_version: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    research_only_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.start, date) or isinstance(self.start, datetime):
            raise TypeError("start must be a date")
        if not isinstance(self.end, date) or isinstance(self.end, datetime):
            raise TypeError("end must be a date")
        if self.end < self.start:
            raise ValueError("end must not precede start")
        initial_cash = _decimal(self.initial_cash, "initial_cash", positive=True)
        object.__setattr__(self, "initial_cash", initial_cash)
        version = str(self.strategy_version).strip()
        if not version:
            raise ValueError("strategy_version must not be empty")
        object.__setattr__(self, "strategy_version", version)
        if not isinstance(self.parameters, Mapping):
            raise TypeError("parameters must be a mapping")
        parameters = _freeze(dict(self.parameters))
        canonical_json(_jsonable(parameters))
        if not isinstance(parameters, Mapping):  # pragma: no cover - root is always a mapping
            raise TypeError("parameters must remain a mapping")
        object.__setattr__(self, "parameters", parameters)
        reasons = tuple(
            dict.fromkeys(str(item) for item in self.research_only_reasons if str(item))
        )
        object.__setattr__(self, "research_only_reasons", reasons)


@dataclass(frozen=True, slots=True)
class BacktestContext:
    as_of_date: date
    execution_date: date
    prices: pd.DataFrame = field(repr=False, compare=False)
    instruments: pd.DataFrame = field(repr=False, compare=False)
    portfolio: PortfolioState
    execution_state: ExecutionState
    parameters: Mapping[str, object]
    data_snapshot_id: int
    stock_features: StockFeatureView | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: str
    trading_dates: tuple[date, ...]
    nav: pd.DataFrame = field(repr=False, compare=False)
    orders: pd.DataFrame = field(repr=False, compare=False)
    fills: pd.DataFrame = field(repr=False, compare=False)
    positions: pd.DataFrame = field(repr=False, compare=False)
    research_only: bool
    limitations: tuple[str, ...]
    snapshot_ids: tuple[int, ...] = ()
    final_state: ExecutionState | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        object.__setattr__(self, "trading_dates", tuple(self.trading_dates))
        for field_name in ("nav", "orders", "fills", "positions"):
            frame = getattr(self, field_name)
            if not isinstance(frame, pd.DataFrame):
                raise TypeError(f"{field_name} must be a pandas DataFrame")
            object.__setattr__(self, field_name, frame.copy(deep=True))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(self, "snapshot_ids", tuple(sorted(set(self.snapshot_ids))))


StrategyCallable = Callable[[BacktestContext], Sequence[Signal]]
OrderProposer = Callable[
    [Sequence[Signal], PortfolioState, Mapping[str, Decimal], pd.DataFrame, RiskPolicy, int],
    list[ProposedOrder],
]


@dataclass(frozen=True, slots=True)
class _PendingFundEntry:
    pending: PendingFundOrder
    order_id: str
