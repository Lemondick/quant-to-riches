using System.Text.Json.Nodes;

namespace QuantAssistant.Core.Abstractions;

/// <summary>
/// E5：账号数据库抽象（Core 层契约，实现见 Infrastructure.Db.AccountDatabase）。
/// Api 层（控制器/服务）只依赖此接口，不再直引 Infrastructure 具体类（依赖倒置）。
/// 热路径统一异步（E6）；同步适配器保留在具体类上供 CLI 使用。
/// </summary>
public interface IAccountDatabase
{
    Task<JsonArray> QueryAsync(string sql, object?[] values, CancellationToken cancellation = default);
    Task<T> ReadAsync<T>(Func<IAccountSql, Task<T>> read, CancellationToken cancellation = default);
    /// <summary>账本写：advisory 锁串行化（仅账本事实表）。</summary>
    Task<T> WriteAsync<T>(string userId, Func<IAccountSql, Task<T>> write, CancellationToken cancellation = default);
    /// <summary>普通写：行级锁即可（E15）。</summary>
    Task<T> WriteNonLedgerAsync<T>(string userId, Func<IAccountSql, Task<T>> write, CancellationToken cancellation = default);
}
