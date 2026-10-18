using System.Globalization;
using System.Text.Json;
using QuantAssistant.Infrastructure.Auth;
using Xunit;

namespace QuantAssistant.Api.Tests;

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
