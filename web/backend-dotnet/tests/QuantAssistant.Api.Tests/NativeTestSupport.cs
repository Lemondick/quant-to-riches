using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text.Json;
using QuantAssistant.Infrastructure.Auth;
using Xunit;

namespace QuantAssistant.Api.Tests;

internal static class TestHostEnvironment
{
    // Request fixtures must not acquire scheduled account writes when CI enters a report window.
    // 另外：E7 起 Program 启动要求连接串（fail-fast）；不触库的契约测试在没有
    // QUANT_PG_CONNECTION 的环境（如本机）用 dummy 串启动，CI 有真实串时不受影响。
    // 需要真实库的测试走 [PgFact] + QUANT_TEST_PG_CONNECTION，本处不干预。
    [ModuleInitializer]
    internal static void Initialize()
    {
        Environment.SetEnvironmentVariable("QUANT_DISABLE_SCHEDULER", "true");
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("QUANT_PG_CONNECTION")))
            Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION",
                "Host=127.0.0.1;Port=59999;Database=quant_test_dummy;Username=none;Password=none;Timeout=2");
    }
}

internal static class NativeTestSupport
{
    internal static decimal DecimalText(this JsonElement value)
    {
        Assert.Equal(JsonValueKind.String, value.ValueKind);
        return decimal.Parse(value.GetString()!, CultureInfo.InvariantCulture);
    }
}

// Each registration test explicitly opens registration on an initialized
// synthetic site, then restores the setting. Fresh installs stay closed.
internal sealed class RegistrationScope : IDisposable
{
    private readonly PgTestUser admin = new();
    private readonly bool previouslyEnabled;

    internal RegistrationScope()
    {
        previouslyEnabled = new AccountRepository(admin.Connection).RegistrationEnabled();
        admin.Execute("UPDATE users SET role='admin' WHERE id=$1", admin.Id);
        admin.Execute("UPDATE site_settings SET value='1' WHERE key='registration_enabled'");
    }

    public void Dispose()
    {
        admin.Execute("UPDATE site_settings SET value=$1 WHERE key='registration_enabled'", previouslyEnabled ? "1" : "0");
        admin.Dispose();
    }
}
