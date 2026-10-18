"""Bounded photo review sessions and explicit, idempotent initial imports."""

from datetime import date
from decimal import Decimal, InvalidOperation
from threading import RLock
from time import time
from uuid import uuid4

from quant_assistant.models.entities import AssetType
from quant_assistant.providers.ocr import MAX_IMAGE_BYTES
from quant_assistant.services.photo_holdings_snapshot import PhotoHoldingsMixin
from quant_assistant.services.photo_review import PhotoReviewMixin
from quant_assistant.services.portfolio_commands import InstrumentInput, PositionInput


def _positive(value, *, allow_zero=False):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("金额、份额和成本必须为有效数字") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ValueError("现金必须为非负数" if allow_zero else "金额、份额和成本必须大于0")
    return number


class PhotoUploadService(PhotoReviewMixin, PhotoHoldingsMixin):
    def __init__(self, recognizer, commands, *, clock=time, database=None):
        self.database = database
        self.recognizer = recognizer
        self.commands = commands
        self.clock = clock
        self.sessions = {}
        self.lock = RLock()

    def _expire(self):
        self.sessions = {
            key: value for key, value in self.sessions.items() if value["expires"] > self.clock()
        }

    def preview(self, image: bytes, as_of_date: date | None, *, holdings_only: bool = False):
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise ValueError("图片不能为空且不得超过6MiB")
        if not (
            image.startswith(b"\x89PNG\r\n\x1a\n")
            or image.startswith(b"\xff\xd8\xff")
            or (image.startswith(b"RIFF") and image[8:12] == b"WEBP")
        ):
            raise ValueError("仅支持PNG、JPEG或WebP图片")
        if as_of_date is not None and as_of_date > date.today():
            raise ValueError("截图日期不能晚于今天")
        if as_of_date is None and not holdings_only:
            raise ValueError("完整账户导入需要指定截图日期")
        options = {"holdings_only": True} if holdings_only else {}
        preview = self.recognizer.preview(
            image, filename="account-image", as_of_date=as_of_date, **options
        )
        positions = [p for p in preview.positions if p.asset_type is not AssetType.CASH]
        if not positions or any(p.asset_type is not AssetType.FUND for p in positions):
            raise ValueError("本入口仅支持完整基金账户截图；股票账户请使用账户导入工具")
        return self._store_preview(preview)

    def _store_preview(self, preview):
        positions = [p for p in preview.positions if p.asset_type is not AssetType.CASH]
        names = {p.symbol: p.name for p in preview.instrument_metadata}
        missing = {p.symbol for p in positions}.difference(names)
        if missing and self.database is not None:
            with self.database.connection() as connection:
                placeholders = ",".join("?" for _ in missing)
                names.update(
                    connection.execute(
                        "SELECT symbol, name FROM instruments WHERE asset_type='fund' "
                        f"AND symbol IN ({placeholders})",
                        sorted(missing),
                    ).fetchall()
                )
        rows = [
            {
                "symbol": p.symbol,
                "name": names.get(p.symbol, p.symbol),
                "quantity": str(p.quantity),
                "average_cost": str(p.average_cost),
                "market_value": str(p.market_value),
            }
            for p in positions
        ]
        inferences = {item.symbol: item for item in preview.inferences}
        for row in rows:
            inference = inferences.get(row["symbol"])
            if inference is not None:
                row.update(
                    cost_amount=str(inference.cost_basis),
                    holding_profit=str(inference.holding_profit),
                    nav_date=inference.nav_date.isoformat(),
                    unit_nav=str(inference.unit_nav),
                )
        token = uuid4().hex
        with self.lock:
            self._expire()
            if len(self.sessions) >= 20:
                raise ValueError("待核对图片过多，请稍后重试")
            self.sessions[token] = {
                "preview": preview,
                "expires": self.clock() + 900,
                "result": None,
                "signature": None,
            }
        cash = next(
            (str(p.market_value) for p in preview.positions if p.asset_type is AssetType.CASH), ""
        )
        return {
            "preview_id": token,
            "as_of_date": preview.as_of_date.isoformat(),
            "positions": rows,
            "cash": cash,
            "warnings": list(preview.warnings),
            "provider": preview.provider,
            "inferred": bool(preview.inferences),
            "requires_cash": preview.requires_cash,
            "expires_in_seconds": 900,
        }

    def confirm(self, preview_id, rows, cash, confirmed):
        if confirmed is not True:
            raise ValueError("请先核对完整基金持仓和现金并确认")
        with self.lock:
            self._expire()
            session = self.sessions.get(preview_id)
            if session is None:
                raise ValueError("识别预览已过期或服务已重启，请重新识别")
            if "preview" not in session:
                raise ValueError("请使用图片预览凭证，不能使用保存核对凭证")
            preview = session["preview"]
            expected = {p.symbol for p in preview.positions if p.asset_type is AssetType.FUND}
            if len(rows) != len(expected) or {row["symbol"] for row in rows} != expected:
                raise ValueError("基金代码或行数与识别结果不一致，请重新识别")
            positions = tuple(
                PositionInput(
                    row["symbol"],
                    AssetType.FUND,
                    _positive(row["quantity"]),
                    _positive(row["average_cost"]),
                    _positive(row["market_value"]),
                    preview.as_of_date,
                )
                for row in sorted(rows, key=lambda row: row["symbol"])
            )
            amount = _positive(cash, allow_zero=True)
            positions += (
                PositionInput(
                    "CASH.CNY", AssetType.CASH, amount, Decimal("1"), amount, preview.as_of_date
                ),
            )
            if session["result"] is not None:
                if positions != session["signature"]:
                    raise ValueError("本次预览已导入，不能更改后再次确认")
                return session["result"]
            event_id = f"photo-import:{preview.image_sha256[:16]}:{preview.as_of_date.isoformat()}"
            result = self.commands.import_initial_account(
                event_id,
                preview.as_of_date,
                positions,
                reason="用户已核对基金截图、完整持仓及现金，确认首次建档",
                instrument_metadata=tuple(
                    item for item in preview.instrument_metadata if item.symbol != "CASH.CNY"
                )
                + (
                    InstrumentInput(
                        "CASH.CNY", AssetType.CASH, "人民币现金", None, preview.as_of_date
                    ),
                ),
            )
            session["signature"] = positions
            session["result"] = {"event_id": result, "status": "imported"}
            return session["result"]
