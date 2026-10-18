namespace QuantAssistant.Core.Errors;

/// <summary>业务异常基类：携带对外稳定错误码。</summary>
public abstract class BusinessError : Exception
{
    protected BusinessError(string code, string message)
        : base(message)
    {
        Code = code;
    }

    public string Code { get; }
}

/// <summary>账本状态冲突（对应 Python LedgerStateError，映射 422）。</summary>
public sealed class LedgerStateError : BusinessError
{
    public LedgerStateError(string code)
        : base(ErrorMessages.Contains(code) ? code : ErrorCodes.InvalidRequest, ErrorMessages.Get(code))
    {
    }
}

/// <summary>费率证据未核验（对应 UnverifiedFeeSchedule，映射 422）。</summary>
public sealed class UnverifiedFeeScheduleError : BusinessError
{
    public UnverifiedFeeScheduleError()
        : base(ErrorCodes.FeeEvidenceUnavailable, ErrorMessages.Get(ErrorCodes.FeeEvidenceUnavailable))
    {
    }
}

/// <summary>AI 报告执行中（映射 409）。</summary>
public sealed class AdvisorBusyError : BusinessError
{
    public AdvisorBusyError()
        : base(ErrorCodes.AdvisorReportActive, ErrorMessages.Get(ErrorCodes.AdvisorReportActive))
    {
    }
}

/// <summary>模型配置版本冲突（映射 409）。</summary>
public sealed class AdvisorSettingsConflictError : BusinessError
{
    public AdvisorSettingsConflictError()
        : base(ErrorCodes.AdvisorSettingsChanged, ErrorMessages.Get(ErrorCodes.AdvisorSettingsChanged))
    {
    }
}

/// <summary>数据库不可用（映射 503）。</summary>
public sealed class DatabaseUnavailableError : BusinessError
{
    public DatabaseUnavailableError()
        : base(ErrorCodes.DatabaseUnavailable, ErrorMessages.Get(ErrorCodes.DatabaseUnavailable))
    {
    }
}

/// <summary>冲正目标成交不存在（映射 404 transaction_not_found）。</summary>
public sealed class TransactionNotFoundError : BusinessError
{
    public TransactionNotFoundError()
        : base(ErrorCodes.TransactionNotFound, ErrorMessages.Get(ErrorCodes.TransactionNotFound))
    {
    }
}

/// <summary>冲正目标分红不存在（映射 404 dividend_not_found）。</summary>
public sealed class DividendNotFoundError : BusinessError
{
    public DividendNotFoundError()
        : base(ErrorCodes.DividendNotFound, ErrorMessages.Get(ErrorCodes.DividendNotFound))
    {
    }
}
