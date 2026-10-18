"""Transactional, idempotent conversion of legacy six-digit stock identifiers."""

import json
import re

from quant_assistant.common.symbols import canonical_symbol


def migrate_stock_symbols(connection):
    stocks = connection.execute(
        "SELECT symbol,exchange FROM instruments WHERE asset_type='stock'"
    ).fetchall()
    mapping = [
        (old, canonical_symbol(old, "stock", exchange))
        for old, exchange in stocks
        if re.fullmatch(r"\d{6}", old)
    ]
    if not mapping:
        return
    columns = connection.execute(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='main'"
    ).fetchall()
    tables = {}
    for table, column in columns:
        tables.setdefault(table, set()).add(column)
    connection.begin()
    try:
        for old, new in mapping:
            # Never merge two separately stored holdings or overwrite their history.
            if connection.execute("SELECT 1 FROM instruments WHERE symbol=?", [new]).fetchone():
                raise ValueError("旧股票代码与完整股票代码同时存在，请先核对重复记录")
            for table, fields in tables.items():
                if "symbol" not in fields:
                    continue
                if "asset_type" in fields:
                    condition = " AND asset_type='stock'"
                elif table in {"daily_prices", "stock_rankings"}:
                    condition = ""
                else:
                    continue
                # Names come from the installed schema, never from an HTTP request.
                connection.execute(
                    f'UPDATE "{table}" SET symbol=? WHERE symbol=?{condition}', [new, old]
                )
            connection.execute(
                "UPDATE corporate_action_events SET target_symbol=? "
                "WHERE target_symbol=? AND asset_type='stock'",
                [new, old],
            )

            # Sell corrections replay these financial snapshots. Normalize typed stock
            # identifiers while keeping every amount, timestamp and audit row intact.
            def normalize(value, old=old, new=new):
                if isinstance(value, list):
                    return [normalize(item) for item in value]
                if isinstance(value, dict):
                    result = {key: normalize(item) for key, item in value.items()}
                    if value.get("asset_type") == "stock":
                        for key in ("symbol", "target_symbol"):
                            if result.get(key) == old:
                                result[key] = new
                    return result
                return value

            for audit_id, before, after in connection.execute(
                "SELECT id,before_json,after_json FROM audit_log"
            ).fetchall():
                normalized = [
                    json.dumps(normalize(json.loads(value)), ensure_ascii=False) if value else value
                    for value in (before, after)
                ]
                connection.execute(
                    "UPDATE audit_log SET before_json=?,after_json=? WHERE id=?",
                    [*normalized, audit_id],
                )
            for event_id, details in connection.execute(
                "SELECT event_id,details_json FROM portfolio_events"
            ).fetchall():
                if details:
                    normalized_details = json.dumps(
                        normalize(json.loads(details)), ensure_ascii=False
                    )
                    connection.execute(
                        "UPDATE portfolio_events SET details_json=? WHERE event_id=?",
                        [normalized_details, event_id],
                    )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
