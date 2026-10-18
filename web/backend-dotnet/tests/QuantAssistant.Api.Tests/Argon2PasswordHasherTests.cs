using QuantAssistant.Infrastructure.Security;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class Argon2PasswordHasherTests
{
    private readonly Argon2PasswordHasher _hasher = new();

    [Fact]
    public void Hash_Then_Verify_RoundTrips()
    {
        var hash = _hasher.Hash("CorrectHorseBattery");
        Assert.StartsWith("$argon2id$v=19$m=19456,t=2,p=1$", hash);
        Assert.True(_hasher.Verify(hash, "CorrectHorseBattery"));
        Assert.False(_hasher.Verify(hash, "WrongPassword00000"));
    }

    [Fact]
    public void Verify_PythonGeneratedPhc_ReturnsTrue()
    {
        // 由旧站点 Python argon2-cffi 生成的 PHC（密码 "CorrectHorseBattery"）。
        const string pythonPhc =
            "$argon2id$v=19$m=19456,t=2,p=1$reCWPtbUkOb3BknGGilWqA$z75BDPLASZRvXQivXEQ22B0NebSLN02Z/A9xGavqWd8";
        Assert.True(_hasher.Verify(pythonPhc, "CorrectHorseBattery"));
        Assert.False(_hasher.Verify(pythonPhc, "WrongPassword00000"));
    }

    [Fact]
    public void Verify_UnicodePassword_RoundTrips()
    {
        var hash = _hasher.Hash("密码Password_12345");
        Assert.True(_hasher.Verify(hash, "密码Password_12345"));
    }
}
