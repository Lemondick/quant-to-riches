using System.Text.Json.Nodes;

namespace QuantAssistant.Core.Abstractions;

/// <summary>
/// E5：账号数据会话抽象（Core 层契约，实现见 Infrastructure.Db.AccountSql）。
/// 覆盖完整会话能力（含同步方法，供 CLI/低频流程与渐进迁移使用）；
/// 控制器热路径统一使用异步方法（E6）。
/// </summary>
public interface IAccountSql
{
    Task<int> ExecuteAsync(string sql, params object?[] values);
    Task<object?> ScalarAsync(string sql, params object?[] values);
    Task<JsonArray> QueryAsync(string sql, params object?[] values);
    int Execute(string sql, params object?[] values);
    object? Scalar(string sql, params object?[] values);
    JsonArray Query(string sql, params object?[] values);
}
