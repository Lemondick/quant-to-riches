"""Single-user preferences shared by local UI and read-mostly API clients."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.services.watchlist import LocalWatchlistStore, WatchlistEntry

_FORMAT_VERSION = 1
_MAX_WATCHLIST_ITEMS = 100
_MAX_READ_NEWS_IDS = 2_000
_MAX_NEWS_ID_LENGTH = 256


@dataclass(frozen=True, slots=True)
class PreferenceSnapshot:
    watchlist: tuple[WatchlistEntry, ...]
    read_news_ids: tuple[str, ...]
    revision: int = 0
    updated_at: datetime | None = None


class LocalPreferenceStore:
    """Persist non-financial preferences with atomic file replacement."""

    def __init__(
        self,
        path: str | Path,
        *,
        legacy_watchlist_path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path).expanduser().resolve()
        self._legacy_watchlist_path = (
            None
            if legacy_watchlist_path is None
            else Path(legacy_watchlist_path).expanduser().resolve()
        )
        self._clock = clock or (lambda: datetime.now(SHANGHAI_TZ))
        self._lock = RLock()

    def load(self) -> PreferenceSnapshot:
        with self._lock:
            return self._load_unlocked()

    def replace_watchlist(self, entries: Iterable[WatchlistEntry]) -> PreferenceSnapshot:
        selected = tuple(entries)
        if any(not isinstance(item, WatchlistEntry) for item in selected):
            raise TypeError("watchlist entries must use WatchlistEntry")
        if len(selected) > _MAX_WATCHLIST_ITEMS:
            raise ValueError(f"自选最多保存 {_MAX_WATCHLIST_ITEMS} 项")
        symbols = tuple(item.symbol for item in selected)
        if len(symbols) != len(set(symbols)):
            raise ValueError("自选代码不能重复")
        with self._lock:
            current = self._load_unlocked()
            if current.watchlist == selected:
                return current
            return self._write(selected, current.read_news_ids, current.revision + 1)

    def add_watchlist(self, entry: WatchlistEntry) -> PreferenceSnapshot:
        if not isinstance(entry, WatchlistEntry):
            raise TypeError("entry must be WatchlistEntry")
        with self._lock:
            current = self._load_unlocked()
            entries = tuple(item for item in current.watchlist if item.symbol != entry.symbol)
            return self.replace_watchlist((*entries, entry))

    def remove_watchlist(self, symbol: str) -> PreferenceSnapshot:
        normalized = str(symbol).strip()
        with self._lock:
            current = self._load_unlocked()
            entries = tuple(item for item in current.watchlist if item.symbol != normalized)
            if len(entries) == len(current.watchlist):
                return current
            return self.replace_watchlist(entries)

    def mark_news_read(self, ids: Iterable[str]) -> PreferenceSnapshot:
        normalized = tuple(_news_id(item) for item in ids)
        with self._lock:
            current = self._load_unlocked()
            merged = tuple(dict.fromkeys((*current.read_news_ids, *normalized)))
            if len(merged) > _MAX_READ_NEWS_IDS:
                merged = merged[-_MAX_READ_NEWS_IDS:]
            if merged == current.read_news_ids:
                return current
            return self._write(current.watchlist, merged, current.revision + 1)

    def _load_unlocked(self) -> PreferenceSnapshot:
        if not self._path.exists():
            return PreferenceSnapshot(self._load_legacy_watchlist(), ())
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("偏好文件无法读取") from exc
        if not isinstance(payload, dict) or payload.get("version") != _FORMAT_VERSION:
            raise ValueError("偏好文件格式无效")
        try:
            watchlist = tuple(WatchlistEntry(**item) for item in payload.get("watchlist", ()))
            read_news_ids = tuple(_news_id(item) for item in payload.get("read_news_ids", ()))
            revision = int(payload.get("revision", 0))
            updated_at = _datetime_or_none(payload.get("updated_at"))
        except (TypeError, ValueError) as exc:
            raise ValueError("偏好文件包含无效数据") from exc
        if len(watchlist) > _MAX_WATCHLIST_ITEMS or len(read_news_ids) > _MAX_READ_NEWS_IDS:
            raise ValueError("偏好文件条目数量超限")
        if revision < 0:
            raise ValueError("偏好文件版本号无效")
        if len({item.symbol for item in watchlist}) != len(watchlist):
            raise ValueError("偏好文件包含重复自选代码")
        if len(set(read_news_ids)) != len(read_news_ids):
            raise ValueError("偏好文件包含重复资讯标识")
        return PreferenceSnapshot(watchlist, read_news_ids, revision, updated_at)

    def _load_legacy_watchlist(self) -> tuple[WatchlistEntry, ...]:
        if self._legacy_watchlist_path is None or not self._legacy_watchlist_path.exists():
            return ()
        return LocalWatchlistStore(self._legacy_watchlist_path).load()

    def _write(
        self,
        watchlist: tuple[WatchlistEntry, ...],
        read_news_ids: tuple[str, ...],
        revision: int,
    ) -> PreferenceSnapshot:
        updated_at = self._clock()
        if not isinstance(updated_at, datetime):
            raise TypeError("preference clock must return datetime")
        updated_at = (
            updated_at.replace(tzinfo=SHANGHAI_TZ)
            if updated_at.tzinfo is None
            else updated_at.astimezone(SHANGHAI_TZ)
        )
        snapshot = PreferenceSnapshot(watchlist, read_news_ids, revision, updated_at)
        payload = {
            "version": _FORMAT_VERSION,
            "revision": revision,
            "updated_at": updated_at.isoformat(),
            "watchlist": [asdict(item) for item in watchlist],
            "read_news_ids": list(read_news_ids),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)
        return snapshot


def _news_id(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _MAX_NEWS_ID_LENGTH or any(ord(char) < 32 for char in text):
        raise ValueError("资讯标识不能为空且最多 256 个可见字符")
    return text


def _datetime_or_none(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value))
    return (
        parsed.replace(tzinfo=SHANGHAI_TZ)
        if parsed.tzinfo is None
        else parsed.astimezone(SHANGHAI_TZ)
    )


__all__ = ["LocalPreferenceStore", "PreferenceSnapshot"]
