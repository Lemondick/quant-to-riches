using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Security;
using QuantAssistant.Core.Jobs;
using QuantAssistant.Infrastructure.Jobs;
using Microsoft.Extensions.Caching.Memory;
using Xunit;
using TradeAction = QuantAssistant.Core.Portfolio.Action;

[assembly: CollectionBehavior(DisableTestParallelization = true)]

namespace QuantAssistant.Api.Tests;

public sealed class PgFactAttribute : FactAttribute
{
    public PgFactAttribute()
    {
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION")))
            Skip = "QUANT_TEST_PG_CONNECTION is required for PostgreSQL integration tests.";
    }

    /// <summary>
    /// 显式跳过：用于引用已下线旧版 API 的用例（#6）。reason 说明需重写的原因，
    /// 在任何环境都跳过，避免 CI 因旧路由 404 而失败。重写完成后移除该参数。
    /// </summary>
    public PgFactAttribute(string skipReason)
    {
        Skip = skipReason;
    }
}

internal sealed class PgTestUser : IDisposable
{
    internal const string Password = "SyntheticReviewPassword123!";
    internal string Id { get; } = Guid.NewGuid().ToString("N");
    internal string Username => "u_" + Id[..20];
    internal string Connection { get; } = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION")!;

    internal PgTestUser()
    {
        Execute("INSERT INTO users(id,username,password_hash,role,status,created_at) VALUES ($1,$2,$3,'user','active',now())",
            Id, Username, new Argon2PasswordHasher().Hash(Password));
    }

    internal void Execute(string sql, params object[] values) => Query(sql, values);
    internal object? Query(string sql, params object[] values)
    {
        using var conn = new NpgsqlConnection(Connection);
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        foreach (var value in values) cmd.Parameters.AddWithValue(value);
        return cmd.ExecuteScalar();
    }

    internal void SeedLedger()
    {
        Execute("INSERT INTO instruments(user_id,symbol,asset_type,name,as_of_date) VALUES ($1,'510300','fund','Synthetic','2026-01-01')", Id);
        Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES ($1,'CNY','cash',10000,1,10000,'2026-01-01',now())", Id);
    }

    internal static void AttachSession(HttpClient client, string connection, string userId)
    {
        var token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(token))).ToLowerInvariant();
        using var conn = new NpgsqlConnection(connection);
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO sessions(token_hash,session_id,user_id,csrf,device,created_at,expires_at) VALUES ($1,$2,$3,'test-csrf','test',now(),now()+interval '1 hour')";
        cmd.Parameters.AddWithValue(digest);
        cmd.Parameters.AddWithValue(Guid.NewGuid().ToString("N"));
        cmd.Parameters.AddWithValue(userId);
        cmd.ExecuteNonQuery();
        client.DefaultRequestHeaders.Add("Cookie", "quant_session=" + token);
        client.DefaultRequestHeaders.Add("X-CSRF-Token", "test-csrf");
    }

    public void Dispose()
    {
        Execute("DELETE FROM worker_heartbeats WHERE worker_id IN(SELECT worker_id FROM compute_jobs WHERE user_id=$1)", Id);
        // user_operation_audit.actor_id 引用 users(id) 且无级联删除，必须先于 users 清理，
        // 否则 Dispose 自身触发 23503 外键违规并留下残留用户（batch4 流 A 实测）。
        Execute("DELETE FROM user_operation_audit WHERE actor_id=$1", Id);
        foreach (var table in new[] { "ledger_reconciliations", "account_events", "account_positions", "portfolio_accounts", "ledger_replay_previews", "user_documents", "model_call_costs", "model_cost_settings", "compute_jobs", "portfolio_valuations", "data_snapshots" })
            Execute($"DELETE FROM {table} WHERE user_id=$1", Id);
        foreach (var table in new[] { "account_activity", "usage_daily", "audit_log", "ledger_corrections", "dividend_events", "corporate_action_events", "portfolio_events", "transactions", "positions", "instruments", "dca_plans", "attribution_reports", "sessions", "recovery_tokens", "agent_research_attempts", "agent_research_jobs" })
            Execute($"DELETE FROM {table} WHERE user_id=$1", Id);
        Execute("DELETE FROM users WHERE id=$1", Id);
    }
}

public sealed class StabilityRegressionTests
{
    private static TransactionInput Trade(string id, DateTimeOffset? when = null) => new(
        "510300", AssetType.Fund, TradeAction.Buy, 100, 10, 0,
        when ?? new DateTimeOffset(2026, 10, 5, 10, 0, 0, TimeSpan.FromHours(8)), id);

    private static WebApplicationFactory<Program> Factory(string connection)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", connection);
        return new WebApplicationFactory<Program>();
    }

    [PgFact]
    public async Task AnonymousIdentityHeader_CannotReadOrWriteAnotherAccount()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        client.DefaultRequestHeaders.Add("X-Test-User", user.Id);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/v1/dca/plans")).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/api/v1/dca/plans", new { symbol = "510300", base_amount = 100 })).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/v1/runtime/status")).StatusCode);
    }

    [PgFact]
    public void DisabledAccount_CannotLoginOrUseOldSession()
    {
        using var user = new PgTestUser();
        var auth = new AuthService(new AccountRepository(user.Connection), new Argon2PasswordHasher(), TimeProvider.System);
        var session = auth.CreateSession(user.Id, "test");
        user.Execute("UPDATE users SET status='disabled' WHERE id=$1", user.Id);
        Assert.Null(auth.Authenticate(user.Username, PgTestUser.Password));
        Assert.Null(auth.SessionAccount(session.Token));
    }

    [PgFact]
    public async Task AuthenticatedWrite_RequiresCsrfAndSameOrigin()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        client.DefaultRequestHeaders.Remove("X-CSRF-Token");
        Assert.Equal(HttpStatusCode.Forbidden, (await client.PostAsJsonAsync("/api/v1/dca/plans", new { symbol = "510300", base_amount = 100 })).StatusCode);
        client.DefaultRequestHeaders.Add("X-CSRF-Token", "test-csrf");
        // 注意：TestClientFactory 已预置 Origin: http://localhost，直接 Add 会产生双 Origin 头，
        // 中间件取 FirstOrDefault() 仍会命中合法值；此处必须先移除再设置才能真正模拟不可信来源。
        client.DefaultRequestHeaders.Remove("Origin");
        client.DefaultRequestHeaders.Add("Origin", "https://untrusted.invalid");
        Assert.Equal(HttpStatusCode.Forbidden, (await client.PostAsJsonAsync("/api/v1/dca/plans", new { symbol = "510300", base_amount = 100 })).StatusCode);
    }

    [PgFact]
    public async Task Login_UsesExistingFrontendSessionContract()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        var response = await client.PostAsJsonAsync("/api/v1/auth/login", new { username = user.Username, password = PgTestUser.Password });
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.False(string.IsNullOrEmpty(body.GetProperty("csrf_token").GetString()));
        Assert.True(body.TryGetProperty("expires_at", out _));
    }

    [PgFact]
    public async Task RecoveryToken_IsConsumedOnceAndRevokesExistingSessions()
    {
        using var user = new PgTestUser();
        var auth = new AuthService(new AccountRepository(user.Connection), new Argon2PasswordHasher(), TimeProvider.System);
        var session = auth.CreateSession(user.Id, "test");
        var recovery = auth.IssueRecovery(user.Id, user.Id);
        using var factory = Factory(user.Connection);
        using var first = factory.CreateTestClient();
        using var second = factory.CreateTestClient();
        const string password = "SyntheticNewPassword123!";
        var body = new { username = user.Username, token = recovery.Token, new_password = password };
        var responses = await Task.WhenAll(first.PostAsJsonAsync("/api/v1/auth/recovery/redeem", body), second.PostAsJsonAsync("/api/v1/auth/recovery/redeem", body));
        var success = Assert.Single(responses, response => response.IsSuccessStatusCode);
        Assert.Single(responses, response => !response.IsSuccessStatusCode);
        Assert.Equal("password_recovered", (await success.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("status").GetString());
        Assert.Contains(success.Headers.GetValues("Set-Cookie"), cookie => cookie.StartsWith("quant_session=;", StringComparison.Ordinal));
        Assert.Null(auth.SessionAccount(session.Token));
        Assert.Null(auth.Authenticate(user.Username, PgTestUser.Password));
        Assert.NotNull(auth.Authenticate(user.Username, password));
    }

    [PgFact]
    public async Task ConcurrentTrades_AreAtomicAndDoNotLoseUpdates()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var svc = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        user.Execute("CREATE FUNCTION test_slow_trade() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END $$; CREATE TRIGGER test_slow_trade BEFORE INSERT ON transactions FOR EACH ROW EXECUTE FUNCTION test_slow_trade()");
        try
        {
            await Task.WhenAll(
                Task.Factory.StartNew(() => svc.RecordTransaction(user.Id, Trade("A")), TaskCreationOptions.LongRunning),
                Task.Factory.StartNew(() => svc.RecordTransaction(user.Id, Trade("B")), TaskCreationOptions.LongRunning));
            Assert.Equal(200m, (decimal)user.Query("SELECT quantity FROM positions WHERE user_id=$1 AND symbol='510300'", user.Id)!);
            Assert.Equal(8000m, (decimal)user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id)!);
        }
        finally { user.Execute("DROP TRIGGER test_slow_trade ON transactions; DROP FUNCTION test_slow_trade()"); }
    }

    [PgFact]
    public void AuditFailure_RollsBackTheEntireTrade()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var svc = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        user.Execute("CREATE FUNCTION test_fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic fault'; END $$; CREATE TRIGGER test_fail_audit BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION test_fail_audit()");
        try
        {
            Assert.Throws<PostgresException>(() => svc.RecordTransaction(user.Id, Trade("FAULT")));
            Assert.Equal(0L, (long)user.Query("SELECT count(*) FROM transactions WHERE user_id=$1", user.Id)!);
            Assert.Equal(10000m, (decimal)user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id)!);
        }
        finally { user.Execute("DROP TRIGGER test_fail_audit ON audit_log; DROP FUNCTION test_fail_audit()"); }
    }

    [PgFact]
    public void FutureTrade_IsRejectedAndRecordedTimestampIsUtc()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var svc = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        Assert.Throws<ArgumentException>(() => svc.RecordTransaction(user.Id, Trade("FUTURE", DateTimeOffset.UtcNow.AddHours(1).ToOffset(TimeSpan.FromHours(8)))));
        svc.RecordTransaction(user.Id, Trade("PAST"));
        Assert.InRange((decimal)user.Query("SELECT extract(epoch FROM recorded_at-now()) FROM transactions WHERE user_id=$1", user.Id)!, -30m, 1m);
    }

    [PgFact]
    public async Task PortfolioTotal_CountsCashOnce()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var body = await (await client.GetAsync("/api/v1/portfolio")).Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(10000m, body.GetProperty("total_value").DecimalText());
        Assert.Equal("2026-01-01", body.GetProperty("as_of_date").GetString());
        Assert.Equal("cash", body.GetProperty("bucket_weights")[0].GetProperty("name").GetString());
        Assert.Equal(JsonValueKind.String, body.GetProperty("positions")[0].GetProperty("market_value").ValueKind);
    }

    [PgFact]
    public async Task EmptyAccount_HasAnEmptyPortfolio()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var response = await client.GetAsync("/api/v1/portfolio");
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, body.GetProperty("positions").GetArrayLength());
        Assert.Equal(JsonValueKind.Null, body.GetProperty("as_of_date").ValueKind);
    }

    [PgFact]
    public async Task LedgerTrade_RegistersInstrumentAndPreservesFrontendContract()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        user.Execute("DELETE FROM instruments WHERE user_id=$1", user.Id);
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var trade = new { entry_type = "buy", symbol = "510300", name = "测试基金", asset_type = "fund", quantity = "100.5", price = "10", fees = "1", occurred_at = "2026-10-05T10:00:00+08:00", external_id = "API-TRADE", note = "test" };
        var response = await client.PostAsJsonAsync("/api/v1/ledger/transactions", trade);
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var entry = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("测试基金", entry.GetProperty("name").GetString());
        Assert.Equal("buy", entry.GetProperty("entry_type").GetString());
        Assert.Equal(100.5m, decimal.Parse(entry.GetProperty("quantity").GetString()!, System.Globalization.CultureInfo.InvariantCulture));
        Assert.Equal(-1006m, decimal.Parse(entry.GetProperty("net_amount").GetString()!, System.Globalization.CultureInfo.InvariantCulture));
        Assert.Equal("manual-api", user.Query("SELECT source FROM transactions WHERE user_id=$1", user.Id));
        var retry = await client.PostAsJsonAsync("/api/v1/ledger/transactions", trade);
        retry.EnsureSuccessStatusCode();
        Assert.Equal(entry.GetProperty("id").GetInt64(), (await retry.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetInt64());
        Assert.Equal(1L, user.Query("SELECT count(*) FROM transactions WHERE user_id=$1", user.Id));
        var dividend = await client.PostAsJsonAsync("/api/v1/ledger/dividends", new { symbol = "510300", asset_type = "fund", gross_amount = "50", fees = "2", occurred_at = "2026-10-05T11:00:00+08:00", external_id = "API-DIVIDEND" });
        dividend.EnsureSuccessStatusCode();
        var dividendEntry = await dividend.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("dividend", dividendEntry.GetProperty("entry_type").GetString());
        Assert.Equal(JsonValueKind.Null, dividendEntry.GetProperty("quantity").ValueKind);
        var ledger = await client.GetAsync("/api/v1/ledger?limit=1");
        ledger.EnsureSuccessStatusCode();
        var body = await ledger.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Single(body.GetProperty("entries").EnumerateArray());
        Assert.Equal(3m, decimal.Parse(body.GetProperty("total_fees").GetString()!, System.Globalization.CultureInfo.InvariantCulture));
        Assert.Equal(48m, decimal.Parse(body.GetProperty("total_dividends").GetString()!, System.Globalization.CultureInfo.InvariantCulture));
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.GetAsync("/api/v1/ledger?limit=501")).StatusCode);
    }

    [PgFact]
    public async Task MigratedLedgerTrade_RetryKeepsOriginalIdAndCash()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var svc = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        var id = svc.RecordTransaction(user.Id, Trade("MIGRATED") with { Source = "manual-api" });
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var response = await client.PostAsJsonAsync("/api/v1/ledger/transactions", new { entry_type = "buy", symbol = "510300", name = "Synthetic", asset_type = "fund", quantity = "100", price = "10", fees = "0", occurred_at = "2026-10-05T10:00:00+08:00", external_id = "MIGRATED" });
        response.EnsureSuccessStatusCode();
        Assert.Equal(id, (await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetInt64());
        Assert.Equal(9000m, user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id));
        Assert.Equal(1L, user.Query("SELECT count(*) FROM transactions WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public async Task ReversedLedgerEntry_RemainsVisibleAndIsExcludedFromTotals()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var svc = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        var id = svc.RecordDividend(user.Id, new DividendInput("510300", AssetType.Fund, 50, 2, new DateTimeOffset(2026, 10, 5, 10, 0, 0, TimeSpan.FromHours(8)), "DIV"));
        svc.CorrectLedgerEntry(user.Id, new LedgerCorrectionInput("dividend", id, "test", new DateTimeOffset(2026, 10, 5, 11, 0, 0, TimeSpan.FromHours(8)), "REVERSE"));
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var response = await client.GetAsync("/api/v1/ledger");
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.True(body.GetProperty("entries")[0].GetProperty("reversed").GetBoolean());
        Assert.Equal("0", body.GetProperty("total_fees").GetString());
        Assert.Equal("0", body.GetProperty("total_dividends").GetString());
    }

    [PgFact]
    public async Task RejectedNewInstrumentTrade_RollsBackMetadataAndLedger()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        user.Execute("DELETE FROM instruments WHERE user_id=$1", user.Id);
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var response = await client.PostAsJsonAsync("/api/v1/ledger/transactions", new { entry_type = "buy", symbol = "510300", name = "Synthetic", asset_type = "fund", quantity = "100000", price = "10", fees = "0", occurred_at = "2026-10-05T10:00:00+08:00", external_id = "INSUFFICIENT" });
        Assert.False(response.IsSuccessStatusCode);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM instruments WHERE user_id=$1", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM transactions WHERE user_id=$1", user.Id));
        Assert.Equal(10000m, user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id));
    }

    [PgFact]
    public async Task LedgerReads_AreIsolatedByAuthenticatedOwner()
    {
        using var owner = new PgTestUser();
        using var other = new PgTestUser();
        other.SeedLedger();
        var id = new TransactionService(new LedgerRepository(owner.Connection), TimeProvider.System).RecordTransaction(other.Id, Trade("PRIVATE"));
        using var factory = Factory(owner.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, owner.Connection, owner.Id);
        client.DefaultRequestHeaders.Add("X-Test-User", other.Id);
        var response = await client.GetAsync("/api/v1/ledger");
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Empty(body.GetProperty("entries").EnumerateArray());
        Assert.Equal("0", body.GetProperty("total_fees").GetString());
        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync($"/api/v1/ledger/transactions/{id}")).StatusCode);
    }

    [PgFact]
    public async Task DcaTrade_UsesOriginalSourceAndIsVisibleAtReturnedLocation()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        var response = await client.PostAsJsonAsync("/api/v1/ledger/transactions", new { entry_type = "dca", symbol = "510300", name = "Synthetic", asset_type = "fund", quantity = "100", price = "10", fees = "0", occurred_at = "2026-10-05T10:00:00+08:00", external_id = "DCA-API" });
        response.EnsureSuccessStatusCode();
        Assert.Equal("dca", user.Query("SELECT source FROM transactions WHERE user_id=$1", user.Id));
        var entry = await (await client.GetAsync(response.Headers.Location!)).Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("dca", entry.GetProperty("entry_type").GetString());
        Assert.Equal("DCA-API", entry.GetProperty("external_id").GetString());
    }

    [PgFact]
    public void CorporateActions_RejectNonpositiveInputsWithoutChangingPositions()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var service = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        service.RecordTransaction(user.Id, Trade("BUY"));
        var when = new DateTimeOffset(2026, 10, 5, 11, 0, 0, TimeSpan.FromHours(8));
        Assert.Throws<ArgumentException>(() => service.RecordCorporateAction(user.Id, new CorporateActionInput("split", "510300", AssetType.Fund, when, "BAD-SPLIT", Ratio: -1)));
        Assert.Throws<ArgumentException>(() => service.RecordCorporateAction(user.Id, new CorporateActionInput("dividend_reinvestment", "510300", AssetType.Fund, when, "BAD-PRICE", Price: 0, GrossAmount: 100)));
        Assert.Equal(100m, user.Query("SELECT quantity FROM positions WHERE user_id=$1 AND symbol='510300'", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM corporate_action_events WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public void TradeCorrection_AfterSplitIsRejectedWithoutChangingLedger()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var service = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        var id = service.RecordTransaction(user.Id, Trade("BUY"));
        var when = new DateTimeOffset(2026, 10, 5, 11, 0, 0, TimeSpan.FromHours(8));
        service.RecordCorporateAction(user.Id, new CorporateActionInput("split", "510300", AssetType.Fund, when, "SPLIT", Ratio: 2));
        var error = Assert.Throws<QuantAssistant.Core.Errors.LedgerStateError>(() => service.CorrectLedgerEntry(user.Id, new LedgerCorrectionInput("transaction", id, "test", when.AddHours(1), "BAD-CORRECTION")));
        Assert.Equal("ledger_correction_dependent_events", error.Code);
        Assert.Equal(200m, user.Query("SELECT quantity FROM positions WHERE user_id=$1 AND symbol='510300'", user.Id));
        Assert.Equal(9000m, user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM ledger_corrections WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public void TradeCorrection_CannotConsumeCashAlreadySpent()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var service = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        var when = new DateTimeOffset(2026, 10, 5, 10, 0, 0, TimeSpan.FromHours(8));
        service.RecordTransaction(user.Id, Trade("BUY") with { Quantity = 1000 });
        var sell = service.RecordTransaction(user.Id, Trade("SELL", when.AddHours(1)) with { Action = TradeAction.Sell, Quantity = 1000 });
        service.RecordTransaction(user.Id, Trade("REBUY", when.AddHours(2)) with { Quantity = 1000 });
        Assert.Throws<ArgumentException>(() => service.CorrectLedgerEntry(user.Id, new LedgerCorrectionInput("transaction", sell, "test", when.AddHours(3), "NO-CASH")));
        Assert.Equal(1000m, user.Query("SELECT quantity FROM positions WHERE user_id=$1 AND symbol='510300'", user.Id));
        Assert.Equal(0m, user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id));
        Assert.Equal(0L, user.Query("SELECT count(*) FROM ledger_corrections WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public void Correction_CannotPredateTheOriginalEvent()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var service = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        var when = new DateTimeOffset(2026, 10, 5, 10, 0, 0, TimeSpan.FromHours(8));
        var id = service.RecordTransaction(user.Id, Trade("BUY", when));
        var error = Assert.Throws<QuantAssistant.Core.Errors.LedgerStateError>(() => service.CorrectLedgerEntry(user.Id, new LedgerCorrectionInput("transaction", id, "test", when.AddMinutes(-1), "BEFORE")));
        Assert.Equal("ledger_correction_date_invalid", error.Code);
        Assert.Equal(0L, user.Query("SELECT count(*) FROM ledger_corrections WHERE user_id=$1", user.Id));
    }

    [PgFact]
    public void ConfirmedCalibration_BlocksEarlierTradeCorrection()
    {
        using var user = new PgTestUser();
        user.SeedLedger();
        var service = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        var id = service.RecordTransaction(user.Id, Trade("BUY"));
        user.Execute("INSERT INTO portfolio_events(user_id,event_id,event_type,effective_date,amount,status,reason,recorded_at) VALUES($1,'CAL','calibration','2026-10-05',0,'confirmed','test',now())", user.Id);
        var error = Assert.Throws<QuantAssistant.Core.Errors.LedgerStateError>(() => service.CorrectLedgerEntry(user.Id, new LedgerCorrectionInput("transaction", id, "test", new DateTimeOffset(2026, 10, 5, 12, 0, 0, TimeSpan.FromHours(8)), "BAD-CORRECTION")));
        Assert.Equal("ledger_correction_dependent_events", error.Code);
        Assert.Equal(100m, user.Query("SELECT quantity FROM positions WHERE user_id=$1 AND symbol='510300'", user.Id));
    }

    [PgFact]
    public void AnotherUsersCalibration_DoesNotBlockOwnTradeCorrection()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        user.SeedLedger();
        var service = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        var id = service.RecordTransaction(user.Id, Trade("BUY"));
        other.Execute("INSERT INTO portfolio_events(user_id,event_id,event_type,effective_date,amount,status,reason,recorded_at) VALUES($1,'CAL','calibration','2026-10-05',0,'confirmed','test',now())", other.Id);
        service.CorrectLedgerEntry(user.Id, new LedgerCorrectionInput("transaction", id, "test", new DateTimeOffset(2026, 10, 5, 12, 0, 0, TimeSpan.FromHours(8)), "CORRECTION"));
        Assert.Equal(10000m, user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id));
    }

    [PgFact]
    public async Task ConcurrentRegistration_EnforcesThirtyAccountLimit()
    {
        using var user = new PgTestUser();
        using var registration = new RegistrationScope();
        var prefix = "cap-" + Guid.NewGuid().ToString("N") + "-";
        var count = (long)user.Query("SELECT count(*) FROM users")!;
        Assert.True(count < 29, "Use a clean isolated test database.");
        user.Execute("INSERT INTO users(id,username,password_hash,role,status,created_at) SELECT $1||g,$1||g,'x','user','active',now() FROM generate_series(1,$2) g", prefix, (int)(29 - count));
        try
        {
            string Register(int suffix)
            {
                try
                {
                    new AccountRepository(user.Connection).CreateUser(prefix + suffix, prefix + suffix, "x", AccountRole.User, DateTime.UtcNow);
                    return "registered";
                }
                catch (AuthFailure error) { return error.Code; }
            }
            var outcomes = await Task.WhenAll(Task.Factory.StartNew(() => Register(100), TaskCreationOptions.LongRunning),
                Task.Factory.StartNew(() => Register(101), TaskCreationOptions.LongRunning));
            Assert.Single(outcomes, s => s == "registered");
            Assert.Single(outcomes, s => s == "registration_full");
            Assert.Equal(30L, (long)user.Query("SELECT count(*) FROM users")!);
        }
        finally { user.Execute("DELETE FROM users WHERE id LIKE $1", prefix + "%"); }
    }

    [PgFact]
    public async Task IdentityHeader_CannotOverrideAuthenticatedAccount()
    {
        using var owner = new PgTestUser();
        using var other = new PgTestUser();
        using var factory = Factory(owner.Connection);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, owner.Connection, owner.Id);
        client.DefaultRequestHeaders.Add("X-Test-User", other.Id);
        var response = await client.PostAsJsonAsync("/api/v1/dca/plans", new { symbol = "510300", base_amount = 100 });
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(owner.Id, body.GetProperty("user_id").GetString());
        Assert.Equal(HttpStatusCode.Forbidden, (await client.GetAsync("/api/v1/admin/users")).StatusCode);
    }

    [PgFact]
    public async Task DatabaseReady_HealthProbeReturnsSuccess()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user.Connection);
        using var client = factory.CreateTestClient();
        Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/readyz")).StatusCode);
    }

    [PgFact]
    public void ExpiredWorkerResult_CannotCompleteJob()
    {
        using var user = new PgTestUser();
        var jobs = new JobRepository(user.Connection);
        var now = DateTime.UtcNow;
        var (id, _) = jobs.Enqueue(user.Id, "expired", "510300", "test", "mock", "model", DateOnly.FromDateTime(now), now);
        jobs.NextJob(user.Id, now.AddSeconds(1));
        Assert.False(jobs.Finish(user.Id, id, 1, JobStatus.Completed, null, "{}", "{}", now.AddSeconds(2)));
    }

    [PgFact]
    public async Task ConcurrentEnqueue_ReturnsSameJobAndConsumesQuotaOnce()
    {
        using var user = new PgTestUser();
        var jobs = new JobRepository(user.Connection);
        var now = DateTime.UtcNow;
        user.Execute("CREATE FUNCTION test_slow_job() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END $$; CREATE TRIGGER test_slow_job BEFORE INSERT ON agent_research_jobs FOR EACH ROW EXECUTE FUNCTION test_slow_job()");
        try
        {
            (string JobId, bool Existing) Enqueue() => jobs.Enqueue(user.Id, "same", "510300", "test", "mock", "model", DateOnly.FromDateTime(now), now);
            var result = await Task.WhenAll(Task.Factory.StartNew(Enqueue, TaskCreationOptions.LongRunning), Task.Factory.StartNew(Enqueue, TaskCreationOptions.LongRunning));
            Assert.Equal(result[0].JobId, result[1].JobId);
            Assert.Equal(1L, (long)user.Query("SELECT count(*) FROM agent_research_attempts WHERE user_id=$1", user.Id)!);
        }
        finally { user.Execute("DROP TRIGGER test_slow_job ON agent_research_jobs; DROP FUNCTION test_slow_job()"); }
    }

    [PgFact]
    public void PeerRankingWrite_ImmediatelyInvalidatesReadCache()
    {
        using var user = new PgTestUser();
        var repo = new PeerRepository(user.Connection, new MemoryCache(new MemoryCacheOptions()));
        var group = Guid.NewGuid().ToString("N");
        var date = new DateOnly(2026, 10, 5);
        repo.CreateGroup(new PeerGroup { Id = group, GroupName = "synthetic" });
        try
        {
            repo.UpsertRankings(new[] { new PeerRanking { Symbol = "test", GroupId = group, DataDate = date, Return1M = 0.1m } });
            Assert.Equal(0.1m, repo.GetFundRanking("test", group, date)!.Return1M);
            repo.GetRankingsByGroup(group, date);
            repo.UpsertRankings(new[] { new PeerRanking { Symbol = "test", GroupId = group, DataDate = date, Return1M = 0.2m } });
            Assert.Equal(0.2m, repo.GetFundRanking("test", group, date)!.Return1M);
            Assert.Equal(0.2m, Assert.Single(repo.GetRankingsByGroup(group, date)).Return1M);
        }
        finally { user.Execute("DELETE FROM peer_groups WHERE id=$1", group); }
    }
}
