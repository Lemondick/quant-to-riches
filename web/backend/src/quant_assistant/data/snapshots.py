"""Immutable, content-addressed Parquet snapshots."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    dataset: str
    source: str
    as_of_date: date
    fetched_at: datetime
    path: Path
    sha256: str
    row_count: int


class SnapshotWriter:
    def __init__(
        self,
        root: Path,
        source: str,
        *,
        clock: Callable[[], datetime] = now_shanghai,
    ):
        if not _SAFE_COMPONENT.fullmatch(source):
            raise ValueError("source must contain only letters, digits, underscores, or hyphens")
        self.root = Path(root)
        self.source = source
        self._clock = clock

    def write(self, dataset: str, as_of_date: date, frame: pd.DataFrame) -> SnapshotRecord:
        if not _SAFE_COMPONENT.fullmatch(dataset):
            raise ValueError("dataset must contain only letters, digits, underscores, or hyphens")
        # Parquet intentionally omits the source index; hash exactly those persisted rows.
        frame = frame.reset_index(drop=True)
        payload_hash = _frame_sha256(frame)
        path = (
            self.root
            / self.source
            / as_of_date.isoformat()
            / f"{dataset}-{payload_hash[:12]}.parquet"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            frame.to_parquet(path, index=False)
        return SnapshotRecord(
            dataset=dataset,
            source=self.source,
            as_of_date=as_of_date,
            fetched_at=self._clock(),
            path=path,
            sha256=payload_hash,
            row_count=len(frame),
        )


def _frame_sha256(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update("\x1f".join(str(column) for column in frame.columns).encode("utf-8"))
    digest.update("\x1f".join(str(dtype) for dtype in frame.dtypes).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()
