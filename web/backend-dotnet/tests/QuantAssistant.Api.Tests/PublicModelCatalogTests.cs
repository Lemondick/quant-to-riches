using System.Net;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Errors;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class PublicModelCatalogTests
{
    [Theory]
    [InlineData("https://models.example.org/v1?")]
    [InlineData("https://models.example.org/v1#")]
    [InlineData("https://models.example.org/a/../v1")]
    [InlineData("https://models.example.org/%2fprivate")]
    [InlineData("https://models.example.org//v1")]
    [InlineData("https://models.example.org/v1\n")]
    [InlineData("https://models.example.org/v1\\other")]
    [InlineData("https://models.example.org/v1//")]
    [InlineData("https://@models.example.org/v1")]
    public void AmbiguousEndpoint_IsRejectedBeforeDiscovery(string endpoint)
        => Assert.Throws<ApplicationFailure>(() => PublicModelCatalog.Endpoint(endpoint));

    [Theory]
    [InlineData("2002:0a00:0001::1")]
    [InlineData("2001:0:4136:e378:8000:63bf:3fff:fdd2")]
    [InlineData("2001:2::1")]
    [InlineData("2001:100::1")]
    [InlineData("3fff::1")]
    [InlineData("192.88.99.1")]
    public async Task NonPublicOrTransitionAddress_IsRejectedBeforeTransport(string address)
    {
        var created = false;
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse(address) }), _ =>
        {
            created = true;
            return new Handler((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)));
        });
        await Assert.ThrowsAsync<ApplicationFailure>(() => catalog.Discover("https://models.example.org/v1", "synthetic-value", CancellationToken.None));
        Assert.False(created);
    }

    [Theory]
    [InlineData("2001:1::1")]
    [InlineData("2001:1::2")]
    [InlineData("2001:1::3")]
    [InlineData("2001:3::1")]
    [InlineData("2001:4:112::1")]
    [InlineData("2001:20::1")]
    [InlineData("2001:30::1")]
    [InlineData("2606:4700:4700::1111")]
    [InlineData("::ffff:8.8.8.8")]
    public async Task RegisteredGlobalAddress_RemainsSupported(string address)
    {
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse(address) }), _ =>
            new Handler((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("{\"data\":[{\"id\":\"synthetic-model\"}]}")
            })));
        Assert.Equal(new[] { "synthetic-model" }, await catalog.Discover("https://models.example.org/v1", "synthetic-value", CancellationToken.None));
    }

    [Fact]
    public async Task JevOfficialCatalog_UsesNamesAndKeepsProviderIdsSeparate()
    {
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8") }), _ =>
            new Handler((request, _) =>
            {
                Assert.Equal("https://api.typesafe.ai/v1/models", request.RequestUri!.AbsoluteUri);
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{\"models\":[{\"name\":\"jev-preview\"},{\"name\":\"jev-latest\"},{\"name\":\"jev-latest\"},{\"name\":\"bad model\"}],\"data\":[{\"id\":\"wrong-provider-model\"}]}") });
            }));
        Assert.Equal(new[] { "jev-latest", "jev-preview" }, await catalog.Discover("https://api.typesafe.ai/v1", "synthetic-key", CancellationToken.None));
    }

    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => send(request, token);
    }

    [Fact]
    public async Task UnreachableFirstAddress_UsesAnotherValidatedAddress()
    {
        var addresses = new[] { IPAddress.Parse("2606:4700:4700::1111"), IPAddress.Parse("8.8.8.8") };
        var used = new List<IPAddress>();
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(addresses), address =>
        {
            used.Add(address);
            return new Handler((request, _) =>
            {
                Assert.Equal("https://models.example.org/v1/models", request.RequestUri!.AbsoluteUri);
                Assert.Equal("Bearer", request.Headers.Authorization!.Scheme);
                Assert.Equal("synthetic-key", request.Headers.Authorization.Parameter);
                if (address.Equals(addresses[0])) throw new HttpRequestException("synthetic TLS failure");
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{\"data\":[{\"id\":\"synthetic-model\"},{\"id\":\"synthetic-model\"}]}") });
            });
        });
        Assert.Equal(new[] { "synthetic-model" }, await catalog.Discover("https://models.example.org/v1", "synthetic-key", CancellationToken.None));
        Assert.Equal(addresses, used);
    }

    [Fact]
    public async Task MixedPrivateAddresses_DoNotOpenAnyConnection()
    {
        var opened = false;
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8"), IPAddress.Loopback }), _ =>
        {
            opened = true;
            throw new InvalidOperationException();
        });
        var error = await Assert.ThrowsAsync<ApplicationFailure>(() => catalog.Discover("https://models.example.org/v1", "synthetic-key", CancellationToken.None));
        Assert.Equal("model_discovery_failed", error.Code);
        Assert.False(opened);
    }

    [Fact]
    public async Task AuthenticationFailure_DoesNotTryMoreAddresses()
    {
        var opened = 0;
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8"), IPAddress.Parse("1.1.1.1") }), _ =>
        {
            opened++;
            return new Handler((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.Unauthorized)));
        });
        await Assert.ThrowsAsync<ApplicationFailure>(() => catalog.Discover("https://models.example.org/v1", "synthetic-key", CancellationToken.None));
        Assert.Equal(1, opened);
    }

    [Fact]
    public async Task CallerCancellation_DoesNotOpenAnotherAddress()
    {
        var opened = 0;
        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8"), IPAddress.Parse("1.1.1.1") }), _ =>
        {
            opened++;
            return new Handler(async (_, token) =>
            {
                await Task.Delay(Timeout.InfiniteTimeSpan, token);
                return new HttpResponseMessage(HttpStatusCode.OK);
            });
        });
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => catalog.Discover("https://models.example.org/v1", "synthetic-key", cancellation.Token));
        Assert.Equal(1, opened);
    }

    [Fact]
    public async Task SlowAddress_DoesNotPreventDiscoveringModelsOnNextAddress()
    {
        var opened = 0;
        var catalog = new PublicModelCatalog((_, _) => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8"), IPAddress.Parse("1.1.1.1") }), _ =>
        {
            var number = ++opened;
            return new Handler(async (_, token) =>
            {
                if (number == 1) await Task.Delay(Timeout.InfiniteTimeSpan, token);
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{\"data\":[{\"id\":\"synthetic-model\"}]}") };
            });
        });
        Assert.Equal(new[] { "synthetic-model" }, await catalog.Discover("https://models.example.org/v1", "synthetic-key", CancellationToken.None));
        Assert.Equal(2, opened);
    }
}
