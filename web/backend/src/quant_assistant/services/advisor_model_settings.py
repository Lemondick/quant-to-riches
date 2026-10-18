"""Persist public model IDs only; keys, provider origins and risk limits stay server-side."""

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ModelId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")]


class AdvisorModelRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    news: ModelId
    strategy: ModelId
    reviewer: ModelId
    decision: ModelId

    @field_validator("news", "strategy", "reviewer", "decision")
    @classmethod
    def reject_key_or_url(cls, value):
        if "://" in value or value.lower().startswith(("sk-", "apikey_")):
            raise ValueError("填写模型ID，不是密钥或API地址")
        return value


class SaveAdvisorModels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: AdvisorModelRoles
    revision: int = Field(ge=0, strict=True)


class AdvisorSettingsConflict(ValueError):
    """A stale browser cannot overwrite another model selection."""


def load_model_selection(database, defaults):
    rows = database.query_df("SELECT models_json, revision FROM advisor_model_settings WHERE id=1")
    if rows.empty:
        return dict(defaults), 0
    row = rows.iloc[0]
    models = AdvisorModelRoles.model_validate(json.loads(row["models_json"])).model_dump()
    return models, int(row["revision"])
