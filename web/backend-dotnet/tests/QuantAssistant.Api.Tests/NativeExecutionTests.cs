using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Infrastructure.Runtime;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class NativeExecutionTests
{
    [PgFact]
    public void PublicReads_ReusesFreshCompletedResultAfterDeadline_AndRefreshDeduplicatesPending()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        const string input = "{\"symbol\":\"004243\"}";
        var first = jobs.Enqueue(user.Id, "fund.detail", input, DateTime.UtcNow.AddMinutes(2));
        var claim = jobs.Claim("synthetic-cache", ["fund.detail"])!;
        Assert.True(jobs.Finish(user.Id, first, claim.Attempt, claim.OwnerToken, "{\"symbol\":\"004243\"}", null));
        user.Execute("UPDATE compute_jobs SET deadline=now()-interval '1 second' WHERE user_id=$1", user.Id);
        Assert.Equal(first, jobs.Enqueue(user.Id, "fund.detail", input, DateTime.UtcNow.AddMinutes(2)));
        Assert.NotEqual(first, jobs.Enqueue(other.Id, "fund.detail", input, DateTime.UtcNow.AddMinutes(2)));
        var refresh = jobs.Enqueue(user.Id, "fund.detail", input, DateTime.UtcNow.AddMinutes(2), reuseCompleted: false);
        Assert.NotEqual(first, refresh);
        Assert.Equal(refresh, jobs.Enqueue(user.Id, "fund.detail", input, DateTime.UtcNow.AddMinutes(2), reuseCompleted: false));
    }

    [PgFact]
    public void PublicReads_CacheExpiryDoesNotExtendFinancialOrPaidResults()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        var first = jobs.Enqueue(user.Id, "fund.detail", "{}", DateTime.UtcNow.AddMinutes(20));
        var claim = jobs.Claim("synthetic-cache-expired", ["fund.detail"])!;
        Assert.True(jobs.Finish(user.Id, first, claim.Attempt, claim.OwnerToken, "{}", null));
        user.Execute("UPDATE compute_jobs SET completed_at=now()-interval '6 minutes' WHERE user_id=$1", user.Id);
        Assert.NotEqual(first, jobs.Enqueue(user.Id, "fund.detail", "{}", DateTime.UtcNow.AddMinutes(2)));
        var analysis = jobs.Enqueue(user.Id, "analysis.data_status", "{}", DateTime.UtcNow.AddMinutes(2));
        var financial = jobs.Claim("synthetic-financial-cache", ["analysis.data_status"])!;
        Assert.True(jobs.Finish(user.Id, analysis, financial.Attempt, financial.OwnerToken, "{}", null));
        user.Execute("UPDATE compute_jobs SET deadline=now()-interval '1 second' WHERE user_id=$1 AND kind='analysis.data_status'", user.Id);
        Assert.NotEqual(analysis, jobs.Enqueue(user.Id, "analysis.data_status", "{}", DateTime.UtcNow.AddMinutes(2)));
    }

    [PgFact]
    public void IndependentPublicReads_CanShareAnAccountWithoutExceedingThreeWorkers()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        jobs.Enqueue(user.Id, "analysis.data_status", "{}", DateTime.UtcNow.AddMinutes(2));
        var financial = jobs.Claim("synthetic-financial", ["analysis.data_status"])!;
        Assert.NotNull(financial);
        foreach (var symbol in new[] { "004243", "510300", "000001" })
            jobs.Enqueue(user.Id, "fund.detail", "{\"symbol\":\"" + symbol + "\"}", DateTime.UtcNow.AddMinutes(2));
        var first = jobs.Claim("synthetic-public-one", ["fund.detail"]);
        var second = jobs.Claim("synthetic-public-two", ["fund.detail"]);
        Assert.NotNull(first); Assert.NotNull(second);
        Assert.Null(jobs.Claim("synthetic-public-full", ["fund.detail"]));
        jobs.Enqueue(user.Id, "analysis.data_status", "{\"nonce\":1}", DateTime.UtcNow.AddMinutes(2));
        Assert.Null(jobs.Claim("synthetic-second-financial", ["analysis.data_status"]));
    }

    [PgFact]
    public void AccountSnapshot_UsesTheCompleteSchemaAndExactFinancialText()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var snapshot = new AnalysisSnapshotRepository(new AccountDatabase(user.Connection)).Create(user.Id);
        Assert.NotNull(snapshot["tables"]!["fund_holding_lots"]);
        Assert.Equal("10000.00000000", snapshot["tables"]!["positions"]![0]!["quantity"]!.GetValue<string>());
        Assert.Null(snapshot["tables"]!["positions"]![0]!["user_id"]);
    }

    [PgFact]
    public void ComputeClaims_AllowThreeAccountsAndOnlyOneJobPerAccount()
    {
        using var first = new PgTestUser();
        using var second = new PgTestUser();
        using var third = new PgTestUser();
        using var fourth = new PgTestUser();
        var jobs = new ComputeJobRepository(first.Connection);
        foreach (var user in new[] { first, first, second, third, fourth })
            jobs.Enqueue(user.Id, "analysis.data_status", "{\"nonce\":\"" + Guid.NewGuid() + "\"}", DateTime.UtcNow.AddMinutes(2));

        var claims = Enumerable.Range(1, 3).Select(i => jobs.Claim("synthetic-" + i, ["analysis.data_status"])!).ToArray();
        Assert.All(claims, Assert.NotNull);
        Assert.Equal(3, claims.Select(job => job.UserId).Distinct().Count());
        Assert.Null(jobs.Claim("synthetic-four", ["analysis.data_status"]));
        var own = Assert.Single(claims, job => job.UserId == first.Id);
        Assert.True(jobs.Finish(own.UserId, own.JobId, own.Attempt, own.OwnerToken, "{}", null));
        Assert.NotNull(jobs.Claim("synthetic-next", ["analysis.data_status"]));
        Assert.Null(jobs.Claim("synthetic-full", ["analysis.data_status"]));
    }

    [PgFact]
    public void ExpiredLease_RejectsLateResultsAndDoesNotRestart()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        var id = jobs.Enqueue(user.Id, "analysis.data_status", "{}", DateTime.UtcNow.AddMinutes(2));
        var claim = jobs.Claim("synthetic-expire", ["analysis.data_status"])!;
        Assert.False(jobs.Heartbeat(user.Id, id, claim.Attempt, "wrong-owner"));
        user.Execute("UPDATE compute_jobs SET lease_expires_at=now()-interval '1 second' WHERE user_id=$1", user.Id);
        Assert.False(jobs.Finish(user.Id, id, claim.Attempt, claim.OwnerToken, "{}", null));
        jobs.Recover();
        Assert.Equal("interrupted", jobs.Read(user.Id, id).Status);
        Assert.Null(jobs.Claim("synthetic-restart", ["analysis.data_status"]));
    }

    [PgFact]
    public void DisabledAccount_StopsHeartbeatAndCompletion()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        var id = jobs.Enqueue(user.Id, "analysis.data_status", "{}", DateTime.UtcNow.AddMinutes(2));
        var claim = jobs.Claim("synthetic-disabled", ["analysis.data_status"])!;
        user.Execute("UPDATE users SET status='disabled' WHERE id=$1", user.Id);
        Assert.False(jobs.Heartbeat(user.Id, id, claim.Attempt, claim.OwnerToken));
        Assert.False(jobs.Finish(user.Id, id, claim.Attempt, claim.OwnerToken, "{}", null));
        jobs.Recover();
        Assert.Equal("interrupted", jobs.Read(user.Id, id).Status);
    }

    [PgFact]
    public void CostBudget_ReservesAtomicallyAndUnknownUsageBlocksFurtherCalls()
    {
        using var user = new PgTestUser();
        var costs = new ModelCostRepository(new AccountDatabase(user.Connection));
        costs.Save(user.Id, new(0, DailyBudget: 0.025m, Prices: new() { ["synthetic-model"] = new(1, 2) }));
        var call = costs.Reserve(user.Id, "synthetic-model", 10000, 5000);
        var failure = Assert.Throws<ApplicationFailure>(() => costs.Reserve(user.Id, "synthetic-model", 10000, 5000));
        Assert.Equal("model_budget_exceeded", failure.Code);
        Assert.Equal(1L, user.Query("SELECT count(*) FROM model_call_costs WHERE user_id=$1", user.Id));
        costs.Finish(user.Id, call, null, null);
        var summary = costs.Read(user.Id)["today"]!;
        Assert.Null(summary["total_cost"]);
        Assert.Equal("unknown_usage_or_price", summary["budget_blocker"]!.GetValue<string>());
        Assert.Throws<ApplicationFailure>(() => costs.Reserve(user.Id, "synthetic-model", 1, 1));
    }

    [PgFact]
    public void CostUsage_ExactZeroAndKnownCosts_DoNotTurnMissingPriceIntoFreeCalls()
    {
        using var user = new PgTestUser();
        var costs = new ModelCostRepository(new AccountDatabase(user.Connection));
        costs.Save(user.Id, new(0, Prices: new() { ["synthetic-model"] = new(1, 2) }));
        var zero = costs.Reserve(user.Id, "synthetic-model", 1, 1);
        costs.Finish(user.Id, zero, 0, 0);
        Assert.Equal("0", costs.Read(user.Id)["today"]!["total_cost"]!.GetValue<string>());
        var known = costs.Reserve(user.Id, "synthetic-model", 123, 45);
        costs.Finish(user.Id, known, 123, 45);
        Assert.Equal("0.000213", costs.Read(user.Id)["today"]!["total_cost"]!.GetValue<string>());
        var unknown = costs.Reserve(user.Id, "unpriced-model", 123, 45);
        costs.Finish(user.Id, unknown, 123, 45);
        var today = costs.Read(user.Id)["today"]!;
        Assert.Equal("0.000213", today["known_cost"]!.GetValue<string>());
        Assert.Equal(1, today["unknown_calls"]!.GetValue<int>());
        Assert.Null(today["total_cost"]);
    }

    [PgFact]
    public void PaidReservation_RejectsAnotherOwnerAndExpiredLease()
    {
        using var user = new PgTestUser();
        var jobs = new ComputeJobRepository(user.Connection);
        var id = jobs.Enqueue(user.Id, "analysis.data_status", "{}", DateTime.UtcNow.AddMinutes(2));
        var claim = jobs.Claim("synthetic-paid", ["analysis.data_status"])!;
        user.Execute("UPDATE compute_jobs SET kind='advisor.report' WHERE user_id=$1", user.Id);
        var costs = new ModelCostRepository(new AccountDatabase(user.Connection));
        var stale = Assert.Throws<ApplicationFailure>(() => costs.ReserveOwned(user.Id, id, claim.Attempt, "other-owner", "synthetic-model", 1, 1));
        Assert.Equal("worker_lease_expired", stale.Code);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM model_call_costs WHERE user_id=$1", user.Id));
        var call = costs.ReserveOwned(user.Id, id, claim.Attempt, claim.OwnerToken, "synthetic-model", 1, 1);
        user.Execute("UPDATE compute_jobs SET lease_expires_at=now()-interval '1 second' WHERE user_id=$1", user.Id);
        Assert.Throws<ApplicationFailure>(() => costs.FinishOwned(user.Id, id, claim.Attempt, claim.OwnerToken, call, 1, 1));
        Assert.Equal("reserved", user.Query("SELECT status FROM model_call_costs WHERE user_id=$1 AND id=$2", user.Id, call));
    }
}
