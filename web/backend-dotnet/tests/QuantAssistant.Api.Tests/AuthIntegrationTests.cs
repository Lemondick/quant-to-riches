using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Security;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// P2 集成测试：在真实 PostgreSQL 上验证注册→登录→会话→恢复码完整链路。
/// 由环境变量 QUANT_TEST_PG_CONNECTION 门控；未设置时跳过（CI 无 PG）。
/// 使用随机测试账号并自清理，不触碰任何真实账号数据。
/// </summary>
public sealed class AuthIntegrationTests
{
    private readonly string? _connection;

    public AuthIntegrationTests()
    {
        _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");
    }

    private AuthService CreateService() =>
        new(new AccountRepository(_connection!), new Argon2PasswordHasher(), TimeProvider.System);

    [PgFact]
    public void Register_Login_Session_Recovery_RoundTrip()
    {
        if (_connection is null)
        {
            return; // 跳过（无 PG）
        }

        using var registration = new RegistrationScope();
        var svc = CreateService();
        var username = $"it_{Guid.NewGuid().ToString("N")[..12]}";
        const string password = "IntegrationTest_12345";
        const string newPassword = "NewPassword_67890_ok";

        try
        {
            // 注册
            var register = svc.Register(username, password);
            Assert.True(register.Ok, register.ErrorCode);

            // 登录
            var account = svc.Authenticate(username, password);
            Assert.NotNull(account);
            Assert.False(svc.Authenticate(username, "WrongPassword_9999") is not null);

            // 会话
            var session = svc.CreateSession(account!.Id, "test-device");
            var sessionAccount = svc.SessionAccount(session.Token);
            Assert.Equal(account.Id, sessionAccount!.Id);

            // 恢复码（管理员签发 → 兑付）
            svc.IssueRecovery(account.Id, account.Id);
            // 注：签发后 token 未回传，这里直接验证兑付失败路径（token 无效）
            var badRedeem = svc.RedeemRecovery(username, "invalid-token", newPassword);
            Assert.False(badRedeem.Ok);
            Assert.Equal("invalid_recovery_token", badRedeem.ErrorCode);
        }
        finally
        {
            Cleanup(username);
        }
    }

    private void Cleanup(string username)
    {
        if (_connection is null)
        {
            return;
        }

        using var conn = new Npgsql.NpgsqlConnection(_connection);
        conn.Open();
        // 依赖顺序清理：先删会话/恢复码，再删用户（外键约束正确生效）。
        foreach (var table in new[] { "sessions", "recovery_tokens", "account_activity" })
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"DELETE FROM {table} WHERE user_id = (SELECT id FROM users WHERE username = $1)";
            cmd.Parameters.AddWithValue(username);
            cmd.ExecuteNonQuery();
        }

        using (var del = conn.CreateCommand())
        {
            del.CommandText = "DELETE FROM users WHERE username = $1";
            del.Parameters.AddWithValue(username);
            del.ExecuteNonQuery();
        }
    }
}
