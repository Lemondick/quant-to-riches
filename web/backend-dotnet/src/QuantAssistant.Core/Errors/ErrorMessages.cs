namespace QuantAssistant.Core.Errors;

/// <summary>
/// 与旧站点一致的固定错误消息（app.py ERROR_MESSAGES）。
/// 迁移不改变这些文案，避免破坏用户可理解的错误提示（计划书 §3）。
/// </summary>
public static class ErrorMessages
{
    private static readonly IReadOnlyDictionary<string, string> Messages =
        new Dictionary<string, string>
        {
            [ErrorCodes.DecisionRunNotFound] = "未找到指定的决策运行。",
            [ErrorCodes.TransactionNotFound] = "未找到要冲正的成交记录。",
            [ErrorCodes.DividendNotFound] = "未找到要冲正的分红记录。",
            [ErrorCodes.DatabaseUnavailable] = "本地数据库暂不可用。",
            [ErrorCodes.InvalidRequest] = "请求参数无效。",
            [ErrorCodes.MissingCashPosition] = "尚未确认账户现金，请先导入完整账户并核对可用现金。账户未改动。",
            [ErrorCodes.InsufficientCash] = "可用现金不足以支付成交金额和费用，请核对现金余额或成交记录。账户未改动。",
            [ErrorCodes.InsufficientPosition] = "卖出份额超过已确认持仓，请核对持仓或成交记录。账户未改动。",
            [ErrorCodes.NavBaselinePending] = "账户快照已保存，但尚未完成该日期的净值入账。请先核对净值基线，再补录后续成交。账户未改动。",
            [ErrorCodes.RouteNotFound] = "未找到请求的接口。",
            [ErrorCodes.MethodNotAllowed] = "该接口不允许此请求方法。",
            [ErrorCodes.AuthenticationRequired] = "需要有效的访问凭据。",
            [ErrorCodes.RateLimited] = "请求过于频繁，请稍后重试。",
            [ErrorCodes.InternalError] = "服务器内部错误。",
            ["quota_exceeded"] = "当日研究次数已达上限，请明日再试。",
            ["job_not_found"] = "任务不存在。",
            ["operation_already_running"] = "已有任务正在执行，请稍后重试。",
            [ErrorCodes.FundLotsUnconfirmed] = "请先按确认单核对所有剩余批次，并明确确认。持仓未改动。",
            [ErrorCodes.FundLotsStale] = "持仓或批次已变化，请刷新并重新核对剩余批次。持仓未改动。",
            [ErrorCodes.FundLotsMismatch] = "批次剩余份额合计与已确认持仓不一致，请核对完整确认单。持仓未改动。",
            [ErrorCodes.FundLotsSelectionInvalid] = "选定的份额超过该批次余额，或重复选择同一批次。没有提交赎回。",
            [ErrorCodes.FeeEvidenceUnavailable] = "缺少在估算日期有效且30日内核对的实际费率，请先核对费用证据。",
            [ErrorCodes.AdvisorReportActive] = "已有AI报告正在执行，请等待完成后再创建新任务。没有发起新的模型调用。",
            [ErrorCodes.AdvisorSettingsChanged] = "模型配置已由其他页面修改，请重新读取后再保存。",
            [ErrorCodes.LedgerCorrectionCostMissing] = "旧卖出记录缺少成交前成本，无法可靠冲正。请使用完整账户对账校准；账户未改动。",
            [ErrorCodes.LedgerCorrectionDateInvalid] = "冲正日期不能早于原记录或当前账本日期。账户未改动。",
            [ErrorCodes.LedgerCorrectionDependentEvents] =
                "原成交之后已有卖出、份额变化或账户校准，无法可靠直接冲正。请先核对关联记录或使用完整账户对账校准；账户未改动。",
            // batch2 新增错误码文案。
            [ErrorCodes.RebalanceTargetNotFound] = "未设置再平衡目标，请先配置目标比例。",
            [ErrorCodes.SmartDcaPlanNotFound] = "未找到该智能定投计划。",
            [ErrorCodes.TwoFactorRequired] = "账号已启用双重验证，请输入 6 位动态口令完成登录。",
            [ErrorCodes.InvalidTotpCode] = "动态口令不正确或已过期，请重新输入。",
            // batch5 新增错误码文案（任务1/4）。
            [ErrorCodes.GoalTargetNotFound] = "未找到该目标规划。",
            [ErrorCodes.DcaExecutionNotFound] = "未找到该定投执行记录。",
            [ErrorCodes.AuditChainNotEnabled] = "审计哈希链尚未启用（admin_audit 缺少 prev_hash/hash 列），请先应用迁移 0036。",
        };

    public static string Get(string code) =>
        Messages.TryGetValue(code, out var message) ? message : ErrorCodes.InvalidRequest;

    public static bool Contains(string code) => Messages.ContainsKey(code);
}
