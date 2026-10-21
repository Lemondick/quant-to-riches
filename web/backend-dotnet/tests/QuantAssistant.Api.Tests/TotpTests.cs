using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// batch2-11/12：TOTP（RFC 6238）与审计哈希链纯函数测试（无 DB）。
/// TOTP 向量来自 RFC 6238 Appendix B（SHA-1，密钥 ASCII "12345678901234567890"）。
/// </summary>
public class TotpTests
{
    // "12345678901234567890" 的 base32（RFC 4648，无填充）。
    private const string RfcSecret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";

    [Fact]
    public void Base32_RoundTrip()
    {
        var secret = Totp.GenerateSecret();
        Assert.Equal(32, secret.Length); // 20 字节 → 32 个 base32 字符
        Assert.Matches("^[A-Z2-7]+$", secret);
    }

    [Theory]
    [InlineData(59L, "287082")]
    [InlineData(1111111109L, "081804")]
    [InlineData(1111111111L, "050471")]
    [InlineData(1234567890L, "005924")]
    [InlineData(2000000000L, "279037")]
    [InlineData(20000000000L, "353130")]
    public void CurrentCode_MatchesRfc6238Vectors(long unixSeconds, string expected)
    {
        var timestamp = DateTimeOffset.FromUnixTimeSeconds(unixSeconds);
        Assert.Equal(expected, Totp.CurrentCode(RfcSecret, timestamp));
    }

    [Fact]
    public void Verify_AcceptsCurrentAndAdjacentSteps()
    {
        var now = DateTimeOffset.UtcNow;
        var code = Totp.CurrentCode(RfcSecret, now);
        Assert.True(Totp.Verify(RfcSecret, code, now));
        // 相邻时间步在窗口内也应通过
        Assert.True(Totp.Verify(RfcSecret, Totp.CurrentCode(RfcSecret, now.AddSeconds(-30)), now));
        Assert.True(Totp.Verify(RfcSecret, Totp.CurrentCode(RfcSecret, now.AddSeconds(30)), now));
    }

    [Fact]
    public void Verify_RejectsWrongCode()
    {
        var now = DateTimeOffset.UtcNow;
        Assert.False(Totp.Verify(RfcSecret, "000000", now));
        Assert.False(Totp.Verify(RfcSecret, "12345", now));
        Assert.False(Totp.Verify(RfcSecret, "abcdef", now));
        Assert.False(Totp.Verify(RfcSecret, "", now));
    }

    [Fact]
    public void ProvisioningUri_HasExpectedShape()
    {
        var uri = Totp.ProvisioningUri("观澜", "alice", RfcSecret);
        Assert.StartsWith("otpauth://totp/", uri);
        Assert.Contains("secret=" + RfcSecret, uri);
        Assert.Contains("digits=6", uri);
        Assert.Contains("period=30", uri);
    }

    [Fact]
    public void AuditChain_ComputeHash_IsDeterministicAndChained()
    {
        var t = new DateTime(2026, 10, 21, 12, 0, 0, DateTimeKind.Utc);
        var h1 = AdminAuditChain.ComputeHash("SHA256", "", "admin1", "status", "u1", "{\"value\":\"x\"}", t);
        var h2 = AdminAuditChain.ComputeHash("SHA256", h1, "admin1", "status", "u2", "{\"value\":\"y\"}", t);
        Assert.Equal(64, h1.Length);
        Assert.NotEqual(h1, h2);
        // 相同输入 → 相同输出；任一字段变化 → 哈希变化
        Assert.Equal(h1, AdminAuditChain.ComputeHash("SHA256", "", "admin1", "status", "u1", "{\"value\":\"x\"}", t));
        Assert.NotEqual(h1, AdminAuditChain.ComputeHash("SHA256", "", "admin1", "status", "u1", "{\"value\":\"z\"}", t));
        Assert.NotEqual(h1, AdminAuditChain.ComputeHash("SHA256", "other", "admin1", "status", "u1", "{\"value\":\"x\"}", t));
    }

    [Fact]
    public void AuditChain_TimestampFormat_MatchesSpecExample()
    {
        // 0036 文件头示例：2026-10-21 12:00:00+00 -> "2026-10-21T12:00:00.000000Z"（微秒恒 6 位）。
        var t = new DateTime(2026, 10, 21, 12, 0, 0, DateTimeKind.Utc);
        Assert.Equal("2026-10-21T12:00:00.000000Z", AdminAuditChain.FormatTimestamp(t));
        // 亚微秒精度截断到 6 位（与 PG to_char US 一致）。
        var t2 = new DateTime(2026, 10, 21, 12, 0, 0, 123, DateTimeKind.Utc).AddTicks(4567);
        Assert.Equal("2026-10-21T12:00:00.123456Z", AdminAuditChain.FormatTimestamp(t2));
    }

    [Fact]
    public void AuditChain_ComputeHash_MatchesIndependentVector()
    {
        // 独立验算（python hashlib.sha256）：preimage =
        // '' || 'admin1' || 'status' || 'u1' || '{"value":"x"}' || '2026-10-21T12:00:00.000000Z'
        var t = new DateTime(2026, 10, 21, 12, 0, 0, DateTimeKind.Utc);
        Assert.Equal("73432011523dabf538000e3c02c525596523a5d50ea2e63cb9e9787c5f482fd3",
            AdminAuditChain.ComputeHash("SHA256", "", "admin1", "status", "u1", "{\"value\":\"x\"}", t));
    }

    [Fact]
    public void AuditChain_UnsupportedAlgo_Throws()
    {
        var t = DateTime.UtcNow;
        Assert.Throws<InvalidOperationException>(() => AdminAuditChain.ComputeHash("SHA512", "", "a", "b", null, "{}", t));
    }
}
