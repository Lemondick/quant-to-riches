"""Production entry points with mandatory account sessions and isolated user storage."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from quant_assistant.config import DeploymentSettings, Settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    """Validated environment-driven settings for a production process."""

    host: str
    port: int
    api_token: str | None
    require_auth: bool
    rate_limit_per_minute: int
    data_dir: Path
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> DeploymentConfig:
        settings = Settings.from_env()
        configured = settings.deployment
        host = configured.host
        port = configured.port
        return cls(
            host=host,
            port=port,
            api_token=None,
            require_auth=True,
            rate_limit_per_minute=configured.rate_limit_per_minute,
            data_dir=settings.data_dir,
            allowed_origins=configured.allowed_origins,
        )


def create_production_app(config: DeploymentConfig | None = None):
    """Build a secured FastAPI application for Uvicorn or a process manager."""

    selected = config or DeploymentConfig.from_env()
    # Keep explicit deployment storage while retaining opted-in research/OCR
    # configuration. for_data_dir() deliberately disables environment settings.
    data_dir = selected.data_dir.expanduser().resolve()
    settings = replace(
        Settings.from_env(),
        data_dir=data_dir,
        raw_snapshot_dir=data_dir / "raw",
        database_path=data_dir / "quant_assistant.duckdb",
        deployment=replace(
            Settings.from_env().deployment,
            host=selected.host,
            port=selected.port,
            require_auth=True,
            rate_limit_per_minute=selected.rate_limit_per_minute,
            allowed_origins=selected.allowed_origins,
        ),
    )
    from quant_assistant.controllers.auth_app import (  # Lazy: FastAPI only for serving.
        create_authenticated_app,
    )
    return create_authenticated_app(settings)


def serve_production(config: DeploymentConfig | None = None) -> None:
    """Run the production API with the validated bind and authentication policy."""

    import uvicorn  # Lazy: the worker imports DeploymentConfig without a web stack.

    selected = config or DeploymentConfig.from_env()
    uvicorn.run(
        create_production_app(selected),
        host=selected.host,
        port=selected.port,
        server_header=False,
        date_header=False,
    )


def _load_token(configured: DeploymentSettings | None = None) -> str | None:
    selected = configured or Settings.from_env().deployment
    if selected.api_token_file:
        path = selected.api_token_file
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("QUANT_API_TOKEN_FILE cannot be read") from exc
    else:
        token = selected.api_token or ""
    if not token:
        return None
    if len(token) < 32:
        raise ValueError("QUANT_API_TOKEN must contain at least 32 characters")
    return token


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _boolean(value: object, *, default: bool, name: str) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _origins(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError("QUANT_CORS_ORIGINS must be a comma-separated string")
    origins = (item.strip().rstrip("/") for item in value.split(",") if item.strip())
    return tuple(dict.fromkeys(origins))


__all__ = ["DeploymentConfig", "create_production_app", "serve_production"]
