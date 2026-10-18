using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Core.History;

/// <summary>历史记录分页结果（items 为动态列，三个 section 字段各异）。</summary>
public sealed record HistoryPage(
    IReadOnlyList<Dictionary<string, object?>> Items,
    int Total,
    int Page,
    int Limit,
    bool HasMore,
    string? Csv = null);

/// <summary>历史查询仓储契约：ledger/actions/corrections 三 section 分页 + 导出 + onboarding。</summary>
public interface IHistoryRepository
{
    /// <summary>分页查询。section ∈ {ledger, actions, corrections}；export=true 返回全部匹配行和 CSV，最多10万条。日期按北京时间，包含首尾日。</summary>
    HistoryPage Query(string userId, string section, int page, int limit, string? symbol, DateOnly? start, DateOnly? end, bool export);
    Task<HistoryPage> QueryAsync(string userId, string section, int page, int limit, string? symbol, DateOnly? start, DateOnly? end, bool export, CancellationToken cancellation = default);

    /// <summary>引导四步状态（组合/基线/模型/报告）。</summary>
    OnboardingStatus Onboarding(string userId);
}

/// <summary>引导四步状态（对应 /api/v1/onboarding）。</summary>
public sealed record OnboardingStatus(IReadOnlyList<OnboardingStep> Steps);

/// <summary>单个引导步骤。</summary>
public sealed record OnboardingStep(string Key, string Label, bool Done, string Url);

/// <summary>非法 section（映射 404）。</summary>
public sealed class UnknownHistorySectionError(string section) : Exception($"unknown history section: {section}");

/// <summary>导出超限（映射 413）。</summary>
public sealed class HistoryExportTooLargeError() : Exception("单次导出最多10万条，请缩小日期范围");
