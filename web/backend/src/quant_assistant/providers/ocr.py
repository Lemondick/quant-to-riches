"""External OCR providers used by the photo-import workflow.

The provider boundary deliberately keeps credentials and image bytes outside the
domain layer.  A production process reads credentials from its environment and
only returns normalized text; it never persists the uploaded image.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quant_assistant.config import Settings

MAX_IMAGE_BYTES = 6 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_PADDLE_OCR_ENDPOINT = "https://paddleocr.xzrcka.me/ocr"
DEFAULT_PADDLE_OCR_TIMEOUT_SECONDS = 45.0
CLIENT_USER_AGENT = "quant-assistant/0.1"
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_GENERAL_BASIC_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"RIFF",
)
_DATE_PATTERNS = (
    re.compile(
        r"(?<!\d)(?P<year>20\d{2})[-/.年](?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?(?!\d)"
    ),
    re.compile(r"(?<!\d)(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日?(?!\d)"),
)


class OcrError(RuntimeError):
    """Base error for the external OCR boundary."""


class OcrNotConfigured(OcrError):
    """No supported OCR provider credentials are configured."""


class OcrUnavailable(OcrError):
    """The configured OCR provider could not be reached or authenticated."""


class OcrProvider(Protocol):
    name: str

    def recognize(self, image: bytes, *, filename: str = "upload") -> OcrResult: ...


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Provider-neutral OCR response with an image fingerprint."""

    provider: str
    text: str
    lines: tuple[str, ...]
    image_sha256: str
    detected_dates: tuple[date, ...] = ()


class DisabledOcrProvider:
    """Explicit fail-closed provider for installations without OCR credentials."""

    name = "disabled"

    def recognize(self, image: bytes, *, filename: str = "upload") -> OcrResult:
        del image, filename
        raise OcrNotConfigured(
            "未配置外部OCR服务；请在服务端设置 QUANT_OCR_PROVIDER，或显式启用 PaddleOCR"
        )


class PaddleOcrProvider:
    """PaddleOCR serving adapter for the deployed JSON ``/ocr`` endpoint."""

    name = "paddleocr"

    def __init__(
        self,
        endpoint: str = DEFAULT_PADDLE_OCR_ENDPOINT,
        *,
        timeout: float = DEFAULT_PADDLE_OCR_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = _paddle_endpoint(endpoint)
        if timeout <= 0 or timeout > 60:
            raise ValueError("OCR timeout must be between 0 and 60 seconds")
        self._timeout = float(timeout)

    @classmethod
    def from_env(cls) -> PaddleOcrProvider:
        configured = Settings.from_env().ocr
        endpoint = configured.paddle_endpoint or DEFAULT_PADDLE_OCR_ENDPOINT
        return cls(endpoint, timeout=configured.timeout_seconds)

    def recognize(self, image: bytes, *, filename: str = "upload") -> OcrResult:
        _validate_image(image, filename)
        payload = {
            "file": base64.b64encode(image).decode("ascii"),
            # PaddleX serving uses 0 for PDF and 1 for image input.
            "fileType": 1,
            "returnWordBox": False,
            "visualize": False,
        }
        try:
            response = _post_json(self._endpoint, payload, timeout=self._timeout)
        except OcrUnavailable:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
            raise OcrUnavailable("外部PaddleOCR服务暂不可用") from exc

        error_code = response.get("errorCode")
        error_message = response.get("errorMsg")
        if error_code not in (None, 0) or error_message not in (None, "", "Success"):
            raise OcrUnavailable("PaddleOCR返回错误")
        lines = _paddle_lines(response)
        return OcrResult(
            provider=self.name,
            text="\n".join(lines),
            lines=lines,
            image_sha256=sha256(image).hexdigest(),
            detected_dates=extract_detected_dates(lines),
        )


class BaiduOcrProvider:
    """Baidu general-basic OCR adapter using server-side API credentials."""

    name = "baidu"

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        token_url: str = BAIDU_TOKEN_URL,
        ocr_url: str = BAIDU_GENERAL_BASIC_URL,
    ) -> None:
        self._api_key = _secret(api_key, "BAIDU_OCR_API_KEY")
        self._secret_key = _secret(secret_key, "BAIDU_OCR_SECRET_KEY")
        if timeout <= 0 or timeout > 60:
            raise ValueError("OCR timeout must be between 0 and 60 seconds")
        self._timeout = float(timeout)
        self._token_url = token_url
        self._ocr_url = ocr_url

    @classmethod
    def from_env(cls) -> BaiduOcrProvider:
        configured = Settings.from_env().ocr
        api_key = configured.baidu_api_key or ""
        secret_key = configured.baidu_secret_key or ""
        timeout = configured.timeout_seconds
        return cls(api_key, secret_key, timeout=timeout)

    def recognize(self, image: bytes, *, filename: str = "upload") -> OcrResult:
        _validate_image(image, filename)
        try:
            token_payload = _post_form(
                self._token_url,
                {
                    "grant_type": "client_credentials",
                    "client_id": self._api_key,
                    "client_secret": self._secret_key,
                },
                timeout=self._timeout,
            )
            access_token = str(token_payload.get("access_token", "")).strip()
            if not access_token:
                raise OcrUnavailable("OCR鉴权失败")
            payload = _post_form(
                f"{self._ocr_url}?{urlencode({'access_token': access_token})}",
                {
                    "image": base64.b64encode(image).decode("ascii"),
                    "detect_direction": "true",
                    "detect_language": "true",
                },
                timeout=self._timeout,
            )
        except OcrUnavailable:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
            raise OcrUnavailable("外部OCR服务暂不可用") from exc

        rows = payload.get("words_result")
        if not isinstance(rows, list):
            raise OcrUnavailable("OCR返回格式无效")
        lines = tuple(
            str(item.get("words", "")).strip()
            for item in rows
            if isinstance(item, dict) and str(item.get("words", "")).strip()
        )
        if not lines:
            raise OcrUnavailable("OCR未识别到可用文字")
        return OcrResult(
            provider=self.name,
            text="\n".join(lines),
            lines=lines,
            image_sha256=sha256(image).hexdigest(),
            detected_dates=extract_detected_dates(lines),
        )


def create_ocr_provider_from_env() -> OcrProvider:
    """Select a configured provider without ever falling back to fake OCR."""

    settings = Settings.from_env()
    configured = settings.ocr.provider
    if configured in {"disabled", "none", "off"}:
        return DisabledOcrProvider()
    if configured in {"paddle", "paddleocr", "paddle-ocr"}:
        return PaddleOcrProvider.from_env()
    if configured == "baidu":
        return BaiduOcrProvider.from_env()
    raise OcrNotConfigured(f"不支持的OCR服务：{configured}")


def _post_form(url: str, fields: dict[str, str], *, timeout: float) -> dict[str, object]:
    request = Request(
        url,
        data=urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("OCR response must be an object")
    if decoded.get("error_code") or decoded.get("error_msg"):
        raise OcrUnavailable("外部OCR返回错误")
    return decoded


def _post_json(url: str, payload: dict[str, object], *, timeout: float) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": CLIENT_USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("OCR response must be an object")
    return decoded


def _paddle_endpoint(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OcrNotConfigured("缺少PADDLE_OCR_ENDPOINT")
    endpoint = value.strip().rstrip("/")
    if not endpoint.lower().startswith(("http://", "https://")):
        raise OcrNotConfigured("PADDLE_OCR_ENDPOINT必须是HTTP(S)地址")
    if not endpoint.lower().endswith("/ocr"):
        endpoint = f"{endpoint}/ocr"
    return endpoint


def _paddle_lines(payload: dict[str, object]) -> tuple[str, ...]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise OcrUnavailable("PaddleOCR返回格式无效")
    rows = result.get("ocrResults")
    if not isinstance(rows, list):
        raise OcrUnavailable("PaddleOCR返回格式无效")
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pruned = row.get("prunedResult")
        if not isinstance(pruned, dict):
            continue
        texts = pruned.get("rec_texts")
        if isinstance(texts, str):
            values = (texts,)
        elif isinstance(texts, (list, tuple)):
            values = texts
        else:
            values = ()
        lines.extend(value.strip() for value in values if isinstance(value, str) and value.strip())
    if not lines:
        raise OcrUnavailable("PaddleOCR未识别到可用文字")
    return tuple(lines)


def extract_detected_dates(lines: object) -> tuple[date, ...]:
    """Extract explicit four-digit dates from OCR text in a deterministic way.

    Only dates that include a year are returned.  This avoids guessing the year
    from a screenshot captured around New Year and lets the UI ask the user to
    confirm when multiple dates (for example, trade date and settlement date)
    are visible.
    """

    if isinstance(lines, str):
        values = (lines,)
    elif isinstance(lines, (list, tuple)):
        values = tuple(item for item in lines if isinstance(item, str))
    else:
        return ()
    found: set[date] = set()
    for line in values:
        for pattern in _DATE_PATTERNS:
            for match in pattern.finditer(line):
                try:
                    found.add(
                        date(
                            int(match.group("year")),
                            int(match.group("month")),
                            int(match.group("day")),
                        )
                    )
                except ValueError:
                    continue
    return tuple(sorted(found))


def _validate_image(image: bytes, filename: str) -> None:
    if not isinstance(image, bytes) or not image:
        raise ValueError("上传图片不能为空")
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError("上传图片不能超过6MB")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("图片文件名不能为空")
    if not any(image.startswith(signature) for signature in _IMAGE_SIGNATURES):
        raise ValueError("仅支持PNG、JPEG或WEBP图片")
    if image.startswith(b"RIFF") and image[8:12] != b"WEBP":
        raise ValueError("仅支持PNG、JPEG或WEBP图片")


def _secret(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OcrNotConfigured(f"缺少{name}")
    return value.strip()


__all__ = [
    "BaiduOcrProvider",
    "DisabledOcrProvider",
    "PaddleOcrProvider",
    "OcrError",
    "OcrNotConfigured",
    "OcrProvider",
    "OcrResult",
    "OcrUnavailable",
    "extract_detected_dates",
    "DEFAULT_PADDLE_OCR_ENDPOINT",
    "create_ocr_provider_from_env",
]
