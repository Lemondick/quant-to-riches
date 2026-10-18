CREATE SEQUENCE IF NOT EXISTS data_snapshot_id_seq START 1;
-- AI research is separate from deterministic decisions and manual ledger state.
CREATE TABLE IF NOT EXISTS agent_preview_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    content_hash VARCHAR NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_research_jobs (
    job_id VARCHAR PRIMARY KEY,
    cache_key VARCHAR NOT NULL UNIQUE,
    symbol VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    attempt_no INTEGER NOT NULL,
    phase VARCHAR NOT NULL,
    deadline TIMESTAMPTZ,
    error_code VARCHAR,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_research_attempts (
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
    PRIMARY KEY (job_id, attempt_no)
);
CREATE SEQUENCE IF NOT EXISTS fee_schedule_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS transaction_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS signal_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS proposed_order_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS audit_log_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS dividend_event_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS operational_alert_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS account_event_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS corporate_action_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS ledger_correction_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS fund_lifecycle_event_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS fund_holding_history_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS fund_style_drift_event_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS factor_alert_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS benchmark_config_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS macro_observation_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS cash_flow_plan_id_seq START 1;

CREATE TABLE IF NOT EXISTS data_snapshots (
    id BIGINT PRIMARY KEY DEFAULT nextval('data_snapshot_id_seq'),
    dataset VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    status VARCHAR NOT NULL CHECK (status IN ('valid', 'stale', 'blocked')),
    issues_json JSON NOT NULL DEFAULT '[]',
    UNIQUE (dataset, source, as_of_date, sha256)
);

CREATE TABLE IF NOT EXISTS instruments (
    symbol VARCHAR PRIMARY KEY,
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS instrument_history (
    symbol VARCHAR NOT NULL,
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
    PRIMARY KEY (symbol, as_of_date)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    symbol VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume DOUBLE NOT NULL,
    amount DOUBLE NOT NULL,
    adjustment_factor DOUBLE,
    suspended BOOLEAN,
    upper_limit DOUBLE,
    lower_limit DOUBLE,
    source_snapshot_id BIGINT,
    PRIMARY KEY (symbol, trade_date)
);

ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS suspended BOOLEAN;
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS upper_limit DOUBLE;
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS lower_limit DOUBLE;

CREATE TABLE IF NOT EXISTS fund_nav (
    symbol VARCHAR NOT NULL,
    nav_date DATE NOT NULL,
    unit_nav DOUBLE NOT NULL,
    accumulated_nav DOUBLE,
    source_snapshot_id BIGINT,
    PRIMARY KEY (symbol, nav_date)
);

-- Manually reconciled remaining lots are independent evidence, never trades.
CREATE SEQUENCE IF NOT EXISTS fund_lot_snapshot_sequence START 1;
CREATE TABLE IF NOT EXISTS fund_lot_snapshots (
    id BIGINT PRIMARY KEY DEFAULT nextval('fund_lot_snapshot_sequence'),
    symbol VARCHAR NOT NULL,
    position_fingerprint VARCHAR NOT NULL,
    request_fingerprint VARCHAR NOT NULL UNIQUE,
    verified_on DATE NOT NULL,
    evidence_reference VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS fund_holding_lots (
    snapshot_id BIGINT NOT NULL,
    lot_id VARCHAR NOT NULL,
    confirmed_on DATE NOT NULL,
    remaining_units DECIMAL(28, 8) NOT NULL CHECK (remaining_units > 0),
    PRIMARY KEY (snapshot_id, lot_id)
);

-- Point-in-time valuation evidence used by Smart DCA.  PE/PB are kept as
-- provider observations rather than recomputed from a future constituent set.
CREATE TABLE IF NOT EXISTS fund_valuation_history (
    symbol VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    pe DOUBLE,
    pb DOUBLE,
    source VARCHAR NOT NULL,
    source_snapshot_id BIGINT,
    PRIMARY KEY (symbol, as_of_date),
    CHECK (pe IS NOT NULL OR pb IS NOT NULL)
);

-- Manager evidence is versioned by its observation date.  Missing fields stay
-- NULL so multi-factor selection can fail closed instead of imputing a score.
CREATE TABLE IF NOT EXISTS fund_manager_history (
    symbol VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    manager_name VARCHAR,
    manager_tenure_years DOUBLE,
    manager_win_rate DOUBLE,
    alpha_stability DOUBLE,
    drawdown_recovery_days DOUBLE,
    source VARCHAR NOT NULL,
    source_snapshot_id BIGINT,
    PRIMARY KEY (symbol, as_of_date)
);

-- Public profile observations used only for lifecycle monitoring.  A row is
-- immutable for a symbol/date: repeat syncs are ignored rather than silently
-- rewriting the evidence that produced an alert.
CREATE TABLE IF NOT EXISTS fund_profile_history (
    symbol VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    manager_names VARCHAR,
    scale_text VARCHAR,
    scale_value DOUBLE,
    scale_unit VARCHAR,
    purchase_status VARCHAR,
    redemption_status VARCHAR,
    fees_json JSON NOT NULL DEFAULT '[]',
    fee_fingerprint VARCHAR,
    source VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_snapshot_id BIGINT,
    PRIMARY KEY (symbol, as_of_date)
);

CREATE TABLE IF NOT EXISTS fund_lifecycle_events (
    id BIGINT PRIMARY KEY DEFAULT nextval('fund_lifecycle_event_id_seq'),
    symbol VARCHAR NOT NULL,
    observed_on DATE NOT NULL,
    event_type VARCHAR NOT NULL CHECK (event_type IN (
        'manager_change', 'scale_drop', 'liquidation_risk',
        'purchase_suspension', 'redemption_suspension', 'fee_change'
    )),
    severity VARCHAR NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    message VARCHAR NOT NULL,
    previous_value VARCHAR,
    current_value VARCHAR,
    source VARCHAR NOT NULL,
    details_json JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (symbol, observed_on, event_type)
);

-- Top disclosed holdings are immutable evidence keyed by fund/report period.
-- A later sync may add a new report period but never rewrite an old one.
CREATE TABLE IF NOT EXISTS fund_holding_history (
    id BIGINT PRIMARY KEY DEFAULT nextval('fund_holding_history_id_seq'),
    symbol VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    holding_symbol VARCHAR NOT NULL,
    holding_name VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'bond')),
    weight DOUBLE NOT NULL CHECK (weight >= 0),
    source VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_snapshot_id BIGINT,
    UNIQUE (symbol, report_date, holding_symbol, asset_type)
);

CREATE TABLE IF NOT EXISTS fund_style_drift_events (
    id BIGINT PRIMARY KEY DEFAULT nextval('fund_style_drift_event_id_seq'),
    symbol VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    previous_report_date DATE NOT NULL,
    severity VARCHAR NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    overlap_ratio DOUBLE NOT NULL CHECK (overlap_ratio >= 0 AND overlap_ratio <= 1),
    message VARCHAR NOT NULL,
    added_json JSON NOT NULL DEFAULT '[]',
    removed_json JSON NOT NULL DEFAULT '[]',
    source VARCHAR NOT NULL,
    details_json JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (symbol, report_date)
);

CREATE TABLE IF NOT EXISTS fund_fee_schedules (
    id BIGINT PRIMARY KEY DEFAULT nextval('fee_schedule_id_seq'),
    symbol VARCHAR NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    purchase_fee_rate DECIMAL(18, 10) NOT NULL DEFAULT 0,
    service_fee_rate_annual DECIMAL(18, 10) NOT NULL DEFAULT 0,
    redemption_tiers_json JSON NOT NULL,
    source VARCHAR NOT NULL,
    verified_on DATE NOT NULL,
    UNIQUE (symbol, effective_from)
);

-- Additive migration: existing fee evidence remains readable and unchanged.
ALTER TABLE fund_fee_schedules ADD COLUMN IF NOT EXISTS terms_json JSON DEFAULT '{}';

CREATE TABLE IF NOT EXISTS fund_share_families (
    family_id VARCHAR NOT NULL,
    symbol VARCHAR PRIMARY KEY,
    share_class VARCHAR NOT NULL,
    benchmark_symbol VARCHAR,
    fund_type VARCHAR,
    is_purchasable BOOLEAN NOT NULL DEFAULT TRUE,
    is_redeemable BOOLEAN NOT NULL DEFAULT TRUE,
    purchase_limit DECIMAL(20, 4),
    as_of_date DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    symbol VARCHAR PRIMARY KEY,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index', 'cash')),
    quantity DECIMAL(28, 8) NOT NULL,
    average_cost DECIMAL(20, 6) NOT NULL,
    market_value DECIMAL(20, 4) NOT NULL,
    as_of_date DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

-- Optional multi-account mirror.  The original ``positions`` ledger remains
-- the single local portfolio used by NAV calculations; these tables keep
-- separately labelled external account snapshots without enabling login,
-- synchronization, or order submission.
CREATE TABLE IF NOT EXISTS portfolio_accounts (
    account_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    platform VARCHAR NOT NULL,
    currency VARCHAR NOT NULL DEFAULT 'CNY',
    status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS account_positions (
    account_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index', 'cash')),
    quantity DECIMAL(28, 8) NOT NULL,
    average_cost DECIMAL(20, 6) NOT NULL,
    market_value DECIMAL(20, 4) NOT NULL,
    as_of_date DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE IF NOT EXISTS account_events (
    id BIGINT PRIMARY KEY DEFAULT nextval('account_event_id_seq'),
    account_id VARCHAR NOT NULL,
    event_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL CHECK (event_type IN ('snapshot', 'archive', 'rename')),
    effective_date DATE NOT NULL,
    reason VARCHAR NOT NULL,
    details_json JSON NOT NULL DEFAULT '{}',
    recorded_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account_id, event_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id BIGINT PRIMARY KEY DEFAULT nextval('transaction_id_seq'),
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index', 'cash')),
    action VARCHAR NOT NULL CHECK (action IN ('buy', 'add', 'hold', 'reduce', 'sell', 'no_trade')),
    quantity DECIMAL(28, 8) NOT NULL,
    price DECIMAL(20, 6) NOT NULL,
    fees DECIMAL(20, 4) NOT NULL DEFAULT 0,
    traded_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    note VARCHAR
);

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS source VARCHAR;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS external_id VARCHAR;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS gross_amount DECIMAL(28, 8);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS cash_delta DECIMAL(28, 8);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS cash_symbol VARCHAR;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS reversed_by_correction_id BIGINT;

CREATE TABLE IF NOT EXISTS dividend_events (
    id BIGINT PRIMARY KEY DEFAULT nextval('dividend_event_id_seq'),
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
    UNIQUE (source, external_id)
);

ALTER TABLE dividend_events ADD COLUMN IF NOT EXISTS reversed_by_correction_id BIGINT;

CREATE TABLE IF NOT EXISTS corporate_action_events (
    id BIGINT PRIMARY KEY DEFAULT nextval('corporate_action_id_seq'),
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
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS ledger_corrections (
    id BIGINT PRIMARY KEY DEFAULT nextval('ledger_correction_id_seq'),
    target_type VARCHAR NOT NULL CHECK (target_type IN ('transaction', 'dividend')),
    target_id BIGINT NOT NULL,
    reason VARCHAR NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    source VARCHAR NOT NULL,
    external_id VARCHAR NOT NULL,
    note VARCHAR,
    UNIQUE (target_type, target_id),
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id VARCHAR PRIMARY KEY,
    as_of_date DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    data_snapshot_id BIGINT,
    strategy_version VARCHAR NOT NULL,
    parameters_json JSON NOT NULL DEFAULT '{}',
    data_status VARCHAR NOT NULL CHECK (data_status IN ('valid', 'stale', 'blocked')),
    readiness VARCHAR NOT NULL CHECK (readiness IN ('research_only', 'paper', 'reference')),
    status VARCHAR NOT NULL,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS signals (
    id BIGINT PRIMARY KEY DEFAULT nextval('signal_id_seq'),
    run_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    target_weight DECIMAL(12, 8) NOT NULL,
    reasons_json JSON NOT NULL,
    as_of_date DATE NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    strategy_version VARCHAR NOT NULL,
    UNIQUE (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS proposed_orders (
    id BIGINT PRIMARY KEY DEFAULT nextval('proposed_order_id_seq'),
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
    UNIQUE (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS stock_rankings (
    run_id VARCHAR NOT NULL,
    strategy_version VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    data_snapshot_id BIGINT NOT NULL,
    symbol VARCHAR NOT NULL,
    momentum_z DOUBLE NOT NULL,
    trend_z DOUBLE NOT NULL,
    low_volatility_z DOUBLE NOT NULL,
    liquidity_z DOUBLE NOT NULL,
    score DOUBLE NOT NULL,
    rank INTEGER NOT NULL CHECK (rank > 0),
    universe_size INTEGER NOT NULL CHECK (universe_size > 0),
    rank_percentile DOUBLE NOT NULL CHECK (
        rank_percentile > 0 AND rank_percentile <= 1
    ),
    PRIMARY KEY (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS portfolio_events (
    event_id VARCHAR PRIMARY KEY,
    event_type VARCHAR NOT NULL CHECK (
        event_type IN ('baseline', 'deposit', 'withdrawal', 'calibration', 'rebase')
    ),
    effective_date DATE NOT NULL,
    amount DECIMAL(28, 8),
    status VARCHAR NOT NULL CHECK (status IN ('confirmed', 'pending', 'void')),
    reason VARCHAR NOT NULL,
    details_json JSON NOT NULL DEFAULT '{}',
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_valuations (
    run_id VARCHAR PRIMARY KEY,
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
    is_baseline BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS net_external_flow DECIMAL(28, 8) DEFAULT 0;
ALTER TABLE portfolio_valuations ADD COLUMN IF NOT EXISTS previous_run_id VARCHAR;
ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS positions_fingerprint VARCHAR DEFAULT '';
ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS events_fingerprint VARCHAR DEFAULT '';
ALTER TABLE portfolio_valuations
    ADD COLUMN IF NOT EXISTS is_baseline BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id VARCHAR PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    strategy_version VARCHAR NOT NULL,
    parameters_json JSON NOT NULL,
    metrics_json JSON,
    readiness VARCHAR NOT NULL CHECK (readiness IN ('research_only', 'paper', 'reference')),
    limitations_json JSON NOT NULL DEFAULT '[]',
    status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_nav (
    run_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    total_value DECIMAL(28, 8),
    unit_nav DECIMAL(28, 12),
    cash DECIMAL(28, 8),
    PRIMARY KEY (run_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id VARCHAR PRIMARY KEY,
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
    idempotency_key VARCHAR NOT NULL UNIQUE,
    note VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id VARCHAR PRIMARY KEY,
    order_id VARCHAR NOT NULL,
    quantity DECIMAL(28, 8) NOT NULL CHECK (quantity > 0),
    price DECIMAL(20, 6) NOT NULL CHECK (price > 0),
    fees DECIMAL(20, 4) NOT NULL CHECK (fees >= 0),
    filled_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    note VARCHAR
);

CREATE TABLE IF NOT EXISTS paper_reconciliations (
    reconciliation_id VARCHAR PRIMARY KEY,
    order_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('matched', 'mismatch', 'missing')),
    details_json JSON NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_locks (
    lock_name VARCHAR PRIMARY KEY,
    owner_token VARCHAR NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS operational_alerts (
    id BIGINT PRIMARY KEY DEFAULT nextval('operational_alert_id_seq'),
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
    UNIQUE (alert_key, status)
);

-- Notification state is separate from the alert message so repeated scans can
-- be deduplicated without dropping the latest observation or audit details.
ALTER TABLE operational_alerts ADD COLUMN IF NOT EXISTS last_notified_at TIMESTAMPTZ;
ALTER TABLE operational_alerts ADD COLUMN IF NOT EXISTS notification_fingerprint VARCHAR;
ALTER TABLE operational_alerts ADD COLUMN IF NOT EXISTS silenced_until TIMESTAMPTZ;

-- Research and planning extensions.  These tables are deliberately isolated
-- from the trading ledger: none of them can create an order or mutate a
-- portfolio position.  Every observation keeps its source/date so a missing
-- or stale feed can fail closed at the API boundary.
CREATE TABLE IF NOT EXISTS drawdown_validations (
    run_id VARCHAR PRIMARY KEY,
    threshold DOUBLE NOT NULL CHECK (threshold > 0 AND threshold <= 1),
    max_drawdown DOUBLE NOT NULL CHECK (max_drawdown >= 0 AND max_drawdown <= 1),
    observed_sessions INTEGER NOT NULL CHECK (observed_sessions >= 0),
    breach_count INTEGER NOT NULL CHECK (breach_count >= 0),
    passed BOOLEAN NOT NULL,
    research_only BOOLEAN NOT NULL DEFAULT TRUE,
    reasons_json JSON NOT NULL DEFAULT '[]',
    validated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_flow_plans (
    plan_id VARCHAR PRIMARY KEY,
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
    CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS factor_observations (
    factor VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    ic DOUBLE,
    ir DOUBLE,
    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
    source VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (factor, as_of_date)
);

CREATE TABLE IF NOT EXISTS factor_alerts (
    id BIGINT PRIMARY KEY DEFAULT nextval('factor_alert_id_seq'),
    factor VARCHAR NOT NULL,
    alert_type VARCHAR NOT NULL CHECK (alert_type IN ('decay', 'missing', 'unstable')),
    severity VARCHAR NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    message VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('open', 'resolved')),
    details_json JSON NOT NULL DEFAULT '{}',
    UNIQUE (factor, alert_type, observed_at)
);

CREATE TABLE IF NOT EXISTS benchmark_configs (
    benchmark_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    components_json JSON NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS macro_observations (
    id BIGINT PRIMARY KEY DEFAULT nextval('macro_observation_id_seq'),
    indicator VARCHAR NOT NULL,
    observed_on DATE NOT NULL,
    value DOUBLE NOT NULL,
    unit VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (indicator, observed_on, source)
);

CREATE TABLE IF NOT EXISTS job_heartbeats (
    job_name VARCHAR PRIMARY KEY,
    expected_interval_minutes INTEGER NOT NULL CHECK (expected_interval_minutes > 0),
    last_started_at TIMESTAMPTZ,
    last_completed_at TIMESTAMPTZ,
    last_status VARCHAR NOT NULL CHECK (last_status IN ('running', 'completed', 'failed', 'blocked')),
    last_error VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT PRIMARY KEY DEFAULT nextval('audit_log_id_seq'),
    entity_type VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    before_json JSON,
    after_json JSON,
    actor VARCHAR NOT NULL DEFAULT 'local_user'
);

CREATE TABLE IF NOT EXISTS market_advisor_reports (
    report_id VARCHAR PRIMARY KEY,
    report_date DATE NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('morning', 'afternoon')),
    status VARCHAR NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'blocked', 'failed')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    input_json JSON,
    report_json JSON,
    error_code VARCHAR
);

ALTER TABLE market_advisor_reports ADD COLUMN IF NOT EXISTS progress_json JSON;
ALTER TABLE market_advisor_reports ADD COLUMN IF NOT EXISTS model_config_json JSON;

CREATE TABLE IF NOT EXISTS advisor_model_settings (
    id INTEGER PRIMARY KEY CHECK (id=1),
    models_json JSON NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- Research evidence only: never account, order, or paper-admission records.
CREATE TABLE IF NOT EXISTS advisor_decision_snapshots (
    report_id VARCHAR PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL,
    snapshot_json JSON NOT NULL,
    fingerprint VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS fund_research_snapshots (
    fingerprint VARCHAR PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    snapshot_json JSON NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fund_research_snapshots_date
    ON fund_research_snapshots(symbol, observed_at);

CREATE INDEX IF NOT EXISTS idx_snapshots_date ON data_snapshots(as_of_date, dataset);
CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(trade_date);
CREATE INDEX IF NOT EXISTS idx_instrument_history_date
    ON instrument_history(as_of_date, symbol);
CREATE INDEX IF NOT EXISTS idx_fund_nav_date ON fund_nav(nav_date);
CREATE INDEX IF NOT EXISTS idx_fund_valuation_date
    ON fund_valuation_history(as_of_date, symbol);
CREATE INDEX IF NOT EXISTS idx_fund_manager_date
    ON fund_manager_history(as_of_date, symbol);
CREATE INDEX IF NOT EXISTS idx_fund_profile_date
    ON fund_profile_history(as_of_date, symbol);
CREATE INDEX IF NOT EXISTS idx_fund_lifecycle_events_date
    ON fund_lifecycle_events(observed_on, symbol, severity);
CREATE INDEX IF NOT EXISTS idx_fund_holding_history_date
    ON fund_holding_history(symbol, report_date, holding_symbol);
CREATE INDEX IF NOT EXISTS idx_fund_style_drift_events_date
    ON fund_style_drift_events(symbol, report_date, severity);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_date ON strategy_runs(as_of_date);
CREATE INDEX IF NOT EXISTS idx_stock_rankings_history
    ON stock_rankings(strategy_version, symbol, as_of_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_valuations_date
    ON portfolio_valuations(as_of_date, generated_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_events_date
    ON portfolio_events(effective_date, event_type, status);
CREATE INDEX IF NOT EXISTS idx_backtest_nav_date
    ON backtest_nav(run_id, as_of_date);
CREATE INDEX IF NOT EXISTS idx_account_positions_date
    ON account_positions(account_id, as_of_date, symbol);
CREATE INDEX IF NOT EXISTS idx_account_events_date
    ON account_events(account_id, effective_date, recorded_at);
CREATE INDEX IF NOT EXISTS idx_paper_orders_run ON paper_orders(run_id, status);
CREATE INDEX IF NOT EXISTS idx_paper_fills_order ON paper_fills(order_id, filled_at);
CREATE INDEX IF NOT EXISTS idx_operational_alerts_status
    ON operational_alerts(status, severity, last_seen_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_external_id
    ON transactions(source, external_id);
CREATE INDEX IF NOT EXISTS idx_dividend_events_date
    ON dividend_events(paid_at, symbol);
CREATE INDEX IF NOT EXISTS idx_corporate_action_events_date
    ON corporate_action_events(effective_at, symbol);
CREATE INDEX IF NOT EXISTS idx_ledger_corrections_target
    ON ledger_corrections(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_drawdown_validations_date
    ON drawdown_validations(validated_at, passed);
CREATE INDEX IF NOT EXISTS idx_cash_flow_plans_status
    ON cash_flow_plans(status, start_date);
CREATE INDEX IF NOT EXISTS idx_factor_observations_date
    ON factor_observations(factor, as_of_date);
CREATE INDEX IF NOT EXISTS idx_factor_alerts_status
    ON factor_alerts(status, observed_at, severity);
CREATE INDEX IF NOT EXISTS idx_macro_observations_date
    ON macro_observations(indicator, observed_on);
CREATE TABLE IF NOT EXISTS advisor_schedule_runs (
    run_date DATE NOT NULL, kind VARCHAR NOT NULL, status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL, report_id VARCHAR, error_code VARCHAR,
    PRIMARY KEY(run_date, kind)
);
CREATE TABLE IF NOT EXISTS advisor_provider_selection (
    id INTEGER PRIMARY KEY,
    provider_revision INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS advisor_notifications (
    id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL, report_id VARCHAR,
    status VARCHAR NOT NULL, error_code VARCHAR, created_at TIMESTAMPTZ NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_job_heartbeats_status
    ON job_heartbeats(last_status, updated_at);
