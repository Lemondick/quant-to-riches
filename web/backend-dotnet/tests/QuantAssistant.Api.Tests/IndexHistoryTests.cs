using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class IndexHistoryTests
{
    [Fact]
    public void IndexHistoryIsAnIndependentCachedPublicOperation()
    {
        Assert.Contains("market.history", ComputeJobRepository.IndependentPublicKinds);
        Assert.Contains("market.history", ComputeJobRepository.AllowedKinds);
        Assert.Equal(3600, ComputeJobRepository.PublicCacheSeconds("market.history"));
    }

    [PgFact]
    public async Task CompletedIndexHistoryIsSharedWithoutAnotherWorkerRequest()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        const string input = "{\"symbol\":\"000300\"}";
        const string result = "{\"symbol\":\"000300\",\"name\":\"CSI300\",\"source\":\"explicit-local-test\",\"items\":[{\"date\":\"2026-10-16\",\"open\":\"10\",\"high\":\"12\",\"low\":\"9\",\"close\":\"11\"}],\"unavailable\":[],\"stale\":false,\"observed_at\":\"2026-10-18T00:00:00Z\"}";
        var id = await jobs.EnqueueAsync(user.Id, "market.history", input, DateTime.UtcNow.AddMinutes(1));
        var claim = (await jobs.ClaimAsync("history-test", ["market.history"]))!;
        Assert.True(await jobs.FinishAsync(user.Id, id, claim.Attempt, claim.OwnerToken, result, null));
        var repository = new PublicSnapshotRepository(new AccountDatabase(user.Connection));
        Assert.NotNull(await repository.ReadAsync("market.history", input));
        var config = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string,string?>()).Build();
        using var memory = new MemoryCache(new MemoryCacheOptions());
        var service = new ComputeService(jobs, new AnalysisSnapshotRepository(new AccountDatabase(user.Connection)), config, memory, repository);
        var value = await service.ReadPublic(user.Id, "market.history", new {symbol="000300"}, CancellationToken.None);
        Assert.Equal("hit", value.Cache);
        Assert.Equal("000300", value.Data!["symbol"]!.GetValue<string>());
        Assert.Equal(1L, user.Query("SELECT count(*) FROM compute_jobs WHERE user_id=$1", user.Id));
    }
}
