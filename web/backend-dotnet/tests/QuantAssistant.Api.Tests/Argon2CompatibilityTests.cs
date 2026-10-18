using System.Text;
using Isopoh.Cryptography.Argon2;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// Argon2id 跨语言兼容验证：C# 实现对固定测试向量的散列结果须与 Python argon2-cffi
/// 逐字节一致（计划书 §3「账号与安全」）。向量见 web/docs/refactor/argon2-vectors.json。
/// </summary>
public sealed class Argon2CompatibilityTests
{
    // 与 services/auth.py 一致：Argon2id, t=2, m=19456 KiB, p=1, hash_len=32。
    private const int TimeCost = 2;
    private const int MemoryCostKib = 19456;
    private const int Parallelism = 1;
    private const int HashLength = 32;

    // 固定盐 00..0f（见 argon2-vectors.json fixed_salt_hex）。
    private static readonly byte[] Salt = Enumerable.Range(0, 16).Select(i => (byte)i).ToArray();

    private static byte[] HashArgon2Id(string password)
    {
        var config = new Argon2Config
        {
            Type = Argon2Type.HybridAddressing, // Argon2id
            Version = Argon2Version.Nineteen,
            TimeCost = TimeCost,
            MemoryCost = MemoryCostKib,
            Lanes = Parallelism,
            Threads = Parallelism,
            HashLength = HashLength,
            Password = Encoding.UTF8.GetBytes(password),
            Salt = Salt,
        };
        var hash = new Argon2(config).Hash();
        // SecureArray<byte> 的底层缓冲即为散列结果；复制出来避免被 Dispose 清空。
        var result = new byte[hash.Buffer.Length];
        Array.Copy(hash.Buffer, result, result.Length);
        hash.Dispose();
        return result;
    }

    [Theory]
    [InlineData("CorrectHorseBattery", "5c2faebb48c5bb8cc67dc5627eea03ea5bc80dcf9cfd470c502db5d6f191fdb3")]
    [InlineData("密码Password_12345", "9b400396da21cb29606c05c96b2b4d97202e9a4acef4d8db787044a53c962634")]
    public void Hash_MatchesPythonReference(string password, string expectedHex)
    {
        var hash = HashArgon2Id(password);
        Assert.Equal(expectedHex, Convert.ToHexString(hash).ToLowerInvariant());
    }

    [Fact]
    public void Hash_LengthIs32Bytes()
    {
        Assert.Equal(HashLength, HashArgon2Id("CorrectHorseBattery").Length);
    }
}
