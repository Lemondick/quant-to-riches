"""Replay confirmed ledger events in temporary storage, without running strategies."""

import json
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.data.snapshots import _frame_sha256
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.models.repositories.database import Database
from quant_assistant.portfolio.nav_continuity import _build_portfolio
from quant_assistant.services.ledger_review_common import rows
from quant_assistant.services.portfolio_commands import (
    CashFlowType,
    CorporateActionInput,
    DividendInput,
    ExternalCashFlowInput,
    LedgerCorrectionInput,
    PortfolioCalibrationInput,
    PortfolioCommandService,
    PositionInput,
    TransactionInput,
)

TABLE_ORDER = {
    "positions": "symbol",
    "instruments": "symbol",
    "transactions": "id",
    "dividend_events": "id",
    "corporate_action_events": "id",
    "ledger_corrections": "id",
    "portfolio_events": "event_id",
    "portfolio_valuations": "as_of_date,run_id",
    "daily_prices": "symbol,trade_date",
    "fund_nav": "symbol,nav_date",
    "data_snapshots": "id",
}


def load_source(conn):
    source = {
        name: rows(conn, f"SELECT * FROM {name} ORDER BY {order}")
        for name, order in TABLE_ORDER.items()
    }
    source["audit_log"] = rows(
        conn,
        "SELECT * FROM audit_log WHERE entity_type IN "
        "('portfolio_account_import','portfolio_calibration','portfolio_baseline','position',"
        "'transaction','transaction_replay','dividend','corporate_action','ledger_correction',"
        "'external_cash_flow','ledger_replay') ORDER BY id",
    )
    return source


def _positions(payload):
    return tuple(
        PositionInput(
            row["symbol"],
            AssetType(row["asset_type"]),
            Decimal(str(row["quantity"])),
            Decimal(str(row["average_cost"])),
            Decimal(str(row["market_value"])),
            datetime.fromisoformat(str(row["as_of_date"])).date(),
        )
        for row in payload
    )


def _audited(source, category, identity):
    matches = [
        row
        for row in source["audit_log"]
        if row["entity_type"] == category and row["entity_id"] == str(identity)
    ]
    if not matches:
        raise ValueError("缺少完整账户快照的原始审计证据，不能重建历史")
    return json.loads(matches[-1]["after_json"])


def _baseline(source):
    baselines = [
        row
        for row in source["portfolio_events"]
        if row["event_type"] == "baseline" and row["status"] == "confirmed"
    ]
    if len(baselines) != 1 or not source["portfolio_valuations"]:
        raise ValueError("需要唯一已确认基线和已保存的净值序列")
    baseline = baselines[0]
    initial = _audited(source, "portfolio_account_import", baseline["event_id"])
    if not initial.get("positions"):
        raise ValueError("初始账户缺少完整持仓快照")
    first = source["portfolio_valuations"][0]
    if first["as_of_date"] != baseline["effective_date"] or not first["is_baseline"]:
        raise ValueError("净值序列与原始账户基线日期不一致")
    if len({row["as_of_date"] for row in source["portfolio_valuations"]}) != len(
        source["portfolio_valuations"]
    ):
        raise ValueError("同一天存在多个估值版本，请先核对历史")
    return baseline, _positions(initial["positions"])


def _verify_marks(conn, day, checked_files):
    positions = rows(
        conn, "SELECT symbol,asset_type FROM positions WHERE asset_type<>'cash' AND quantity>0"
    )
    for item in positions:
        fund = item["asset_type"] == "fund"
        table, date_column, price = (
            ("fund_nav", "nav_date", "unit_nav")
            if fund
            else ("daily_prices", "trade_date", "close")
        )
        row = conn.execute(
            f"SELECT p.{price},s.status,s.issues_json,s.path,s.sha256,s.dataset,s.row_count "
            f"FROM {table} p LEFT JOIN data_snapshots s ON s.id=p.source_snapshot_id "
            f"WHERE p.symbol=? AND p.{date_column}=?",
            [item["symbol"], day],
        ).fetchone()
        if (
            not row
            or row[0] is None
            or Decimal(str(row[0])) <= 0
            or row[1] != "valid"
            or json.loads(row[2] or "[]")
        ):
            raise ValueError(f"{item['symbol']}在{day}缺少已验证的正式估值证据")
        if row[5] not in ({"fund_nav", "fund_daily"} if fund else {"daily_prices"}):
            raise ValueError("估值证据的数据类型不匹配")
        path = Path(row[3])
        key = (str(path), row[4])
        if key not in checked_files:
            try:
                frame = pd.read_parquet(path)
            except Exception as error:
                raise ValueError(f"{item['symbol']}历史估值证据文件缺失或无法读取") from error
            if len(frame) != row[6] or _frame_sha256(frame) != row[4]:
                raise ValueError(f"{item['symbol']}历史估值证据内容校验失败")
            checked_files[key] = frame
        frame = checked_files[key]
        if not {"symbol", date_column, price}.issubset(frame.columns):
            raise ValueError("历史估值证据缺少必需字段")
        marks = frame.loc[
            frame["symbol"].astype(str).eq(item["symbol"])
            & pd.to_datetime(frame[date_column]).dt.date.eq(day)
        ]
        if len(marks) != 1 or Decimal(str(marks.iloc[0][price])) != Decimal(str(row[0])):
            raise ValueError(f"{item['symbol']}历史估值与原始快照不一致")


def _valuation(db, original, now, checked_files):
    day = original["as_of_date"]
    with db.connection() as conn:
        _verify_marks(conn, day, checked_files)
        positions = conn.execute("SELECT * FROM positions ORDER BY symbol").fetchdf()
        prices = conn.execute("SELECT * FROM daily_prices WHERE trade_date<=?", [day]).fetchdf()
        fund_nav = conn.execute("SELECT * FROM fund_nav WHERE nav_date<=?", [day]).fetchdf()
        previous = conn.execute(
            "SELECT * FROM portfolio_valuations ORDER BY as_of_date DESC LIMIT 1"
        ).fetchdf()
        baseline = conn.execute(
            "SELECT * FROM portfolio_events WHERE event_type='baseline' AND effective_date=?", [day]
        ).fetchdf()
        previous_day = day if previous.empty else previous.iloc[0]["as_of_date"].date()
        flows = conn.execute(
            "SELECT * FROM portfolio_events WHERE status='confirmed' AND event_type "
            "IN ('deposit','withdrawal','calibration') AND effective_date>? AND effective_date<=?",
            [previous_day, day],
        ).fetchdf()
        _, portfolio, draft, _, blockers = _build_portfolio(
            positions,
            prices,
            fund_nav,
            day,
            previous,
            baseline,
            flows,
            pd.DataFrame(),
            pd.DataFrame(),
            None,
        )
        if blockers or not draft.valid:
            raise ValueError("；".join(blockers) or "历史净值不可重算")
        result = {
            **original,
            "generated_at": now,
            "total_value": portfolio.total_value,
            "cash": portfolio.cash,
            "unit_nav": portfolio.unit_nav,
            "high_water_mark": portfolio.high_water_mark,
            "net_external_flow": draft.net_external_flow,
            "previous_run_id": draft.previous_run_id,
            "positions_fingerprint": draft.positions_fingerprint,
            "events_fingerprint": draft.events_fingerprint,
            "is_baseline": draft.is_baseline,
        }
        columns = list(result)
        conn.execute(
            f"INSERT INTO portfolio_valuations ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [result[column] for column in columns],
        )
        return result


def _transaction(row):
    return TransactionInput(
        row["symbol"],
        AssetType(row["asset_type"]),
        Action(row["action"]),
        row["quantity"],
        row["price"],
        row["fees"],
        row["traded_at"],
        row["external_id"],
        source=row["source"],
        cash_symbol=row["cash_symbol"],
        note=row["note"],
    )


def _apply(commands, source, kind, row, ids):
    if kind == "transaction":
        ids[(kind, row["id"])] = commands.record_transaction(_transaction(row))
    elif kind == "dividend":
        ids[(kind, row["id"])] = commands.record_dividend(
            DividendInput(
                row["symbol"],
                AssetType(row["asset_type"]),
                row["gross_amount"],
                row["fees"],
                row["paid_at"],
                row["external_id"],
                row["source"],
                row["cash_symbol"],
                row["note"],
            )
        )
    elif kind == "corporate":
        if row["action_type"] == "custody_transfer":
            raise ValueError("转托管涉及平台账户历史，需先核对完整平台快照再重建")
        fields = {key: row[key] for key in CorporateActionInput.__dataclass_fields__}
        fields["asset_type"] = AssetType(fields["asset_type"])
        commands.record_corporate_action(CorporateActionInput(**fields))
    elif kind == "correction":
        target = ids.get((row["target_type"], row["target_id"]))
        if target is None:
            raise ValueError("冲正目标不在可重建的历史中")
        commands.correct_ledger_entry(
            LedgerCorrectionInput(
                row["target_type"],
                target,
                row["reason"],
                row["effective_at"],
                row["external_id"],
                row["source"],
                row["note"],
            )
        )
    elif kind == "flow":
        if row["event_type"] in {"deposit", "withdrawal"}:
            details = json.loads(row["details_json"])
            commands.record_external_cash_flow(
                ExternalCashFlowInput(
                    row["event_id"],
                    CashFlowType(row["event_type"]),
                    abs(row["amount"]),
                    row["effective_date"],
                    row["reason"],
                    details.get("cash_symbol"),
                )
            )
        elif row["event_type"] == "calibration":
            details = _audited(source, "portfolio_calibration", row["event_id"])
            commands.record_portfolio_calibration(
                PortfolioCalibrationInput(
                    row["event_id"],
                    row["effective_date"],
                    _positions(details["positions"]),
                    row["amount"],
                    row["reason"],
                )
            )
        else:
            raise ValueError("历史包含未支持的净值重置事件，需要单独核对")
        _restore_event_record(commands._database, row)


def _restore_event_record(database, original):
    # Rebuild balances through domain commands while retaining original evidence provenance.
    with database.connection() as conn:
        conn.execute(
            "UPDATE portfolio_events SET recorded_at=?,details_json=? WHERE event_id=?",
            [original["recorded_at"], original["details_json"], original["event_id"]],
        )


def _event_order(source, kind, row):
    category = {"corporate": "corporate_action", "correction": "ledger_correction"}.get(kind, kind)
    if kind == "flow":
        category = (
            "portfolio_calibration" if row["event_type"] == "calibration" else "external_cash_flow"
        )
    identity = row["event_id"] if kind == "flow" else row["id"]
    records = [
        entry["id"]
        for entry in source["audit_log"]
        if entry["entity_type"] == category and entry["entity_id"] == str(identity)
    ]
    if len(records) != 1:
        raise ValueError("账本事件缺少唯一原始审计顺序，不能自动重建")
    return records[0]


def simulate(source, missing, directory, now):
    trades = [missing] if isinstance(missing, TransactionInput) else list(missing)
    if not 1 <= len(trades) <= 1000:
        raise ValueError("每批历史成交必须为1至1000笔")
    if len({(item.source, item.external_id) for item in trades}) != len(trades):
        raise ValueError("批次内成交编号不能重复")
    if any(
        right.traded_at <= left.traded_at for left, right in zip(trades, trades[1:], strict=False)
    ):
        raise ValueError("批次内成交必须按真实时间顺序排列；同时发生的成交需先核对日内顺序")
    baseline, initial = _baseline(source)
    for item in trades:
        if item.traded_at.date() <= baseline["effective_date"]:
            raise ValueError("补录日期必须晚于已确认账户基线")
        if item.traded_at > now:
            raise ValueError("不能补录未来成交")
    if any(row["status"] == "pending" for row in source["portfolio_events"]):
        raise ValueError("存在待确认的资金事件，请先核对")
    if any(
        row[field] == item.traded_at
        for table, field in (
            ("transactions", "traded_at"),
            ("dividend_events", "paid_at"),
            ("corporate_action_events", "effective_at"),
            ("ledger_corrections", "effective_at"),
        )
        for row in source[table]
        for item in trades
    ):
        raise ValueError("补录与原始事件同时发生，缺少原始日内顺序证据，请先核对")
    if any(
        row["effective_at"] >= item.traded_at
        and item.symbol in {row["symbol"], row["target_symbol"]}
        for row in source["corporate_action_events"]
        for item in trades
    ):
        raise ValueError("补录影响后续公司行动权益，需先核对拆分、转换或分红再投的完整权益数量")
    if len(source["transactions"]) + len(source["portfolio_valuations"]) + len(trades) > 20000:
        raise ValueError("单次回算最多2万条交易与估值，请分段核对")
    db = Database(Path(directory) / "replay.duckdb")
    try:
        db.initialize()
        with db.connection() as conn:
            for table in ("instruments", "daily_prices", "fund_nav", "data_snapshots"):
                if source[table]:
                    frame = pd.DataFrame(source[table])
                    conn.register("replay_input", frame)
                    try:
                        conn.execute(f"INSERT INTO {table} BY NAME SELECT * FROM replay_input")
                    finally:
                        conn.unregister("replay_input")
        commands = PortfolioCommandService(db, lambda: now)
        commands.import_initial_account(
            baseline["event_id"], baseline["effective_date"], initial, reason=baseline["reason"]
        )
        _restore_event_record(db, baseline)
        events = []
        for table, kind, field in (
            ("transactions", "transaction", "traded_at"),
            ("dividend_events", "dividend", "paid_at"),
            ("corporate_action_events", "corporate", "effective_at"),
            ("ledger_corrections", "correction", "effective_at"),
        ):
            for row in source[table]:
                events.append(
                    (row[field], 1, row["recorded_at"], _event_order(source, kind, row), kind, row)
                )
        for row in source["portfolio_events"]:
            if row["event_type"] != "baseline" and row["status"] == "confirmed":
                events.append(
                    (
                        datetime.combine(row["effective_date"], time.min, SHANGHAI_TZ),
                        0,
                        row["recorded_at"],
                        _event_order(source, "flow", row),
                        "flow",
                        row,
                    )
                )
        for index, item in enumerate(trades):
            events.append((item.traded_at, 1, now, -index - 1, "missing", item))
        calibration_days = {
            row["effective_date"]
            for row in source["portfolio_events"]
            if row["event_type"] == "calibration" and row["status"] == "confirmed"
        }
        if any(
            instant.date() in calibration_days
            and (kind != "flow" or row["event_type"] != "calibration")
            for instant, _, _, _, kind, row in events
        ):
            raise ValueError("校准日同时有其他账本活动，缺少日内顺序证据，不能自动重建")
        for row in source["portfolio_valuations"]:
            events.append(
                (
                    datetime.combine(row["as_of_date"], time.max, SHANGHAI_TZ),
                    2,
                    row["generated_at"],
                    row["run_id"],
                    "valuation",
                    row,
                )
            )
        ids, valuations, checked_files = {}, [], {}
        for _, _, _, _, kind, row in sorted(events, key=lambda item: item[:4]):
            if kind == "missing":
                commands.record_transaction(row)
            elif kind == "valuation":
                valuations.append(_valuation(db, row, now, checked_files))
            else:
                _apply(commands, source, kind, row, ids)
        with db.connection() as conn:
            return {
                "positions": rows(conn, "SELECT * FROM positions ORDER BY symbol"),
                "valuations": valuations,
                "transaction_audits": rows(
                    conn,
                    "SELECT t.source,t.external_id,a.before_json,a.after_json "
                    "FROM transactions t JOIN audit_log a ON a.entity_type='transaction' "
                    "AND a.entity_id=CAST(t.id AS VARCHAR) "
                    "ORDER BY t.id",
                ),
            }
    finally:
        db.close()
