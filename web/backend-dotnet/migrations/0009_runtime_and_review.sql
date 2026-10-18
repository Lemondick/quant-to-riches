-- Persist legacy runtime settings and manual ledger review evidence per account.
CREATE TABLE alert_center_settings (
    user_id text NOT NULL REFERENCES users(id), id integer NOT NULL,
    payload json NOT NULL, PRIMARY KEY(user_id,id)
);
CREATE TABLE model_cost_settings (
    user_id text NOT NULL REFERENCES users(id), id integer NOT NULL,
    payload json NOT NULL, PRIMARY KEY(user_id,id)
);
CREATE TABLE model_call_costs (
    user_id text NOT NULL REFERENCES users(id), id varchar NOT NULL,
    called_at timestamptz NOT NULL, model varchar NOT NULL, status varchar NOT NULL,
    prices_json json, currency varchar, input_tokens bigint, output_tokens bigint,
    reserved_cost varchar, cost varchar, PRIMARY KEY(user_id,id)
);
CREATE TABLE user_alert_center (
    user_id text NOT NULL REFERENCES users(id), id varchar NOT NULL,
    kind varchar NOT NULL, subject varchar NOT NULL, detail json NOT NULL,
    first_seen timestamptz NOT NULL, last_seen timestamptz NOT NULL,
    active boolean NOT NULL, acknowledged boolean NOT NULL,
    PRIMARY KEY(user_id,id), UNIQUE(user_id,kind,subject)
);
CREATE TABLE ledger_replay_previews (
    user_id text NOT NULL REFERENCES users(id), preview_id varchar NOT NULL,
    source_fingerprint varchar NOT NULL, plan_json json NOT NULL, result_json json NOT NULL,
    created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
    consumed_at timestamptz, transaction_id bigint, PRIMARY KEY(user_id,preview_id)
);
CREATE TABLE ledger_reconciliations (
    user_id text NOT NULL REFERENCES users(id), reconciliation_id varchar NOT NULL,
    created_at timestamptz NOT NULL, source_fingerprint varchar NOT NULL, result_json json NOT NULL,
    resolution varchar, resolution_note varchar, resolved_at timestamptz,
    PRIMARY KEY(user_id,reconciliation_id)
);
CREATE INDEX model_call_costs_daily ON model_call_costs(user_id,called_at);
CREATE INDEX user_alert_center_active ON user_alert_center(user_id,active);
