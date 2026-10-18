using Npgsql;
using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using QuantAssistant.Infrastructure.History;
using Xunit;

namespace QuantAssistant.Api.Tests;

/// <summary>P5 历史查询集成测试：ledger/actions/corrections 三 section 分页 + 导出 + onboarding。</summary>
public sealed class HistoryIntegrationTests
{
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    private static void SeedRows(PgTestUser user, int count) => user.Execute(
        "INSERT INTO transactions(user_id,id,symbol,asset_type,action,quantity,price,gross_amount,fees,cash_delta,traded_at,source,note) " +
        "SELECT $1,n,'000001','fund','buy',9007199254740993.12345678,1,9007199254740993.12345678,0,-9007199254740993.12345678,'2026-10-07T16:00:00Z','manual','synthetic-'||n FROM generate_series(1,$2) n", user.Id, count);

    [PgFact]
    public async Task Export_ReturnsAllMatchingRows_RegardlessOfPage_AndPreservesDecimalText()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        SeedRows(user, 601);
        SeedRows(other, 1);
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        foreach (var route in new[] { "/api/v1/history/ledger/export", "/api/v1/history/ledger?export=true&page=3&limit=1" })
        {
            var response = await client.GetAsync(route);
            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            var data = await response.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal(601, data.GetProperty("total").GetInt32());
            var rows = data.GetProperty("items").EnumerateArray().ToArray();
            Assert.Equal(601, rows.Length);
            Assert.Equal(601, rows[0].GetProperty("id").GetInt64());
            Assert.Equal(1, rows[^1].GetProperty("id").GetInt64());
            Assert.Equal("9007199254740993.12345678", rows[0].GetProperty("gross_amount").GetString());
            Assert.Equal(602, data.GetProperty("csv").GetString()!.Split("\r\n", StringSplitOptions.RemoveEmptyEntries).Length);
        }
    }

    [PgFact]
    public void DateFilter_UsesInclusiveBeijingDays_InEverySection_AndIgnoresDatabaseTimezone()
    {
        using var user = new PgTestUser();
        user.Execute("INSERT INTO transactions(user_id,id,symbol,asset_type,action,quantity,price,gross_amount,fees,cash_delta,traded_at,source) " +
            "SELECT $1,n,'000001','fund','buy',1,1,1,0,-1,stamp,'manual' FROM (VALUES(1,'2026-10-07T15:59:59.999999Z'::timestamptz),(2,'2026-10-07T16:00:00Z'),(3,'2026-10-08T15:59:59.999999Z'),(4,'2026-10-08T16:00:00Z')) v(n,stamp)", user.Id);
        user.Execute("INSERT INTO corporate_action_events(user_id,id,symbol,asset_type,action_type,effective_at,source,external_id) SELECT user_id,id,symbol,asset_type,'split',traded_at,'manual',id::text FROM transactions WHERE user_id=$1", user.Id);
        user.Execute("INSERT INTO ledger_corrections(user_id,id,target_type,target_id,reason,effective_at,source,external_id) SELECT user_id,id,'transaction',id,'synthetic',traded_at,'manual',id::text FROM transactions WHERE user_id=$1", user.Id);
        foreach (var zone in new[] { "UTC", "Pacific/Honolulu", "Asia/Shanghai" })
        {
            var repo = new HistoryRepository(new NpgsqlConnectionStringBuilder(user.Connection) { Timezone = zone }.ConnectionString);
            foreach (var section in new[] { "ledger", "actions", "corrections" })
            {
                var result = repo.Query(user.Id, section, 1, 50, null, new DateOnly(2026, 10, 8), new DateOnly(2026, 10, 8), false);
                Assert.Equal(2, result.Total);
                Assert.Equal(new long[] { 3, 2 }, result.Items.Select(row => (long)row["id"]!).ToArray());
            }
            Assert.Equal(4, repo.Query(user.Id, "ledger", 1, 50, null, DateOnly.MinValue, DateOnly.MaxValue, false).Total);
        }
    }

    [PgFact]
    public void Pagination_DisambiguatesTransactionAndDividend_WithSameIdAndTimestamp()
    {
        using var user = new PgTestUser();
        SeedRows(user, 1);
        user.Execute("INSERT INTO dividend_events(user_id,id,symbol,asset_type,gross_amount,fees,net_amount,paid_at,source,external_id,cash_symbol) VALUES($1,1,'000001','fund',1,0,1,'2026-10-07T16:00:00Z','manual','synthetic','CNY')", user.Id);
        var repo = new HistoryRepository(user.Connection);
        Assert.Equal("dividend", repo.Query(user.Id, "ledger", 1, 1, null, null, null, false).Items.Single()["entry_type"]);
        Assert.Equal("buy", repo.Query(user.Id, "ledger", 2, 1, null, null, null, false).Items.Single()["entry_type"]);
    }

    [PgFact]
    public void Csv_UsesInvariantAmounts_IsoTimestamps_AndGuardsWhitespaceBeforeFormulas()
    {
        using var user = new PgTestUser();
        SeedRows(user, 1);
        user.Execute("UPDATE transactions SET note=$2 WHERE user_id=$1", user.Id, " \t=SUM(1,2)");
        var culture = CultureInfo.CurrentCulture;
        try
        {
            CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("fr-FR");
            var csv = new HistoryRepository(user.Connection).Query(user.Id, "ledger", 1, 50, null, null, null, true).Csv!;
            Assert.Contains("9007199254740993.12345678", csv);
            Assert.Contains("2026-10-07T16:00:00.0000000Z", csv);
            Assert.Contains("' \t=SUM(1,2)", csv);
            Assert.EndsWith("\r\n", csv);
        }
        finally { CultureInfo.CurrentCulture = culture; }
    }

    [PgFact]
    public async Task BothExportRoutes_RejectOverOneHundredThousandRows_With413()
    {
        using var user = new PgTestUser();
        SeedRows(user, 100_001);
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        foreach (var route in new[] { "/api/v1/history/ledger/export", "/api/v1/history/ledger?export=true" })
        {
            var response = await client.GetAsync(route);
            Assert.Equal(HttpStatusCode.RequestEntityTooLarge, response.StatusCode);
            var data = await response.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal("history_export_too_large", data.GetProperty("error_code").GetString());
        }
    }

    [PgFact]
    public async Task Query_RejectsInvalidBounds_AndOverlongSymbol_OnBothRoutes()
    {
        using var user = new PgTestUser();
        var repo = new HistoryRepository(user.Connection);
        foreach (var (page, limit) in new[] { (0, 50), (100001, 50), (1, 0), (1, 501) })
            Assert.Throws<ArgumentException>(() => repo.Query(user.Id, "ledger", page, limit, null, null, null, false));
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        using var factory = new WebApplicationFactory<Program>();
        using var client = factory.CreateClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        foreach (var route in new[] { "/api/v1/history/ledger", "/api/v1/history/ledger/export" })
        {
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.GetAsync(route + "?symbol=" + new string('a', 33))).StatusCode);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.GetAsync(route + "?start=2026-10-08&end=2026-10-07")).StatusCode);
        }
    }

    [PgFact]
    public void LedgerSection_CombinesTransactionsAndDividends()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var repo = new HistoryRepository(_connection);
        try
        {
            SeedUser(userId);
            SeedInstrument(userId, "600000.SH", "浦发银行");
            SeedTransaction(userId, 1, "600000.SH", "buy", 200m, 10.5m, 5m, "2026-09-20");
            SeedDividend(userId, 2, "600000.SH", 100m, 1m, "2026-09-25");

            var page = repo.Query(userId, "ledger", 1, 50, null, null, null, export: false);

            Assert.Equal(2, page.Total);
            Assert.Contains(page.Items, r => (string?)r["entry_type"] == "buy");
            Assert.Contains(page.Items, r => (string?)r["entry_type"] == "dividend");
            // 倒序：分红（09-25）在前
            Assert.Equal("dividend", (string?)page.Items[0]["entry_type"]);
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void LedgerSection_FiltersBySymbolAndDate()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var repo = new HistoryRepository(_connection);
        try
        {
            SeedUser(userId);
            SeedInstrument(userId, "600000.SH", "A");
            SeedInstrument(userId, "000001.SZ", "B");
            SeedTransaction(userId, 1, "600000.SH", "buy", 100m, 10m, 1m, "2026-09-01");
            SeedTransaction(userId, 2, "000001.SZ", "buy", 100m, 10m, 1m, "2026-09-30");

            // 按 symbol 过滤
            var bySymbol = repo.Query(userId, "ledger", 1, 50, "600000", null, null, export: false);
            Assert.Equal(1, bySymbol.Total);

            // 按日期过滤
            var byDate = repo.Query(userId, "ledger", 1, 50, null, new DateOnly(2026, 9, 15), null, export: false);
            Assert.Equal(1, byDate.Total);
            Assert.Equal("000001.SZ", (string?)byDate.Items[0]["symbol"]);
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void Export_ProducesCsv_WithFormulaInjectionGuard()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var repo = new HistoryRepository(_connection);
        try
        {
            SeedUser(userId);
            SeedInstrument(userId, "600000.SH", "A");
            // note 以 = 开头，模拟公式注入
            SeedTransactionWithNote(userId, 1, "600000.SH", "=HYPERLINK(\"http://evil\")", "2026-09-20");

            var page = repo.Query(userId, "ledger", 1, 50, null, null, null, export: true);

            Assert.NotNull(page.Csv);
            Assert.Contains("'=HYPERLINK", page.Csv);
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void Onboarding_ReflectsSeededState()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var repo = new HistoryRepository(_connection);
        try
        {
            SeedUser(userId);
            // 未建持仓 → 组合步骤未完成
            var empty = repo.Onboarding(userId);
            Assert.False(empty.Steps.First(s => s.Key == "portfolio").Done);

            // 建持仓 + 现金 → 组合步骤完成
            SeedPosition(userId, "600000.SH", "stock", 100m);
            SeedPosition(userId, "CNY", "cash", 0m);
            var seeded = repo.Onboarding(userId);
            Assert.True(seeded.Steps.First(s => s.Key == "portfolio").Done);
        }
        finally
        {
            Cleanup(userId);
        }
    }

    private void SeedUser(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO users (id, username, password_hash, role, status, created_at) VALUES ($1,$2,'x','user','active',now())";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue($"u_{userId[..8]}");
        cmd.ExecuteNonQuery();
    }

    private void SeedInstrument(string userId, string symbol, string name)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO instruments (user_id, symbol, name, asset_type, as_of_date) VALUES ($1,$2,$3,'stock','2026-09-01') ON CONFLICT DO NOTHING";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(name);
        cmd.ExecuteNonQuery();
    }

    private void SeedTransaction(string userId, long id, string symbol, string action, decimal qty, decimal price, decimal fees, string date)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO transactions (user_id, id, symbol, asset_type, action, quantity, price, gross_amount, fees, cash_delta, traded_at, source) VALUES ($1,$2,$3,'stock',$4,$5,$6,$7,$8,$9,$10,'manual')";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(id);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(action);
        cmd.Parameters.AddWithValue(qty);
        cmd.Parameters.AddWithValue(price);
        cmd.Parameters.AddWithValue(qty * price);
        cmd.Parameters.AddWithValue(fees);
        cmd.Parameters.AddWithValue(-(qty * price + fees));
        cmd.Parameters.AddWithValue(DateTime.Parse(date + " 10:00:00").ToUniversalTime());
        cmd.ExecuteNonQuery();
    }

    private void SeedTransactionWithNote(string userId, long id, string symbol, string note, string date)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO transactions (user_id, id, symbol, asset_type, action, quantity, price, gross_amount, fees, cash_delta, traded_at, source, note) VALUES ($1,$2,$3,'stock','buy',100,10,1000,1,-1001,$4,'manual',$5)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(id);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(DateTime.Parse(date + " 10:00:00").ToUniversalTime());
        cmd.Parameters.AddWithValue(note);
        cmd.ExecuteNonQuery();
    }

    private void SeedDividend(string userId, long id, string symbol, decimal gross, decimal fees, string date)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO dividend_events (user_id, id, symbol, asset_type, gross_amount, fees, net_amount, paid_at, source, external_id, cash_symbol) VALUES ($1,$2,$3,'stock',$4,$5,$6,$7,'manual',$8,'CNY')";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(id);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(gross);
        cmd.Parameters.AddWithValue(fees);
        cmd.Parameters.AddWithValue(gross - fees);
        cmd.Parameters.AddWithValue(DateTime.Parse(date + " 10:00:00").ToUniversalTime());
        cmd.Parameters.AddWithValue($"div-{id}");
        cmd.ExecuteNonQuery();
    }

    private void SeedPosition(string userId, string symbol, string assetType, decimal quantity)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO positions (user_id, symbol, asset_type, quantity, average_cost, market_value, as_of_date) VALUES ($1,$2,$3,$4,0,0,$5)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(assetType);
        cmd.Parameters.AddWithValue(quantity);
        cmd.Parameters.AddWithValue(new DateOnly(2026, 9, 1));
        cmd.ExecuteNonQuery();
    }

    private void Cleanup(string userId)
    {
        using var conn = Open();
        foreach (var table in new[] { "audit_log", "ledger_corrections", "corporate_action_events", "dividend_events", "transactions", "portfolio_valuations", "positions", "instruments", "sessions", "recovery_tokens" })
        {
            using var cmd = conn.CreateCommand();
            cmd.CommandText = $"DELETE FROM {table} WHERE user_id = $1";
            cmd.Parameters.AddWithValue(userId);
            cmd.ExecuteNonQuery();
        }

        using (var del = conn.CreateCommand())
        {
            del.CommandText = "DELETE FROM users WHERE id = $1";
            del.Parameters.AddWithValue(userId);
            del.ExecuteNonQuery();
        }
    }

    private NpgsqlConnection Open()
    {
        var conn = new NpgsqlConnection(_connection);
        conn.Open();
        return conn;
    }
}
