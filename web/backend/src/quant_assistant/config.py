"""Environment-derived application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from quant_assistant.analysis.research_config import AgentResearchSettings


@dataclass(frozen=True, slots=True)
class DeploymentSettings:
    """Validated process/network knobs shared by API entry points."""

    host: str = "127.0.0.1"
    port: int = 8765
    require_auth: bool | None = None
    rate_limit_per_minute: int = 120
    allowed_origins: tuple[str, ...] = ()
    api_token: str | None = field(default=None, repr=False, metadata={"secret": True})
    api_token_file: Path | None = None

    @classmethod
    def from_env(cls) -> DeploymentSettings:
        host = _text(os.environ.get("QUANT_API_HOST", "127.0.0.1"), "QUANT_API_HOST")
        port = _positive_int(os.environ.get("QUANT_API_PORT", "8765"), "QUANT_API_PORT")
        remote = host.lower() not in {"127.0.0.1", "localhost", "::1"}
        require_auth = _optional_bool(
            os.environ.get("QUANT_REQUIRE_AUTH"),
            default=remote,
            name="QUANT_REQUIRE_AUTH",
        )
        rate_limit = _positive_int(
            os.environ.get("QUANT_RATE_LIMIT_PER_MINUTE", "120"),
            "QUANT_RATE_LIMIT_PER_MINUTE",
        )
        token = (os.environ.get("QUANT_API_TOKEN") or "").strip() or None
        token_file_value = (os.environ.get("QUANT_API_TOKEN_FILE") or "").strip()
        return cls(
            host=host,
            port=port,
            require_auth=require_auth,
            rate_limit_per_minute=rate_limit,
            allowed_origins=_origins(os.environ.get("QUANT_CORS_ORIGINS", "")),
            api_token=token,
            api_token_file=Path(token_file_value).expanduser() if token_file_value else None,
        )


@dataclass(frozen=True, slots=True)
class OcrSettings:
    """OCR provider selection and endpoint settings."""

    provider: str = "paddleocr"
    paddle_endpoint: str = "https://paddleocr.xzrcka.me/ocr"
    timeout_seconds: float = 45.0
    baidu_api_key: str | None = field(default=None, repr=False, metadata={"secret": True})
    baidu_secret_key: str | None = field(default=None, repr=False, metadata={"secret": True})

    @classmethod
    def from_env(cls) -> OcrSettings:
        provider = (os.environ.get("QUANT_OCR_PROVIDER") or "paddleocr").strip().lower()
        endpoint = (
            os.environ.get("PADDLE_OCR_ENDPOINT") or "https://paddleocr.xzrcka.me/ocr"
        ).strip()
        timeout = _positive_float(
            os.environ.get("QUANT_OCR_TIMEOUT_SECONDS", "45"),
            "QUANT_OCR_TIMEOUT_SECONDS",
            maximum=60,
        )
        return cls(
            provider=provider,
            paddle_endpoint=endpoint,
            timeout_seconds=timeout,
            baidu_api_key=_secret_from_env_or_file("BAIDU_OCR_API_KEY", "BAIDU_OCR_API_KEY_FILE"),
            baidu_secret_key=_secret_from_env_or_file(
                "BAIDU_OCR_SECRET_KEY", "BAIDU_OCR_SECRET_KEY_FILE"
            ),
        )


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    """Optional notification credentials kept out of normal logs/repr."""

    wecom_webhook_url: str | None = field(default=None, repr=False, metadata={"secret": True})
    serverchan_sendkey: str | None = field(default=None, repr=False, metadata={"secret": True})
    pushplus_token: str | None = field(default=None, repr=False, metadata={"secret": True})
    pushplus_channel: str = "wechat"
    wechat_app_id: str | None = field(default=None, repr=False, metadata={"secret": True})
    wechat_app_secret: str | None = field(default=None, repr=False, metadata={"secret": True})
    wechat_openid: str | None = field(default=None, repr=False, metadata={"secret": True})
    wechat_template_id: str | None = field(default=None, repr=False, metadata={"secret": True})
    wechat_data_json: str = field(default="", repr=False)
    wechat_page: str = "pages/home/index"
    wechat_miniprogram_state: str = "formal"

    @classmethod
    def from_env(cls) -> NotificationSettings:
        return cls(
            wecom_webhook_url=_optional_env("QUANT_WECOM_WEBHOOK_URL"),
            serverchan_sendkey=(
                _optional_env("QUANT_SERVERCHAN_SENDKEY") or _optional_env("SERVERCHAN_SENDKEY")
            ),
            pushplus_token=(
                _optional_env("QUANT_PUSHPLUS_TOKEN") or _optional_env("PUSHPLUS_TOKEN")
            ),
            pushplus_channel=(os.environ.get("QUANT_PUSHPLUS_CHANNEL") or "wechat").strip(),
            wechat_app_id=_optional_env("QUANT_WECHAT_APP_ID"),
            wechat_app_secret=_optional_env("QUANT_WECHAT_APP_SECRET"),
            wechat_openid=_optional_env("QUANT_WECHAT_OPENID"),
            wechat_template_id=_optional_env("QUANT_WECHAT_TEMPLATE_ID"),
            wechat_data_json=os.environ.get("QUANT_WECHAT_DATA_JSON", ""),
            wechat_page=(os.environ.get("QUANT_WECHAT_PAGE") or "pages/home/index").strip(),
            wechat_miniprogram_state=(
                os.environ.get("QUANT_WECHAT_MINIPROGRAM_STATE") or "formal"
            ).strip(),
        )

    def configured_channels(self) -> tuple[str, ...]:
        channels: list[str] = []
        if self.wecom_webhook_url:
            channels.append("企业微信")
        if self.serverchan_sendkey:
            channels.append("Server酱")
        if self.pushplus_token:
            channels.append("PushPlus")
        if any(
            (
                self.wechat_app_id,
                self.wechat_app_secret,
                self.wechat_openid,
                self.wechat_template_id,
            )
        ):
            channels.append("微信订阅")
        return tuple(channels)


@dataclass(frozen=True, slots=True)
class ValuationAlertSettings:
    """Threshold and notification policy for read-only fund valuation alerts."""

    # ``None`` keeps the historical two-level 1.50/3.00 percentage-point
    # behaviour.  Operators can opt into a lower informational level through
    # ``QUANT_VALUATION_ALERT_INFO_THRESHOLD`` without changing defaults.
    info_change_rate_percent: Decimal | None = None
    warning_change_rate_percent: Decimal = Decimal("1.50")
    critical_change_rate_percent: Decimal = Decimal("3.00")
    silence_minutes: int = 30

    def __post_init__(self) -> None:
        for name in ("warning_change_rate_percent", "critical_change_rate_percent"):
            value = _decimal_threshold(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.info_change_rate_percent is not None:
            info = _decimal_threshold(self.info_change_rate_percent, "info_change_rate_percent")
            if info <= 0:
                raise ValueError("info_change_rate_percent must be positive")
            object.__setattr__(self, "info_change_rate_percent", info)
            if info > self.warning_change_rate_percent:
                raise ValueError("info threshold must not exceed warning threshold")
        if self.warning_change_rate_percent > self.critical_change_rate_percent:
            raise ValueError("warning threshold must not exceed critical threshold")
        if isinstance(self.silence_minutes, bool) or not isinstance(self.silence_minutes, int):
            raise ValueError("silence_minutes must be an integer")
        if not 0 <= self.silence_minutes <= 1440:
            raise ValueError("silence_minutes must be between 0 and 1440")

    @classmethod
    def from_env(cls) -> ValuationAlertSettings:
        info = _optional_decimal_env(
            os.environ.get("QUANT_VALUATION_ALERT_INFO_THRESHOLD"),
            "QUANT_VALUATION_ALERT_INFO_THRESHOLD",
        )
        warning = _decimal_threshold(
            os.environ.get("QUANT_VALUATION_ALERT_WARNING_THRESHOLD", "1.50"),
            "QUANT_VALUATION_ALERT_WARNING_THRESHOLD",
        )
        critical = _decimal_threshold(
            os.environ.get("QUANT_VALUATION_ALERT_CRITICAL_THRESHOLD", "3.00"),
            "QUANT_VALUATION_ALERT_CRITICAL_THRESHOLD",
        )
        silence = _nonnegative_int(
            os.environ.get("QUANT_VALUATION_ALERT_SILENCE_MINUTES", "30"),
            "QUANT_VALUATION_ALERT_SILENCE_MINUTES",
        )
        return cls(info, warning, critical, silence)


@dataclass(frozen=True, slots=True)
class Settings:
    """Local-only storage and optional data-provider configuration."""

    data_dir: Path
    raw_snapshot_dir: Path
    database_path: Path
    # Provider credentials must never appear in logs, reprs, snapshots, or
    # serialized settings.  The value is only consumed at the provider boundary.
    tushare_token: str | None = field(
        default=None,
        repr=False,
        metadata={"secret": True},
    )
    deployment: DeploymentSettings = field(default_factory=DeploymentSettings)
    ocr: OcrSettings = field(default_factory=OcrSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    valuation_alerts: ValuationAlertSettings = field(default_factory=ValuationAlertSettings)
    agent_research: AgentResearchSettings = field(default_factory=AgentResearchSettings)

    @classmethod
    def from_env(cls) -> Settings:
        configured_dir = os.environ.get("QUANT_DATA_DIR")
        data_dir = Path(configured_dir).expanduser() if configured_dir else Path.cwd() / "data"
        token = os.environ.get("TUSHARE_TOKEN") or None
        return cls._build(
            data_dir,
            tushare_token=token,
            deployment=DeploymentSettings.from_env(),
            ocr=OcrSettings.from_env(),
            notifications=NotificationSettings.from_env(),
            valuation_alerts=ValuationAlertSettings.from_env(),
            agent_research=AgentResearchSettings.from_env(),
        )

    @classmethod
    def for_data_dir(cls, path: str | Path) -> Settings:
        """Create deterministic settings without consulting the environment."""

        return cls._build(Path(path), tushare_token=None)

    @classmethod
    def _build(
        cls,
        data_dir: Path,
        *,
        tushare_token: str | None,
        deployment: DeploymentSettings | None = None,
        ocr: OcrSettings | None = None,
        notifications: NotificationSettings | None = None,
        valuation_alerts: ValuationAlertSettings | None = None,
        agent_research: AgentResearchSettings | None = None,
    ) -> Settings:
        normalized_dir = data_dir.expanduser().resolve()
        return cls(
            data_dir=normalized_dir,
            raw_snapshot_dir=normalized_dir / "raw",
            database_path=normalized_dir / "quant_assistant.duckdb",
            tushare_token=tushare_token,
            deployment=deployment or DeploymentSettings(),
            ocr=ocr or OcrSettings(),
            notifications=notifications or NotificationSettings(),
            valuation_alerts=valuation_alerts or ValuationAlertSettings(),
            agent_research=agent_research or AgentResearchSettings(),
        )


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _secret_from_env_or_file(name: str, file_name: str) -> str | None:
    direct = _optional_env(name)
    if direct:
        return direct
    configured = _optional_env(file_name)
    if not configured:
        return None
    try:
        return Path(configured).expanduser().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


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


def _positive_float(value: object, name: str, *, maximum: float) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed <= 0 or parsed > maximum:
        raise ValueError(f"{name} must be between 0 and {maximum:g}")
    return parsed


def _decimal_threshold(value: object, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    return parsed


def _optional_decimal_env(value: object, name: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _decimal_threshold(value, name)


def _nonnegative_int(value: object, name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed


def _optional_bool(value: object, *, default: bool, name: str) -> bool:
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


__all__ = [
    "DeploymentSettings",
    "NotificationSettings",
    "OcrSettings",
    "Settings",
    "ValuationAlertSettings",
]
