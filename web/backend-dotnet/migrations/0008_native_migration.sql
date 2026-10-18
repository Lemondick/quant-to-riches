CREATE TABLE migration_batches (
    fingerprint TEXT PRIMARY KEY,
    format_version INTEGER NOT NULL,
    manifest_json JSONB NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE user_documents (
    user_id TEXT NOT NULL REFERENCES users(id),
    document_key TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id, document_key)
);
CREATE TABLE compute_jobs (
    user_id TEXT NOT NULL REFERENCES users(id),
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    input_json JSONB NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','cancel_requested','completed','failed','cancelled','interrupted')),
    attempt INTEGER NOT NULL DEFAULT 1,
    owner_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    deadline TIMESTAMPTZ NOT NULL,
    result_json JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY(user_id,job_id)
);
CREATE INDEX compute_job_queue ON compute_jobs(status,created_at);
CREATE TABLE worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    capabilities JSONB NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    stopping BOOLEAN NOT NULL DEFAULT false
);
