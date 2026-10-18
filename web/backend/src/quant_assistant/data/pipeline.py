"""Deterministic orchestration for the daily market-data update."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType

import pandas as pd

from quant_assistant.config import Settings
from quant_assistant.data.research import ResearchSyncResult, sync_research_evidence
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.data.validation import ValidationIssue, validate_dataset
from quant_assistant.models.entities import DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import DailyMarketProvider

DEFAULT_RISK_INDEXES = ("000300.SH",)


@dataclass(frozen=True, slots=True)
class PipelineDatasetResult:
    dataset: str
    snapshot_id: int
    status: DataStatus
    row_count: int
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    as_of_date: date
    datasets: Mapping[str, PipelineDatasetResult]
    tradable: bool
    active_source: str | None = None
    provider_status: pd.DataFrame | None = None
    research: ResearchSyncResult | None = None


def run_daily_data_pipeline(
    settings: Settings, provider: DailyMarketProvider, as_of_date: date
) -> PipelineResult:
    """Fetch, snapshot, validate, and persist one completed market date."""

    db = Database(settings.database_path)
    db.initialize()
    writer = SnapshotWriter(settings.raw_snapshot_dir, provider.name)
    fetchers = build_dataset_fetchers(provider, as_of_date)
    results: dict[str, PipelineDatasetResult] = {}

    for dataset, fetch in fetchers.items():
        frame = fetch()
        if dataset == "instruments":
            frame = _point_in_time_instruments(frame, as_of_date)
            frame = _with_system_instruments(frame, as_of_date)
        actual_source = (
            getattr(provider, "last_stock_daily_source", None)
            if dataset == "daily_prices"
            else None
        )
        snapshot_writer = SnapshotWriter(writer.root, actual_source) if actual_source else writer
        snapshot = snapshot_writer.write(dataset, as_of_date, frame)
        validation = validate_dataset(dataset, frame, as_of_date)
        snapshot_id = db.record_snapshot(snapshot, validation)
        if validation.status is DataStatus.VALID:
            _persist_normalized_dataset(db, dataset, as_of_date, frame, snapshot_id)
        results[dataset] = PipelineDatasetResult(
            dataset=dataset,
            snapshot_id=snapshot_id,
            status=validation.status,
            row_count=len(frame),
            issues=validation.issues,
        )

    required = ("instruments", "daily_prices", "index_history")
    tradable = all(results[name].status is DataStatus.VALID for name in required)
    research = _sync_research_if_supported(settings, db, provider, as_of_date)
    return PipelineResult(
        as_of_date=as_of_date,
        datasets=MappingProxyType(results.copy()),
        tradable=tradable,
        active_source=getattr(provider, "active_source", None) or getattr(provider, "name", None),
        provider_status=_provider_status(provider),
        research=research,
    )


def _sync_research_if_supported(
    settings: Settings,
    database: Database,
    provider: DailyMarketProvider,
    as_of_date: date,
) -> ResearchSyncResult | None:
    """Run the optional evidence sync as part of every live data update.

    Offline fixtures and third-party providers that only implement the daily
    market contract are left untouched.  A provider exposing either research
    endpoint opts into the same snapshot/validation path used by the explicit
    ``sync-research`` command, so a scheduled ``update-data``/``run-daily``
    cannot silently skip PE/PB evidence.
    """

    methods = (
        getattr(provider, "fetch_valuation_history", None),
        getattr(provider, "fetch_manager_history", None),
    )
    if not any(callable(method) for method in methods):
        return None
    tracked = database.query_df(
        """
        SELECT symbol FROM positions WHERE asset_type = 'fund'
        UNION
        SELECT symbol FROM fund_share_families
        ORDER BY symbol
        """
    )
    fund_symbols = tuple(tracked["symbol"].astype(str).tolist()) if not tracked.empty else ()
    return sync_research_evidence(
        settings,
        provider,
        as_of_date - timedelta(days=365),
        as_of_date,
        fund_symbols=fund_symbols,
    )


def _provider_status(provider: DailyMarketProvider) -> pd.DataFrame | None:
    method = getattr(provider, "source_status", None)
    if not callable(method):
        return None
    try:
        status = method()
    except Exception:
        return None
    return status.copy(deep=True) if isinstance(status, pd.DataFrame) else None


def build_dataset_fetchers(
    provider: DailyMarketProvider, as_of_date: date
) -> Mapping[str, Callable[[], pd.DataFrame]]:
    """Keep fetch order stable because persistence composes stock and index prices."""

    return {
        "instruments": provider.fetch_stock_universe,
        "daily_prices": lambda: provider.fetch_stock_daily(as_of_date),
        "index_history": lambda: provider.fetch_index_history(
            DEFAULT_RISK_INDEXES, as_of_date, as_of_date
        ),
        "fund_daily": provider.fetch_fund_daily,
    }


def _persist_normalized_dataset(
    db: Database,
    dataset: str,
    as_of_date: date,
    frame: pd.DataFrame,
    snapshot_id: int,
) -> None:
    if dataset == "instruments":
        allowed = [
            "symbol",
            "asset_type",
            "name",
            "exchange",
            "industry",
            "board",
            "listed_on",
            "delisted_on",
            "is_st",
            "is_suspended",
            "status",
            "as_of_date",
        ]
        normalized = frame.loc[:, frame.columns.intersection(allowed)].copy()
        db.upsert_frame("instruments", normalized)
        db.upsert_frame("instrument_history", normalized)
        return

    if dataset in {"daily_prices", "index_history"}:
        prices = frame.copy()
        prices["source_snapshot_id"] = snapshot_id
        if dataset == "daily_prices":
            # Stocks and indices share a table but have independent validation.
            # An invalid index refresh must not lose its last valid evidence.
            indices = db.query_df("SELECT symbol FROM instruments WHERE asset_type = 'index'")
            db.replace_for_date(
                "daily_prices",
                as_of_date,
                prices,
                preserve_symbols=indices["symbol"].astype(str).tolist(),
            )
        else:
            db.upsert_frame("daily_prices", prices)
        return

    if dataset == "fund_daily":
        columns = ["symbol", "nav_date", "unit_nav", "accumulated_nav"]
        nav = frame.loc[:, columns].copy()
        nav["source_snapshot_id"] = snapshot_id
        db.replace_for_date("fund_nav", as_of_date, nav)
        instruments = _fund_instruments(frame, as_of_date)
        db.upsert_frame("instruments", instruments)
        db.upsert_frame("instrument_history", instruments)
        return

    raise ValueError(f"no persistence mapping for dataset: {dataset}")


def _with_system_instruments(frame: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    system = pd.DataFrame(
        [
            {
                "symbol": "000300.SH",
                "asset_type": "index",
                "name": "沪深300",
                "exchange": "SH",
                "industry": None,
                "board": "index",
                "listed_on": date(2005, 4, 8),
                "delisted_on": None,
                "is_st": False,
                "is_suspended": False,
                "status": "active",
                "as_of_date": as_of_date,
            },
            {
                "symbol": "CASH.CNY",
                "asset_type": "cash",
                "name": "人民币现金",
                "exchange": "LOCAL",
                "industry": None,
                "board": "cash",
                "listed_on": date(2000, 1, 1),
                "delisted_on": None,
                "is_st": False,
                "is_suspended": False,
                "status": "active",
                "as_of_date": as_of_date,
            },
        ]
    )
    existing = set(frame["symbol"].astype(str)) if "symbol" in frame else set()
    additions = system.loc[~system["symbol"].isin(existing)]
    return pd.concat([frame, additions], ignore_index=True, sort=False)


def _point_in_time_instruments(frame: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    """Make a current exchange catalogue safe for an earlier decision date.

    Exchange feeds can advertise securities before their listing day.  Retaining those
    rows in an earlier snapshot would leak future knowledge into eligibility checks.
    """

    result = frame.copy(deep=True)
    result["as_of_date"] = as_of_date
    if {"asset_type", "listed_on"}.issubset(result.columns):
        listed_on = pd.to_datetime(result["listed_on"], errors="coerce")
        future_stock = result["asset_type"].astype(str).eq("stock") & listed_on.gt(
            pd.Timestamp(as_of_date)
        )
        result = result.loc[~future_stock].copy()
    return result.reset_index(drop=True)


def _fund_instruments(frame: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    names = frame.get("name", frame["symbol"]).astype("string").fillna(frame["symbol"])
    return pd.DataFrame(
        {
            "symbol": frame["symbol"].astype(str),
            "asset_type": "fund",
            "name": names.astype(str),
            "exchange": "OTC",
            "industry": None,
            "board": "fund",
            "listed_on": None,
            "delisted_on": None,
            "is_st": False,
            "is_suspended": False,
            "status": "active",
            "as_of_date": as_of_date,
        }
    )
