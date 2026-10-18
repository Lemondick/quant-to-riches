"""Merge screenshots and freeze a verified full-account reconciliation for review."""

import hashlib
import json
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from quant_assistant.models.entities import AssetType
from quant_assistant.services.photo_import import PhotoImportPreview
from quant_assistant.services.portfolio_commands import (
    InstrumentInput,
    PortfolioCalibrationInput,
    PositionInput,
)


def _state(connection):
    positions = connection.execute(
        "SELECT symbol, asset_type, quantity, average_cost, market_value, as_of_date "
        "FROM positions ORDER BY symbol"
    ).fetchall()
    events = connection.execute("SELECT * FROM portfolio_events ORDER BY event_id").fetchall()
    valuations = connection.execute(
        "SELECT as_of_date, total_value FROM portfolio_valuations ORDER BY as_of_date, run_id"
    ).fetchall()
    transactions = connection.execute("SELECT * FROM transactions ORDER BY id").fetchall()
    fingerprint = hashlib.sha256(
        json.dumps([positions, events, valuations, transactions], default=str).encode()
    ).hexdigest()
    return positions, fingerprint, valuations


class PhotoReviewMixin:
    def _session(self, token):
        self._expire()
        session = self.sessions.get(token)
        if session is None:
            raise ValueError("预览已过期，请重新核对")
        return session

    def _preview_session(self, token):
        session = self._session(token)
        if "preview" not in session:
            raise ValueError("请使用图片或人工录入预览，不能使用确认凭证")
        return session["preview"]

    def manual(self, as_of_date):
        if as_of_date > date.today():
            raise ValueError("日期不能晚于今天")
        return self._store_preview(
            PhotoImportPreview(
                as_of_date,
                "人工录入",
                uuid4().hex,
                (),
                (),
                (),
                ("请录入同一账户的全部基金与现金；基金代码将在差异预览时核验。",),
                1.0,
                True,
            )
        )

    def merge(self, tokens):
        if not 1 <= len(tokens) <= 6:
            raise ValueError("每次合并1至6张截图")
        with self.lock:
            previews = [self._preview_session(token) for token in tokens]
            if len({p.as_of_date for p in previews}) != 1:
                raise ValueError("不同日期截图不能合并")
            positions, metadata, warnings = {}, {}, []
            for preview in previews:
                warnings.extend(preview.warnings)
                metadata.update({p.symbol: p for p in preview.instrument_metadata})
                for row in preview.positions:
                    old = positions.get(row.symbol)
                    if old is not None and old != row:
                        warnings.append(
                            f"{row.symbol}多图数值冲突，暂保留第一张数值，必须人工核对修改。"
                        )
                    elif old is not None:
                        warnings.append(f"{row.symbol}重复行已去重，不累加份额或金额。")
                    positions.setdefault(row.symbol, row)
            merged = replace(
                previews[0],
                positions=tuple(positions.values()),
                instrument_metadata=tuple(metadata.values()),
                warnings=tuple(dict.fromkeys(warnings)),
                image_sha256=hashlib.sha256(
                    "|".join(sorted(p.image_sha256 for p in previews)).encode()
                ).hexdigest(),
                inferences=tuple(i for p in previews for i in p.inferences),
            )
            return self._store_preview(merged)

    def review(self, token, rows, cash, mode, external_flow):
        from quant_assistant.services.photo_upload import _positive

        if self.database is None:
            raise ValueError("账户审核服务未配置")
        if mode not in {"initial", "calibration"}:
            raise ValueError("导入模式无效")
        with self.lock:
            preview = self._preview_session(token)
            codes = [row["symbol"].strip() for row in rows]
            if not 1 <= len(codes) <= 100 or len(set(codes)) != len(codes):
                raise ValueError("基金不能为空或重复，请先合并重复行")
            if any(not re.fullmatch(r"[0-9]{6}", code) for code in codes):
                raise ValueError("基金代码必须为6位数字")
            positions = tuple(
                PositionInput(
                    code,
                    AssetType.FUND,
                    _positive(row["quantity"]).normalize(),
                    _positive(row["average_cost"]).normalize(),
                    _positive(row["market_value"]).normalize(),
                    preview.as_of_date,
                )
                for code, row in zip(codes, rows, strict=True)
            )
            metadata = self.recognizer.resolve_review_instruments(positions, preview.as_of_date)
            amount = _positive(cash, allow_zero=True).normalize()
            positions += (
                PositionInput(
                    "CASH.CNY", AssetType.CASH, amount, Decimal(1), amount, preview.as_of_date
                ),
            )
            metadata = tuple(p for p in metadata if p.symbol != "CASH.CNY") + (
                InstrumentInput("CASH.CNY", AssetType.CASH, "人民币现金", None, preview.as_of_date),
            )
            try:
                flow = Decimal(external_flow)
            except InvalidOperation as exc:
                raise ValueError("请填写有效净入金金额，无入出金填0") from exc
            if not flow.is_finite() or (mode == "initial" and flow != 0):
                raise ValueError("净入金金额无效；首次建档请填0")
            with self.database.connection() as conn:
                before, fingerprint, valuations = _state(conn)
                if any(row[1] not in {"fund", "cash"} for row in before):
                    raise ValueError("账户含股票等其他资产，不能使用基金快照覆盖")
                tracked = conn.execute(
                    "SELECT count(*) FROM portfolio_events "
                    "WHERE event_type='baseline' AND status='confirmed'"
                ).fetchone()[0]
                if mode == "initial" and (
                    tracked
                    or valuations
                    or conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
                ):
                    raise ValueError("账户已有记录，请选择已有账户校准")
                if mode == "calibration" and (
                    not valuations or preview.as_of_date <= max(r[0] for r in valuations)
                ):
                    raise ValueError("校准需要已有有效净值，且截图日期必须晚于最近估值日")
                if any(row[5] > preview.as_of_date for row in before):
                    raise ValueError("截图日期早于当前持仓日期，不能回退账户")
            old = {r[0]: r for r in before}
            new = {p.symbol: p for p in positions}
            differences = []
            for code in sorted(old.keys() | new.keys()):
                a, b = old.get(code), new.get(code)
                differences.append(
                    {
                        "symbol": code,
                        "change": "新增"
                        if a is None
                        else "移除"
                        if b is None
                        else "保留"
                        if (a[2], a[3], a[4]) == (b.quantity, b.average_cost, b.market_value)
                        else "修改",
                        "before_quantity": format(a[2], "f") if a else "0",
                        "after_quantity": format(b.quantity, "f") if b else "0",
                        "before_cost": format(a[3], "f") if a else "0",
                        "after_cost": format(b.average_cost, "f") if b else "0",
                        "before_value": format(a[4], "f") if a else "0",
                        "after_value": format(b.market_value, "f") if b else "0",
                    }
                )
            review_id = uuid4().hex
            total = sum(p.market_value for p in positions)
            if total - flow <= 0:
                raise ValueError("扣除净入金后的账户价值必须为正")
            self._expire()
            if len(self.sessions) >= 20:
                raise ValueError("待核对记录过多，请稍后重试")
            self.sessions[review_id] = {
                "expires": self.clock() + 900,
                "positions": positions,
                "metadata": metadata,
                "fingerprint": fingerprint,
                "mode": mode,
                "flow": flow,
                "result": None,
            }
            return {
                "review_id": review_id,
                "differences": differences,
                "total": format(total, "f"),
                "mode": mode,
                "names": {p.symbol: p.name for p in metadata},
                "external_flow": format(flow, "f"),
            }

    def commit_review(self, token, confirmed):
        if confirmed is not True:
            raise ValueError("请核对差异并明确确认")
        with self.lock:
            session = self._session(token)
            if session.get("kind") == "photo_holdings":
                raise ValueError("请使用截图持仓保存入口")
            if "fingerprint" not in session:
                raise ValueError("请先生成差异预览")
            if session["result"] is not None:
                return session["result"]
            with self.database.connection() as conn:
                if _state(conn)[1] != session["fingerprint"]:
                    raise ValueError("账户已变化，请重新生成差异预览")
                positions = session["positions"]
                payload = [
                    session["mode"],
                    str(session["flow"].normalize()),
                    [
                        [
                            p.symbol,
                            str(p.quantity.normalize()),
                            str(p.average_cost.normalize()),
                            str(p.market_value.normalize()),
                            p.as_of_date.isoformat(),
                        ]
                        for p in sorted(positions, key=lambda p: p.symbol)
                    ],
                ]
                event = "photo-review:" + hashlib.sha256(json.dumps(payload).encode()).hexdigest()
                if session["mode"] == "initial":
                    self.commands.import_initial_account(
                        event,
                        positions[0].as_of_date,
                        positions,
                        reason="用户核对完整基金与现金差异后首次建档",
                        instrument_metadata=session["metadata"],
                    )
                else:
                    self.commands.record_portfolio_calibration(
                        PortfolioCalibrationInput(
                            event,
                            positions[0].as_of_date,
                            positions,
                            session["flow"],
                            "用户核对完整基金与现金差异后校准账户",
                        ),
                        instrument_metadata=session["metadata"],
                    )
            session["result"] = {"event_id": event, "status": "imported"}
            return session["result"]
