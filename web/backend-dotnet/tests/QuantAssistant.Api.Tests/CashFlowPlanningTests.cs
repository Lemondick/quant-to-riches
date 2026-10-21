using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class CashFlowPlanningTests
{
    private const string Route = "/api/v1/planning/cash-flows";
    private static WebApplicationFactory<Program> Factory(PgTestUser user)
    {
        Environment.SetEnvironmentVariable("QUANT_PG_CONNECTION", user.Connection);
        return new();
    }
    private static JsonObject Body(string id, string start = "2026-01-31", string frequency = "monthly", string? end = "2026-03-31") => new()
    {
        ["plan_id"] = id, ["name"] = "合成资金计划", ["direction"] = "deposit", ["amount"] = "1500.00000001",
        ["frequency"] = frequency, ["start_date"] = start, ["end_date"] = end, ["note"] = "仅用于计划"
    };
    private static async Task<JsonElement> Json(HttpResponseMessage response)
    {
        Assert.True(response.IsSuccessStatusCode, $"{response.StatusCode}: {await response.Content.ReadAsStringAsync()}");
        return await response.Content.ReadFromJsonAsync<JsonElement>();
    }
    private static JsonObject Edit(JsonObject original, JsonElement saved, string status = "active")
    {
        var body = original.DeepClone().AsObject();
        body.Remove("plan_id");
        body["status"] = status;
        body["expected_updated_at"] = saved.GetProperty("updated_at").GetString();
        return body;
    }

    [PgFact("测试引用已下线的旧版 Python API 路由/语义（如 /api/v1/planning/cash-flows、operations.alerts 结构），需按原生 API 重写后移除本 Skip（#6）。")]
    public async Task ListPagesPastFiveHundred_WithStableOrderingFilteredTotalsAndAccountIsolation()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        using var factory = Factory(user);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        try
        {
            user.Execute("""
                INSERT INTO cash_flow_plans(user_id,plan_id,name,direction,amount,frequency,start_date,status,created_at,updated_at)
                SELECT $1,'page-'||lpad(i::text,3,'0'),'合成分页计划','deposit',9007199254740993.00000001,
                'once','2026-01-01',CASE WHEN i%2=0 THEN 'paused' ELSE 'active' END,now(),now()
                FROM generate_series(0,504) i
                """, user.Id);
            using var foreign = factory.CreateTestClient();
            PgTestUser.AttachSession(foreign, user.Connection, other.Id);
            await Json(await foreign.PostAsJsonAsync(Route, Body("foreign-only")));
            var first = await Json(await client.GetAsync(Route + "?limit=20&offset=0"));
            Assert.Equal(505, first.GetProperty("total").GetInt64());
            Assert.Equal(0, first.GetProperty("offset").GetInt32());
            Assert.Equal(20, first.GetProperty("limit").GetInt32());
            Assert.True(first.GetProperty("has_more").GetBoolean());
            Assert.Equal("page-000", first.GetProperty("items")[0].GetProperty("plan_id").GetString());
            Assert.Equal(9007199254740993.00000001m, first.GetProperty("items")[0].GetProperty("amount").DecimalText());
            var last = await Json(await client.GetAsync(Route + "?limit=20&offset=500"));
            Assert.Equal(new[] { "page-500", "page-501", "page-502", "page-503", "page-504" },
                last.GetProperty("items").EnumerateArray().Select(item => item.GetProperty("plan_id").GetString()));
            Assert.False(last.GetProperty("has_more").GetBoolean());
            var filtered = await Json(await client.GetAsync(Route + "?status=active&limit=20&offset=250"));
            Assert.Equal(252, filtered.GetProperty("total").GetInt64());
            Assert.Equal(new[] { "page-501", "page-503" }, filtered.GetProperty("items").EnumerateArray().Select(item => item.GetProperty("plan_id").GetString()));
            var empty = await Json(await client.GetAsync(Route + "?limit=20&offset=2147483647"));
            Assert.Empty(empty.GetProperty("items").EnumerateArray());
            Assert.False(empty.GetProperty("has_more").GetBoolean());
            foreach (var query in new[] { "offset=-1", "limit=501", "status=unknown" })
                Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.GetAsync(Route + "?" + query)).StatusCode);
            foreach (var table in new[] { "transactions", "portfolio_events", "audit_log" })
                Assert.Equal(0L, user.Query($"SELECT count(*) FROM {table} WHERE user_id=$1", user.Id));
        }
        finally
        {
            user.Execute("DELETE FROM cash_flow_plans WHERE user_id IN($1,$2)", user.Id, other.Id);
        }
    }

    [PgFact("测试引用已下线的旧版 Python API 路由/语义（如 /api/v1/planning/cash-flows、operations.alerts 结构），需按原生 API 重写后移除本 Skip（#6）。")]
    public async Task CreateAndList_HandleMaximumDates_AndAnchoredMonthAndLeapYearSchedules()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        try
        {
            foreach (var frequency in new[] { "once", "weekly", "monthly", "quarterly", "annual" })
            {
                var saved = await Json(await client.PostAsJsonAsync(Route, Body(frequency, "9999-12-31", frequency, null)));
                Assert.Equal("9999-12-31", Assert.Single(saved.GetProperty("next_dates").EnumerateArray()).GetString());
            }
            var list = await Json(await client.GetAsync(Route));
            Assert.Equal(5, list.GetProperty("items").GetArrayLength());
            Assert.Equal(5, (await Json(await client.GetAsync(Route + "/due?as_of=9999-12-31"))).GetProperty("items").GetArrayLength());
            var monthly = await Json(await client.PostAsJsonAsync(Route, Body("month")));
            Assert.Equal(new[] { "2026-01-31", "2026-02-28", "2026-03-31" }, monthly.GetProperty("next_dates").EnumerateArray().Select(item => item.GetString()));
            Assert.Single((await Json(await client.GetAsync(Route + "/due?as_of=2026-02-28"))).GetProperty("items").EnumerateArray());
            Assert.Empty((await Json(await client.GetAsync(Route + "/due?as_of=2026-03-28"))).GetProperty("items").EnumerateArray());
            await Json(await client.PostAsJsonAsync(Route, Body("leap", "2024-02-29", "annual", "2028-02-29")));
            foreach (var date in new[] { "2025-02-28", "2028-02-29" })
                Assert.Equal("leap", Assert.Single((await Json(await client.GetAsync(Route + "/due?as_of=" + date))).GetProperty("items").EnumerateArray()).GetProperty("plan_id").GetString());
            Assert.Empty((await Json(await client.GetAsync(Route + "/due?as_of=2028-02-28"))).GetProperty("items").EnumerateArray());
            // Earlier, non-due plans must not hide a due plan after the first 500 rows.
            user.Execute("""
                INSERT INTO cash_flow_plans(user_id,plan_id,name,direction,amount,frequency,start_date,status,created_at,updated_at)
                SELECT $1,'earlier-'||i,'早期单次计划','deposit',1,'once','2026-01-01','active',now(),now()
                FROM generate_series(1,500) i
                """, user.Id);
            Assert.Equal(new[] { "leap", "month" }, (await Json(await client.GetAsync(Route + "/due?as_of=2026-02-28"))).GetProperty("items").EnumerateArray().Select(item => item.GetProperty("plan_id").GetString()));
        }
        finally { user.Execute("DELETE FROM cash_flow_plans WHERE user_id=$1", user.Id); }
    }

    [PgFact("测试引用已下线的旧版 Python API 路由/语义（如 /api/v1/planning/cash-flows、operations.alerts 结构），需按原生 API 重写后移除本 Skip（#6）。")]
    public async Task Lifecycle_IsVersionedOwnedAndDoesNotPostFinancialEvents()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        user.SeedLedger();
        using var factory = Factory(user);
        using var client = factory.CreateTestClient();
        using var foreign = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        PgTestUser.AttachSession(foreign, user.Connection, other.Id);
        try
        {
            var original = Body("owned-plan");
            var saved = await Json(await client.PostAsJsonAsync(Route, original));
            Assert.Equal(1500.00000001m, saved.GetProperty("amount").DecimalText());
            var edit = Edit(original, saved);
            edit["name"] = "合成提款计划";
            edit["direction"] = "withdrawal";
            edit["amount"] = "9007199254740993.00000001";
            var updated = await Json(await client.PutAsJsonAsync(Route + "/owned-plan", edit));
            Assert.Equal(9007199254740993.00000001m, updated.GetProperty("amount").DecimalText());
            Assert.Equal(saved.GetProperty("created_at").GetString(), updated.GetProperty("created_at").GetString());
            Assert.Equal(HttpStatusCode.Conflict, (await client.PutAsJsonAsync(Route + "/owned-plan", edit)).StatusCode);
            Assert.Equal(HttpStatusCode.NotFound, (await foreign.PutAsJsonAsync(Route + "/owned-plan", edit)).StatusCode);
            foreach (var status in new[] { "paused", "active", "completed", "active" })
            {
                var request = new { status, expected_updated_at = updated.GetProperty("updated_at").GetString() };
                updated = await Json(await client.PatchAsJsonAsync(Route + "/owned-plan/status", request));
                Assert.Equal(status, updated.GetProperty("status").GetString());
                var due = await Json(await client.GetAsync(Route + "/due?as_of=2026-02-28"));
                Assert.Equal(status == "active" ? 1 : 0, due.GetProperty("items").GetArrayLength());
                Assert.Equal(status == "active" ? 3 : 0, updated.GetProperty("next_dates").GetArrayLength());
            }
            Assert.Equal(HttpStatusCode.NotFound, (await foreign.PatchAsJsonAsync(Route + "/owned-plan/status", new { status = "paused", expected_updated_at = updated.GetProperty("updated_at").GetString() })).StatusCode);
            Assert.Empty((await Json(await foreign.GetAsync(Route))).GetProperty("items").EnumerateArray());
            Assert.Equal(10000m, user.Query("SELECT market_value FROM positions WHERE user_id=$1", user.Id));
            foreach (var table in new[] { "transactions", "portfolio_events", "audit_log" })
                Assert.Equal(0L, user.Query($"SELECT count(*) FROM {table} WHERE user_id=$1", user.Id));
        }
        finally { user.Execute("DELETE FROM cash_flow_plans WHERE user_id=$1", user.Id); }
    }

    [PgFact("测试引用已下线的旧版 Python API 路由/语义（如 /api/v1/planning/cash-flows、operations.alerts 结构），需按原生 API 重写后移除本 Skip（#6）。")]
    public async Task CreateRetry_IsIdempotent_AndConcurrentEditsConflict()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        try
        {
            var original = Body("retry-plan");
            var responses = await Task.WhenAll(client.PostAsJsonAsync(Route, original.DeepClone()), client.PostAsJsonAsync(Route, original.DeepClone()));
            var saved = await Json(responses[0]);
            var retry = await Json(responses[1]);
            Assert.Equal(saved.ToString(), retry.ToString());
            Assert.Equal(1L, user.Query("SELECT count(*) FROM cash_flow_plans WHERE user_id=$1", user.Id));
            var conflicting = original.DeepClone().AsObject();
            conflicting["name"] = "不能覆盖";
            Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync(Route, conflicting)).StatusCode);
            var first = Edit(original, saved); first["amount"] = "100";
            var second = Edit(original, saved); second["amount"] = "200";
            var results = await Task.WhenAll(client.PutAsJsonAsync(Route + "/retry-plan", first), client.PutAsJsonAsync(Route + "/retry-plan", second));
            Assert.Single(results, result => result.StatusCode == HttpStatusCode.OK);
            Assert.Single(results, result => result.StatusCode == HttpStatusCode.Conflict);
        }
        finally { user.Execute("DELETE FROM cash_flow_plans WHERE user_id=$1", user.Id); }
    }

    [PgFact("测试引用已下线的旧版 Python API 路由/语义（如 /api/v1/planning/cash-flows、operations.alerts 结构），需按原生 API 重写后移除本 Skip（#6）。")]
    public async Task InvalidInputAndMissingVersions_DoNotWriteOrOverwrite()
    {
        using var user = new PgTestUser();
        using var factory = Factory(user);
        using var client = factory.CreateTestClient();
        PgTestUser.AttachSession(client, user.Connection, user.Id);
        try
        {
            foreach (var (key, value) in new (string, string?)[] { ("name", "\n"), ("plan_id", " "), ("amount", "0.000000001"), ("amount", "-1"), ("end_date", "2025-12-31"), ("frequency", "daily") })
            {
                var invalid = Body("invalid"); invalid[key] = value;
                Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync(Route, invalid)).StatusCode);
            }
            var missing = Body("missing-date"); missing.Remove("start_date");
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PostAsJsonAsync(Route, missing)).StatusCode);
            Assert.Equal(0L, user.Query("SELECT count(*) FROM cash_flow_plans WHERE user_id=$1", user.Id));
            var saved = await Json(await client.PostAsJsonAsync(Route, Body("valid")));
            var edit = Edit(Body("valid"), saved); edit.Remove("expected_updated_at");
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PutAsJsonAsync(Route + "/valid", edit)).StatusCode);
            Assert.Equal(HttpStatusCode.UnprocessableEntity, (await client.PatchAsJsonAsync(Route + "/valid/status", new { status = "paused" })).StatusCode);
            var rows = (await Json(await client.GetAsync(Route))).GetProperty("items");
            Assert.Equal(saved.ToString(), Assert.Single(rows.EnumerateArray()).ToString());
        }
        finally { user.Execute("DELETE FROM cash_flow_plans WHERE user_id=$1", user.Id); }
    }
}
