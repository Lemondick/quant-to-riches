using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Health;

/// <summary>
/// P1 占位就绪探测：数据库/Worker 尚未接入时，给出安全且可判定的「不可用」状态。
/// 计划书 P1 要求「数据库、Worker 和私有配置不可用时给出安全且可判定的状态」——
/// 未实现能力不得伪造成功。
/// </summary>
public sealed class NotConfiguredProbe : IReadinessProbe
{
    public HealthStatus Check() => new("not_ready", "not_configured");
}
