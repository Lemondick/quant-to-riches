using Npgsql;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Portfolio;
using Xunit;
using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Api.Tests;

/// <summary>
/// P3 集成测试：真实 PG 上验证成交记录 → 持仓/现金更新 → 幂等完整链路。
/// 由 QUANT_TEST_PG_CONNECTION 门控；使用随机用户并自清理。
/// </summary>
public sealed class TransactionIntegrationTests
{
    private static readonly TimeSpan Shanghai = TimeSpan.FromHours(8);
    private readonly string? _connection = Environment.GetEnvironmentVariable("QUANT_TEST_PG_CONNECTION");

    [PgFact]
    public void BuyThenSell_UpdatesPositionAndCash_Idempotent()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var service = new TransactionService(new LedgerRepository(_connection), TimeProvider.System);
        try
        {
            Seed(userId);

            // 买入 200 股 @10.5 + 5 费
            var buy = service.RecordTransaction(userId, new TransactionInput(
                "510300", AssetType.Fund, Action.Buy, 200m, 10.5m, 5m,
                new DateTimeOffset(2026, 9, 30, 10, 0, 0, Shanghai), "BUY-1"));
            Assert.True(buy > 0);

            var pos = Position(userId, "510300");
            Assert.Equal(200m, pos.Quantity);
            Assert.Equal(10.525m, pos.AverageCost);
            Assert.Equal(7895m, Cash(userId, "CNY")); // 10000 - 2105

            // 幂等：重复买入相同 external_id 返回原 ID
            var buyAgain = service.RecordTransaction(userId, new TransactionInput(
                "510300", AssetType.Fund, Action.Buy, 200m, 10.5m, 5m,
                new DateTimeOffset(2026, 9, 30, 10, 0, 0, Shanghai), "BUY-1"));
            Assert.Equal(buy, buyAgain);

            // 卖出 100 股 @12 + 2 费
            service.RecordTransaction(userId, new TransactionInput(
                "510300", AssetType.Fund, Action.Sell, 100m, 12m, 2m,
                new DateTimeOffset(2026, 9, 30, 14, 0, 0, Shanghai), "SELL-1"));

            pos = Position(userId, "510300");
            Assert.Equal(100m, pos.Quantity);
            Assert.Equal(10.525m, pos.AverageCost); // 卖出成本不变
            Assert.Equal(9093m, Cash(userId, "CNY")); // 7895 + 1198
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void RecordDividend_UpdatesCash_Idempotent()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var service = new TransactionService(new LedgerRepository(_connection), TimeProvider.System);
        try
        {
            Seed(userId);

            var div = service.RecordDividend(userId, new DividendInput(
                "510300", AssetType.Fund, 150m, 5m,
                new DateTimeOffset(2026, 9, 30, 11, 0, 0, Shanghai), "DIV-1"));
            Assert.True(div > 0);
            Assert.Equal(10145m, Cash(userId, "CNY")); // 10000 + (150-5)

            var again = service.RecordDividend(userId, new DividendInput(
                "510300", AssetType.Fund, 150m, 5m,
                new DateTimeOffset(2026, 9, 30, 11, 0, 0, Shanghai), "DIV-1"));
            Assert.Equal(div, again);
            Assert.Equal(10145m, Cash(userId, "CNY")); // 幂等不重复
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void CorrectBuyTransaction_RestoresPositionAndCash()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var service = new TransactionService(new LedgerRepository(_connection), TimeProvider.System);
        try
        {
            Seed(userId);

            var buy = service.RecordTransaction(userId, new TransactionInput(
                "510300", AssetType.Fund, Action.Buy, 200m, 10.5m, 5m,
                new DateTimeOffset(2026, 9, 30, 10, 0, 0, Shanghai), "BUY-C1"));
            Assert.Equal(7895m, Cash(userId, "CNY"));

            // 冲正该买入：持仓归零，现金回到 10000
            service.CorrectLedgerEntry(userId, new LedgerCorrectionInput(
                "transaction", buy, "误录", new DateTimeOffset(2026, 9, 30, 15, 0, 0, Shanghai), "COR-1"));

            Assert.Equal(10000m, Cash(userId, "CNY"));
            Assert.Null(PositionOrNull(userId, "510300"));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void Split_DoublesQuantity_HalvesCost()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var service = new TransactionService(new LedgerRepository(_connection), TimeProvider.System);
        try
        {
            Seed(userId);
            service.RecordTransaction(userId, new TransactionInput(
                "510300", AssetType.Fund, Action.Buy, 200m, 10m, 0m,
                new DateTimeOffset(2026, 9, 30, 10, 0, 0, Shanghai), "BUY-S1"));

            service.RecordCorporateAction(userId, new CorporateActionInput(
                "split", "510300", AssetType.Fund,
                new DateTimeOffset(2026, 9, 30, 15, 0, 0, Shanghai), "CA-S1",
                Ratio: 2m));

            var pos = Position(userId, "510300");
            Assert.Equal(400m, pos.Quantity);
            Assert.Equal(5m, pos.AverageCost); // 200*10 / 400
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void DividendReinvestment_AddsShares_ReducesCash()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var service = new TransactionService(new LedgerRepository(_connection), TimeProvider.System);
        try
        {
            Seed(userId);
            service.RecordTransaction(userId, new TransactionInput(
                "510300", AssetType.Fund, Action.Buy, 200m, 10m, 0m,
                new DateTimeOffset(2026, 9, 30, 10, 0, 0, Shanghai), "BUY-D1"));

            service.RecordCorporateAction(userId, new CorporateActionInput(
                "dividend_reinvestment", "510300", AssetType.Fund,
                new DateTimeOffset(2026, 9, 30, 15, 0, 0, Shanghai), "CA-D1",
                Price: 10m, GrossAmount: 100m));

            var pos = Position(userId, "510300");
            Assert.Equal(210m, pos.Quantity);
            Assert.Equal(10m, pos.AverageCost); // (200*10 + 100)/210
            Assert.Equal(7900m, Cash(userId, "CNY")); // 10000 - 2000(买入) - 100(再投资)
        }
        finally
        {
            Cleanup(userId);
        }
    }

    [PgFact]
    public void RecordCashFlow_Deposit_IncreasesCash()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var service = new TransactionService(new LedgerRepository(_connection), TimeProvider.System);
        try
        {
            Seed(userId);
            SeedValuation(userId, new DateOnly(2026, 9, 29));

            service.RecordExternalCashFlow(userId, new ExternalCashFlowInput(
                "FLOW-1", CashFlowType.Deposit, 500m, new DateOnly(2026, 9, 30), "入金"));

            Assert.Equal(10500m, Cash(userId, "CNY"));
        }
        finally
        {
            Cleanup(userId);
        }
    }

    private void SeedValuation(string userId, DateOnly asOfDate)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO portfolio_valuations (user_id, run_id, as_of_date, generated_at, data_snapshot_id, total_value, cash, unit_nav, high_water_mark) VALUES ($1, 'run-1', $2, now(), 1, 10000, 10000, 1, 1)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(asOfDate.ToDateTime(TimeOnly.MinValue, DateTimeKind.Utc));
        cmd.ExecuteNonQuery();
    }

    [PgFact]
    public void PortfolioCalibration_ReplacesPositions()
    {
        if (_connection is null)
        {
            return;
        }

        var userId = Guid.NewGuid().ToString("N");
        var service = new TransactionService(new LedgerRepository(_connection), TimeProvider.System);
        try
        {
            Seed(userId);
            SeedValuation(userId, new DateOnly(2026, 9, 29));

            service.RecordPortfolioCalibration(userId, new PortfolioCalibrationInput(
                "CAL-1", new DateOnly(2026, 9, 30),
                new[]
                {
                    new CalibrationPosition("CNY", AssetType.Cash, 8000m, 1m, 8000m, new DateOnly(2026, 9, 30)),
                    new CalibrationPosition("600000", AssetType.Stock, 100m, 10m, 1000m, new DateOnly(2026, 9, 30)),
                },
                0m, "对账校准"));

            Assert.Equal(8000m, Cash(userId, "CNY"));
            var stock = Position(userId, "600000");
            Assert.Equal(100m, stock.Quantity);
        }
        finally
        {
            Cleanup(userId);
        }
    }

    private void Seed(string userId)
    {
        using var conn = Open();
        using (var u = conn.CreateCommand())
        {
            u.CommandText = "INSERT INTO users (id, username, password_hash, role, status, created_at) VALUES ($1,$2,'x','user','active',now())";
            u.Parameters.AddWithValue(userId);
            u.Parameters.AddWithValue($"u_{userId[..8]}");
            u.ExecuteNonQuery();
        }

        using (var ins = conn.CreateCommand())
        {
            ins.CommandText = "INSERT INTO instruments (user_id, symbol, asset_type, name, as_of_date) VALUES ($1,'510300','fund','沪深300ETF','2026-09-29')";
            ins.Parameters.AddWithValue(userId);
            ins.ExecuteNonQuery();
        }

        using (var cash = conn.CreateCommand())
        {
            cash.CommandText = "INSERT INTO positions (user_id, symbol, asset_type, quantity, average_cost, market_value, as_of_date, updated_at) VALUES ($1,'CNY','cash',10000,1,10000,'2026-09-29',now())";
            cash.Parameters.AddWithValue(userId);
            cash.ExecuteNonQuery();
        }
    }

    private (decimal Quantity, decimal AverageCost) Position(string userId, string symbol)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT quantity, average_cost FROM positions WHERE user_id=$1 AND symbol=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        using var r = cmd.ExecuteReader();
        Assert.True(r.Read(), "持仓应存在");
        return (r.GetDecimal(0), r.GetDecimal(1));
    }

    private (decimal Quantity, decimal AverageCost)? PositionOrNull(string userId, string symbol)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT quantity, average_cost FROM positions WHERE user_id=$1 AND symbol=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        using var r = cmd.ExecuteReader();
        return r.Read() ? (r.GetDecimal(0), r.GetDecimal(1)) : null;
    }

    private decimal Cash(string userId, string symbol)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT market_value FROM positions WHERE user_id=$1 AND symbol=$2 AND asset_type='cash'";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        using var r = cmd.ExecuteReader();
        Assert.True(r.Read(), "现金持仓应存在");
        return r.GetDecimal(0);
    }

    private void Cleanup(string userId)
    {
        using var conn = Open();
        foreach (var table in new[] { "audit_log", "ledger_corrections", "corporate_action_events", "dividend_events", "transactions", "portfolio_events", "portfolio_valuations", "positions", "instruments", "sessions", "recovery_tokens" })
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
