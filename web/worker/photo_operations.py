"""OCR and instrument identification only. C# owns confirmations and account writes."""

import base64
import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from analysis_context import analytical_context, shanghai_now

from quant_assistant.models.entities import AssetType
from quant_assistant.services.photo_import import PhotoAccountImportService
from quant_assistant.services.photo_upload import PhotoUploadService
from quant_assistant.services.portfolio_commands import PositionInput

PHOTO_CAPABILITIES = ("photo.recognize", "photo.identify")


def execute_photo(kind, payload):
    with analytical_context(payload["snapshot"]) as (database, _queries, _directory):
        recognizer = PhotoAccountImportService.from_env(database)
        day = date.fromisoformat(payload["as_of_date"]) if payload.get("as_of_date") else None
        if kind == "photo.recognize":
            service = PhotoUploadService(recognizer, None, database=database)
            result = service.preview(
                base64.b64decode(payload["image_base64"], validate=True),
                day,
                holdings_only=payload["holdings_only"],
            )
            preview = service.sessions[result["preview_id"]]["preview"]
            result["image_sha256"] = preview.image_sha256
            return result
        rows = tuple(
            PositionInput(
                row["symbol"],
                AssetType.FUND,
                Decimal(row["quantity"]),
                Decimal(row["average_cost"]),
                Decimal(row["market_value"]),
                day,
            )
            for row in payload["positions"]
        )
        result = recognizer.resolve_review_instruments(rows, day)
        return json.loads(json.dumps([asdict(row) for row in result], default=str))
