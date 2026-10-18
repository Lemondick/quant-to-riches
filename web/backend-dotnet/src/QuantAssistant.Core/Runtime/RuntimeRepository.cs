namespace QuantAssistant.Core.Runtime;

/// <summary>运行维护仓储契约：管理员运行指标聚合。</summary>
public interface IRuntimeRepository
{
    /// <summary>账户运行概览（用户 + 任务失败计数 + 数据状态）。</summary>
    IReadOnlyList<AccountRuntime> Accounts();

    /// <summary>业务就绪：所有 active 账户数据可用。</summary>
    bool BusinessReady();
}

/// <summary>账户运行概览（对应旧站点 /admin/runtime 的 accounts 项）。</summary>
public sealed record AccountRuntime(
    string UserId,
    string Username,
    string Status,
    string DataStatus,
    int AdvisorFailures,
    int ResearchFailures);
