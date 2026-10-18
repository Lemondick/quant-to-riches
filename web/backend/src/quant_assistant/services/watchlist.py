"""Local watchlist contracts and migration of previously saved preferences."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock

_SYMBOL_RE = re.compile(r"\d{6}")
_ASSET_TYPES = frozenset({"fund", "stock", "index"})
_MAX_ITEMS = 100


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    symbol: str
    name: str
    asset_type: str

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip()
        name = str(self.name).strip()
        asset_type = str(self.asset_type).strip().lower()
        if _SYMBOL_RE.fullmatch(symbol) is None:
            raise ValueError("自选代码必须是六位数字")
        if not name or len(name) > 80:
            raise ValueError("自选名称不能为空且最多 80 个字符")
        if asset_type not in _ASSET_TYPES:
            raise ValueError("自选类型必须是 fund、stock 或 index")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "asset_type", asset_type)


class LocalWatchlistStore:
    """Persist user-owned watchlist entries without touching portfolio facts."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser().resolve()
        self._lock = RLock()

    def load(self) -> tuple[WatchlistEntry, ...]:
        with self._lock:
            if not self._path.exists():
                return ()
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("本地自选文件无法读取") from exc
            if not isinstance(payload, list):
                raise ValueError("本地自选文件格式无效")
            try:
                entries = tuple(WatchlistEntry(**item) for item in payload)
            except (TypeError, ValueError) as exc:
                raise ValueError("本地自选文件包含无效条目") from exc
            return entries[:_MAX_ITEMS]

    def add(self, entry: WatchlistEntry) -> tuple[WatchlistEntry, ...]:
        if not isinstance(entry, WatchlistEntry):
            raise TypeError("entry must be WatchlistEntry")
        with self._lock:
            current = list(self.load())
            current = [item for item in current if item.symbol != entry.symbol]
            current.append(entry)
            if len(current) > _MAX_ITEMS:
                raise ValueError(f"自选最多保存 {_MAX_ITEMS} 项")
            self._write(tuple(current))
            return tuple(current)

    def remove(self, symbol: str) -> tuple[WatchlistEntry, ...]:
        normalized = str(symbol).strip()
        if _SYMBOL_RE.fullmatch(normalized) is None:
            raise ValueError("自选代码必须是六位数字")
        with self._lock:
            current = tuple(item for item in self.load() if item.symbol != normalized)
            self._write(current)
            return current

    def _write(self, entries: tuple[WatchlistEntry, ...]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        payload = [asdict(item) for item in entries]
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)


__all__ = ["LocalWatchlistStore", "WatchlistEntry"]
