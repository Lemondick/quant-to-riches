"""Isolated runner for the pinned TradingAgents-Astock LangGraph.

Input/output are single JSON documents. The process is intentionally stateless:
all result, cache and memory paths are scoped to a temporary directory.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.callbacks import BaseCallbackHandler

UPSTREAM_COMMIT = "e3981af1e4c94656cdc663f1466d54fd49253dd5"
MAX_INPUT_BYTES = 256_000
MAX_OUTPUT_BYTES = 2_000_000
MAX_LLM_CALLS = 80
MAX_TOOL_CALLS = 120
MAX_INPUT_CHARS_PER_CALL = 50_000
MAX_OUTPUT_TOKENS = 2_500
SYMBOL_RE = re.compile(r"^\d{6}\.(?:SH|SZ)$")


class RunnerFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _safe_text(value, limit=12000):
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return ""


def _pin_public_endpoint(endpoint: str):
    """Validate the configured HTTPS endpoint and pin its DNS answers for this process."""
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(ord(ch) < 32 for ch in endpoint)
    ):
        raise RunnerFailure("model_configuration")

    host = parsed.hostname
    port = parsed.port or 443
    original_getaddrinfo = socket.getaddrinfo
    try:
        rows = original_getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise RunnerFailure("model_endpoint_unavailable") from None

    addresses = []
    for _family, _socktype, _proto, _canonname, sockaddr in rows:
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise RunnerFailure("model_endpoint_unavailable") from None
        if not ip.is_global:
            raise RunnerFailure("model_endpoint_unavailable")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise RunnerFailure("model_endpoint_unavailable")

    def pinned_getaddrinfo(query_host, query_port, family=0, type=0, proto=0, flags=0):
        try:
            same_host = str(query_host).rstrip(".").lower() == host.rstrip(".").lower()
        except Exception:
            same_host = False
        if not same_host:
            return original_getaddrinfo(query_host, query_port, family, type, proto, flags)
        results = []
        for address in addresses:
            ip = ipaddress.ip_address(address)
            candidate_family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            if family not in (0, socket.AF_UNSPEC, candidate_family):
                continue
            if type not in (0, socket.SOCK_STREAM):
                continue
            sockaddr = (
                (address, query_port, 0, 0)
                if candidate_family == socket.AF_INET6
                else (address, query_port)
            )
            results.append(
                (candidate_family, socket.SOCK_STREAM, proto or socket.IPPROTO_TCP, "", sockaddr)
            )
        if not results:
            raise socket.gaierror(socket.EAI_NONAME, "No pinned public address")
        return results

    socket.getaddrinfo = pinned_getaddrinfo
    return original_getaddrinfo, host, addresses


class BudgetCallbacks(BaseCallbackHandler):
    """Reserve every LLM request in the authoritative C# cost ledger."""

    raise_error = True

    def __init__(self, execution: dict, fallback_model: str):
        self.api = str(execution["api"]).rstrip("/")
        self.secret = str(execution["secret"])
        self.lease = {
            key: execution["lease"][key] for key in ("user_id", "job_id", "attempt", "owner_token")
        }
        self.fallback_model = fallback_model
        self.session = __import__("requests").Session()
        self.session.trust_env = False
        self.session.headers["X-Quant-Worker-Secret"] = self.secret
        self.pending = {}
        self.llm_calls = 0
        self.tool_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def _post(self, path, payload):
        response = self.session.post(self.api + "/" + path, json=payload, timeout=(3, 10))
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _usage(response):
        usage = getattr(response, "llm_output", None) or {}
        usage = usage.get("token_usage") or usage.get("usage") or {}
        if not usage and getattr(response, "generations", None):
            try:
                message = response.generations[0][0].message
                usage = getattr(message, "usage_metadata", None) or {}
            except (AttributeError, IndexError, TypeError):
                usage = {}
        if not isinstance(usage, dict):
            return None, None
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if (
            type(input_tokens) is not int
            or type(output_tokens) is not int
            or input_tokens < 0
            or output_tokens < 0
        ):
            return None, None
        return input_tokens, output_tokens

    def _reserve_call(self, prompts, run_id, kwargs):
        if self.llm_calls >= MAX_LLM_CALLS:
            raise RunnerFailure("model_call_limit")
        prompt_chars = sum(len(prompt) for prompt in prompts if isinstance(prompt, str))
        if prompt_chars > MAX_INPUT_CHARS_PER_CALL:
            raise RunnerFailure("model_input_limit")
        invocation = kwargs.get("invocation_params") or {}
        model = invocation.get("model_name") or invocation.get("model") or self.fallback_model
        reserved = self._post(
            "cost-reserve",
            {
                **self.lease,
                "model": str(model),
                "role": "tradingagents",
                "provider": "compatible_api",
                "max_input_tokens": max(1, prompt_chars),
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            },
        )
        self.pending[str(run_id)] = (reserved["call_id"], str(model))
        self.llm_calls += 1

    def on_llm_start(self, serialized, prompts, run_id, **kwargs):
        self._reserve_call(prompts, run_id, kwargs)

    def on_chat_model_start(self, serialized, messages, run_id, **kwargs):
        prompt_text = [str(message) for batch in messages for message in batch]
        self._reserve_call(prompt_text, run_id, kwargs)

    def on_llm_end(self, response, run_id, **kwargs):
        pending = self.pending.pop(str(run_id), None)
        if pending is None:
            raise RunnerFailure("cost_reservation_missing")
        call_id, _ = pending
        input_tokens, output_tokens = self._usage(response)
        self._post(
            "cost-finish",
            {
                **self.lease,
                "call_id": call_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "stage": "response",
                "unknown_reason": None
                if input_tokens is not None and output_tokens is not None
                else "no_usage",
            },
        )
        if input_tokens is not None:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

    def on_llm_error(self, error, run_id, **kwargs):
        pending = self.pending.pop(str(run_id), None)
        if pending is not None:
            call_id, _ = pending
            self._post(
                "cost-finish",
                {
                    **self.lease,
                    "call_id": call_id,
                    "input_tokens": None,
                    "output_tokens": None,
                    "stage": "transport_error",
                    "unknown_reason": "no_usage",
                },
            )

    def on_tool_start(self, serialized, input_str, run_id, **kwargs):
        self.tool_calls += 1
        if self.tool_calls > MAX_TOOL_CALLS:
            raise RunnerFailure("tool_call_limit")

    def summary(self):
        return {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "usage_status": "reported"
            if self.llm_calls and self.total_input_tokens + self.total_output_tokens > 0
            else "unknown",
        }


def run(task: dict) -> dict:
    if not isinstance(task, dict) or task.get("schema_version") != "quant-tradingagents-v1":
        raise RunnerFailure("invalid_request")
    symbol = task.get("symbol")
    trade_date = task.get("trade_date")
    if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol):
        raise RunnerFailure("invalid_request")
    if not isinstance(trade_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date):
        raise RunnerFailure("invalid_request")
    from datetime import date

    try:
        parsed_date = date.fromisoformat(trade_date)
    except ValueError:
        raise RunnerFailure("invalid_request") from None
    if parsed_date > date.today():
        raise RunnerFailure("invalid_request")

    endpoint = task.get("endpoint")
    api_key = task.get("api_key")
    quick_model = task.get("quick_model")
    deep_model = task.get("deep_model")
    if not all(
        isinstance(v, str) and v.strip() for v in (endpoint, api_key, quick_model, deep_model)
    ):
        raise RunnerFailure("model_configuration")
    if len(api_key) > 4096 or any(ch.isspace() for ch in api_key):
        raise RunnerFailure("model_configuration")
    if len(quick_model) > 128 or len(deep_model) > 128:
        raise RunnerFailure("model_configuration")

    execution = task.get("execution")
    if not isinstance(execution, dict) or not isinstance(execution.get("lease"), dict):
        raise RunnerFailure("model_configuration")

    old_resolver, pinned_host, pinned_addresses = _pin_public_endpoint(endpoint)
    try:
        # Never share upstream memory/cache/logs across accounts or jobs.
        with tempfile.TemporaryDirectory(prefix="quant-tradingagents-") as directory:
            root = Path(directory)
            cache_dir = root / "cache"
            results_dir = root / "results"
            cache_dir.mkdir()
            results_dir.mkdir()
            home_dir = root / "home"
            home_dir.mkdir()
            memory_path = root / "memory.md"
            # Pinned upstream missing_data.py resolves Path.home() at import
            # time, independently of its configurable result/cache paths.
            os.environ["HOME"] = str(home_dir)
            os.environ["USERPROFILE"] = str(home_dir)
            os.environ["OPENAI_API_KEY"] = api_key
            os.environ["TRADINGAGENTS_RESULTS_DIR"] = str(results_dir)
            os.environ["TRADINGAGENTS_CACHE_DIR"] = str(cache_dir)
            os.environ["TRADINGAGENTS_MEMORY_LOG_PATH"] = str(memory_path)
            os.environ["TRADINGAGENTS_MAX_TOKENS"] = str(MAX_OUTPUT_TOKENS)

            from tradingagents.graph.trading_graph import TradingAgentsGraph

            callbacks = BudgetCallbacks(execution, quick_model)
            config = {
                "llm_provider": "openai_compatible",
                "backend_url": endpoint.rstrip("/"),
                "quick_think_llm": quick_model,
                "deep_think_llm": deep_model,
                "output_language": "Chinese",
                "max_tokens": MAX_OUTPUT_TOKENS,
                "llm_timeout": 45,
                "llm_max_retries": 1,
                "llm_retry_delay": 1,
                "max_debate_rounds": 1,
                "max_risk_discuss_rounds": 1,
                "max_recur_limit": 48,
                "selected_analysts": [
                    "market",
                    "social",
                    "news",
                    "fundamentals",
                    "policy",
                    "hot_money",
                    "lockup",
                ],
                "data_vendors": {
                    "core_stock_apis": "a_stock",
                    "technical_indicators": "a_stock",
                    "fundamental_data": "a_stock",
                    "news_data": "a_stock",
                    "signal_data": "a_stock",
                },
                "tool_vendors": {},
                "checkpoint_enabled": False,
                "project_dir": str(root),
                "results_dir": str(results_dir),
                "data_cache_dir": str(cache_dir),
                "memory_log_path": str(memory_path),
            }
            graph = TradingAgentsGraph(
                selected_analysts=config["selected_analysts"],
                debug=False,
                config=config,
                callbacks=[callbacks],
            )
            try:
                company = symbol.split(".")[0]
                # The pinned propagate() omits callbacks from graph config.
                # Its public lifecycle keeps runtime tool events and constructor
                # LLM callbacks on the same authoritative budget handler.
                initial_state, args, _ = graph.prepare_graph_run(
                    company, trade_date, callbacks=[callbacks]
                )
                final_state = graph.graph.invoke(initial_state, **args)
                signal = graph.finalize_graph_run(company, trade_date, final_state)
            finally:
                # Upstream normally closes the graph during propagate; close again
                # defensively if a provider failed before normal graph cleanup.
                with suppress(Exception):
                    graph.close_graph_run()

            analyst_fields = {
                "market": "market_report",
                "social": "sentiment_report",
                "news": "news_report",
                "fundamentals": "fundamentals_report",
                "policy": "policy_report",
                "hot_money": "hot_money_report",
                "lockup": "lockup_report",
            }
            analysts = {
                role: _safe_text(final_state.get(field, ""))
                for role, field in analyst_fields.items()
            }
            quality = _safe_text(final_state.get("data_quality_summary", ""))
            missing_tasks = final_state.get("missing_data_tasks", [])
            if not isinstance(missing_tasks, list):
                missing_tasks = []
            result = {
                "schema_version": "tradingagents-result-v1",
                "upstream": {
                    "repository": "simonlin1212/TradingAgents-astock",
                    "commit": UPSTREAM_COMMIT,
                    "version": "0.5.21",
                },
                "input": {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "endpoint_host": pinned_host,
                    "pinned_public_addresses": pinned_addresses,
                },
                "analysts": analysts,
                "data_quality_summary": quality,
                "missing_data_tasks": [_safe_text(item, 1000) for item in missing_tasks[:100]],
                "missing_data_complete": bool(final_state.get("missing_data_complete", False)),
                "missing_data_requires_reanalysis": bool(
                    final_state.get("missing_data_requires_reanalysis", False)
                ),
                "research_plan": _safe_text(final_state.get("investment_plan", "")),
                "trader_proposal": _safe_text(final_state.get("trader_investment_plan", "")),
                "risk_debate": _safe_text(final_state.get("risk_debate_state", {})),
                "portfolio_decision": _safe_text(final_state.get("final_trade_decision", "")),
                "signal": _safe_text(signal, 3000),
                "usage": callbacks.summary(),
                "research_only": True,
                "automatic_trading": False,
            }
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_OUTPUT_BYTES:
                raise RunnerFailure("result_too_large")
            return result
    finally:
        socket.getaddrinfo = old_resolver


def main():
    content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(content) > MAX_INPUT_BYTES:
        result = {"error_code": "invalid_request"}
    else:
        try:
            task = json.loads(content)
            result = {"result": run(task), "error_code": None}
        except RunnerFailure as error:
            result = {"result": None, "error_code": error.code}
        except Exception:
            # Do not emit exception text; SDK errors can include prompt or endpoint details.
            result = {"result": None, "error_code": "tradingagents_failed"}
    output = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(output) > MAX_OUTPUT_BYTES:
        output = b'{"result":null,"error_code":"result_too_large"}'
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
