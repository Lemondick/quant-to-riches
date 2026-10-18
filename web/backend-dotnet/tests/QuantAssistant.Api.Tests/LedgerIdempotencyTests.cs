using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Api.Errors;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;
using TradeAction = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Api.Tests;

public sealed class PgTheoryAttribute : TheoryAttribute
{
    public PgTheoryAttribute()
    {
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION")))
            Skip = "QUANT_TEST_PG_CONNECTION is required for PostgreSQL integration tests.";
    }
}

public sealed class LedgerIdempotencyTests
{
    private static readonly DateOnly Baseline = new(2026, 1, 1);
    private static DateTimeOffset At(int day) => new(2026, 1, day, 10, 0, 0, TimeSpan.FromHours(8));

    private sealed record Scenario(PgTestUser User, TransactionService Service, object Input, string Entity)
    {
        internal string Run(object input) => input switch
        {
            TransactionInput value => Service.RecordTransaction(User.Id, value).ToString(CultureInfo.InvariantCulture),
            DividendInput value => Service.RecordDividend(User.Id, value).ToString(CultureInfo.InvariantCulture),
            LedgerCorrectionInput value => Service.CorrectLedgerEntry(User.Id, value).ToString(CultureInfo.InvariantCulture),
            CorporateActionInput value => Service.RecordCorporateAction(User.Id, value).ToString(CultureInfo.InvariantCulture),
            ExternalCashFlowInput value => Service.RecordExternalCashFlow(User.Id, value),
            PortfolioCalibrationInput value => Service.RecordPortfolioCalibration(User.Id, value),
            _ => throw new InvalidOperationException("Unknown synthetic ledger case")
        };
    }

    private static Scenario Prepare(PgTestUser user, string kind)
    {
        user.SeedLedger();
        user.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES ($1,'510300','fund',100,1,100,'2026-01-01',now())", user.Id);
        user.Execute("INSERT INTO portfolio_valuations(user_id,run_id,as_of_date,generated_at,data_snapshot_id,total_value,cash,unit_nav,high_water_mark) VALUES ($1,'synthetic-baseline','2026-01-01',now(),1,10100,10000,1,1)", user.Id);
        var service = new TransactionService(new LedgerRepository(user.Connection), TimeProvider.System);
        object input;
        string entity;
        switch (kind)
        {
            case "transaction":
                input = new TransactionInput("510300", AssetType.Fund, TradeAction.Buy, 100m, 1m, 0m, At(3), "same-key", Note: "note");
                entity = "transaction";
                break;
            case "dividend":
                input = new DividendInput("510300", AssetType.Fund, 100m, 1m, At(3), "same-key", Note: "note");
                entity = "dividend";
                break;
            case "correction":
                var target = service.RecordTransaction(user.Id, new TransactionInput("510300", AssetType.Fund, TradeAction.Buy, 10m, 1m, 0m, At(2), "target"));
                input = new LedgerCorrectionInput("transaction", target, "reason", At(3), "same-key", Note: "note");
                entity = "ledger_correction";
                break;
            case "corporate":
                input = new CorporateActionInput("split", "510300", AssetType.Fund, At(3), "same-key", Ratio: 2m, Note: "note");
                entity = "corporate_action";
                break;
            case "flow":
                input = new ExternalCashFlowInput("same-key", CashFlowType.Deposit, 100m, Baseline.AddDays(2), "reason");
                entity = "external_cash_flow";
                break;
            case "calibration":
                input = new PortfolioCalibrationInput("same-key", Baseline.AddDays(2),
                    [new("CNY", AssetType.Cash, 9000m, 1m, 9000m, Baseline.AddDays(2)), new("510300", AssetType.Fund, 100m, 1m, 1000m, Baseline.AddDays(2))], 0m, "reason");
                entity = "portfolio_calibration";
                break;
            default: throw new ArgumentException("Unknown synthetic kind");
        }
        return new(user, service, input, entity);
    }

    private static string State(PgTestUser user) => new AccountDatabase(user.Connection).Read(sql => FinancialSnapshot.Hash(FinancialSnapshot.Read(sql, user.Id)));
    private static long Audits(Scenario value) => (long)value.User.Query("SELECT count(*) FROM audit_log WHERE user_id=$1 AND entity_type=$2", value.User.Id, value.Entity)!;
    private static string Holdings(PgTestUser user) => (string)user.Query("SELECT jsonb_agg(jsonb_build_array(symbol,asset_type,quantity,average_cost,market_value,as_of_date) ORDER BY symbol)::text FROM positions WHERE user_id=$1", user.Id)!;

    private static object Change(object input, string name, object? value)
    {
        var json = JsonSerializer.SerializeToNode(input)!.AsObject();
        json[name] = JsonSerializer.SerializeToNode(value);
        return json.Deserialize(input.GetType())!;
    }

    private static IEnumerable<(string Field, object? Value)> Mutations(object value) => value switch
    {
        TransactionInput => [("Quantity", 101m), ("Price", 2m), ("Fees", 1m), ("Symbol", "510500"), ("AssetType", AssetType.Stock), ("Action", TradeAction.Add), ("TradedAt", At(4)), ("CashSymbol", "USD"), ("Note", "changed")],
        DividendInput => [("GrossAmount", 101m), ("Fees", 2m), ("Symbol", "510500"), ("AssetType", AssetType.Stock), ("PaidAt", At(4)), ("CashSymbol", "USD"), ("Note", "changed")],
        LedgerCorrectionInput item => [("Reason", "changed"), ("TargetId", item.TargetId + 1), ("TargetType", "dividend"), ("EffectiveAt", At(4)), ("Note", "changed")],
        CorporateActionInput => [("Ratio", 3m), ("ActionType", "custody_transfer"), ("Symbol", "510500"), ("AssetType", AssetType.Stock), ("TargetSymbol", "510500"), ("Quantity", 50m), ("TargetQuantity", 50m), ("Price", 2m), ("TargetPrice", 2m), ("GrossAmount", 1m), ("Fees", 1m), ("EffectiveAt", At(4)), ("CashSymbol", "USD"), ("FromAccount", "a"), ("ToAccount", "b"), ("Note", "changed")],
        ExternalCashFlowInput => [("Amount", 101m), ("FlowType", CashFlowType.Withdrawal), ("EffectiveDate", Baseline.AddDays(3)), ("Reason", "changed"), ("CashSymbol", "USD")],
        PortfolioCalibrationInput item => [("Positions", item.Positions.Select(p => p.Symbol == "510300" ? p with { MarketValue = 1001m } : p).ToArray()), ("AsOfDate", Baseline.AddDays(3)), ("NetExternalFlow", 1m), ("Reason", "changed")],
        _ => throw new ArgumentException("Unknown synthetic input")
    };

    private static object Equivalent(object value) => value switch
    {
        TransactionInput item => item with { Symbol = " 510300 ", Quantity = 100.000m, Source = " MANUAL ", ExternalId = " same-key ", CashSymbol = " cny ", Note = " note " },
        DividendInput item => item with { Symbol = " 510300 ", GrossAmount = 100.000m, Source = " MANUAL ", ExternalId = " same-key ", CashSymbol = " cny ", Note = " note " },
        LedgerCorrectionInput item => item with { TargetType = " TRANSACTION ", Source = " MANUAL ", ExternalId = " same-key ", Reason = " reason ", Note = " note " },
        CorporateActionInput item => item with { ActionType = " SPLIT ", Symbol = " 510300 ", Ratio = 2.000m, Source = " MANUAL ", ExternalId = " same-key ", Note = " note " },
        ExternalCashFlowInput item => item with { EventId = " same-key ", Amount = 100.000m, Reason = " reason ", CashSymbol = " cny " },
        PortfolioCalibrationInput item => item with { EventId = " same-key ", Reason = " reason ", Positions = item.Positions.Reverse().Select(p => p with { Quantity = p.Quantity * 1.000m }).ToArray() },
        _ => throw new ArgumentException("Unknown synthetic input")
    };

    [PgTheory]
    [InlineData("transaction"), InlineData("dividend"), InlineData("correction"), InlineData("corporate"), InlineData("flow"), InlineData("calibration")]
    public void IdenticalAndNormalizedRequestsAreReadonly(string kind)
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, kind);
        var id = scenario.Run(scenario.Input);
        var after = State(user);
        Assert.Equal(id, scenario.Run(scenario.Input));
        Assert.Equal(id, scenario.Run(Equivalent(scenario.Input)));
        Assert.Equal(after, State(user));
        Assert.Equal(1, Audits(scenario));
    }

    [PgTheory]
    [InlineData("transaction"), InlineData("dividend"), InlineData("correction"), InlineData("corporate"), InlineData("flow"), InlineData("calibration")]
    public void EveryChangedPayloadFieldConflictsWithoutWriting(string kind)
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, kind);
        scenario.Run(scenario.Input);
        var after = State(user);
        foreach (var (field, value) in Mutations(scenario.Input))
        {
            var error = Assert.Throws<ApplicationFailure>(() => { scenario.Run(Change(scenario.Input, field, value)); });
            Assert.Equal((409, "idempotency_conflict"), ExceptionTranslator.Translate(error));
            Assert.Equal(after, State(user));
        }
    }

    [PgTheory]
    [InlineData("transaction"), InlineData("dividend"), InlineData("correction"), InlineData("corporate"), InlineData("flow"), InlineData("calibration")]
    public async Task ConcurrentIdenticalRequestsWriteOnce(string kind)
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, kind);
        using var barrier = new Barrier(2);
        Task<string> Run() => Task.Run(() => { Assert.True(barrier.SignalAndWait(TimeSpan.FromSeconds(15))); return scenario.Run(scenario.Input); });
        var results = await Task.WhenAll(Run(), Run());
        Assert.Equal(results[0], results[1]);
        Assert.Equal(1, Audits(scenario));
        using var expectedUser = new PgTestUser();
        var expected = Prepare(expectedUser, kind);
        expected.Run(expected.Input);
        Assert.Equal(Holdings(expectedUser), Holdings(user));
    }

    [PgTheory]
    [InlineData("transaction"), InlineData("dividend"), InlineData("correction"), InlineData("corporate"), InlineData("flow"), InlineData("calibration")]
    public async Task ConcurrentConflictingRequestsHaveOneWinner(string kind)
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, kind);
        var mutation = Mutations(scenario.Input).First();
        using var barrier = new Barrier(2);
        Task<Exception?> Run(object value) => Task.Run(() =>
        {
            Assert.True(barrier.SignalAndWait(TimeSpan.FromSeconds(15)));
            try { scenario.Run(value); return (Exception?)null; }
            catch (Exception error) { return error; }
        });
        var results = await Task.WhenAll(Run(scenario.Input), Run(Change(scenario.Input, mutation.Field, mutation.Value)));
        Assert.Single(results, error => error is null);
        var conflict = Assert.IsType<ApplicationFailure>(Assert.Single(results, error => error is not null));
        Assert.Equal((409, "idempotency_conflict"), ExceptionTranslator.Translate(conflict));
        Assert.Equal(1, Audits(scenario));
        using var expectedUser = new PgTestUser();
        var expected = Prepare(expectedUser, kind);
        var winner = results[0] is null ? expected.Input : Change(expected.Input, mutation.Field, mutation.Value);
        expected.Run(winner);
        Assert.Equal(Holdings(expectedUser), Holdings(user));
    }

    [PgTheory]
    [InlineData("transaction"), InlineData("dividend"), InlineData("correction"), InlineData("corporate"), InlineData("flow"), InlineData("calibration")]
    public void SameKeyIsIsolatedBetweenUsers(string kind)
    {
        using var alice = new PgTestUser();
        using var bob = new PgTestUser();
        var first = Prepare(alice, kind);
        var second = Prepare(bob, kind);
        first.Run(first.Input);
        second.Run(second.Input);
        Assert.Equal(1, Audits(first));
        Assert.Equal(1, Audits(second));
    }

    [PgTheory]
    [InlineData("transaction"), InlineData("dividend"), InlineData("correction"), InlineData("corporate"), InlineData("flow"), InlineData("calibration")]
    public void LegacyEventsWithoutRequestFingerprintRemainReadableAndReplayable(string kind)
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, kind);
        var id = scenario.Run(scenario.Input);
        user.Execute("UPDATE audit_log SET after_json=after_json::jsonb-'request_fingerprint' WHERE user_id=$1 AND entity_type=$2", user.Id, scenario.Entity);
        if (scenario.Input is PortfolioCalibrationInput calibration)
        {
            var oldJson = JsonSerializer.Serialize(calibration.Positions.OrderBy(p => p.Symbol).Select(p => new
            {
                symbol = p.Symbol, asset_type = p.AssetType.ToString().ToLowerInvariant(), quantity = p.Quantity,
                average_cost = p.AverageCost, market_value = p.MarketValue,
            }));
            var oldHash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(oldJson))).ToLowerInvariant();
            user.Execute("UPDATE portfolio_events SET details_json=jsonb_set(details_json::jsonb,'{positions_fingerprint}',to_jsonb($3::text)) WHERE user_id=$1 AND event_id=$2", user.Id, id, oldHash);
        }
        var legacy = State(user);
        Assert.Equal(id, scenario.Run(scenario.Input));
        Assert.Equal(legacy, State(user));
        foreach (var (field, value) in Mutations(scenario.Input))
        {
            // Legacy split rows store quantities derived from account state rather than the omitted input.
            // The explicit quantity is checked against that recorded value; unavailable original intent is not invented.
            var error = Assert.Throws<ApplicationFailure>(() => scenario.Run(Change(scenario.Input, field, value)));
            Assert.Equal("idempotency_conflict", error.Code);
            Assert.Equal(legacy, State(user));
        }
    }

    [Fact]
    public void RequestHashIgnoresPropertyOrderAndDecimalScaleButPreservesValues()
    {
        using var first = JsonDocument.Parse("{\"quantity\":100.000,\"nested\":{\"b\":2,\"a\":1}}");
        using var reordered = JsonDocument.Parse("{\"nested\":{\"a\":1.00,\"b\":2},\"quantity\":100}");
        using var changed = JsonDocument.Parse("{\"nested\":{\"a\":1,\"b\":2},\"quantity\":100.00000001}");
        Assert.Equal(LedgerIdempotency.Hash(first.RootElement), LedgerIdempotency.Hash(reordered.RootElement));
        Assert.NotEqual(LedgerIdempotency.Hash(first.RootElement), LedgerIdempotency.Hash(changed.RootElement));
    }

    [PgTheory]
    [InlineData("transaction"), InlineData("dividend"), InlineData("flow")]
    public void ReplayUsesOriginalCashCurrencyEvenAfterAnotherCurrencyIsAdded(string kind)
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, kind);
        user.Execute("UPDATE positions SET symbol='USD' WHERE user_id=$1 AND symbol='CNY'", user.Id);
        var id = scenario.Run(scenario.Input);
        user.Execute("INSERT INTO positions(user_id,symbol,asset_type,quantity,average_cost,market_value,as_of_date,updated_at) VALUES ($1,'CNY','cash',50,1,50,'2026-01-03',now())", user.Id);
        var before = State(user);
        Assert.Equal(id, scenario.Run(scenario.Input));
        Assert.Equal(id, scenario.Run(Change(scenario.Input, "CashSymbol", " usd ")));
        Assert.Equal(before, State(user));
    }

    [PgFact]
    public void DividendReplayIsReadonlyAfterValuationAdvances()
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, "dividend");
        var id = scenario.Run(scenario.Input);
        user.Execute("UPDATE portfolio_valuations SET as_of_date='2026-01-04' WHERE user_id=$1", user.Id);
        var before = State(user);
        Assert.Equal(id, scenario.Run(scenario.Input));
        Assert.Equal(before, State(user));
    }

    [PgFact]
    public void InstrumentNameChangeWithSameTransactionKeyConflicts()
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, "transaction");
        var input = (TransactionInput)scenario.Input;
        var id = scenario.Service.RecordTransaction(user.Id, input, "name");
        var before = State(user);
        Assert.Equal(id, scenario.Service.RecordTransaction(user.Id, input, " name "));
        Assert.Equal("idempotency_conflict", Assert.Throws<ApplicationFailure>(() => scenario.Service.RecordTransaction(user.Id, input, "other")).Code);
        Assert.Equal(before, State(user));
    }

    [PgTheory]
    [InlineData(-1), InlineData(0), InlineData(1)]
    public void DividendRespectsLatestConfirmedValuationAndRollsBackOnRejection(int delta)
    {
        using var user = new PgTestUser();
        var scenario = Prepare(user, "dividend");
        user.Execute("UPDATE portfolio_valuations SET as_of_date='2026-01-02' WHERE user_id=$1", user.Id);
        var input = ((DividendInput)scenario.Input) with { PaidAt = At(2 + delta) };
        var before = State(user);
        if (delta <= 0)
        {
            var error = Assert.Throws<ApplicationFailure>(() => { scenario.Run(input); });
            Assert.Equal("dividend_date_invalid", error.Code);
            Assert.Equal(before, State(user));
        }
        else
        {
            scenario.Run(input);
            Assert.Equal(10099m, (decimal)user.Query("SELECT market_value FROM positions WHERE user_id=$1 AND symbol='CNY'", user.Id)!);
        }
    }

    [PgFact]
    public async Task HttpDividendConflictIs409AndDoesNotChangeFinancialState()
    {
        using var user = new PgTestUser();
        Prepare(user, "dividend");
        using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.UseSetting("QUANT_PG_CONNECTION", user.Connection));
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        object Body(decimal amount) => new { symbol = "510300", asset_type = "fund", gross_amount = amount.ToString(CultureInfo.InvariantCulture), fees = "1", occurred_at = At(3).ToString("O"), external_id = "api-key", note = "note" };
        Assert.Equal(HttpStatusCode.Created, (await client.PostAsJsonAsync("/api/v1/ledger/dividends", Body(100m))).StatusCode);
        var before = State(user);
        var conflict = await client.PostAsJsonAsync("/api/v1/ledger/dividends", Body(101m));
        Assert.Equal(HttpStatusCode.Conflict, conflict.StatusCode);
        Assert.Equal("idempotency_conflict", (await conflict.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("error_code").GetString());
        Assert.Equal(before, State(user));
    }
}
