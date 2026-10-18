"""Private per-user Jev credential, independent of the compatible model provider."""

import json

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from quant_assistant.services.advisor_model_settings import AdvisorSettingsConflict
from quant_assistant.services.advisor_provider import AdvisorProvider, ProviderDiscoveryError


class JevInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1, max_length=4096)
    revision: int = Field(ge=0, strict=True)


class JevConnection(AdvisorProvider):
    def __init__(self, directory):
        super().__init__(directory)
        self.config_file = self.directory / "advisor-jev.json"

    def load(self):
        if not self.config_file.is_file():
            return {"revision": 0}
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    @property
    def key_file(self):
        return self.directory / self.load().get("credential_file", "advisor-jev.key")

    def public(self, *, key_file=None):
        return {
            "revision": self.load()["revision"],
            "has_key": self.key_file.is_file() or bool(key_file and key_file.is_file()),
        }

    def save(self, body):
        from uuid import uuid4

        body = JevInput.model_validate(body)
        current = self.load()
        if body.revision != current["revision"]:
            raise AdvisorSettingsConflict("Jev连接已变化，请重新读取后修改")
        key = body.api_key.get_secret_value().strip()
        if not key or any(char.isspace() for char in key):
            raise ProviderDiscoveryError("请提供有效Jev API Key")
        data = {
            "revision": current["revision"] + 1,
            "credential_file": "advisor-jev-" + uuid4().hex + ".key",
        }
        self.write_private(data, key)
