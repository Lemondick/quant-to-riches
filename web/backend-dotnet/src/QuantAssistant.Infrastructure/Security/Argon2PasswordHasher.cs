using System.Text;
using Isopoh.Cryptography.Argon2;
using QuantAssistant.Core.Abstractions;

namespace QuantAssistant.Infrastructure.Security;

/// <summary>
/// Argon2id 密码哈希（Isopoh 实现），参数与旧站点一致：t=2, m=19456 KiB, p=1, hash=32B。
/// PHC 字符串手动构造/解析（格式 $argon2id$v=19$m=...,t=...,p=...$salt$hash），
/// 避免依赖库的序列化 API；跨语言兼容已由 Argon2CompatibilityTests 验证。
/// </summary>
public sealed class Argon2PasswordHasher : IPasswordHasher
{
    private const int TimeCost = 2;
    private const int MemoryCostKib = 19456;
    private const int Parallelism = 1;
    private const int HashLength = 32;
    private const int SaltLength = 16;

    public string Hash(string password)
    {
        var salt = RandomSalt();
        var hash = Compute(password, salt, TimeCost, MemoryCostKib, Parallelism, HashLength);
        return $"$argon2id$v=19$m={MemoryCostKib},t={TimeCost},p={Parallelism}$"
               + $"{ToB64NoPad(salt)}${ToB64NoPad(hash)}";
    }

    public bool Verify(string hashed, string password)
    {
        if (string.IsNullOrWhiteSpace(hashed) || !hashed.StartsWith("$argon2id$", StringComparison.Ordinal))
        {
            return false;
        }

        try
        {
            var parts = hashed.Split('$'); // ["", "argon2id", "v=19", "m=...,t=...,p=...", salt, hash]
            if (parts.Length != 6)
            {
                return false;
            }

            var (m, t, p) = ParseParams(parts[3]);
            var salt = FromB64NoPad(parts[4]);
            var expected = FromB64NoPad(parts[5]);
            var actual = Compute(password, salt, t, m, p, expected.Length);
            return CryptographicOperations.FixedTimeEquals(expected, actual);
        }
        catch
        {
            return false;
        }
    }

    private static byte[] Compute(string password, byte[] salt, int t, int m, int p, int hashLength)
    {
        var config = new Argon2Config
        {
            Type = Argon2Type.HybridAddressing, // Argon2id
            Version = Argon2Version.Nineteen,
            TimeCost = t,
            MemoryCost = m,
            Lanes = p,
            Threads = p,
            HashLength = hashLength,
            Password = Encoding.UTF8.GetBytes(password),
            Salt = salt,
        };
        using var hash = new Argon2(config).Hash();
        var result = new byte[hash.Buffer.Length];
        Array.Copy(hash.Buffer, result, result.Length);
        return result;
    }

    private static (int MemoryKib, int Time, int Parallelism) ParseParams(string segment)
    {
        var m = MemoryCostKib;
        var t = TimeCost;
        var p = Parallelism;
        foreach (var kv in segment.Split(','))
        {
            var eq = kv.IndexOf('=');
            if (eq < 0)
            {
                continue;
            }

            var key = kv[..eq];
            var value = int.Parse(kv[(eq + 1)..]);
            switch (key)
            {
                case "m": m = value; break;
                case "t": t = value; break;
                case "p": p = value; break;
            }
        }

        return (m, t, p);
    }

    private static byte[] RandomSalt()
    {
        var salt = new byte[SaltLength];
        System.Security.Cryptography.RandomNumberGenerator.Fill(salt);
        return salt;
    }

    private static string ToB64NoPad(byte[] data) => Convert.ToBase64String(data).TrimEnd('=');

    private static byte[] FromB64NoPad(string value)
    {
        var pad = (4 - value.Length % 4) % 4;
        return Convert.FromBase64String(value + new string('=', pad));
    }

    private static class CryptographicOperations
    {
        public static bool FixedTimeEquals(byte[] a, byte[] b)
        {
            if (a.Length != b.Length)
            {
                return false;
            }

            var diff = 0;
            for (var i = 0; i < a.Length; i++)
            {
                diff |= a[i] ^ b[i];
            }

            return diff == 0;
        }
    }
}
