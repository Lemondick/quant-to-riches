using System.Text.Json;
using System.Text.Json.Serialization;

namespace QuantAssistant.Api.Serialization;

/// <summary>
/// 全局 JSON 默认配置：snake_case 命名 + 拒绝额外字段（旧封闭模型行为）。
/// 所有写响应/异常处理共用同一份配置，保证错误响应与业务响应字段口径一致。
/// </summary>
public static class JsonDefaults
{
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = new SnakeCaseNamingPolicy(),
        // 旧站点封闭模型拒绝额外字段；迁移保持（计划书 §3「现有封闭模型拒绝额外字段」）。
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        Converters = {new QuantAssistant.Infrastructure.Db.FinanceJson.DecimalConverter()},
    };
}
