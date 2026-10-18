"""P0 interface contract baseline: dump JSON Schema of Pydantic request/response models.

Read-only introspection; no product code modified, no DB/network access.
Run with the project venv: .venv/Scripts/python.exe tools/dump_schema_contract.py
Output: schema-contract.json (alongside this file).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import quant_assistant.controllers.schemas as schemas
import quant_assistant.controllers.candidate_schemas as candidate
import quant_assistant.controllers.schema_common as schema_common

BASELINE_COMMIT = "29568a8a0a8aa9def7241d5d199bdf5d8a111adc"


def collect(module, out: dict, label: str) -> None:
    for name, obj in sorted(vars(module).items()):
        if isinstance(obj, type) and hasattr(obj, "model_json_schema"):
            # 仅收 ApiModel / BaseModel 派生的契约模型
            try:
                out[f"{label}.{name}"] = obj.model_json_schema()
            except Exception as e:  # noqa: BLE001
                out[f"{label}.{name}"] = {"error": str(e)}


def main() -> None:
    contract: dict = {}
    collect(schemas, contract, "schemas")
    collect(candidate, contract, "candidate_schemas")
    collect(schema_common, contract, "schema_common")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": BASELINE_COMMIT,
        "source": [
            "web/backend/src/quant_assistant/controllers/schemas.py",
            "web/backend/src/quant_assistant/controllers/candidate_schemas.py",
            "web/backend/src/quant_assistant/controllers/schema_common.py",
        ],
        "model_count": len(contract),
        "models": contract,
    }
    out = Path(__file__).resolve().parent.parent / "schema-contract.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"models: {len(contract)} -> {out}")


if __name__ == "__main__":
    main()
