"""User-owned compatible model catalog; private credentials never enter public results."""

import getpass
import ipaddress
import json
import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from quant_assistant.services.advisor_model_settings import (
    AdvisorModelRoles,
    AdvisorSettingsConflict,
)


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=8, max_length=500)
    api_key: SecretStr = Field(default=SecretStr(""))
    revision: int = Field(ge=0, strict=True)


class ProviderDiscoveryError(ValueError):
    """Only locally generated, secret-free messages may cross the API boundary."""


def _resolve(host):
    return [item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


class AdvisorProvider:
    def __init__(self, directory, *, get=requests.get, resolve=_resolve):
        self.directory = Path(directory)
        self.config_file = self.directory / "advisor-provider.json"
        self.get, self.resolve = get, resolve

    @property
    def key_file(self):
        return self.directory / self.load().get("credential_file", "advisor-byok.key")

    def load(self):
        if not self.config_file.is_file():
            return {"endpoint": "", "models": [], "revision": 0}
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    def public(self, *, endpoint="", key_file=None):
        data = self.load()
        return {
            **{k: v for k, v in data.items() if k != "credential_file"},
            "endpoint": data["endpoint"] or endpoint,
            "has_key": self.key_file.is_file() or bool(key_file and key_file.is_file()),
        }

    def discover(self, body, *, fallback_key=None):
        body = ProviderInput.model_validate(body)
        current = self.load()
        if current["revision"] != body.revision:
            raise AdvisorSettingsConflict("服务连接已变化，请重新读取后再修改")
        endpoint = body.endpoint.strip().rstrip("/")
        url = urlsplit(endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.port not in (None, 443)
            or url.hostname.lower() == "localhost"
        ):
            raise ProviderDiscoveryError("请输入公开HTTPS兼容API地址，不包含密钥或查询参数")
        try:
            addresses = self.resolve(url.hostname)
            if not addresses or any(
                not ipaddress.ip_address(value).is_global for value in addresses
            ):
                raise ValueError
        except (ValueError, OSError):
            raise ProviderDiscoveryError("API地址不可用或指向非公开网络") from None
        key = body.api_key.get_secret_value().strip()
        if not key:
            path = self.key_file if self.key_file.is_file() else fallback_key
            if endpoint != (current["endpoint"] or endpoint) or not path or not path.is_file():
                raise ProviderDiscoveryError("更换API地址时请提供该服务的API Key")
            key = path.read_text(encoding="utf-8").strip()
        if not key or len(key) > 4096 or any(char.isspace() for char in key):
            raise ProviderDiscoveryError("请提供有效API Key")
        try:
            response = self.get(
                endpoint + "/models",
                headers={"Authorization": "Bearer " + key},
                timeout=(5, 15),
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise ProviderDiscoveryError(
                    "API Key或模型列表权限无效"
                    if response.status_code in (401, 403)
                    else "模型列表暂不可用，请稍后手动重试"
                )
            if len(response.content) > 500000 or key.encode() in response.content:
                raise ProviderDiscoveryError("模型列表响应异常")
            rows = json.loads(response.content).get("data")
            if not isinstance(rows, list) or len(rows) > 2000:
                raise ProviderDiscoveryError("服务未返回兼容模型列表")
            models = set()
            for row in rows:
                model = row.get("id") if isinstance(row, dict) else None
                try:
                    AdvisorModelRoles(news=model, strategy=model, reviewer=model, decision=model)
                    models.add(model)
                except ValueError:
                    continue
            if not models:
                raise ProviderDiscoveryError("没有可选择的模型，请核对Key权限")
        except requests.RequestException:
            raise ProviderDiscoveryError("读取模型列表超时或连接失败，没有更改服务配置") from None
        except (AttributeError, json.JSONDecodeError):
            raise ProviderDiscoveryError("服务未返回兼容模型列表") from None
        data = {
            "endpoint": endpoint,
            "models": sorted(models),
            "revision": current["revision"] + 1,
            "credential_file": "advisor-" + uuid4().hex + ".key",
        }
        self.write_private(data, key)
        return self.public()

    def write_private(self, data, key):
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            subprocess.run(
                [
                    "icacls",
                    str(self.directory),
                    "/inheritance:r",
                    "/grant:r",
                    getpass.getuser() + ":(OI)(CI)F",
                    "SYSTEM:(OI)(CI)F",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.chmod(self.directory, 0o700)
        # Each replacement is atomic; no secret is stored in the account database.
        for path, text in (
            (self.directory / data["credential_file"], key),
            (self.config_file, json.dumps(data)),
        ):
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
