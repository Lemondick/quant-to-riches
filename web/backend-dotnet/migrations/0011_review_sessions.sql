CREATE TABLE review_sessions(
 user_id TEXT NOT NULL REFERENCES users(id),
 id TEXT NOT NULL,
 kind TEXT NOT NULL,
 payload JSONB NOT NULL,
 source_fingerprint TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL,
 result JSONB,
 PRIMARY KEY(user_id,id)
);
CREATE INDEX review_sessions_expiry ON review_sessions(expires_at);
