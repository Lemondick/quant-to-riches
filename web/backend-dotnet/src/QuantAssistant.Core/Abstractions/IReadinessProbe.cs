namespace QuantAssistant.Core.Abstractions;

/// <summary>就绪探测结果：与旧站点 /readyz 语义一致（status + database connected）。</summary>
public sealed record HealthStatus(string Status, string Database);

/// <summary>
/// 运行就绪探测契约。P1 骨架只有不可用探测；P2（数据库）、P4（Worker）起由真实实现替换。
/// </summary>
public interface IReadinessProbe
{
    /// <summary>返回就绪状态；任何异常不得抛出（由调用方兜底为 not_ready）。</summary>
    HealthStatus Check();
}
