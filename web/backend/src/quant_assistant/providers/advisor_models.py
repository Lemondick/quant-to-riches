"""Bounded server-only HTTP calls; no provider errors or credentials escape."""

import json
import math
import re

import requests

from quant_assistant.services.model_costs import CostLimitError


class AdvisorModelError(RuntimeError):
    VALIDATION_REASONS = frozenset(
        {
            "response_shape",
            "empty_response",
            "incomplete_response",
            "truncated_response",
            "invalid_json",
            "report_schema",
            "missing_claims",
            "citation_mismatch",
            "recommendation_scope",
            "duplicate_candidates",
            "response_too_large",
            "sensitive_output",
        }
    )
    CODES = frozenset(
        {
            "model_configuration",
            "model_timeout",
            "model_rate_limited",
            "model_unavailable",
            "invalid_output",
            "invalid_questions",
            "invalid_jev_output",
            "interrupted",
            "model_budget_exceeded",
            "preview_expired",
            "preview_changed",
        }
    )

    def __init__(self, code, *, timeout_phase=None, validation_reason=None):
        # Only stable codes cross the API boundary, even for injected providers.
        super().__init__(code if code in self.CODES else "invalid_output")
        self.timeout_phase = (
            timeout_phase if timeout_phase in {"connect", "read", "unknown"} else None
        )
        self.validation_reason = (
            validation_reason
            if isinstance(validation_reason, str) and validation_reason in self.VALIDATION_REASONS
            else None
        )


class AdvisorModels:
    def __init__(self, settings, *, post=requests.post):
        self.settings = settings
        self.post = post
        self.costs = None
        self.call_role = "unknown"
        self.cancelled = lambda: False

    def _reject_secret_echo(self, value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for path in (self.settings.key_file, self.settings.jev_key_file):
            if path and path.is_file():
                secret = path.read_text(encoding="utf-8").strip()
                if secret and secret in text:
                    raise AdvisorModelError("invalid_output", validation_reason="sensitive_output")

    def _request(self, endpoint, key_file, body):
        reservation, usage, stage = None, None, "response"
        try:
            if self.cancelled():
                raise AdvisorModelError("interrupted")
            key = key_file.read_text(encoding="utf-8").strip()
            if not key or len(json.dumps(body, ensure_ascii=False)) > 90000:
                raise AdvisorModelError("model_configuration")
            if self.costs is not None:
                if hasattr(self.costs, "set_context"):
                    self.costs.set_context(
                        self.call_role,
                        "jev" if key_file is self.settings.jev_key_file else "compatible_api",
                    )
                reservation = self.costs.reserve(
                    body.get("model", "unknown"),
                    # Compatible APIs do not provide a verified pre-call input token
                    # ceiling. Byte-length guesses cannot guarantee a monetary cap.
                    max_output_tokens=body.get("max_tokens"),
                )
            response = self.post(
                endpoint,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json=body,
                timeout=(10, 60),
                allow_redirects=False,
            )
            if response.status_code != 200:
                stage = "http_error"
                raise AdvisorModelError(
                    "model_rate_limited" if response.status_code == 429 else "model_unavailable"
                )
            if len(response.content) > 500000:
                raise AdvisorModelError("invalid_output", validation_reason="response_too_large")
            # Providers may echo input on failure; never retain raw response text.
            text = response.content.decode("utf-8")
            self._reject_secret_echo(text)
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                raise AdvisorModelError(
                    "invalid_output", validation_reason="invalid_json"
                ) from None
            usage = value.get("usage") if isinstance(value, dict) else None
            self._reject_secret_echo(value)
            if not isinstance(value, dict):
                raise AdvisorModelError("invalid_output", validation_reason="response_shape")
            if self.cancelled():
                raise AdvisorModelError("interrupted")
            return value
        except AdvisorModelError as error:
            if stage == "response":
                stage = "interrupted" if str(error) == "interrupted" else "invalid_response"
            raise
        except CostLimitError:
            raise
        except requests.ConnectTimeout:
            stage = "connect_timeout"
            raise AdvisorModelError("model_timeout", timeout_phase="connect") from None
        except requests.ReadTimeout:
            stage = "read_timeout"
            raise AdvisorModelError("model_timeout", timeout_phase="read") from None
        except requests.Timeout:
            stage = "timeout"
            raise AdvisorModelError("model_timeout", timeout_phase="unknown") from None
        except (OSError, AttributeError, ValueError, requests.RequestException):
            stage = "transport_error"
            raise AdvisorModelError("model_unavailable") from None
        finally:
            if reservation is not None:
                if hasattr(self.costs, "set_outcome"):
                    self.costs.set_outcome(stage)
                self.costs.finish(reservation, usage)

    def chat(self, model, instructions, state):
        data = self._request(
            self.settings.endpoint.rstrip("/") + "/chat/completions",
            self.settings.key_file,
            {
                "model": model,
                "max_tokens": 3000,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": json.dumps(state, ensure_ascii=False, default=str)},
                ],
            },
        )
        try:
            choices = data["choices"]
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError
            choice = choices[0]
        except (KeyError, TypeError, ValueError):
            raise AdvisorModelError("invalid_output", validation_reason="response_shape") from None
        if choice.get("finish_reason") != "stop":
            reason = (
                "truncated_response"
                if choice.get("finish_reason") == "length"
                else "incomplete_response"
            )
            raise AdvisorModelError("invalid_output", validation_reason=reason)
        try:
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError
            content = message["content"]
        except (KeyError, TypeError, ValueError):
            raise AdvisorModelError("invalid_output", validation_reason="response_shape") from None
        if content is None or (isinstance(content, str) and not content.strip()):
            raise AdvisorModelError("invalid_output", validation_reason="empty_response")
        if not isinstance(content, str):
            raise AdvisorModelError("invalid_output", validation_reason="response_shape")
        content = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", content)
        try:
            value = json.loads(fenced.group(1) if fenced else content)
        except json.JSONDecodeError:
            raise AdvisorModelError("invalid_output", validation_reason="invalid_json") from None
        if not isinstance(value, dict):
            raise AdvisorModelError("invalid_output", validation_reason="response_shape")
        self._reject_secret_echo(value)
        return value

    def decide(self, state, questions, *, model=None):
        self.call_role = "jev"
        if not 1 <= len(questions) <= 20:
            raise AdvisorModelError("invalid_questions")
        data = self._request(
            self.settings.jev_endpoint,
            self.settings.jev_key_file,
            {"model": model or self.settings.jev_model, "state": state, "questions": questions},
        )
        answers = data.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise AdvisorModelError("invalid_jev_output")
        for name, question in questions.items():
            answer = answers[name]
            if question["type"] == "noul":
                if answer.get("type") != "noul" or not _probability(answer.get("noul")):
                    raise AdvisorModelError("invalid_jev_output")
                continue
            probabilities = answer.get("probabilities", {})
            choices = set(question["criteria"])
            if (
                answer.get("type") != "choice"
                or set(probabilities) != choices
                or answer.get("choice") not in choices
                or not _probability(answer.get("confidence"))
                or not all(_probability(p) for p in probabilities.values())
                or abs(sum(probabilities.values()) - 1) > 0.02
                or probabilities[answer["choice"]] < max(probabilities.values()) - 0.02
            ):
                raise AdvisorModelError("invalid_jev_output")
        return {"model": data.get("model"), "answers": answers}


def _probability(value):
    return type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 1
