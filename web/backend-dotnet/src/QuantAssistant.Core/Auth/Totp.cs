using System.Security.Cryptography;
using System.Text;

namespace QuantAssistant.Core.Auth;

/// <summary>
/// batch2 任务11：TOTP 自研实现（RFC 6238：base32 解码 + HMAC-SHA1，不引新 NuGet 包）。
/// 时间步 30 秒、6 位数字、SHA1；校验允许前后 1 个时间步的时钟漂移窗口。
/// </summary>
public static class Totp
{
    private const int StepSeconds = 30;
    private const string Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

    /// <summary>生成 20 字节随机密钥的 base32 表示（RFC 4648，无填充）。</summary>
    public static string GenerateSecret() => Encode(RandomNumberGenerator.GetBytes(20));

    /// <summary>计算指定时刻的 6 位动态口令。</summary>
    public static string CurrentCode(string base32Secret, DateTimeOffset timestamp)
    {
        var key = Decode(base32Secret);
        var counter = (ulong)(timestamp.ToUnixTimeSeconds() / StepSeconds);
        var message = BitConverter.GetBytes(counter);
        if (BitConverter.IsLittleEndian) Array.Reverse(message);
        var hash = HMACSHA1.HashData(key, message);
        var offset = hash[^1] & 0x0F;
        var code = ((hash[offset] & 0x7F) << 24 | hash[offset + 1] << 16 | hash[offset + 2] << 8 | hash[offset + 3]) % 1_000_000;
        return code.ToString("D6", System.Globalization.CultureInfo.InvariantCulture);
    }

    /// <summary>校验动态口令；window 为允许的时间步漂移（默认前后各 1 步）。</summary>
    public static bool Verify(string base32Secret, string code, DateTimeOffset timestamp, int window = 1)
    {
        if (code is null || code.Length != 6 || !code.All(char.IsDigit)) return false;
        try
        {
            var expected = Encoding.ASCII.GetBytes(code);
            for (var step = -window; step <= window; step++)
            {
                var candidate = Encoding.ASCII.GetBytes(CurrentCode(base32Secret, timestamp.AddSeconds(step * StepSeconds)));
                if (CryptographicOperations.FixedTimeEquals(candidate, expected)) return true;
            }
        }
        catch (FormatException) { return false; }
        return false;
    }

    /// <summary>otpauth:// URI（供验证器 App 扫码）。</summary>
    public static string ProvisioningUri(string issuer, string username, string base32Secret) =>
        $"otpauth://totp/{Uri.EscapeDataString(issuer)}:{Uri.EscapeDataString(username)}" +
        $"?secret={base32Secret}&issuer={Uri.EscapeDataString(issuer)}&digits=6&period=30&algorithm=SHA1";

    internal static string Encode(byte[] data)
    {
        var output = new StringBuilder((data.Length + 4) / 5 * 8);
        for (var i = 0; i < data.Length; i += 5)
        {
            ulong buffer = 0; var count = Math.Min(5, data.Length - i);
            for (var j = 0; j < count; j++) buffer = buffer << 8 | data[i + j];
            buffer <<= (5 - count) * 8;
            var chars = (int)Math.Ceiling(count * 8 / 5.0);
            for (var j = 0; j < chars; j++) output.Append(Alphabet[(int)(buffer >> (35 - j * 5)) & 31]);
        }
        return output.ToString();
    }

    internal static byte[] Decode(string input)
    {
        var clean = input.TrimEnd('=').ToUpperInvariant();
        if (clean.Length == 0 || clean.Any(c => Alphabet.IndexOf(c) < 0))
            throw new FormatException("无效的 base32 编码");
        var output = new List<byte>(clean.Length * 5 / 8);
        ulong buffer = 0; var bits = 0;
        foreach (var c in clean)
        {
            buffer = buffer << 5 | (ulong)(uint)Alphabet.IndexOf(c); bits += 5;
            if (bits >= 8) { output.Add((byte)(buffer >> (bits - 8))); bits -= 8; buffer &= (1UL << bits) - 1; }
        }
        return output.ToArray();
    }
}
