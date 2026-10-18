"""Compute cross-sectional factor z-scores from DuckDB prices and publish them for scoring.

This worker reads daily prices from the local DuckDB archive, derives momentum and
volatility exposures per symbol, winsorizes and standardizes them across the
cross-section, then upserts definitions and exposures for every user.

It is an offline batch job: it never touches the account ledger and never writes
strategy evidence. A missing price archive or an empty cross-section aborts
instead of substituting placeholder data.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MOMENTUM_WINDOW = 20
TRADING_DAYS_PER_YEAR = 252
WINSOR_LOWER = 0.02
WINSOR_UPPER = 0.98

# Per-factor metadata mirrored into factor_definitions on first publish.
FACTORS: tuple[tuple[str, str, str, int], ...] = (
    ("MOM", "20日动量", "momentum", 1),
    ("VOL", "20日波动", "risk", -1),
)


@dataclass(frozen=True, slots=True)
class PublishSettings:
    """Connection targets for the offline worker, resolved from the environment."""

    duckdb_path: Path
    postgres_dsn: str

    @classmethod
    def from_env(cls) -> PublishSettings:
        data_dir = Path(os.environ.get("QUANT_DATA_DIR", "data")).expanduser()
        duckdb_path = Path(
            os.environ.get("QUANT_DUCKDB_PATH") or data_dir / "quant_assistant.duckdb"
        ).expanduser()
        dsn = (os.environ.get("QUANT_POSTGRES_DSN") or "").strip()
        if not dsn:
            raise ValueError(
                "QUANT_POSTGRES_DSN is required, for example "
                "'host=127.0.0.1 port=5432 dbname=quant_assistant user=<user> password=<secret>'"
            )
        return cls(duckdb_path=duckdb_path, postgres_dsn=dsn)


def compute_zscore(frame: pd.DataFrame, column: str) -> pd.Series:
    """Winsorize at the 2nd/98th percentile, then standardize the cross-section.

    A cross-section with fewer than two observations has no dispersion, so every
    member scores zero rather than producing NaN.
    """
    series = frame[column].astype(float)
    if len(frame) < 2:
        return series * 0.0
    lower, upper = series.quantile(WINSOR_LOWER), series.quantile(WINSOR_UPPER)
    clipped = series.clip(lower=lower, upper=upper)
    mean, std = clipped.mean(), clipped.std()
    if std == 0:
        return clipped - mean
    return (clipped - mean) / std


def load_exposures(duckdb_path: Path, target_date: str) -> pd.DataFrame:
    """Read prices up to the target date and derive per-symbol momentum/volatility."""
    if not duckdb_path.is_file():
        raise FileNotFoundError(f"DuckDB archive is missing: {duckdb_path}")
    with duckdb.connect(str(duckdb_path), read_only=True) as duck:
        history = duck.execute(
            """
            SELECT symbol, trade_date, close AS close_price
            FROM daily_prices
            WHERE trade_date <= ?
            ORDER BY symbol, trade_date
            """,
            [target_date],
        ).df()
    if history.empty:
        raise ValueError(f"No daily prices at or before {target_date}")

    history["return"] = history.groupby("symbol")["close_price"].pct_change()

    def metrics(group: pd.DataFrame) -> pd.Series:
        if len(group) < MOMENTUM_WINDOW:
            return pd.Series({"MOM": 0.0, "VOL": 0.0})
        window = group.tail(MOMENTUM_WINDOW)
        momentum = (window["close_price"].iloc[-1] / window["close_price"].iloc[0]) - 1
        volatility = window["return"].std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        return pd.Series({"MOM": momentum, "VOL": volatility})

    factors = history.groupby("symbol").apply(metrics, include_groups=False).reset_index()

    target = pd.to_datetime(target_date).date()
    on_date = history[history["trade_date"] == target]
    if on_date.empty:
        # A non-trading target date falls back to each symbol's latest observation.
        on_date = history.groupby("symbol").tail(1)
    prices = pd.merge(on_date, factors, on="symbol")
    prices["MOM_Z"] = compute_zscore(prices, "MOM")
    prices["VOL_Z"] = compute_zscore(prices, "VOL")
    return prices


def publish(settings: PublishSettings, prices: pd.DataFrame, target_date: str) -> int:
    """Upsert factor definitions and exposures for every user; return exposure rows written."""
    import psycopg2

    written = 0
    with psycopg2.connect(settings.postgres_dsn) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users")
            users = cursor.fetchall()
            if not users:
                logger.warning("No users found; nothing to publish.")
                return 0
            for (user_id,) in users:
                definition_rows = [
                    (user_id, code, name, category, direction)
                    for code, name, category, direction in FACTORS
                ]
                cursor.executemany(
                    """
                    INSERT INTO factor_definitions
                        (user_id, factor_code, factor_name, category, direction)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, factor_code) DO NOTHING
                    """,
                    definition_rows,
                )
                exposure_rows = [
                    (
                        user_id,
                        row["symbol"],
                        code,
                        target_date,
                        float(row[code]),
                        float(row[f"{code}_Z"]),
                    )
                    for row in prices.to_dict("records")
                    for code, _name, _category, _direction in FACTORS
                ]
                cursor.executemany(
                    """
                    INSERT INTO factor_exposures
                        (user_id, symbol, factor_code, data_date, raw_value, z_score)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, symbol, factor_code, data_date)
                    DO UPDATE SET
                        raw_value = EXCLUDED.raw_value,
                        z_score = EXCLUDED.z_score,
                        updated_at = NOW()
                    """,
                    exposure_rows,
                )
                written += len(exposure_rows)
        conn.commit()
    return written


def run(target_date: str) -> None:
    settings = PublishSettings.from_env()
    logger.info("Computing factors for %s...", target_date)
    prices = load_exposures(settings.duckdb_path, target_date)
    written = publish(settings, prices, target_date)
    logger.info("Published %s factor exposures across %s symbols.", written, len(prices))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="target trading date, YYYY-MM-DD")
    args = parser.parse_args()
    run(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
