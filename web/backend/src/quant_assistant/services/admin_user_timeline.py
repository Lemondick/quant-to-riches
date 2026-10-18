"""Paged administrator event projections from durable actions, never request polling."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from quant_assistant.services.auth import public_user, timestamp

BUSINESS_EVENTS = """
    SELECT 'audit:' || CAST(id AS VARCHAR) AS id,
           CASE WHEN entity_type='portfolio_account_import' THEN 'imports'
                WHEN entity_type IN ('transaction','dividend','external_cash_flow',
                                     'corporate_action','ledger_correction') THEN 'trades'
                ELSE 'portfolio' END AS category,
           entity_type AS action, entity_id AS reference, changed_at AS occurred_at,
           'completed' AS status
    FROM audit_log
    UNION ALL
    SELECT 'snapshot:' || CAST(id AS VARCHAR),
           CASE WHEN dataset='fund_nav' THEN 'nav' ELSE 'data' END,
           'data_ingested', dataset, fetched_at, status FROM data_snapshots
    UNION ALL
    SELECT 'advisor:' || report_id, 'reports', 'advisor_report_outcome', kind,
           updated_at, status FROM market_advisor_reports
    WHERE status IN ('completed','blocked','failed')
    UNION ALL
    SELECT 'research:' || job_id || ':' || CAST(attempt_no AS VARCHAR),
           'tasks', 'research_task_outcome', job_id, completed_at, execution_status
    FROM agent_research_attempts
    WHERE completed_at IS NOT NULL
      AND execution_status NOT IN ('queued','running','cancel_requested')
"""

ACCOUNT_EVENTS = """
    SELECT 'account:created' AS id,'account' AS category,'account_created' AS action,
           '' AS reference,created_at AS occurred_at,'completed' AS status FROM users WHERE id=?
    UNION ALL
    SELECT 'account:' || id,'account',action,'',created_at,'completed'
    FROM account_activity WHERE user_id=?
    UNION ALL
    SELECT 'account:last-login','account','last_login_recorded','',last_login_at,'completed'
    FROM users WHERE id=? AND last_login_at IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM account_activity
        WHERE user_id=? AND action='login' AND created_at=last_login_at
    )
    UNION ALL
    SELECT 'admin:' || id,'account',action,
           CASE WHEN action='status' THEN json_extract(detail,'$.value') ELSE '' END,
           created_at,'completed' FROM admin_audit
    WHERE target_id=? AND action IN ('status','revoke_sessions','recovery_issued')
"""


def _rows(connection, sql, parameters):
    cursor = connection.execute(sql, parameters)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


class AdminUserTimeline:
    def __init__(self, contexts, auth):
        self.contexts, self.auth = contexts, auth

    def events(self, user, *, category="all", page=1, limit=50, start=None, end=None):
        now = datetime.fromtimestamp(self.auth.clock(), UTC)
        today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        first_day = today - timedelta(days=29)
        trends = {
            first_day + timedelta(days=day): {"actions": 0, "failures": 0} for day in range(30)
        }
        where, parameters = [], []
        if category not in {"all", "business", "account"}:
            where.append("category=?")
            parameters.append(category)
        for value, operator in ((start, ">="), (end, "<=")):
            if value:
                where.append(f"CAST(occurred_at AT TIME ZONE 'Asia/Shanghai' AS DATE){operator}?")
                parameters.append(value)
        suffix = " WHERE " + " AND ".join(where) if where else ""
        candidates, total = [], 0
        # Each source contributes its highest page*limit entries before the global stable merge.
        depth = page * limit
        with self.contexts.read_database(user) as connection:
            if connection is not None:
                daily = _rows(
                    connection,
                    f"""
                    SELECT CAST(occurred_at AT TIME ZONE 'Asia/Shanghai' AS DATE) AS day,
                           count(*) AS actions,
                           count(*) FILTER(WHERE status IN ('failed','blocked','timed_out',
                                                            'cancelled','interrupted')) AS failures
                    FROM ({BUSINESS_EVENTS}) events
                    WHERE CAST(occurred_at AT TIME ZONE 'Asia/Shanghai' AS DATE) BETWEEN ? AND ?
                    GROUP BY day
                """,
                    [first_day, today],
                )
                for row in daily:
                    trends[row["day"]] = {"actions": row["actions"], "failures": row["failures"]}
                if category != "account":
                    total += connection.execute(
                        f"SELECT count(*) FROM ({BUSINESS_EVENTS}) events{suffix}", parameters
                    ).fetchone()[0]
                    candidates.extend(
                        _rows(
                            connection,
                            f"SELECT * FROM ({BUSINESS_EVENTS}) events{suffix} "
                            "ORDER BY occurred_at DESC,id DESC LIMIT ?",
                            [*parameters, depth],
                        )
                    )
        if category in {"all", "account"}:
            account_where, account_parameters = [], [user["id"]] * 5
            for value, operator in ((start, ">="), (end, "<=")):
                if value:
                    # Fixed +08:00 is Shanghai's timezone for supported account history.
                    account_where.append(f"date(occurred_at,'unixepoch','+8 hours'){operator}?")
                    account_parameters.append(value.isoformat())
            account_suffix = " WHERE " + " AND ".join(account_where) if account_where else ""
            with self.auth.repository.connection() as connection:
                total += connection.execute(
                    f"SELECT count(*) FROM ({ACCOUNT_EVENTS}) events{account_suffix}",
                    account_parameters,
                ).fetchone()[0]
                accounts = _rows(
                    connection,
                    f"SELECT * FROM ({ACCOUNT_EVENTS}) events{account_suffix} "
                    "ORDER BY occurred_at DESC,id DESC LIMIT ?",
                    [*account_parameters, depth],
                )
                for row in accounts:
                    row["occurred_at"] = datetime.fromtimestamp(row["occurred_at"], UTC)
                candidates.extend(accounts)
        candidates.sort(key=lambda row: (row["occurred_at"], row["id"]), reverse=True)
        return {
            "user": public_user(user),
            "page": page,
            "limit": limit,
            "total": total,
            "items": [
                {**row, "occurred_at": timestamp(row["occurred_at"].timestamp())}
                for row in candidates[(page - 1) * limit : depth]
            ],
            "trends": [{"day": day.isoformat(), **values} for day, values in trends.items()],
            "trend_scope": "business_actions",
            "request_polling_included": False,
            "history_note": "仅展示已保存的业务事件与账号记录；缺失的旧历史不会补写。",
        }
