"""Execute the pinned full TradingAgents graph in its isolated virtual environment."""

import json
import os
import subprocess
from pathlib import Path

from quant_assistant.providers.advisor_models import AdvisorModelError

MAX_RESULT_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 170


def execute_full_tradingagents(payload: dict) -> dict:
    python = os.environ.get("QUANT_TRADINGAGENTS_PYTHON", "").strip()
    runner = Path(
        os.environ.get(
            "QUANT_TRADINGAGENTS_RUNNER",
            "/opt/quant-tradingagents/runner.py",
        )
    )
    if not python or not Path(python).is_file() or not runner.is_file():
        raise AdvisorModelError("model_configuration")

    request = payload.get("request")
    credentials = payload.get("credentials")
    models = payload.get("models")
    execution = payload.get("_execution")
    if (
        not isinstance(request, dict)
        or not isinstance(credentials, dict)
        or not isinstance(models, dict)
        or not isinstance(execution, dict)
    ):
        raise AdvisorModelError("model_configuration")

    task = {
        "schema_version": "quant-tradingagents-v1",
        "symbol": request.get("symbol"),
        "trade_date": request.get("data_date"),
        "endpoint": credentials.get("endpoint"),
        "api_key": credentials.get("api_key"),
        "quick_model": models.get("strategy"),
        "deep_model": models.get("reviewer"),
        "execution": execution,
    }
    body = json.dumps(task, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(body) > 256_000:
        raise AdvisorModelError("invalid_output")

    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        }
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        process = subprocess.run(
            [python, "-I", str(runner)],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AdvisorModelError("model_timeout") from None
    except OSError:
        raise AdvisorModelError("model_unavailable") from None

    if process.returncode != 0 or len(process.stdout) > MAX_RESULT_BYTES:
        raise AdvisorModelError("model_unavailable")
    try:
        envelope = json.loads(process.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AdvisorModelError("invalid_output") from None
    if not isinstance(envelope, dict) or envelope.get("error_code"):
        code = envelope.get("error_code") if isinstance(envelope, dict) else None
        if code == "model_call_limit":
            raise AdvisorModelError("model_budget_exceeded")
        if code in {"model_configuration", "invalid_request"}:
            raise AdvisorModelError("model_configuration")
        if code == "model_endpoint_unavailable":
            raise AdvisorModelError("model_unavailable")
        if code == "model_input_limit" or code == "tool_call_limit" or code == "result_too_large":
            raise AdvisorModelError("invalid_output")
        raise AdvisorModelError("model_unavailable")
    result = envelope.get("result")
    if (
        not isinstance(result, dict)
        or result.get("research_only") is not True
        or result.get("automatic_trading") is not False
    ):
        raise AdvisorModelError("invalid_output")
    return result
