import json
from unittest.mock import patch

from quant_assistant.providers.advisor_models import AdvisorModelError
from tradingagents_operations import execute_full_tradingagents


def payload(tmp_path):
    python = tmp_path / "python"
    runner = tmp_path / "runner.py"
    python.write_text("", encoding="utf-8")
    runner.write_text("", encoding="utf-8")
    return {
        "request": {
            "symbol": "600519.SH",
            "data_date": "2026-10-20",
            "facts": [],
        },
        "credentials": {
            "endpoint": "https://models.example.org/v1",
            "api_key": "synthetic-key",
        },
        "models": {"strategy": "quick-model", "reviewer": "deep-model"},
        "_execution": {
            "api": "http://127.0.0.1:8765/internal/v1/worker",
            "secret": "x" * 40,
            "lease": {
                "user_id": "u",
                "job_id": "j",
                "attempt": 1,
                "owner_token": "o",
            },
        },
    }


def test_full_engine_requires_explicit_installed_runtime(monkeypatch):
    monkeypatch.delenv("QUANT_TRADINGAGENTS_PYTHON", raising=False)
    monkeypatch.delenv("QUANT_TRADINGAGENTS_RUNNER", raising=False)
    with patch("tradingagents_operations.Path.is_file", return_value=False):
        try:
            execute_full_tradingagents({})
        except AdvisorModelError:
            pass
        else:
            raise AssertionError("missing runtime must fail closed")


def test_full_engine_accepts_only_research_only_result(monkeypatch, tmp_path):
    from tradingagents_operations import subprocess

    task = payload(tmp_path)
    monkeypatch.setenv("QUANT_TRADINGAGENTS_PYTHON", str(tmp_path / "python"))
    monkeypatch.setenv("QUANT_TRADINGAGENTS_RUNNER", str(tmp_path / "runner.py"))

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "result": {
                    "research_only": True,
                    "automatic_trading": False,
                    "portfolio_decision": "Hold",
                }
            }
        ).encode()

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["task"] = json.loads(kwargs["input"])
        captured["env"] = kwargs["env"]
        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = execute_full_tradingagents(task)
    assert result["research_only"] is True
    assert result["automatic_trading"] is False
    assert captured["task"]["quick_model"] == "quick-model"
    assert captured["task"]["deep_model"] == "deep-model"
    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["command"][1] == "-I"


def test_full_engine_rejects_non_research_result(monkeypatch, tmp_path):
    from tradingagents_operations import subprocess

    task = payload(tmp_path)
    monkeypatch.setenv("QUANT_TRADINGAGENTS_PYTHON", str(tmp_path / "python"))
    monkeypatch.setenv("QUANT_TRADINGAGENTS_RUNNER", str(tmp_path / "runner.py"))

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {"result": {"research_only": False, "automatic_trading": True}}
        ).encode()

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Completed())
    try:
        execute_full_tradingagents(task)
    except AdvisorModelError:
        pass
    else:
        raise AssertionError("non-research output must be rejected")
