"""Verified, secret-excluding backups for the local application state."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

import duckdb

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings

MANIFEST_NAME = "manifest.json"
BACKUP_FORMAT_VERSION = 1
LOCAL_CONFIG_NAMES = ("config.toml", "settings.json")


def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


@dataclass(frozen=True, slots=True)
class BackupFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class BackupManifest:
    version: int
    created_at: datetime
    files: tuple[BackupFile, ...]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("备份包含不安全路径")
    if len(path.parts) != 1:
        raise ValueError("备份只允许根目录文件")
    return path


def _manifest_payload(manifest: BackupManifest) -> dict[str, object]:
    return {
        "version": manifest.version,
        "created_at": manifest.created_at.isoformat(),
        "files": [
            {"path": item.path, "sha256": item.sha256, "size": item.size} for item in manifest.files
        ],
    }


def _parse_manifest(payload: bytes) -> BackupManifest:
    try:
        raw = json.loads(payload.decode("utf-8"))
        version = int(raw["version"])
        created_at = datetime.fromisoformat(raw["created_at"])
        raw_files = raw["files"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("备份清单无效") from exc
    if version != BACKUP_FORMAT_VERSION:
        raise ValueError("不支持的备份格式版本")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("备份清单时间缺少时区")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("备份清单没有文件")
    files: list[BackupFile] = []
    seen: set[str] = set()
    for raw_file in raw_files:
        try:
            raw_path = str(raw_file["path"])
            digest = str(raw_file["sha256"])
            size = int(raw_file["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("备份文件记录无效") from exc
        path = str(_safe_member_path(raw_path))
        if path in seen or len(digest) != 64 or size < 0:
            raise ValueError("备份文件记录重复或校验信息无效")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValueError("备份 SHA-256 无效") from exc
        seen.add(path)
        files.append(BackupFile(path=path, sha256=digest.lower(), size=size))
    if "quant_assistant.duckdb" not in seen:
        raise ValueError("备份缺少数据库")
    return BackupManifest(version=version, created_at=created_at, files=tuple(files))


def create_backup(
    settings: Settings,
    output: str | Path,
    *,
    clock: Callable[[], datetime] = _now_shanghai,
) -> BackupManifest:
    """Checkpoint DuckDB and create a manifest-verified ZIP archive."""

    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    database_path = settings.database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError("本地数据库不存在，请先初始化")
    output_path = Path(output).expanduser().resolve()
    if output_path == database_path:
        raise ValueError("备份输出不能覆盖数据库")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(database_path)) as connection:
        connection.execute("CHECKPOINT")

    included: list[Path] = [database_path]
    for name in LOCAL_CONFIG_NAMES:
        candidate = settings.data_dir / name
        if candidate.is_file():
            included.append(candidate.resolve())
    if any(path.name.lower().startswith(".env") for path in included):
        raise RuntimeError("备份拒绝包含环境密钥文件")
    for path in included:
        if path == database_path:
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"备份无法读取配置文件：{path.name}") from exc
        if "tushare_token" in text or "tushare-token" in text:
            raise RuntimeError("备份拒绝包含 TUSHARE_TOKEN 配置")
        if settings.tushare_token and settings.tushare_token.lower() in text:
            raise RuntimeError("备份拒绝包含数据源密钥")

    created_at = clock()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    files = tuple(
        BackupFile(path=path.name, sha256=_sha256_file(path), size=path.stat().st_size)
        for path in sorted(included, key=lambda item: item.name)
    )
    manifest = BackupManifest(BACKUP_FORMAT_VERSION, created_at, files)
    manifest_text = json.dumps(
        _manifest_payload(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    with zipfile.ZipFile(
        output_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(included, key=lambda item: item.name):
            archive.write(path, arcname=path.name)
        archive.writestr(MANIFEST_NAME, manifest_text.encode("utf-8"))
    return manifest


def _validate_archive_members(names: Sequence[str], expected: set[str]) -> None:
    if len(names) != len(set(names)):
        raise ValueError("备份包含重复文件")
    for name in names:
        _safe_member_path(name)
    if set(names) != expected | {MANIFEST_NAME}:
        raise ValueError("备份内容与清单不一致")


def restore_backup(
    archive_path: str | Path,
    target: str | Path,
    *,
    force: bool = False,
) -> BackupManifest:
    """Verify every member before restoring into an explicit target directory."""

    source = Path(archive_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("备份文件不存在")
    target_path = Path(target).expanduser().resolve()
    if target_path.exists() and any(target_path.iterdir()) and not force:
        raise FileExistsError("恢复目标非空；如确认覆盖请使用 --force")

    with zipfile.ZipFile(source, mode="r") as archive:
        names = archive.namelist()
        if MANIFEST_NAME not in names:
            raise ValueError("备份缺少清单")
        manifest = _parse_manifest(archive.read(MANIFEST_NAME))
        expected = {item.path for item in manifest.files}
        _validate_archive_members(names, expected)
        verified_payloads: dict[str, bytes] = {}
        for item in manifest.files:
            payload = archive.read(item.path)
            if len(payload) != item.size or _sha256_bytes(payload) != item.sha256:
                raise ValueError(f"备份文件校验失败：{item.path}")
            verified_payloads[item.path] = payload

    target_path.mkdir(parents=True, exist_ok=True)
    for name, payload in verified_payloads.items():
        destination = (target_path / name).resolve()
        if destination.parent != target_path:
            raise ValueError("恢复目标路径无效")
        destination.write_bytes(payload)
    return manifest


__all__ = [
    "BackupFile",
    "BackupManifest",
    "create_backup",
    "restore_backup",
]
