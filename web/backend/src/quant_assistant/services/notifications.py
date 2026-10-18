"""Read-only notification adapters for local operational workflows.

The application never logs in to a broker or submits an order.  This module
only sends explicitly formatted text to operator-provided notification
channels (Enterprise WeChat, Server酱, or PushPlus). Missing credentials are a
safe no-op and network failures are returned as a structured result so a
scheduler can report them.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from quant_assistant.config import Settings


class NotificationStatus(StrEnum):
    """Outcome of one notification attempt."""

    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """Safe, non-secret notification outcome."""

    channel: str
    status: NotificationStatus
    message: str
    http_status: int | None = None

    @property
    def delivered(self) -> bool:
        return self.status is NotificationStatus.SENT


class WebhookNotifier(Protocol):
    """Minimal notifier boundary used by CLI and scheduled jobs."""

    channel: str

    def send(self, message: str, *, title: str | None = None) -> NotificationResult: ...


class WeComWebhookNotifier:
    """Enterprise WeChat ``群机器人`` text webhook adapter.

    The webhook URL is supplied by the operator through an environment
    variable or an explicit constructor argument.  It is never included in a
    result, exception, or log message.
    """

    channel = "wecom"
    max_message_length = 4096

    def __init__(self, webhook_url: str, *, timeout: float = 8.0) -> None:
        if not isinstance(webhook_url, str) or not webhook_url.strip():
            raise ValueError("企业微信Webhook地址不能为空")
        parsed = urlparse(webhook_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("企业微信Webhook地址必须是HTTP(S) URL")
        if timeout <= 0 or timeout > 30:
            raise ValueError("通知超时时间必须在0到30秒之间")
        self._webhook_url = webhook_url.strip()
        self._timeout = float(timeout)

    def send(self, message: str, *, title: str | None = None) -> NotificationResult:
        text = _message_text(message, title=title)
        if len(text) > self.max_message_length:
            raise ValueError("企业微信通知正文不能超过4096个字符")
        payload = {
            "msgtype": "text",
            "text": {"content": text},
        }
        request = Request(
            self._webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-assistant/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                status_value = getattr(response, "status", None)
                if status_value is None:
                    status_value = response.getcode()
                status_code = int(status_value)
        except (HTTPError, URLError, TimeoutError, OSError, AttributeError, TypeError, ValueError):
            return NotificationResult(
                self.channel,
                NotificationStatus.FAILED,
                "企业微信通知网络请求失败",
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return NotificationResult(
                self.channel,
                NotificationStatus.FAILED,
                "企业微信通知响应格式无效",
                status_code,
            )
        if not isinstance(body, Mapping) or body.get("errcode") != 0:
            return NotificationResult(
                self.channel,
                NotificationStatus.FAILED,
                "企业微信通知被远端拒绝",
                status_code,
            )
        return NotificationResult(
            self.channel,
            NotificationStatus.SENT,
            "企业微信通知已发送",
            status_code,
        )


class ServerChanNotifier:
    """Server酱 Turbo/SC3 personal notification adapter."""

    channel = "serverchan"
    max_message_length = 20_000
    _sc3_pattern = re.compile(r"^sctp(?P<uid>\d+)t[A-Za-z0-9_-]+$")
    _turbo_pattern = re.compile(r"^SCT[A-Za-z0-9_-]+$")

    def __init__(self, sendkey: str, *, timeout: float = 8.0) -> None:
        key = str(sendkey or "").strip()
        sc3 = self._sc3_pattern.fullmatch(key)
        if sc3 is not None:
            self._endpoint = f"https://{sc3.group('uid')}.push.ft07.com/send/{key}.send"
        elif self._turbo_pattern.fullmatch(key) is not None:
            self._endpoint = f"https://sctapi.ftqq.com/{key}.send"
        else:
            raise ValueError("Server酱SendKey格式无效")
        if timeout <= 0 or timeout > 30:
            raise ValueError("通知超时时间必须在0到30秒之间")
        self._timeout = float(timeout)

    def send(self, message: str, *, title: str | None = None) -> NotificationResult:
        text = _message_text(message, title=None)
        if len(text) > self.max_message_length:
            raise ValueError("Server酱通知正文过长")
        payload = urlencode(
            {
                "title": _notification_title(title, text),
                "desp": text,
            }
        ).encode("utf-8")
        request = Request(
            self._endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": "quant-assistant/0.1",
            },
            method="POST",
        )
        body, status_code = _open_json(request, timeout=self._timeout)
        if body is None or body.get("code") != 0:
            return NotificationResult(
                self.channel,
                NotificationStatus.FAILED,
                "Server酱通知发送失败",
                status_code,
            )
        return NotificationResult(
            self.channel,
            NotificationStatus.SENT,
            "Server酱通知已发送",
            status_code,
        )


class PushPlusNotifier:
    """PushPlus personal WeChat notification adapter."""

    channel = "pushplus"
    endpoint = "https://www.pushplus.plus/send/"
    max_message_length = 20_000

    def __init__(self, token: str, *, channel: str = "wechat", timeout: float = 8.0) -> None:
        normalized = str(token or "").strip()
        if len(normalized) < 16 or not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
            raise ValueError("PushPlus Token格式无效")
        selected_channel = str(channel or "").strip().lower()
        if selected_channel not in {"wechat", "app", "mail", "extension"}:
            raise ValueError("PushPlus通知渠道不受支持")
        if timeout <= 0 or timeout > 30:
            raise ValueError("通知超时时间必须在0到30秒之间")
        self._token = normalized
        self._channel = selected_channel
        self._timeout = float(timeout)

    def send(self, message: str, *, title: str | None = None) -> NotificationResult:
        text = _message_text(message, title=None)
        if len(text) > self.max_message_length:
            raise ValueError("PushPlus通知正文过长")
        payload = {
            "token": self._token,
            "title": _notification_title(title, text),
            "content": text,
            "template": "txt",
            "channel": self._channel,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-assistant/0.1",
            },
            method="POST",
        )
        body, status_code = _open_json(request, timeout=self._timeout)
        if body is None or body.get("code") not in {0, 200}:
            return NotificationResult(
                self.channel,
                NotificationStatus.FAILED,
                "PushPlus通知发送失败",
                status_code,
            )
        return NotificationResult(
            self.channel,
            NotificationStatus.SENT,
            "PushPlus通知已发送",
            status_code,
        )


class WeChatSubscribeNotifier:
    """Official WeChat Mini Program subscription-message adapter.

    This is intentionally a single-user, server-side integration: the AppID,
    AppSecret, OpenID and template ID are supplied through environment
    variables and never sent to the Mini Program bundle.  ``data_template``
    may contain ``{title}`` and ``{content}`` placeholders; when omitted, a
    conservative two-field ``thing`` payload is used.
    """

    channel = "wechat_subscribe"
    token_endpoint = "https://api.weixin.qq.com/cgi-bin/token"
    send_endpoint = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"
    max_message_length = 20_000

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        openid: str,
        template_id: str,
        *,
        page: str = "pages/home/index",
        miniprogram_state: str = "formal",
        data_template: Mapping[str, object] | None = None,
        timeout: float = 8.0,
    ) -> None:
        values = {
            "AppID": app_id,
            "AppSecret": app_secret,
            "OpenID": openid,
            "模板ID": template_id,
        }
        for label, value in values.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"微信订阅{label}不能为空")
        if timeout <= 0 or timeout > 30:
            raise ValueError("通知超时时间必须在0到30秒之间")
        state = str(miniprogram_state or "formal").strip().lower()
        if state not in {"formal", "trial", "developer"}:
            raise ValueError("微信小程序环境必须是formal、trial或developer")
        selected_page = str(page or "").strip().lstrip("/")
        if selected_page and ("\n" in selected_page or "\r" in selected_page):
            raise ValueError("微信订阅跳转页面不能包含换行")
        if data_template is not None and not isinstance(data_template, Mapping):
            raise ValueError("微信订阅模板数据必须是JSON对象")
        self._app_id = app_id.strip()
        self._app_secret = app_secret.strip()
        self._openid = openid.strip()
        self._template_id = template_id.strip()
        self._page = selected_page
        self._miniprogram_state = state
        self._data_template = None if data_template is None else dict(data_template)
        self._timeout = float(timeout)
        self._cached_access_token: str | None = None
        self._access_token_expires_at = 0.0

    def send(self, message: str, *, title: str | None = None) -> NotificationResult:
        text = _message_text(message, title=None)
        if len(text) > self.max_message_length:
            raise ValueError("微信订阅通知正文过长")
        access_token, token_status = self._access_token()
        if not access_token:
            return NotificationResult(
                self.channel,
                NotificationStatus.FAILED,
                "微信订阅通知授权失败",
                token_status,
            )
        title_text = _notification_title(title, text)
        payload: dict[str, object] = {
            "touser": self._openid,
            "template_id": self._template_id,
            "miniprogram_state": self._miniprogram_state,
            "lang": "zh_CN",
            "data": _render_wechat_data(self._data_template, title_text, text),
        }
        if self._page:
            payload["page"] = self._page
        request = Request(
            f"{self.send_endpoint}?{urlencode({'access_token': access_token})}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-assistant/0.1",
            },
            method="POST",
        )
        body, status_code = _open_json(request, timeout=self._timeout)
        if body is None or body.get("errcode") != 0:
            return NotificationResult(
                self.channel,
                NotificationStatus.FAILED,
                "微信订阅通知被远端拒绝",
                status_code,
            )
        return NotificationResult(
            self.channel,
            NotificationStatus.SENT,
            "微信订阅通知已发送",
            status_code,
        )

    def _access_token(self) -> tuple[str | None, int | None]:
        if self._cached_access_token and time.monotonic() < self._access_token_expires_at:
            return self._cached_access_token, None
        query = urlencode(
            {
                "grant_type": "client_credential",
                "appid": self._app_id,
                "secret": self._app_secret,
            }
        )
        request = Request(
            f"{self.token_endpoint}?{query}",
            headers={"Accept": "application/json", "User-Agent": "quant-assistant/0.1"},
            method="GET",
        )
        body, status_code = _open_json(request, timeout=self._timeout)
        if body is None:
            return None, status_code
        token = body.get("access_token")
        if not isinstance(token, str) or not token.strip():
            return None, status_code
        try:
            expires_in = int(body.get("expires_in", 7200))
        except (TypeError, ValueError):
            expires_in = 7200
        self._cached_access_token = token.strip()
        self._access_token_expires_at = time.monotonic() + max(60, expires_in - 120)
        return self._cached_access_token, status_code


class FanoutNotifier:
    """Send one operator message to every explicitly configured channel."""

    def __init__(self, notifiers: tuple[WebhookNotifier, ...]) -> None:
        if not notifiers:
            raise ValueError("通知通道不能为空")
        self._notifiers = notifiers
        self.channel = "+".join(item.channel for item in notifiers)

    def send(self, message: str, *, title: str | None = None) -> NotificationResult:
        results = tuple(item.send(message, title=title) for item in self._notifiers)
        failed = sum(item.status is NotificationStatus.FAILED for item in results)
        sent = sum(item.status is NotificationStatus.SENT for item in results)
        status = NotificationStatus.FAILED if failed else NotificationStatus.SENT
        return NotificationResult(
            self.channel,
            status,
            f"通知通道发送完成：成功{sent}，失败{failed}",
        )


def notifier_from_env(*, timeout: float = 8.0) -> WebhookNotifier | None:
    """Build all explicitly configured notifiers without exposing credentials."""

    configured = Settings.from_env().notifications
    notifiers: list[WebhookNotifier] = []
    url = configured.wecom_webhook_url or ""
    if url:
        notifiers.append(WeComWebhookNotifier(url, timeout=timeout))
    sendkey = configured.serverchan_sendkey or ""
    if sendkey:
        notifiers.append(ServerChanNotifier(sendkey, timeout=timeout))
    pushplus_token = configured.pushplus_token or ""
    if pushplus_token:
        notifiers.append(
            PushPlusNotifier(
                pushplus_token,
                channel=configured.pushplus_channel,
                timeout=timeout,
            )
        )
    wechat_values = {
        "app_id": configured.wechat_app_id or "",
        "app_secret": configured.wechat_app_secret or "",
        "openid": configured.wechat_openid or "",
        "template_id": configured.wechat_template_id or "",
    }
    if any(wechat_values.values()):
        if not all(wechat_values.values()):
            raise ValueError("微信订阅通知需要同时配置AppID、AppSecret、OpenID和模板ID")
        data_template = _load_wechat_data_template(configured.wechat_data_json)
        notifiers.append(
            WeChatSubscribeNotifier(
                **wechat_values,
                page=configured.wechat_page,
                miniprogram_state=configured.wechat_miniprogram_state,
                data_template=data_template,
                timeout=timeout,
            )
        )
    if not notifiers:
        return None
    if len(notifiers) == 1:
        return notifiers[0]
    return FanoutNotifier(tuple(notifiers))


def send_optional_notification(
    notifier: WebhookNotifier | None,
    message: str,
    *,
    title: str | None = None,
) -> NotificationResult:
    """Send when configured, otherwise return an explicit skipped result."""

    if notifier is None:
        return NotificationResult("operator", NotificationStatus.SKIPPED, "未配置通知通道")
    return notifier.send(message, title=title)


def _open_json(
    request: Request, *, timeout: float
) -> tuple[Mapping[str, object] | None, int | None]:
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status_value = getattr(response, "status", None)
            if status_value is None:
                status_value = response.getcode()
            status_code = int(status_value)
    except (HTTPError, URLError, TimeoutError, OSError, AttributeError, TypeError, ValueError):
        return None, None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, status_code
    return (body if isinstance(body, Mapping) else None), status_code


def _notification_title(title: str | None, message: str) -> str:
    value = str(title or message.splitlines()[0] or "量化助手通知").strip().replace("\n", " ")
    return value[:32]


def _load_wechat_data_template(value: str) -> dict[str, object] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("微信订阅模板数据必须是有效JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("微信订阅模板数据必须是JSON对象")
    return dict(parsed)


def _render_wechat_data(
    template: Mapping[str, object] | None,
    title: str,
    content: str,
) -> dict[str, object]:
    selected: Mapping[str, object] = template or {
        "thing1": {"value": title[:20]},
        "thing2": {"value": " ".join(content.split())[:20]},
    }
    rendered = _render_wechat_value(selected, title, content)
    return dict(rendered) if isinstance(rendered, Mapping) else {}


def _render_wechat_value(value: object, title: str, content: str) -> object:
    if isinstance(value, str):
        return value.replace("{title}", title).replace("{content}", content)
    if isinstance(value, Mapping):
        return {str(key): _render_wechat_value(item, title, content) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_wechat_value(item, title, content) for item in value]
    return value


def format_daily_order_digest(decision: object, *, title: str = "量化助手每日清单") -> str:
    """Render a compact, manual-confirmation-only daily order message."""

    as_of = _field(decision, "as_of_date", "—")
    status = _enum_text(_field(decision, "status", "—"))
    readiness = _enum_text(_field(decision, "readiness", "—"))
    regime = _enum_text(_field(decision, "market_regime", "—"))
    run_id = str(_field(decision, "run_id", "—"))
    lines = [
        title,
        f"日期：{as_of}  数据：{status}  模式：{readiness}  风险：{regime}",
        f"运行：{run_id}",
    ]
    blockers = tuple(_field(decision, "blockers", ()) or ())
    orders = tuple(_field(decision, "orders", ()) or ())
    actionable = [
        order
        for order in orders
        if _enum_text(_field(order, "action", "")).lower() in {"buy", "add", "reduce", "sell"}
    ]
    if actionable:
        lines.append("人工复核清单：")
        for order in actionable[:30]:
            action = _enum_text(_field(order, "action", "")).upper()
            symbol = str(_field(order, "symbol", "—"))
            amount = _decimal_text(_field(order, "amount", None))
            lines.append(f"- {action} {symbol} ¥{amount}")
        if len(actionable) > 30:
            lines.append(f"- 其余{len(actionable) - 30}项请在本地仪表盘查看")
    else:
        lines.append("人工复核清单：无新增/减少建议")
    if blockers:
        lines.append("阻断：" + "；".join(str(item) for item in blockers[:5]))
    lines.append("仅生成建议；请在官方客户端人工核对后操作。")
    return "\n".join(lines)


def format_valuation_alert_digest(alerts: Iterable[object]) -> str:
    """Render tail valuation anomalies without claiming an official NAV."""

    items = tuple(alerts)
    lines = ["量化助手 基金估值/净值异动预警"]
    if not items:
        return "\n".join((*lines, "当前持仓未发现超过阈值的基金估值/净值异动。"))
    for alert in items:
        symbol = str(_field(alert, "symbol", "—"))
        rate = _decimal_text(_field(alert, "change_rate", None), suffix="%")
        severity = _enum_text(_field(alert, "severity", "warning")).upper()
        observed = str(_field(alert, "observed_at", "—"))
        lines.append(f"- [{severity}] {symbol} 估值变动 {rate}（{observed}）")
    lines.append("结果仅供参考，最终净值以基金公告/官方渠道为准。")
    return "\n".join(lines)


def _message_text(message: str, *, title: str | None) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("通知正文不能为空")
    normalized = message.strip()
    if title and title.strip() and not normalized.startswith(title.strip()):
        return f"{title.strip()}\n{normalized}"
    return normalized


def _field(item: object, name: str, default: object) -> object:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_text(value: object) -> str:
    return str(getattr(value, "value", value))


def _decimal_text(value: object, *, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    if not number.is_finite():
        return "—"
    return f"{number:.2f}{suffix}"


__all__ = [
    "NotificationResult",
    "NotificationStatus",
    "FanoutNotifier",
    "PushPlusNotifier",
    "ServerChanNotifier",
    "WeChatSubscribeNotifier",
    "WebhookNotifier",
    "WeComWebhookNotifier",
    "format_daily_order_digest",
    "format_valuation_alert_digest",
    "notifier_from_env",
    "send_optional_notification",
]
