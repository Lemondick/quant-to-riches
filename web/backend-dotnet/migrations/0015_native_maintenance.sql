CREATE TABLE native_monitor_samples (
 captured_at TIMESTAMPTZ PRIMARY KEY,
 payload JSONB NOT NULL
);
CREATE TABLE native_maintenance_state (
 id INTEGER PRIMARY KEY CHECK(id=1),
 completed_at TIMESTAMPTZ NOT NULL,
 previews_deleted INTEGER NOT NULL,
 public_jobs_deleted INTEGER NOT NULL,
 samples_deleted INTEGER NOT NULL
);
CREATE INDEX compute_public_retention ON compute_jobs(completed_at)
 WHERE status IN ('completed','failed','cancelled','interrupted')
 AND kind IN ('market.snapshot','market.quotes','market.news','fund.detail','fund.valuations');
