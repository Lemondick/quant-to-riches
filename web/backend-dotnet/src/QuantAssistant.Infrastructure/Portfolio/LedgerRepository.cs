using System.Text.Json;
using Npgsql;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using Action = QuantAssistant.Core.Portfolio.Action;

namespace QuantAssistant.Infrastructure.Portfolio;

/// <summary>
/// PostgreSQL 账本仓储：成交、持仓、现金、审计。SQL 参数化，按 user_id 隔离；
/// 现金是 positions 表 asset_type='cash' 的特殊行（quantity=balance, average_cost=1）。
/// </summary>
public sealed class LedgerRepository(string connectionString) : ILedgerRepository
{
    private readonly NpgsqlConnection? _connection;
    private readonly NpgsqlTransaction? _transaction;

    private LedgerRepository(string connectionString, NpgsqlConnection connection, NpgsqlTransaction transaction)
        : this(connectionString)
    {
        _connection = connection;
        _transaction = transaction;
    }

    public T ExecuteAtomically<T>(string userId, Func<ILedgerRepository, T> operation)
    {
        if (_transaction is not null) return operation(this);
        using var connection = new NpgsqlConnection(connectionString);
        connection.Open();
        using var transaction = connection.BeginTransaction();
        using (var gate = connection.CreateCommand())
        {
            gate.Transaction = transaction;
            gate.CommandText = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))";
            gate.Parameters.AddWithValue("ledger:" + userId);
            gate.ExecuteNonQuery();
        }
        using(var account=connection.CreateCommand())
        {
            account.Transaction=transaction;account.CommandText="SELECT 1 FROM users WHERE id=$1 AND status='active' FOR SHARE";account.Parameters.AddWithValue(userId);
            if(account.ExecuteScalar() is null)throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
        }
        var result = operation(new LedgerRepository(connectionString, connection, transaction));
        transaction.Commit();
        return result;
    }

    private const string LedgerEntriesSql = """
        SELECT * FROM (
            SELECT t.id, CASE WHEN lower(coalesce(t.source,''))='dca' THEN 'dca'
                WHEN t.action IN ('buy','add') THEN 'buy' ELSE 'sell' END AS entry_type,
                t.symbol, coalesce(i.name,t.symbol) AS name, t.asset_type, t.quantity, t.price,
                t.gross_amount, t.fees, t.cash_delta AS net_amount, t.traded_at AS occurred_at,
                coalesce(t.external_id,'') AS external_id, t.note, t.reversed_by_correction_id IS NOT NULL AS reversed
            FROM transactions t LEFT JOIN instruments i ON i.user_id=t.user_id AND i.symbol=t.symbol
            WHERE t.user_id=$1
            UNION ALL
            SELECT d.id, 'dividend', d.symbol, coalesce(i.name,d.symbol), d.asset_type,
                NULL, NULL, d.gross_amount, d.fees, d.net_amount, d.paid_at,
                d.external_id, d.note, d.reversed_by_correction_id IS NOT NULL
            FROM dividend_events d LEFT JOIN instruments i ON i.user_id=d.user_id AND i.symbol=d.symbol
            WHERE d.user_id=$1
        ) ledger
        """;

    public LedgerSnapshot ReadLedger(string userId, int limit)
    {
        if (limit is < 1 or > 500) throw new ArgumentException("账本条数须为1至500");
        if (_transaction is null) return ExecuteAtomically(userId, scoped => scoped.ReadLedger(userId, limit));
        using var conn = Open();
        using var entriesCommand = conn.CreateCommand();
        entriesCommand.CommandText = LedgerEntriesSql + " ORDER BY occurred_at DESC, entry_type, id DESC LIMIT $2";
        entriesCommand.Parameters.AddWithValue(userId);
        entriesCommand.Parameters.AddWithValue(limit);
        var entries = ReadEntries(entriesCommand);
        using var totals = conn.CreateCommand();
        totals.CommandText = """
            SELECT coalesce((SELECT sum(fees) FROM transactions WHERE user_id=$1 AND reversed_by_correction_id IS NULL),0)
                 + coalesce((SELECT sum(fees) FROM dividend_events WHERE user_id=$1 AND reversed_by_correction_id IS NULL),0),
                   coalesce((SELECT sum(net_amount) FROM dividend_events WHERE user_id=$1 AND reversed_by_correction_id IS NULL),0)
            """;
        totals.Parameters.AddWithValue(userId);
        using var reader = totals.ExecuteReader();
        reader.Read();
        return new LedgerSnapshot(entries, reader.GetDecimal(0), reader.GetDecimal(1));
    }

    public async Task<LedgerSnapshot> ReadLedgerAsync(string userId, int limit, CancellationToken cancellation = default)
    {
        if (limit is < 1 or > 500) throw new ArgumentException("账本条数须为1至500");
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(System.Data.IsolationLevel.RepeatableRead, cancellation);
        await using var command = new NpgsqlCommand(LedgerEntriesSql + " ORDER BY occurred_at DESC, entry_type, id DESC LIMIT $2", connection, transaction);
        command.Parameters.AddWithValue(userId); command.Parameters.AddWithValue(limit);
        var entries = new List<LedgerEntry>();
        await using (var reader = await command.ExecuteReaderAsync(cancellation))
            while (await reader.ReadAsync(cancellation)) entries.Add(MapEntry(reader));
        command.Parameters.Clear(); command.Parameters.AddWithValue(userId);
        command.CommandText = """
            SELECT coalesce((SELECT sum(fees) FROM transactions WHERE user_id=$1 AND reversed_by_correction_id IS NULL),0)
                 + coalesce((SELECT sum(fees) FROM dividend_events WHERE user_id=$1 AND reversed_by_correction_id IS NULL),0),
                   coalesce((SELECT sum(net_amount) FROM dividend_events WHERE user_id=$1 AND reversed_by_correction_id IS NULL),0)
            """;
        LedgerSnapshot snapshot;
        await using (var reader = await command.ExecuteReaderAsync(cancellation))
        {
            await reader.ReadAsync(cancellation);
            snapshot = new(entries, reader.GetDecimal(0), reader.GetDecimal(1));
        }
        await transaction.CommitAsync(cancellation);
        return snapshot;
    }

    public LedgerEntry? FindLedgerEntry(string userId, long id, bool dividend)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = LedgerEntriesSql + " WHERE id=$2 AND (entry_type='dividend')=$3";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(id);
        cmd.Parameters.AddWithValue(dividend);
        return ReadEntries(cmd).SingleOrDefault();
    }

    private static IReadOnlyList<LedgerEntry> ReadEntries(NpgsqlCommand command)
    {
        using var reader = command.ExecuteReader();
        var entries = new List<LedgerEntry>();
        while (reader.Read())
            entries.Add(MapEntry(reader));
        return entries;
    }
    private static LedgerEntry MapEntry(NpgsqlDataReader reader) => new(reader.GetInt64(0), reader.GetString(1), reader.GetString(2), reader.GetString(3),
                ParseAssetType(reader.GetString(4)), reader.IsDBNull(5) ? null : reader.GetDecimal(5),
                reader.IsDBNull(6) ? null : reader.GetDecimal(6), reader.GetDecimal(7), reader.GetDecimal(8), reader.GetDecimal(9),
                new DateTimeOffset(reader.GetFieldValue<DateTime>(10), TimeSpan.Zero).ToOffset(TimeSpan.FromHours(8)),
                reader.GetString(11), reader.IsDBNull(12) ? null : reader.GetString(12), reader.GetBoolean(13));

    public void EnsureInstrument(string userId, string symbol, AssetType assetType, string name, DateOnly asOfDate)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "INSERT INTO instruments(user_id,symbol,asset_type,name,as_of_date) VALUES($1,$2,$3,$4,$5) ON CONFLICT(user_id,symbol) DO NOTHING";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(ToDbAssetType(assetType));
        cmd.Parameters.AddWithValue(name);
        cmd.Parameters.AddWithValue(asOfDate);
        cmd.ExecuteNonQuery();
    }
    public LedgerTransaction? FindTransaction(string userId, string source, string externalId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT id, symbol, asset_type, action, quantity, price, fees, traded_at, cash_symbol, note " +
            "FROM transactions WHERE user_id = $1 AND source = $2 AND external_id = $3";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(source);
        cmd.Parameters.AddWithValue(externalId);
        using var reader = cmd.ExecuteReader();
        if (!reader.Read())
        {
            return null;
        }

        return new LedgerTransaction(
            reader.GetInt64(0),
            reader.GetString(1),
            ParseAssetType(reader.GetString(2)),
            ParseAction(reader.GetString(3)),
            reader.GetDecimal(4),
            reader.GetDecimal(5),
            reader.GetDecimal(6),
            new DateTimeOffset(reader.GetFieldValue<DateTime>(7), TimeSpan.Zero),
            reader.IsDBNull(8) ? null : reader.GetString(8),
            reader.IsDBNull(9) ? null : reader.GetString(9));
    }

    public DateOnly? LatestValuationDate(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT as_of_date FROM portfolio_valuations WHERE user_id = $1 " +
            "ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT 1";
        cmd.Parameters.AddWithValue(userId);
        var result = cmd.ExecuteScalar();
        return result is null or DBNull ? null : (DateOnly)result;
    }

    public bool HasConfirmedBaseline(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT count(*) FROM portfolio_events WHERE user_id = $1 AND event_type = 'baseline' AND status = 'confirmed'";
        cmd.Parameters.AddWithValue(userId);
        return Convert.ToInt32(cmd.ExecuteScalar()) > 0;
    }

    public void RequireKnownInstrument(string userId, string symbol, AssetType assetType)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT asset_type FROM instruments WHERE symbol = $1 AND user_id = $2";
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(userId);
        var row = cmd.ExecuteScalar() as string;
        if (row is null)
        {
            throw new ArgumentException($"未知标的：{symbol}");
        }

        if (row != ToDbAssetType(assetType))
        {
            throw new ArgumentException($"标的{symbol}的资产类型为{row}，不是{ToDbAssetType(assetType)}");
        }
    }

    public CashState GetCashState(string userId, string? cashSymbol)
    {
        using var conn = Open();
        if (cashSymbol is null)
        {
            using var all = conn.CreateCommand();
            all.CommandText = "SELECT symbol, market_value, as_of_date FROM positions WHERE user_id = $1 AND asset_type = 'cash' ORDER BY symbol";
            all.Parameters.AddWithValue(userId);
            var rows = new List<(string Symbol, decimal Balance, DateOnly AsOfDate)>();
            using (var cashReader = all.ExecuteReader())
            {
                while (cashReader.Read())
                {
                    rows.Add((cashReader.GetString(0), cashReader.GetDecimal(1), cashReader.GetFieldValue<DateOnly>(2)));
                }
            }

            if (rows.Count == 0)
            {
                throw new ArgumentException("缺少现金持仓，无法维护双分录账本");
            }

            if (rows.Count != 1)
            {
                throw new ArgumentException("存在多个现金持仓，请显式指定cash_symbol");
            }

            return new CashState(rows[0].Symbol, rows[0].Balance, rows[0].AsOfDate);
        }

        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT symbol, market_value, as_of_date FROM positions WHERE user_id = $1 AND symbol = $2 AND asset_type = 'cash'";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(cashSymbol);
        using var reader = cmd.ExecuteReader();
        if (!reader.Read())
        {
            throw new ArgumentException($"现金持仓不存在：{cashSymbol}");
        }

        return new CashState(reader.GetString(0), reader.GetDecimal(1), reader.GetFieldValue<DateOnly>(2));
    }

    public PositionState? GetPositionState(string userId, string symbol)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT symbol, asset_type, quantity, average_cost, market_value, as_of_date FROM positions WHERE user_id = $1 AND symbol = $2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        using var reader = cmd.ExecuteReader();
        if (!reader.Read())
        {
            return null;
        }

        return new PositionState(
            reader.GetString(0),
            ParseAssetType(reader.GetString(1)),
            reader.GetDecimal(2),
            reader.GetDecimal(3),
            reader.GetDecimal(4),
            reader.GetFieldValue<DateOnly>(5));
    }

    public long InsertTransaction(string userId, ValidatedTransaction v, string cashSymbol, DateTime recordedAt)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO transactions (user_id, symbol, asset_type, action, quantity, price, fees, traded_at, recorded_at, note, source, external_id, gross_amount, cash_delta, cash_symbol) " +
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING id";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(v.Symbol);
        cmd.Parameters.AddWithValue(ToDbAssetType(v.AssetType));
        cmd.Parameters.AddWithValue(ToDbAction(v.Action));
        cmd.Parameters.AddWithValue(v.Quantity);
        cmd.Parameters.AddWithValue(v.Price);
        cmd.Parameters.AddWithValue(v.Fees);
        cmd.Parameters.AddWithValue(v.TradedAt.UtcDateTime);
        cmd.Parameters.AddWithValue(recordedAt);
        cmd.Parameters.AddWithValue((object?)v.Note ?? DBNull.Value);
        cmd.Parameters.AddWithValue(v.Source);
        cmd.Parameters.AddWithValue(v.ExternalId);
        cmd.Parameters.AddWithValue(v.GrossAmount);
        cmd.Parameters.AddWithValue(v.CashDelta);
        cmd.Parameters.AddWithValue(cashSymbol);
        return Convert.ToInt64(cmd.ExecuteScalar());
    }

    public void UpsertPosition(string userId, string symbol, AssetType assetType, decimal quantity, decimal averageCost, decimal marketValue, DateOnly asOfDate, DateTime updatedAt)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO positions (user_id, symbol, asset_type, quantity, average_cost, market_value, as_of_date, updated_at) " +
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) " +
            "ON CONFLICT (user_id, symbol) DO UPDATE SET asset_type = EXCLUDED.asset_type, quantity = EXCLUDED.quantity, " +
            "average_cost = EXCLUDED.average_cost, market_value = EXCLUDED.market_value, as_of_date = EXCLUDED.as_of_date, updated_at = EXCLUDED.updated_at";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(ToDbAssetType(assetType));
        cmd.Parameters.AddWithValue(quantity);
        cmd.Parameters.AddWithValue(averageCost);
        cmd.Parameters.AddWithValue(marketValue);
        cmd.Parameters.AddWithValue(asOfDate.ToDateTime(TimeOnly.MinValue, DateTimeKind.Utc));
        cmd.Parameters.AddWithValue(updatedAt);
        cmd.ExecuteNonQuery();
    }

    public void DeletePosition(string userId, string symbol)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM positions WHERE user_id = $1 AND symbol = $2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        cmd.ExecuteNonQuery();
    }

    public void WriteCashPosition(string userId, string cashSymbol, decimal balance, DateOnly asOfDate, DateTime updatedAt)
    {
        if (balance < 0)
        {
            throw new ArgumentException("现金余额不能为负");
        }

        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "UPDATE positions SET quantity = $1, average_cost = 1, market_value = $1, as_of_date = $2, updated_at = $3 " +
            "WHERE user_id = $4 AND symbol = $5 AND asset_type = 'cash'";
        cmd.Parameters.AddWithValue(balance);
        cmd.Parameters.AddWithValue(asOfDate.ToDateTime(TimeOnly.MinValue, DateTimeKind.Utc));
        cmd.Parameters.AddWithValue(updatedAt);
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(cashSymbol);
        var affected = cmd.ExecuteNonQuery();
        if (affected != 1)
        {
            throw new InvalidOperationException("现金持仓更新失败");
        }
    }

    public void InsertAudit(string userId, string entityType, string entityId, DateTime changedAt, string? beforeJson, string afterJson)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO audit_log (user_id, entity_type, entity_id, changed_at, before_json, after_json) " +
            "VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(entityType);
        cmd.Parameters.AddWithValue(entityId);
        cmd.Parameters.AddWithValue(changedAt);
        cmd.Parameters.AddWithValue((object?)beforeJson ?? DBNull.Value);
        cmd.Parameters.AddWithValue(afterJson);
        cmd.ExecuteNonQuery();
    }

    public long? FindDividend(string userId, string source, string externalId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT id FROM dividend_events WHERE user_id = $1 AND source = $2 AND external_id = $3";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(source);
        cmd.Parameters.AddWithValue(externalId);
        var result = cmd.ExecuteScalar();
        return result is null or DBNull ? null : Convert.ToInt64(result);
    }

    public RecordedLedgerRequest? ReadRecordedRequest(string userId, string entityType, string entityId)
    {
        var (table, column, cast) = entityType switch
        {
            "transaction" => ("transactions", "id", "::bigint"),
            "dividend" => ("dividend_events", "id", "::bigint"),
            "ledger_correction" => ("ledger_corrections", "id", "::bigint"),
            "corporate_action" => ("corporate_action_events", "id", "::bigint"),
            "external_cash_flow" or "portfolio_calibration" => ("portfolio_events", "event_id", ""),
            _ => throw new ArgumentException("Unknown ledger event type")
        };
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = $"""
            SELECT to_jsonb(entry)::text,
                (SELECT a.after_json->>'request_fingerprint' FROM audit_log a
                 WHERE a.user_id=$1 AND a.entity_type=$3 AND a.entity_id=$2
                   AND a.after_json->>'request_fingerprint' IS NOT NULL ORDER BY a.id LIMIT 1)
            FROM {table} entry WHERE entry.user_id=$1 AND entry.{column}=$2{cast}
            """;
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(entityId);
        cmd.Parameters.AddWithValue(entityType);
        using var reader = cmd.ExecuteReader();
        return reader.Read() ? new(reader.GetString(0), reader.IsDBNull(1) ? null : reader.GetString(1)) : null;
    }

    public long InsertDividend(string userId, ValidatedDividend v, string cashSymbol, DateTime recordedAt)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO dividend_events (user_id, symbol, asset_type, gross_amount, fees, net_amount, paid_at, recorded_at, source, external_id, cash_symbol, note) " +
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING id";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(v.Symbol);
        cmd.Parameters.AddWithValue(ToDbAssetType(v.AssetType));
        cmd.Parameters.AddWithValue(v.GrossAmount);
        cmd.Parameters.AddWithValue(v.Fees);
        cmd.Parameters.AddWithValue(v.NetAmount);
        cmd.Parameters.AddWithValue(v.PaidAt.UtcDateTime);
        cmd.Parameters.AddWithValue(recordedAt);
        cmd.Parameters.AddWithValue(v.Source);
        cmd.Parameters.AddWithValue(v.ExternalId);
        cmd.Parameters.AddWithValue(cashSymbol);
        cmd.Parameters.AddWithValue((object?)v.Note ?? DBNull.Value);
        return Convert.ToInt64(cmd.ExecuteScalar());
    }

    public long? FindCorrection(string userId, string source, string externalId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT id FROM ledger_corrections WHERE user_id = $1 AND source = $2 AND external_id = $3";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(source);
        cmd.Parameters.AddWithValue(externalId);
        var result = cmd.ExecuteScalar();
        return result is null or DBNull ? null : Convert.ToInt64(result);
    }

    public CorrectionTarget? GetTransactionForCorrection(string userId, long targetId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT id, symbol, asset_type, action, quantity, price, fees, cash_delta, cash_symbol, traded_at, reversed_by_correction_id " +
            "FROM transactions WHERE user_id = $1 AND id = $2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(targetId);
        using var reader = cmd.ExecuteReader();
        if (!reader.Read())
        {
            return null;
        }

        return new CorrectionTarget(
            reader.GetInt64(0),
            reader.GetString(1),
            ParseAssetType(reader.GetString(2)),
            ParseAction(reader.GetString(3)),
            reader.GetDecimal(4),
            reader.GetDecimal(5),
            reader.GetDecimal(6),
            reader.GetDecimal(7),
            reader.IsDBNull(8) ? null : reader.GetString(8),
            new DateTimeOffset(reader.GetFieldValue<DateTime>(9), TimeSpan.Zero),
            !reader.IsDBNull(10));
    }

    public DividendTarget? GetDividendForCorrection(string userId, long targetId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT id, net_amount, cash_symbol, paid_at, reversed_by_correction_id FROM dividend_events WHERE user_id = $1 AND id = $2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(targetId);
        using var reader = cmd.ExecuteReader();
        if (!reader.Read())
        {
            return null;
        }

        return new DividendTarget(
            reader.GetInt64(0),
            reader.GetDecimal(1),
            reader.IsDBNull(2) ? null : reader.GetString(2),
            new DateTimeOffset(reader.GetFieldValue<DateTime>(3), TimeSpan.Zero),
            !reader.IsDBNull(4));
    }

    public string? GetAuditBeforeJson(string userId, string entityType, string entityId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT before_json::text FROM audit_log WHERE user_id = $1 AND entity_type = $2 AND entity_id = $3 ORDER BY id LIMIT 1";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(entityType);
        cmd.Parameters.AddWithValue(entityId);
        var result = cmd.ExecuteScalar();
        return result is null or DBNull ? null : (string)result;
    }

    public bool HasLaterUnreversedSell(string userId, string symbol, DateTimeOffset tradedAt, long targetId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT 1 FROM transactions WHERE user_id = $1 AND symbol = $2 " +
            "AND (traded_at > $3 OR (traded_at = $3 AND id > $4)) " +
            "AND action IN ('sell','reduce') AND reversed_by_correction_id IS NULL LIMIT 1";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(tradedAt.UtcDateTime);
        cmd.Parameters.AddWithValue(targetId);
        return cmd.ExecuteScalar() is not null;
    }

    public bool HasCorrectionDependencies(string userId, string symbol, DateTimeOffset tradedAt)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT EXISTS (
                SELECT 1 FROM corporate_action_events WHERE user_id=$1 AND effective_at >= $3
                    AND (symbol=$2 OR target_symbol=$2)
                UNION ALL
                SELECT 1 FROM portfolio_events WHERE user_id=$1 AND event_type='calibration'
                    AND status='confirmed' AND effective_date >= $4
            )
            """;
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(symbol);
        cmd.Parameters.AddWithValue(tradedAt.UtcDateTime);
        cmd.Parameters.AddWithValue(DateOnly.FromDateTime(tradedAt.UtcDateTime.AddHours(8)));
        return cmd.ExecuteScalar() is true;
    }

    public long InsertCorrection(string userId, string targetType, long targetId, string reason, DateTimeOffset effectiveAt, DateTime recordedAt, string source, string externalId, string? note)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO ledger_corrections (user_id, target_type, target_id, reason, effective_at, recorded_at, source, external_id, note) " +
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(targetType);
        cmd.Parameters.AddWithValue(targetId);
        cmd.Parameters.AddWithValue(reason);
        cmd.Parameters.AddWithValue(effectiveAt.UtcDateTime);
        cmd.Parameters.AddWithValue(recordedAt);
        cmd.Parameters.AddWithValue(source);
        cmd.Parameters.AddWithValue(externalId);
        cmd.Parameters.AddWithValue((object?)note ?? DBNull.Value);
        return Convert.ToInt64(cmd.ExecuteScalar());
    }

    public void MarkReversed(string userId, string targetType, long targetId, long correctionId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = $"UPDATE {(targetType == "transaction" ? "transactions" : "dividend_events")} SET reversed_by_correction_id = $1 WHERE user_id = $2 AND id = $3";
        cmd.Parameters.AddWithValue(correctionId);
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(targetId);
        cmd.ExecuteNonQuery();
    }

    public long? FindCorporateAction(string userId, string source, string externalId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT id FROM corporate_action_events WHERE user_id = $1 AND source = $2 AND external_id = $3";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(source);
        cmd.Parameters.AddWithValue(externalId);
        var result = cmd.ExecuteScalar();
        return result is null or DBNull ? null : Convert.ToInt64(result);
    }

    public long InsertCorporateAction(string userId, CorporateActionRecord r)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO corporate_action_events (user_id, action_type, symbol, target_symbol, asset_type, quantity, target_quantity, price, target_price, gross_amount, fees, cash_delta, ratio, effective_at, recorded_at, source, external_id, cash_symbol, from_account, to_account, note) " +
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,now(),$15,$16,$17,$18,$19,$20) RETURNING id";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(r.ActionType);
        cmd.Parameters.AddWithValue(r.Symbol);
        cmd.Parameters.AddWithValue((object?)r.TargetSymbol ?? DBNull.Value);
        cmd.Parameters.AddWithValue(ToDbAssetType(r.AssetType));
        cmd.Parameters.AddWithValue((object?)r.Quantity ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)r.TargetQuantity ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)r.Price ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)r.TargetPrice ?? DBNull.Value);
        cmd.Parameters.AddWithValue(r.GrossAmount);
        cmd.Parameters.AddWithValue(r.Fees);
        cmd.Parameters.AddWithValue(r.CashDelta);
        cmd.Parameters.AddWithValue((object?)r.Ratio ?? DBNull.Value);
        cmd.Parameters.AddWithValue(r.EffectiveAt.UtcDateTime);
        cmd.Parameters.AddWithValue(r.Source);
        cmd.Parameters.AddWithValue(r.ExternalId);
        cmd.Parameters.AddWithValue((object?)r.CashSymbol ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)r.FromAccount ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)r.ToAccount ?? DBNull.Value);
        cmd.Parameters.AddWithValue((object?)r.Note ?? DBNull.Value);
        return Convert.ToInt64(cmd.ExecuteScalar());
    }

    public PortfolioEvent? FindPortfolioEvent(string userId, string eventId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT event_id, event_type, effective_date, amount, status, reason, details_json::text FROM portfolio_events WHERE user_id = $1 AND event_id = $2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(eventId);
        using var reader = cmd.ExecuteReader();
        if (!reader.Read())
        {
            return null;
        }

        return new PortfolioEvent(
            reader.GetString(0),
            reader.GetString(1),
            reader.GetFieldValue<DateOnly>(2),
            reader.GetDecimal(3),
            reader.GetString(4),
            reader.GetString(5),
            reader.IsDBNull(6) ? null : reader.GetString(6));
    }

    public void InsertCashFlowEvent(string userId, string eventId, string flowType, DateOnly effectiveDate, decimal signedAmount, string reason, string detailsJson, DateTime recordedAt)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO portfolio_events (user_id, event_id, event_type, effective_date, amount, status, reason, details_json, recorded_at) " +
            "VALUES ($1,$2,$3,$4,$5,'confirmed',$6,$7::jsonb,$8)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(eventId);
        cmd.Parameters.AddWithValue(flowType);
        cmd.Parameters.AddWithValue(effectiveDate.ToDateTime(TimeOnly.MinValue, DateTimeKind.Utc));
        cmd.Parameters.AddWithValue(signedAmount);
        cmd.Parameters.AddWithValue(reason);
        cmd.Parameters.AddWithValue(detailsJson);
        cmd.Parameters.AddWithValue(recordedAt);
        cmd.ExecuteNonQuery();
    }

    public IReadOnlyList<PositionState> ListPositions(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT p.symbol, p.asset_type, p.quantity, p.average_cost, p.market_value, p.as_of_date, COALESCE(i.name,p.symbol) FROM positions p LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol WHERE p.user_id=$1 ORDER BY p.asset_type,p.symbol";
        cmd.Parameters.AddWithValue(userId);
        var result = new List<PositionState>();
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            result.Add(new PositionState(reader.GetString(0), ParseAssetType(reader.GetString(1)), reader.GetDecimal(2), reader.GetDecimal(3), reader.GetDecimal(4), reader.GetFieldValue<DateOnly>(5), reader.GetString(6)));
        }

        return result;
    }

    public async Task<IReadOnlyList<PositionState>> ListPositionsAsync(string userId, CancellationToken cancellation = default)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var command = new NpgsqlCommand("SELECT p.symbol,p.asset_type,p.quantity,p.average_cost,p.market_value,p.as_of_date,COALESCE(i.name,p.symbol) FROM positions p LEFT JOIN instruments i ON i.user_id=p.user_id AND i.symbol=p.symbol WHERE p.user_id=$1 ORDER BY p.asset_type,p.symbol", connection);
        command.Parameters.AddWithValue(userId);
        await using var reader = await command.ExecuteReaderAsync(cancellation);
        var result = new List<PositionState>();
        while (await reader.ReadAsync(cancellation))
            result.Add(new(reader.GetString(0), ParseAssetType(reader.GetString(1)), reader.GetDecimal(2), reader.GetDecimal(3), reader.GetDecimal(4), reader.GetFieldValue<DateOnly>(5), reader.GetString(6)));
        return result;
    }

    public void ReplaceAllPositions(string userId, IReadOnlyList<CalibrationPosition> positions, DateOnly asOfDate, DateTime updatedAt)
    {
        if (_transaction is null)
        {
            ExecuteAtomically(userId, repository =>
            {
                repository.ReplaceAllPositions(userId, positions, asOfDate, updatedAt);
                return true;
            });
            return;
        }
        using var conn = Open();
        using (var del = conn.CreateCommand())
        {
            del.CommandText = "DELETE FROM positions WHERE user_id = $1";
            del.Parameters.AddWithValue(userId);
            del.ExecuteNonQuery();
        }

        foreach (var row in positions)
        {
            using var ins = conn.CreateCommand();
            ins.CommandText = "INSERT INTO positions (user_id, symbol, asset_type, quantity, average_cost, market_value, as_of_date, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)";
            ins.Parameters.AddWithValue(userId);
            ins.Parameters.AddWithValue(row.Symbol);
            ins.Parameters.AddWithValue(ToDbAssetType(row.AssetType));
            ins.Parameters.AddWithValue(row.Quantity);
            ins.Parameters.AddWithValue(row.AverageCost);
            ins.Parameters.AddWithValue(row.MarketValue);
            ins.Parameters.AddWithValue(asOfDate.ToDateTime(TimeOnly.MinValue, DateTimeKind.Utc));
            ins.Parameters.AddWithValue(updatedAt);
            ins.ExecuteNonQuery();
        }

    }

    public void InsertCalibrationEvent(string userId, string eventId, DateOnly asOfDate, decimal netExternalFlow, string reason, string detailsJson, DateTime recordedAt)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO portfolio_events (user_id, event_id, event_type, effective_date, amount, status, reason, details_json, recorded_at) " +
            "VALUES ($1,$2,'calibration',$3,$4,'confirmed',$5,$6::jsonb,$7)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(eventId);
        cmd.Parameters.AddWithValue(asOfDate.ToDateTime(TimeOnly.MinValue, DateTimeKind.Utc));
        cmd.Parameters.AddWithValue(netExternalFlow);
        cmd.Parameters.AddWithValue(reason);
        cmd.Parameters.AddWithValue(detailsJson);
        cmd.Parameters.AddWithValue(recordedAt);
        cmd.ExecuteNonQuery();
    }

    private ConnectionLease Open()
    {
        if (_connection is not null) return new ConnectionLease(_connection, _transaction, false);
        var conn = new NpgsqlConnection(connectionString);
        try { conn.Open(); }
        catch { conn.Dispose(); throw; }
        return new ConnectionLease(conn, null, true);
    }

    private sealed class ConnectionLease(NpgsqlConnection connection, NpgsqlTransaction? transaction, bool ownsConnection) : IDisposable
    {
        public NpgsqlCommand CreateCommand()
        {
            var command = connection.CreateCommand();
            command.Transaction = transaction;
            return command;
        }

        public void Dispose()
        {
            if (ownsConnection) connection.Dispose();
        }
    }

    internal static AssetType ParseAssetType(string value) => value switch
    {
        "stock" => AssetType.Stock,
        "fund" => AssetType.Fund,
        "index" => AssetType.Index,
        "cash" => AssetType.Cash,
        _ => throw new InvalidOperationException($"unknown asset_type: {value}"),
    };

    internal static Action ParseAction(string value) => value switch
    {
        "buy" => Action.Buy,
        "add" => Action.Add,
        "hold" => Action.Hold,
        "reduce" => Action.Reduce,
        "sell" => Action.Sell,
        "no_trade" => Action.NoTrade,
        _ => throw new InvalidOperationException($"unknown action: {value}"),
    };

    internal static string ToDbAssetType(AssetType value) => value switch
    {
        AssetType.Stock => "stock",
        AssetType.Fund => "fund",
        AssetType.Index => "index",
        AssetType.Cash => "cash",
        _ => throw new InvalidOperationException(),
    };

    internal static string ToDbAction(Action value) => value switch
    {
        Action.Buy => "buy",
        Action.Add => "add",
        Action.Hold => "hold",
        Action.Reduce => "reduce",
        Action.Sell => "sell",
        Action.NoTrade => "no_trade",
        _ => throw new InvalidOperationException(),
    };
}
