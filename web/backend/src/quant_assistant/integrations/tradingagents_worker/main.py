"""One task, stdin JSON -> stdout JSON; no database, tools, SDK or hidden retries."""

import json
import os
import ssl
import sys
import time
from pathlib import Path

# The parent starts Python with -I; only this audited adapter directory is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from contract import ROLES, validate_role
from prompts import ROLE_PROMPTS
from public_https import EndpointError, PinnedHTTPSConnection
from public_https import endpoint as model_endpoint


class WorkerError(Exception):
    pass


def mock_role(role, request):
    base = {key: request[key] for key in ("symbol", "data_date")}
    if role == "reviewer":
        return {
            **base,
            "summary": "模拟复核：此报告仅验证功能，不代表真实模型观点。",
            "disagreements": [{"text": "模拟：高排名不能证明未来收益。", "fact_ids": ["rank"]}],
            "questions": ["是否存在尚未纳入数据的公司公告或除权事件？"],
        }
    return {
        **base,
        "points": [
            {
                "text": "模拟：结合候选名次检查量价依据。"
                if role == "bull"
                else "模拟：价格波动仍需复核。",
                "fact_ids": ["rank" if role == "bull" else "volatility_20"],
            }
        ],
    }


def call_model(config, role, request, previous, deadline, retry_budget):
    try:
        endpoint = model_endpoint(config["endpoint"])
    except EndpointError:
        raise WorkerError("model_not_configured") from None
    key_path = os.environ.get("QUANT_AGENT_KEY_FILE", "")
    try:
        key = Path(key_path).read_text(encoding="utf-8").strip() if key_path else ""
    except OSError:
        key = ""
    if not key:
        raise WorkerError("model_not_configured")
    path = endpoint.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    body = json.dumps(
        {
            "model": config["model"],
            "max_tokens": 1200,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": ROLE_PROMPTS[role]},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"input": request, "previous": previous}, ensure_ascii=False
                    ),
                },
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    if len(body) > 48000:
        raise WorkerError("invalid_output")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WorkerError("timed_out")
        connection = PinnedHTTPSConnection(
            endpoint.hostname,
            endpoint.port,
            timeout=min(5, remaining),
            context=ssl.create_default_context(),
        )
        try:
            connection.connect()
            connection.sock.settimeout(min(45, max(0.1, deadline - time.monotonic())))
            connection.request(
                "POST",
                path,
                body,
                {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key,
                },
            )
            response = connection.getresponse()
            if response.status == 429:
                if retry_budget[0] <= 0:
                    raise WorkerError("model_rate_limited")
                retry_budget[0] -= 1
                # Only definite rate-limit responses are retried, once across all roles.
                time.sleep(min(1, max(0, deadline - time.monotonic())))
                continue
            if response.status != 200:
                raise WorkerError("model_error")
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise WorkerError("invalid_output")
            payload = json.loads(data)
            choice = payload["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise WorkerError("invalid_output")
            value = json.loads(choice["message"]["content"])
            if key in json.dumps(value, ensure_ascii=False):
                raise WorkerError("invalid_output")
            usage = payload.get("usage")
            # Whitelist numeric usage fields. Never archive provider response metadata.
            usage = (
                {
                    k: usage[k]
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if type(usage.get(k)) is int and usage[k] >= 0
                }
                if isinstance(usage, dict)
                else None
            )
            return value, usage or None
        except EndpointError:
            raise WorkerError("model_not_configured") from None
        except TimeoutError as exc:
            raise WorkerError("timed_out") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WorkerError("invalid_output") from exc
        finally:
            connection.close()


def run(task):
    request, config = task["request"], task["config"]
    deadline = time.monotonic() + min(180, task["timeout_seconds"])
    previous, usage, retries = {}, [], [1]
    for role in ROLES:
        if config["provider"] == "mock":
            value, tokens = mock_role(role, request), None
        elif config["provider"] == "compatible":
            value, tokens = call_model(config, role, request, previous, deadline, retries)
        else:
            raise WorkerError("model_not_configured")
        previous[role] = validate_role(value, role, request)
        usage.append(tokens)
    return {"report": previous, "usage": usage, "config": config}


if __name__ == "__main__":
    if sys.platform == "linux":
        import ctypes
        import signal

        # Stop on API/daemon death; PID is checked only against our actual parent.
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0:
            raise SystemExit(1)
        if os.getppid() != int(os.environ.get("QUANT_AGENT_PARENT_PID", "-1")):
            raise SystemExit(1)
    try:
        content = sys.stdin.buffer.read(64001)
        if len(content) > 64000:
            raise WorkerError("invalid_output")
        result = run(json.loads(content))
    except WorkerError as error:
        result = {"error_code": str(error)}
    except ValueError:
        result = {"error_code": "invalid_output"}
    except Exception:
        result = {"error_code": "model_error"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
