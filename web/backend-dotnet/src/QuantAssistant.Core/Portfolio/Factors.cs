using System.ComponentModel.DataAnnotations;

namespace QuantAssistant.Core.Portfolio;

/// <summary>
/// 因子分类
/// </summary>
public static class FactorCategory
{
    public const string Value = "value";
    public const string Momentum = "momentum";
    public const string Quality = "quality";
    public const string Risk = "risk";
    public const string Size = "size";
}

/// <summary>
/// 因子定义（元数据）
/// </summary>
public sealed class FactorDefinition
{
    public required string UserId { get; init; }
    public required string FactorCode { get; init; }
    public required string FactorName { get; init; }
    public required string Category { get; init; }
    
    /// <summary>
    /// 1 代表因子值越大越好，-1 代表越小越好。
    /// </summary>
    public int Direction { get; init; } = 1;
    public string? Description { get; init; }
    public DateTimeOffset CreatedAt { get; init; }
}

/// <summary>
/// 标的在某日的因子暴露度（原始值与标准化得分）
/// </summary>
public sealed class FactorExposure
{
    public required string UserId { get; init; }
    public required string Symbol { get; init; }
    public required string FactorCode { get; init; }
    public DateOnly DataDate { get; init; }
    
    public decimal? RawValue { get; init; }
    public decimal? ZScore { get; init; }
    public DateTimeOffset UpdatedAt { get; init; }
}

/// <summary>
/// 因子仓储接口
/// </summary>
public interface IFactorRepository
{
    void DefineFactor(FactorDefinition definition);
    void DeleteFactor(string userId, string factorCode);
    IReadOnlyList<FactorDefinition> ListDefinitions(string userId);

    void UpsertExposures(string userId, IReadOnlyList<FactorExposure> exposures);
    
    /// <summary>
    /// 查询某日期的标的截面因子打分
    /// </summary>
    IReadOnlyList<FactorExposure> GetCrossSection(string userId, DateOnly date, string? category = null);
}
