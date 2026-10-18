CREATE TABLE site_restore_runs (
    archive_sha256 TEXT PRIMARY KEY,
    destination TEXT NOT NULL,
    staging TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('database_restored','prepared','published')),
    database_fingerprint TEXT,
    restored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
