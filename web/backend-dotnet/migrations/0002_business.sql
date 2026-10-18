-- 0002_business.sql — 业务域（来自旧每用户 DuckDB schema.sql，合并为单 PG 库，加 user_id 隔离维度）
-- 规则：user_id 首列 + 复合主键/唯一约束含 user_id；DOUBLE -> DOUBLE PRECISION；全局序列 nextval。
-- 排除表（保留在 DuckDB/Parquet，Python 分析域，计划书表1）：
--   backtest_nav, backtest_runs, daily_prices, fund_holding_history, fund_lifecycle_events, fund_manager_history, fund_nav, fund_profile_history, fund_style_drift_events, fund_valuation_history, instrument_history, stock_rankings
-- 本文件由 tools/translate_business_schema.py 生成，只读分析，不改产品代码。

-- 全局自增序列（跨用户共享；导入后校准到 max(id)+1，见计划书 §4）。
CREATE SEQUENCE IF NOT EXISTS account_event_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS audit_log_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS corporate_action_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS data_snapshot_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS dividend_event_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS factor_alert_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS fee_schedule_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS fund_lot_snapshot_sequence START 1;
CREATE SEQUENCE IF NOT EXISTS ledger_correction_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS macro_observation_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS operational_alert_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS proposed_order_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS signal_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS transaction_id_seq START 1;

CREATE TABLE agent_preview_snapshots (
    user_id TEXT NOT NULL REFERENCES users (id),
    snapshot_id VARCHAR,
    content_hash VARCHAR NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, snapshot_id)
);
CREATE TABLE agent_research_jobs (
    user_id TEXT NOT NULL REFERENCES users (id),
    job_id VARCHAR,
    cache_key VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    attempt_no INTEGER NOT NULL,
    phase VARCHAR NOT NULL,
    deadline TIMESTAMPTZ,
    error_code VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, job_id),
    UNIQUE (user_id, cache_key)
);
CREATE TABLE agent_research_attempts (
    user_id TEXT NOT NULL REFERENCES users (id),
    job_id VARCHAR NOT NULL,
    attempt_no INTEGER NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    quota_date DATE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    execution_status VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    report_json JSON,
    usage_json JSON,
    model VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    upstream_commit VARCHAR NOT NULL,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, job_id, attempt_no)
);
CREATE TABLE data_snapshots (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('data_snapshot_id_seq'),
    dataset VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    status VARCHAR NOT NULL CHECK (status IN ('valid', 'stale', 'blocked')),
    issues_json JSON NOT NULL DEFAULT '[]',
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, dataset, source, as_of_date, sha256)
);
CREATE TABLE instruments (
    user_id TEXT NOT NULL REFERENCES users (id),
    symbol VARCHAR,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index', 'cash')),
    name VARCHAR NOT NULL,
    exchange VARCHAR,
    industry VARCHAR,
    board VARCHAR,
    listed_on DATE,
    delisted_on DATE,
    is_st BOOLEAN NOT NULL DEFAULT FALSE,
    is_suspended BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR,
    as_of_date DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (user_id, symbol)
);
CREATE TABLE fund_lot_snapshots (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('fund_lot_snapshot_sequence'),
    symbol VARCHAR NOT NULL,
    position_fingerprint VARCHAR NOT NULL,
    request_fingerprint VARCHAR NOT NULL,
    verified_on DATE NOT NULL,
    evidence_reference VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, request_fingerprint)
);
CREATE TABLE fund_holding_lots (
    user_id TEXT NOT NULL REFERENCES users (id),
    snapshot_id BIGINT NOT NULL,
    lot_id VARCHAR NOT NULL,
    confirmed_on DATE NOT NULL,
    remaining_units DECIMAL(28, 8) NOT NULL CHECK (remaining_units > 0),
    PRIMARY KEY (user_id, snapshot_id, lot_id)
);
CREATE TABLE fund_fee_schedules (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('fee_schedule_id_seq'),
    symbol VARCHAR NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    purchase_fee_rate DECIMAL(18, 10) NOT NULL DEFAULT 0,
    service_fee_rate_annual DECIMAL(18, 10) NOT NULL DEFAULT 0,
    redemption_tiers_json JSON NOT NULL,
    source VARCHAR NOT NULL,
    verified_on DATE NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, symbol, effective_from)
);
CREATE TABLE fund_share_families (
    user_id TEXT NOT NULL REFERENCES users (id),
    family_id VARCHAR NOT NULL,
    symbol VARCHAR,
    share_class VARCHAR NOT NULL,
    benchmark_symbol VARCHAR,
    fund_type VARCHAR,
    is_purchasable BOOLEAN NOT NULL DEFAULT TRUE,
    is_redeemable BOOLEAN NOT NULL DEFAULT TRUE,
    purchase_limit DECIMAL(20, 4),
    as_of_date DATE NOT NULL,
    PRIMARY KEY (user_id, symbol)
);
CREATE TABLE positions (
    user_id TEXT NOT NULL REFERENCES users (id),
    symbol VARCHAR,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index', 'cash')),
    quantity DECIMAL(28, 8) NOT NULL,
    average_cost DECIMAL(20, 6) NOT NULL,
    market_value DECIMAL(20, 4) NOT NULL,
    as_of_date DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (user_id, symbol)
);
CREATE TABLE portfolio_accounts (
    user_id TEXT NOT NULL REFERENCES users (id),
    account_id VARCHAR,
    name VARCHAR NOT NULL,
    platform VARCHAR NOT NULL,
    currency VARCHAR NOT NULL DEFAULT 'CNY',
    status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (user_id, account_id)
);
CREATE TABLE account_positions (
    user_id TEXT NOT NULL REFERENCES users (id),
    account_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index', 'cash')),
    quantity DECIMAL(28, 8) NOT NULL,
    average_cost DECIMAL(20, 6) NOT NULL,
    market_value DECIMAL(20, 4) NOT NULL,
    as_of_date DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (user_id, account_id, symbol)
);
CREATE TABLE account_events (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('account_event_id_seq'),
    account_id VARCHAR NOT NULL,
    event_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL CHECK (event_type IN ('snapshot', 'archive', 'rename')),
    effective_date DATE NOT NULL,
    reason VARCHAR NOT NULL,
    details_json JSON NOT NULL DEFAULT '{}',
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, account_id, event_id)
);
CREATE TABLE transactions (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('transaction_id_seq'),
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index', 'cash')),
    action VARCHAR NOT NULL CHECK (action IN ('buy', 'add', 'hold', 'reduce', 'sell', 'no_trade')),
    quantity DECIMAL(28, 8) NOT NULL,
    price DECIMAL(20, 6) NOT NULL,
    fees DECIMAL(20, 4) NOT NULL DEFAULT 0,
    traded_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    note VARCHAR,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE dividend_events (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('dividend_event_id_seq'),
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund')),
    gross_amount DECIMAL(28, 8) NOT NULL CHECK (gross_amount > 0),
    fees DECIMAL(20, 4) NOT NULL DEFAULT 0 CHECK (fees >= 0),
    net_amount DECIMAL(28, 8) NOT NULL CHECK (net_amount > 0),
    paid_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    source VARCHAR NOT NULL,
    external_id VARCHAR NOT NULL,
    cash_symbol VARCHAR NOT NULL,
    note VARCHAR,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, source, external_id)
);
CREATE TABLE corporate_action_events (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('corporate_action_id_seq'),
    action_type VARCHAR NOT NULL CHECK (action_type IN (
        'dividend_reinvestment', 'fund_conversion', 'split', 'custody_transfer'
    )),
    symbol VARCHAR NOT NULL,
    target_symbol VARCHAR,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund')),
    quantity DECIMAL(28, 8),
    target_quantity DECIMAL(28, 8),
    price DECIMAL(20, 6),
    target_price DECIMAL(20, 6),
    gross_amount DECIMAL(28, 8) NOT NULL DEFAULT 0,
    fees DECIMAL(20, 4) NOT NULL DEFAULT 0 CHECK (fees >= 0),
    cash_delta DECIMAL(28, 8) NOT NULL DEFAULT 0,
    ratio DECIMAL(28, 10),
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    source VARCHAR NOT NULL,
    external_id VARCHAR NOT NULL,
    cash_symbol VARCHAR,
    from_account VARCHAR,
    to_account VARCHAR,
    note VARCHAR,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, source, external_id)
);
CREATE TABLE ledger_corrections (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('ledger_correction_id_seq'),
    target_type VARCHAR NOT NULL CHECK (target_type IN ('transaction', 'dividend')),
    target_id BIGINT NOT NULL,
    reason VARCHAR NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    source VARCHAR NOT NULL,
    external_id VARCHAR NOT NULL,
    note VARCHAR,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, target_type, target_id),
    UNIQUE (user_id, source, external_id)
);
CREATE TABLE strategy_runs (
    user_id TEXT NOT NULL REFERENCES users (id),
    run_id VARCHAR,
    as_of_date DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    data_snapshot_id BIGINT,
    strategy_version VARCHAR NOT NULL,
    parameters_json JSON NOT NULL DEFAULT '{}',
    data_status VARCHAR NOT NULL CHECK (data_status IN ('valid', 'stale', 'blocked')),
    readiness VARCHAR NOT NULL CHECK (readiness IN ('research_only', 'paper', 'reference')),
    status VARCHAR NOT NULL,
    error_message VARCHAR,
    PRIMARY KEY (user_id, run_id)
);
CREATE TABLE signals (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('signal_id_seq'),
    run_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    target_weight DECIMAL(12, 8) NOT NULL,
    reasons_json JSON NOT NULL,
    as_of_date DATE NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    strategy_version VARCHAR NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, run_id, symbol)
);
CREATE TABLE proposed_orders (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('proposed_order_id_seq'),
    run_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    amount DECIMAL(20, 4) NOT NULL,
    quantity DECIMAL(28, 8),
    target_weight DECIMAL(12, 8) NOT NULL,
    reasons_json JSON NOT NULL,
    as_of_date DATE NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    data_snapshot_id BIGINT NOT NULL,
    strategy_version VARCHAR NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, run_id, symbol)
);
CREATE TABLE portfolio_events (
    user_id TEXT NOT NULL REFERENCES users (id),
    event_id VARCHAR,
    event_type VARCHAR NOT NULL CHECK (
        event_type IN ('baseline', 'deposit', 'withdrawal', 'calibration', 'rebase')
    ),
    effective_date DATE NOT NULL,
    amount DECIMAL(28, 8),
    status VARCHAR NOT NULL CHECK (status IN ('confirmed', 'pending', 'void')),
    reason VARCHAR NOT NULL,
    details_json JSON NOT NULL DEFAULT '{}',
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, event_id)
);
CREATE TABLE portfolio_valuations (
    user_id TEXT NOT NULL REFERENCES users (id),
    run_id VARCHAR,
    as_of_date DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    data_snapshot_id BIGINT NOT NULL,
    total_value DECIMAL(28, 8) NOT NULL CHECK (total_value > 0),
    cash DECIMAL(28, 8) NOT NULL CHECK (cash >= 0),
    unit_nav DECIMAL(28, 12) NOT NULL CHECK (unit_nav > 0),
    high_water_mark DECIMAL(28, 12) NOT NULL CHECK (
        high_water_mark >= unit_nav
    ),
    net_external_flow DECIMAL(28, 8) NOT NULL DEFAULT 0,
    previous_run_id VARCHAR,
    positions_fingerprint VARCHAR NOT NULL DEFAULT '',
    events_fingerprint VARCHAR NOT NULL DEFAULT '',
    is_baseline BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (user_id, run_id)
);
CREATE TABLE paper_orders (
    user_id TEXT NOT NULL REFERENCES users (id),
    order_id VARCHAR,
    proposal_id BIGINT NOT NULL,
    run_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund')),
    action VARCHAR NOT NULL CHECK (action IN ('buy', 'add', 'reduce', 'sell')),
    requested_amount DECIMAL(20, 4) NOT NULL CHECK (requested_amount >= 0),
    requested_quantity DECIMAL(28, 8),
    status VARCHAR NOT NULL CHECK (
        status IN (
            'draft', 'approved', 'submitted', 'partially_filled',
            'filled', 'cancelled', 'rejected', 'expired'
        )
    ),
    valid_until TIMESTAMPTZ NOT NULL,
    idempotency_key VARCHAR NOT NULL,
    note VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, order_id),
    UNIQUE (user_id, idempotency_key)
);
CREATE TABLE paper_fills (
    user_id TEXT NOT NULL REFERENCES users (id),
    fill_id VARCHAR,
    order_id VARCHAR NOT NULL,
    quantity DECIMAL(28, 8) NOT NULL CHECK (quantity > 0),
    price DECIMAL(20, 6) NOT NULL CHECK (price > 0),
    fees DECIMAL(20, 4) NOT NULL CHECK (fees >= 0),
    filled_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    note VARCHAR,
    PRIMARY KEY (user_id, fill_id)
);
CREATE TABLE paper_reconciliations (
    user_id TEXT NOT NULL REFERENCES users (id),
    reconciliation_id VARCHAR,
    order_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('matched', 'mismatch', 'missing')),
    details_json JSON NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, reconciliation_id)
);
CREATE TABLE operation_locks (
    user_id TEXT NOT NULL REFERENCES users (id),
    lock_name VARCHAR,
    owner_token VARCHAR NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, lock_name)
);
CREATE TABLE operational_alerts (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('operational_alert_id_seq'),
    alert_key VARCHAR NOT NULL,
    severity VARCHAR NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    message VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('open', 'resolved')),
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    last_notified_at TIMESTAMPTZ,
    notification_fingerprint VARCHAR,
    silenced_until TIMESTAMPTZ,
    details_json JSON NOT NULL DEFAULT '{}',
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, alert_key, status)
);
CREATE TABLE drawdown_validations (
    user_id TEXT NOT NULL REFERENCES users (id),
    run_id VARCHAR,
    threshold DOUBLE PRECISION NOT NULL CHECK (threshold > 0 AND threshold <= 1),
    max_drawdown DOUBLE PRECISION NOT NULL CHECK (max_drawdown >= 0 AND max_drawdown <= 1),
    observed_sessions INTEGER NOT NULL CHECK (observed_sessions >= 0),
    breach_count INTEGER NOT NULL CHECK (breach_count >= 0),
    passed BOOLEAN NOT NULL,
    research_only BOOLEAN NOT NULL DEFAULT TRUE,
    reasons_json JSON NOT NULL DEFAULT '[]',
    validated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, run_id)
);
CREATE TABLE cash_flow_plans (
    user_id TEXT NOT NULL REFERENCES users (id),
    plan_id VARCHAR,
    name VARCHAR NOT NULL,
    direction VARCHAR NOT NULL CHECK (direction IN ('deposit', 'withdrawal')),
    amount DECIMAL(28, 8) NOT NULL CHECK (amount > 0),
    frequency VARCHAR NOT NULL CHECK (frequency IN ('once', 'weekly', 'monthly', 'quarterly', 'annual')),
    start_date DATE NOT NULL,
    end_date DATE,
    status VARCHAR NOT NULL CHECK (status IN ('active', 'paused', 'completed')),
    note VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (end_date IS NULL OR end_date >= start_date),
    PRIMARY KEY (user_id, plan_id)
);
CREATE TABLE factor_observations (
    user_id TEXT NOT NULL REFERENCES users (id),
    factor VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    ic DOUBLE PRECISION,
    ir DOUBLE PRECISION,
    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
    source VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, factor, as_of_date)
);
CREATE TABLE factor_alerts (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('factor_alert_id_seq'),
    factor VARCHAR NOT NULL,
    alert_type VARCHAR NOT NULL CHECK (alert_type IN ('decay', 'missing', 'unstable')),
    severity VARCHAR NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    message VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('open', 'resolved')),
    details_json JSON NOT NULL DEFAULT '{}',
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, factor, alert_type, observed_at)
);
CREATE TABLE benchmark_configs (
    user_id TEXT NOT NULL REFERENCES users (id),
    benchmark_id VARCHAR,
    name VARCHAR NOT NULL,
    components_json JSON NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, benchmark_id)
);
CREATE TABLE macro_observations (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('macro_observation_id_seq'),
    indicator VARCHAR NOT NULL,
    observed_on DATE NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, id),
    UNIQUE (user_id, indicator, observed_on, source)
);
CREATE TABLE job_heartbeats (
    user_id TEXT NOT NULL REFERENCES users (id),
    job_name VARCHAR,
    expected_interval_minutes INTEGER NOT NULL CHECK (expected_interval_minutes > 0),
    last_started_at TIMESTAMPTZ,
    last_completed_at TIMESTAMPTZ,
    last_status VARCHAR NOT NULL CHECK (last_status IN ('running', 'completed', 'failed', 'blocked')),
    last_error VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, job_name)
);
CREATE TABLE audit_log (
    user_id TEXT NOT NULL REFERENCES users (id),
    id BIGINT DEFAULT nextval('audit_log_id_seq'),
    entity_type VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    before_json JSON,
    after_json JSON,
    actor VARCHAR NOT NULL DEFAULT 'local_user',
    PRIMARY KEY (user_id, id)
);
CREATE TABLE market_advisor_reports (
    user_id TEXT NOT NULL REFERENCES users (id),
    report_id VARCHAR,
    report_date DATE NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('morning', 'afternoon')),
    status VARCHAR NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'blocked', 'failed')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    input_json JSON,
    report_json JSON,
    error_code VARCHAR,
    PRIMARY KEY (user_id, report_id)
);
CREATE TABLE advisor_model_settings (
    user_id TEXT NOT NULL REFERENCES users (id),
    id INTEGER CHECK (id=1),
    models_json JSON NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE advisor_decision_snapshots (
    user_id TEXT NOT NULL REFERENCES users (id),
    report_id VARCHAR,
    captured_at TIMESTAMPTZ NOT NULL,
    snapshot_json JSON NOT NULL,
    fingerprint VARCHAR NOT NULL,
    PRIMARY KEY (user_id, report_id)
);
CREATE TABLE fund_research_snapshots (
    user_id TEXT NOT NULL REFERENCES users (id),
    fingerprint VARCHAR,
    symbol VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    snapshot_json JSON NOT NULL,
    PRIMARY KEY (user_id, fingerprint)
);
CREATE TABLE advisor_schedule_runs (
    user_id TEXT NOT NULL REFERENCES users (id),
    run_date DATE NOT NULL,
    kind VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    report_id VARCHAR,
    error_code VARCHAR,
    PRIMARY KEY (user_id, run_date, kind)
);
CREATE TABLE advisor_provider_selection (
    user_id TEXT NOT NULL REFERENCES users (id),
    id INTEGER,
    provider_revision INTEGER NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE advisor_notifications (
    user_id TEXT NOT NULL REFERENCES users (id),
    id VARCHAR,
    kind VARCHAR NOT NULL,
    report_id VARCHAR,
    status VARCHAR NOT NULL,
    error_code VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (user_id, id)
);

-- 增量迁移列（ALTER TABLE ... ADD COLUMN，来自旧 DuckDB 的增量迁移）。
ALTER TABLE fund_fee_schedules ADD COLUMN IF NOT EXISTS terms_json JSON DEFAULT '{}';
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS source VARCHAR;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS external_id VARCHAR;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS gross_amount DECIMAL(28, 8);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS cash_delta DECIMAL(28, 8);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS cash_symbol VARCHAR;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS reversed_by_correction_id BIGINT;
ALTER TABLE dividend_events ADD COLUMN IF NOT EXISTS reversed_by_correction_id BIGINT;
ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS net_external_flow DECIMAL(28, 8) DEFAULT 0;
ALTER TABLE portfolio_valuations ADD COLUMN IF NOT EXISTS previous_run_id VARCHAR;
ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS positions_fingerprint VARCHAR DEFAULT '';
ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS events_fingerprint VARCHAR DEFAULT '';
ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS is_baseline BOOLEAN DEFAULT FALSE;
ALTER TABLE operational_alerts ADD COLUMN IF NOT EXISTS last_notified_at TIMESTAMPTZ;
ALTER TABLE operational_alerts ADD COLUMN IF NOT EXISTS notification_fingerprint VARCHAR;
ALTER TABLE operational_alerts ADD COLUMN IF NOT EXISTS silenced_until TIMESTAMPTZ;
ALTER TABLE market_advisor_reports ADD COLUMN IF NOT EXISTS progress_json JSON;
ALTER TABLE market_advisor_reports ADD COLUMN IF NOT EXISTS model_config_json JSON;

-- 用户隔离索引：每张业务表按 user_id 建索引（串账户防护，计划书 §7「跨账号ID访问被拒绝」）。
CREATE INDEX IF NOT EXISTS idx_agent_preview_snapshots_user ON agent_preview_snapshots (user_id);
CREATE INDEX IF NOT EXISTS idx_agent_research_jobs_user ON agent_research_jobs (user_id);
CREATE INDEX IF NOT EXISTS idx_agent_research_attempts_user ON agent_research_attempts (user_id);
CREATE INDEX IF NOT EXISTS idx_data_snapshots_user ON data_snapshots (user_id);
CREATE INDEX IF NOT EXISTS idx_instruments_user ON instruments (user_id);
CREATE INDEX IF NOT EXISTS idx_fund_lot_snapshots_user ON fund_lot_snapshots (user_id);
CREATE INDEX IF NOT EXISTS idx_fund_holding_lots_user ON fund_holding_lots (user_id);
CREATE INDEX IF NOT EXISTS idx_fund_fee_schedules_user ON fund_fee_schedules (user_id);
CREATE INDEX IF NOT EXISTS idx_fund_share_families_user ON fund_share_families (user_id);
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions (user_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_accounts_user ON portfolio_accounts (user_id);
CREATE INDEX IF NOT EXISTS idx_account_positions_user ON account_positions (user_id);
CREATE INDEX IF NOT EXISTS idx_account_events_user ON account_events (user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_dividend_events_user ON dividend_events (user_id);
CREATE INDEX IF NOT EXISTS idx_corporate_action_events_user ON corporate_action_events (user_id);
CREATE INDEX IF NOT EXISTS idx_ledger_corrections_user ON ledger_corrections (user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_user ON strategy_runs (user_id);
CREATE INDEX IF NOT EXISTS idx_signals_user ON signals (user_id);
CREATE INDEX IF NOT EXISTS idx_proposed_orders_user ON proposed_orders (user_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_events_user ON portfolio_events (user_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_valuations_user ON portfolio_valuations (user_id);
CREATE INDEX IF NOT EXISTS idx_paper_orders_user ON paper_orders (user_id);
CREATE INDEX IF NOT EXISTS idx_paper_fills_user ON paper_fills (user_id);
CREATE INDEX IF NOT EXISTS idx_paper_reconciliations_user ON paper_reconciliations (user_id);
CREATE INDEX IF NOT EXISTS idx_operation_locks_user ON operation_locks (user_id);
CREATE INDEX IF NOT EXISTS idx_operational_alerts_user ON operational_alerts (user_id);
CREATE INDEX IF NOT EXISTS idx_drawdown_validations_user ON drawdown_validations (user_id);
CREATE INDEX IF NOT EXISTS idx_cash_flow_plans_user ON cash_flow_plans (user_id);
CREATE INDEX IF NOT EXISTS idx_factor_observations_user ON factor_observations (user_id);
CREATE INDEX IF NOT EXISTS idx_factor_alerts_user ON factor_alerts (user_id);
CREATE INDEX IF NOT EXISTS idx_benchmark_configs_user ON benchmark_configs (user_id);
CREATE INDEX IF NOT EXISTS idx_macro_observations_user ON macro_observations (user_id);
CREATE INDEX IF NOT EXISTS idx_job_heartbeats_user ON job_heartbeats (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_market_advisor_reports_user ON market_advisor_reports (user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_model_settings_user ON advisor_model_settings (user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_decision_snapshots_user ON advisor_decision_snapshots (user_id);
CREATE INDEX IF NOT EXISTS idx_fund_research_snapshots_user ON fund_research_snapshots (user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_schedule_runs_user ON advisor_schedule_runs (user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_provider_selection_user ON advisor_provider_selection (user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_notifications_user ON advisor_notifications (user_id);
