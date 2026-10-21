using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using QuantAssistant.Api.Services;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class TestHostIsolationTests
{
    [Fact]
    public void RequestTestsNeverStartAutomaticAdvisorJobs()
    {
        using var factory=new WebApplicationFactory<Program>();
        using var client=factory.CreateTestClient();
        Assert.Equal("true",factory.Services.GetRequiredService<IConfiguration>()["QUANT_DISABLE_SCHEDULER"]);
        Assert.False(factory.Services.GetRequiredService<NativeScheduler>().Running);
    }
}
