"""Opt-in research configuration; credentials stay outside task JSON."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class AgentResearchSettings:
    enabled: bool = False
    provider: str = "mock"
    model: str = "mock-v1"
    endpoint: str = ""
    key_file: Path | None = None
    evidence_manifest: Path | None = None
    python_executable: str = ""
    transport: str = "subprocess"
    exchange_dir: Path | None = None
    daily_limit: int = 10
    timeout_seconds: float = 180

    def __post_init__(self):
        if self.provider not in {"mock", "compatible"}:
            raise ValueError("unsupported research provider")
        if self.transport not in {"subprocess", "exchange"}:
            raise ValueError("unsupported research transport")
        if not 1 <= self.daily_limit <= 100 or not 1 <= self.timeout_seconds <= 180:
            raise ValueError("invalid research limits")
        if self.transport == "exchange" and self.exchange_dir is None:
            raise ValueError("exchange directory required")
        if self.endpoint:
            parsed = urlsplit(self.endpoint)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query:
                raise ValueError("research endpoint must be HTTPS without credentials or query")

    @classmethod
    def from_env(cls):
        def path(name):
            value = os.environ.get("QUANT_AGENT_" + name, "").strip()
            return Path(value).expanduser() if value else None

        return cls(
            enabled=os.environ.get("QUANT_AGENT_RESEARCH_ENABLED", "false").lower() == "true",
            provider=os.environ.get("QUANT_AGENT_PROVIDER", "mock"),
            model=os.environ.get("QUANT_AGENT_MODEL", "mock-v1"),
            endpoint=os.environ.get("QUANT_AGENT_ENDPOINT", ""),
            key_file=path("KEY_FILE"),
            evidence_manifest=path("EVIDENCE_MANIFEST"),
            python_executable=os.environ.get("QUANT_AGENT_PYTHON", ""),
            transport=os.environ.get("QUANT_AGENT_TRANSPORT", "subprocess"),
            exchange_dir=path("EXCHANGE_DIR"),
            daily_limit=int(os.environ.get("QUANT_AGENT_DAILY_LIMIT", "10")),
            timeout_seconds=float(os.environ.get("QUANT_AGENT_TIMEOUT_SECONDS", "180")),
        )

    def model_config(self):
        return {"provider": self.provider, "model": self.model, "endpoint": self.endpoint}
