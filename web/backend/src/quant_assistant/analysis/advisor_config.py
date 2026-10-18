"""Private server configuration for the three-model and Jev workflow."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AdvisorSettings:
    enabled: bool = False
    endpoint: str = "https://cpa.xzrcka.me/v1"
    news_model: str = "gemini-3.1-pro-low"
    strategy_model: str = "gpt-6.1-sol"
    reviewer_model: str = "gpt-6-astra"
    key_file: Path | None = None
    jev_endpoint: str = "https://api.typesafe.ai/v1/systemone"
    jev_model: str = "jev-latest"
    jev_key_file: Path | None = None
    confidence_threshold: float = 0.8
    daily_limit: int = 4

    def __post_init__(self):
        for endpoint in (self.endpoint, self.jev_endpoint):
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query:
                raise ValueError("advisor endpoints must use HTTPS without embedded credentials")
        if not 0.5 <= self.confidence_threshold <= 1 or not 2 <= self.daily_limit <= 10:
            raise ValueError("invalid advisor limits")

    @classmethod
    def from_file(cls):
        path = Path(os.environ.get("QUANT_ADVISOR_CONFIG", "deploy/research-secrets/advisor.json"))
        if not path.is_file():
            return cls()
        values = json.loads(path.read_text(encoding="utf-8"))
        for key in ("key_file", "jev_key_file"):
            if values.get(key):
                values[key] = Path(values[key])
        return cls(**values)

    def public(self):
        ready = self.enabled and all(p and p.is_file() for p in (self.key_file, self.jev_key_file))
        return {
            "configured": bool(ready),
            "models": {
                "news": self.news_model,
                "strategy": self.strategy_model,
                "reviewer": self.reviewer_model,
                "decision": self.jev_model,
            },
            "confidence_threshold": self.confidence_threshold,
            "daily_limit": self.daily_limit,
            "timezone": "Asia/Shanghai",
            "schedules": {"morning": "09:30", "afternoon": "14:30"},
            "wechat_enabled": False,
            "automatic_trading": False,
        }
