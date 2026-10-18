using System.Text.Json.Nodes;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Jobs;

public sealed class AnalysisSnapshotRepository(AccountDatabase database)
{
    private static readonly string[] Tables = ["instruments", "positions", "transactions", "dividend_events", "ledger_corrections", "corporate_action_events",
        "portfolio_events", "portfolio_valuations", "strategy_runs", "signals", "proposed_orders", "data_snapshots", "fund_share_families", "fund_fee_schedules",
        "factor_observations", "drawdown_validations", "benchmark_configs", "macro_observations", "portfolio_accounts", "account_positions", "account_events",
        "fund_research_snapshots", "advisor_decision_snapshots", "operational_alerts", "job_heartbeats", "paper_orders", "paper_fills", "paper_reconciliations", "audit_log", "market_advisor_reports", "fund_lot_snapshots", "fund_holding_lots"];

    public JsonObject Create(string userId) => database.Read(sql => Create(sql,userId));
    public Task<JsonObject> CreateAsync(string userId, CancellationToken cancellation = default) => database.ReadAsync(async sql =>
    {
        var role = await sql.ScalarAsync("SELECT role FROM users WHERE id=$1 AND status='active'", userId) as string ?? throw new AuthFailure("authentication_required");
        var tables = new JsonObject();
        foreach (var table in Tables)
        {
            var rows = await sql.QueryAsync($"SELECT * FROM {table} WHERE user_id=$1 LIMIT 50001", userId);
            if (rows.Count > 50000) throw new ApplicationFailure("analysis_input_too_large", 422, "研究输入超过当前单机限制，请缩小数据范围。");
            foreach (var row in rows.OfType<JsonObject>()) row.Remove("user_id");
            tables[table] = rows;
        }
        return new JsonObject { ["version"] = 1, ["user_id"] = userId, ["role"] = role, ["tables"] = tables };
    }, cancellation);
    public static JsonObject Create(AccountSql sql,string userId)
    {
        var role = sql.Scalar("SELECT role FROM users WHERE id=$1 AND status='active'", userId) as string ?? throw new AuthFailure("authentication_required");
        var tables = new JsonObject();
        foreach (var table in Tables)
        {
            // table identifiers are fixed server constants, never request text.
            var rows = sql.Query($"SELECT * FROM {table} WHERE user_id=$1 LIMIT 50001", userId);
            if (rows.Count > 50000) throw new ApplicationFailure("analysis_input_too_large", 422, "研究输入超过当前单机限制，请缩小数据范围。");
            foreach (var row in rows.OfType<JsonObject>()) row.Remove("user_id");
            tables[table] = rows;
        }
        return new JsonObject { ["version"] = 1, ["user_id"] = userId, ["role"] = role, ["tables"] = tables };
    }
}
