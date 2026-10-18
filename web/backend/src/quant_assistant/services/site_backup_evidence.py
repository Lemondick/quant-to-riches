"""Read-only evidence of full-site restore integrity and current account coverage."""

import re
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from quant_assistant.site_backup import restore_site_backup


def _accounts(root):
    path = root / "auth/accounts.sqlite3"
    if not path.is_file():
        raise ValueError("site_account_registry_missing")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT id,role FROM users").fetchall()
    if not rows or sum(role == "admin" for _, role in rows) != 1:
        raise ValueError("site_admin_registry_invalid")
    if any(
        not re.fullmatch(r"[a-f0-9]{32}", user_id) or role not in {"admin", "user"}
        for user_id, role in rows
    ):
        raise ValueError("site_account_identity_invalid")
    return dict(rows)


def verify_site_archive(archive: Path, *, site_root: Path | None = None) -> dict:
    """Restore only to a temporary directory; never change source files or account sessions."""
    archive = Path(archive).resolve()
    result = {
        "archive": archive.name,
        "verified": False,
        "account_count": 0,
        "business_database_count": 0,
        "blockers": [],
    }
    try:
        with TemporaryDirectory(prefix="quant-site-restore-check-") as temporary:
            restored = Path(temporary) / "restored"
            restore_site_backup(archive, restored)
            accounts = _accounts(restored)
            if not (restored / "quant_assistant.duckdb").is_file():
                raise ValueError("site_admin_database_missing")
            if site_root is not None:
                source = Path(site_root).resolve()
                current = _accounts(source)
                if any(accounts.get(identity) != role for identity, role in current.items()):
                    raise ValueError("site_backup_account_coverage_incomplete")
                for identity, role in current.items():
                    directory = Path() if role == "admin" else Path("users") / identity
                    for path in (source / directory).rglob("*"):
                        relative = path.relative_to(source)
                        if (
                            not path.is_file()
                            or (role == "admin" and relative.parts[0] == "users")
                            or any(
                                part
                                in {"auth", "backups", "runtime", "__pycache__", "server-secrets"}
                                for part in relative.parts
                            )
                            or path.name == ".site.lock"
                            or path.name.startswith(".env")
                            or path.suffix in {".key", ".pem", ".wal"}
                        ):
                            continue
                        if not (restored / relative).is_file():
                            raise ValueError("site_backup_storage_coverage_incomplete")
            result.update(
                verified=True,
                account_count=len(accounts),
                business_database_count=len(list(restored.rglob("*.duckdb"))),
            )
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        sqlite3.Error,
        duckdb.Error,
        zipfile.BadZipFile,
    ):
        # Metadata is safe for administrator runtime views; no account payload or paths are exposed.
        result["blockers"] = ["full_site_backup_verification_failed"]
    return result
