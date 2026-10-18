using System.Text;
using System.Text.Json;

namespace QuantAssistant.Api.Serialization;

/// <summary>
/// snake_case JSON 命名策略：与旧 Python/Pydantic 站点的字段名保持一致（计划书 §3「保留字段名」）。
/// </summary>
public sealed class SnakeCaseNamingPolicy : JsonNamingPolicy
{
    public override string ConvertName(string name)
    {
        if (string.IsNullOrEmpty(name))
        {
            return name;
        }

        var builder = new StringBuilder(name.Length + 8);
        for (var i = 0; i < name.Length; i++)
        {
            var c = name[i];
            if (char.IsUpper(c))
            {
                // 大写前补下划线，除非是串首或前一个已是大写（避免连续缩写被拆开）
                if (i > 0 && !char.IsUpper(name[i - 1]))
                {
                    builder.Append('_');
                }

                builder.Append(char.ToLowerInvariant(c));
            }
            else
            {
                builder.Append(c);
            }
        }

        return builder.ToString();
    }
}
