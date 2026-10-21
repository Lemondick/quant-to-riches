namespace QuantAssistant.Core.Errors;

/// <summary>
/// 对外稳定错误码。与旧 Python 站点 app.py 的 ERROR_MESSAGES 键一一对应，
/// 迁移后不得新增/重命名，以保持旧接口错误契约（计划书 §3「接口与前端兼容」）。
/// </summary>
public static class ErrorCodes
{
    public const string DecisionRunNotFound = "decision_run_not_found";
    public const string TransactionNotFound = "transaction_not_found";
    public const string DividendNotFound = "dividend_not_found";
    public const string DatabaseUnavailable = "database_unavailable";
    public const string InvalidRequest = "invalid_request";
    public const string MissingCashPosition = "missing_cash_position";
    public const string InsufficientCash = "insufficient_cash";
    public const string InsufficientPosition = "insufficient_position";
    public const string NavBaselinePending = "nav_baseline_pending";
    public const string RouteNotFound = "route_not_found";
    public const string MethodNotAllowed = "method_not_allowed";
    public const string AuthenticationRequired = "authentication_required";
    public const string RateLimited = "rate_limited";
    public const string InternalError = "internal_error";
    public const string FundLotsUnconfirmed = "fund_lots_unconfirmed";
    public const string FundLotsStale = "fund_lots_stale";
    public const string FundLotsMismatch = "fund_lots_mismatch";
    public const string FundLotsSelectionInvalid = "fund_lots_selection_invalid";
    public const string FeeEvidenceUnavailable = "fee_evidence_unavailable";
    public const string AdvisorReportActive = "advisor_report_active";
    public const string AdvisorSettingsChanged = "advisor_settings_changed";
    public const string LedgerCorrectionCostMissing = "ledger_correction_cost_missing";
    public const string LedgerCorrectionDateInvalid = "ledger_correction_date_invalid";
    public const string LedgerCorrectionDependentEvents = "ledger_correction_dependent_events";

    // batch2 新增错误码（任务1/5/7/11）。原注释称"不得新增"，batch2 经任务铁律授权新增：
    // 新码先在 Core 注册，用户侧写操作仍走 user_operation_audit 审计。
    public const string RebalanceTargetNotFound = "rebalance_target_not_found";
    public const string SmartDcaPlanNotFound = "smart_dca_plan_not_found";
    public const string TwoFactorRequired = "two_factor_required";
    public const string InvalidTotpCode = "invalid_totp_code";
    public const string AuditChainNotEnabled = "audit_chain_not_enabled";
}
