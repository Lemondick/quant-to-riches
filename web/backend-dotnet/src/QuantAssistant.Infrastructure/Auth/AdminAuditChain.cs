using System.Security.Cryptography;
using System.Text;

namespace QuantAssistant.Infrastructure.Auth;

/// <summary>
/// batch2 任务12：审计哈希链纯函数。
/// 标准口径（以流 D 0036_audit_hash_chain.sql 文件头为准，deploy/verify-audit-chain.sh 按此验算，
/// A 流写入端逐字遵守）：
///   preimage = prev_hash || actor_id || action || target_id || detail || created_at_utc
///   - NULL 一律视为空字符串（COALESCE）；
///   - 无分隔符，直接拼接；
///   - created_at_utc = to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
///     例：2026-10-21 12:00:00+00 -> "2026-10-21T12:00:00.000000Z"（微秒恒 6 位）；
///   - hash = 小写 hex(SHA256(UTF-8(preimage)))。
/// 创世行（id 最小）：prev_hash = ''（NULL 亦视为 ''）。
/// QUANT_AUDIT_HASH_ALGO 目前仅支持 SHA256（其他值在写入时直接抛错，避免静默降级）。
/// </summary>
public static class AdminAuditChain
{
    public static string ComputeHash(string algo, string prevHash, string actorId, string action, string? targetId,
        string detail, DateTime createdAtUtc)
    {
        if (!string.Equals(algo, "SHA256", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"不支持的审计哈希算法：{algo}（QUANT_AUDIT_HASH_ALGO 仅支持 SHA256）。");
        var input = string.Concat(prevHash, actorId, action, targetId ?? "", detail, FormatTimestamp(createdAtUtc));
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(input))).ToLowerInvariant();
    }

    /// <summary>
    /// created_at_utc 口径：to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')。
    /// .NET 侧等价写法："yyyy-MM-ddTHH:mm:ss.ffffffZ"（微秒恒 6 位，与 PG 的 US 一致，均为截断）。
    /// </summary>
    public static string FormatTimestamp(DateTime value) =>
        (value.Kind == DateTimeKind.Utc ? value : value.ToUniversalTime())
            .ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ", System.Globalization.CultureInfo.InvariantCulture);

    public const string GenesisPrevHash = "";
}
