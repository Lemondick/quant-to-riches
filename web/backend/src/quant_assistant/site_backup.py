"""Offline full-site backup and verified restore into a new directory."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile, TemporaryDirectory

import duckdb


class SiteLock:
    def __init__(self, root):
        self.path = Path(root) / ".site.lock"
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError("请先停止后端，再进行完整备份或恢复；站点已被占用") from None
        return self

    def close(self):
        if self.file:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_args):
        self.close()


def private_path(path):
    if os.name == "nt":
        import getpass
        import subprocess

        grant = "(OI)(CI)F" if path.is_dir() else "F"

        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                getpass.getuser() + ":" + grant,
                "SYSTEM:" + grant,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def create_site_backup(root, output, *, include_secrets=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError("备份必须保存到数据目录以外的新文件")
    if not (root / "auth/accounts.sqlite3").is_file():
        raise ValueError("未找到完整账号库")
    new_directory = not output.parent.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    if new_directory:
        private_path(output.parent)
    with SiteLock(root), TemporaryDirectory() as staging:
        # The site lock excludes the web process for the complete backup. Windows
        # DuckDB handles must be closed before copying their checkpointed files.
        for path in root.rglob("*.duckdb"):
            if "backups" not in path.relative_to(root).parts:
                conn = duckdb.connect(str(path))
                try:
                    conn.execute("CHECKPOINT")
                finally:
                    conn.close()
        auth_copy = Path(staging) / "accounts.sqlite3"
        source = sqlite3.connect(root / "auth/accounts.sqlite3")
        target = sqlite3.connect(auth_copy)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        files = {}
        temporary = Path(staging) / "backup.zip"
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if path.is_symlink():
                    raise ValueError("备份不支持符号链接")
                if not path.is_file() or any(
                    part in {"backups", "runtime", "__pycache__"} for part in relative.parts
                ):
                    continue
                if (
                    path.name in {".site.lock", "accounts.sqlite3-wal", "accounts.sqlite3-shm"}
                    or path.suffix == ".wal"
                ):
                    continue
                secret = (
                    path.suffix in {".key", ".pem"}
                    or path.name.startswith(".env")
                    or "server-secrets" in relative.parts
                )
                if secret and not include_secrets:
                    continue
                contents = (
                    auth_copy.read_bytes()
                    if relative.as_posix() == "auth/accounts.sqlite3"
                    else path.read_bytes()
                )
                files[relative.as_posix()] = hashlib.sha256(contents).hexdigest()
                archive.writestr(relative.as_posix(), contents)
            archive.writestr(
                "site-manifest.json",
                json.dumps(
                    {"format": 2, "files": files, "includes_secrets": include_secrets},
                    ensure_ascii=False,
                ),
            )
        with NamedTemporaryFile(dir=output.parent, delete=False) as pending:
            partial = Path(pending.name)
        try:
            private_path(partial)
            shutil.copyfile(temporary, partial)
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)
    return {"file": str(output), "files": len(files), "includes_secrets": include_secrets}


def restore_site_backup(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("恢复目录必须不存在，避免覆盖已有数据")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        TemporaryDirectory(dir=destination.parent) as temporary,
        zipfile.ZipFile(archive_path) as archive,
    ):
        staging = Path(temporary) / "data"
        staging.mkdir()
        manifest = json.loads(archive.read("site-manifest.json"))
        if manifest.get("format") != 2 or not isinstance(manifest.get("files"), dict):
            raise ValueError("不支持的完整备份格式")
        files = manifest["files"]
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(files) | {"site-manifest.json"}:
            raise ValueError("备份清单与文件不一致")
        if sum(item.file_size for item in archive.infolist()) > 20 * 1024**3:
            raise ValueError("备份解压总大小超过20GB")
        for name, digest in files.items():
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in name
                or ":" in name
                or not name
            ):
                raise ValueError("不安全的备份路径")
            contents = archive.read(name)
            if hashlib.sha256(contents).hexdigest() != digest:
                raise ValueError("备份文件校验失败")
            path = staging.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        if not (staging / "auth/accounts.sqlite3").is_file():
            raise ValueError("备份缺少账号库")
        conn = sqlite3.connect(staging / "auth/accounts.sqlite3")
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("账号库损坏")
            conn.execute("DELETE FROM sessions")
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_tokens'"
            ).fetchone():
                conn.execute("DELETE FROM recovery_tokens")
            conn.commit()
        finally:
            conn.close()
        for path in staging.rglob("*.duckdb"):
            conn = duckdb.connect(str(path), read_only=True)
            try:
                conn.execute("SELECT count(*) FROM positions").fetchone()
                conn.execute("SELECT count(*) FROM transactions").fetchone()
            finally:
                conn.close()
        private_path(staging)
        private_path(staging / "auth")
        staging.rename(destination)
    return {"directory": str(destination), "files": len(files), "sessions_revoked": True}


def main():
    parser = argparse.ArgumentParser(description="停服后完整备份 / 校验并恢复至新目录")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("create")
    backup.add_argument("--data-dir", required=True, type=Path)
    backup.add_argument("--output", required=True, type=Path)
    backup.add_argument("--include-secrets", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("--archive", required=True, type=Path)
    restore.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    result = (
        create_site_backup(args.data_dir, args.output, include_secrets=args.include_secrets)
        if args.command == "create"
        else restore_site_backup(args.archive, args.destination)
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
