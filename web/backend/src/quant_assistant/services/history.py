"""Paged, filtered historical ledger reads and spreadsheet-safe CSV export."""

import csv
import io

from quant_assistant.services.admin_user_details import LEDGER, _rows, _value

LEDGER_HISTORY = f"SELECT *, reversed_by_correction_id IS NOT NULL AS reversed FROM ({LEDGER})"
ACTIONS = "SELECT * FROM corporate_action_events"
CORRECTIONS = """SELECT c.*,coalesce(t.symbol,d.symbol) AS symbol FROM ledger_corrections c
LEFT JOIN transactions t ON c.target_type='transaction' AND c.target_id=t.id
LEFT JOIN dividend_events d ON c.target_type='dividend' AND c.target_id=d.id"""
PROJECTIONS = {
    "ledger": (LEDGER_HISTORY, "occurred_at"),
    "actions": (ACTIONS, "effective_at"),
    "corrections": (CORRECTIONS, "effective_at"),
}


def filters(sql, *, symbol="", start=None, end=None, time_column=None):
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    clauses, values = [], []
    if symbol:
        clauses.append("contains(upper(symbol), upper(?))")
        values.append(symbol.strip())
    if time_column:
        for date_value, operator in ((start, ">="), (end, "<=")):
            if date_value:
                clauses.append(f"CAST({time_column} AS DATE) {operator} ?")
                values.append(date_value)
    return f"SELECT * FROM ({sql}) filtered" + (
        " WHERE " + " AND ".join(clauses) if clauses else ""
    ), values


def csv_text(items):
    if not items:
        return ""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(items[0]))
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                key: (
                    "'" + value
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                    else value
                )
                for key, value in item.items()
            }
        )
    return output.getvalue()


def history(
    connection, section, *, page=1, limit=50, symbol="", start=None, end=None, export=False
):
    sql, time_column = PROJECTIONS[section]
    sql, values = filters(sql, symbol=symbol, start=start, end=end, time_column=time_column)
    total = connection.execute(f"SELECT count(*) FROM ({sql}) history", values).fetchone()[0]
    rows = _rows(
        connection,
        f"SELECT * FROM ({sql}) history ORDER BY {time_column} DESC,id DESC LIMIT ? OFFSET ?",
        [*values, limit, (page - 1) * limit],
    )
    items = [{key: _value(value) for key, value in row.items()} for row in rows]
    result = {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
    }
    if export:
        result["csv"] = csv_text(items)
    return result
