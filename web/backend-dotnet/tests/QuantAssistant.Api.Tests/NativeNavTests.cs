using System.Security.Cryptography;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Caching.Memory;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class NativeNavTests
{
    [PgFact]
    public async Task FormalNav_RejectsChangedSourceAndCommitsOnlyOnce()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        user.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES($1,'510300','fund',10,1,10,'2026-10-01',now())", user.Id);
        user.Execute("INSERT INTO portfolio_events(user_id,event_id,event_type,effective_date,status,reason,recorded_at) VALUES($1,'synthetic-baseline','baseline','2026-10-01','confirmed','synthetic',now())", user.Id);
        var directory = Path.Combine(Path.GetTempPath(), "quant-nav-test-" + Guid.NewGuid().ToString("N"));
        var sourceDirectory = Path.Combine(directory, "users", user.Id, "native-raw");
        Directory.CreateDirectory(sourceDirectory);
        try
        {
            var path = Path.Combine(sourceDirectory, "synthetic-source.bin");
            byte[] bytes = [1, 2, 3, 4];
            await File.WriteAllBytesAsync(path, bytes);
            var fileHash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
            var configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
                { ["QUANT_WORKER_SECRET"] = "synthetic-worker-secret-20261006-nav", ["QUANT_SITE_ROOT"] = directory }).Build();
            var database = new AccountDatabase(user.Connection);
            var jobs = new ComputeJobRepository(user.Connection);
            using var cache = new MemoryCache(new MemoryCacheOptions());
            var service = new FundNavMigrationService(database, new ComputeService(jobs, new AnalysisSnapshotRepository(database), configuration, cache), configuration);
            using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            var pending = service.Run(user.Id, new(2026, 10, 1), false, null, cancellation.Token);
            ComputeJob? claim = null;
            for (var i = 0; i < 100 && claim is null; i++)
            {
                claim = jobs.Claim("synthetic-nav-" + user.Id, ["fund.nav_sources"]);
                if (claim is null) await Task.Delay(20, cancellation.Token);
            }
            Assert.NotNull(claim);
            var result = new JsonObject { ["sources"] = new JsonArray(new JsonObject
                { ["symbol"] = "510300", ["unit_nav"] = "1.25", ["source"] = "eastmoney", ["snapshot_path"] = path,
                  ["snapshot_sha256"] = new string('a', 64), ["snapshot_file_sha256"] = fileHash, ["observed_at"] = DateTimeOffset.UtcNow.ToString("O") }), ["blockers"] = new JsonArray() };
            Assert.True(jobs.Finish(user.Id, claim.JobId, claim.Attempt, claim.OwnerToken, result.ToJsonString(), null));
            var preview = await pending;
            Assert.Equal("ready", preview["status"]!.GetValue<string>());
            var id = preview["preview_id"]!.GetValue<string>();
            await File.WriteAllTextAsync(path, "changed synthetic source");
            var error = await Assert.ThrowsAsync<ApplicationFailure>(() => service.Run(user.Id, new(2026, 10, 1), true, id, cancellation.Token));
            Assert.Equal("nav_source_changed", error.Code);
            Assert.Equal(0L, user.Query("SELECT count(*) FROM portfolio_valuations WHERE user_id=$1", user.Id));
            await File.WriteAllBytesAsync(path, bytes);
            Assert.Equal("recorded", (await service.Run(user.Id, new(2026, 10, 1), true, id, cancellation.Token))["status"]!.GetValue<string>());
            Assert.Equal("existing", (await service.Run(user.Id, new(2026, 10, 1), true, id, cancellation.Token))["status"]!.GetValue<string>());
            Assert.Equal(1L, user.Query("SELECT count(*) FROM portfolio_valuations WHERE user_id=$1", user.Id));
            Assert.Equal(10012.5m, user.Query("SELECT total_value FROM portfolio_valuations WHERE user_id=$1", user.Id));
        }
        finally { Directory.Delete(directory, recursive: true); }
    }
}
