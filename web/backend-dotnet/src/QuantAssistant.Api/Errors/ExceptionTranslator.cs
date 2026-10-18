using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Core.Operations;
using QuantAssistant.Core.Abstractions;
using Npgsql;

namespace QuantAssistant.Api.Errors;

/// <summary>
/// 异常 → (HTTP 状态码, 错误码) 映射。与旧站点 app.py 的 exception_handler 一一对应。
/// </summary>
public static class ExceptionTranslator
{
    public static (int Status, string Code) Translate(Exception exception) => exception switch
    {
        AuthFailure e => (AuthErrors.Status(e.Code), e.Code),
        ApplicationFailure e => (e.Status, e.Code),
        AccountNotFound => (404, "account_not_found"),
        JobQuotaExceeded => (429, "quota_exceeded"),
        JobNotFoundError => (404, "job_not_found"),
        OperationAlreadyRunningError => (409, "operation_already_running"),
        ArgumentException => (422, ErrorCodes.InvalidRequest),
        NpgsqlException => (503, ErrorCodes.DatabaseUnavailable),
        LedgerStateError e => (422, e.Code),
        UnverifiedFeeScheduleError => (422, ErrorCodes.FeeEvidenceUnavailable),
        AdvisorBusyError => (409, ErrorCodes.AdvisorReportActive),
        AdvisorSettingsConflictError => (409, ErrorCodes.AdvisorSettingsChanged),
        DatabaseUnavailableError => (503, ErrorCodes.DatabaseUnavailable),
        TransactionNotFoundError => (404, ErrorCodes.TransactionNotFound),
        DividendNotFoundError => (404, ErrorCodes.DividendNotFound),
        BusinessError e => (422, ErrorMessages.Contains(e.Code) ? e.Code : ErrorCodes.InvalidRequest),
        _ => (500, ErrorCodes.InternalError),
    };
}
