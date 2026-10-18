"""Durable research jobs with atomic deduplication and pre-call quota reservations."""

import json
from uuid import uuid4

from quant_assistant.analysis.research_models import (
    ACTIVE,
    PROMPT_VERSION,
    UPSTREAM_COMMIT,
    ResearchError,
    canonical,
)


def row_dict(cursor):
    row = cursor.fetchone()
    return (
        dict(zip([column[0] for column in cursor.description], row, strict=True)) if row else None
    )


class ResearchRepository:
    def __init__(self, database, clock):
        self.database, self.clock = database, clock

    def get(self, job_id):
        with self.database.connection() as conn:
            row = row_dict(
                conn.execute(
                    """SELECT j.*, a.snapshot_id, a.report_json, a.usage_json,
                a.provider, a.model, a.prompt_version, a.upstream_commit, a.completed_at
                FROM agent_research_jobs j JOIN agent_research_attempts a
                ON a.job_id=j.job_id AND a.attempt_no=j.attempt_no WHERE j.job_id=?""",
                    [job_id],
                )
            )
        if row is None:
            raise ResearchError("job_not_found", 404)
        return row

    def enqueue(self, cache_key, symbol, snapshot_id, settings, retry_id=None):
        now = self.clock()
        with self.database.transaction() as conn:
            existing = row_dict(
                conn.execute("SELECT * FROM agent_research_jobs WHERE cache_key=?", [cache_key])
            )
            if retry_id:
                old = row_dict(
                    conn.execute("SELECT * FROM agent_research_jobs WHERE job_id=?", [retry_id])
                )
                if old is None:
                    raise ResearchError("job_not_found", 404)
                if old["status"] in ACTIVE:
                    raise ResearchError("job_active")
            if existing and (not retry_id or existing["job_id"] != retry_id):
                return existing["job_id"], True
            count = conn.execute(
                "SELECT count(*) FROM agent_research_attempts WHERE quota_date=?", [now.date()]
            ).fetchone()[0]
            if count >= settings.daily_limit:
                raise ResearchError("quota_exceeded", 429)
            job_id = existing["job_id"] if existing else uuid4().hex
            attempt = existing["attempt_no"] + 1 if existing else 1
            if existing:
                conn.execute(
                    """UPDATE agent_research_jobs SET status='queued', attempt_no=?,
                    phase='queued',deadline=NULL,error_code=NULL WHERE job_id=?""",
                    [attempt, job_id],
                )
            else:
                conn.execute(
                    "INSERT INTO agent_research_jobs "
                    "VALUES (?,?,?,'queued',?,'queued',NULL,NULL,?)",
                    [job_id, cache_key, symbol, attempt, now],
                )
            conn.execute(
                """INSERT INTO agent_research_attempts
                (job_id,attempt_no,snapshot_id,quota_date,started_at,execution_status,
                 provider,model,prompt_version,upstream_commit)
                 VALUES (?,?,?,?,?,'queued',?,?,?,?)""",
                [
                    job_id,
                    attempt,
                    snapshot_id,
                    now.date(),
                    now,
                    settings.provider,
                    settings.model,
                    PROMPT_VERSION,
                    UPSTREAM_COMMIT,
                ],
            )
        return job_id, False

    def next_job(self, deadline, daily_limit=10):
        with self.database.transaction() as conn:
            job = row_dict(
                conn.execute(
                    "SELECT * FROM agent_research_jobs WHERE status='queued' "
                    "ORDER BY created_at,job_id LIMIT 1"
                )
            )
            if job is None:
                return None
            today = self.clock().date()
            reserved = conn.execute(
                "SELECT quota_date FROM agent_research_attempts WHERE job_id=? AND attempt_no=?",
                [job["job_id"], job["attempt_no"]],
            ).fetchone()[0]
            if reserved != today:
                count = conn.execute(
                    "SELECT count(*) FROM agent_research_attempts WHERE quota_date=?",
                    [today],
                ).fetchone()[0]
                if count >= daily_limit:
                    conn.execute(
                        "UPDATE agent_research_jobs SET status='failed',phase='failed',"
                        "error_code='quota_exceeded' WHERE job_id=?",
                        [job["job_id"]],
                    )
                    conn.execute(
                        "UPDATE agent_research_attempts SET execution_status='failed',"
                        "completed_at=? WHERE job_id=? AND attempt_no=?",
                        [self.clock(), job["job_id"], job["attempt_no"]],
                    )
                    return None
                # Only an unstarted reservation moves to the actual launch date.
                conn.execute(
                    "UPDATE agent_research_attempts SET quota_date=? "
                    "WHERE job_id=? AND attempt_no=?",
                    [today, job["job_id"], job["attempt_no"]],
                )
            conn.execute(
                "UPDATE agent_research_jobs SET status='running',phase='research',deadline=? "
                "WHERE job_id=?",
                [deadline, job["job_id"]],
            )
            conn.execute(
                "UPDATE agent_research_attempts SET execution_status='running' "
                "WHERE job_id=? AND attempt_no=?",
                [job["job_id"], job["attempt_no"]],
            )
        return self.get(job["job_id"])

    def finish(self, job_id, attempt, status, *, error=None, report=None, usage=None):
        with self.database.transaction() as conn:
            job = row_dict(
                conn.execute("SELECT * FROM agent_research_jobs WHERE job_id=?", [job_id])
            )
            if not job or job["attempt_no"] != attempt or job["status"] not in ACTIVE:
                return False
            if job["status"] == "cancel_requested":
                status, error, report, usage = "cancelled", "cancelled", None, None
            conn.execute(
                "UPDATE agent_research_jobs SET status=?,phase=?,error_code=? WHERE job_id=?",
                [status, status, error, job_id],
            )
            conn.execute(
                """UPDATE agent_research_attempts SET execution_status=?,report_json=?,
                usage_json=?,completed_at=? WHERE job_id=? AND attempt_no=?""",
                [
                    status,
                    canonical(report) if report else None,
                    canonical(usage) if usage else None,
                    self.clock(),
                    job_id,
                    attempt,
                ],
            )
        return True

    def cancel(self, job_id):
        with self.database.transaction() as conn:
            row = row_dict(
                conn.execute("SELECT * FROM agent_research_jobs WHERE job_id=?", [job_id])
            )
            if row is None:
                raise ResearchError("job_not_found", 404)
            if row["status"] == "queued":
                conn.execute(
                    "UPDATE agent_research_jobs SET status='cancelled',phase='cancelled',"
                    "error_code='cancelled' WHERE job_id=?",
                    [job_id],
                )
                conn.execute(
                    "UPDATE agent_research_attempts SET execution_status='cancelled',"
                    "completed_at=? WHERE job_id=? AND attempt_no=?",
                    [self.clock(), job_id, row["attempt_no"]],
                )
            elif row["status"] == "running":
                conn.execute(
                    "UPDATE agent_research_jobs SET status='cancel_requested' WHERE job_id=?",
                    [job_id],
                )

    def recover(self):
        with self.database.transaction() as conn:
            conn.execute(
                """UPDATE agent_research_attempts SET execution_status='interrupted',
                completed_at=? WHERE execution_status IN ('queued','running')""",
                [self.clock()],
            )
            conn.execute("""UPDATE agent_research_jobs SET status='interrupted',phase='interrupted',
                error_code='interrupted' WHERE status IN ('queued','running','cancel_requested')""")

    def decoded(self, job_id):
        row = self.get(job_id)
        for column in ("report_json", "usage_json"):
            value = row.pop(column)
            row[column.removesuffix("_json")] = json.loads(value) if value else None
        return row
