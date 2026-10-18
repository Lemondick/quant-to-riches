"""Bounded read-only administrator projections, without initializing user services."""

import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, localcontext

from quant_assistant.services.auth import AuthError, public_user
from quant_assistant.services.preferences import LocalPreferenceStore

HOLDINGS = """
    SELECT p.*, coalesce(i.name, p.symbol) AS name FROM (
        SELECT 'local' AS source, '' AS account_id, '本地主账本' AS account_name,
               'local-ledger' AS platform, 'CNY' AS currency,
               symbol, asset_type, quantity, average_cost, market_value, as_of_date, updated_at
        FROM positions WHERE quantity>0
        UNION ALL
        SELECT 'snapshot', p.account_id, a.name, a.platform, a.currency,
               p.symbol, p.asset_type, p.quantity, p.average_cost, p.market_value,
               p.as_of_date, p.updated_at
        FROM account_positions p JOIN portfolio_accounts a ON a.account_id=p.account_id
        WHERE a.status='active' AND p.quantity>0
    ) p LEFT JOIN instruments i ON i.symbol=p.symbol
"""

LEDGER = """
    SELECT t.id, CASE WHEN lower(coalesce(t.source,''))='dca' THEN 'dca'
                     WHEN t.action IN ('buy','add') THEN 'buy'
                     WHEN t.action IN ('sell','reduce') THEN 'sell' ELSE t.action END AS entry_type,
           t.symbol, coalesce(i.name,t.symbol) AS name, t.asset_type,
           t.quantity, t.price, t.gross_amount, t.fees, t.cash_delta AS net_amount,
           t.traded_at AS occurred_at, t.note, t.reversed_by_correction_id
    FROM transactions t LEFT JOIN instruments i ON i.symbol=t.symbol
    UNION ALL
    SELECT d.id, 'dividend', d.symbol, coalesce(i.name,d.symbol), d.asset_type,
           NULL, NULL, d.gross_amount, d.fees, d.net_amount, d.paid_at, d.note,
           d.reversed_by_correction_id
    FROM dividend_events d LEFT JOIN instruments i ON i.symbol=d.symbol
"""

REPORTS = """
    SELECT 'advisor' AS source, report_id, 0 AS attempt_no, kind, '' AS symbol,
           status, report_date, created_at, updated_at, error_code,
           report_json IS NOT NULL AS has_content
    FROM market_advisor_reports
    UNION ALL
    SELECT 'research', a.job_id, a.attempt_no, 'stock_research', j.symbol,
           a.execution_status, a.quota_date, a.started_at, a.completed_at,
           CASE WHEN j.attempt_no=a.attempt_no THEN j.error_code ELSE NULL END,
           TRUE
    FROM agent_research_attempts a JOIN agent_research_jobs j ON j.job_id=a.job_id
    WHERE a.report_json IS NOT NULL
"""

TASKS = """
    SELECT job_id, symbol, status, attempt_no, phase, error_code, created_at
    FROM agent_research_jobs
"""


def _value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _rows(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _page(connection, sql, order, page, limit):
    total = connection.execute(f"SELECT count(*) FROM ({sql}) rows").fetchone()[0]
    items = _rows(
        connection,
        f"SELECT * FROM ({sql}) rows ORDER BY {order} LIMIT ? OFFSET ?",
        [limit, (page - 1) * limit],
    )
    return total, items


class AdminUserDetails:
    def __init__(self, contexts):
        self.contexts = contexts

    def details(self, user, *, section, page, limit, symbol="", start=None, end=None):
        try:
            directory = self.contexts.directory(user)
            summary = self.contexts.summary(user)
        except (ValueError, OSError):
            raise AuthError("user_data_unavailable") from None
        if summary["data_status"] == "unavailable":
            raise AuthError("user_data_unavailable")
        overview = {
            **summary,
            "local_market_value": None,
            "local_cash": None,
            "accounts": [],
        }
        total, items = 0, []
        try:
            with self.contexts.read_database(user) as connection:
                if connection is not None:
                    totals = _rows(
                        connection,
                        """
                        SELECT coalesce(sum(market_value),0) AS local_market_value,
                               coalesce(sum(CASE WHEN asset_type='cash'
                                                 THEN market_value ELSE 0 END),0) AS local_cash
                        FROM positions WHERE quantity>0
                    """,
                    )[0]
                    overview.update({key: _value(value) for key, value in totals.items()})
                    overview["accounts"] = _rows(
                        connection,
                        """
                        SELECT a.account_id, a.name, a.platform, a.currency,
                               count(p.symbol) AS position_count,
                               coalesce(sum(p.market_value),0) AS market_value,
                               max(p.as_of_date) AS as_of_date
                        FROM portfolio_accounts a LEFT JOIN account_positions p
                          ON p.account_id=a.account_id AND p.quantity>0
                        WHERE a.status='active'
                        GROUP BY a.account_id,a.name,a.platform,a.currency ORDER BY a.account_id
                    """,
                    )
                    projections = {
                        "holdings": (HOLDINGS, "source,account_id,asset_type,symbol"),
                        "ledger": (LEDGER, "occurred_at DESC,entry_type,id DESC"),
                        "reports": (REPORTS, "created_at DESC,source,report_id,attempt_no DESC"),
                        "tasks": (TASKS, "created_at DESC,job_id"),
                    }
                    if section in projections:
                        sql, order = projections[section]
                        from quant_assistant.services.history import filters

                        time_column = {
                            "holdings": "as_of_date",
                            "ledger": "occurred_at",
                            "reports": "created_at",
                            "tasks": "created_at",
                        }[section]
                        sql, parameters = filters(
                            sql, symbol=symbol, start=start, end=end, time_column=time_column
                        )
                        total = connection.execute(
                            f"SELECT count(*) FROM ({sql}) rows", parameters
                        ).fetchone()[0]
                        items = _rows(
                            connection,
                            f"SELECT * FROM ({sql}) rows ORDER BY {order} LIMIT ? OFFSET ?",
                            [*parameters, limit, (page - 1) * limit],
                        )
                if section == "watchlist":
                    preferences = LocalPreferenceStore(
                        directory / "preferences.json",
                        legacy_watchlist_path=directory / "watchlist.json",
                    ).load()
                    watchlist = [asdict(item) for item in preferences.watchlist]
                    if symbol:
                        watchlist = [
                            item for item in watchlist if symbol.upper() in item["symbol"].upper()
                        ]
                    total, items = len(watchlist), watchlist[(page - 1) * limit : page * limit]
        except (ValueError, OSError, RuntimeError) as error:
            if isinstance(error, AuthError):
                raise
            raise AuthError("user_data_unavailable") from None
        if section == "holdings":
            # Preserve the full product of DECIMAL(28,8) quantity and DECIMAL(20,6) cost.
            with localcontext() as arithmetic:
                arithmetic.prec = 64
                for item in items:
                    known_cost = item["asset_type"] != "cash" and item["average_cost"] > 0
                    cost = item["quantity"] * item["average_cost"] if known_cost else None
                    item["cost_value"] = cost
                    item["floating_profit"] = (
                        item["market_value"] - cost if cost is not None else None
                    )
        overview["accounts"] = [
            {key: _value(value) for key, value in item.items()} for item in overview["accounts"]
        ]
        return {
            "user": public_user(user),
            "data_status": summary["data_status"],
            "overview": overview,
            "section": section,
            "page": page,
            "limit": limit,
            "total": total,
            "items": [{key: _value(value) for key, value in item.items()} for item in items],
        }

    def task_statistics(self, user):
        from datetime import UTC, timedelta

        since = datetime.fromtimestamp(self.contexts.auth.clock(), UTC) - timedelta(days=7)
        with self.contexts.read_database(user) as conn:
            if conn is None:
                return {
                    "reports_7d": 0,
                    "reports_succeeded_7d": 0,
                    "reports_failed_7d": 0,
                    "average_duration_seconds": None,
                    "recent_errors": [],
                }
            sql = """SELECT 'advisor' AS source,report_id,status,error_code,
                created_at AS started_at,
                updated_at AS completed_at FROM market_advisor_reports
                UNION ALL SELECT 'research',a.job_id,a.execution_status,
                CASE WHEN a.attempt_no=j.attempt_no THEN j.error_code ELSE NULL END,
                a.started_at,a.completed_at FROM agent_research_attempts a
                JOIN agent_research_jobs j ON j.job_id=a.job_id"""
            rows = _rows(
                conn,
                f"SELECT * FROM ({sql}) tasks WHERE started_at>=? ORDER BY started_at DESC",
                [since],
            )
        completed = [
            row
            for row in rows
            if row["status"] not in {"queued", "running", "cancel_requested"}
            and row["completed_at"]
        ]
        durations = [
            max(0, (row["completed_at"] - row["started_at"]).total_seconds()) for row in completed
        ]
        errors = [row for row in completed if row["status"] not in {"succeeded", "completed"}]
        return {
            "reports_7d": len(rows),
            "reports_succeeded_7d": sum(
                row["status"] in {"succeeded", "completed"} for row in rows
            ),
            "reports_failed_7d": len(errors),
            "average_duration_seconds": round(sum(durations) / len(durations), 2)
            if durations
            else None,
            "recent_errors": [
                {key: _value(value) for key, value in row.items()} for row in errors[:10]
            ],
        }

    def report(self, user, *, source, report_id, attempt_no):
        with self.contexts.read_database(user) as connection:
            if connection is None:
                raise AuthError("report_not_found")
            if source == "advisor":
                rows = _rows(
                    connection,
                    """
                    SELECT report_id,kind,status,report_date,created_at,updated_at,report_json
                    FROM market_advisor_reports WHERE report_id=?
                """,
                    [report_id],
                )
            else:
                rows = _rows(
                    connection,
                    """
                    SELECT a.job_id AS report_id,a.attempt_no,j.symbol,
                           a.execution_status AS status,a.quota_date AS report_date,
                           a.started_at AS created_at,a.completed_at AS updated_at,a.report_json
                    FROM agent_research_attempts a JOIN agent_research_jobs j ON j.job_id=a.job_id
                    WHERE a.job_id=? AND a.attempt_no=? AND a.report_json IS NOT NULL
                """,
                    [report_id, attempt_no],
                )
        if not rows:
            raise AuthError("report_not_found")
        row = rows[0]
        try:
            raw = row.pop("report_json")
            content = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            raise AuthError("user_data_unavailable") from None
        if content is not None and not isinstance(content, dict):
            raise AuthError("user_data_unavailable")
        return {
            "user_id": user["id"],
            "source": source,
            "report": content,
            "historical_record": True,
            **{key: _value(value) for key, value in row.items()},
        }
